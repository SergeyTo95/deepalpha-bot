from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


class SoftwareFactoryError(RuntimeError):
    def __init__(self, code: str, *, detail: str = "", status: int = 400) -> None:
        super().__init__(code)
        self.code = str(code)
        self.detail = str(detail or "")[:1000]
        self.status = int(status)


def _text(value: Any, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _str_list(value: Any, *, limit: int = 100, item_limit: int = 1000) -> List[str]:
    if value is None:
        return []
    source = value if isinstance(value, (list, tuple, set)) else [value]
    result: List[str] = []
    for raw in source:
        item = _text(raw, item_limit)
        if item and item not in result:
            result.append(item)
        if len(result) >= limit:
            break
    return result


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class ProjectSpec:
    project_id: str
    title: str
    objective: str
    acceptance_criteria: Tuple[str, ...]
    constraints: Tuple[str, ...] = ()
    allowed_paths: Tuple[str, ...] = ()
    blocked_paths: Tuple[str, ...] = ()
    deliverables: Tuple[Mapping[str, Any], ...] = ()
    assumptions: Tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    spec_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    version: int = 1

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ProjectSpec":
        project_id = _text(payload.get("project_id"), 160)
        title = _text(payload.get("title") or payload.get("name"), 200)
        objective = _text(payload.get("objective") or payload.get("goal"), 12000)
        if not project_id:
            raise SoftwareFactoryError("velia_factory_project_id_required")

        deliverables_raw = payload.get("deliverables") or []
        if not isinstance(deliverables_raw, (list, tuple)):
            raise SoftwareFactoryError("velia_factory_deliverables_invalid")
        deliverables: List[Mapping[str, Any]] = []
        seen_ids: Set[str] = set()
        for index, raw in enumerate(deliverables_raw):
            if not isinstance(raw, Mapping):
                raise SoftwareFactoryError("velia_factory_deliverable_invalid")
            item = dict(raw)
            task_id = _text(item.get("id") or f"task-{index + 1}", 120)
            if task_id in seen_ids:
                raise SoftwareFactoryError("velia_factory_deliverable_id_duplicate", detail=task_id)
            seen_ids.add(task_id)
            item["id"] = task_id
            item["title"] = _text(item.get("title") or task_id, 240)
            item["goal"] = _text(item.get("goal") or item.get("objective") or item["title"], 8000)
            item["kind"] = _text(item.get("kind") or "coding", 80).lower()
            item["depends_on"] = _str_list(item.get("depends_on"), limit=50, item_limit=120)
            item["allowed_paths"] = _str_list(item.get("allowed_paths"), limit=100, item_limit=500)
            deliverables.append(item)

        try:
            version = max(1, int(payload.get("version") or 1))
        except (TypeError, ValueError) as exc:
            raise SoftwareFactoryError("velia_factory_spec_version_invalid") from exc

        metadata = payload.get("metadata") or {}
        return cls(
            project_id=project_id,
            title=title,
            objective=objective,
            acceptance_criteria=tuple(_str_list(payload.get("acceptance_criteria"), limit=100, item_limit=2000)),
            constraints=tuple(_str_list(payload.get("constraints"), limit=100, item_limit=2000)),
            allowed_paths=tuple(_str_list(payload.get("allowed_paths"), limit=100, item_limit=500)),
            blocked_paths=tuple(_str_list(payload.get("blocked_paths"), limit=100, item_limit=500)),
            deliverables=tuple(deliverables),
            assumptions=tuple(_str_list(payload.get("assumptions"), limit=100, item_limit=2000)),
            metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
            spec_id=_text(payload.get("spec_id"), 160) or str(uuid.uuid4()),
            version=version,
        )

    def semantic_payload(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "title": self.title,
            "objective": self.objective,
            "acceptance_criteria": list(self.acceptance_criteria),
            "constraints": list(self.constraints),
            "allowed_paths": list(self.allowed_paths),
            "blocked_paths": list(self.blocked_paths),
            "deliverables": [dict(item) for item in self.deliverables],
            "assumptions": list(self.assumptions),
            "metadata": dict(self.metadata),
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(canonical_json(self.semantic_payload()).encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "version": self.version,
            "fingerprint": self.fingerprint,
            **self.semantic_payload(),
        }


@dataclass(frozen=True)
class BrainEntry:
    kind: str
    text: str
    source: str
    confidence: float = 1.0
    metadata: Mapping[str, Any] = field(default_factory=dict)
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "kind": self.kind,
            "text": self.text,
            "source": self.source,
            "confidence": round(min(1.0, max(0.0, float(self.confidence))), 4),
            "metadata": dict(self.metadata),
        }


class ProjectBrain:
    def __init__(self, entries: Optional[Iterable[Mapping[str, Any] | BrainEntry]] = None) -> None:
        self._entries: List[BrainEntry] = []
        self._keys: Set[Tuple[str, str, str]] = set()
        for entry in entries or []:
            if isinstance(entry, BrainEntry):
                self.add_entry(entry)
            elif isinstance(entry, Mapping):
                self.add(
                    kind=entry.get("kind"),
                    text=entry.get("text"),
                    source=entry.get("source"),
                    confidence=entry.get("confidence", 1.0),
                    metadata=entry.get("metadata") or {},
                    entry_id=entry.get("entry_id"),
                )

    @classmethod
    def from_spec(cls, spec: ProjectSpec) -> "ProjectBrain":
        brain = cls()
        if spec.objective:
            brain.add("objective", spec.objective, "project_spec")
        for item in spec.acceptance_criteria:
            brain.add("acceptance_criterion", item, "project_spec")
        for item in spec.constraints:
            brain.add("constraint", item, "project_spec")
        for item in spec.assumptions:
            brain.add("assumption", item, "project_spec", confidence=0.7)
        return brain

    def add(
        self,
        kind: Any,
        text: Any,
        source: Any,
        *,
        confidence: Any = 1.0,
        metadata: Optional[Mapping[str, Any]] = None,
        entry_id: Any = None,
    ) -> Optional[BrainEntry]:
        normalized = BrainEntry(
            kind=_text(kind, 80) or "fact",
            text=_text(text, 8000),
            source=_text(source, 200) or "unknown",
            confidence=float(confidence or 0.0),
            metadata=dict(metadata or {}),
            entry_id=_text(entry_id, 160) or str(uuid.uuid4()),
        )
        if not normalized.text:
            return None
        return self.add_entry(normalized)

    def add_entry(self, entry: BrainEntry) -> Optional[BrainEntry]:
        key = (entry.kind.lower(), entry.text.strip().lower(), entry.source.lower())
        if key in self._keys:
            return None
        self._keys.add(key)
        self._entries.append(entry)
        return entry

    def snapshot(self) -> List[Dict[str, Any]]:
        return [entry.to_dict() for entry in self._entries]


FACTORY_STATES = {
    "draft",
    "clarifying",
    "ready",
    "planning",
    "executing",
    "validating",
    "repairing",
    "reviewing",
    "blocked",
    "completed",
    "failed",
    "cancelled",
}
TERMINAL_STATES = {"completed", "failed", "cancelled"}
_TRANSITIONS: Dict[str, Set[str]] = {
    "draft": {"clarifying", "ready", "failed", "cancelled"},
    "clarifying": {"ready", "blocked", "failed", "cancelled"},
    "ready": {"planning", "blocked", "failed", "cancelled"},
    "planning": {"clarifying", "executing", "blocked", "failed", "cancelled"},
    "executing": {"validating", "repairing", "reviewing", "blocked", "completed", "failed", "cancelled"},
    "validating": {"reviewing", "repairing", "blocked", "completed", "failed", "cancelled"},
    "repairing": {"executing", "validating", "blocked", "failed", "cancelled"},
    "reviewing": {"completed", "repairing", "blocked", "failed", "cancelled"},
    "blocked": {"clarifying", "ready", "planning", "executing", "failed", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


class FactoryStateMachine:
    @staticmethod
    def can_transition(current: str, target: str) -> bool:
        return target in _TRANSITIONS.get(current, set())

    @staticmethod
    def transition(current: str, target: str) -> str:
        current = _text(current, 40).lower()
        target = _text(target, 40).lower()
        if current not in FACTORY_STATES or target not in FACTORY_STATES:
            raise SoftwareFactoryError("velia_factory_state_invalid", detail=f"{current}->{target}")
        if not FactoryStateMachine.can_transition(current, target):
            raise SoftwareFactoryError(
                "velia_factory_transition_invalid",
                detail=f"{current}->{target}",
                status=409,
            )
        return target


TASK_STATES = {"pending", "ready", "dispatched", "running", "succeeded", "failed", "blocked", "cancelled"}


@dataclass
class FactoryTask:
    task_id: str
    title: str
    goal: str
    kind: str = "coding"
    depends_on: List[str] = field(default_factory=list)
    allowed_paths: List[str] = field(default_factory=list)
    status: str = "pending"
    external_ref: str = ""
    result: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "goal": self.goal,
            "kind": self.kind,
            "depends_on": list(self.depends_on),
            "allowed_paths": list(self.allowed_paths),
            "status": self.status,
            "external_ref": self.external_ref,
            "result": dict(self.result),
        }


class TaskDAG:
    def __init__(self, tasks: Sequence[FactoryTask]) -> None:
        self.tasks: Dict[str, FactoryTask] = {}
        for task in tasks:
            if task.task_id in self.tasks:
                raise SoftwareFactoryError("velia_factory_task_duplicate", detail=task.task_id)
            if task.status not in TASK_STATES:
                raise SoftwareFactoryError("velia_factory_task_state_invalid", detail=task.status)
            self.tasks[task.task_id] = task
        self._validate()

    @classmethod
    def from_spec(cls, spec: ProjectSpec) -> "TaskDAG":
        tasks: List[FactoryTask] = []
        if spec.deliverables:
            for raw in spec.deliverables:
                task_paths = _str_list(raw.get("allowed_paths"), limit=100, item_limit=500) or list(spec.allowed_paths)
                tasks.append(
                    FactoryTask(
                        task_id=_text(raw.get("id"), 120),
                        title=_text(raw.get("title"), 240),
                        goal=_text(raw.get("goal"), 8000),
                        kind=_text(raw.get("kind") or "coding", 80).lower(),
                        depends_on=_str_list(raw.get("depends_on"), limit=50, item_limit=120),
                        allowed_paths=task_paths,
                    )
                )
        else:
            tasks.append(
                FactoryTask(
                    task_id="implementation",
                    title=spec.title or "Implement project objective",
                    goal=spec.objective,
                    kind="coding",
                    allowed_paths=list(spec.allowed_paths),
                )
            )
        return cls(tasks)

    def _validate(self) -> None:
        for task in self.tasks.values():
            missing = [dep for dep in task.depends_on if dep not in self.tasks]
            if missing:
                raise SoftwareFactoryError(
                    "velia_factory_dependency_missing",
                    detail=f"{task.task_id}:{','.join(missing)}",
                )
            if task.task_id in task.depends_on:
                raise SoftwareFactoryError("velia_factory_dependency_cycle", detail=task.task_id)

        visiting: Set[str] = set()
        visited: Set[str] = set()

        def walk(task_id: str) -> None:
            if task_id in visiting:
                raise SoftwareFactoryError("velia_factory_dependency_cycle", detail=task_id)
            if task_id in visited:
                return
            visiting.add(task_id)
            for dep in self.tasks[task_id].depends_on:
                walk(dep)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in self.tasks:
            walk(task_id)

    def ready_tasks(self) -> List[FactoryTask]:
        result: List[FactoryTask] = []
        for task in self.tasks.values():
            if task.status not in {"pending", "ready"}:
                continue
            if all(self.tasks[dep].status == "succeeded" for dep in task.depends_on):
                task.status = "ready"
                result.append(task)
        return result

    def set_status(
        self,
        task_id: str,
        status: str,
        *,
        external_ref: str = "",
        result: Optional[Mapping[str, Any]] = None,
    ) -> FactoryTask:
        task = self.tasks.get(task_id)
        if not task:
            raise SoftwareFactoryError("velia_factory_task_not_found", detail=task_id, status=404)
        if status not in TASK_STATES:
            raise SoftwareFactoryError("velia_factory_task_state_invalid", detail=status)
        task.status = status
        if external_ref:
            task.external_ref = _text(external_ref, 300)
        if result is not None:
            task.result = dict(result)
        return task

    def complete(self) -> bool:
        return bool(self.tasks) and all(task.status == "succeeded" for task in self.tasks.values())

    def snapshot(self) -> List[Dict[str, Any]]:
        return [task.to_dict() for task in self.tasks.values()]


@dataclass(frozen=True)
class ClarificationResult:
    questions: Tuple[Mapping[str, Any], ...] = ()
    assumptions: Tuple[str, ...] = ()

    @property
    def blocking(self) -> bool:
        return bool(self.questions)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blocking": self.blocking,
            "questions": [dict(question) for question in self.questions],
            "assumptions": list(self.assumptions),
        }


class Clarifier:
    """Deterministic material-gap gate. Low-risk gaps become assumptions; write-scope gaps block execution."""

    def evaluate(self, spec: ProjectSpec) -> ClarificationResult:
        questions: List[Mapping[str, Any]] = []
        assumptions: List[str] = []
        if not spec.objective:
            questions.append(
                {
                    "key": "objective",
                    "question": "What should VELIA build or change?",
                    "reason": "objective_required",
                }
            )

        coding_needed = not spec.deliverables or any(
            _text(item.get("kind") or "coding").lower() == "coding" for item in spec.deliverables
        )
        explicit_task_paths = any(_str_list(item.get("allowed_paths")) for item in spec.deliverables)
        if coding_needed and not spec.allowed_paths and not explicit_task_paths:
            questions.append(
                {
                    "key": "allowed_paths",
                    "question": "Which repository paths may the autonomous team modify?",
                    "reason": "write_scope_required",
                }
            )

        if not spec.acceptance_criteria:
            assumptions.append("Use repository tests, static checks and existing CI as the minimum acceptance criteria.")
        if not spec.title:
            assumptions.append("Derive the working title from the objective.")
        return ClarificationResult(tuple(questions), tuple(assumptions))
