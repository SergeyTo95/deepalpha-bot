import ast
import io
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import List
import pytest
from services.velia_pdf_text import extract_pages


def page(text):return SimpleNamespace(extract_text=lambda:text)


def test_mixed_scan_does_not_silently_disappear():
    value=extract_pages([page('English'),page(''),page('Русский')])
    assert '[Page 1]\nEnglish' in value and '[Page 3]\nРусский' in value
    assert '[Page 2: no text extracted' in value and 'blank or require OCR' in value


def test_all_unreadable_still_empty_and_parser_failure_marked():
    broken=SimpleNamespace(extract_text=lambda:(_ for _ in ()).throw(ValueError()))
    assert extract_pages([page(''),broken])==''
    assert '[Page 2: no text extracted' in extract_pages([page('known'),broken])


def test_effective_safe_attachment_path_preserves_missing_page(monkeypatch):
    # Compile the production function without initializing unrelated providers/DB.
    path=Path('services/velia_attachment_final_safety_patch.py')
    function=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='_safe_extract_pdf')
    class Error(ValueError):
        def __init__(self,code,status=400):super().__init__(code);self.status=status
    namespace={'io':io,'List':List,'attachment_service':SimpleNamespace(AttachmentError=Error,
               _env_int=lambda *args:200,_normalize_text=lambda s:s)}
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(path),'exec'),namespace)
    fake=SimpleNamespace(is_encrypted=False,pages=[page('Known text'),page('')])
    monkeypatch.setitem(sys.modules,'pypdf',SimpleNamespace(PdfReader=lambda *args,**kw:fake))
    assert '[Page 2: no text extracted' in namespace['_safe_extract_pdf'](b'%PDF')
    fake.pages=[page('')]
    with pytest.raises(Error,match='document_has_no_readable_text'):namespace['_safe_extract_pdf'](b'%PDF')
    fake.is_encrypted=True
    with pytest.raises(Error,match='encrypted_pdf_not_supported'):namespace['_safe_extract_pdf'](b'%PDF')
