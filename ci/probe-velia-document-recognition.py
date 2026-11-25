"""Verify the installed document parser in the release image using a synthetic scan."""

import io

from PIL import Image, ImageDraw, ImageFont

from services.velia_attachment_final_safety_patch import _safe_extract_pdf


def main() -> None:
    scan = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(scan)
    font = ImageFont.load_default(size=42)
    columns = (90, 490, 820, 1150)
    for y in (245, 325, 405, 485):
        draw.line((columns[0], y, columns[-1], y), fill="black", width=3)
    for x in columns:
        draw.line((x, 245, x, 485), fill="black", width=3)
    rows = (("Item", "Quantity", "Price"), ("Paper", "120", "240"), ("Folders", "3", "150"))
    for row_number, row in enumerate(rows):
        for x, value in zip(columns, row):
            draw.text((x + 20, 258 + row_number * 80), value, fill="black", font=font)

    pdf = io.BytesIO()
    scan.save(pdf, format="PDF", resolution=150)
    markdown = _safe_extract_pdf(pdf.getvalue())
    if "| Paper | 120 | 240 |" not in markdown or "| Folders | 3 | 150 |" not in markdown:
        raise RuntimeError("Document recognition did not preserve the synthetic table.")
    print("VELIA_DOCUMENT_RECOGNITION_OK", flush=True)


if __name__ == "__main__":
    main()
