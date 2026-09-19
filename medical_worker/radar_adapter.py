from __future__ import annotations

import csv
import importlib
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List


UPSTREAM_COMMIT = "9319f36642b6f3f4708c8e5c8844ab114d6e7b24"
SCORE_SEMANTICS = "model_score_not_calibrated_probability"


class RadarAdapterError(RuntimeError):
    pass


class RadarAdapter:
    def __init__(
        self,
        *,
        upstream_root: str,
        model_root: str,
    ) -> None:
        self.upstream_root = Path(upstream_root).resolve()
        self.inference_root = self.upstream_root / "RADAR_inference"
        self.model_root = Path(model_root).resolve()
        self._lock = threading.Lock()
        self._initialized = None
        self._module = None

    def readiness(self) -> Dict[str, Any]:
        required = [
            self.inference_root / "inference_demo.py",
            self.model_root / "checkpoint_radar_pretrain.pth",
            self.model_root / "infer_text_embedding_merlin.pt",
            self.model_root / "bert-base-chinese",
        ]
        missing = [str(path) for path in required if not path.exists()]
        return {
            "ready": not missing,
            "missing": missing,
            "upstream_commit": UPSTREAM_COMMIT,
        }

    def _load(self):
        readiness = self.readiness()
        if not readiness["ready"]:
            raise RadarAdapterError("radar_model_files_missing")
        if str(self.inference_root) not in sys.path:
            sys.path.insert(0, str(self.inference_root))
        os.environ["MODEL_ROOT"] = str(self.model_root)
        os.environ["CONFIGS_ROOT"] = str(self.model_root)
        previous = os.getcwd()
        try:
            os.chdir(self.inference_root)
            module = importlib.import_module("inference_demo")
            initialized = module.initialize()
        finally:
            os.chdir(previous)
        self._module = module
        self._initialized = initialized

    def ensure_loaded(self) -> None:
        if self._initialized is not None:
            return
        with self._lock:
            if self._initialized is None:
                self._load()

    @staticmethod
    def _parse_column(column: str) -> tuple[str, str]:
        label = str(column or "").strip()
        if "_(" in label and label.endswith(")"):
            english = label.rsplit("_(", 1)[1][:-1]
        else:
            english = label
        if "_" in english:
            organ, finding = english.split("_", 1)
        else:
            organ, finding = "Abdomen", english
        return organ.strip(), finding.strip()

    def infer(self, nifti_path: str, output_dir: str, job_tag: str) -> Dict[str, Any]:
        self.ensure_loaded()
        src = Path(nifti_path).resolve()
        out = Path(output_dir).resolve()
        input_dir = out / "radar_input"
        result_dir = out / "radar_results"
        shutil.rmtree(input_dir, ignore_errors=True)
        shutil.rmtree(result_dir, ignore_errors=True)
        input_dir.mkdir(parents=True, exist_ok=True)
        result_dir.mkdir(parents=True, exist_ok=True)
        target = input_dir / ("study.nii.gz" if src.name.endswith(".gz") else "study.nii")
        shutil.copyfile(src, target)

        with self._lock:
            previous = os.getcwd()
            try:
                os.chdir(self.inference_root)
                self._module.inference(
                    self._initialized,
                    str(input_dir),
                    str(result_dir),
                    str(job_tag),
                )
            finally:
                os.chdir(previous)

        csv_path = result_dir / f"RADAR_infer_results_{job_tag}.csv"
        if not csv_path.exists():
            raise RadarAdapterError("radar_result_missing")

        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if len(rows) != 1:
            raise RadarAdapterError("radar_result_invalid")
        row = rows[0]
        findings: List[Dict[str, Any]] = []
        omitted: List[str] = []

        for key, value in row.items():
            if key == "file_name":
                continue
            try:
                score = float(value)
            except (TypeError, ValueError):
                omitted.append(str(key))
                continue
            # The upstream demo uses a raw gallbladder segmentation voxel count
            # for surgically_absent_gallbladder rather than a normalized score.
            # Do not misrepresent that value as a probability/model score.
            if not 0.0 <= score <= 1.0:
                omitted.append(str(key))
                continue
            organ, finding = self._parse_column(key)
            if not organ or not finding:
                omitted.append(str(key))
                continue
            findings.append({
                "organ": organ,
                "finding": finding,
                "score": round(score, 6),
            })

        findings.sort(key=lambda item: item["score"], reverse=True)
        return {
            "provider": "radar",
            "model": "RADAR",
            "upstream_commit": UPSTREAM_COMMIT,
            "score_semantics": SCORE_SEMANTICS,
            "calibrated_probability": False,
            "diagnosis": False,
            "findings": findings,
            "omitted_upstream_outputs": omitted,
        }
