"""Eight acceptance cases for the consequence-inheritance experiment."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
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
    _copy_contained_consequence,
    same_material_consequence,
)


class CustomString(str):
    pass


class CustomAuthorityDecision(AuthorityDecision):
    pass


class CustomDatetime(datetime):
    pass


class HostileIdentifier(str):
    """Test-only str subclass: lying equality/inequality and a pinned hash.

    ``alias`` is the string whose dict/set bucket the hash lands in. Every
    hostile method records its use so tests can prove rejection happens before
    any of them runs.
    """

    def __new__(cls, value: str, alias: str) -> "HostileIdentifier":
        obj = super().__new__(cls, value)
        obj.alias = alias
        obj.calls: list[str] = []
        return obj

    def __eq__(self, other) -> bool:
        self.calls.append("eq")
        return True

    def __ne__(self, other) -> bool:
        self.calls.append("ne")
        return False

    def __hash__(self) -> int:
        self.calls.append("hash")
        return hash(self.alias)

    def __len__(self) -> int:
        self.calls.append("len")
        return super().__len__()

    def strip(self, *args) -> str:
        self.calls.append("strip")
        return super().strip(*args)


class HostileOutcomeStr(str):
    """Test-only str subclass posing as an outcome: lying eq, pinned hash."""

    def __new__(cls, value: str, alias: str) -> "HostileOutcomeStr":
        obj = super().__new__(cls, value)
        obj.alias = alias
        obj.calls: list[str] = []
        return obj

    def __eq__(self, other) -> bool:
        self.calls.append("eq")
        return True

    def __ne__(self, other) -> bool:
        self.calls.append("ne")
        return False

    def __hash__(self) -> int:
        self.calls.append("hash")
        return hash(self.alias)


class HostileOutcomeObject:
    """Test-only non-str outcome with lying equality and a pinned hash."""

    def __init__(self, alias: str) -> None:
        self.alias = alias
        self.calls: list[str] = []

    def __eq__(self, other) -> bool:
        self.calls.append("eq")
        return True

    def __ne__(self, other) -> bool:
        self.calls.append("ne")
        return False

    def __hash__(self) -> int:
        self.calls.append("hash")
        return hash(self.alias)


class HostileEqualityOnlyOutcome:
    """Test-only outcome: lying __eq__ only (identity hash)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __eq__(self, other) -> bool:
        self.calls.append("eq")
        return True

    __hash__ = object.__hash__


class HostileHashOnlyOutcome:
    """Test-only outcome: pinned hash only (identity equality)."""

    def __init__(self, alias: str) -> None:
        self.alias = alias
        self.calls: list[str] = []

    def __hash__(self) -> int:
        self.calls.append("hash")
        return hash(self.alias)


class ForeignStrEnum(str, Enum):
    DEFINITE_NOT_EXECUTED = "DEFINITE_NOT_EXECUTED"


class PlainStrSubclass(str):
    pass


class ScriptedOutcomeActuator:
    """Counts entries; each entry returns the next scripted outcome."""

    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.effects = 0

    def actuate(self, consequence: ContainedConsequence):
        self.effects += 1
        return self.outcomes.pop(0)


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


class CustomBaseException(BaseException):
    pass


class CountingReleaseActuator:
    """Counts external effects, then raises or returns as configured."""

    def __init__(self, result=None, raises: BaseException | None = None) -> None:
        self.result = result
        self.raises = raises
        self.effects = 0

    def actuate(self, consequence: ContainedConsequence):
        self.effects += 1
        if self.raises is not None:
            raise self.raises
        return self.result


def _hash_raising_outcome(error: BaseException) -> str:
    class HashRaisingOutcome(str):
        def __hash__(self) -> int:
            raise error

    return HashRaisingOutcome("SUCCEEDED")


