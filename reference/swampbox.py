"""Small in-memory consequence-boundary reference model."""

from __future__ import annotations

from dataclasses import dataclass
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
