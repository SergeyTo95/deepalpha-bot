"""Run OCRia locally in a bounded process for chat attachments."""

import io
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

from PIL import Image, ImageOps

from services import velia_attachment_service as attachments

_logger = logging.getLogger(__name__)
_ocr_slot = threading.BoundedSemaphore(1)
_PDF_DPI = 175
_MAX_RENDER_PIXELS = 5_000_000


def enabled() -> bool:
    return os.getenv("VELIA_ATTACHMENTS_OCRIA_ENABLED") == "1"


def pdf_page_limit() -> int:
    return attachments._env_int("VELIA_ATTACHMENTS_OCR_MAX_PAGES", 8, 1, 8)


def pdf_page_sizes_supported(pages) -> bool:
    for page in pages:
        try:
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            unit = float(page.user_unit)
            width_px = width * unit * _PDF_DPI / 72
            height_px = height * unit * _PDF_DPI / 72
            if not (0 < width_px <= 4000 and 0 < height_px <= 4000
                    and width_px * height_px <= _MAX_RENDER_PIXELS):
                return False
        except (AttributeError, ArithmeticError, TypeError, ValueError):
            return False
    return True


def extract_markdown(raw: bytes, *, suffix: str, page_count: int) -> str:
    binary, limiter = shutil.which("lit"), shutil.which("prlimit")
    if not enabled() or not binary or not limiter:
        raise attachments.AttachmentError("attachment_ocr_unavailable", status=503)
    if not _ocr_slot.acquire(timeout=1):
        raise attachments.AttachmentError("preview_capacity_exceeded", status=429)

    # Keep provider credentials and proxy settings out of the document parser.
    env = {
        key: os.environ[key] for key in ("HOME", "PATH", "LITEPARSE_MODEL_DIR")
        if key in os.environ
    }
    env.update(OMP_NUM_THREADS="1", RAYON_NUM_THREADS="1", MALLOC_ARENA_MAX="2")
    try:
        with tempfile.TemporaryDirectory(prefix="velia-ocr-") as directory:
            source = Path(directory) / ("input" + suffix)
            source.write_bytes(raw)
            output = Path(directory) / "text.md"
            subprocess.run(
                [
                    limiter, "--as=4294967296", "--fsize=2097152", "--",
                    binary, "parse", "--quiet", "--format", "markdown",
                    "--ocr-backend", "ocria-nano", "--num-workers", "1",
                    "--max-pages", str(page_count), "--dpi", str(_PDF_DPI),
                    "--image-mode", "off", "--no-links", "--keep-headers-footers",
                    "--output", str(output), str(source),
                ],
                cwd=directory,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=12,
            )
            return attachments._normalize_text(output.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired:
        _logger.warning("OCRia attachment timed out")
        raise attachments.AttachmentError("attachment_ocr_timeout", status=503) from None
    except (OSError, subprocess.CalledProcessError, UnicodeError):
        _logger.warning("OCRia attachment could not be parsed")
        raise attachments.AttachmentError("attachment_ocr_failed", status=422) from None
    finally:
        _ocr_slot.release()


def inspect_text_image(raw: bytes, mime_type: str) -> dict:
    _, width, height = attachments._verify_image(raw, mime_type)
    encoded = io.BytesIO()
    try:
        with Image.open(io.BytesIO(raw)) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((2000, 2000))
            image.save(encoded, format="PNG")
    except (OSError, ValueError, Image.DecompressionBombError):
        raise attachments.AttachmentError("invalid_image", status=415) from None
    text = extract_markdown(encoded.getvalue(), suffix=".png", page_count=1)
    if not text:
        raise attachments.AttachmentError("image_has_no_readable_text", status=422)
    return {
        "kind": "image",
        "mime_type": mime_type,
        "width": width,
        "height": height,
        "extracted_text": (
            "OCR_TEXT_ONLY_UNTRUSTED:\n"
            "Only recognized text follows. No visual description was performed.\n" + text
        ),
    }
