"""Persistent, owner-scoped foundation for VELIA Research Center.

Stage 1 deliberately separates research orchestration from arbitrary tool
execution. Missions, hypotheses and experiment plans are durable and auditable;
every branch is checked by the deterministic Research Safety Officer before it
can become executable. Later workers can consume only records whose safety
snapshot still permits the requested capability.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, Dict, List, Optional

from services import velia_project_service as projects
from services import velia_research_safety_service as safety
from services.velia_chat_service import _iso


MISSION_STATES = {
    "planned", "literature", "hypotheses", "experiments", "replication",
    "synthesis", "completed", "blocked", "cancelled",
}
EVIDENCE_GRADES = {
    "systematic_review", "meta_analysis", "rct", "observational", "preclinical",
    "in_vitro", "simulation", "expert_opinion", "unknown",
}
DOMAIN_HINTS = {
    "medicine": ("medical", "medicine", "clinical", "disease", "therapy", "treatment", "медицин", "клинич", "заболев", "лечен", "терап"),
    "biology": ("biology", "genetic", "cell", "protein", "биолог", "генет", "клетк", "белок"),
    "materials": ("material", "battery", "alloy", "polymer", "материал", "аккумулятор", "сплав", "полимер"),
    "energy": ("energy", "solar", "reactor", "grid", "энерг", "солнеч", "реактор", "электросет"),
    "space": ("space", "orbit", "rocket", "satellite", "космос", "орбит", "ракет", "спутник"),
    "economics": ("econom", "market", "finance", "эконом", "рынок", "финанс"),
    "ai": ("artificial intelligence", "machine learning", "neural", " ии ", "нейросет", "машинн"),
    "agriculture": ("agricultur", "crop", "soil", "сельск", "урож", "почв"),
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def enabled() -> bool:
    return _env_bool("VELIA_RESEARCH_CENTER_ENABLED", False)


def autonomy_enabled() -> bool:
    return enabled() and _env_bool("VELIA_RESEARCH_AUTONOMY_ENABLED", False)


def experiment_execution_enabled() -> bool:
    return autonomy_enabled() and _env_bool("VELIA_RESEARCH_EXPERIMENT_EXECUTION_ENABLED", False)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _text(value: Any, limit: int, code: str) -> str:
    if not isinstance(value, str) or "\x00" in value or len(value) > limit:
        raise projects.ProjectError(code)
    return re.sub(r"\s+", " ", value).strip()


def _domain(goal: str) -> str:
    low = " " + goal.lower().replace("ё", "е") + " "
    scores = {name: sum(1 for hint in hints if hint in low) for name, hints in DOMAIN_HINTS.items()}
    name, score = max(scores.items(), key=lambda item: item[1])
    return name if score else "general"


def _plan(domain: str, decision: Dict[str, Any]) -> Dict[str, Any]:
    roles = [
        "research_director", "literature_scout", "methodologist", "statistician",
        "skeptic", "replication_agent", "evidence_reviewer", "safety_officer",
    ]
    if domain == "medicine":
        roles.insert(-2, "medical_evidence_reviewer")
    return {
        "version": 1,
        "domain": domain,
        "roles": roles,
        "phases": [
            "literature", "hypotheses", "experiments", "replication", "synthesis",
        ],
        "parallel_hypothesis_branches": True,
        "safety_checkpoints": ["intake", "hypothesis", "experiment", "tool_call", "final_output"],
        "execution_policy": {
            "decision": decision["decision"],
            "read_only_only": bool(decision.get("read_only_only")),
            "arbitrary_shell": False,
            "arbitrary_url_fetch": False,
            "experiment_execution_enabled": False,
        },
    }


def ensure_tables() -> None:
    """Create Stage 1 schema and migrate the existing project resource check."""
    if not projects.ready():
        projects.ensure_tables()
    with projects.transaction() as cur:
        # Existing Railway databases still carry the original generated CHECK.
        # Avoid taking a DDL lock on every process start: migrate only when the
        # current definition has not yet learned the research resource kind.
        cur.execute("""SELECT pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE conrelid='velia_project_resources'::regclass
              AND conname='velia_project_resources_kind_check'""")
        resource_check = cur.fetchone()
        if not resource_check or "'research'" not in str(resource_check["definition"]):
            cur.execute("ALTER TABLE velia_project_resources DROP CONSTRAINT IF EXISTS velia_project_resources_kind_check")
            cur.execute("""ALTER TABLE velia_project_resources
                ADD CONSTRAINT velia_project_resources_kind_check
                CHECK(kind IN ('chat','deepalpha','image','video','music','research')) NOT VALID""")
            cur.execute("ALTER TABLE velia_project_resources VALIDATE CONSTRAINT velia_project_resources_kind_check")
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_missions (
            mission_id TEXT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            project_id TEXT NULL,
            goal TEXT NOT NULL,
            domain TEXT NOT NULL,
            status TEXT NOT NULL,
            safety_json TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(mission_id,user_id),
            CHECK(status IN ('planned','literature','hypotheses','experiments','replication','synthesis','completed','blocked','cancelled')),
            FOREIGN KEY(mission_id,user_id) REFERENCES velia_project_resources(resource_id,user_id) ON DELETE CASCADE,
            FOREIGN KEY(project_id,user_id) REFERENCES velia_projects(project_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_events (
            sequence_id BIGSERIAL PRIMARY KEY,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            FOREIGN KEY(mission_id,user_id) REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_hypotheses (
            hypothesis_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            title TEXT NOT NULL,
            rationale TEXT NOT NULL,
            status TEXT NOT NULL,
            evidence_grade TEXT NOT NULL DEFAULT 'unknown',
            safety_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            FOREIGN KEY(mission_id,user_id) REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_experiments (
            experiment_id TEXT PRIMARY KEY,
            hypothesis_id TEXT NULL,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            status TEXT NOT NULL,
            method_json TEXT NOT NULL,
            result_json TEXT NULL,
            safety_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            FOREIGN KEY(mission_id,user_id) REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE,
            FOREIGN KEY(hypothesis_id) REFERENCES velia_research_hypotheses(hypothesis_id) ON DELETE SET NULL)""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_velia_research_owner ON velia_research_missions(user_id,created_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_velia_research_events ON velia_research_events(mission_id,user_id,sequence_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_velia_research_hypotheses ON velia_research_hypotheses(mission_id,user_id,created_at)")


def status() -> Dict[str, Any]:
    return {
        "enabled": enabled(),
        "autonomy_enabled": autonomy_enabled(),
        "experiment_execution_enabled": experiment_execution_enabled(),
        "stage": 1,
        "mode": "persistent_research_center",
        "safety": "deterministic_fail_closed",
        "arbitrary_shell": False,
        "arbitrary_url_fetch": False,
        "domains": sorted(list(DOMAIN_HINTS) + ["general"]),
        "evidence_grades": sorted(EVIDENCE_GRADES),
    }


def _event(cur, mission_id: str, user_id: int, event_type: str, payload: Dict[str, Any]) -> None:
    encoded = _json(payload)
    if len(encoded) > 24000:
        raise projects.ProjectError("research_event_too_large")
    cur.execute("""INSERT INTO velia_research_events(mission_id,user_id,event_type,payload_json)
        VALUES(%s,%s,%s,%s)""", (mission_id, int(user_id), event_type[:80], encoded))


def _mission(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["mission_id"],
        "project_id": row["project_id"],
        "goal": row["goal"],
        "domain": row["domain"],
        "status": row["status"],
        "safety": json.loads(row["safety_json"]),
        "plan": json.loads(row["plan_json"]),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def create_mission(user_id: int, data: Dict[str, Any], client_request_id: str) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_center_disabled", 503)
    if not isinstance(data, dict):
        raise projects.ProjectError("invalid_request")
    goal = _text(data.get("goal", ""), 6000, "invalid_research_goal")
    if len(goal) < 3:
        raise projects.ProjectError("research_goal_required")
    title = _text(data.get("title", ""), 120, "invalid_title") or goal[:90]
    project_id = data.get("project_id") or None
    if project_id is not None and (not isinstance(project_id, str) or not project_id or len(project_id) > 128):
        raise projects.ProjectError("invalid_project_id")
    key = projects.request_key(client_request_id)
    decision = safety.classify(goal, phase="intake")
    domain = _domain(goal)
    plan = _plan(domain, decision)
    digest = projects._hash([project_id, "research", goal, title])

    with projects.transaction(user_id) as cur:
        if project_id:
            projects._owned(cur, user_id, project_id)
        cur.execute("SELECT * FROM velia_project_resources WHERE user_id=%s AND client_request_id=%s", (int(user_id), key))
        existing = cur.fetchone()
        if existing:
            if existing["kind"] != "research" or existing["request_hash"] != digest:
                raise projects.ProjectError("idempotency_conflict", 409)
            cur.execute("SELECT * FROM velia_research_missions WHERE mission_id=%s AND user_id=%s", (existing["resource_id"], int(user_id)))
            row = cur.fetchone()
            if not row:
                raise projects.ProjectError("research_storage_inconsistent", 503)
            return _mission(row)
        cur.execute("SELECT COUNT(*) AS count FROM velia_research_missions WHERE user_id=%s", (int(user_id),))
        if cur.fetchone()["count"] >= 500:
            raise projects.ProjectError("research_mission_limit_exceeded", 429)
        mission_id = str(uuid.uuid4())
        cur.execute("""INSERT INTO velia_project_resources(
                resource_id,user_id,project_id,kind,title,seed_query,client_request_id,request_hash)
            VALUES(%s,%s,%s,'research',%s,%s,%s,%s)""",
            (mission_id, int(user_id), project_id, title, goal[:2000], key, digest))
        state = "blocked" if decision["decision"] == "blocked" else "planned"
        cur.execute("""INSERT INTO velia_research_missions(
                mission_id,user_id,project_id,goal,domain,status,safety_json,plan_json)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (mission_id, int(user_id), project_id, goal, domain, state, _json(decision), _json(plan)))
        row = cur.fetchone()
        _event(cur, mission_id, user_id, "mission_created", {"domain": domain, "status": state})
        _event(cur, mission_id, user_id, "safety_decision", decision)
        return _mission(row)


