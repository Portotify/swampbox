"""Offline behavioral tests for the sanitized GitHub effect seam."""

from __future__ import annotations

import importlib
import json
import subprocess
import unittest
from unittest.mock import patch

from experiments import github_issue_live as live
from reference.github_issue_effect import (
    GitHubIssueConsequence,
    GitHubIssueExperiment,
)
from reference.swampbox import AdmissionResult, SyntheticAdmissionProvider


class ScriptedLookup:
    def __init__(self, *responses: object) -> None:
        self._responses = list(responses)
        self.calls: list[GitHubIssueConsequence] = []

    def __call__(self, consequence: GitHubIssueConsequence) -> tuple[str, ...]:
        self.calls.append(consequence)
        if not self._responses:
            raise AssertionError("unexpected lookup")
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return tuple(response)  # type: ignore[arg-type]


class CreateSpy:
    def __init__(self, return_value: object = "issue-1") -> None:
        self.return_value = return_value
        self.calls: list[GitHubIssueConsequence] = []

    def __call__(self, consequence: GitHubIssueConsequence) -> object:
        self.calls.append(consequence)
        return self.return_value


class GitHubIssueExperimentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.consequence = GitHubIssueConsequence(
            repository="example/disposable",
            title="[experiment] one issue",
            body="controlled consequence",
        )

    def _allow(
        self, admitted: GitHubIssueConsequence | None = None
    ) -> AdmissionResult:
        proposed = (admitted or self.consequence).as_proposed_consequence()
        return SyntheticAdmissionProvider("ALLOW", proposed).admit(
            self.consequence.as_proposed_consequence()
        )

    def _deny(self) -> AdmissionResult:
        return SyntheticAdmissionProvider("DENY").admit(
            self.consequence.as_proposed_consequence()
        )

    def _run(
        self,
        lookup: ScriptedLookup,
        create: CreateSpy,
        admission: AdmissionResult | None,
    ):
        return GitHubIssueExperiment(lookup, create).run(
            self.consequence, admission
        )

    def test_denied_admission_does_not_create(self) -> None:
        lookup = ScriptedLookup(())
        create = CreateSpy()

        receipt = self._run(lookup, create, self._deny())

        self.assertEqual(receipt.outcome, "not_attempted_admission_not_allowed")
        self.assertEqual(len(create.calls), 0)

    def test_repository_mismatch_does_not_create(self) -> None:
        lookup = ScriptedLookup(())
        create = CreateSpy()
        admitted = GitHubIssueConsequence(
            "example/other", self.consequence.title, self.consequence.body
        )

        receipt = self._run(lookup, create, self._allow(admitted))

        self.assertEqual(receipt.outcome, "not_attempted_consequence_mismatch")
        self.assertEqual(len(create.calls), 0)

    def test_title_mismatch_does_not_create(self) -> None:
        lookup = ScriptedLookup(())
        create = CreateSpy()
        admitted = GitHubIssueConsequence(
            self.consequence.repository, "different title", self.consequence.body
        )

        receipt = self._run(lookup, create, self._allow(admitted))

        self.assertEqual(receipt.outcome, "not_attempted_consequence_mismatch")
        self.assertEqual(len(create.calls), 0)

    def test_body_mismatch_does_not_create(self) -> None:
        lookup = ScriptedLookup(())
        create = CreateSpy()
        admitted = GitHubIssueConsequence(
            self.consequence.repository, self.consequence.title, "different body"
        )

        receipt = self._run(lookup, create, self._allow(admitted))

        self.assertEqual(receipt.outcome, "not_attempted_consequence_mismatch")
        self.assertEqual(len(create.calls), 0)

    def test_pre_existing_exact_issue_prevents_create(self) -> None:
        lookup = ScriptedLookup(("issue-old",))
        create = CreateSpy()

        receipt = self._run(lookup, create, self._allow())

        self.assertEqual(receipt.outcome, "not_attempted_existing_exact_issue")
        self.assertEqual(receipt.baseline_match_count, 1)
        self.assertEqual(len(create.calls), 0)

    def test_allowed_exact_consequence_creates_once_and_verifies(self) -> None:
        lookup = ScriptedLookup((), ("issue-1",))
        create = CreateSpy()

        receipt = self._run(lookup, create, self._allow())

        self.assertEqual(receipt.outcome, "verified_success")
        self.assertTrue(receipt.attempted)
        self.assertEqual(receipt.baseline_match_count, 0)
        self.assertEqual(receipt.post_create_match_count, 1)
        self.assertEqual(receipt.issue_reference, "issue-1")
        self.assertEqual(len(create.calls), 1)

    def test_post_create_zero_matches_is_not_success_and_does_not_retry(self) -> None:
        lookup = ScriptedLookup((), ())
        create = CreateSpy()

        receipt = self._run(lookup, create, self._allow())

        self.assertEqual(receipt.outcome, "external_result_unverified")
        self.assertFalse(receipt.issue_reference)
        self.assertEqual(receipt.post_create_match_count, 0)
        self.assertEqual(len(create.calls), 1)

    def test_post_create_multiple_matches_is_not_success(self) -> None:
        lookup = ScriptedLookup((), ("issue-1", "issue-2"))
        create = CreateSpy()

        receipt = self._run(lookup, create, self._allow())

        self.assertEqual(receipt.outcome, "external_result_unverified")
        self.assertEqual(receipt.post_create_match_count, 2)
        self.assertEqual(len(create.calls), 1)

    def test_ambiguous_lookup_is_uncertain_without_retry(self) -> None:
        lookup = ScriptedLookup((), RuntimeError("external state unavailable"))
        create = CreateSpy()

        receipt = self._run(lookup, create, self._allow())

        self.assertEqual(receipt.outcome, "external_result_uncertain")
        self.assertEqual(len(create.calls), 1)

    def test_create_response_alone_is_insufficient(self) -> None:
        lookup = ScriptedLookup((), ())
        create = CreateSpy(return_value="issue-from-create-response")

        receipt = self._run(lookup, create, self._allow())

        self.assertEqual(receipt.outcome, "external_result_unverified")
        self.assertIsNone(receipt.issue_reference)
        self.assertEqual(len(create.calls), 1)

    def test_lookup_create_lookup_order_is_observable(self) -> None:
        events: list[str] = []

        def lookup(consequence: GitHubIssueConsequence):
            events.append("lookup")
            return () if events == ["lookup"] else ("issue-1",)

        def create(consequence: GitHubIssueConsequence):
            events.append("create")
            return "ignored-create-response"

        receipt = GitHubIssueExperiment(lookup, create).run(
            self.consequence, self._allow()
        )

        self.assertEqual(receipt.outcome, "verified_success")
        self.assertEqual(events, ["lookup", "create", "lookup"])

    def test_invalid_admission_does_not_create(self) -> None:
        lookup = ScriptedLookup(())
        create = CreateSpy()

        receipt = self._run(lookup, create, None)

        self.assertEqual(receipt.outcome, "not_attempted_invalid_admission")
        self.assertEqual(len(create.calls), 0)

    def test_consequence_and_receipt_have_no_credential_fields(self) -> None:
        lookup = ScriptedLookup((), ("issue-1",))
        create = CreateSpy()

        receipt = self._run(lookup, create, self._allow())
        field_names = set(receipt.__dataclass_fields__) | set(
            receipt.consequence.__dataclass_fields__
        )

        self.assertFalse(
            field_names & {"token", "credential", "session", "account", "keyring"}
        )

    def test_create_exception_still_never_retries(self) -> None:
        lookup = ScriptedLookup((), ())
        calls: list[GitHubIssueConsequence] = []

        def create(consequence: GitHubIssueConsequence):
            calls.append(consequence)
            raise RuntimeError("attempt boundary result is unclear")

        receipt = GitHubIssueExperiment(lookup, create).run(
            self.consequence, self._allow()
        )

        self.assertEqual(receipt.outcome, "external_result_unverified")
        self.assertEqual(len(calls), 1)


