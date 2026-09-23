"""Offline-safe reference seam for one controlled GitHub issue consequence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from experiments.legacy_synthetic import AdmissionResult
from reference.swampbox import ProposedConsequence


CREATE_GITHUB_ISSUE = "CREATE_GITHUB_ISSUE"


@dataclass(frozen=True)
class GitHubIssueConsequence:
    """The complete public consequence: repository, title, and body."""

    repository: str
    title: str
    body: str

    def as_proposed_consequence(self) -> ProposedConsequence:
        """Bridge to the existing public admission value without new fields."""

        return ProposedConsequence(
            consequence_type=CREATE_GITHUB_ISSUE,
            target=self.repository,
            payload=(self.title, self.body),
        )


@dataclass(frozen=True)
class GitHubIssueReceipt:
    """Minimal observation for this reference experiment only."""

    consequence: GitHubIssueConsequence
    admission_result: str
    baseline_match_count: int | None
    attempted: bool
    post_create_match_count: int | None
    outcome: str
    issue_reference: str | None = None


ExactIssueLookup = Callable[[GitHubIssueConsequence], Iterable[str]]
CreateIssue = Callable[[GitHubIssueConsequence], object]


class GitHubIssueExperiment:
    """Coordinate the offline-testable public GitHub consequence boundary.

    The callables are deliberately narrow seams. This module does not know how
    credentials, GitHub clients, shells, or network transports work.
    """

    def __init__(
        self,
        exact_issue_lookup: ExactIssueLookup,
        create_issue: CreateIssue,
    ) -> None:
        self._exact_issue_lookup = exact_issue_lookup
        self._create_issue = create_issue

    def run(
        self,
        consequence: GitHubIssueConsequence,
        admission: AdmissionResult | None,
    ) -> GitHubIssueReceipt:
        baseline = self._lookup(consequence)
        if baseline is None:
            return self._receipt(
                consequence,
                admission,
                baseline_match_count=None,
                attempted=False,
                post_create_match_count=None,
                outcome="external_result_uncertain",
            )

        if baseline:
            return self._receipt(
                consequence,
                admission,
                baseline_match_count=len(baseline),
                attempted=False,
                post_create_match_count=None,
                outcome="not_attempted_existing_exact_issue",
            )

        proposed = consequence.as_proposed_consequence()
        if not isinstance(admission, AdmissionResult):
            return self._receipt(
                consequence,
                admission,
                baseline_match_count=0,
                attempted=False,
                post_create_match_count=None,
                outcome="not_attempted_invalid_admission",
            )

        if admission.result != "ALLOW":
            return self._receipt(
                consequence,
                admission,
                baseline_match_count=0,
                attempted=False,
                post_create_match_count=None,
                outcome="not_attempted_admission_not_allowed",
            )

        if admission.admitted_consequence != proposed:
            return self._receipt(
                consequence,
                admission,
                baseline_match_count=0,
                attempted=False,
                post_create_match_count=None,
                outcome="not_attempted_consequence_mismatch",
            )

        try:
            self._create_issue(consequence)
        except Exception:
            # The external boundary may have been crossed before the caller
            # observed an exception. Verify once and never retry here.
            pass

        post_create = self._lookup(consequence)
        if post_create is None:
            return self._receipt(
                consequence,
                admission,
                baseline_match_count=0,
                attempted=True,
                post_create_match_count=None,
                outcome="external_result_uncertain",
            )

        if len(post_create) == 1:
            return self._receipt(
                consequence,
                admission,
                baseline_match_count=0,
                attempted=True,
                post_create_match_count=1,
                outcome="verified_success",
                issue_reference=post_create[0],
            )

        return self._receipt(
            consequence,
            admission,
            baseline_match_count=0,
            attempted=True,
            post_create_match_count=len(post_create),
            outcome="external_result_unverified",
        )

    def _lookup(self, consequence: GitHubIssueConsequence) -> tuple[str, ...] | None:
        try:
            return tuple(self._exact_issue_lookup(consequence))
        except Exception:
            return None

    @staticmethod
    def _receipt(
        consequence: GitHubIssueConsequence,
        admission: AdmissionResult | None,
        *,
        baseline_match_count: int | None,
        attempted: bool,
        post_create_match_count: int | None,
        outcome: str,
        issue_reference: str | None = None,
    ) -> GitHubIssueReceipt:
        admission_result = (
            admission.result if isinstance(admission, AdmissionResult) else "INVALID"
        )
        return GitHubIssueReceipt(
            consequence=consequence,
            admission_result=admission_result,
            baseline_match_count=baseline_match_count,
            attempted=attempted,
            post_create_match_count=post_create_match_count,
            outcome=outcome,
            issue_reference=issue_reference,
        )
