"""Local file decoding/OCR for selected text models; never calls another LLM."""
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time

from PIL import Image, ImageOps

from services import velia_attachment_service as attachments

_OCR_SLOTS = threading.BoundedSemaphore(2)


def _command(args, *, data=None, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise attachments.AttachmentError("attachment_ocr_timeout", status=504)
    if not _OCR_SLOTS.acquire(timeout=min(5, remaining)):
        raise attachments.AttachmentError("attachment_ocr_busy", status=429)
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise attachments.AttachmentError("attachment_ocr_timeout", status=504)
        return subprocess.run(args, input=data, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=min(30, remaining), check=True,
            env={**os.environ, "OMP_THREAD_LIMIT": "2"}).stdout
    except FileNotFoundError:
        raise attachments.AttachmentError("attachment_ocr_unavailable", status=503) from None
    except subprocess.TimeoutExpired:
        raise attachments.AttachmentError("attachment_ocr_timeout", status=504) from None
    except subprocess.CalledProcessError:
        raise attachments.AttachmentError("attachment_ocr_failed", status=422) from None
    finally:
        _OCR_SLOTS.release()


def ocr_image(raw, *, deadline=None):
    deadline = deadline or time.monotonic() + 30
    binary = shutil.which("tesseract")
    if not binary:
        raise attachments.AttachmentError("attachment_ocr_unavailable", status=503)
    languages = os.getenv("VELIA_FILE_OCR_LANGUAGES", "eng+rus+tur").strip()
    if not re.fullmatch(r"[a-z_]{3,20}(?:\+[a-z_]{3,20}){0,7}", languages):
        raise attachments.AttachmentError("attachment_ocr_unavailable", status=503)
    try:
        with Image.open(io.BytesIO(raw)) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((2400, 2400))
            encoded = io.BytesIO()
            image.save(encoded, format="PNG")
    except (OSError, ValueError, Image.DecompressionBombError):
        raise attachments.AttachmentError("invalid_image", status=415) from None
    output = _command([binary, "stdin", "stdout", "-l", languages, "--psm", "3"],
        data=encoded.getvalue(), deadline=deadline)
    return attachments._normalize_text(output.decode("utf-8", errors="replace"), max_chars=20000)


def _pdf_text(raw):
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted:
            raise attachments.AttachmentError("encrypted_pdf_not_supported", status=415)
        pages = list(reader.pages)
    except attachments.AttachmentError:
        raise
    except Exception:
        raise attachments.AttachmentError("invalid_pdf", status=415) from None
    if len(pages) > attachments._env_int("VELIA_ATTACHMENTS_PDF_MAX_PAGES", 200, 1, 1000):
        raise attachments.AttachmentError("pdf_too_many_pages", status=413)
    deadline, scanned, chunks = time.monotonic() + 90, 0, []
    max_scans = attachments._env_int("VELIA_ATTACHMENTS_OCR_MAX_PAGES", 8, 1, 20)
    with tempfile.TemporaryDirectory(prefix="velia-file-") as directory:
        source = Path(directory) / "input.pdf"
        source.write_bytes(raw)
        for index, page in enumerate(pages, 1):
            try:
                text = str(page.extract_text() or "").strip()
                resources = page.get("/Resources")
                images = bool(resources and resources.get_object().get("/XObject"))
            except Exception:
                raise attachments.AttachmentError("invalid_pdf", status=415) from None
            if not text and images:
                scanned += 1
                if scanned > max_scans:
                    raise attachments.AttachmentError("document_ocr_too_many_pages", status=413)
                binary = shutil.which("pdftoppm")
                if not binary:
                    raise attachments.AttachmentError("attachment_ocr_unavailable", status=503)
                output = Path(directory) / "page"
                _command([binary, "-f", str(index), "-l", str(index), "-singlefile",
                    "-scale-to", "2400", "-png", str(source), str(output)], deadline=deadline)
                try:
                    text = ocr_image(output.with_suffix(".png").read_bytes(), deadline=deadline)
                except OSError:
                    raise attachments.AttachmentError("attachment_ocr_failed", status=422) from None
                if not text:
                    raise attachments.AttachmentError("document_has_no_readable_text", status=422)
            if text:
                chunks.append(f"[Page {index}]\n{text}")
    text = attachments._normalize_text("\n\n".join(chunks))
    if not text:
        raise attachments.AttachmentError("document_has_no_readable_text", status=422)
    return text


def inspect_text_attachment(raw, mime_type):
    """Decode files as untrusted text. Image OCR is not scene understanding."""
    if mime_type in attachments._ALLOWED_IMAGE_MIME_TYPES:
        _, width, height = attachments._verify_image(raw, mime_type)
        text = ocr_image(raw)
        if not text:
            raise attachments.AttachmentError("image_has_no_readable_text", status=422)
        return {"kind": "image", "mime_type": mime_type, "width": width, "height": height,
            "extracted_text": "OCR_TEXT_ONLY_UNTRUSTED:\nOnly recognized text follows. "
                "No visual description or scene analysis was performed.\n" + text}
    if mime_type == "application/pdf":
        if not raw.startswith(b"%PDF-"):
            raise attachments.AttachmentError("attachment_type_mismatch", status=415)
        text = _pdf_text(raw)
    elif mime_type == "text/plain":
        text = attachments._decode_text(raw)
    elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        text = attachments._extract_docx(raw)
    else:
        raise attachments.AttachmentError("attachment_type_not_supported", status=415)
    if not text:
        raise attachments.AttachmentError("document_has_no_readable_text", status=422)
    return {"kind": "document", "mime_type": mime_type, "width": None, "height": None,
        "extracted_text": text}
