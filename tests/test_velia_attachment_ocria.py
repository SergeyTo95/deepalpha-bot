import asyncio
import io

from aiohttp import ClientSession, FormData, web
from aiohttp.test_utils import TestServer
from PIL import Image
from pypdf import PdfReader, PdfWriter
import pytest

from services import velia_attachment_final_safety_patch as safety
from services import velia_attachment_idempotency_service as uploads
from services import velia_attachment_ocr_service as ocr
from services import velia_attachment_service as attachments
import velia_mobile_attachment_routes as routes


def scanned_pdf(page_count=1):
    buffer = io.BytesIO()
    Image.new("RGB", (300, 400), "white").save(buffer, "PDF")
    page = PdfReader(io.BytesIO(buffer.getvalue())).pages[0]
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_page(page)
    pdf = io.BytesIO()
    writer.write(pdf)
    return pdf.getvalue()


def test_scanned_pdf_uses_structured_ocr_after_validation(monkeypatch):
    monkeypatch.setenv("VELIA_ATTACHMENTS_OCRIA_ENABLED", "1")
    table = "| Item | Qty |\n|---|---|\n| Paper | 12 |"

    def extract(raw, *, suffix, page_count):
        assert raw.startswith(b"%PDF-")
        assert suffix == ".pdf" and page_count == 1
        return table

    monkeypatch.setattr(ocr, "extract_markdown", extract)
    assert safety._safe_extract_pdf(scanned_pdf()) == table
    with pytest.raises(attachments.AttachmentError, match="document_ocr_too_many_pages"):
        safety._safe_extract_pdf(scanned_pdf(9))
    monkeypatch.setenv("VELIA_ATTACHMENTS_OCRIA_ENABLED", "0")
    with pytest.raises(attachments.AttachmentError, match="document_has_no_readable_text"):
        safety._safe_extract_pdf(scanned_pdf())


def test_selected_flash_upload_enters_local_image_ocr(monkeypatch):
    from services import velia_flash_service as flash

    monkeypatch.setenv("VELIA_MOBILE_API_ENABLED", "true")
    monkeypatch.setenv("VELIA_FILE_ANALYST_ENABLED", "true")
    monkeypatch.setenv("VELIA_ATTACHMENTS_OCRIA_ENABLED", "1")
    monkeypatch.setattr(flash, "available", lambda: True)
    monkeypatch.setattr(flash, "attachments_available", lambda: True)
    monkeypatch.setattr(routes, "_scrub_legacy_payloads_best_effort", lambda: None)
    monkeypatch.setattr(routes, "_require_mobile_auth", lambda request: {"user_id": 7})
    monkeypatch.setattr(routes, "get_conversation", lambda *args: {"id": "conversation"})
    monkeypatch.setattr(uploads, "_existing_or_reserve", lambda **kwargs: None)
    monkeypatch.setattr(uploads.upload_service, "_complete_attachment", lambda **kwargs: kwargs["inspected"])

    calls = []

    def extract(raw, *, suffix, page_count):
        calls.append((suffix, page_count))
        return "| Item | Qty |\n|---|---|\n| Paper | 12 |"

    def forbid_vision(*_args, **_kwargs):
        pytest.fail("Selected file upload invoked vision.")

    monkeypatch.setattr(ocr, "extract_markdown", extract)
    monkeypatch.setattr(attachments, "_analyze_image", forbid_vision)

    async def upload():
        app = web.Application()
        routes.setup_velia_mobile_attachment_routes(app)
        async with TestServer(app) as server, ClientSession() as client:
            photo = io.BytesIO()
            Image.new("RGB", (300, 400), "white").save(photo, "PNG")
            form = FormData()
            form.add_field("file", photo.getvalue(), filename="scan.png", content_type="image/png")
            headers = {"X-Velia-Chat-Mode": "flash", "Idempotency-Key": "ocr-sample-123"}
            async with client.post(
                server.make_url("/mobile-api/v1/conversations/conversation/attachments/selected"),
                data=form,
                headers=headers,
            ) as response:
                assert response.status == 201, await response.text()

    asyncio.run(upload())
    assert calls == [(".png", 1)]