class _InterruptingReleaseStates(dict):
    """Test-only state map that fails while committing one target state."""

    def __init__(self, states, interrupted_state, signal: BaseException) -> None:
        super().__init__(states)
        self._interrupted_state = interrupted_state
        self._signal = signal

    def __setitem__(self, key, value) -> None:
        if value is self._interrupted_state:
            raise self._signal
        super().__setitem__(key, value)


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
        self.assertEqual(self.store.release_state("consequence-x"), ReleaseState.UNCERTAIN)

        second = boundary.release("execution-a", "consequence-x")

        self.assertEqual(second.reason, "consequence_not_releasable")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(actuator.calls), 1)

    def _fresh_release(self, actuator):
        store = ExecutionScopedConsequenceStore()
        store.register_execution("execution-a")
        store.submit("execution-a", "consequence-x", self.consequence)
        provider = RecordingAuthorityProvider(self._decision)
        boundary = ReleaseBoundary(store, provider, actuator, self.clock)
        return store, provider, boundary

    def _assert_uncertain_and_not_retried(self, store, provider, boundary, actuator):
        self.assertEqual(store.release_state("consequence-x"), ReleaseState.UNCERTAIN)
        self.assertNotIn("consequence-x", store._release_in_progress)

        second = boundary.release("execution-a", "consequence-x")

        self.assertEqual(second.reason, "consequence_not_releasable")
        self.assertFalse(second.released)
        self.assertEqual(store.release_state("consequence-x"), ReleaseState.UNCERTAIN)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(actuator.effects, 1)

    def test_post_actuation_base_exception_marks_uncertain_and_propagates(self) -> None:
        signals = {
            "KeyboardInterrupt": KeyboardInterrupt,
            "SystemExit": SystemExit,
            "GeneratorExit": GeneratorExit,
            "CancelledError": asyncio.CancelledError,
            "CustomBaseException": CustomBaseException,
        }
        for name, signal_type in signals.items():
            with self.subTest(signal=name):
                signal = signal_type()
                actuator = CountingReleaseActuator(raises=signal)
                store, provider, boundary = self._fresh_release(actuator)

                with self.assertRaises(signal_type) as caught:
                    boundary.release("execution-a", "consequence-x")

                self.assertIs(caught.exception, signal)
                self._assert_uncertain_and_not_retried(
                    store, provider, boundary, actuator
                )

    def test_ordinary_normalization_exception_marks_uncertain_result(self) -> None:
        # The actuator really succeeded; normalization itself then fails.
        actuator = CountingReleaseActuator(result=ActuatorOutcome.SUCCEEDED)
        store, provider, boundary = self._fresh_release(actuator)

        with patch(
            "reference.swampbox._normalize_actuator_outcome",
            side_effect=RuntimeError("normalization failed"),
        ):
            result = boundary.release("execution-a", "consequence-x")

        self.assertEqual(result.status, ReleaseState.UNCERTAIN)
        self.assertEqual(result.reason, "actuator_outcome_uncertain")
        self.assertFalse(result.released)
        self._assert_uncertain_and_not_retried(store, provider, boundary, actuator)

    def test_normalization_base_exception_marks_uncertain_and_propagates(self) -> None:
        signal = KeyboardInterrupt()
        actuator = CountingReleaseActuator(result=ActuatorOutcome.SUCCEEDED)
        store, provider, boundary = self._fresh_release(actuator)

        with patch(
            "reference.swampbox._normalize_actuator_outcome", side_effect=signal
        ):
            with self.assertRaises(KeyboardInterrupt) as caught:
                boundary.release("execution-a", "consequence-x")

        self.assertIs(caught.exception, signal)
        self._assert_uncertain_and_not_retried(store, provider, boundary, actuator)

    def test_interrupted_released_commit_marks_uncertain_and_propagates(self) -> None:
        signal = KeyboardInterrupt()
        actuator = CountingReleaseActuator(result=ActuatorOutcome.SUCCEEDED)
        store, provider, boundary = self._fresh_release(actuator)
        store._release_states = _InterruptingReleaseStates(
            store._release_states, ReleaseState.RELEASED, signal
        )

        with self.assertRaises(KeyboardInterrupt) as caught:
            boundary.release("execution-a", "consequence-x")

        self.assertIs(caught.exception, signal)
        self._assert_uncertain_and_not_retried(store, provider, boundary, actuator)

    def test_committed_released_is_not_downgraded_by_later_interruption(self) -> None:
        actuator = CountingReleaseActuator(result=ActuatorOutcome.SUCCEEDED)
        store, provider, boundary = self._fresh_release(actuator)
        signal = KeyboardInterrupt()

        with patch("reference.swampbox._decision_id_or_none", side_effect=signal):
            with self.assertRaises(KeyboardInterrupt) as caught:
                boundary.release("execution-a", "consequence-x")

        self.assertIs(caught.exception, signal)
        self.assertEqual(store.release_state("consequence-x"), ReleaseState.RELEASED)
        self.assertNotIn("consequence-x", store._release_in_progress)
        second = boundary.release("execution-a", "consequence-x")
        self.assertEqual(second.reason, "consequence_not_releasable")
        self.assertEqual(actuator.effects, 1)

    def test_pre_actuation_copy_failure_remains_contained(self) -> None:
        signal = KeyboardInterrupt()
        actuator = CountingReleaseActuator(result=ActuatorOutcome.SUCCEEDED)
        store, provider, boundary = self._fresh_release(actuator)
        real_copy = _copy_contained_consequence
        copies = []

        def copy_or_fail(contained):
            copies.append(contained)
            if len(copies) == 2:  # 1: evaluation snapshot, 2: actuator input
                raise signal
            return real_copy(contained)

        with patch("reference.swampbox._copy_contained_consequence", copy_or_fail):
            with self.assertRaises(KeyboardInterrupt) as caught:
                boundary.release("execution-a", "consequence-x")

        self.assertIs(caught.exception, signal)
        self.assertEqual(len(copies), 2)
        self.assertEqual(actuator.effects, 0)
        self.assertEqual(store.release_state("consequence-x"), ReleaseState.CONTAINED)
        self.assertNotIn("consequence-x", store._release_in_progress)

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


