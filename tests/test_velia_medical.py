import importlib.util
import os
import sys
import zipfile
from pathlib import Path

import pytest

from services import velia_medical_service as medical
from services import velia_project_service as projects


def test_medical_defaults_fail_closed(monkeypatch):
    for name in [
        "VELIA_MEDICAL_ENABLED",
        "VELIA_MEDICAL_RADAR_NONCOMMERCIAL_ACK",
        "VELIA_MEDICAL_WORKER_BASE_URL",
        "VELIA_MEDICAL_WORKER_AUTH_TOKEN",
    ]:
        monkeypatch.delenv(name, raising=False)
    status = medical.status()
    assert status["enabled"] is False
    assert status["radar"]["available"] is False
    assert status["radar"]["commercial_use_allowed_by_public_weights"] is False
    assert status["raw_medical_data_persisted_on_railway"] is False
    assert status["direct_android_to_gpu"] is False


def test_public_radar_requires_noncommercial_ack(monkeypatch):
    monkeypatch.setenv("VELIA_MEDICAL_ENABLED", "true")
    monkeypatch.setenv("VELIA_MEDICAL_PROVIDER", "radar")
    monkeypatch.setenv("VELIA_MEDICAL_WORKER_BASE_URL", "https://medical.example.invalid")
    monkeypatch.setenv("VELIA_MEDICAL_WORKER_AUTH_TOKEN", "secret")
    monkeypatch.setenv("VELIA_MEDICAL_RADAR_NONCOMMERCIAL_ACK", "false")
    assert medical.radar_available() is False
    monkeypatch.setenv("VELIA_MEDICAL_RADAR_NONCOMMERCIAL_ACK", "true")
    assert medical.radar_available() is True


def test_result_contract_rejects_probability_claim():
    with pytest.raises(projects.ProjectError) as error:
        medical.validate_result({
            "provider": "radar",
            "score_semantics": "calibrated_probability",
            "findings": [],
        })
    assert error.value.code == "medical_worker_invalid_result"


def test_result_contract_accepts_model_scores():
    medical.validate_result({
        "provider": "radar",
        "score_semantics": "model_score_not_calibrated_probability",
        "findings": [
            {"organ": "Liver", "finding": "Cyst", "score": 0.42},
            {"organ": "Pancreas", "finding": "Pancreatitis", "score": 0.81},
        ],
    })


def _load_worker_module(name: str):
    root = Path(__file__).resolve().parents[1] / "medical_worker"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(name, root / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_worker_rejects_zip_traversal():
    app = _load_worker_module("app")
    info = zipfile.ZipInfo("../escape.dcm")
    assert app._zip_member_is_safe(info) is False


def test_worker_accepts_normal_zip_member():
    app = _load_worker_module("app")
    info = zipfile.ZipInfo("series/0001.dcm")
    assert app._zip_member_is_safe(info) is True


def test_radar_adapter_parses_english_labels():
    adapter_module = _load_worker_module("radar_adapter")
    organ, finding = adapter_module.RadarAdapter._parse_column(
        "肝_肝细胞癌_(Liver_Hepatocellular carcinoma)"
    )
    assert organ == "Liver"
    assert finding == "Hepatocellular carcinoma"


def test_archived_source_requires_separate_acceptance(tmp_path, monkeypatch):
    module = _load_worker_module('radar_adapter')
    monkeypatch.delenv('VELIA_MEDICAL_ACCEPTED_SOURCE_SHA256', raising=False)
    adapter = module.RadarAdapter(upstream_root=str(tmp_path), model_root=str(tmp_path))
    assert adapter.readiness()['source_accepted'] is False
    assert adapter.readiness()['ready'] is False
    assert adapter.readiness()['upstream_commit'] is None


def test_source_archive_rejects_wrong_checksum(tmp_path):
    module = _load_worker_module('upstream_source')
    with pytest.raises(RuntimeError, match='checksum mismatch'):
        module.unpack_source(b'untrusted archive', tmp_path)


def test_case_requires_explicit_supported_scope(monkeypatch):
    monkeypatch.setenv("VELIA_MEDICAL_ENABLED", "true")
    monkeypatch.setenv("VELIA_MEDICAL_PROVIDER", "radar")
    monkeypatch.setenv("VELIA_MEDICAL_WORKER_BASE_URL", "https://medical.example.invalid")
    monkeypatch.setenv("VELIA_MEDICAL_WORKER_AUTH_TOKEN", "secret")
    monkeypatch.setenv("VELIA_MEDICAL_RADAR_NONCOMMERCIAL_ACK", "true")
    with pytest.raises(projects.ProjectError) as error:
        medical.create_case(
            1,
            {
                "modality": "ct",
                "study_kind": "contrast_abdomen",
                "title": "CT",
                "contrast_enhanced_confirmed": False,
                "abdomen_confirmed": True,
            },
        )
    assert error.value.code == "medical_scope_confirmation_required"
