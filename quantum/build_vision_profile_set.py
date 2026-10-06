#!/usr/bin/env python3
"""Generate a VELIA-owned multimodal routing set for Quantum.

These images are synthetic and deterministic. They are not a release-quality
vision benchmark; they exist to expose the original Qwen vision-token routing
distribution so RCO cannot prune away experts that multimodal inputs rely on.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path


PROMPTS = {
    "en": "Describe the important visual information and mention any visible numbers.",
    "ru": "Опиши важную визуальную информацию и укажи все заметные числа.",
    "es": "Describe la información visual importante y menciona los números visibles.",
    "de": "Beschreibe die wichtigen visuellen Informationen und nenne sichtbare Zahlen.",
    "fr": "Décris les informations visuelles importantes et mentionne les nombres visibles.",
    "tr": "Önemli görsel bilgileri açıkla ve görünen sayıları belirt.",
    "pt": "Descreva as informações visuais importantes e mencione os números visíveis.",
    "it": "Descrivi le informazioni visive importanti e indica i numeri visibili.",
    "pl": "Opisz ważne informacje wizualne i podaj widoczne liczby.",
    "uk": "Опиши важливу візуальну інформацію та вкажи помітні числа.",
    "ar": "صف المعلومات المرئية المهمة واذكر أي أرقام ظاهرة.",
    "zh": "描述图像中的重要视觉信息，并指出可见的数字。",
    "ja": "画像の重要な視覚情報を説明し、見える数字を挙げてください。",
    "ko": "이미지의 중요한 시각 정보를 설명하고 보이는 숫자를 언급하세요.",
    "hi": "महत्वपूर्ण दृश्य जानकारी का वर्णन करें और दिखाई देने वाली संख्याएँ बताएं।",
    "id": "Jelaskan informasi visual yang penting dan sebutkan angka yang terlihat.",
    "vi": "Mô tả thông tin hình ảnh quan trọng và nêu các con số nhìn thấy.",
    "nl": "Beschrijf de belangrijke visuele informatie en noem zichtbare getallen.",
    "cs": "Popiš důležité vizuální informace a uveď viditelná čísla.",
    "ro": "Descrie informațiile vizuale importante și menționează numerele vizibile.",
}


def _scene_chart(draw, rng, width, height):
    margin = 70
    base_y = height - margin
    draw.line((margin, margin, margin, base_y), width=4)
    draw.line((margin, base_y, width - margin, base_y), width=4)
    values = [rng.randint(15, 95) for _ in range(5)]
    span = width - 2 * margin
    bar_w = span // 8
    for i, value in enumerate(values):
        x0 = margin + 40 + i * (bar_w + 35)
        x1 = x0 + bar_w
        y0 = base_y - int((height - 2 * margin) * value / 110)
        draw.rectangle((x0, y0, x1, base_y - 2), outline="black", width=3)
        draw.text((x0 + 4, y0 - 22), str(value), fill="black")
    draw.text((margin, 25), "Q1 Q2 Q3 Q4 Q5", fill="black")


def _scene_table(draw, rng, width, height):
    left, top = 55, 55
    cols, rows = 4, 5
    cell_w = (width - 2 * left) // cols
    cell_h = (height - 2 * top) // rows
    for r in range(rows + 1):
        y = top + r * cell_h
        draw.line((left, y, left + cols * cell_w, y), width=3)
    for c in range(cols + 1):
        x = left + c * cell_w
        draw.line((x, top, x, top + rows * cell_h), width=3)
    headers = ["ID", "A", "B", "SUM"]
    for c, text in enumerate(headers):
        draw.text((left + c * cell_w + 12, top + 12), text, fill="black")
    for r in range(1, rows):
        a, b = rng.randint(10, 99), rng.randint(10, 99)
        values = [r, a, b, a + b]
        for c, value in enumerate(values):
            draw.text(
                (left + c * cell_w + 12, top + r * cell_h + 12),
                str(value),
                fill="black",
            )


def _scene_ui(draw, rng, width, height):
    draw.rounded_rectangle((45, 45, width - 45, height - 45), radius=18, outline="black", width=4)
    draw.rectangle((70, 80, width - 70, 135), outline="black", width=3)
    draw.text((85, 95), "Search / command", fill="black")
    labels = ["Open", "Analyze", "Save"]
    for i, label in enumerate(labels):
        y = 180 + i * 95
        draw.rounded_rectangle((90, y, width - 90, y + 60), radius=12, outline="black", width=3)
        draw.text((120, y + 20), f"{label} {rng.randint(1, 99)}", fill="black")
    draw.text((90, height - 90), f"Status {rng.randint(100, 999)}", fill="black")


def _scene_geometry(draw, rng, width, height):
    cx, cy = width // 2, height // 2
    radius = min(width, height) // 4
    points = []
    sides = 3 + rng.randint(0, 3)
    for i in range(sides):
        angle = -math.pi / 2 + 2 * math.pi * i / sides
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    draw.polygon(points, outline="black")
    for i, (x, y) in enumerate(points):
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), outline="black", width=2)
        draw.text((x + 9, y - 8), str(i + 1), fill="black")
    draw.text((55, 45), f"sides={sides}", fill="black")


def _scene_timeline(draw, rng, width, height):
    y = height // 2
    draw.line((70, y, width - 70, y), width=4)
    years = sorted(rng.sample(range(2010, 2031), 5))
    for i, year in enumerate(years):
        x = 90 + i * (width - 180) // 4
        draw.line((x, y - 25, x, y + 25), width=3)
        draw.text((x - 18, y + 35), str(year), fill="black")
        draw.text((x - 12, y - 55), f"E{i + 1}", fill="black")


SCENES = [_scene_chart, _scene_table, _scene_ui, _scene_geometry, _scene_timeline]


def build(output_dir: Path, count: int = 256, seed: int = 42) -> dict:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise SystemExit("Pillow is required; install quantum/requirements-corpus.txt") from exc

    languages = list(PROMPTS)
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "vision_manifest.jsonl"

    rng = random.Random(seed)
    rows = []
    for index in range(count):
        language = languages[index % len(languages)]
        scene = SCENES[index % len(SCENES)]
        image = Image.new("RGB", (768, 512), "white")
        draw = ImageDraw.Draw(image)
        scene(draw, rng, 768, 512)
        draw.text((18, 485), f"VELIA-Q {index:03d}", fill="black")

        path = images_dir / f"vision-{index:04d}.png"
        image.save(path, format="PNG", optimize=True)

        rows.append(
            {
                "id": f"velia-vision-{index:04d}",
                "image": str(path.resolve()),
                "prompt": PROMPTS[language],
                "language": language,
                "source": "velia-owned/synthetic-routing-v1",
                "license": "VELIA-owned",
                "scene": scene.__name__.removeprefix("_scene_"),
            }
        )

    with manifest_path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    language_counts = {
        language: sum(1 for row in rows if row["language"] == language)
        for language in languages
    }
    scene_counts = {
        scene.__name__.removeprefix("_scene_"): sum(
            1 for row in rows
            if row["scene"] == scene.__name__.removeprefix("_scene_")
        )
        for scene in SCENES
    }
    report = {
        "ok": True,
        "samples": len(rows),
        "languages": language_counts,
        "scenes": scene_counts,
        "manifest": str(manifest_path.resolve()),
        "purpose": "routing-profile only; not a release-quality vision benchmark",
    }
    (output_dir / "vision_set_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--count", type=int, default=256)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    report = build(args.output_dir, count=args.count, seed=args.seed)
    if report["samples"] < 256:
        raise SystemExit("Quantum vision routing set must contain at least 256 samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
