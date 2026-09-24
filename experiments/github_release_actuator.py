"""Experiment-only GitHub issue actuator for the canonical release boundary.

This is Step 13B experiment harness code.  It is not a canonical SwampBox
component and it does not change the actuator protocol: it only implements the
existing ``actuate(consequence) -> ActuatorOutcome`` shape.

Effect-finality contract (frozen in Step 13B-0):

SUCCEEDED
    The ``gh api`` POST was spawned, exited 0, and stdout is one JSON object
    whose ``number`` is a positive ``int`` (not ``bool``) and whose
    ``html_url`` is exactly
    ``https://github.com/{owner}/{repo}/issues/{number}`` for the configured
    repository.

DEFINITE_NOT_EXECUTED
    Only before the spawn boundary, when the adapter itself establishes that no
    external attempt was made (invalid consequence, target mismatch, ``gh``
    not resolvable).  Never inferred from a ``gh`` exit code.

UNCERTAIN
    Every outcome after the spawn boundary that lacks SUCCEEDED evidence.

The adapter reports effect finality only.  It does not decide authority,
custody, or lineage, does not read GitHub back, and never retries.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Callable

from reference.swampbox import ActuatorOutcome


CREATE_GITHUB_ISSUE = "CREATE_GITHUB_ISSUE"
GITHUB_HOSTNAME = "github.com"
DEFAULT_TIMEOUT_SECONDS = 60

_REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$"
)
_PAYLOAD_KEYS = frozenset({"title", "body"})

Runner = Callable[..., Any]
ExecutableResolver = Callable[[str], "str | None"]


class GitHubReleaseActuator:
    """Create one GitHub issue in one configured disposable repository."""

    def __init__(
        self,
        target_repository: str,
        *,
        disposable_target_confirmed: bool = False,
        runner: Runner = subprocess.run,
        resolve_executable: ExecutableResolver = shutil.which,
        timeout_seconds: int | float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if disposable_target_confirmed is not True:
            raise ValueError("explicit disposable target confirmation required")
        if type(target_repository) is not str or not _REPOSITORY_PATTERN.fullmatch(
            target_repository
        ):
            raise ValueError("target_repository must be exactly OWNER/REPOSITORY")
        if (
            type(timeout_seconds) not in (int, float)
            or not timeout_seconds > 0
        ):
            raise ValueError("timeout_seconds must be a positive number")

        self._target_repository = target_repository
        self._runner = runner
        self._resolve_executable = resolve_executable
        self._timeout_seconds = timeout_seconds

    def actuate(self, consequence: Any) -> ActuatorOutcome:
        spawn_boundary_crossed = False
        try:
            # Pre-spawn phase: nothing below may contact GitHub.
            argv = self._prepare_argv(consequence)
            if argv is None:
                return ActuatorOutcome.DEFINITE_NOT_EXECUTED

            # Spawn boundary.  Set immediately before the runner expression is
            # evaluated; after this point no path may return
            # DEFINITE_NOT_EXECUTED, whatever the exit status or exception.
            spawn_boundary_crossed = True
            completed = self._runner(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self._timeout_seconds,
                check=False,
            )
            return self._classify(completed)
        except Exception:
            # BaseException is deliberately not caught: control-flow signals
            # propagate and ReleaseBoundary marks the consequence UNCERTAIN.
            if spawn_boundary_crossed:
                return ActuatorOutcome.UNCERTAIN
            return ActuatorOutcome.DEFINITE_NOT_EXECUTED

    def _prepare_argv(self, consequence: Any) -> list[str] | None:
        fields = self._issue_fields(consequence)
        if fields is None:
            return None
        title, body = fields

        executable = self._resolve_gh()
        if executable is None:
            return None

        return [
            executable,
            "api",
            f"repos/{self._target_repository}/issues",
            "--method",
            "POST",
            "--hostname",
            GITHUB_HOSTNAME,
            "-f",
            f"title={title}",
            "-f",
            f"body={body}",
        ]

    def _issue_fields(self, consequence: Any) -> tuple[str, str] | None:
        if type(consequence.consequence_type) is not str:
            return None
        if consequence.consequence_type != CREATE_GITHUB_ISSUE:
            return None
        if type(consequence.target) is not str:
            return None
        if consequence.target != self._target_repository:
            return None

        payload = consequence.payload
        if type(payload) is not dict or set(payload) != _PAYLOAD_KEYS:
            return None

        title = payload["title"]
        body = payload["body"]
        for value in (title, body):
            if type(value) is not str or value.strip() == "" or "\x00" in value:
                return None
        return title, body

    def _resolve_gh(self) -> str | None:
        found = self._resolve_executable("gh")
        if type(found) is not str or found == "":
            return None
        resolved = os.path.abspath(found)
        # A .cmd/.bat shim would be parsed by cmd.exe, so title/body would no
        # longer be pure argv data.  Only a native executable is acceptable.
        if sys.platform == "win32" and not resolved.lower().endswith(".exe"):
            return None
        return resolved

    def _classify(self, completed: Any) -> ActuatorOutcome:
        returncode = completed.returncode
        stdout = completed.stdout
        if type(returncode) is not int or returncode != 0:
            return ActuatorOutcome.UNCERTAIN
        if type(stdout) is not str:
            return ActuatorOutcome.UNCERTAIN

        payload = json.loads(stdout)
        if type(payload) is not dict:
            return ActuatorOutcome.UNCERTAIN

        number = payload.get("number")
        if type(number) is not int or number <= 0:
            return ActuatorOutcome.UNCERTAIN

        html_url = payload.get("html_url")
        expected_url = (
            f"https://{GITHUB_HOSTNAME}/{self._target_repository}/issues/{number}"
        )
        if type(html_url) is not str or html_url != expected_url:
            return ActuatorOutcome.UNCERTAIN

        return ActuatorOutcome.SUCCEEDED


__all__ = [
    "CREATE_GITHUB_ISSUE",
    "DEFAULT_TIMEOUT_SECONDS",
    "GitHubReleaseActuator",
]
