import json
from pathlib import Path

from quantum.validate_calibration import validate
from quantum.acceptance import evaluate as evaluate_acceptance
from quantum.preflight import validate_config_payload
from quantum.build_public_corpus import aya_record, normalize_language, select_balanced
from quantum.merge_calibration import merge_rows
from quantum.patch_llama_ple_q8 import INSERT, MARKER, NEEDLE

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


def test_quantum_acceptance_requires_every_language_and_cpu_runtime():
    repo = Path(__file__).resolve().parents[1]
    spec = json.loads((repo / "quantum" / "spec.json").read_text(encoding="utf-8"))
    languages = {lang: 0.80 for lang in json.loads(
        (repo / "quantum" / "calibration_plan.json").read_text(encoding="utf-8")
    )["core_languages"]}
    benchmark = {
        "core_languages": list(languages),
        "base": {
            "overall": 0.80,
            "tool_call": 0.80,
            "coding": 0.80,
            "reasoning": 0.80,
            "vision": 0.80,
            "languages": languages,
        },
        "candidate": {
            "overall": 0.78,
            "tool_call": 0.78,
            "coding": 0.76,
            "reasoning": 0.76,
            "vision": 0.75,
            "languages": {lang: 0.76 for lang in languages},
        },
    }
    runtime = {
        "rss_gb": 19.0,
        "warm_output_tokens_per_second": 12.0,
        "ttft_seconds_2k": 5.0,
    }
    report = evaluate_acceptance(spec, benchmark, runtime)
    assert report["ok"] is True

    benchmark["candidate"]["languages"]["tr"] = 0.60
    report = evaluate_acceptance(spec, benchmark, runtime)
    assert report["ok"] is False
    assert any("language tr" in error for error in report["errors"])


def test_quantum_acceptance_rejects_slow_or_oversized_candidate():
    repo = Path(__file__).resolve().parents[1]
    spec = json.loads((repo / "quantum" / "spec.json").read_text(encoding="utf-8"))
    base_languages = {"en": 1.0}
    benchmark = {
        "core_languages": ["en"],
        "base": {
            "overall": 1.0, "tool_call": 1.0, "coding": 1.0,
            "reasoning": 1.0, "vision": 1.0, "languages": base_languages,
        },
        "candidate": {
            "overall": 1.0, "tool_call": 1.0, "coding": 1.0,
            "reasoning": 1.0, "vision": 1.0, "languages": {"en": 1.0},
        },
    }
    runtime = {
        "rss_gb": 22.0,
        "warm_output_tokens_per_second": 8.0,
        "ttft_seconds_2k": 8.0,
    }
    report = evaluate_acceptance(spec, benchmark, runtime)
    assert report["ok"] is False
    assert any("rss_gb" in error for error in report["errors"])
    assert any("warm output" in error for error in report["errors"])
    assert any("TTFT" in error for error in report["errors"])


def test_quantum_preflight_accepts_expected_qwen4exp_shape():
    payload = {
        "model_type": "qwen4_exp",
        "architectures": ["Qwen4ExpForConditionalGeneration"],
        "text_config": {
            "model_type": "qwen4_exp_text",
            "num_hidden_layers": 48,
            "num_experts": 512,
            "num_experts_per_tok": 10,
            "hidden_size": 2560,
            "moe_intermediate_size": 640,
            "ple_layer_ids": [0, 1],
            "split_ngram_parts": 128,
        },
    }
    assert validate_config_payload(payload) == []


def test_quantum_preflight_rejects_wrong_expert_topology():
    payload = {
        "model_type": "qwen4_exp",
        "architectures": ["Qwen4ExpForConditionalGeneration"],
        "text_config": {
            "model_type": "qwen4_exp_text",
            "num_hidden_layers": 48,
            "num_experts": 256,
            "num_experts_per_tok": 5,
            "hidden_size": 2560,
            "moe_intermediate_size": 640,
            "ple_layer_ids": [0],
            "split_ngram_parts": 128,
        },
    }
    errors = validate_config_payload(payload)
    assert any("num_experts=" in error for error in errors)
    assert any("num_experts_per_tok=" in error for error in errors)


def test_quantum_public_corpus_normalizes_languages_and_categories():
    assert normalize_language("eng") == "en"
    assert normalize_language("rus") == "ru"
    assert normalize_language("tr") == "tr"
    assert normalize_language("unknown-language") == ""

    row = {
        "id": 7,
        "inputs": "Translate this sentence into German.",
        "targets": "Übersetze diesen Satz ins Deutsche.",
        "language": "eng",
        "task_type": "generation",
        "dataset_name": "unit",
    }
    record = aya_record(
        row,
        source_id="aya-collection:test",
        license_id="Apache-2.0",
        default_category="general_dialogue",
    )
    assert record is not None
    assert record["language"] == "en"
    assert record["category"] == "translation"


def test_quantum_public_corpus_selection_is_language_balanced():
    rows = []
    for language in ("en", "ru", "tr"):
        for index in range(10):
            rows.append({
                "id": f"{language}-{index}",
                "language": language,
                "category": "general_dialogue",
                "split": "calibration",
                "source": "aya-human",
                "license": "Apache-2.0",
                "messages": [
                    {"role": "user", "content": f"{language} unique prompt {index}"},
                    {"role": "assistant", "content": f"answer {index}"},
                ],
            })
    selected = select_balanced(rows, {"en", "ru", "tr"}, 4, 42)
    counts = {language: 0 for language in ("en", "ru", "tr")}
    for row in selected:
        counts[row["language"]] += 1
    assert counts == {"en": 4, "ru": 4, "tr": 4}


def test_quantum_merge_rejects_holdout_source_leak(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    manifest = json.loads((repo / "quantum" / "source_manifest.json").read_text(encoding="utf-8"))
    component = tmp_path / "component.jsonl"
    row = {
        "id": "leak-1",
        "language": "en",
        "category": "reasoning_math",
        "split": "calibration",
        "source": "mgsm",
        "license": "CC-BY-SA-4.0",
        "messages": [
            {"role": "user", "content": "benchmark prompt"},
            {"role": "assistant", "content": "benchmark answer"},
        ],
    }
    component.write_text(json.dumps(row) + "\n", encoding="utf-8")
    rows, errors = merge_rows([component], manifest)
    assert rows == []
    assert any("holdout source leaked" in error for error in errors)


def test_quantum_aya_human_long_prompt_routes_to_documents():
    row = {
        "id": "long-doc",
        "inputs": "A" * 900,
        "targets": "A concise grounded answer with enough content to be meaningful.",
        "language_code": "eng",
    }
    record = aya_record(
        row,
        source_id="aya-human",
        license_id="Apache-2.0",
        default_category="general_dialogue",
        language_field="language_code",
    )
    assert record is not None
    assert record["category"] == "documents_retrieval"


def test_quantum_ple_q8_overlay_targets_only_ple_before_upstream_tq_policy():
    upstream = "prefix\n" + NEEDLE + "suffix\n"
    patched = upstream.replace(NEEDLE, INSERT + NEEDLE, 1)
    assert patched.count(MARKER) == 1
    assert "PER_LAYER_TOKEN_EMBD" in INSERT
    assert "GGMLQuantizationType.Q8_0" in INSERT
    assert "MOSTLY_TQ2_0" in INSERT
    assert patched.index(MARKER) < patched.index(NEEDLE)
