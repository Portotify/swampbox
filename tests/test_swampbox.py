"""Eight acceptance cases for the consequence-inheritance experiment."""

from __future__ import annotations

import unittest

from reference.swampbox import (
    AdmissionResult,
    ArtifactStore,
    PersistedArtifact,
    ProposedConsequence,
    ReceiptSink,
    SimulatedActuator,
    SwampBoxBoundary,
    SyntheticAdmissionProvider,
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


if __name__ == "__main__":
    unittest.main()

