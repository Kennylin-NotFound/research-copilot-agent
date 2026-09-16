import unittest
from uuid import uuid4
from pydantic import ValidationError
from product.contracts import (ProjectState, StatePatch, RunBudget, apply_state_patch,
    MessageIntent, FileVersion, RunStatus, validate_transition, ToolObservation, EvidenceRef, SkillRuntime)


class ContractsTest(unittest.TestCase):
    def test_revision_conflict_cannot_overwrite(self):
        state = ProjectState(objective="original", selected_file_ids=[uuid4()])
        with self.assertRaisesRegex(ValueError, "version_conflict"):
            apply_state_patch(state, 3, StatePatch(expected_revision=2, objective="late"))
        self.assertEqual(state.objective, "original")

    def test_patch_preserves_existing_evidence_selection(self):
        state = ProjectState(objective="original", selected_file_ids=[uuid4()])
        updated = apply_state_patch(state, 0, StatePatch(expected_revision=0, dimensions=["memory"]))
        self.assertEqual(state.selected_file_ids, updated.selected_file_ids)
        self.assertEqual(state.objective, updated.objective)
        self.assertEqual(updated.dimensions, ["memory"])

    def test_invalid_patch_and_unknown_fields_rejected(self):
        for data in ({"expected_revision": 0}, {"expected_revision": 0, "objective": None},
                     {"expected_revision": 0, "owner_id": str(uuid4())},
                     {"expected_revision": -1, "objective": "bad"}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                StatePatch.model_validate(data)

    def test_budget_cannot_be_unbounded(self):
        for data in ({"max_steps": 0}, {"max_retries": 100}, {"timeout_seconds": 10000}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                RunBudget.model_validate(data)

    def test_explicit_empty_selection_clears_only_selection(self):
        state = ProjectState(objective="keep", selected_file_ids=[uuid4()])
        updated = apply_state_patch(state, 1, StatePatch(expected_revision=1, selected_file_ids=[]))
        self.assertEqual(updated.selected_file_ids, [])
        self.assertEqual(updated.objective, "keep")

    def test_ambiguous_intents_cannot_silently_modify_state(self):
        with self.assertRaises(ValidationError):
            MessageIntent(kind="ask", patch=StatePatch(expected_revision=0, objective="overwrite"))
        with self.assertRaises(ValidationError):
            MessageIntent(kind="clarify")
        self.assertEqual(MessageIntent(kind="clarify", question="Which files?").kind, "clarify")

    def test_ready_requires_completed_index_and_original_evidence(self):
        data = dict(file_version_id=uuid4(), file_id=uuid4(), project_id=uuid4(), owner_id=uuid4(),
                    version=1, content_sha256="a" * 64, size_bytes=200, kind="original",
                    media_type="application/pdf", status="ready")
        with self.assertRaises(ValidationError):
            FileVersion(**data)
        data.update(parser_version="1", embedding_model="fixture", embedding_dimension=3)
        self.assertEqual(FileVersion(**data).status, "ready")
        data["kind"] = "generated"
        with self.assertRaises(ValidationError):
            FileVersion(**data)

    def test_terminal_and_cancelling_runs_cannot_publish_success(self):
        for previous in (RunStatus.CANCELLING, RunStatus.CANCELLED, RunStatus.FAILED, RunStatus.COMPLETED):
            with self.subTest(previous=previous), self.assertRaises(ValueError):
                validate_transition(previous, RunStatus.COMPLETED)
        validate_transition(RunStatus.RUNNING, RunStatus.COMPLETED)

    def test_error_tool_result_requires_error_category(self):
        with self.assertRaises(ValidationError):
            ToolObservation(action_id=uuid4(), run_id=uuid4(), attempt=1, tool="read_chunks", status="error")

    def test_no_arbitrary_executable_tools_in_skill_contract(self):
        with self.assertRaises(ValidationError):
            SkillRuntime(skill_id="evidence-qa", version="1", body_sha256="b" * 64,
                         tools=["shell"], output_type="answer")


if __name__ == "__main__":
    unittest.main()
