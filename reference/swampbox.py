"""Small in-memory consequence-boundary reference model."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any


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

    def register_execution(self, execution_id: str) -> None:
        _validate_identifier(execution_id, "execution_id")
        self._executions.add(execution_id)

    def submit(
        self,
        execution_id: str,
        consequence_id: str,
        consequence: ProposedConsequence,
    ) -> ContainedConsequence:
        _validate_identifier(execution_id, "execution_id")
        _validate_identifier(consequence_id, "consequence_id")
        self._require_registered_execution(execution_id)
        if not isinstance(consequence, ProposedConsequence):
            raise TypeError("consequence must be a ProposedConsequence")
        if consequence_id in self._consequences:
            raise ValueError("consequence_id already exists")

        contained = ContainedConsequence(
            consequence_id=consequence_id,
            origin_execution_id=execution_id,
            current_execution_id=execution_id,
            consequence_type=deepcopy(consequence.consequence_type),
            target=deepcopy(consequence.target),
            payload=deepcopy(consequence.payload),
        )
        self._consequences[consequence_id] = contained
        return _copy_contained_consequence(contained)

    def read(
        self,
        execution_id: str,
        consequence_id: str,
    ) -> ContainedConsequence:
        _validate_identifier(execution_id, "execution_id")
        _validate_identifier(consequence_id, "consequence_id")
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
        self._require_registered_execution(source_execution_id)
        self._require_registered_execution(target_execution_id)
        if source_execution_id == target_execution_id:
            raise ValueError("source and target executions must differ")

        contained = self._read_internal(source_execution_id, consequence_id)
        transferred = replace(
            contained,
            current_execution_id=target_execution_id,
        )
        self._consequences[consequence_id] = transferred
        return _copy_contained_consequence(transferred)

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
