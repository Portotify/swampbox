"""Small in-memory consequence-boundary reference model."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import math
from threading import RLock
from typing import Any
from typing import Callable, Protocol


ALLOWED_RESULTS = frozenset({"ALLOW", "HOLD", "DENY"})


@dataclass(frozen=True)
class PersistedArtifact:
    artifact_id: str
    origin: str
    task_type: str
    target: str
    payload: Any


@dataclass(frozen=True)
class ProposedConsequence:
    consequence_type: str
    target: str
    payload: Any


@dataclass(frozen=True)
class ContainedConsequence:
    """An immutable consequence visible only in its current execution scope."""

    consequence_id: str
    origin_execution_id: str
    current_execution_id: str
    consequence_type: str
    target: str
    payload: Any


class AuthorityVerdict(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


@dataclass(frozen=True)
class AuthorityDecision:
    """A provider decision bound to one exact contained material snapshot."""

    decision_id: str
    verdict: str
    consequence_id: str
    execution_id: str
    bound_consequence: ProposedConsequence
    issued_at: datetime
    valid_until: datetime


class AuthorityProvider(Protocol):
    def evaluate(
        self,
        consequence: ContainedConsequence,
        evaluated_at: datetime,
    ) -> AuthorityDecision:
        """Evaluate the exact snapshot for its current custodian."""


class ReleaseState(str, Enum):
    CONTAINED = "CONTAINED"
    RELEASED = "RELEASED"
    UNCERTAIN = "UNCERTAIN"


class ActuatorOutcome(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    DEFINITE_NOT_EXECUTED = "DEFINITE_NOT_EXECUTED"
    UNCERTAIN = "UNCERTAIN"


class ReleaseActuator(Protocol):
    def actuate(self, consequence: ContainedConsequence) -> ActuatorOutcome | str:
        """Actuate the exact snapshot supplied by the release boundary."""


@dataclass(frozen=True)
class ReleaseResult:
    consequence_id: str
    status: ReleaseState
    reason: str
    authority_decision_id: str | None = None
    actuator_outcome: ActuatorOutcome | None = None

    @property
    def state(self) -> ReleaseState:
        return self.status

    @property
    def released(self) -> bool:
        return self.status is ReleaseState.RELEASED


@dataclass(frozen=True)
class AdmissionResult:
    result: str
    admitted_consequence: ProposedConsequence | None = None


@dataclass(frozen=True)
class Receipt:
    committed: bool
    consequence: ProposedConsequence
    outcome: str


class ArtifactStore:
    """In-memory storage that stores artifacts but never grants permission."""

    def __init__(self) -> None:
        self._artifacts: dict[str, PersistedArtifact] = {}

    def persist(self, artifact: PersistedArtifact) -> None:
        if not isinstance(artifact, PersistedArtifact):
            raise TypeError("artifact must be a PersistedArtifact")
        self._artifacts[artifact.artifact_id] = artifact

    def read(self, artifact_id: str) -> PersistedArtifact | None:
        return self._artifacts.get(artifact_id)


class ExecutionScopedConsequenceStore:
    """In-memory storage for consequences scoped to one execution at a time."""

    def __init__(self) -> None:
        self._executions: set[str] = set()
        self._consequences: dict[str, ContainedConsequence] = {}
        self._release_states: dict[str, ReleaseState] = {}
        self._release_in_progress: set[str] = set()
        self._release_lock = RLock()

    def register_execution(self, execution_id: str) -> None:
        _validate_identifier(execution_id, "execution_id")
        with self._release_lock:
            self._executions.add(execution_id)

    def submit(
        self,
        execution_id: str,
        consequence_id: str,
        consequence: ProposedConsequence,
    ) -> ContainedConsequence:
        _validate_identifier(execution_id, "execution_id")
        _validate_identifier(consequence_id, "consequence_id")
        with self._release_lock:
            self._require_registered_execution(execution_id)
            if type(consequence) is not ProposedConsequence:
                raise TypeError("consequence must be an exact ProposedConsequence")
            if consequence_id in self._consequences:
                raise ValueError("consequence_id already exists")
            _validate_consequence_material(consequence)

            contained = ContainedConsequence(
                consequence_id=consequence_id,
                origin_execution_id=execution_id,
                current_execution_id=execution_id,
                consequence_type=deepcopy(consequence.consequence_type),
                target=deepcopy(consequence.target),
                payload=deepcopy(consequence.payload),
            )
            detached = _copy_contained_consequence(contained)
            self._consequences[consequence_id] = contained
            self._release_states[consequence_id] = ReleaseState.CONTAINED
            return detached

    def read(
        self,
        execution_id: str,
        consequence_id: str,
    ) -> ContainedConsequence:
        _validate_identifier(execution_id, "execution_id")
        _validate_identifier(consequence_id, "consequence_id")
        with self._release_lock:
            self._require_registered_execution(execution_id)
            contained = self._consequences.get(consequence_id)
            if contained is None or contained.current_execution_id != execution_id:
                raise KeyError("consequence is not visible to execution")
            return _copy_contained_consequence(contained)

    def transfer(
        self,
        source_execution_id: str,
        target_execution_id: str,
        consequence_id: str,
    ) -> ContainedConsequence:
        _validate_identifier(source_execution_id, "source_execution_id")
        _validate_identifier(target_execution_id, "target_execution_id")
        _validate_identifier(consequence_id, "consequence_id")
        with self._release_lock:
            self._require_registered_execution(source_execution_id)
            self._require_registered_execution(target_execution_id)
            if source_execution_id == target_execution_id:
                raise ValueError("source and target executions must differ")
            if consequence_id not in self._consequences:
                raise KeyError("consequence is not visible to execution")
            if consequence_id in self._release_in_progress:
                raise ValueError("consequence release is in progress")
            if self._release_states.get(consequence_id) is not ReleaseState.CONTAINED:
                raise ValueError("consequence is no longer normally contained")

            contained = self._read_internal(source_execution_id, consequence_id)
            transferred = replace(
                contained,
                current_execution_id=target_execution_id,
            )
            self._consequences[consequence_id] = transferred
            return _copy_contained_consequence(transferred)

    def release_state(self, consequence_id: str) -> ReleaseState:
        _validate_identifier(consequence_id, "consequence_id")
        with self._release_lock:
            if consequence_id not in self._consequences:
                raise KeyError("consequence does not exist")
            return self._release_states[consequence_id]

    def _read_internal(
        self,
        execution_id: str,
        consequence_id: str,
    ) -> ContainedConsequence:
        contained = self._consequences.get(consequence_id)
        if contained is None or contained.current_execution_id != execution_id:
            raise KeyError("consequence is not visible to execution")
        return contained

    def _require_registered_execution(self, execution_id: str) -> None:
        if execution_id not in self._executions:
            raise KeyError("execution is not registered")


def _validate_identifier(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{field_name} must be a non-empty identifier")


def _is_exact_identifier(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value.strip() == value
    )


def _validate_consequence_material(consequence: ProposedConsequence) -> None:
    if type(consequence.consequence_type) is not str:
        raise TypeError("consequence_type must be an exact str")
    if type(consequence.target) is not str:
        raise TypeError("target must be an exact str")
    _validate_payload_value(consequence.payload, set())


def _validate_payload_value(value: Any, active_containers: set[int]) -> None:
    value_type = type(value)
    if value is None or value_type is bool or value_type is int:
        return
    if value_type is float:
        if not math.isfinite(value):
            raise ValueError("consequence payload float must be finite")
        return
    if value_type is str:
        return

    if value_type is list:
        _validate_container_path(value, active_containers)
        try:
            for item in value:
                _validate_payload_value(item, active_containers)
        finally:
            active_containers.remove(id(value))
        return

    if value_type is dict:
        _validate_container_path(value, active_containers)
        try:
            for key, item in value.items():
                if type(key) is not str:
                    raise TypeError("consequence payload dict keys must be exact str")
                _validate_payload_value(item, active_containers)
        finally:
            active_containers.remove(id(value))
        return

    raise TypeError("unsupported consequence payload value type")


def _validate_container_path(
    container: list[Any] | dict[str, Any],
    active_containers: set[int],
) -> None:
    container_id = id(container)
    if container_id in active_containers:
        raise ValueError("consequence payload contains a cycle")
    active_containers.add(container_id)


def _copy_contained_consequence(
    contained: ContainedConsequence,
) -> ContainedConsequence:
    return replace(
        contained,
        consequence_type=deepcopy(contained.consequence_type),
        target=deepcopy(contained.target),
        payload=deepcopy(contained.payload),
    )


class SyntheticAdmissionProvider:
    """Deterministic provider for synthetic admission results."""

    def __init__(
        self,
        result: str,
        admitted_consequence: ProposedConsequence | None = None,
    ) -> None:
        self.result = result
        self.admitted_consequence = admitted_consequence

    def admit(self, consequence: ProposedConsequence) -> AdmissionResult:
        if self.result == "ALLOW" and self.admitted_consequence is None:
            return AdmissionResult("ALLOW", consequence)
        return AdmissionResult(self.result, self.admitted_consequence)


class SimulatedActuator:
    """Low-level synthetic actuator with no external I/O."""

    def __init__(self) -> None:
        self.commits: list[ProposedConsequence] = []

    def commit(self, consequence: ProposedConsequence) -> str:
        if not isinstance(consequence, ProposedConsequence):
            raise TypeError("consequence must be a ProposedConsequence")
        self.commits.append(consequence)
        return "simulated_commit"


class ReceiptSink:
    """In-memory sink for committed and non-committed attempts."""

    def __init__(self) -> None:
        self.receipts: list[Receipt] = []

    def record(self, receipt: Receipt) -> Receipt:
        self.receipts.append(receipt)
        return receipt


def _same_value(left: Any, right: Any) -> bool:
    """Compare values without allowing bool/int coercion."""

    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return (
            left.keys() == right.keys()
            and all(_same_value(left[key], right[key]) for key in left)
        )
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(
            _same_value(a, b) for a, b in zip(left, right)
        )
    return left == right


def _same_consequence(
    left: ProposedConsequence, right: ProposedConsequence
) -> bool:
    return (
        _same_value(left.consequence_type, right.consequence_type)
        and _same_value(left.target, right.target)
        and _same_value(left.payload, right.payload)
    )


def same_material_consequence(
    declaration: ProposedConsequence, proposed: ProposedConsequence
) -> bool:
    """Return whether two consequences have identical modeled material fields."""

    return _same_consequence(declaration, proposed)


def _proposed_from_contained(
    contained: ContainedConsequence,
) -> ProposedConsequence:
    return ProposedConsequence(
        consequence_type=deepcopy(contained.consequence_type),
        target=deepcopy(contained.target),
        payload=deepcopy(contained.payload),
    )


def _default_release_clock() -> datetime:
    return datetime.now(timezone.utc)


class ReleaseBoundary:
    """Provider-neutral in-memory boundary for releasing one contained effect."""

    def __init__(
        self,
        store: ExecutionScopedConsequenceStore,
        authority_provider: AuthorityProvider,
        actuator: ReleaseActuator,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._authority_provider = authority_provider
        self._actuator = actuator
        self._clock = clock or _default_release_clock

    def release(self, execution_id: str, consequence_id: str) -> ReleaseResult:
        _validate_identifier(execution_id, "execution_id")
        _validate_identifier(consequence_id, "consequence_id")

        with self._store._release_lock:
            try:
                self._store._require_registered_execution(execution_id)
            except KeyError:
                return self._contained_result(consequence_id, "execution_not_registered")

            contained = self._store._consequences.get(consequence_id)
            if contained is None:
                return self._contained_result(consequence_id, "consequence_not_found")
            if consequence_id in self._store._release_in_progress:
                return self._contained_result(consequence_id, "release_in_progress")
            if self._store._release_states[consequence_id] is not ReleaseState.CONTAINED:
                return self._contained_result(
                    consequence_id,
                    "consequence_not_releasable",
                )
            if contained.current_execution_id != execution_id:
                return self._contained_result(consequence_id, "not_current_custodian")
            evaluation_snapshot = _copy_contained_consequence(contained)

        evaluated_at = self._clock()
        try:
            decision = self._authority_provider.evaluate(
                evaluation_snapshot,
                evaluated_at,
            )
        except Exception:
            return self._contained_result(consequence_id, "authority_unavailable")

        with self._store._release_lock:
            current = self._store._consequences.get(consequence_id)
            if current is None:
                return self._contained_result(consequence_id, "consequence_not_found")
            if self._store._release_states[consequence_id] is not ReleaseState.CONTAINED:
                return self._contained_result(consequence_id, "consequence_not_releasable")
            if current.current_execution_id != execution_id:
                return self._contained_result(
                    consequence_id,
                    "custody_changed_before_release",
                )

            now = self._clock()
            reason = self._validate_final_authority(
                decision,
                current,
                execution_id,
                now,
            )
            if reason is not None:
                return self._contained_result(
                    consequence_id,
                    reason,
                    decision_id=_decision_id_or_none(decision),
                )

            self._store._release_in_progress.add(consequence_id)
            try:
                actuator_input = _copy_contained_consequence(current)
                try:
                    raw_outcome = self._actuator.actuate(actuator_input)
                except Exception:
                    raw_outcome = ActuatorOutcome.UNCERTAIN

                outcome = _normalize_actuator_outcome(raw_outcome)
                if outcome is ActuatorOutcome.SUCCEEDED:
                    self._store._release_states[consequence_id] = ReleaseState.RELEASED
                    return ReleaseResult(
                        consequence_id,
                        ReleaseState.RELEASED,
                        "actuator_succeeded",
                        _decision_id_or_none(decision),
                        outcome,
                    )
                if outcome is ActuatorOutcome.DEFINITE_NOT_EXECUTED:
                    return ReleaseResult(
                        consequence_id,
                        ReleaseState.CONTAINED,
                        "actuator_definitely_not_executed",
                        _decision_id_or_none(decision),
                        outcome,
                    )

                self._store._release_states[consequence_id] = ReleaseState.UNCERTAIN
                return ReleaseResult(
                    consequence_id,
                    ReleaseState.UNCERTAIN,
                    "actuator_outcome_uncertain",
                    _decision_id_or_none(decision),
                    ActuatorOutcome.UNCERTAIN,
                )
            finally:
                self._store._release_in_progress.discard(consequence_id)

    def _validate_final_authority(
        self,
        decision: object,
        contained: ContainedConsequence,
        execution_id: str,
        now: datetime,
    ) -> str | None:
        if type(decision) is not AuthorityDecision:
            return "authority_invalid"
        if not _is_exact_identifier(decision.decision_id):
            return "authority_invalid"
        if not _is_exact_identifier(decision.consequence_id):
            return "authority_invalid"
        if not _is_exact_identifier(decision.execution_id):
            return "authority_invalid"
        if type(decision.verdict) is not str:
            return "authority_invalid"
        if decision.verdict not in {
            AuthorityVerdict.ALLOW.value,
            AuthorityVerdict.DENY.value,
        }:
            return "authority_invalid"
        if decision.verdict != AuthorityVerdict.ALLOW.value:
            return "authority_not_allowed"
        if decision.consequence_id != contained.consequence_id:
            return "authority_consequence_mismatch"
        if decision.execution_id != execution_id:
            return "authority_custodian_mismatch"
        if type(decision.bound_consequence) is not ProposedConsequence:
            return "authority_invalid"
        try:
            _validate_consequence_material(decision.bound_consequence)
        except (TypeError, ValueError):
            return "authority_invalid"
        if not _same_consequence(
            decision.bound_consequence,
            _proposed_from_contained(contained),
        ):
            return "authority_material_mismatch"
        if (
            type(decision.issued_at) is not datetime
            or type(decision.valid_until) is not datetime
        ):
            return "authority_invalid_timestamp"
        if type(now) is not datetime:
            return "clock_invalid"
        try:
            if decision.valid_until <= decision.issued_at:
                return "authority_invalid_timestamp"
            if now < decision.issued_at:
                return "authority_not_yet_valid"
            if now >= decision.valid_until:
                return "authority_expired"
        except TypeError:
            return "authority_invalid_timestamp"
        return None

    @staticmethod
    def _contained_result(
        consequence_id: str,
        reason: str,
        decision_id: str | None = None,
    ) -> ReleaseResult:
        return ReleaseResult(
            consequence_id,
            ReleaseState.CONTAINED,
            reason,
            decision_id,
        )


def _decision_id_or_none(decision: object) -> str | None:
    return decision.decision_id if isinstance(decision, AuthorityDecision) else None


def _normalize_actuator_outcome(value: object) -> ActuatorOutcome:
    if isinstance(value, ActuatorOutcome):
        return value
    if isinstance(value, str):
        try:
            return ActuatorOutcome(value)
        except ValueError:
            pass
    return ActuatorOutcome.UNCERTAIN


class SwampBoxBoundary:
    """The normal route from an admission result to the actuator."""

    def __init__(self, actuator: SimulatedActuator, sink: ReceiptSink) -> None:
        self._actuator = actuator
        self._sink = sink

    def commit(
        self,
        proposed: ProposedConsequence,
        admission: AdmissionResult | None,
    ) -> Receipt:
        if not isinstance(proposed, ProposedConsequence):
            return self._record(
                proposed, False, "not_committed_invalid_consequence"
            )

        if not isinstance(admission, AdmissionResult):
            return self._record(proposed, False, "not_committed_missing_admission")

        if admission.result not in ALLOWED_RESULTS:
            return self._record(proposed, False, "not_committed_invalid_admission")

        if admission.result != "ALLOW":
            return self._record(proposed, False, "not_committed_admission_not_allowed")

        if not isinstance(admission.admitted_consequence, ProposedConsequence):
            return self._record(proposed, False, "not_committed_invalid_admission")

        if not _same_consequence(proposed, admission.admitted_consequence):
            return self._record(proposed, False, "not_committed_consequence_mismatch")

        self._actuator.commit(proposed)
        return self._record(proposed, True, "committed")

    def _record(
        self,
        consequence: ProposedConsequence,
        committed: bool,
        outcome: str,
    ) -> Receipt:
        receipt = Receipt(committed, consequence, outcome)
        return self._sink.record(receipt)
