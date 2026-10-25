"""Local OCR acceptance; no user files, chat writes, or external model calls."""
import io
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont
from services import velia_attachment_service as files
from services import velia_flash_service as flash, velia_quantum_service as quantum
from services.velia_attachment_text_service import inspect_text_attachment


def main():
    try:
        if not flash.attachments_available() or not quantum.public_capability()["chat_quantum_attachments"]:
            raise RuntimeError("file_processing_disabled")
        languages = subprocess.run(["tesseract", "--list-langs"], capture_output=True,
            text=True, timeout=5, check=True).stdout.splitlines()
        if not {"eng", "rus", "tur"}.issubset(set(languages)):
            raise RuntimeError("ocr_languages_missing")
        def forbidden(*args, **kwargs):
            raise RuntimeError("unexpected_model_call")
        files.call_gemini = files.call_kimi_vision = forbidden
        flash.generate = quantum.generate = forbidden
        image = Image.new("RGB", (1000, 220), "white")
        ImageDraw.Draw(image).text((40, 65), "VELIA 42 INVOICE", fill="black",
            font=ImageFont.truetype("DejaVuSans.ttf", 64))
        for kind, mime in (("PNG", "image/png"), ("PDF", "application/pdf")):
            output = io.BytesIO()
            image.save(output, format=kind)
            result = inspect_text_attachment(output.getvalue(), mime)
            if "42" not in result["extracted_text"]:
                raise RuntimeError("ocr_acceptance_failed")
        text = inspect_text_attachment("Сумма 42 TON".encode(), "text/plain")
        if "42 TON" not in text["extracted_text"]:
            raise RuntimeError("text_acceptance_failed")
    except Exception:
        print("VELIA_SELECTED_INPUTS_FAILED", flush=True)
        return 1
    print("VELIA_SELECTED_INPUTS_OK ocr=true languages=eng,rus,tur other_model_calls=0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
