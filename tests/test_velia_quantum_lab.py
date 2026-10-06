import json
from pathlib import Path

from quantum.validate_calibration import validate


def _plan(tmp_path: Path) -> Path:
    plan = {
        "categories": ["general_dialogue", "reasoning_math"],
        "core_languages": ["en", "ru", "tr", "es"],
        "extended_languages": ["de"],
        "language_balance": {
            "minimum_distinct_languages": 4,
            "minimum_core_language_share_each": 0.20,
            "maximum_language_share_any": 0.30,
            "maximum_en_plus_ru_share": 0.60,
        },
        "category_balance": {
            "minimum_share": {
                "general_dialogue": 0.40,
                "reasoning_math": 0.40,
            },
            "maximum_share_any": 0.60,
        },
        "minimum_dataset_size": {
            "smoke": 10,
            "pruning_search": 20,
            "release_candidate": 40,
        },
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def _record(i, language, category, split="calibration"):
    return {
        "id": f"sample-{i}",
        "language": language,
        "category": category,
        "split": split,
        "source": "unit-test",
        "license": "CC0-1.0",
        "messages": [
            {"role": "user", "content": f"{language} unique question number {i}"},
            {"role": "assistant", "content": f"answer number {i} with enough tokens"},
        ],
    }


def test_quantum_calibration_validator_accepts_balanced_multilingual_data(tmp_path):
    languages = ["en", "ru", "tr", "es"]
    rows = []
    for i in range(20):
        rows.append(_record(
            i,
            languages[i % len(languages)],
            "general_dialogue" if i % 2 == 0 else "reasoning_math",
            "holdout" if i in {16, 17, 18, 19} else "calibration",
        ))
    dataset = tmp_path / "data.jsonl"
    dataset.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    report = validate(dataset, _plan(tmp_path), "smoke")
    assert report["ok"] is True
    assert report["samples"] == 20
    assert len(report["languages"]) == 4
    assert report["splits"]["holdout"] == 4


def test_quantum_calibration_validator_rejects_language_collapse(tmp_path):
    rows = []
    for i in range(20):
        rows.append(_record(
            i,
            "en" if i < 17 else ["ru", "tr", "es"][i - 17],
            "general_dialogue" if i % 2 == 0 else "reasoning_math",
            "holdout" if i == 19 else "calibration",
        ))
    dataset = tmp_path / "data.jsonl"
    dataset.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    report = validate(dataset, _plan(tmp_path), "smoke")
    assert report["ok"] is False
    assert any("language en" in error for error in report["errors"])
    assert any("core language" in error for error in report["errors"])


def test_quantum_calibration_validator_rejects_cross_split_prompt_leakage(tmp_path):
    languages = ["en", "ru", "tr", "es"]
    rows = []
    for i in range(20):
        rows.append(_record(
            i,
            languages[i % 4],
            "general_dialogue" if i % 2 == 0 else "reasoning_math",
            "holdout" if i >= 16 else "calibration",
        ))
    rows[-1]["messages"][0]["content"] = rows[0]["messages"][0]["content"]
    dataset = tmp_path / "data.jsonl"
    dataset.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    report = validate(dataset, _plan(tmp_path), "smoke")
    assert report["ok"] is False
    assert any("crosses splits" in error for error in report["errors"])


def test_quantum_spec_is_cpu_railway_and_not_flash():
    repo = Path(__file__).resolve().parents[1]
    spec = json.loads((repo / "quantum" / "spec.json").read_text(encoding="utf-8"))
    assert spec["product"]["model_id"] == "velia-quantum"
    assert spec["base"]["model"] == "Qwen/Qwen3.8-Flash-Next"
    assert spec["compression"]["expert_selection"]["keep_experts_per_layer"] == 256
    assert spec["compression"]["expert_selection"]["preserve_top_k"] == 10
    assert spec["runtime_target"]["platform"] == "Railway"
    assert spec["runtime_target"]["accelerator"] == "cpu"
    assert spec["safety"]["do_not_replace_flash_before_acceptance"] is True
