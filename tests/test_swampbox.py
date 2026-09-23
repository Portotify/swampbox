"""Eight acceptance cases for the consequence-inheritance experiment."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from reference.swampbox import (
    AdmissionResult,
    ArtifactStore,
    ContainedConsequence,
    ExecutionScopedConsequenceStore,
    PersistedArtifact,
    ProposedConsequence,
    ReceiptSink,
    SimulatedActuator,
    SwampBoxBoundary,
    SyntheticAdmissionProvider,
    same_material_consequence,
)


class SwampBoxInheritanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = ArtifactStore()
        self.actuator = SimulatedActuator()
        self.sink = ReceiptSink()
        self.boundary = SwampBoxBoundary(self.actuator, self.sink)
        self.artifact = PersistedArtifact(
            artifact_id="artifact-a1",
            origin="agent-a",
            task_type="create_review_item",
            target="target-x",
            payload={"title": "content-y"},
        )

    def _persisted_consequence(self) -> ProposedConsequence:
        self.store.persist(self.artifact)
        restored = self.store.read(self.artifact.artifact_id)
        self.assertIsNotNone(restored)
        return ProposedConsequence(
            consequence_type="synthetic_task",
            target=restored.target,
            payload=restored.payload,
        )

    def test_persisted_artifact_fresh_allow_exact_match_commits(self) -> None:
        proposed = self._persisted_consequence()
        provider = SyntheticAdmissionProvider("ALLOW")

        receipt = self.boundary.commit(proposed, provider.admit(proposed))

        self.assertTrue(receipt.committed)
        self.assertEqual(receipt.outcome, "committed")
        self.assertEqual(self.actuator.commits, [proposed])

    def test_persisted_artifact_without_admission_does_not_commit(self) -> None:
        proposed = self._persisted_consequence()

        receipt = self.boundary.commit(proposed, None)

        self.assertFalse(receipt.committed)
        self.assertEqual(len(self.actuator.commits), 0)

    def test_persisted_artifact_with_deny_does_not_commit(self) -> None:
        proposed = self._persisted_consequence()
        provider = SyntheticAdmissionProvider("DENY")

        receipt = self.boundary.commit(proposed, provider.admit(proposed))

        self.assertFalse(receipt.committed)
        self.assertEqual(len(self.actuator.commits), 0)

    def test_persisted_artifact_with_hold_does_not_commit(self) -> None:
        proposed = self._persisted_consequence()
        provider = SyntheticAdmissionProvider("HOLD")

        receipt = self.boundary.commit(proposed, provider.admit(proposed))

        self.assertFalse(receipt.committed)
        self.assertEqual(len(self.actuator.commits), 0)

    def test_different_admitted_consequence_does_not_commit(self) -> None:
        proposed = self._persisted_consequence()
        different = ProposedConsequence(
            consequence_type=proposed.consequence_type,
            target="target-z",
            payload=proposed.payload,
        )
        provider = SyntheticAdmissionProvider("ALLOW", different)

        receipt = self.boundary.commit(proposed, provider.admit(proposed))

        self.assertFalse(receipt.committed)
        self.assertEqual(len(self.actuator.commits), 0)

    def test_agent_a_ends_but_artifact_remains_readable_without_permission(self) -> None:
        store = ArtifactStore()

        def agent_a_run() -> None:
            store.persist(self.artifact)

        agent_a_run()
        restored = store.read(self.artifact.artifact_id)

        self.assertEqual(restored, self.artifact)
        proposed = ProposedConsequence(
            consequence_type="synthetic_task",
            target=restored.target,
            payload=restored.payload,
        )
        receipt = self.boundary.commit(proposed, None)

        self.assertFalse(receipt.committed)
        self.assertEqual(len(self.actuator.commits), 0)

    def test_origin_and_provenance_without_admission_does_not_commit(self) -> None:
        proposed = self._persisted_consequence()
        restored = self.store.read(self.artifact.artifact_id)

        self.assertEqual(restored.origin, "agent-a")
        receipt = self.boundary.commit(proposed, None)

        self.assertFalse(receipt.committed)
        self.assertEqual(len(self.actuator.commits), 0)

    def test_malformed_admission_does_not_commit(self) -> None:
        proposed = self._persisted_consequence()
        malformed = AdmissionResult("MAYBE", proposed)

        receipt = self.boundary.commit(proposed, malformed)

        self.assertFalse(receipt.committed)
        self.assertEqual(len(self.actuator.commits), 0)


class ExecutionScopedConsequenceContainmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = ExecutionScopedConsequenceStore()
        for execution_id in ("execution-a", "execution-b", "execution-c"):
            self.store.register_execution(execution_id)
        self.consequence = ProposedConsequence(
            consequence_type="synthetic_task",
            target="target-x",
            payload={"title": "content-y"},
        )

    def _submit(self) -> ContainedConsequence:
        return self.store.submit("execution-a", "consequence-x", self.consequence)

    def test_execution_a_can_submit_and_read_own_consequence(self) -> None:
        submitted = self._submit()

        self.assertEqual(
            self.store.read("execution-a", "consequence-x"), submitted
        )

    def test_execution_b_cannot_read_before_explicit_transfer(self) -> None:
        self._submit()

        with self.assertRaises(KeyError):
            self.store.read("execution-b", "consequence-x")

    def test_explicit_transfer_makes_consequence_visible_to_b(self) -> None:
        self._submit()

        transferred = self.store.transfer(
            "execution-a", "execution-b", "consequence-x"
        )

        self.assertEqual(
            self.store.read("execution-b", "consequence-x"), transferred
        )
        with self.assertRaises(KeyError):
            self.store.read("execution-a", "consequence-x")

    def test_transfer_preserves_origin_and_material_fields(self) -> None:
        submitted = self._submit()

        transferred = self.store.transfer(
            "execution-a", "execution-b", "consequence-x"
        )

        self.assertEqual(transferred.origin_execution_id, "execution-a")
        self.assertEqual(transferred.current_execution_id, "execution-b")
        self.assertEqual(transferred.consequence_type, submitted.consequence_type)
        self.assertEqual(transferred.target, submitted.target)
        self.assertEqual(transferred.payload, submitted.payload)
        self.assertEqual(submitted.current_execution_id, "execution-a")

    def test_execution_c_cannot_read_consequence_transferred_to_b(self) -> None:
        self._submit()
        self.store.transfer("execution-a", "execution-b", "consequence-x")

        with self.assertRaises(KeyError):
            self.store.read("execution-c", "consequence-x")

    def test_non_current_execution_cannot_transfer_consequence(self) -> None:
        self._submit()

        with self.assertRaises(KeyError):
            self.store.transfer("execution-b", "execution-c", "consequence-x")

        self.assertEqual(
            self.store.read("execution-a", "consequence-x").current_execution_id,
            "execution-a",
        )

    def test_transfer_of_nonexistent_consequence_fails_closed(self) -> None:
        with self.assertRaises(KeyError):
            self.store.transfer("execution-a", "execution-b", "missing")

    def test_unregistered_execution_cannot_read_or_receive(self) -> None:
        self._submit()

        with self.assertRaises(KeyError):
            self.store.read("execution-unknown", "consequence-x")
        with self.assertRaises(KeyError):
            self.store.transfer(
                "execution-a", "execution-unknown", "consequence-x"
            )

    def test_malformed_identifiers_fail_closed_without_creating_visibility(self) -> None:
        with self.assertRaises(ValueError):
            self.store.read("", "consequence-x")
        with self.assertRaises(ValueError):
            self.store.transfer("execution-a", " ", "consequence-x")

    def test_transfer_does_not_invoke_admission_actuator_or_receipt_sink(self) -> None:
        self._submit()

        with (
            patch("reference.swampbox.SyntheticAdmissionProvider.admit") as admit,
            patch("reference.swampbox.SimulatedActuator.commit") as commit,
            patch("reference.swampbox.ReceiptSink.record") as record,
        ):
            transferred = self.store.transfer(
                "execution-a", "execution-b", "consequence-x"
            )

        admit.assert_not_called()
        commit.assert_not_called()
        record.assert_not_called()
        self.assertIsInstance(transferred, ContainedConsequence)

    def test_material_comparison_semantics_remain_unchanged(self) -> None:
        same = ProposedConsequence(
            consequence_type="synthetic_task",
            target="target-x",
            payload={"title": "content-y"},
        )
        different = ProposedConsequence(
            consequence_type="other_task",
            target="target-x",
            payload={"title": "content-y"},
        )

        self.assertTrue(same_material_consequence(self.consequence, same))
        self.assertFalse(same_material_consequence(self.consequence, different))

    def test_original_nested_payload_mutation_does_not_change_stored_snapshot(self) -> None:
        payload = {"nested": {"items": [{"title": "safe"}]}}
        consequence = ProposedConsequence("synthetic_task", "target-x", payload)
        self.store.submit("execution-a", "consequence-nested", consequence)

        payload["nested"]["items"][0]["title"] = "changed"
        payload["nested"]["items"].append({"title": "added"})

        stored = self.store.read("execution-a", "consequence-nested")
        self.assertEqual(
            stored.payload,
            {"nested": {"items": [{"title": "safe"}]}},
        )

    def test_submit_result_payload_cannot_mutate_internal_snapshot(self) -> None:
        submitted = self.store.submit(
            "execution-a",
            "consequence-nested",
            ProposedConsequence(
                "synthetic_task",
                "target-x",
                {"nested": {"items": [{"title": "safe"}]}},
            ),
        )

        submitted.payload["nested"]["items"][0]["title"] = "changed"

        self.assertEqual(
            self.store.read("execution-a", "consequence-nested").payload,
            {"nested": {"items": [{"title": "safe"}]}},
        )

    def test_read_result_nested_payload_cannot_mutate_internal_snapshot(self) -> None:
        self.store.submit(
            "execution-a",
            "consequence-nested",
            ProposedConsequence(
                "synthetic_task",
                "target-x",
                {"nested": {"items": [{"title": "safe"}]}},
            ),
        )
        read_result = self.store.read("execution-a", "consequence-nested")

        read_result.payload["nested"]["items"].append({"title": "changed"})

        self.assertEqual(
            self.store.read("execution-a", "consequence-nested").payload,
            {"nested": {"items": [{"title": "safe"}]}},
        )

    def test_transfer_preserves_material_snapshot(self) -> None:
        original_payload = {"nested": {"items": [{"title": "safe"}]}}
        self.store.submit(
            "execution-a",
            "consequence-nested",
            ProposedConsequence("synthetic_task", "target-x", original_payload),
        )
        original_payload["nested"]["items"][0]["title"] = "changed"

        self.store.transfer("execution-a", "execution-b", "consequence-nested")

        self.assertEqual(
            self.store.read("execution-b", "consequence-nested").payload,
            {"nested": {"items": [{"title": "safe"}]}},
        )

    def test_transfer_result_payload_cannot_mutate_b_scope(self) -> None:
        self.store.submit(
            "execution-a",
            "consequence-nested",
            ProposedConsequence(
                "synthetic_task",
                "target-x",
                {"nested": {"items": [{"title": "safe"}]}},
            ),
        )
        transferred = self.store.transfer(
            "execution-a", "execution-b", "consequence-nested"
        )

        transferred.payload["nested"]["items"][0]["title"] = "changed"

        self.assertEqual(
            self.store.read("execution-b", "consequence-nested").payload,
            {"nested": {"items": [{"title": "safe"}]}},
        )


if __name__ == "__main__":
    unittest.main()
