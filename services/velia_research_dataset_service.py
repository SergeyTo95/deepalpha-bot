"""Immutable numeric dataset registry for VELIA Research Center.

Datasets are accepted only as bounded structured JSON numeric tables. The registry
never opens files, fetches URLs, evaluates expressions, or executes user code.
Each snapshot has immutable content and split hashes and deterministic train/test
membership that can be revalidated immediately before Safe Compute execution.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
from typing import Any, Dict, List, Optional, Tuple

from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_safety_service as safety
from services.velia_chat_service import _iso


REGISTRY_VERSION = 1
MAX_ROWS = 2000
MAX_COLUMNS = 16
MAX_NAME = 80
ALLOWED_PROVENANCE_KINDS = {
    "user_supplied",
    "instrument_export",
    "published_extracted",
    "simulation",
}
ALLOWED_SPLITS = {"train", "test"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def enabled() -> bool:
    return center.enabled() and _env_bool("VELIA_RESEARCH_DATASET_REGISTRY_ENABLED", False)


def status() -> Dict[str, Any]:
    return {
        "enabled": enabled(),
        "registry_version": REGISTRY_VERSION,
        "numeric_only": True,
        "max_rows": MAX_ROWS,
        "max_columns": MAX_COLUMNS,
        "immutable_snapshots": True,
        "deterministic_train_test_split": True,
        "arbitrary_code": False,
        "arbitrary_shell": False,
        "arbitrary_file_access": False,
        "arbitrary_url_fetch": False,
        "dynamic_expression_eval": False,
    }


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, limit: int, code: str, *, required: bool = False) -> str:
    if value is None:
        if required:
            raise projects.ProjectError(code)
        return ""
    if not isinstance(value, str) or "\x00" in value or len(value) > limit:
        raise projects.ProjectError(code)
    value = re.sub(r"\s+", " ", value).strip()
    if required and not value:
        raise projects.ProjectError(code)
    return value


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise projects.ProjectError("invalid_research_dataset")
    value = float(value)
    if not math.isfinite(value) or abs(value) > 1e100:
        raise projects.ProjectError("invalid_research_dataset")
    return value


def _column_name(value: Any) -> str:
    value = _text(value, MAX_NAME, "invalid_research_dataset", required=True)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", value):
        raise projects.ProjectError("invalid_research_dataset")
    return value


def _provenance(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {
        "kind", "source_label", "description", "license", "collected_at"
    }:
        raise projects.ProjectError("invalid_research_dataset_provenance")
    kind = str(value.get("kind") or "")
    if kind not in ALLOWED_PROVENANCE_KINDS:
        raise projects.ProjectError("invalid_research_dataset_provenance")
    return {
        "kind": kind,
        "source_label": _text(value.get("source_label"), 300, "invalid_research_dataset_provenance"),
        "description": _text(value.get("description"), 1200, "invalid_research_dataset_provenance"),
        "license": _text(value.get("license"), 120, "invalid_research_dataset_provenance"),
        "collected_at": _text(value.get("collected_at"), 80, "invalid_research_dataset_provenance"),
    }


def _validate_create(data: Any) -> Tuple[str, List[str], List[List[float]], Dict[str, Any], Dict[str, Any]]:
    if not isinstance(data, dict) or set(data) - {
        "name", "columns", "rows", "provenance", "split"
    }:
        raise projects.ProjectError("invalid_research_dataset")
    name = _text(data.get("name"), 160, "invalid_research_dataset", required=True)

    columns_raw = data.get("columns")
    rows_raw = data.get("rows")
    if not isinstance(columns_raw, list) or not 1 <= len(columns_raw) <= MAX_COLUMNS:
        raise projects.ProjectError("invalid_research_dataset")
    columns = [_column_name(value) for value in columns_raw]
    if len(set(columns)) != len(columns):
        raise projects.ProjectError("invalid_research_dataset")

    if not isinstance(rows_raw, list) or not 4 <= len(rows_raw) <= MAX_ROWS:
        raise projects.ProjectError("invalid_research_dataset")
    rows: List[List[float]] = []
    for raw in rows_raw:
        if not isinstance(raw, list) or len(raw) != len(columns):
            raise projects.ProjectError("invalid_research_dataset")
        rows.append([_number(value) for value in raw])

    provenance = _provenance(data.get("provenance"))

    split_raw = data.get("split")
    if split_raw is None:
        split_raw = {}
    if not isinstance(split_raw, dict) or set(split_raw) - {"test_fraction", "seed"}:
        raise projects.ProjectError("invalid_research_dataset_split")
    test_fraction = split_raw.get("test_fraction", 0.2)
    seed = split_raw.get("seed", 0)
    if isinstance(test_fraction, bool) or not isinstance(test_fraction, (int, float)):
        raise projects.ProjectError("invalid_research_dataset_split")
    test_fraction = float(test_fraction)
    if not 0.1 <= test_fraction <= 0.5:
        raise projects.ProjectError("invalid_research_dataset_split")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**63 - 1:
        raise projects.ProjectError("invalid_research_dataset_split")
    split_config = {"test_fraction": test_fraction, "seed": seed}
    return name, columns, rows, provenance, split_config


def _split_membership(row_count: int, config: Dict[str, Any]) -> Dict[str, List[int]]:
    indices = list(range(row_count))
    rng = random.Random(int(config["seed"]))
    rng.shuffle(indices)
    test_count = max(1, min(row_count - 1, int(round(row_count * float(config["test_fraction"])))))
    test = sorted(indices[:test_count])
    test_set = set(test)
    train = [idx for idx in range(row_count) if idx not in test_set]
    if not train or not test:
        raise projects.ProjectError("invalid_research_dataset_split")
    return {"train": train, "test": test}


def ensure_tables() -> None:
    if not projects.ready():
        center.ensure_tables()
    with projects.transaction() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_datasets (
            dataset_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            name TEXT NOT NULL,
            registry_version INTEGER NOT NULL,
            dataset_hash TEXT NOT NULL,
            split_hash TEXT NOT NULL,
            columns_json TEXT NOT NULL,
            rows_json TEXT NOT NULL,
            provenance_json TEXT NOT NULL,
            split_json TEXT NOT NULL,
            safety_json TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            column_count INTEGER NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(mission_id,user_id,dataset_hash,split_hash),
            CHECK(row_count BETWEEN 4 AND 2000),
            CHECK(column_count BETWEEN 1 AND 16),
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_datasets_mission
            ON velia_research_datasets(mission_id,user_id,created_at DESC)""")