class IdentifierExactTypeBindingTests(unittest.TestCase):
    """F-01: boundary-critical identifiers must be exact built-in str."""

    MATERIAL = ProposedConsequence("synthetic_task", "target-x", {"title": "content-y"})

    def setUp(self) -> None:
        self.clock = MutableClock(datetime(2026, 1, 1, 12, 0, 0))

    def _store(self) -> ExecutionScopedConsequenceStore:
        store = ExecutionScopedConsequenceStore()
        store.register_execution("execution-a")
        store.register_execution("execution-b")
        store.submit("execution-a", "consequence-x", self.MATERIAL)
        return store

    def _decision(
        self,
        contained: ContainedConsequence,
        evaluated_at: datetime,
        *,
        decision_id="decision-f01",
        consequence_id=None,
        execution_id=None,
    ) -> AuthorityDecision:
        return AuthorityDecision(
            decision_id=decision_id,
            verdict=AuthorityVerdict.ALLOW.value,
            consequence_id=(
                contained.consequence_id if consequence_id is None else consequence_id
            ),
            execution_id=(
                contained.current_execution_id if execution_id is None else execution_id
            ),
            bound_consequence=ProposedConsequence(
                contained.consequence_type, contained.target, contained.payload
            ),
            issued_at=evaluated_at,
            valid_until=evaluated_at + timedelta(minutes=5),
        )

    @staticmethod
    def _snapshot(store: ExecutionScopedConsequenceStore):
        return (
            dict(store._executions),
            dict(store._consequences),
            dict(store._release_states),
            set(store._release_in_progress),
        )

    def _assert_only_plain_str_keys(self, store) -> None:
        for mapping in (store._executions, store._consequences, store._release_states):
            for key in mapping:
                self.assertIs(type(key), str)
        for stored in store._consequences.values():
            self.assertIs(type(stored.consequence_id), str)
            self.assertIs(type(stored.origin_execution_id), str)
            if stored.current_execution_id is not None:
                self.assertIs(type(stored.current_execution_id), str)
            if stored.parent_consequence_id is not None:
                self.assertIs(type(stored.parent_consequence_id), str)

    # -- positive control ---------------------------------------------------

    def test_plain_str_identifiers_still_release_with_lineage(self) -> None:
        store = self._store()
        store.end_execution("execution-a")
        store.adopt("execution-b", "consequence-x")
        child = store.submit(
            "execution-b",
            "consequence-c",
            ProposedConsequence(
                "derived_task", "target-derived", {"n": 1},
                parent_consequence_id="consequence-x",
            ),
        )
        actuator = CountingReleaseActuator(result=ActuatorOutcome.SUCCEEDED)
        provider = RecordingAuthorityProvider(self._decision)

        result = ReleaseBoundary(store, provider, actuator, self.clock).release(
            "execution-b", "consequence-c"
        )

        self.assertEqual(child.parent_consequence_id, "consequence-x")
        self.assertEqual(result.status, ReleaseState.RELEASED)
        self.assertEqual(result.reason, "actuator_succeeded")
        self.assertEqual(actuator.effects, 1)
        self._assert_only_plain_str_keys(store)

    def test_existing_invalid_plain_values_still_fail_the_same_way(self) -> None:
        store = self._store()
        for bad in ("", " ", " padded", "padded ", None, 1, b"bytes"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    store.register_execution(bad)
                with self.assertRaises(ValueError):
                    store.release_state(bad)

    # -- ingress ------------------------------------------------------------

    def test_every_public_identifier_parameter_rejects_hostile_subclass(self) -> None:
        def execution() -> HostileIdentifier:
            return HostileIdentifier("execution-a", alias="execution-a")

        def consequence() -> HostileIdentifier:
            return HostileIdentifier("consequence-x", alias="consequence-x")

        child = lambda h: ProposedConsequence(
            "derived_task", "target-derived", {"n": 1}, parent_consequence_id=h
        )
        cases = {
            "register_execution.execution_id": (execution, lambda s, b, h: s.register_execution(h)),
            "end_execution.execution_id": (execution, lambda s, b, h: s.end_execution(h)),
            "submit.execution_id": (execution, lambda s, b, h: s.submit(h, "consequence-new", self.MATERIAL)),
            "submit.consequence_id": (consequence, lambda s, b, h: s.submit("execution-a", h, self.MATERIAL)),
            "submit.parent_consequence_id": (consequence, lambda s, b, h: s.submit("execution-a", "consequence-new", child(h))),
            "read.execution_id": (execution, lambda s, b, h: s.read(h, "consequence-x")),
            "read.consequence_id": (consequence, lambda s, b, h: s.read("execution-a", h)),
            "transfer.source_execution_id": (execution, lambda s, b, h: s.transfer(h, "execution-b", "consequence-x")),
            "transfer.target_execution_id": (execution, lambda s, b, h: s.transfer("execution-a", h, "consequence-x")),
            "transfer.consequence_id": (consequence, lambda s, b, h: s.transfer("execution-a", "execution-b", h)),
            "inspect_quarantined.consequence_id": (consequence, lambda s, b, h: s.inspect_quarantined(h)),
            "adopt.execution_id": (execution, lambda s, b, h: s.adopt(h, "consequence-x")),
            "adopt.consequence_id": (consequence, lambda s, b, h: s.adopt("execution-b", h)),
            "release_state.consequence_id": (consequence, lambda s, b, h: s.release_state(h)),
            "release.execution_id": (execution, lambda s, b, h: b.release(h, "consequence-x")),
            "release.consequence_id": (consequence, lambda s, b, h: b.release("execution-a", h)),
        }
        for name, (make_hostile, operation) in cases.items():
            with self.subTest(parameter=name):
                store = self._store()
                provider = RecordingAuthorityProvider(self._decision)
                actuator = CountingReleaseActuator(result=ActuatorOutcome.SUCCEEDED)
                boundary = ReleaseBoundary(store, provider, actuator, self.clock)
                before = self._snapshot(store)
                hostile = make_hostile()

                with self.assertRaises(ValueError):
                    operation(store, boundary, hostile)

                self.assertEqual(hostile.calls, [])
                self.assertEqual(self._snapshot(store), before)
                self.assertEqual(provider.calls, [])
                self.assertEqual(actuator.effects, 0)
                self._assert_only_plain_str_keys(store)

    # -- consequence binding (the 14A F-01 attack) ---------------------------

    def _x_decision_captured_via_dne(self, store):
        """Capture a decision bound to X; a DNE outcome keeps X releasable."""

        captured = {}

        def factory(contained, evaluated_at):
            decision = self._decision(contained, evaluated_at, decision_id="decision-x")
            captured["x"] = decision
            return decision

        actuator = CountingReleaseActuator(result=ActuatorOutcome.DEFINITE_NOT_EXECUTED)
        ReleaseBoundary(
            store, RecordingAuthorityProvider(factory), actuator, self.clock
        ).release("execution-a", "consequence-x")
        self.assertEqual(actuator.effects, 1)
        return captured["x"], actuator

    def test_plain_str_control_x_bound_authority_does_not_release_c(self) -> None:
        store = self._store()
        x_decision, actuator = self._x_decision_captured_via_dne(store)
        store.submit("execution-a", "consequence-c", self.MATERIAL)
        replay = RecordingAuthorityProvider(lambda contained, at: x_decision)

        result = ReleaseBoundary(store, replay, actuator, self.clock).release(
            "execution-a", "consequence-c"
        )

        self.assertEqual(result.reason, "authority_consequence_mismatch")
        self.assertEqual(actuator.effects, 1)

    def test_hostile_consequence_id_cannot_make_foreign_authority_reach_actuator(self) -> None:
        store = self._store()
        x_decision, actuator = self._x_decision_captured_via_dne(store)
        entries_before = actuator.effects
        hostile_c = HostileIdentifier("consequence-c", alias="consequence-c")
        submit_rejected = False
        try:
            store.submit("execution-a", hostile_c, self.MATERIAL)
        except ValueError:
            submit_rejected = True
        replay = RecordingAuthorityProvider(lambda contained, at: x_decision)
        boundary = ReleaseBoundary(store, replay, actuator, self.clock)

        try:
            result = boundary.release("execution-a", "consequence-c")
        except (KeyError, ValueError):
            result = None

        # The security consequence first: X's authority must never actuate C.
        self.assertEqual(actuator.effects, entries_before)
        self.assertTrue(result is None or not result.released)
        self.assertTrue(submit_rejected)
        self.assertEqual(hostile_c.calls, [])
        with self.assertRaises(KeyError):
            store.release_state("consequence-c")
        self._assert_only_plain_str_keys(store)

    # -- custodian binding ---------------------------------------------------

    def test_plain_str_control_decision_for_other_custodian_does_not_release(self) -> None:
        store = self._store()
        provider = RecordingAuthorityProvider(
            lambda contained, at: self._decision(contained, at, execution_id="execution-b")
        )
        actuator = CountingReleaseActuator(result=ActuatorOutcome.SUCCEEDED)

        result = ReleaseBoundary(store, provider, actuator, self.clock).release(
            "execution-a", "consequence-x"
        )

        self.assertEqual(result.reason, "authority_custodian_mismatch")
        self.assertEqual(actuator.effects, 0)

    def test_hostile_execution_id_cannot_defeat_custodian_binding(self) -> None:
        store = self._store()
        provider = RecordingAuthorityProvider(
            lambda contained, at: self._decision(contained, at, execution_id="execution-b")
        )
        actuator = CountingReleaseActuator(result=ActuatorOutcome.SUCCEEDED)
        boundary = ReleaseBoundary(store, provider, actuator, self.clock)
        hostile_a = HostileIdentifier("execution-a", alias="execution-a")

        try:
            result = boundary.release(hostile_a, "consequence-x")
        except (KeyError, ValueError):
            result = None

        self.assertEqual(actuator.effects, 0)
        self.assertTrue(result is None or not result.released)
        self.assertEqual(store.release_state("consequence-x"), ReleaseState.CONTAINED)
        self.assertEqual(hostile_a.calls, [])

    def test_hostile_execution_id_cannot_enter_the_store_as_custodian(self) -> None:
        store = self._store()
        hostile = HostileIdentifier("execution-h", alias="execution-b")
        before = self._snapshot(store)

        with self.assertRaises(ValueError):
            store.register_execution(hostile)
        with self.assertRaises(ValueError):
            store.submit(hostile, "consequence-h", self.MATERIAL)

        self.assertEqual(self._snapshot(store), before)
        self.assertEqual(hostile.calls, [])
        self._assert_only_plain_str_keys(store)

    # -- parent lineage --------------------------------------------------------

    def test_hostile_parent_id_cannot_become_stored_lineage(self) -> None:
        store = self._store()
        hostile_parent = HostileIdentifier("anything", alias="consequence-x")
        before = self._snapshot(store)

        with self.assertRaises(ValueError):
            store.submit(
                "execution-a",
                "consequence-child",
                ProposedConsequence(
                    "derived_task", "target-derived", {"n": 1},
                    parent_consequence_id=hostile_parent,
                ),
            )

        with self.assertRaises(KeyError):
            store.read("execution-a", "consequence-child")
        self.assertEqual(self._snapshot(store), before)
        self.assertEqual(hostile_parent.calls, [])
        self._assert_only_plain_str_keys(store)

    # -- provider-returned identifiers -----------------------------------------

    def test_provider_returned_hostile_identifiers_fail_closed(self) -> None:
        fields = {
            "consequence_id": ("consequence-x", "consequence_id"),
            "execution_id": ("execution-a", "execution_id"),
            "decision_id": ("decision-f01", "decision_id"),
        }
        for name, (real_value, keyword) in fields.items():
            with self.subTest(field=name):
                store = self._store()
                hostile = HostileIdentifier(real_value, alias=real_value)
                provider = RecordingAuthorityProvider(
                    lambda contained, at: self._decision(contained, at, **{keyword: hostile})
                )
                actuator = CountingReleaseActuator(result=ActuatorOutcome.SUCCEEDED)

                result = ReleaseBoundary(store, provider, actuator, self.clock).release(
                    "execution-a", "consequence-x"
                )

                self.assertEqual(actuator.effects, 0)
                self.assertEqual(result.status, ReleaseState.CONTAINED)
                self.assertEqual(result.reason, "authority_invalid")
                self.assertEqual(store.release_state("consequence-x"), ReleaseState.CONTAINED)
                self.assertEqual(len(provider.calls), 1)
                self.assertEqual(hostile.calls, [])


class ActuatorOutcomeExactRecognitionTests(unittest.TestCase):
    """F-03: only exactly recognized outcomes may carry finality meaning."""

    MATERIAL = ProposedConsequence("synthetic_task", "target-x", {"title": "content-y"})

    def setUp(self) -> None:
        self.clock = MutableClock(datetime(2026, 1, 1, 12, 0, 0))

    def _decision(self, contained: ContainedConsequence, evaluated_at: datetime):
        return AuthorityDecision(
            decision_id="decision-f03",
            verdict=AuthorityVerdict.ALLOW.value,
            consequence_id=contained.consequence_id,
            execution_id=contained.current_execution_id,
            bound_consequence=ProposedConsequence(
                contained.consequence_type, contained.target, contained.payload
            ),
            issued_at=evaluated_at,
            valid_until=evaluated_at + timedelta(minutes=5),
        )

    def _world(self, actuator):
        store = ExecutionScopedConsequenceStore()
        store.register_execution("execution-a")
        store.submit("execution-a", "consequence-x", self.MATERIAL)
        provider = RecordingAuthorityProvider(self._decision)
        boundary = ReleaseBoundary(store, provider, actuator, self.clock)
        return store, provider, boundary

    def _assert_terminal_uncertain_after_first_release(
        self, store, provider, boundary, actuator, first
    ) -> None:
        provider_calls = len(provider.calls)
        second = boundary.release("execution-a", "consequence-x")

        # The security consequence first: no second actuator entry.
        self.assertEqual(actuator.effects, 1)
        self.assertEqual(first.status, ReleaseState.UNCERTAIN)
        self.assertEqual(first.reason, "actuator_outcome_uncertain")
        self.assertIs(first.actuator_outcome, ActuatorOutcome.UNCERTAIN)
        self.assertFalse(first.released)
        self.assertEqual(store.release_state("consequence-x"), ReleaseState.UNCERTAIN)
        self.assertNotIn("consequence-x", store._release_in_progress)
        self.assertEqual(second.reason, "consequence_not_releasable")
        self.assertFalse(second.released)
        self.assertEqual(len(provider.calls), provider_calls)

    # -- primary regression (the 14A duplicate-actuation attack) -------------

    def test_spoofed_dne_after_effect_cannot_permit_second_actuation(self) -> None:
        hostile = HostileOutcomeStr("effect-was-performed", alias="DEFINITE_NOT_EXECUTED")
        # A second entry (only possible if the first was misread as retryable)
        # would report SUCCEEDED, i.e. a duplicate real effect.
        actuator = ScriptedOutcomeActuator(hostile, ActuatorOutcome.SUCCEEDED)
        store, provider, boundary = self._world(actuator)

        first = boundary.release("execution-a", "consequence-x")

        self.assertEqual(actuator.effects, 1)
        self._assert_terminal_uncertain_after_first_release(
            store, provider, boundary, actuator, first
        )
        self.assertEqual(hostile.calls, [])

    # -- hostile / malformed outcome matrix -------------------------------------

    def test_every_noncanonical_outcome_is_uncertain_and_terminal(self) -> None:
        cases = {
            "str subclass spoofing DEFINITE_NOT_EXECUTED": lambda: HostileOutcomeStr("x", alias="DEFINITE_NOT_EXECUTED"),
            "str subclass spoofing SUCCEEDED": lambda: HostileOutcomeStr("x", alias="SUCCEEDED"),
            "object with lying __eq__ and __hash__ (DNE)": lambda: HostileOutcomeObject("DEFINITE_NOT_EXECUTED"),
            "object with lying __eq__ and __hash__ (SUCCEEDED)": lambda: HostileOutcomeObject("SUCCEEDED"),
            "object with lying __eq__ only": HostileEqualityOnlyOutcome,
            "object with lying __hash__ only": lambda: HostileHashOnlyOutcome("SUCCEEDED"),
            "hash-raising str subclass": lambda: _hash_raising_outcome(RuntimeError("hash")),
            "honest plain str subclass of DNE": lambda: PlainStrSubclass("DEFINITE_NOT_EXECUTED"),
            "honest plain str subclass of SUCCEEDED": lambda: PlainStrSubclass("SUCCEEDED"),
            "foreign str Enum with DNE value": lambda: ForeignStrEnum.DEFINITE_NOT_EXECUTED,
            "None": lambda: None,
            "True": lambda: True,
            "False": lambda: False,
            "int 0": lambda: 0,
            "int 1": lambda: 1,
            "arbitrary object": object,
            "bytes": lambda: b"SUCCEEDED",
            "unknown exact str": lambda: "SURPRISE",
            "wrong-case exact str": lambda: "succeeded",
            "padded exact str": lambda: "SUCCEEDED ",
        }
        for name, make in cases.items():
            with self.subTest(outcome=name):
                value = make()
                actuator = ScriptedOutcomeActuator(value, ActuatorOutcome.SUCCEEDED)
                store, provider, boundary = self._world(actuator)

                first = boundary.release("execution-a", "consequence-x")

                self._assert_terminal_uncertain_after_first_release(
                    store, provider, boundary, actuator, first
                )
                self.assertEqual(getattr(value, "calls", []), [])

    # -- canonical controls --------------------------------------------------

    def test_canonical_succeeded_still_releases_and_is_terminal(self) -> None:
        actuator = ScriptedOutcomeActuator(ActuatorOutcome.SUCCEEDED)
        store, provider, boundary = self._world(actuator)

        first = boundary.release("execution-a", "consequence-x")
        second = boundary.release("execution-a", "consequence-x")

        self.assertEqual(first.status, ReleaseState.RELEASED)
        self.assertEqual(first.reason, "actuator_succeeded")
        self.assertIs(first.actuator_outcome, ActuatorOutcome.SUCCEEDED)
        self.assertEqual(second.reason, "consequence_not_releasable")
        self.assertEqual(actuator.effects, 1)
        self.assertEqual(len(provider.calls), 1)

    def test_canonical_dne_stays_contained_and_a_legitimate_retry_succeeds(self) -> None:
        actuator = ScriptedOutcomeActuator(
            ActuatorOutcome.DEFINITE_NOT_EXECUTED, ActuatorOutcome.SUCCEEDED
        )
        store, provider, boundary = self._world(actuator)

        first = boundary.release("execution-a", "consequence-x")

        self.assertEqual(first.status, ReleaseState.CONTAINED)
        self.assertEqual(first.reason, "actuator_definitely_not_executed")
        self.assertIs(first.actuator_outcome, ActuatorOutcome.DEFINITE_NOT_EXECUTED)
        self.assertEqual(store.release_state("consequence-x"), ReleaseState.CONTAINED)
        self.assertNotIn("consequence-x", store._release_in_progress)

        second = boundary.release("execution-a", "consequence-x")

        self.assertEqual(second.status, ReleaseState.RELEASED)
        self.assertEqual(second.reason, "actuator_succeeded")
        self.assertEqual(actuator.effects, 2)
        self.assertEqual(len(provider.calls), 2)

    def test_canonical_dne_may_retry_but_spoofed_dne_may_not(self) -> None:
        canonical = ScriptedOutcomeActuator(
            ActuatorOutcome.DEFINITE_NOT_EXECUTED, ActuatorOutcome.SUCCEEDED
        )
        spoofed = ScriptedOutcomeActuator(
            HostileOutcomeStr("x", alias="DEFINITE_NOT_EXECUTED"),
            ActuatorOutcome.SUCCEEDED,
        )
        results = {}
        for name, actuator in (("canonical", canonical), ("spoofed", spoofed)):
            store, provider, boundary = self._world(actuator)
            boundary.release("execution-a", "consequence-x")
            results[name] = (
                boundary.release("execution-a", "consequence-x"),
                actuator.effects,
                store.release_state("consequence-x"),
            )

        self.assertEqual(results["spoofed"][1], 1)
        self.assertEqual(results["spoofed"][2], ReleaseState.UNCERTAIN)
        self.assertEqual(results["spoofed"][0].reason, "consequence_not_releasable")
        self.assertEqual(results["canonical"][1], 2)
        self.assertEqual(results["canonical"][2], ReleaseState.RELEASED)

    def test_canonical_uncertain_stays_terminal(self) -> None:
        actuator = ScriptedOutcomeActuator(
            ActuatorOutcome.UNCERTAIN, ActuatorOutcome.SUCCEEDED
        )
        store, provider, boundary = self._world(actuator)

        first = boundary.release("execution-a", "consequence-x")

        self._assert_terminal_uncertain_after_first_release(
            store, provider, boundary, actuator, first
        )

    def test_exact_builtin_strings_remain_recognized(self) -> None:
        # ReleaseActuator is declared as returning ``ActuatorOutcome | str``;
        # exact built-in strings keep their meaning, subclasses do not.
        expected = {
            "SUCCEEDED": (ReleaseState.RELEASED, "actuator_succeeded"),
            "DEFINITE_NOT_EXECUTED": (ReleaseState.CONTAINED, "actuator_definitely_not_executed"),
            "UNCERTAIN": (ReleaseState.UNCERTAIN, "actuator_outcome_uncertain"),
        }
        for raw, (state, reason) in expected.items():
            with self.subTest(raw=raw):
                store, provider, boundary = self._world(ScriptedOutcomeActuator(raw))

                result = boundary.release("execution-a", "consequence-x")

                self.assertEqual((result.status, result.reason), (state, reason))

    # -- exception controls (12U) ------------------------------------------------

    def test_ordinary_actuator_exception_is_uncertain_and_terminal(self) -> None:
        actuator = CountingReleaseActuator(raises=RuntimeError("response lost"))
        store, provider, boundary = self._world(actuator)

        first = boundary.release("execution-a", "consequence-x")

        self._assert_terminal_uncertain_after_first_release(
            store, provider, boundary, actuator, first
        )


class ReadRecordingClock(MutableClock):
    """MutableClock that records every value it returns."""

    def __init__(self, value: datetime) -> None:
        super().__init__(value)
        self.reads: list[datetime] = []

    def __call__(self) -> datetime:
        self.reads.append(self.value)
        return self.value


class ClockStampedActuator:
    """Counts entries and stamps the clock value seen at each entry."""

    def __init__(self, clock: MutableClock, *outcomes) -> None:
        self.clock = clock
        self.outcomes = list(outcomes)
        self.effects = 0
        self.entry_times: list[datetime] = []

    def actuate(self, consequence: ContainedConsequence):
        self.effects += 1
        self.entry_times.append(self.clock.value)
        return self.outcomes.pop(0)


class AuthorityFinalTemporalCheckTests(unittest.TestCase):
    """F-02: the SAME decision is re-checked immediately before actuator entry.

    This is a local temporal check of the returned decision. It is not a second
    provider evaluation and does not detect external revocation.
    """

    MATERIAL = ProposedConsequence("synthetic_task", "target-x", {"title": "content-y"})
    T0 = datetime(2026, 1, 1, 12, 0, 0)
    TICK = timedelta(microseconds=1)

    def setUp(self) -> None:
        self.clock = ReadRecordingClock(self.T0)
        self.decisions: list[AuthorityDecision] = []

    def _factory(self, windows=(timedelta(minutes=5),), issued_offset=timedelta(0)):
        def factory(contained: ContainedConsequence, evaluated_at: datetime):
            window = windows[min(len(self.decisions), len(windows) - 1)]
            issued = evaluated_at + issued_offset
            decision = AuthorityDecision(
                decision_id=f"decision-f02-{len(self.decisions)}",
                verdict=AuthorityVerdict.ALLOW.value,
                consequence_id=contained.consequence_id,
                execution_id=contained.current_execution_id,
                bound_consequence=ProposedConsequence(
                    contained.consequence_type, contained.target, contained.payload
                ),
                issued_at=issued,
                valid_until=issued + window,
            )
            self.decisions.append(decision)
            return decision

        return factory

    def _world(self, actuator, factory):
        store = ExecutionScopedConsequenceStore()
        store.register_execution("execution-a")
        store.submit("execution-a", "consequence-x", self.MATERIAL)
        provider = RecordingAuthorityProvider(factory)
        boundary = ReleaseBoundary(store, provider, actuator, self.clock)
        return store, provider, boundary

    @contextmanager
    def _time_passes_during_preparation(self, new_value):
        """Advance the clock while the boundary prepares the actuator input.

        Uses the existing copy seam; no production hook. Copies inside
        release(): 1 evaluation snapshot, 2 actuator input.
        """

        real_copy = _copy_contained_consequence
        seen = {"n": 0}

        def copy_then_advance(contained):
            result = real_copy(contained)
            seen["n"] += 1
            if seen["n"] == 2:
                self.clock.value = new_value
            return result

        with patch("reference.swampbox._copy_contained_consequence", copy_then_advance):
            yield

    # -- primary regression ---------------------------------------------------------

    def test_authority_expiring_during_preparation_does_not_reach_actuator(self) -> None:
        actuator = ClockStampedActuator(
            self.clock, ActuatorOutcome.SUCCEEDED, ActuatorOutcome.SUCCEEDED
        )
        windows = (timedelta(seconds=60), timedelta(minutes=5))
        store, provider, boundary = self._world(actuator, self._factory(windows))
        first_valid_until = self.T0 + timedelta(seconds=60)

        with self._time_passes_during_preparation(first_valid_until + timedelta(seconds=1)):
            first = boundary.release("execution-a", "consequence-x")

        # The security consequence first: expired authority must not actuate.
        self.assertEqual(actuator.effects, 0)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(first.status, ReleaseState.CONTAINED)
        self.assertEqual(first.reason, "authority_expired")
        self.assertIsNone(first.actuator_outcome)
        self.assertEqual(first.authority_decision_id, "decision-f02-0")
        self.assertEqual(store.release_state("consequence-x"), ReleaseState.CONTAINED)
        self.assertNotIn("consequence-x", store._release_in_progress)
        # initial check, then the final check (which refused); no third read.
        self.assertEqual(len(self.clock.reads), 3)

        # A NEW release attempt evaluates the provider again and may proceed.
        second = boundary.release("execution-a", "consequence-x")

        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(second.status, ReleaseState.RELEASED)
        self.assertEqual(second.reason, "actuator_succeeded")
        self.assertEqual(actuator.effects, 1)
        fresh = self.decisions[1]
        self.assertIsNot(fresh, self.decisions[0])
        self.assertTrue(fresh.issued_at <= actuator.entry_times[0] < fresh.valid_until)

    # -- exact boundaries at the final check ------------------------------------------

    def test_final_check_preserves_valid_until_boundary(self) -> None:
        valid_until = self.T0 + timedelta(seconds=60)
        cases = {
            "exactly valid_until is expired": (valid_until, 0, "authority_expired"),
            "one tick before valid_until is valid": (valid_until - self.TICK, 1, "actuator_succeeded"),
        }
        for name, (final_time, entries, reason) in cases.items():
            with self.subTest(case=name):
                self.setUp()
                actuator = ClockStampedActuator(self.clock, ActuatorOutcome.SUCCEEDED)
                store, provider, boundary = self._world(
                    actuator, self._factory((timedelta(seconds=60),))
                )

                with self._time_passes_during_preparation(final_time):
                    result = boundary.release("execution-a", "consequence-x")

                self.assertEqual(actuator.effects, entries)
                self.assertEqual(result.reason, reason)
                self.assertEqual(len(provider.calls), 1)

    def test_final_check_preserves_issued_at_boundary(self) -> None:
        cases = {
            "clock before issued_at is not yet valid": (self.T0 - self.TICK, 0, "authority_not_yet_valid"),
            "clock exactly at issued_at is valid": (self.T0, 1, "actuator_succeeded"),
        }
        for name, (final_time, entries, reason) in cases.items():
            with self.subTest(case=name):
                self.setUp()
                actuator = ClockStampedActuator(self.clock, ActuatorOutcome.SUCCEEDED)
                store, provider, boundary = self._world(actuator, self._factory())

                with self._time_passes_during_preparation(final_time):
                    result = boundary.release("execution-a", "consequence-x")

                self.assertEqual(actuator.effects, entries)
                self.assertEqual(result.reason, reason)
                self.assertEqual(len(provider.calls), 1)

    def test_invalid_clock_value_at_final_check_fails_closed(self) -> None:
        actuator = ClockStampedActuator(self.clock, ActuatorOutcome.SUCCEEDED)
        store, provider, boundary = self._world(actuator, self._factory())

        with self._time_passes_during_preparation("not-a-datetime"):
            result = boundary.release("execution-a", "consequence-x")

        self.assertEqual(actuator.effects, 0)
        self.assertEqual(result.reason, "clock_invalid")
        self.assertEqual(store.release_state("consequence-x"), ReleaseState.CONTAINED)
        self.assertNotIn("consequence-x", store._release_in_progress)

    # -- initial validation is unchanged ------------------------------------------------

    def test_initially_expired_decision_is_rejected_by_the_initial_check(self) -> None:
        actuator = ClockStampedActuator(self.clock, ActuatorOutcome.SUCCEEDED)
        factory = self._factory((timedelta(minutes=5),), issued_offset=-timedelta(minutes=10))
        store, provider, boundary = self._world(actuator, factory)

        result = boundary.release("execution-a", "consequence-x")

        self.assertEqual(actuator.effects, 0)
        self.assertEqual(result.reason, "authority_expired")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(store.release_state("consequence-x"), ReleaseState.CONTAINED)
        self.assertEqual(len(self.clock.reads), 2)  # the final check never ran

    def test_initially_not_yet_valid_decision_is_rejected_by_the_initial_check(self) -> None:
        actuator = ClockStampedActuator(self.clock, ActuatorOutcome.SUCCEEDED)
        factory = self._factory(issued_offset=self.TICK)
        store, provider, boundary = self._world(actuator, factory)

        result = boundary.release("execution-a", "consequence-x")

        self.assertEqual(actuator.effects, 0)
        self.assertEqual(result.reason, "authority_not_yet_valid")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(self.clock.reads), 2)

    # -- same decision, not a re-evaluation; outcome semantics unchanged -------------

    def test_successful_release_evaluates_once_and_reads_the_clock_three_times(self) -> None:
        actuator = ClockStampedActuator(self.clock, ActuatorOutcome.SUCCEEDED)
        store, provider, boundary = self._world(actuator, self._factory())

        result = boundary.release("execution-a", "consequence-x")

        self.assertEqual(result.status, ReleaseState.RELEASED)
        self.assertEqual(result.reason, "actuator_succeeded")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(actuator.effects, 1)
        # evaluated_at, initial validation, final pre-actuation validation
        self.assertEqual(len(self.clock.reads), 3)
        self.assertEqual(len(self.decisions), 1)

    def test_canonical_dne_and_uncertain_semantics_are_unchanged(self) -> None:
        actuator = ClockStampedActuator(
            self.clock, ActuatorOutcome.DEFINITE_NOT_EXECUTED, ActuatorOutcome.SUCCEEDED
        )
        store, provider, boundary = self._world(actuator, self._factory())

        first = boundary.release("execution-a", "consequence-x")

        self.assertEqual(first.status, ReleaseState.CONTAINED)
        self.assertEqual(first.reason, "actuator_definitely_not_executed")
        self.assertEqual(len(provider.calls), 1)

        second = boundary.release("execution-a", "consequence-x")

        self.assertEqual(second.status, ReleaseState.RELEASED)
        self.assertEqual(actuator.effects, 2)
        self.assertEqual(len(provider.calls), 2)

        self.setUp()
        uncertain = ClockStampedActuator(
            self.clock, ActuatorOutcome.UNCERTAIN, ActuatorOutcome.SUCCEEDED
        )
        store, provider, boundary = self._world(uncertain, self._factory())

        first = boundary.release("execution-a", "consequence-x")
        second = boundary.release("execution-a", "consequence-x")

        self.assertEqual(first.status, ReleaseState.UNCERTAIN)
        self.assertEqual(second.reason, "consequence_not_releasable")
        self.assertEqual(uncertain.effects, 1)
        self.assertEqual(len(provider.calls), 1)


