"""Eight acceptance cases for the consequence-inheritance experiment."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
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


class CustomString(str):
    pass


class SampleEnum(Enum):
    VALUE = "value"


class CustomProposedConsequence(ProposedConsequence):
    pass


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

    def test_supported_payload_values_are_accepted(self) -> None:
        supported = (
            None,
            True,
            7,
            1.25,
            "text",
            [],
            {},
            {"nested": [None, False, 3, 2.5, "value"]},
        )

        for index, payload in enumerate(supported):
            with self.subTest(index=index, payload_type=type(payload).__name__):
                submitted = self.store.submit(
                    "execution-a",
                    f"supported-{index}",
                    ProposedConsequence("synthetic_task", "target-x", payload),
                )
                self.assertEqual(submitted.payload, payload)

    def test_unsupported_top_level_payload_values_are_rejected(self) -> None:
        unsupported = (
            b"bytes",
            bytearray(b"bytearray"),
            ("tuple",),
            {"set"},
            frozenset({"frozenset"}),
            1 + 2j,
            Decimal("1.25"),
            date(2026, 1, 1),
            datetime(2026, 1, 1, 12, 0),
            time(12, 0),
            SampleEnum.VALUE,
            object(),
            lambda: None,
        )

        for index, payload in enumerate(unsupported):
            with self.subTest(index=index, payload_type=type(payload).__name__):
                with self.assertRaises(TypeError):
                    self.store.submit(
                        "execution-a",
                        f"unsupported-{index}",
                        ProposedConsequence("synthetic_task", "target-x", payload),
                    )

    def test_non_string_dict_key_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            self.store.submit(
                "execution-a",
                "non-string-key",
                ProposedConsequence("synthetic_task", "target-x", {1: "value"}),
            )

    def test_non_finite_float_is_rejected(self) -> None:
        for index, value in enumerate((float("nan"), float("inf"), float("-inf"))):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    self.store.submit(
                        "execution-a",
                        f"non-finite-{index}",
                        ProposedConsequence("synthetic_task", "target-x", value),
                    )

    def test_nested_unsupported_values_are_rejected(self) -> None:
        unsupported = (
            {"items": [object()]},
            {"nested": {"value": b"bytes"}},
        )

        for index, payload in enumerate(unsupported):
            with self.subTest(index=index):
                with self.assertRaises(TypeError):
                    self.store.submit(
                        "execution-a",
                        f"nested-unsupported-{index}",
                        ProposedConsequence("synthetic_task", "target-x", payload),
                    )

    def test_payload_cycles_are_rejected(self) -> None:
        self_referential_list: list[object] = []
        self_referential_list.append(self_referential_list)

        self_referential_dict: dict[str, object] = {}
        self_referential_dict["self"] = self_referential_dict

        indirect_list: list[object] = []
        indirect_dict: dict[str, object] = {"list": indirect_list}
        indirect_list.append(indirect_dict)

        for index, payload in enumerate(
            (self_referential_list, self_referential_dict, indirect_list)
        ):
            with self.subTest(index=index):
                with self.assertRaisesRegex(
                    ValueError, "consequence payload contains a cycle"
                ):
                    self.store.submit(
                        "execution-a",
                        f"cycle-{index}",
                        ProposedConsequence("synthetic_task", "target-x", payload),
                    )

    def test_shared_non_cyclic_reference_is_accepted(self) -> None:
        shared = {"value": "safe"}
        payload = {"left": shared, "right": shared}

        submitted = self.store.submit(
            "execution-a",
            "shared-reference",
            ProposedConsequence("synthetic_task", "target-x", payload),
        )

        self.assertEqual(
            submitted.payload,
            {"left": {"value": "safe"}, "right": {"value": "safe"}},
        )

    def test_rejected_submit_does_not_reserve_consequence_id(self) -> None:
        with self.assertRaises(TypeError):
            self.store.submit(
                "execution-a",
                "reusable-id",
                ProposedConsequence("synthetic_task", "target-x", object()),
            )
        with self.assertRaises(KeyError):
            self.store.read("execution-a", "reusable-id")

        submitted = self.store.submit(
            "execution-a",
            "reusable-id",
            ProposedConsequence("synthetic_task", "target-x", {"valid": True}),
        )
        self.assertEqual(submitted.payload, {"valid": True})

    def test_consequence_type_and_target_require_exact_str(self) -> None:
        invalid_values = (None, 1, CustomString("custom"))

        for field_name in ("consequence_type", "target"):
            for index, invalid_value in enumerate(invalid_values):
                with self.subTest(field=field_name, index=index):
                    fields: dict[str, object] = {
                        "consequence_type": "synthetic_task",
                        "target": "target-x",
                        "payload": {"valid": True},
                    }
                    fields[field_name] = invalid_value
                    with self.assertRaises(TypeError):
                        self.store.submit(
                            "execution-a",
                            f"invalid-{field_name}-{index}",
                            ProposedConsequence(**fields),  # type: ignore[arg-type]
                        )

    def test_exact_proposed_consequence_is_accepted(self) -> None:
        submitted = self.store.submit(
            "execution-a",
            "exact-consequence",
            ProposedConsequence("synthetic_task", "target-x", {"valid": True}),
        )

        self.assertEqual(submitted.consequence_type, "synthetic_task")
        self.assertEqual(submitted.target, "target-x")

    def test_proposed_consequence_subclass_is_rejected_and_id_remains_reusable(
        self,
    ) -> None:
        subclass_consequence = CustomProposedConsequence(
            "synthetic_task", "target-x", {"valid": True}
        )

        with self.assertRaises(TypeError):
            self.store.submit(
                "execution-a", "subclass-id", subclass_consequence
            )
        with self.assertRaises(KeyError):
            self.store.read("execution-a", "subclass-id")

        submitted = self.store.submit(
            "execution-a",
            "subclass-id",
            ProposedConsequence("synthetic_task", "target-x", {"valid": True}),
        )
        self.assertEqual(submitted.payload, {"valid": True})

    def test_failed_detached_return_copy_does_not_store_or_reserve_id(self) -> None:
        with patch(
            "reference.swampbox._copy_contained_consequence",
            side_effect=RuntimeError("detached copy failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "detached copy failed"):
                self.store.submit(
                    "execution-a",
                    "return-copy-id",
                    ProposedConsequence(
                        "synthetic_task", "target-x", {"valid": True}
                    ),
                )

        with self.assertRaises(KeyError):
            self.store.read("execution-a", "return-copy-id")

        submitted = self.store.submit(
            "execution-a",
            "return-copy-id",
            ProposedConsequence("synthetic_task", "target-x", {"valid": True}),
        )
        self.assertEqual(submitted.payload, {"valid": True})


if __name__ == "__main__":
    unittest.main()