class FakeCommandRunner:
    def __init__(self, *responses: object) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, arguments: list[str], **kwargs: object):
        self.calls.append((arguments, kwargs))
        if not self._responses:
            raise AssertionError("unexpected gh command")
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["gh"], returncode=returncode, stdout=stdout, stderr="safe-test-error"
    )


def issue(
    title: str,
    body: object,
    *,
    number: int = 1,
    pull_request: bool = False,
    repository: str = "example/disposable",
    repository_url: str | None = None,
    html_url: str | None = None,
    include_repository_metadata: bool = True,
) -> dict[str, object]:
    value: dict[str, object] = {
        "title": title,
        "body": body,
        "number": number,
    }
    if include_repository_metadata:
        value["repository_url"] = repository_url or (
            f"https://api.github.com/repos/{repository}"
        )
        value["html_url"] = html_url or (
            f"https://github.com/{repository}/issues/{number}"
        )
    if pull_request:
        value["pull_request"] = {"url": "https://api.github.com/pulls/1"}
    return value


def slurped(*pages: list[dict[str, object]]) -> str:
    return json.dumps(list(pages))


class LiveRunnerTests(unittest.TestCase):
    repository = "example/disposable"
    title = "[experiment] one issue"
    body = "controlled consequence"

    def _run(
        self,
        runner: FakeCommandRunner,
        *,
        repository: str | None = None,
        title: str | None = None,
        body: str | None = None,
    ):
        return live.run_live_experiment(
            self.repository if repository is None else repository,
            self.title if title is None else title,
            self.body if body is None else body,
            live=True,
            confirm_disposable_target=True,
            command_runner=runner,
        )

    def test_import_is_side_effect_free(self) -> None:
        with patch.object(live.subprocess, "run") as run:
            importlib.reload(live)
        run.assert_not_called()

    def test_missing_live_confirmation_prevents_subprocess(self) -> None:
        runner = FakeCommandRunner()

        with self.assertRaises(live.LiveInputError):
            live.run_live_experiment(
                self.repository,
                self.title,
                self.body,
                command_runner=runner,
            )

        self.assertEqual(runner.calls, [])

    def test_malformed_repository_prevents_subprocess(self) -> None:
        runner = FakeCommandRunner()

        with self.assertRaises(live.LiveInputError):
            self._run(runner, repository="-bad/repository")

        self.assertEqual(runner.calls, [])

    def test_baseline_timeout_prevents_create(self) -> None:
        runner = FakeCommandRunner(
            subprocess.TimeoutExpired(cmd=["gh"], timeout=60)
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "external_result_uncertain")
        self.assertEqual(len(runner.calls), 1)

    def test_baseline_nonzero_exit_prevents_create(self) -> None:
        runner = FakeCommandRunner(completed("", returncode=1))

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "external_result_uncertain")
        self.assertEqual(len(runner.calls), 1)

    def test_baseline_malformed_json_prevents_create(self) -> None:
        runner = FakeCommandRunner(completed("not-json"))

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "external_result_uncertain")
        self.assertEqual(len(runner.calls), 1)

    def test_empty_page_collection_is_not_zero_baseline(self) -> None:
        runner = FakeCommandRunner(completed("[]"))

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "external_result_uncertain")
        self.assertEqual(receipt.baseline_match_count, None)
        self.assertEqual(len(runner.calls), 1)

    def test_baseline_missing_body_schema_prevents_create(self) -> None:
        runner = FakeCommandRunner(
            completed(json.dumps([[{"title": self.title}]]))
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "external_result_uncertain")
        self.assertEqual(len(runner.calls), 1)

    def test_baseline_invalid_body_type_prevents_create(self) -> None:
        runner = FakeCommandRunner(
            completed(slurped([issue(self.title, {"not": "a string"})]))
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "external_result_uncertain")
        self.assertEqual(len(runner.calls), 1)

    def test_matching_repository_identity_metadata_is_accepted(self) -> None:
        runner = FakeCommandRunner(
            completed(slurped([issue(self.title, self.body)]))
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "not_attempted_existing_exact_issue")
        self.assertEqual(receipt.baseline_match_count, 1)
        self.assertEqual(len(runner.calls), 1)

    def test_contradictory_repository_identity_metadata_fails_closed(self) -> None:
        runner = FakeCommandRunner(
            completed(
                slurped(
                    [
                        issue(
                            self.title,
                            self.body,
                            repository_url="https://api.github.com/repos/example/other",
                        )
                    ]
                )
            )
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "external_result_uncertain")
        self.assertIsNone(receipt.baseline_match_count)
        self.assertEqual(len(runner.calls), 1)

    def test_contradictory_safe_issue_reference_fails_closed(self) -> None:
        runner = FakeCommandRunner(
            completed(
                slurped(
                    [
                        issue(
                            self.title,
                            self.body,
                            html_url="https://github.com/example/other/issues/1",
                        )
                    ]
                )
            )
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "external_result_uncertain")
        self.assertIsNone(receipt.baseline_match_count)
        self.assertEqual(len(runner.calls), 1)

    def test_absent_repository_identity_metadata_remains_allowed(self) -> None:
        runner = FakeCommandRunner(
            completed(
                slurped(
                    [
                        issue(
                            self.title,
                            self.body,
                            include_repository_metadata=False,
                        )
                    ]
                )
            )
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "not_attempted_existing_exact_issue")
        self.assertEqual(receipt.baseline_match_count, 1)
        self.assertEqual(len(runner.calls), 1)

    def test_null_body_is_known_non_match_not_empty_string(self) -> None:
        consequence = GitHubIssueConsequence(
            repository=self.repository,
            title=self.title,
            body="",
        )

        matches = live._parse_slurped_issue_pages(
            slurped([issue(self.title, None)]), consequence
        )

        self.assertEqual(matches, ())

    def test_pull_request_is_excluded_from_exact_match(self) -> None:
        runner = FakeCommandRunner(
            completed(slurped([issue(self.title, self.body, pull_request=True)])),
            completed(slurped([])),
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.baseline_match_count, 0)
        self.assertEqual(len(runner.calls), 3)

    def test_state_all_is_present_in_read_command(self) -> None:
        runner = FakeCommandRunner(
            completed(slurped([])), completed(slurped([]))
        )

        self._run(runner)

        read_args = runner.calls[0][0]
        self.assertEqual(read_args[:6], [
            "gh", "api", "--method", "GET", "--paginate", "--slurp"
        ])
        self.assertEqual(
            read_args[6],
            "repos/example/disposable/issues?state=all&per_page=100",
        )

    def test_later_slurped_page_exact_match_prevents_create(self) -> None:
        runner = FakeCommandRunner(
            completed(
                slurped(
                    [issue("other title", "other body")],
                    [issue(self.title, self.body)],
                )
            )
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "not_attempted_existing_exact_issue")
        self.assertEqual(receipt.baseline_match_count, 1)
        self.assertEqual(len(runner.calls), 1)

    def test_invalid_slurp_page_shape_fails_closed(self) -> None:
        runner = FakeCommandRunner(completed(json.dumps([{"title": self.title}])))

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "external_result_uncertain")
        self.assertEqual(len(runner.calls), 1)

    def test_read_and_create_vectors_are_narrow_and_shell_free(self) -> None:
        runner = FakeCommandRunner(
            completed(slurped([])),
            completed("https://github.com/example/disposable/issues/1"),
            completed(slurped([issue(self.title, self.body)])),
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "verified_success")
        read_args, read_kwargs = runner.calls[0]
        create_args, create_kwargs = runner.calls[1]
        self.assertEqual(read_args[0], "gh")
        self.assertEqual(read_args[1], "api")
        self.assertEqual(create_args, [
            "gh", "issue", "create", "--repo", self.repository,
            "--title", self.title, "--body", self.body,
        ])
        self.assertFalse(read_kwargs["shell"])
        self.assertFalse(create_kwargs["shell"])

    def test_create_timeout_is_attempted_once_and_post_read_can_verify(self) -> None:
        runner = FakeCommandRunner(
            completed(slurped([])),
            subprocess.TimeoutExpired(cmd=["gh"], timeout=60),
            completed(slurped([issue(self.title, self.body)])),
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "verified_success")
        self.assertEqual(sum(call[0][1:3] == ["issue", "create"] for call in runner.calls), 1)
        self.assertEqual(len(runner.calls), 3)

    def test_create_nonzero_is_not_retried(self) -> None:
        runner = FakeCommandRunner(
            completed(slurped([])),
            completed("", returncode=1),
            completed(slurped([])),
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "external_result_unverified")
        self.assertEqual(len(runner.calls), 3)

    def test_create_output_alone_is_insufficient(self) -> None:
        runner = FakeCommandRunner(
            completed(slurped([])),
            completed("https://github.com/example/disposable/issues/1"),
            completed(slurped([])),
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "external_result_unverified")
        self.assertEqual(len(runner.calls), 3)

    def test_post_read_failure_has_no_success_and_no_retry(self) -> None:
        runner = FakeCommandRunner(
            completed(slurped([])),
            completed("created"),
            subprocess.TimeoutExpired(cmd=["gh"], timeout=60),
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "external_result_uncertain")
        self.assertEqual(len(runner.calls), 3)

    def test_post_zero_has_no_success(self) -> None:
        runner = FakeCommandRunner(
            completed(slurped([])), completed("created"), completed(slurped([]))
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "external_result_unverified")
        self.assertEqual(len(runner.calls), 3)

    def test_post_multiple_has_no_success(self) -> None:
        runner = FakeCommandRunner(
            completed(slurped([])),
            completed("created"),
            completed(slurped([issue(self.title, self.body, number=1), issue(self.title, self.body, number=2)])),
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "external_result_unverified")
        self.assertEqual(receipt.post_create_match_count, 2)
        self.assertEqual(len(runner.calls), 3)

    def test_post_null_body_does_not_verify_string_consequence(self) -> None:
        runner = FakeCommandRunner(
            completed(slurped([])),
            completed("created"),
            completed(slurped([issue(self.title, None)])),
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "external_result_unverified")
        self.assertEqual(receipt.post_create_match_count, 0)

    def test_post_invalid_schema_has_no_success_or_retry(self) -> None:
        runner = FakeCommandRunner(
            completed(slurped([])),
            completed("created"),
            completed(json.dumps([[{"title": self.title}] ])),
        )

        receipt = self._run(runner)

        self.assertEqual(receipt.outcome, "external_result_uncertain")
        self.assertEqual(len(runner.calls), 3)

    def test_credentials_are_absent_from_live_receipt(self) -> None:
        runner = FakeCommandRunner(
            completed(slurped([])),
            completed("created"),
            completed(slurped([issue(self.title, self.body)])),
        )

        receipt = self._run(runner)
        serialized = json.dumps(receipt.__dict__, default=str)

        for forbidden in ("token", "credential", "session", "keyring", "account"):
            self.assertNotIn(forbidden, serialized.lower())


if __name__ == "__main__":
    unittest.main()
