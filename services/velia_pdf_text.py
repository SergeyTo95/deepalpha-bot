"""Page-aware native PDF text extraction, shared by existing attachment paths."""


def extract_pages(pages):
    chunks=[];readable=False
    for number,page in enumerate(pages,1):
        try:text=str(page.extract_text() or '').strip()
        except Exception:text=''
        if text:
            readable=True;chunks.append(f'[Page {number}]\n{text}')
        else:
            # A blank page and an image-only scan cannot be distinguished here.
            chunks.append(f'[Page {number}: no text extracted; page may be blank or require OCR]')
    return '\n\n'.join(chunks) if readable else ''
