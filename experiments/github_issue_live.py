"""Explicit, opt-in live reference runner for one GitHub issue consequence.

This module is intentionally inert when imported.  It contains one narrow
read/create/read seam and has no authentication, credential, or generic
command-runner abstraction.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict
from typing import Any, Callable, Sequence

from reference.github_issue_effect import (
    GitHubIssueConsequence,
    GitHubIssueExperiment,
    GitHubIssueReceipt,
)
from reference.swampbox import SyntheticAdmissionProvider


_REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$"
)
_COMMAND_TIMEOUT_SECONDS = 60
_SAFE_ISSUE_URL_PATTERN = re.compile(
    r"^https://github\.com/"
    r"(?P<repository>[A-Za-z0-9][A-Za-z0-9_.-]*/"
    r"[A-Za-z0-9][A-Za-z0-9_.-]*)/issues/[1-9][0-9]*$"
)
_API_ISSUE_URL_PATTERN = re.compile(
    r"^https://api\.github\.com/repos/"
    r"(?P<repository>[A-Za-z0-9][A-Za-z0-9_.-]*/"
    r"[A-Za-z0-9][A-Za-z0-9_.-]*)/issues/[1-9][0-9]*$"
)
_REPOSITORY_URL_PATTERN = re.compile(
    r"^https://api\.github\.com/repos/"
    r"(?P<repository>[A-Za-z0-9][A-Za-z0-9_.-]*/"
    r"[A-Za-z0-9][A-Za-z0-9_.-]*)$"
)
_REPOSITORY_IDENTITY_PATTERNS = {
    "repository_url": _REPOSITORY_URL_PATTERN,
    "url": _API_ISSUE_URL_PATTERN,
    "html_url": _SAFE_ISSUE_URL_PATTERN,
}

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class LiveInputError(ValueError):
    """A local input/confirmation failure before any subprocess is started."""


class LiveCommandError(RuntimeError):
    """A sanitized command failure; raw subprocess diagnostics stay private."""


def _validate_repository(repository: str) -> str:
    if not isinstance(repository, str) or not _REPOSITORY_PATTERN.fullmatch(
        repository
    ):
        raise LiveInputError("repository must be OWNER/REPOSITORY")
    return repository


def _validate_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise LiveInputError(f"{field_name} must be a non-empty string")
    return value


def _read_arguments(repository: str) -> list[str]:
    return [
        "gh",
        "api",
        "--method",
        "GET",
        "--paginate",
        "--slurp",
        f"repos/{repository}/issues?state=all&per_page=100",
    ]


def _create_arguments(consequence: GitHubIssueConsequence) -> list[str]:
    return [
        "gh",
        "issue",
        "create",
        "--repo",
        consequence.repository,
        "--title",
        consequence.title,
        "--body",
        consequence.body,
    ]


def _run_fixed_gh(
    arguments: Sequence[str],
    command_runner: CommandRunner,
) -> str:
    try:
        completed = command_runner(
            list(arguments),
            shell=False,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise LiveCommandError("command_timeout") from exc
    except FileNotFoundError as exc:
        raise LiveCommandError("gh_unavailable") from exc
    except OSError as exc:
        raise LiveCommandError("command_unavailable") from exc

    if not isinstance(completed.returncode, int) or completed.returncode != 0:
        raise LiveCommandError("command_failed")
    if not isinstance(completed.stdout, str):
        raise LiveCommandError("command_output_invalid")
    return completed.stdout


def _safe_issue_reference(
    issue: dict[str, Any], repository: str, match_index: int
) -> str:
    html_url = issue.get("html_url")
    if "html_url" in issue:
        if not isinstance(html_url, str):
            raise LiveCommandError("issue_repository_identity_invalid")
        match = _SAFE_ISSUE_URL_PATTERN.fullmatch(html_url)
        if match is None or match.group("repository") != repository:
            raise LiveCommandError("issue_repository_identity_mismatch")
        return html_url

    number = issue.get("number")
    if isinstance(number, int) and not isinstance(number, bool) and number > 0:
        return f"{repository}#issue-{number}"

    return f"{repository}#exact-match-{match_index}"


def _validate_repository_identity(
    issue: dict[str, Any], repository: str
) -> None:
    for field_name, pattern in _REPOSITORY_IDENTITY_PATTERNS.items():
        if field_name not in issue:
            continue

        value = issue[field_name]
        if not isinstance(value, str):
            raise LiveCommandError("issue_repository_identity_invalid")

        match = pattern.fullmatch(value)
        if match is None:
            raise LiveCommandError("issue_repository_identity_invalid")
        if match.group("repository") != repository:
            raise LiveCommandError("issue_repository_identity_mismatch")


def _parse_slurped_issue_pages(
    stdout: str,
    consequence: GitHubIssueConsequence,
) -> tuple[str, ...]:
    try:
        pages = json.loads(stdout)
    except (TypeError, ValueError) as exc:
        raise LiveCommandError("json_invalid") from exc

    if not isinstance(pages, list) or not pages:
        raise LiveCommandError("page_collection_invalid")

    issues: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, list):
            raise LiveCommandError("page_invalid")
        for issue in page:
            if not isinstance(issue, dict):
                raise LiveCommandError("issue_invalid")
            issues.append(issue)

    matches: list[str] = []
    for issue in issues:
        # The repository /issues endpoint can include pull requests.
        if "pull_request" in issue:
            continue

        _validate_repository_identity(issue, consequence.repository)

        title = issue.get("title")
        if not isinstance(title, str):
            raise LiveCommandError("issue_title_invalid")

        if "body" not in issue:
            raise LiveCommandError("issue_body_missing")
        body = issue["body"]
        if body is not None and not isinstance(body, str):
            raise LiveCommandError("issue_body_invalid")

        # A known JSON null is a valid external value, but it is not equal to
        # any concrete string consequence body, including the empty string.
        if body is None:
            continue

        if title == consequence.title and body == consequence.body:
            matches.append(
                _safe_issue_reference(issue, consequence.repository, len(matches) + 1)
            )

    return tuple(matches)


def _build_experiment(
    command_runner: CommandRunner,
) -> Callable[[GitHubIssueConsequence], GitHubIssueReceipt]:
    def run(consequence: GitHubIssueConsequence) -> GitHubIssueReceipt:
        def lookup(current: GitHubIssueConsequence) -> tuple[str, ...]:
            stdout = _run_fixed_gh(
                _read_arguments(current.repository), command_runner
            )
            return _parse_slurped_issue_pages(stdout, current)

        def create(current: GitHubIssueConsequence) -> object:
            # The Phase 1 experiment catches this failure and performs one
            # fresh read without retrying the create operation.
            return _run_fixed_gh(_create_arguments(current), command_runner)

        admission = SyntheticAdmissionProvider(
            "ALLOW", consequence.as_proposed_consequence()
        ).admit(consequence.as_proposed_consequence())
        return GitHubIssueExperiment(lookup, create).run(consequence, admission)

    return run


def run_live_experiment(
    repository: str,
    title: str,
    body: str,
    *,
    live: bool = False,
    confirm_disposable_target: bool = False,
    command_runner: CommandRunner = subprocess.run,
) -> GitHubIssueReceipt:
    """Run the explicit reference flow using an injected command seam.

    The default command seam is used only after both deliberate confirmations
    and all local consequence validation succeed. Tests inject a fake seam;
    importing this module performs no command.
    """

    if not live or not confirm_disposable_target:
        raise LiveInputError("explicit live and disposable-target confirmations required")

    final_repository = _validate_repository(repository)
    final_title = _validate_text(title, "title")
    final_body = _validate_text(body, "body")
    consequence = GitHubIssueConsequence(
        repository=final_repository,
        title=final_title,
        body=final_body,
    )
    return _build_experiment(command_runner)(consequence)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explicit one-shot reference GitHub issue experiment"
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-disposable-target", action="store_true")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    if not arguments.live or not arguments.confirm_disposable_target:
        parser.error(
            "--live and --confirm-disposable-target are required for live execution"
        )

    try:
        receipt = run_live_experiment(
            arguments.repo,
            arguments.title,
            arguments.body,
            live=True,
            confirm_disposable_target=True,
        )
    except LiveInputError as exc:
        print(f"LIVE_RUNNER_INPUT_ERROR={exc}", file=sys.stderr)
        return 2

    print(json.dumps(asdict(receipt), sort_keys=True))
    return 0 if receipt.outcome == "verified_success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
