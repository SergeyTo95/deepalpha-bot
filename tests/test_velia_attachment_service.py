import io
import uuid
import zipfile

import pytest
from PIL import Image

from services import velia_attachment_service as service


def test_sanitize_filename_removes_paths_controls_and_trailing_dots():
    assert service.sanitize_filename("../folder/ report\x00 .pdf ") == "report .pdf"
    assert service.sanitize_filename("   ...   ") == "attachment"


def test_normalize_attachment_ids_deduplicates_without_reordering():
    first = str(uuid.uuid4())
    second = str(uuid.uuid4())

    assert service.normalize_attachment_ids([first, second, first]) == [first, second]


def test_normalize_attachment_ids_rejects_invalid_and_too_many_values():
    with pytest.raises(service.AttachmentError) as invalid:
        service.normalize_attachment_ids(["not-a-uuid"])
    assert invalid.value.code == "invalid_attachment_id"

    with pytest.raises(service.AttachmentError) as too_many:
        service.normalize_attachment_ids([str(uuid.uuid4()) for _ in range(5)])
    assert too_many.value.code == "too_many_attachments"


def test_plain_text_is_decoded_and_normalized():
    inspected = service.inspect_attachment(
        "  Первая строка  \r\n\r\n\r\n Вторая\tстрока ".encode("utf-8"),
        "text/plain",
    )

    assert inspected["kind"] == "document"
    assert inspected["mime_type"] == "text/plain"
    assert inspected["extracted_text"] == "Первая строка\n\nВторая строка"


def test_pdf_mime_requires_pdf_magic_before_parser_is_called():
    with pytest.raises(service.AttachmentError) as mismatch:
        service.inspect_attachment(b"not a pdf", "application/pdf")

    assert mismatch.value.code == "attachment_type_mismatch"
    assert mismatch.value.status == 415


def test_docx_extracts_paragraph_text_from_minimal_valid_package():
    content_types = b"<?xml version='1.0'?><Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'></Types>"
    document = b"""<?xml version='1.0' encoding='UTF-8'?>
    <w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>
      <w:body>
        <w:p><w:r><w:t>Hello</w:t></w:r><w:r><w:t> world</w:t></w:r></w:p>
        <w:p><w:r><w:t>Second paragraph</w:t></w:r></w:p>
      </w:body>
    </w:document>
    """
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", document)

    inspected = service.inspect_attachment(
        output.getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert inspected["kind"] == "document"
    assert inspected["extracted_text"] == "Hello world\nSecond paragraph"


def test_declared_image_mime_must_match_verified_image_format():
    output = io.BytesIO()
    Image.new("RGB", (8, 8)).save(output, format="PNG")

    with pytest.raises(service.AttachmentError) as mismatch:
        service._verify_image(output.getvalue(), "image/jpeg")

    assert mismatch.value.code == "attachment_type_mismatch"
    assert mismatch.value.status == 415


def test_attachment_error_never_contains_uploaded_content():
    secret = "private-user-secret"

    with pytest.raises(service.AttachmentError) as error:
        service.inspect_attachment(secret.encode("utf-8"), "application/pdf")

    assert secret not in str(error.value)
