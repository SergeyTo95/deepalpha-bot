from io import BytesIO
import asyncio
import pytest
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer
from desktop.document_exports import export_document, setup_document_exports

CONTENT='# План работы\nПривет, Велия!\n\n| Статья | Доход |\n|---|---|\n| Работа | 125 |\n| Итог | =SUM(B2:B2) |\n| Внешнее | =WEBSERVICE("https://example.com") |'


def test_office_exports_round_trip_and_preserve_safe_formulas():
    from openpyxl import load_workbook
    from docx import Document
    from pptx import Presentation
    book=load_workbook(BytesIO(export_document('xlsx',CONTENT)))
    assert book.active['B2'].value==125 and book.active['B3'].value=='=SUM(B2:B2)'
    assert book.active['B4'].data_type=='s'
    document=Document(BytesIO(export_document('docx',CONTENT)))
    assert any('Привет, Велия!' in paragraph.text for paragraph in document.paragraphs)
    deck=Presentation(BytesIO(export_document('pptx',CONTENT)))
    assert any('Привет, Велия!' in shape.text for slide in deck.slides for shape in slide.shapes if shape.has_text_frame)


def test_pdf_preserves_cyrillic_and_escapes_markup():
    from pypdf import PdfReader
    pdf=PdfReader(BytesIO(export_document('pdf',CONTENT+'\n<script>данные</script>')))
    text=''.join(page.extract_text() for page in pdf.pages)
    assert 'Привет, Велия!' in text and '<script>данные</script>' in text


def test_exports_require_authentication_and_same_origin():
    async def scenario():
        async def session(request):return object() if request.headers.get('X-Account') else None
        app=web.Application();setup_document_exports(app,session_for=session,same_origin=lambda request:request.headers.get('Origin')=='https://velia.example',json_response=lambda value,status=200:web.json_response(value,status=status))
        async with TestServer(app) as server, ClientSession() as client:
            url=server.make_url('/web-api/v1/documents/export')
            assert (await client.post(url,json={'format':'pdf','content':CONTENT})).status==403
            assert (await client.post(url,json={'format':'pdf','content':CONTENT},headers={'Origin':'https://velia.example'})).status==401
            response=await client.post(url,json={'format':'pdf','content':CONTENT},headers={'Origin':'https://velia.example','X-Account':'7'})
            assert response.status==200 and response.headers['Cache-Control']=='no-store' and (await response.read()).startswith(b'%PDF')
            response=await client.post(url,json={'format':'pdf','content':'x'*50001},headers={'Origin':'https://velia.example','X-Account':'7'})
            assert response.status==400
    asyncio.run(scenario())