def _assert_integrity(row: Dict[str, Any]) -> None:
    columns = json.loads(row["columns_json"])
    rows = json.loads(row["rows_json"])
    provenance = json.loads(row["provenance_json"])
    split_snapshot = json.loads(row["split_json"])
    observed_dataset_hash = _sha({
        "registry_version": int(row["registry_version"]),
        "columns": columns,
        "rows": rows,
        "provenance": provenance,
    })
    if observed_dataset_hash != str(row["dataset_hash"]):
        raise projects.ProjectError("research_dataset_integrity_mismatch", 409)
    observed_split_hash = _sha({
        "dataset_hash": observed_dataset_hash,
        "split": split_snapshot,
    })
    if observed_split_hash != str(row["split_hash"]):
        raise projects.ProjectError("research_dataset_integrity_mismatch", 409)


def _row(row: Dict[str, Any], *, include_rows: bool = False) -> Dict[str, Any]:
    result = {
        "id": row["dataset_id"],
        "mission_id": row["mission_id"],
        "name": row["name"],
        "registry_version": int(row["registry_version"]),
        "dataset_hash": row["dataset_hash"],
        "split_hash": row["split_hash"],
        "columns": json.loads(row["columns_json"]),
        "provenance": json.loads(row["provenance_json"]),
        "split": json.loads(row["split_json"]),
        "safety": json.loads(row["safety_json"]),
        "row_count": int(row["row_count"]),
        "column_count": int(row["column_count"]),
        "created_at": _iso(row["created_at"]),
    }
    if include_rows:
        result["rows"] = json.loads(row["rows_json"])
    return result


def create(user_id: int, mission_id: str, data: Any) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_dataset_registry_disabled", 503)
    mission = center.get_mission(user_id, mission_id)
    if mission["status"] in {"blocked", "cancelled", "completed"}:
        raise projects.ProjectError("research_mission_not_active", 409)

    name, columns, rows, provenance, split_config = _validate_create(data)
    metadata_safety = safety.classify(
        _json({"name": name, "columns": columns, "provenance": provenance}),
        phase="tool_call",
    )
    if metadata_safety["decision"] == "blocked":
        raise projects.ProjectError("research_dataset_safety_blocked", 403)
    if mission["safety"].get("read_only_only"):
        metadata_safety = {
            **metadata_safety,
            "read_only_only": True,
            "execution_allowed": False,
            "inherited_read_only": True,
        }

    membership = _split_membership(len(rows), split_config)
    split_snapshot = {
        **split_config,
        "train_indices": membership["train"],
        "test_indices": membership["test"],
        "train_count": len(membership["train"]),
        "test_count": len(membership["test"]),
    }
    immutable_payload = {
        "registry_version": REGISTRY_VERSION,
        "columns": columns,
        "rows": rows,
        "provenance": provenance,
    }
    dataset_hash = _sha(immutable_payload)
    split_hash = _sha({
        "dataset_hash": dataset_hash,
        "split": split_snapshot,
    })
    dataset_id = hashlib.sha256(
        (str(mission_id) + "|" + dataset_hash + "|" + split_hash).encode("utf-8")
    ).hexdigest()

    with projects.transaction(user_id) as cur:
        cur.execute("""INSERT INTO velia_research_datasets(
            dataset_id,mission_id,user_id,name,registry_version,dataset_hash,split_hash,
            columns_json,rows_json,provenance_json,split_json,safety_json,row_count,column_count)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(mission_id,user_id,dataset_hash,split_hash) DO NOTHING""",
            (
                dataset_id, str(mission_id), int(user_id), name, REGISTRY_VERSION,
                dataset_hash, split_hash, _json(columns), _json(rows), _json(provenance),
                _json(split_snapshot), _json(metadata_safety), len(rows), len(columns),
            ))
        cur.execute("""SELECT * FROM velia_research_datasets
            WHERE mission_id=%s AND user_id=%s AND dataset_hash=%s AND split_hash=%s""",
            (str(mission_id), int(user_id), dataset_hash, split_hash))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_dataset_state_missing", 500)
        _assert_integrity(row)
        center._event(cur, str(mission_id), user_id, "research_dataset_registered", {
            "dataset_id": row["dataset_id"],
            "dataset_hash": row["dataset_hash"],
            "split_hash": row["split_hash"],
            "row_count": row["row_count"],
            "column_count": row["column_count"],
            "provenance_kind": provenance["kind"],
        })
        return _row(row)


