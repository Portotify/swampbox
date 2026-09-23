"""Eight acceptance cases for the consequence-inheritance experiment."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from dataclasses import replace
from enum import Enum
import unittest
from unittest.mock import patch

from experiments.legacy_synthetic import (
    AdmissionResult,
    ReceiptSink,
    SimulatedActuator,
    SwampBoxBoundary,
    SyntheticAdmissionProvider,
)
from reference.swampbox import (
    ArtifactStore,
    ContainedConsequence,
    ExecutionScopedConsequenceStore,
    ExecutionState,
    PersistedArtifact,
    ProposedConsequence,
    ActuatorOutcome,
    AuthorityDecision,
    AuthorityVerdict,
    ReleaseBoundary,
    ReleaseState,
    same_material_consequence,
)


class CustomString(str):
    pass


class CustomAuthorityDecision(AuthorityDecision):
    pass


class CustomDatetime(datetime):
    pass


class EqualitySpoof:
    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False


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
            patch("experiments.legacy_synthetic.SyntheticAdmissionProvider.admit") as admit,
            patch("experiments.legacy_synthetic.SimulatedActuator.commit") as commit,
            patch("experiments.legacy_synthetic.ReceiptSink.record") as record,
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


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class RecordingAuthorityProvider:
    def __init__(self, factory):
        self.factory = factory
        self.calls: list[tuple[ContainedConsequence, datetime]] = []

    def evaluate(
        self,
        consequence: ContainedConsequence,
        evaluated_at: datetime,
    ) -> AuthorityDecision:
        self.calls.append((consequence, evaluated_at))
        return self.factory(consequence, evaluated_at)


class RecordingReleaseActuator:
    def __init__(self, outcome: ActuatorOutcome | str) -> None:
        self.outcome = outcome
        self.calls: list[ContainedConsequence] = []

    def actuate(self, consequence: ContainedConsequence) -> ActuatorOutcome | str:
        self.calls.append(consequence)
        return self.outcome


class RaisingReleaseActuator:
    def __init__(self) -> None:
        self.calls: list[ContainedConsequence] = []

    def actuate(self, consequence: ContainedConsequence) -> ActuatorOutcome:
        self.calls.append(consequence)
        raise RuntimeError("external response lost")


class ReleaseBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = ExecutionScopedConsequenceStore()
        self.store.register_execution("execution-a")
        self.store.register_execution("execution-b")
        self.clock = MutableClock(datetime(2026, 1, 1, 12, 0, 0))
        self.consequence = ProposedConsequence(
            "synthetic_task", "target-x", {"title": "content-y"}
        )
        self.store.submit("execution-a", "consequence-x", self.consequence)

    def _decision(
        self,
        contained: ContainedConsequence,
        evaluated_at: datetime,
        *,
        verdict: str = AuthorityVerdict.ALLOW.value,
        consequence_id: str | None = None,
        execution_id: str | None = None,
        bound_consequence: ProposedConsequence | None = None,
        valid_until: datetime | None = None,
    ) -> AuthorityDecision:
        return AuthorityDecision(
            decision_id="decision-1",
            verdict=verdict,
            consequence_id=consequence_id or contained.consequence_id,
            execution_id=execution_id or contained.current_execution_id,
            bound_consequence=bound_consequence
            or ProposedConsequence(
                contained.consequence_type,
                contained.target,
                contained.payload,
            ),
            issued_at=evaluated_at,
            valid_until=valid_until or evaluated_at + timedelta(minutes=5),
        )

    def _release(
        self,
        provider: RecordingAuthorityProvider,
        actuator: RecordingReleaseActuator | RaisingReleaseActuator,
    ) -> tuple[ReleaseBoundary, object]:
        boundary = ReleaseBoundary(
            self.store,
            provider,
            actuator,
            clock=self.clock,
        )
        return boundary, boundary.release("execution-a", "consequence-x")

    def test_fresh_exact_allow_reaches_actuator_and_releases(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        _, result = self._release(provider, actuator)

        self.assertEqual(result.status, ReleaseState.RELEASED)
        self.assertEqual(self.store.release_state("consequence-x"), ReleaseState.RELEASED)
        self.assertEqual(len(actuator.calls), 1)
        self.assertEqual(actuator.calls[0].payload, {"title": "content-y"})

    def test_deny_does_not_invoke_actuator(self) -> None:
        provider = RecordingAuthorityProvider(
            lambda consequence, now: self._decision(
                consequence, now, verdict=AuthorityVerdict.DENY.value
            )
        )
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        _, result = self._release(provider, actuator)

        self.assertEqual(result.status, ReleaseState.CONTAINED)
        self.assertEqual(result.reason, "authority_not_allowed")
        self.assertEqual(actuator.calls, [])

    def test_provider_exception_does_not_invoke_actuator(self) -> None:
        class FailingProvider:
            def evaluate(self, consequence, evaluated_at):
                raise TimeoutError

        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        boundary = ReleaseBoundary(self.store, FailingProvider(), actuator, self.clock)

        result = boundary.release("execution-a", "consequence-x")

        self.assertEqual(result.status, ReleaseState.CONTAINED)
        self.assertEqual(result.reason, "authority_unavailable")
        self.assertEqual(actuator.calls, [])

    def test_expired_allow_does_not_invoke_actuator(self) -> None:
        def allow_then_expire(consequence, now):
            decision = self._decision(
                consequence, now, valid_until=now + timedelta(seconds=1)
            )
            self.clock.value += timedelta(seconds=1)
            return decision

        provider = RecordingAuthorityProvider(allow_then_expire)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        _, result = self._release(provider, actuator)

        self.assertEqual(result.status, ReleaseState.CONTAINED)
        self.assertEqual(result.reason, "authority_expired")
        self.assertEqual(actuator.calls, [])

    def test_allow_at_exact_expiry_boundary_is_expired(self) -> None:
        provider = RecordingAuthorityProvider(
            lambda consequence, now: self._decision(
                consequence, now, valid_until=now + timedelta(seconds=1)
            )
        )
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        class ExpireOnFinalCheck:
            def __init__(self, clock: MutableClock) -> None:
                self.clock = clock
                self.calls = 0

            def __call__(self) -> datetime:
                self.calls += 1
                if self.calls == 2:
                    self.clock.value += timedelta(seconds=1)
                return self.clock.value

        clock = ExpireOnFinalCheck(self.clock)
        boundary = ReleaseBoundary(self.store, provider, actuator, clock)
        result = boundary.release("execution-a", "consequence-x")

        self.assertEqual(result.reason, "authority_expired")
        self.assertEqual(actuator.calls, [])

    def test_authority_for_another_consequence_does_not_invoke_actuator(self) -> None:
        provider = RecordingAuthorityProvider(
            lambda consequence, now: self._decision(
                consequence, now, consequence_id="consequence-y"
            )
        )
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        _, result = self._release(provider, actuator)

        self.assertEqual(result.reason, "authority_consequence_mismatch")
        self.assertEqual(actuator.calls, [])

    def test_authority_for_another_custodian_does_not_invoke_actuator(self) -> None:
        provider = RecordingAuthorityProvider(
            lambda consequence, now: self._decision(
                consequence, now, execution_id="execution-b"
            )
        )
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        _, result = self._release(provider, actuator)

        self.assertEqual(result.reason, "authority_custodian_mismatch")
        self.assertEqual(actuator.calls, [])

    def test_authority_for_different_type_does_not_invoke_actuator(self) -> None:
        provider = RecordingAuthorityProvider(
            lambda consequence, now: self._decision(
                consequence,
                now,
                bound_consequence=ProposedConsequence(
                    "different_task", consequence.target, consequence.payload
                ),
            )
        )
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        _, result = self._release(provider, actuator)

        self.assertEqual(result.reason, "authority_material_mismatch")
        self.assertEqual(actuator.calls, [])

    def test_authority_for_different_target_does_not_invoke_actuator(self) -> None:
        provider = RecordingAuthorityProvider(
            lambda consequence, now: self._decision(
                consequence,
                now,
                bound_consequence=ProposedConsequence(
                    consequence.consequence_type, "target-y", consequence.payload
                ),
            )
        )
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        _, result = self._release(provider, actuator)

        self.assertEqual(result.reason, "authority_material_mismatch")
        self.assertEqual(actuator.calls, [])

    def test_authority_for_different_payload_does_not_invoke_actuator(self) -> None:
        provider = RecordingAuthorityProvider(
            lambda consequence, now: self._decision(
                consequence,
                now,
                bound_consequence=ProposedConsequence(
                    consequence.consequence_type,
                    consequence.target,
                    {"title": "different"},
                ),
            )
        )
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        _, result = self._release(provider, actuator)

        self.assertEqual(result.reason, "authority_material_mismatch")
        self.assertEqual(actuator.calls, [])

    def test_allow_alone_does_not_mark_consequence_released(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.DEFINITE_NOT_EXECUTED)

        _, result = self._release(provider, actuator)

        self.assertEqual(result.status, ReleaseState.CONTAINED)
        self.assertEqual(self.store.release_state("consequence-x"), ReleaseState.CONTAINED)

    def test_non_custodian_cannot_request_release(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        boundary = ReleaseBoundary(self.store, provider, actuator, self.clock)

        result = boundary.release("execution-b", "consequence-x")

        self.assertEqual(result.reason, "not_current_custodian")
        self.assertEqual(provider.calls, [])
        self.assertEqual(actuator.calls, [])

    def test_transfer_before_final_commit_blocks_old_allow(self) -> None:
        def evaluate_and_transfer(consequence, now):
            self.store.transfer("execution-a", "execution-b", "consequence-x")
            return self._decision(consequence, now)

        provider = RecordingAuthorityProvider(evaluate_and_transfer)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        _, result = self._release(provider, actuator)

        self.assertEqual(result.reason, "custody_changed_before_release")
        self.assertEqual(actuator.calls, [])
        self.assertEqual(
            self.store.read("execution-b", "consequence-x").current_execution_id,
            "execution-b",
        )

    def test_old_allow_cannot_release_after_transfer_and_b_requires_new_decision(self) -> None:
        provider_a = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        boundary_a = ReleaseBoundary(self.store, provider_a, actuator, self.clock)
        self.store.transfer("execution-a", "execution-b", "consequence-x")

        old_result = boundary_a.release("execution-a", "consequence-x")
        self.assertEqual(old_result.reason, "not_current_custodian")
        self.assertEqual(actuator.calls, [])

        provider_b = RecordingAuthorityProvider(
            lambda consequence, now: self._decision(
                consequence, now, execution_id="execution-b"
            )
        )
        boundary_b = ReleaseBoundary(self.store, provider_b, actuator, self.clock)
        new_result = boundary_b.release("execution-b", "consequence-x")

        self.assertEqual(new_result.status, ReleaseState.RELEASED)
        self.assertEqual(len(actuator.calls), 1)

    def test_transfer_does_not_invoke_provider_or_actuator(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        self.store.transfer("execution-a", "execution-b", "consequence-x")

        self.assertEqual(provider.calls, [])
        self.assertEqual(actuator.calls, [])

    def test_origin_is_preserved_across_release(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        _, result = self._release(provider, actuator)

        self.assertEqual(result.status, ReleaseState.RELEASED)
        self.assertEqual(actuator.calls[0].origin_execution_id, "execution-a")

    def test_actuator_receives_canonical_contained_snapshot(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        boundary = ReleaseBoundary(self.store, provider, actuator, self.clock)

        result = boundary.release("execution-a", "consequence-x")

        self.assertEqual(result.status, ReleaseState.RELEASED)
        self.assertEqual(actuator.calls[0], self.store.read("execution-a", "consequence-x"))

    def test_caller_mutation_after_submit_cannot_change_released_effect(self) -> None:
        original = {"nested": {"value": "safe"}}
        store = ExecutionScopedConsequenceStore()
        store.register_execution("execution-a")
        store.submit(
            "execution-a",
            "consequence-mutable",
            ProposedConsequence("synthetic_task", "target-x", original),
        )
        original["nested"]["value"] = "changed"
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        boundary = ReleaseBoundary(store, provider, actuator, self.clock)

        result = boundary.release("execution-a", "consequence-mutable")

        self.assertEqual(result.status, ReleaseState.RELEASED)
        self.assertEqual(actuator.calls[0].payload, {"nested": {"value": "safe"}})

    def test_actuator_cannot_receive_different_payload_from_authority(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        boundary = ReleaseBoundary(self.store, provider, actuator, self.clock)

        result = boundary.release("execution-a", "consequence-x")

        self.assertEqual(result.status, ReleaseState.RELEASED)
        self.assertEqual(actuator.calls[0].payload, {"title": "content-y"})
        self.assertEqual(provider.calls[0][0].payload, {"title": "content-y"})

    def test_material_changed_at_final_check_does_not_invoke_actuator(self) -> None:
        def evaluate_and_mutate(consequence, now):
            self.store._consequences["consequence-x"] = replace(
                self.store._consequences["consequence-x"],
                payload={"title": "changed"},
            )
            return self._decision(consequence, now)

        provider = RecordingAuthorityProvider(evaluate_and_mutate)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        _, result = self._release(provider, actuator)

        self.assertEqual(result.reason, "authority_material_mismatch")
        self.assertEqual(actuator.calls, [])

    def test_succeeded_actuator_marks_released(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        _, result = self._release(provider, actuator)

        self.assertEqual(result.status, ReleaseState.RELEASED)

    def test_definitely_not_executed_actuator_remains_contained(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.DEFINITE_NOT_EXECUTED)
        _, result = self._release(provider, actuator)

        self.assertEqual(result.status, ReleaseState.CONTAINED)
        self.assertEqual(result.reason, "actuator_definitely_not_executed")

    def test_uncertain_actuator_marks_uncertain(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.UNCERTAIN)
        _, result = self._release(provider, actuator)

        self.assertEqual(result.status, ReleaseState.UNCERTAIN)
        self.assertEqual(self.store.release_state("consequence-x"), ReleaseState.UNCERTAIN)

    def test_ambiguous_actuator_exception_marks_uncertain(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RaisingReleaseActuator()
        boundary = ReleaseBoundary(self.store, provider, actuator, self.clock)

        result = boundary.release("execution-a", "consequence-x")

        self.assertEqual(result.status, ReleaseState.UNCERTAIN)
        self.assertEqual(result.reason, "actuator_outcome_uncertain")

    def test_released_consequence_cannot_be_released_twice(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        boundary = ReleaseBoundary(self.store, provider, actuator, self.clock)
        first = boundary.release("execution-a", "consequence-x")
        second = boundary.release("execution-a", "consequence-x")

        self.assertEqual(first.status, ReleaseState.RELEASED)
        self.assertEqual(second.reason, "consequence_not_releasable")
        self.assertEqual(len(actuator.calls), 1)

    def test_uncertain_consequence_cannot_be_automatically_retried(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.UNCERTAIN)
        boundary = ReleaseBoundary(self.store, provider, actuator, self.clock)
        first = boundary.release("execution-a", "consequence-x")
        second = boundary.release("execution-a", "consequence-x")

        self.assertEqual(first.status, ReleaseState.UNCERTAIN)
        self.assertEqual(second.reason, "consequence_not_releasable")
        self.assertEqual(len(actuator.calls), 1)

    def test_provider_failure_before_actuator_is_not_uncertain(self) -> None:
        class FailingProvider:
            def evaluate(self, consequence, evaluated_at):
                raise RuntimeError

        actuator = RecordingReleaseActuator(ActuatorOutcome.UNCERTAIN)
        boundary = ReleaseBoundary(self.store, FailingProvider(), actuator, self.clock)

        result = boundary.release("execution-a", "consequence-x")

        self.assertEqual(result.status, ReleaseState.CONTAINED)
        self.assertNotEqual(result.status, ReleaseState.UNCERTAIN)
        self.assertEqual(actuator.calls, [])

    def test_transfer_after_released_fails_closed(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        self._release(provider, actuator)

        with self.assertRaises(ValueError):
            self.store.transfer("execution-a", "execution-b", "consequence-x")

    def test_transfer_after_uncertain_fails_closed(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.UNCERTAIN)
        self._release(provider, actuator)

        with self.assertRaises(ValueError):
            self.store.transfer("execution-a", "execution-b", "consequence-x")

    def test_reentrant_transfer_cannot_interleave_serialized_actuation(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)

        class ReentrantActuator:
            def __init__(self, store) -> None:
                self.store = store
                self.transfer_error = None

            def actuate(self, consequence):
                try:
                    self.store.transfer(
                        "execution-a", "execution-b", "consequence-x"
                    )
                except ValueError as error:
                    self.transfer_error = error
                return ActuatorOutcome.SUCCEEDED

        actuator = ReentrantActuator(self.store)
        boundary = ReleaseBoundary(self.store, provider, actuator, self.clock)

        result = boundary.release("execution-a", "consequence-x")

        self.assertEqual(result.status, ReleaseState.RELEASED)
        self.assertIsNotNone(actuator.transfer_error)
        self.assertEqual(
            self.store.read("execution-a", "consequence-x").current_execution_id,
            "execution-a",
        )

    def test_authority_decision_subclass_is_rejected(self) -> None:
        provider = RecordingAuthorityProvider(
            lambda consequence, now: CustomAuthorityDecision(
                **self._decision(consequence, now).__dict__
            )
        )
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        _, result = self._release(provider, actuator)

        self.assertEqual(result.reason, "authority_invalid")
        self.assertEqual(actuator.calls, [])
        self.assertEqual(self.store.release_state("consequence-x"), ReleaseState.CONTAINED)

    def test_verdict_equality_spoof_is_rejected(self) -> None:
        provider = RecordingAuthorityProvider(
            lambda consequence, now: replace(
                self._decision(consequence, now),
                verdict=EqualitySpoof(),
            )
        )
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        _, result = self._release(provider, actuator)

        self.assertEqual(result.reason, "authority_invalid")
        self.assertEqual(actuator.calls, [])

    def test_arbitrary_verdict_is_rejected(self) -> None:
        provider = RecordingAuthorityProvider(
            lambda consequence, now: self._decision(
                consequence, now, verdict="MAYBE"
            )
        )
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        _, result = self._release(provider, actuator)

        self.assertEqual(result.reason, "authority_invalid")
        self.assertEqual(actuator.calls, [])

    def test_custom_string_authority_fields_are_rejected(self) -> None:
        for field_name in (
            "decision_id",
            "verdict",
            "consequence_id",
            "execution_id",
        ):
            with self.subTest(field=field_name):
                provider = RecordingAuthorityProvider(
                    lambda consequence, now, field=field_name: replace(
                        self._decision(consequence, now),
                        **{
                            field: CustomString(
                                self._decision(consequence, now).__dict__[field]
                            )
                        },
                    )
                )
                actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

                _, result = self._release(provider, actuator)

                self.assertEqual(result.reason, "authority_invalid")
                self.assertEqual(actuator.calls, [])

    def test_custom_authority_timestamps_are_rejected(self) -> None:
        for field_name in ("issued_at", "valid_until"):
            with self.subTest(field=field_name):
                provider = RecordingAuthorityProvider(
                    lambda consequence, now, field=field_name: replace(
                        self._decision(consequence, now),
                        **{field: CustomDatetime(2026, 1, 1, 12, 0, 0)},
                    )
                )
                actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

                _, result = self._release(provider, actuator)

                self.assertEqual(result.reason, "authority_invalid_timestamp")
                self.assertEqual(actuator.calls, [])

    def test_custom_clock_result_is_rejected(self) -> None:
        current = datetime(2026, 1, 1, 12, 0, 0)

        class SequenceClock:
            def __init__(self) -> None:
                self.values = [current, CustomDatetime(2026, 1, 1, 12, 0, 0)]

            def __call__(self) -> datetime:
                return self.values.pop(0)

        provider = RecordingAuthorityProvider(
            lambda consequence, now: self._decision(
                consequence,
                datetime(2026, 1, 1, 12, 0, 0),
            )
        )
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        boundary = ReleaseBoundary(self.store, provider, actuator, SequenceClock())

        result = boundary.release("execution-a", "consequence-x")

        self.assertEqual(result.reason, "clock_invalid")
        self.assertEqual(result.status, ReleaseState.CONTAINED)
        self.assertEqual(actuator.calls, [])

    def test_valid_until_before_issued_at_is_rejected(self) -> None:
        provider = RecordingAuthorityProvider(
            lambda consequence, now: self._decision(
                consequence,
                now,
                valid_until=now - timedelta(seconds=1),
            )
        )
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        _, result = self._release(provider, actuator)

        self.assertEqual(result.reason, "authority_invalid_timestamp")
        self.assertEqual(actuator.calls, [])

    def test_reentrant_same_consequence_release_is_rejected_before_provider(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        holder: dict[str, ReleaseBoundary] = {}

        class ReentrantReleaseActuator:
            def __init__(self) -> None:
                self.calls = 0
                self.nested_result = None

            def actuate(self, consequence):
                self.calls += 1
                self.nested_result = holder["boundary"].release(
                    "execution-a", "consequence-x"
                )
                return ActuatorOutcome.SUCCEEDED

        actuator = ReentrantReleaseActuator()
        holder["boundary"] = ReleaseBoundary(
            self.store,
            provider,
            actuator,
            self.clock,
        )

        result = holder["boundary"].release("execution-a", "consequence-x")

        self.assertEqual(result.status, ReleaseState.RELEASED)
        self.assertEqual(actuator.nested_result.reason, "release_in_progress")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(actuator.calls, 1)
        self.assertEqual(self.store.release_state("consequence-x"), ReleaseState.RELEASED)

    def test_reentrant_release_through_second_boundary_is_store_blocked(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        holder: dict[str, ReleaseBoundary] = {}

        class ReentrantReleaseActuator:
            def __init__(self) -> None:
                self.calls = 0
                self.nested_result = None

            def actuate(self, consequence):
                self.calls += 1
                self.nested_result = holder["second"].release(
                    "execution-a", "consequence-x"
                )
                return ActuatorOutcome.SUCCEEDED

        actuator = ReentrantReleaseActuator()
        holder["second"] = ReleaseBoundary(
            self.store,
            provider,
            actuator,
            self.clock,
        )
        holder["first"] = ReleaseBoundary(
            self.store,
            provider,
            actuator,
            self.clock,
        )

        result = holder["first"].release("execution-a", "consequence-x")

        self.assertEqual(result.status, ReleaseState.RELEASED)
        self.assertEqual(actuator.nested_result.reason, "release_in_progress")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(actuator.calls, 1)

    def test_actuator_mutation_does_not_change_canonical_material(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)

        class MutatingActuator:
            def actuate(self, consequence):
                consequence.payload["title"] = "mutated"
                return ActuatorOutcome.SUCCEEDED

        boundary = ReleaseBoundary(
            self.store,
            provider,
            MutatingActuator(),
            self.clock,
        )

        result = boundary.release("execution-a", "consequence-x")

        self.assertEqual(result.status, ReleaseState.RELEASED)
        self.assertEqual(
            self.store.read("execution-a", "consequence-x").payload,
            {"title": "content-y"},
        )

    def test_malformed_actuator_result_cannot_become_released(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator("NOT_A_REAL_OUTCOME")

        _, result = self._release(provider, actuator)

        self.assertEqual(result.status, ReleaseState.UNCERTAIN)
        self.assertNotEqual(result.status, ReleaseState.RELEASED)

    def test_second_release_after_released_does_not_call_provider(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        boundary = ReleaseBoundary(self.store, provider, actuator, self.clock)

        first = boundary.release("execution-a", "consequence-x")
        second = boundary.release("execution-a", "consequence-x")

        self.assertEqual(first.status, ReleaseState.RELEASED)
        self.assertEqual(second.reason, "consequence_not_releasable")
        self.assertEqual(len(provider.calls), 1)

    def test_second_release_after_uncertain_does_not_call_provider(self) -> None:
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.UNCERTAIN)
        boundary = ReleaseBoundary(self.store, provider, actuator, self.clock)

        first = boundary.release("execution-a", "consequence-x")
        second = boundary.release("execution-a", "consequence-x")

        self.assertEqual(first.status, ReleaseState.UNCERTAIN)
        self.assertEqual(second.reason, "consequence_not_releasable")
        self.assertEqual(len(provider.calls), 1)


class ExecutionInheritanceBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = ExecutionScopedConsequenceStore()
        for execution_id in ("execution-a", "execution-b", "execution-c"):
            self.store.register_execution(execution_id)
        self.consequence = ProposedConsequence(
            "synthetic_task",
            "target-x",
            {"title": "content-y"},
        )
        self.clock = MutableClock(datetime(2026, 1, 1, 12, 0, 0))

    def _submit(self, consequence_id: str = "consequence-x") -> None:
        self.store.submit("execution-a", consequence_id, self.consequence)

    def _derived(self, parent_consequence_id: str | None) -> ProposedConsequence:
        return ProposedConsequence(
            "derived_task",
            "target-derived",
            {"source": "derived"},
            parent_consequence_id=parent_consequence_id,
        )

    def test_root_consequence_defaults_parent_to_none(self) -> None:
        self._submit()

        stored = self.store.read("execution-a", "consequence-x")

        self.assertIsNone(stored.parent_consequence_id)

    def test_adoption_then_derive_accepts_single_parent_and_preserves_identity(self) -> None:
        self._submit()
        self.store.end_execution("execution-a")

        with self.assertRaises(ValueError):
            self.store.submit(
                "execution-b",
                "consequence-c",
                self._derived("consequence-x"),
            )

        self.store.adopt("execution-b", "consequence-x")
        derived = self.store.submit(
            "execution-b",
            "consequence-c",
            self._derived("consequence-x"),
        )

        self.assertEqual(derived.parent_consequence_id, "consequence-x")
        self.assertEqual(derived.origin_execution_id, "execution-b")
        self.assertEqual(derived.current_execution_id, "execution-b")
        self.assertEqual(self.store.release_state("consequence-c"), ReleaseState.CONTAINED)

    def test_invalid_parent_does_not_insert_child(self) -> None:
        with self.assertRaises(KeyError):
            self.store.submit(
                "execution-a",
                "consequence-c",
                self._derived("missing-parent"),
            )

        with self.assertRaises(KeyError):
            self.store.read("execution-a", "consequence-c")

    def test_parent_held_by_another_execution_is_rejected(self) -> None:
        self._submit()

        with self.assertRaises(ValueError):
            self.store.submit(
                "execution-b",
                "consequence-c",
                self._derived("consequence-x"),
            )

    def test_released_parent_is_rejected(self) -> None:
        self._submit()
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        result = ReleaseBoundary(
            self.store,
            provider,
            actuator,
            self.clock,
        ).release("execution-a", "consequence-x")

        self.assertEqual(result.status, ReleaseState.RELEASED)
        with self.assertRaises(ValueError):
            self.store.submit(
                "execution-a",
                "consequence-c",
                self._derived("consequence-x"),
            )

    def test_uncertain_parent_is_rejected(self) -> None:
        self._submit()
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.UNCERTAIN)
        result = ReleaseBoundary(
            self.store,
            provider,
            actuator,
            self.clock,
        ).release("execution-a", "consequence-x")

        self.assertEqual(result.status, ReleaseState.UNCERTAIN)
        with self.assertRaises(ValueError):
            self.store.submit(
                "execution-a",
                "consequence-c",
                self._derived("consequence-x"),
            )

    def test_self_parentage_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.submit(
                "execution-a",
                "consequence-c",
                self._derived("consequence-c"),
            )

    def test_lineage_survives_transfer_quarantine_and_adoption(self) -> None:
        self.store.register_execution("execution-d")
        self._submit()
        derived = self.store.submit(
            "execution-a",
            "consequence-c",
            self._derived("consequence-x"),
        )

        self.assertEqual(derived.parent_consequence_id, "consequence-x")
        self.store.transfer("execution-a", "execution-b", "consequence-c")
        self.assertEqual(
            self.store.read("execution-b", "consequence-c").parent_consequence_id,
            "consequence-x",
        )

        self.store.end_execution("execution-b")
        self.assertEqual(
            self.store.inspect_quarantined("consequence-c").parent_consequence_id,
            "consequence-x",
        )
        self.store.adopt("execution-d", "consequence-c")
        self.assertEqual(
            self.store.read("execution-d", "consequence-c").parent_consequence_id,
            "consequence-x",
        )

    def test_lineage_is_not_material_comparison(self) -> None:
        left = self._derived("parent-a")
        right = self._derived("parent-b")

        self.assertTrue(same_material_consequence(left, right))
        self.assertFalse(
            same_material_consequence(
                left,
                ProposedConsequence(
                    "different_task",
                    right.target,
                    right.payload,
                    parent_consequence_id="parent-b",
                ),
            )
        )

    def test_payload_lineage_convention_is_not_canonical_lineage(self) -> None:
        submitted = self.store.submit(
            "execution-a",
            "consequence-payload-parent",
            ProposedConsequence(
                "derived_task",
                "target-derived",
                {"derived_from": "consequence-x"},
            ),
        )

        self.assertIsNone(submitted.parent_consequence_id)

    def test_child_release_uses_independent_authority_evaluation(self) -> None:
        self._submit()
        derived = self.store.submit(
            "execution-a",
            "consequence-c",
            self._derived("consequence-x"),
        )
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        result = ReleaseBoundary(
            self.store,
            provider,
            actuator,
            self.clock,
        ).release("execution-a", "consequence-c")

        self.assertEqual(result.status, ReleaseState.RELEASED)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0][0].consequence_id, "consequence-c")
        self.assertEqual(provider.calls[0][0].parent_consequence_id, "consequence-x")
        self.assertEqual(actuator.calls[0].parent_consequence_id, "consequence-x")
        self.assertEqual(derived.parent_consequence_id, "consequence-x")

    def test_parent_state_change_does_not_erase_child_lineage(self) -> None:
        self._submit()
        self.store.submit(
            "execution-a",
            "consequence-c",
            self._derived("consequence-x"),
        )
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        result = ReleaseBoundary(
            self.store,
            provider,
            actuator,
            self.clock,
        ).release("execution-a", "consequence-x")

        self.assertEqual(result.status, ReleaseState.RELEASED)
        child = self.store.read("execution-a", "consequence-c")
        self.assertEqual(child.parent_consequence_id, "consequence-x")
        self.assertEqual(
            self.store.release_state("consequence-c"),
            ReleaseState.CONTAINED,
        )

    def test_parent_release_in_progress_rejects_derived_submission(self) -> None:
        self._submit()
        derived = self._derived("consequence-x")

        class ReentrantActuator:
            def __init__(self, store) -> None:
                self.store = store
                self.error = None

            def actuate(self, consequence):
                try:
                    self.store.submit(
                        "execution-a",
                        "consequence-c",
                        derived,
                    )
                except ValueError as error:
                    self.error = error
                return ActuatorOutcome.SUCCEEDED

        actuator = ReentrantActuator(self.store)
        provider = RecordingAuthorityProvider(self._decision)
        result = ReleaseBoundary(
            self.store,
            provider,
            actuator,
            self.clock,
        ).release("execution-a", "consequence-x")

        self.assertEqual(result.status, ReleaseState.RELEASED)
        self.assertIsNotNone(actuator.error)
        with self.assertRaises(KeyError):
            self.store.read("execution-a", "consequence-c")

    def _decision(
        self,
        contained: ContainedConsequence,
        evaluated_at: datetime,
        *,
        execution_id: str | None = None,
    ) -> AuthorityDecision:
        return AuthorityDecision(
            decision_id="decision-inheritance",
            verdict=AuthorityVerdict.ALLOW.value,
            consequence_id=contained.consequence_id,
            execution_id=execution_id or contained.current_execution_id,
            bound_consequence=ProposedConsequence(
                contained.consequence_type,
                contained.target,
                contained.payload,
            ),
            issued_at=evaluated_at,
            valid_until=evaluated_at + timedelta(minutes=5),
        )

    def test_execution_lifecycle_is_active_then_ended_and_cannot_restart(self) -> None:
        self.assertEqual(self.store._executions["execution-a"], ExecutionState.ACTIVE)

        self.store.end_execution("execution-a")

        self.assertEqual(self.store._executions["execution-a"], ExecutionState.ENDED)
        with self.assertRaises(ValueError):
            self.store.register_execution("execution-a")

    def test_end_quarantines_contained_consequence_without_changing_origin_or_material(self) -> None:
        self._submit()

        self.store.end_execution("execution-a")
        quarantined = self.store.inspect_quarantined("consequence-x")

        self.assertEqual(quarantined.origin_execution_id, "execution-a")
        self.assertIsNone(quarantined.current_execution_id)
        self.assertEqual(quarantined.consequence_type, "synthetic_task")
        self.assertEqual(quarantined.target, "target-x")
        self.assertEqual(quarantined.payload, {"title": "content-y"})
        with self.assertRaises(KeyError):
            self.store.read("execution-a", "consequence-x")
        with self.assertRaises(KeyError):
            self.store.read("execution-b", "consequence-x")

    def test_new_execution_does_not_automatically_inherit_quarantined_consequence(self) -> None:
        self._submit()
        self.store.end_execution("execution-a")

        with self.assertRaises(KeyError):
            self.store.read("execution-b", "consequence-x")

        adopted = self.store.adopt("execution-b", "consequence-x")
        self.assertEqual(adopted.current_execution_id, "execution-b")
        self.assertEqual(
            self.store.read("execution-b", "consequence-x").current_execution_id,
            "execution-b",
        )

    def test_inspection_is_detached_and_does_not_grant_custody(self) -> None:
        self._submit()
        self.store.end_execution("execution-a")

        inspected = self.store.inspect_quarantined("consequence-x")
        inspected.payload["title"] = "mutated"

        stored = self.store.inspect_quarantined("consequence-x")
        self.assertIsNone(stored.current_execution_id)
        self.assertEqual(stored.payload, {"title": "content-y"})
        with self.assertRaises(KeyError):
            self.store.read("execution-b", "consequence-x")

    def test_adoption_is_custody_only_and_does_not_invoke_provider_or_actuator(self) -> None:
        self._submit()
        self.store.end_execution("execution-a")
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        adopted = self.store.adopt("execution-b", "consequence-x")

        self.assertEqual(adopted.current_execution_id, "execution-b")
        self.assertEqual(self.store.release_state("consequence-x"), ReleaseState.CONTAINED)
        self.assertEqual(provider.calls, [])
        self.assertEqual(actuator.calls, [])

    def test_adoption_requires_active_registered_execution_and_quarantine(self) -> None:
        self._submit()
        self.store.end_execution("execution-a")
        self.store.end_execution("execution-b")

        with self.assertRaises(KeyError):
            self.store.adopt("execution-unknown", "consequence-x")
        with self.assertRaises(KeyError):
            self.store.adopt("execution-b", "consequence-x")

        with self.assertRaises(KeyError):
            self.store.transfer("execution-a", "execution-c", "consequence-x")

    def test_ended_execution_cannot_submit_read_transfer_receive_or_release(self) -> None:
        self._submit()
        self.store.end_execution("execution-a")
        self.store.register_execution("execution-d")

        with self.assertRaises(KeyError):
            self.store.submit("execution-a", "new-consequence", self.consequence)
        with self.assertRaises(KeyError):
            self.store.read("execution-a", "consequence-x")
        with self.assertRaises(KeyError):
            self.store.transfer("execution-a", "execution-b", "consequence-x")
        with self.assertRaises(KeyError):
            self.store.transfer("execution-b", "execution-a", "consequence-x")

        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        result = ReleaseBoundary(
            self.store,
            provider,
            actuator,
            self.clock,
        ).release("execution-a", "consequence-x")

        self.assertEqual(result.reason, "execution_not_active")
        self.assertEqual(provider.calls, [])
        self.assertEqual(actuator.calls, [])

    def test_quarantined_consequence_cannot_release_before_adoption(self) -> None:
        self._submit()
        self.store.end_execution("execution-a")
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        result = ReleaseBoundary(
            self.store,
            provider,
            actuator,
            self.clock,
        ).release("execution-b", "consequence-x")

        self.assertEqual(result.reason, "consequence_not_in_active_custody")
        self.assertEqual(provider.calls, [])
        self.assertEqual(actuator.calls, [])

    def test_release_after_adoption_evaluates_current_b_custodian(self) -> None:
        self._submit()
        self.store.end_execution("execution-a")
        self.store.adopt("execution-b", "consequence-x")
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)

        result = ReleaseBoundary(
            self.store,
            provider,
            actuator,
            self.clock,
        ).release("execution-b", "consequence-x")

        self.assertEqual(result.status, ReleaseState.RELEASED)
        self.assertEqual(provider.calls[0][0].current_execution_id, "execution-b")
        self.assertEqual(actuator.calls[0].current_execution_id, "execution-b")

    def test_a_bound_authority_cannot_release_after_b_adopts(self) -> None:
        self._submit()
        self.store.end_execution("execution-a")
        self.store.adopt("execution-b", "consequence-x")

        provider = RecordingAuthorityProvider(
            lambda consequence, now: self._decision(
                consequence,
                now,
                execution_id="execution-a",
            )
        )
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        result = ReleaseBoundary(
            self.store,
            provider,
            actuator,
            self.clock,
        ).release("execution-b", "consequence-x")

        self.assertEqual(result.reason, "authority_custodian_mismatch")
        self.assertEqual(actuator.calls, [])
        self.assertEqual(self.store.release_state("consequence-x"), ReleaseState.CONTAINED)

    def test_adopt_then_transfer_preserves_origin_and_material(self) -> None:
        self._submit()
        self.store.end_execution("execution-a")
        self.store.adopt("execution-b", "consequence-x")

        transferred = self.store.transfer(
            "execution-b",
            "execution-c",
            "consequence-x",
        )

        self.assertEqual(transferred.origin_execution_id, "execution-a")
        self.assertEqual(transferred.current_execution_id, "execution-c")
        self.assertEqual(transferred.consequence_type, "synthetic_task")
        self.assertEqual(transferred.target, "target-x")
        self.assertEqual(transferred.payload, {"title": "content-y"})

    def test_two_adoption_attempts_have_one_custody_winner(self) -> None:
        self._submit()
        self.store.end_execution("execution-a")

        self.store.adopt("execution-b", "consequence-x")

        with self.assertRaises(ValueError):
            self.store.adopt("execution-c", "consequence-x")
        self.assertEqual(
            self.store.read("execution-b", "consequence-x").current_execution_id,
            "execution-b",
        )

    def test_terminal_states_are_not_adoptable_or_quarantined(self) -> None:
        self._submit("released-consequence")
        provider = RecordingAuthorityProvider(self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        result = ReleaseBoundary(
            self.store,
            provider,
            actuator,
            self.clock,
        ).release("execution-a", "released-consequence")
        self.assertEqual(result.status, ReleaseState.RELEASED)

        self.store.end_execution("execution-a")

        with self.assertRaises(KeyError):
            self.store.inspect_quarantined("released-consequence")
        with self.assertRaises(ValueError):
            self.store.adopt("execution-b", "released-consequence")

    def test_end_is_atomic_when_a_consequence_is_release_in_progress(self) -> None:
        self._submit("consequence-one")
        self._submit("consequence-two")
        with self.store._release_lock:
            self.store._release_in_progress.add("consequence-one")
            try:
                with self.assertRaises(ValueError):
                    self.store.end_execution("execution-a")
            finally:
                self.store._release_in_progress.remove("consequence-one")

        self.assertEqual(self.store._executions["execution-a"], ExecutionState.ACTIVE)
        self.assertEqual(
            self.store.read("execution-a", "consequence-one").current_execution_id,
            "execution-a",
        )
        self.assertEqual(
            self.store.read("execution-a", "consequence-two").current_execution_id,
            "execution-a",
        )

    def test_provider_ending_execution_causes_final_release_recheck_to_fail_closed(self) -> None:
        self._submit()

        class EndingProvider:
            def __init__(self, store, decision_factory) -> None:
                self.store = store
                self.decision_factory = decision_factory
                self.calls = 0

            def evaluate(self, consequence, evaluated_at):
                self.calls += 1
                decision = self.decision_factory(consequence, evaluated_at)
                self.store.end_execution("execution-a")
                return decision

        provider = EndingProvider(self.store, self._decision)
        actuator = RecordingReleaseActuator(ActuatorOutcome.SUCCEEDED)
        result = ReleaseBoundary(
            self.store,
            provider,
            actuator,
            self.clock,
        ).release("execution-a", "consequence-x")

        self.assertEqual(result.reason, "custody_changed_before_release")
        self.assertEqual(actuator.calls, [])
        self.assertEqual(self.store.inspect_quarantined("consequence-x").current_execution_id, None)

    def test_reentrant_end_during_actuation_fails_without_partial_termination(self) -> None:
        self._submit()
        provider = RecordingAuthorityProvider(self._decision)

        class EndingActuator:
            def __init__(self, store) -> None:
                self.store = store
                self.end_error = None

            def actuate(self, consequence):
                try:
                    self.store.end_execution("execution-a")
                except ValueError as error:
                    self.end_error = error
                return ActuatorOutcome.SUCCEEDED

        actuator = EndingActuator(self.store)
        result = ReleaseBoundary(
            self.store,
            provider,
            actuator,
            self.clock,
        ).release("execution-a", "consequence-x")

        self.assertEqual(result.status, ReleaseState.RELEASED)
        self.assertIsNotNone(actuator.end_error)
        self.assertEqual(self.store._executions["execution-a"], ExecutionState.ACTIVE)
        self.assertEqual(
            self.store.read("execution-a", "consequence-x").current_execution_id,
            "execution-a",
        )


class ExecutionScopedConsequenceContainmentRegressionTests(unittest.TestCase):
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