def get_mission(user_id: int, mission_id: str) -> Dict[str, Any]:
    with projects.transaction() as cur:
        cur.execute("SELECT * FROM velia_research_missions WHERE mission_id=%s AND user_id=%s", (str(mission_id), int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_mission_not_found", 404)
        return _mission(row)


def list_missions(user_id: int, offset: int = 0) -> Dict[str, Any]:
    offset = int(offset)
    if offset < 0 or offset > 500:
        raise projects.ProjectError("invalid_offset")
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_missions WHERE user_id=%s
            ORDER BY created_at DESC,mission_id DESC LIMIT 51 OFFSET %s""", (int(user_id), offset))
        rows = list(cur.fetchall())
        return {"missions": [_mission(row) for row in rows[:50]],
                "next_offset": offset + 50 if len(rows) > 50 else None}


def list_events(user_id: int, mission_id: str, after: int = 0) -> List[Dict[str, Any]]:
    get_mission(user_id, mission_id)
    with projects.transaction() as cur:
        cur.execute("""SELECT sequence_id,event_type,payload_json,created_at FROM velia_research_events
            WHERE mission_id=%s AND user_id=%s AND sequence_id>%s ORDER BY sequence_id ASC LIMIT 200""",
            (str(mission_id), int(user_id), max(0, int(after))))
        return [{"sequence": row["sequence_id"], "type": row["event_type"],
                 "payload": json.loads(row["payload_json"]), "created_at": _iso(row["created_at"])}
                for row in cur.fetchall()]


def add_hypothesis(user_id: int, mission_id: str, title: str, rationale: str = "") -> Dict[str, Any]:
    title = _text(title, 600, "invalid_hypothesis")
    rationale = _text(rationale, 3000, "invalid_hypothesis")
    if not title:
        raise projects.ProjectError("invalid_hypothesis")
    decision = safety.classify(title + "\n" + rationale, phase="hypothesis")
    with projects.transaction(user_id) as cur:
        cur.execute("SELECT status,safety_json FROM velia_research_missions WHERE mission_id=%s AND user_id=%s FOR UPDATE", (str(mission_id), int(user_id)))
        mission = cur.fetchone()
        if not mission:
            raise projects.ProjectError("research_mission_not_found", 404)
        if mission["status"] in {"blocked", "cancelled", "completed"}:
            raise projects.ProjectError("research_mission_not_active", 409)
        mission_safety = json.loads(mission["safety_json"])
        if mission_safety.get("read_only_only"):
            decision = {**decision, "read_only_only": True, "inherited_read_only": True}
        hypothesis_id = str(uuid.uuid4())
        state = "blocked" if decision["decision"] == "blocked" else "proposed"
        cur.execute("""INSERT INTO velia_research_hypotheses(
            hypothesis_id,mission_id,user_id,title,rationale,status,safety_json)
            VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (hypothesis_id, str(mission_id), int(user_id), title, rationale, state, _json(decision)))
        _event(cur, str(mission_id), user_id, "hypothesis_created",
               {"hypothesis_id": hypothesis_id, "status": state, "safety": decision})
        return {"id": hypothesis_id, "title": title, "rationale": rationale,
                "status": state, "safety": decision, "evidence_grade": "unknown"}


def plan_experiment(user_id: int, mission_id: str, method: Dict[str, Any], hypothesis_id: Optional[str] = None) -> Dict[str, Any]:
    if not isinstance(method, dict) or not method:
        raise projects.ProjectError("invalid_experiment_plan")
    encoded = _json(method)
    if len(encoded) > 12000:
        raise projects.ProjectError("invalid_experiment_plan")
    decision = safety.classify(encoded, phase="experiment")
    with projects.transaction(user_id) as cur:
        cur.execute("SELECT status,safety_json FROM velia_research_missions WHERE mission_id=%s AND user_id=%s FOR UPDATE", (str(mission_id), int(user_id)))
        mission = cur.fetchone()
        if not mission:
            raise projects.ProjectError("research_mission_not_found", 404)
        if mission["status"] in {"blocked", "cancelled", "completed"}:
            raise projects.ProjectError("research_mission_not_active", 409)
        inherited_read_only = bool(json.loads(mission["safety_json"]).get("read_only_only"))
        if hypothesis_id:
            cur.execute("""SELECT status,safety_json FROM velia_research_hypotheses
                WHERE hypothesis_id=%s AND mission_id=%s AND user_id=%s""",
                        (str(hypothesis_id), str(mission_id), int(user_id)))
            hypothesis = cur.fetchone()
            if not hypothesis:
                raise projects.ProjectError("research_hypothesis_not_found", 404)
            if hypothesis["status"] == "blocked":
                raise projects.ProjectError("research_hypothesis_not_active", 409)
            inherited_read_only = inherited_read_only or bool(
                json.loads(hypothesis["safety_json"]).get("read_only_only")
            )
        if inherited_read_only:
            decision = {**decision, "read_only_only": True, "inherited_read_only": True}
        executable = (
            bool(decision.get("execution_allowed"))
            and not bool(decision.get("read_only_only"))
            and experiment_execution_enabled()
        )
        experiment_id = str(uuid.uuid4())
        state = "blocked" if decision["decision"] == "blocked" else "planned"
        cur.execute("""INSERT INTO velia_research_experiments(
            experiment_id,hypothesis_id,mission_id,user_id,status,method_json,safety_json)
            VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (experiment_id, hypothesis_id, str(mission_id), int(user_id), state, encoded, _json(decision)))
        _event(cur, str(mission_id), user_id, "experiment_planned",
               {"experiment_id": experiment_id, "status": state, "execution_ready": executable,
                "safety": decision})
        return {"id": experiment_id, "hypothesis_id": hypothesis_id, "status": state,
                "execution_ready": executable, "safety": decision, "method": method}


def cancel_mission(user_id: int, mission_id: str) -> Dict[str, Any]:
    with projects.transaction(user_id) as cur:
        cur.execute("SELECT * FROM velia_research_missions WHERE mission_id=%s AND user_id=%s FOR UPDATE", (str(mission_id), int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_mission_not_found", 404)
        if row["status"] not in {"completed", "cancelled"}:
            cur.execute("UPDATE velia_research_missions SET status='cancelled',updated_at=NOW() WHERE mission_id=%s AND user_id=%s RETURNING *",
                        (str(mission_id), int(user_id)))
            row = cur.fetchone()
            _event(cur, str(mission_id), user_id, "mission_cancelled", {})
        return _mission(row)
