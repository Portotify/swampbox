"""Noncanonical synthetic admission and actuation experiment surface.

This module preserves the earlier synthetic experiment contract.  It is
intentionally separate from the canonical execution-scoped containment and
provider-neutral release path in ``reference.swampbox``.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from reference.swampbox import ProposedConsequence, same_material_consequence


ALLOWED_RESULTS = frozenset({"ALLOW", "HOLD", "DENY"})


@dataclass(frozen=True)
class AdmissionResult:
    result: str
    admitted_consequence: ProposedConsequence | None = None


@dataclass(frozen=True)
class Receipt:
    committed: bool
    consequence: ProposedConsequence
    outcome: str


class SyntheticAdmissionProvider:
    """Deterministic provider for the legacy synthetic experiment only."""

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
    """Low-level synthetic actuator for the legacy experiment only."""

    def __init__(self) -> None:
        self.commits: list[ProposedConsequence] = []

    def commit(self, consequence: ProposedConsequence) -> str:
        if not isinstance(consequence, ProposedConsequence):
            raise TypeError("consequence must be a ProposedConsequence")
        self.commits.append(consequence)
        return "simulated_commit"


class ReceiptSink:
    """In-memory sink for legacy synthetic commit attempts."""

    def __init__(self) -> None:
        self.receipts: list[Receipt] = []

    def record(self, receipt: Receipt) -> Receipt:
        self.receipts.append(receipt)
        return receipt


class SwampBoxBoundary:
    """Legacy admission-to-actuator boundary; not the canonical release path."""

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

        if not same_material_consequence(proposed, admission.admitted_consequence):
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
