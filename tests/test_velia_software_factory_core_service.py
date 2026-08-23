import unittest

from services.velia_software_factory_core_service import (
    Clarifier,
    FactoryStateMachine,
    ProjectBrain,
    ProjectSpec,
    SoftwareFactoryError,
    TaskDAG,
)


class ProjectSpecTests(unittest.TestCase):
    def test_fingerprint_is_semantic_and_stable(self):
        payload = {
            "project_id": "p1",
            "title": "Shop",
            "objective": "Build checkout",
            "allowed_paths": ["services/", "tests/"],
            "acceptance_criteria": ["tests pass"],
        }
        a = ProjectSpec.from_payload(payload)
        b = ProjectSpec.from_payload({**payload, "spec_id": "other", "version": 9})
        self.assertEqual(a.fingerprint, b.fingerprint)

    def test_duplicate_deliverable_ids_rejected(self):
        with self.assertRaises(SoftwareFactoryError):
            ProjectSpec.from_payload(
                {"project_id": "p1", "deliverables": [{"id": "x"}, {"id": "x"}]}
            )

    def test_invalid_spec_version_rejected(self):
        with self.assertRaises(SoftwareFactoryError) as ctx:
            ProjectSpec.from_payload({"project_id": "p1", "version": "not-a-number"})
        self.assertEqual("velia_factory_spec_version_invalid", ctx.exception.code)


class ProjectBrainTests(unittest.TestCase):
    def test_brain_deduplicates_same_fact(self):
        brain = ProjectBrain()
        self.assertIsNotNone(brain.add("constraint", "No deploy", "spec"))
        self.assertIsNone(brain.add("constraint", "No deploy", "spec"))
        self.assertEqual(1, len(brain.snapshot()))


class StateMachineTests(unittest.TestCase):
    def test_happy_path(self):
        state = "draft"
        for target in ("ready", "planning", "executing", "reviewing", "completed"):
            state = FactoryStateMachine.transition(state, target)
        self.assertEqual("completed", state)

    def test_terminal_transition_rejected(self):
        with self.assertRaises(SoftwareFactoryError):
            FactoryStateMachine.transition("completed", "executing")


class TaskDagTests(unittest.TestCase):
    def test_dependency_gate(self):
        spec = ProjectSpec.from_payload(
            {
                "project_id": "p1",
                "objective": "Build API and UI",
                "allowed_paths": ["services/", "webapp/"],
                "acceptance_criteria": ["tests pass"],
                "deliverables": [
                    {"id": "api", "title": "API", "goal": "Build API", "allowed_paths": ["services/"]},
                    {
                        "id": "ui",
                        "title": "UI",
                        "goal": "Build UI",
                        "depends_on": ["api"],
                        "allowed_paths": ["webapp/"],
                    },
                ],
            }
        )
        dag = TaskDAG.from_spec(spec)
        self.assertEqual(["api"], [task.task_id for task in dag.ready_tasks()])
        dag.set_status("api", "succeeded")
        self.assertEqual(["ui"], [task.task_id for task in dag.ready_tasks()])

    def test_cycle_rejected(self):
        spec = ProjectSpec.from_payload(
            {
                "project_id": "p1",
                "objective": "x",
                "allowed_paths": ["services/"],
                "deliverables": [
                    {"id": "a", "depends_on": ["b"]},
                    {"id": "b", "depends_on": ["a"]},
                ],
            }
        )
        with self.assertRaises(SoftwareFactoryError):
            TaskDAG.from_spec(spec)


class ClarifierTests(unittest.TestCase):
    def test_blocks_only_material_gaps(self):
        spec = ProjectSpec.from_payload({"project_id": "p1", "objective": "Implement endpoint"})
        result = Clarifier().evaluate(spec)
        self.assertTrue(result.blocking)
        self.assertIn("allowed_paths", {q["key"] for q in result.questions})
        self.assertTrue(result.assumptions)

    def test_ready_when_write_scope_and_objective_known(self):
        spec = ProjectSpec.from_payload(
            {
                "project_id": "p1",
                "objective": "Implement endpoint",
                "allowed_paths": ["services/"],
                "acceptance_criteria": ["unit tests pass"],
            }
        )
        self.assertFalse(Clarifier().evaluate(spec).blocking)


if __name__ == "__main__":
    unittest.main()
