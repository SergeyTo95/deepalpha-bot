"""Run the installed parser in an isolated subprocess and retain page numbers."""

from pathlib import Path
import sys

from liteparse import LiteParse


def main(source: str, output: str, page_count: str, dpi: str) -> None:
    parser = LiteParse(
        ocr_backend="ocria-nano",
        output_format="markdown",
        num_workers=1,
        max_pages=int(page_count),
        dpi=int(dpi),
        image_mode="off",
        extract_links=False,
        keep_headers_footers=True,
        quiet=True,
    )
    result = parser.parse(source)
    sections = []
    for page in result.pages:
        text = (page.markdown or page.text).strip()
        if text:
            sections.append(f"[PDF_PAGE {page.page_num}]\n{text}")
    Path(output).write_text("\n\n".join(sections), encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:])