def get(user_id: int, dataset_id: str, *, include_rows: bool = False) -> Dict[str, Any]:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_datasets
            WHERE dataset_id=%s AND user_id=%s""",
            (str(dataset_id), int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_dataset_not_found", 404)
        _assert_integrity(row)
        return _row(row, include_rows=include_rows)


def list_datasets(user_id: int, mission_id: str, offset: int = 0) -> Dict[str, Any]:
    center.get_mission(user_id, mission_id)
    offset = int(offset)
    if offset < 0 or offset > 500:
        raise projects.ProjectError("invalid_offset")
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_datasets
            WHERE mission_id=%s AND user_id=%s
            ORDER BY created_at DESC,dataset_id DESC LIMIT 51 OFFSET %s""",
            (str(mission_id), int(user_id), offset))
        rows = list(cur.fetchall())
    return {
        "datasets": [_row(row) for row in rows[:50]],
        "next_offset": offset + 50 if len(rows) > 50 else None,
    }


def snapshot(user_id: int, dataset_id: str, split: str, columns: List[str]) -> Dict[str, Any]:
    if split not in ALLOWED_SPLITS:
        raise projects.ProjectError("invalid_research_dataset_split")
    if not isinstance(columns, list) or not 1 <= len(columns) <= 2:
        raise projects.ProjectError("invalid_research_dataset_columns")
    requested = [_column_name(value) for value in columns]
    if len(set(requested)) != len(requested):
        raise projects.ProjectError("invalid_research_dataset_columns")

    dataset = get(user_id, dataset_id, include_rows=False)
    if dataset["safety"].get("read_only_only") or dataset["safety"].get("decision") == "blocked":
        raise projects.ProjectError("research_dataset_read_only", 403)
    known = set(dataset["columns"])
    if any(value not in known for value in requested):
        raise projects.ProjectError("research_dataset_column_not_found", 404)
    indices = dataset["split"][split + "_indices"]
    return {
        "dataset_id": dataset["id"],
        "mission_id": dataset["mission_id"],
        "dataset_hash": dataset["dataset_hash"],
        "split_hash": dataset["split_hash"],
        "split": split,
        "columns": requested,
        "row_count": len(indices),
        "provenance_kind": dataset["provenance"]["kind"],
        "registry_version": dataset["registry_version"],
    }


def verify_snapshot(user_id: int, expected: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(expected, dict) or set(expected) != {
        "dataset_id", "mission_id", "dataset_hash", "split_hash", "split",
        "columns", "row_count", "provenance_kind", "registry_version"
    }:
        raise projects.ProjectError("research_dataset_snapshot_invalid", 409)
    current = snapshot(
        user_id,
        str(expected["dataset_id"]),
        str(expected["split"]),
        list(expected["columns"]),
    )
    if current != expected:
        raise projects.ProjectError("research_dataset_snapshot_mismatch", 409)
    return current


def materialize(user_id: int, expected: Dict[str, Any]) -> Dict[str, List[float]]:
    current = verify_snapshot(user_id, expected)
    dataset = get(user_id, current["dataset_id"], include_rows=True)
    indices = dataset["split"][current["split"] + "_indices"]
    positions = {name: dataset["columns"].index(name) for name in current["columns"]}
    output: Dict[str, List[float]] = {}
    for name in current["columns"]:
        position = positions[name]
        output[name] = [float(dataset["rows"][idx][position]) for idx in indices]
    if any(len(values) != current["row_count"] for values in output.values()):
        raise projects.ProjectError("research_dataset_snapshot_mismatch", 409)
    return output