class InterruptAfterAddMarkerSet(set):
    """Test-only instrument: performs the real add, then raises once.

    Models an interruption delivered right after marker membership has been
    established. Hostile set replacement is not a production threat model; this
    only makes the timing deterministic.
    """

    def __init__(self, signal: BaseException) -> None:
        super().__init__()
        self.signal = signal
        self.armed = True

    def add(self, value) -> None:
        super().add(value)
        if self.armed:
            self.armed = False
            raise self.signal


class InterruptBeforeAddMarkerSet(InterruptAfterAddMarkerSet):
    """Test-only instrument: raises once BEFORE membership is established."""

    def add(self, value) -> None:
        if self.armed:
            self.armed = False
            raise self.signal
        super().add(value)


class ReleaseMarkerCleanupOwnershipTests(unittest.TestCase):
    """F-05: the in-progress marker is cleanup-owned from the moment it is set."""

    MATERIAL = ProposedConsequence("synthetic_task", "target-x", {"title": "content-y"})

    def setUp(self) -> None:
        self.clock = MutableClock(datetime(2026, 1, 1, 12, 0, 0))

    def _decision(self, contained: ContainedConsequence, evaluated_at: datetime):
        return AuthorityDecision(
            decision_id="decision-f05",
            verdict=AuthorityVerdict.ALLOW.value,
            consequence_id=contained.consequence_id,
            execution_id=contained.current_execution_id,
            bound_consequence=ProposedConsequence(
                contained.consequence_type, contained.target, contained.payload
            ),
            issued_at=evaluated_at,
            valid_until=evaluated_at + timedelta(minutes=5),
        )

    def _world(self, marker_set, actuator):
        store = ExecutionScopedConsequenceStore()
        store.register_execution("execution-a")
        store.submit("execution-a", "consequence-x", self.MATERIAL)
        store._release_in_progress = marker_set
        provider = RecordingAuthorityProvider(self._decision)
        boundary = ReleaseBoundary(store, provider, actuator, self.clock)
        return store, provider, boundary

    def test_interruption_after_marker_establishment_leaves_no_stale_marker(self) -> None:
        signals = {
            "KeyboardInterrupt": KeyboardInterrupt,
            "SystemExit": SystemExit,
            "GeneratorExit": GeneratorExit,
            "CancelledError": asyncio.CancelledError,
            "CustomBaseException": CustomBaseException,
            "ordinary Exception": lambda: RuntimeError("interrupted"),
        }
        for name, make in signals.items():
            with self.subTest(signal=name):
                signal = make()
                markers = InterruptAfterAddMarkerSet(signal)
                actuator = CountingReleaseActuator(result=ActuatorOutcome.SUCCEEDED)
                store, provider, boundary = self._world(markers, actuator)

                with self.assertRaises(type(signal)) as caught:
                    boundary.release("execution-a", "consequence-x")

                # The marker must not outlive the interrupted release.
                self.assertNotIn("consequence-x", store._release_in_progress)
                self.assertIs(caught.exception, signal)
                self.assertEqual(
                    store.release_state("consequence-x"), ReleaseState.CONTAINED
                )
                self.assertEqual(len(provider.calls), 1)
                self.assertEqual(actuator.effects, 0)

                # A normal later release proceeds; the marker was never cleared by hand.
                second = boundary.release("execution-a", "consequence-x")

                self.assertEqual(second.status, ReleaseState.RELEASED)
                self.assertEqual(second.reason, "actuator_succeeded")
                self.assertEqual(len(provider.calls), 2)
                self.assertEqual(actuator.effects, 1)
                self.assertEqual(
                    store.release_state("consequence-x"), ReleaseState.RELEASED
                )
                self.assertNotIn("consequence-x", store._release_in_progress)

    def test_interrupted_release_does_not_block_the_custodian_lifecycle(self) -> None:
        signal = KeyboardInterrupt()
        markers = InterruptAfterAddMarkerSet(signal)
        actuator = CountingReleaseActuator(result=ActuatorOutcome.SUCCEEDED)
        store, provider, boundary = self._world(markers, actuator)
        store.register_execution("execution-b")

        with self.assertRaises(KeyboardInterrupt):
            boundary.release("execution-a", "consequence-x")

        moved = store.transfer("execution-a", "execution-b", "consequence-x")
        store.end_execution("execution-a")

        self.assertEqual(moved.current_execution_id, "execution-b")
        self.assertEqual(actuator.effects, 0)

    def test_interruption_before_marker_establishment_is_also_clean(self) -> None:
        signal = KeyboardInterrupt()
        markers = InterruptBeforeAddMarkerSet(signal)
        actuator = CountingReleaseActuator(result=ActuatorOutcome.SUCCEEDED)
        store, provider, boundary = self._world(markers, actuator)

        with self.assertRaises(KeyboardInterrupt) as caught:
            boundary.release("execution-a", "consequence-x")

        self.assertIs(caught.exception, signal)
        self.assertNotIn("consequence-x", store._release_in_progress)
        self.assertEqual(store.release_state("consequence-x"), ReleaseState.CONTAINED)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(actuator.effects, 0)

        second = boundary.release("execution-a", "consequence-x")

        self.assertEqual(second.status, ReleaseState.RELEASED)
        self.assertEqual(actuator.effects, 1)


if __name__ == "__main__":
    unittest.main()
