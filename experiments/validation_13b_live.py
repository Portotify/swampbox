"""Step 13B-2B: fail-closed live harness for ONE canonical GitHub effect.

Experiment harness code.  It is not production code, not a governance
provider, and not a new SwampBox capability.

Ordinary execution is SAFE.  Running this file with no arguments performs only
local checks and can never write.  A GitHub write is reachable only when ALL of
these are supplied explicitly:

    --live
    --confirm-disposable-target
    --confirmation-token SWAMPBOX-13B-ONE-GITHUB-ISSUE
    --expected-head <exact 40-hex commit>

Modes:

    DRY             (default) local checks only; no GitHub call at all
    PREFLIGHT-ONLY  --preflight-only: local checks + read-only GitHub GETs
    LIVE            fully armed: preflight, canonical lifecycle, authority
                    probes, re-check, then at most ONE gh api POST attempt

The canonical path is used for the effect: ExecutionScopedConsequenceStore,
ReleaseBoundary and AuthorityProvider.evaluate reach GitHubReleaseActuator,
whose runner is a counting wrapper.  The write budget is one POST *attempt*
(counted before delegation), not one success.  Nothing is retried, cleaned up,
or written to any historical issue.

The baseline stays offline (experiments/validation_13b.py); this harness never
creates a second real issue.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if __package__ in (None, ""):
    sys.path.insert(0, REPO_ROOT)

from experiments.github_release_actuator import (  # noqa: E402
    CREATE_GITHUB_ISSUE,
    GitHubReleaseActuator,
)
from experiments.validation_13b import (  # noqa: E402
    HarnessInvariantError,
    ScriptedAuthorityProvider,
    _allow_decision,
    _expect_error,
    _material,
    _require,
)
from reference.swampbox import (  # noqa: E402
    ActuatorOutcome,
    ExecutionScopedConsequenceStore,
    ProposedConsequence,
    ReleaseBoundary,
    ReleaseState,
)


# ---- Pinned expectations (verified again at runtime, never assumed) -------
EXPECTED_LOGIN = "synthetic-operator"
TARGET_REPOSITORY = "example-org/swampbox-effect-test"
TARGET_REPOSITORY_ID = 123456789
TARGET_NODE_ID = "R_kgDOExampleTarget"
PUBLIC_TARGET_IS_SYNTHETIC = True
CANONICAL_REPOSITORY = "Portotify/swampbox"
CANONICAL_REPOSITORY_ID = 1377727499
CANONICAL_NODE_ID = "R_kgDOUh50Cw"
GITHUB_HOSTNAME = "github.com"

CONFIRMATION_TOKEN = "SWAMPBOX-13B-ONE-GITHUB-ISSUE"
TITLE_PREFIX = "[SwampBox 13B] canonical release "
ISSUE_BODY = (
    "Controlled SwampBox Step 13B validation issue.\n"
    "Disposable test repository. No production data."
)
X_BODY = "Pending SwampBox Step 13B proposal X. Never released."

WRITE_BUDGET = 1
COMMAND_TIMEOUT_SECONDS = 60

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_GATE = 2
EXIT_ANOMALY = 3
EXIT_UNCERTAIN = 4

_HEAD_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{5,63}$")
_ISSUE_URL_PREFIX = f"https://{GITHUB_HOSTNAME}/{TARGET_REPOSITORY}/issues/"

Runner = Callable[..., Any]
ExecutableResolver = Callable[[str], "str | None"]


class ReadFailure(Exception):
    """A read-only query failed; details are deliberately not carried."""


class WriteRefused(Exception):
    """The counted write runner refused a command; nothing was delegated."""


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _default_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


@dataclass
class LiveDeps:
    """Injected seams.  Tests supply fakes for every one of them."""

    git_runner: Runner = subprocess.run
    read_runner: Runner = subprocess.run
    write_delegate: Runner | None = None  # None: every write is refused
    resolve_executable: ExecutableResolver = shutil.which
    actuator_resolver: ExecutableResolver | None = None
    store_factory: Callable[[], ExecutionScopedConsequenceStore] = (
        ExecutionScopedConsequenceStore
    )
    clock: Callable[[], datetime] = _default_clock
    run_id_factory: Callable[[], str] = _default_run_id
    emit: Callable[[str], None] = print


@dataclass(frozen=True)
class Gates:
    live: bool = False
    confirm_disposable_target: bool = False
    confirmation_token: str | None = None
    expected_head: str | None = None
    preflight_only: bool = False


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class RunResult:
    exit_code: int
    lines: list[str]
    facts: dict[str, Any]


def live_gate_failures(gates: Gates) -> list[str]:
    """Return every unmet live gate (never echoes a supplied token)."""

    failures = []
    if not gates.live:
        failures.append("--live is required")
    if not gates.confirm_disposable_target:
        failures.append("--confirm-disposable-target is required")
    if gates.confirmation_token != CONFIRMATION_TOKEN:
        failures.append("the exact --confirmation-token is required")
    if not (
        isinstance(gates.expected_head, str)
        and _HEAD_PATTERN.fullmatch(gates.expected_head)
    ):
        failures.append("--expected-head <exact 40-hex commit> is required")
    return failures


# ---- Read-only seams -------------------------------------------------------


def _git(deps: LiveDeps, *args: str) -> str:
    completed = deps.git_runner(
        ["git", *args],
        cwd=REPO_ROOT,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    if (
        type(completed.returncode) is not int
        or completed.returncode != 0
        or type(completed.stdout) is not str
    ):
        raise ReadFailure("git read failed")
    return completed.stdout


class GitHubReader:
    """Fixed read-only ``gh api --method GET`` queries.  Never a write client."""

    def __init__(self, runner: Runner, gh_path: str) -> None:
        self._runner = runner
        self._gh_path = gh_path

    def get(self, endpoint: str, jq: str, *, paginate: bool = False) -> str:
        argv = [self._gh_path, "api", "--method", "GET", "--hostname", GITHUB_HOSTNAME]
        if paginate:
            argv.append("--paginate")
        argv += ["--jq", jq, endpoint]
        completed = self._runner(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
        if (
            type(completed.returncode) is not int
            or completed.returncode != 0
            or type(completed.stdout) is not str
        ):
            raise ReadFailure("github read failed")
        return completed.stdout

    def get_object(self, endpoint: str, jq: str) -> dict[str, Any]:
        value = json.loads(self.get(endpoint, jq))
        if type(value) is not dict:
            raise ReadFailure("unexpected response shape")
        return value

    def get_string_lines(self, endpoint: str, jq: str) -> list[str]:
        values = [
            json.loads(line)
            for line in self.get(endpoint, jq, paginate=True).splitlines()
            if line.strip()
        ]
        if any(type(value) is not str for value in values):
            raise ReadFailure("unexpected response shape")
        return values


_USER_JQ = "{login: .login, id: .id}"
_TARGET_JQ = (
    "{full_name: .full_name, id: .id, node_id: .node_id, private: .private,"
    " archived: .archived, has_issues: .has_issues}"
)
_CANONICAL_JQ = "{full_name: .full_name, id: .id, node_id: .node_id}"
_TITLES_JQ = '.[] | select(has("pull_request") | not) | .title | @json'
_ISSUE_JQ = "{number: .number, html_url: .html_url, title: .title, repository_url: .repository_url}"


def _is_int(value: Any) -> bool:
    return type(value) is int


@dataclass(frozen=True)
class Preflight:
    checks: tuple[Check, ...]
    github_reads_performed: bool

    @property
    def passed(self) -> bool:
        return all(check.ok for check in self.checks)

    def lines(self) -> list[str]:
        out = []
        for check in self.checks:
            suffix = f" ({check.detail})" if check.detail else ""
            out.append(f"  [{'ok' if check.ok else 'FAIL'}] {check.name}{suffix}")
        return out


def _attempt(function: Callable[[], Any]) -> tuple[bool, Any]:
    try:
        return True, function()
    except Exception:
        return False, None


def run_preflight(
    deps: LiveDeps,
    *,
    github: bool,
    expected_head: str | None,
    planned_title: str,
) -> Preflight:
    """Evaluate every safety fact; each check is independent and fail-closed."""

    checks: list[Check] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append(Check(name, bool(ok), detail))

    # -- local facts --------------------------------------------------------
    head_ok, head = _attempt(lambda: _git(deps, "rev-parse", "HEAD").strip())
    if expected_head is None:
        add("HEAD pin", True, "skipped: no --expected-head supplied")
    else:
        add(
            "HEAD equals --expected-head",
            head_ok and head == expected_head,
            "match" if head_ok and head == expected_head else "mismatch or unreadable",
        )

    status_ok, status = _attempt(lambda: _git(deps, "status", "--porcelain"))
    add(
        "git worktree clean",
        status_ok and status.strip() == "",
        "clean" if status_ok and status.strip() == "" else "dirty or unreadable",
    )

    probe = GitHubReleaseActuator(
        TARGET_REPOSITORY,
        disposable_target_confirmed=True,  # resolver probe only; runner refuses
        runner=_refuse_runner,
        resolve_executable=deps.resolve_executable,
    )
    gh_ok, gh_path = _attempt(probe._resolve_gh)
    gh_path = gh_path if gh_ok and gh_path is not None else None
    add(
        "gh resolves to an adapter-compatible native executable",
        gh_path is not None,
        "resolved" if gh_path is not None else "not resolvable",
    )

    if not github:
        add("GitHub reads", True, "not performed in this mode")
        return Preflight(tuple(checks), github_reads_performed=False)

    if gh_path is None:
        for name in (
            "active login is the pinned login",
            "target identity pinned",
            "target private and not archived",
            "target has issues enabled",
            "canonical repository identity pinned",
            "target identity != canonical identity",
            "planned issue title unused in target",
        ):
            add(name, False, "unavailable: gh not usable")
        return Preflight(tuple(checks), github_reads_performed=False)

    reader = GitHubReader(deps.read_runner, gh_path)

    user_ok, user = _attempt(lambda: reader.get_object("user", _USER_JQ))
    add(
        "active login is the pinned login",
        user_ok and user.get("login") == EXPECTED_LOGIN,
        "match" if user_ok and user.get("login") == EXPECTED_LOGIN else "mismatch or unreadable",
    )

    target_ok, target = _attempt(
        lambda: reader.get_object(f"repos/{TARGET_REPOSITORY}", _TARGET_JQ)
    )
    target_pinned = (
        target_ok
        and target.get("full_name") == TARGET_REPOSITORY
        and _is_int(target.get("id"))
        and target.get("id") == TARGET_REPOSITORY_ID
        and target.get("node_id") == TARGET_NODE_ID
    )
    add("target identity pinned", target_pinned, "full_name, id, node_id match" if target_pinned else "mismatch or unreadable")
    add(
        "target private and not archived",
        target_ok and target.get("private") is True and target.get("archived") is False,
        "private, active" if target_ok and target.get("private") is True and target.get("archived") is False else "public, archived, or unreadable",
    )
    add(
        "target has issues enabled",
        target_ok and target.get("has_issues") is True,
    )

    canonical_ok, canonical = _attempt(
        lambda: reader.get_object(f"repos/{CANONICAL_REPOSITORY}", _CANONICAL_JQ)
    )
    canonical_pinned = (
        canonical_ok
        and canonical.get("full_name") == CANONICAL_REPOSITORY
        and _is_int(canonical.get("id"))
        and canonical.get("id") == CANONICAL_REPOSITORY_ID
        and canonical.get("node_id") == CANONICAL_NODE_ID
    )
    add("canonical repository identity pinned", canonical_pinned, "full_name, id, node_id match" if canonical_pinned else "mismatch or unreadable")

    distinct = (
        target_ok
        and canonical_ok
        and target.get("id") != canonical.get("id")
        and target.get("node_id") != canonical.get("node_id")
        and target.get("full_name") != canonical.get("full_name")
        and TARGET_REPOSITORY != CANONICAL_REPOSITORY
    )
    add("target identity != canonical identity", distinct)

    titles_ok, titles = _attempt(
        lambda: reader.get_string_lines(
            f"repos/{TARGET_REPOSITORY}/issues?state=all&per_page=100", _TITLES_JQ
        )
    )
    add(
        "planned issue title unused in target",
        titles_ok and planned_title not in titles,
        "unused; open and closed checked" if titles_ok and planned_title not in titles else "collision or unreadable",
    )
    return Preflight(tuple(checks), github_reads_performed=True)


def _refuse_runner(*args: Any, **kwargs: Any) -> Any:
    raise WriteRefused("runner is not armed")


# ---- The one-POST budget ---------------------------------------------------


def extract_issue_identity(stdout: Any) -> tuple[int, str] | None:
    """Strict identity extraction (same rule the adapter used for SUCCEEDED)."""

    if type(stdout) is not str:
        return None
    try:
        payload = json.loads(stdout)
    except ValueError:
        return None
    if type(payload) is not dict:
        return None
    number = payload.get("number")
    url = payload.get("html_url")
    if type(number) is not int or number <= 0:
        return None
    if type(url) is not str or url != f"{_ISSUE_URL_PREFIX}{number}":
        return None
    return number, url


class CountedWriteRunner:
    """Counts POST *attempts*, enforces the budget, records the response
    identity observationally, and never retries.

    The count is incremented immediately before delegating, so a timeout or
    non-zero exit still consumes the budget.
    """

    def __init__(self, delegate: Runner | None, planned_title: str) -> None:
        self._delegate = delegate
        self._expected_tail = [
            "api",
            f"repos/{TARGET_REPOSITORY}/issues",
            "--method",
            "POST",
            "--hostname",
            GITHUB_HOSTNAME,
            "-f",
            f"title={planned_title}",
            "-f",
            f"body={ISSUE_BODY}",
        ]
        self.attempts = 0
        self.refused = 0
        self.returncode: int | None = None
        self.identity: tuple[int, str] | None = None

    def _is_expected_command(self, argv: Any, kwargs: dict[str, Any]) -> bool:
        return (
            isinstance(argv, list)
            and len(argv) == len(self._expected_tail) + 1
            and os.path.basename(str(argv[0])).lower() in {"gh", "gh.exe"}
            and argv[1:] == self._expected_tail
            and kwargs.get("shell") is False
        )

    def __call__(self, argv: Any, **kwargs: Any) -> Any:
        if not self._is_expected_command(argv, kwargs):
            self.refused += 1
            raise WriteRefused("command is not the fixed issue POST")
        if self._delegate is None:
            self.refused += 1
            raise WriteRefused("write delegate is not armed")
        if self.attempts >= WRITE_BUDGET:
            self.refused += 1
            raise WriteRefused("write budget exhausted")

        self.attempts += 1  # counted BEFORE the command that may perform the POST
        completed = self._delegate(argv, **kwargs)

        returncode = getattr(completed, "returncode", None)
        self.returncode = returncode if type(returncode) is int else None
        self.identity = extract_issue_identity(getattr(completed, "stdout", None))
        return completed


# ---- Canonical lifecycle ----------------------------------------------------


def _issue_material(title: str, body: str, parent: str | None = None) -> ProposedConsequence:
    return ProposedConsequence(
        CREATE_GITHUB_ISSUE,
        TARGET_REPOSITORY,
        {"title": title, "body": body},
        parent_consequence_id=parent,
    )


def _result_facts(result: Any, attempts: int) -> dict[str, Any]:
    return {
        "status": result.status.value,
        "reason": result.reason,
        "write_attempts": attempts,
    }


def _independent_verification(
    reader: GitHubReader, number: int, url: str, title: str
) -> str:
    """Observational GET only.  Never changes state, never retries a write."""

    ok, data = _attempt(
        lambda: reader.get_object(f"repos/{TARGET_REPOSITORY}/issues/{number}", _ISSUE_JQ)
    )
    if not ok:
        return "UNAVAILABLE"
    repository_url = data.get("repository_url")
    matches = (
        type(data.get("number")) is int
        and data.get("number") == number
        and data.get("html_url") == url
        and data.get("title") == title
        and (
            repository_url is None
            or repository_url == f"https://api.{GITHUB_HOSTNAME}/repos/{TARGET_REPOSITORY}"
        )
    )
    return "PASS" if matches else "FAIL"


def _run_lifecycle(
    deps: LiveDeps,
    gates: Gates,
    run_id: str,
    title: str,
    counted: CountedWriteRunner,
    facts: dict[str, Any],
) -> int:
    """A -> X -> quarantine -> B adopt -> C -> probes -> fresh authority."""

    actuator = GitHubReleaseActuator(
        TARGET_REPOSITORY,
        disposable_target_confirmed=True,
        runner=counted,
        resolve_executable=deps.actuator_resolver or deps.resolve_executable,
        timeout_seconds=COMMAND_TIMEOUT_SECONDS,
    )
    store = deps.store_factory()
    provider = ScriptedAuthorityProvider()
    boundary = ReleaseBoundary(store, provider, actuator, clock=deps.clock)

    x_title = f"[SwampBox 13B] pending proposal {run_id}"
    _require(x_title != title, "X and C titles must differ")

    store.register_execution("exec-A")
    store.register_execution("exec-B")
    x = store.submit("exec-A", "consequence-X", _issue_material(x_title, X_BODY))
    x_id = x.consequence_id
    x_decision = provider.issue_allow(x, deps.clock(), decision_id=f"decision-X-{run_id}")
    facts["x_consequence_id"] = x_id

    store.end_execution("exec-A")
    _expect_error(KeyError, "A must not read X after ending", lambda: store.read("exec-A", x_id))
    quarantined = store.inspect_quarantined(x_id)
    _require(quarantined.current_execution_id is None, "X must have no custodian")
    _require(store.release_state(x_id) is ReleaseState.CONTAINED, "X must stay CONTAINED")
    facts["x_quarantined"] = "yes"

    _expect_error(KeyError, "B must not read X before adoption", lambda: store.read("exec-B", x_id))
    pre = boundary.release("exec-B", x_id)
    _require(
        pre.reason == "consequence_not_in_active_custody",
        f"B must not release X before adoption (got {pre.reason})",
    )
    c_material = _issue_material(title, ISSUE_BODY, parent=x_id)
    _expect_error(
        ValueError,
        "B must not derive C before adoption",
        lambda: store.submit("exec-B", "consequence-C", c_material),
    )
    _require(len(provider.calls) == 0 and counted.attempts == 0, "no authority or write before adoption")

    store.inspect_quarantined(x_id)
    _expect_error(KeyError, "inspection must not grant custody", lambda: store.read("exec-B", x_id))
    adopted = store.adopt("exec-B", x_id)
    _require(adopted.current_execution_id == "exec-B", "B must hold X after adoption")
    facts["b_adopted_x"] = "yes"

    c = store.submit("exec-B", "consequence-C", c_material)
    c_id = c.consequence_id
    _require(c.parent_consequence_id == x_id, "C must record X as parent")
    facts["c_consequence_id"] = c_id
    facts["c_parent_id"] = c.parent_consequence_id
    _require(store.release_state(c_id) is ReleaseState.CONTAINED, "C starts CONTAINED")
    _require(len(provider.calls) == 0, "derivation must not evaluate authority")

    # -- authority probe 1: X-bound decision replayed for C ------------------
    provider.queue(lambda snapshot, at: x_decision)
    probe1 = boundary.release("exec-B", c_id)
    facts["probe1"] = _result_facts(probe1, counted.attempts)
    _require(
        probe1.status is ReleaseState.CONTAINED
        and probe1.reason == "authority_consequence_mismatch"
        and counted.attempts == 0,
        f"probe 1 must be rejected by ReleaseBoundary before any write (got {probe1.reason})",
    )

    # -- authority probe 2: rebound identity, X's material -------------------
    provider.queue(
        lambda snapshot, at: _allow_decision(
            f"decision-X-rebound-{run_id}",
            snapshot.consequence_id,
            snapshot.current_execution_id or "",
            _issue_material(x_title, X_BODY),
            at,
        )
    )
    probe2 = boundary.release("exec-B", c_id)
    facts["probe2"] = _result_facts(probe2, counted.attempts)
    _require(
        probe2.status is ReleaseState.CONTAINED
        and probe2.reason == "authority_material_mismatch"
        and counted.attempts == 0,
        f"probe 2 must be rejected by ReleaseBoundary before any write (got {probe2.reason})",
    )
    _require(len(provider.calls) == 2 and provider.unscripted_calls == 0, "exactly two scripted probes")

    # -- immediate pre-write re-check (authoritative) ------------------------
    final = run_preflight(
        deps, github=True, expected_head=gates.expected_head, planned_title=title
    )
    facts["final_preflight"] = final
    if not final.passed:
        facts["blocked_before_write"] = "final pre-write preflight failed"
        return EXIT_BLOCKED
    _require(counted.attempts == 0, "no write before the final preflight")

    deps.emit("FINAL PLAN (informational; run is non-interactive):")
    for line in (
        f"  LIVE TARGET: {TARGET_REPOSITORY}",
        f"  TARGET REPOSITORY ID: {TARGET_REPOSITORY_ID}",
        f"  AUTHENTICATED LOGIN: {EXPECTED_LOGIN}",
        f"  ISSUE TITLE: {title}",
        f"  WRITE BUDGET: {WRITE_BUDGET} POST ATTEMPT",
        "  RETRY: DISABLED",
        f"  CANONICAL REPOSITORY: {CANONICAL_REPOSITORY}",
        "  LIVE WRITE ATTEMPTED: NO",
    ):
        deps.emit(line)

    # -- fresh C-bound authority: the first write-capable path ---------------
    provider.queue(
        lambda snapshot, at: _allow_decision(
            f"decision-C-fresh-{run_id}",
            snapshot.consequence_id,
            snapshot.current_execution_id or "",
            _material(snapshot),
            at,
        )
    )
    fresh = boundary.release("exec-B", c_id)
    facts["fresh"] = {
        **_result_facts(fresh, counted.attempts),
        "actuator_outcome": fresh.actuator_outcome.value if fresh.actuator_outcome else None,
        "authority_decision_id": fresh.authority_decision_id,
    }
    facts["write_attempts"] = counted.attempts
    facts["release_state"] = store.release_state(c_id).value
    facts["provider_calls"] = len(provider.calls)
    _require(counted.attempts <= WRITE_BUDGET, "write budget exceeded")
    _require(counted.refused == 0, "the counted runner refused a command")

    if fresh.status is ReleaseState.RELEASED:
        _require(counted.attempts == 1, "RELEASED requires exactly one POST attempt")
        identity = counted.identity
        if identity is None:
            facts["verification"] = "NOT RUN (identity not extractable)"
            return EXIT_ANOMALY
        number, url = identity
        facts["issue_number"] = number
        facts["issue_url"] = url
        # Observational only: nothing below may change state or write again.
        try:
            reader = GitHubReader(deps.read_runner, _resolved_gh(deps))
            facts["verification"] = _independent_verification(reader, number, url, title)
        except Exception:
            facts["verification"] = "UNAVAILABLE"

        second = boundary.release("exec-B", c_id)
        facts["second_release"] = {
            **_result_facts(second, counted.attempts),
            "provider_calls": len(provider.calls),
        }
        _require(
            second.reason == "consequence_not_releasable"
            and len(provider.calls) == facts["provider_calls"]
            and counted.attempts == 1,
            f"second release must be refused without a write (got {second.reason})",
        )
        facts["final_write_attempts"] = counted.attempts
        return EXIT_OK if facts["verification"] == "PASS" else EXIT_ANOMALY

    facts["verification"] = "NOT RUN"
    facts["second_release"] = "NOT RUN"
    facts["final_write_attempts"] = counted.attempts
    if fresh.status is ReleaseState.UNCERTAIN:
        facts["issue_number"] = "not established (UNCERTAIN)"
        return EXIT_UNCERTAIN
    if fresh.actuator_outcome is ActuatorOutcome.DEFINITE_NOT_EXECUTED:
        facts["dne"] = "adapter refused before any subprocess attempt"
        _require(counted.attempts == 0, "DNE requires zero POST attempts")
    else:
        facts["dne"] = f"blocked before the actuator ({fresh.reason})"
    return EXIT_BLOCKED


def _resolved_gh(deps: LiveDeps) -> str:
    probe = GitHubReleaseActuator(
        TARGET_REPOSITORY,
        disposable_target_confirmed=True,
        runner=_refuse_runner,
        resolve_executable=deps.resolve_executable,
    )
    resolved = probe._resolve_gh()
    if resolved is None:
        raise ReadFailure("gh not resolvable")
    return resolved


# ---- Orchestration ----------------------------------------------------------


def execute(gates: Gates, deps: LiveDeps) -> RunResult:
    facts: dict[str, Any] = {}

    if gates.live and gates.preflight_only:
        return RunResult(
            EXIT_GATE,
            ["GATE FAILURE: --live and --preflight-only are mutually exclusive",
             "LIVE WRITE ATTEMPTED: NO"],
            facts,
        )

    if gates.live:
        failures = live_gate_failures(gates)
        if failures:
            return RunResult(
                EXIT_GATE,
                ["LIVE GATES NOT SATISFIED (fail closed):"]
                + [f"  - {failure}" for failure in failures]
                + ["LIVE WRITE ATTEMPTED: NO"],
                facts,
            )
        mode = "LIVE"
    elif gates.preflight_only:
        mode = "PREFLIGHT-ONLY"
    else:
        mode = "DRY"

    armed = mode == "LIVE"
    if mode == "LIVE" and PUBLIC_TARGET_IS_SYNTHETIC:
        return RunResult(
            EXIT_GATE,
            [
                "LIVE TARGET CONFIGURATION REQUIRED: the public repository "
                "contains only a sanitized synthetic target",
                "LIVE WRITE ATTEMPTED: NO",
            ],
            facts,
        )
    run_id = deps.run_id_factory()
    if type(run_id) is not str or not _RUN_ID_PATTERN.fullmatch(run_id):
        return RunResult(
            EXIT_BLOCKED,
            ["RUN ID REJECTED (unsafe for use in an issue title)", "LIVE WRITE ATTEMPTED: NO"],
            facts,
        )
    title = f"{TITLE_PREFIX}{run_id}"
    facts.update(mode=mode, run_id=run_id, title=title)

    initial = run_preflight(
        deps,
        github=mode != "DRY",
        expected_head=gates.expected_head,
        planned_title=title,
    )
    facts["preflight"] = initial

    if mode != "LIVE":
        return RunResult(
            EXIT_OK if initial.passed else EXIT_BLOCKED,
            _render_non_live(mode, run_id, title, initial),
            facts,
        )

    counted = CountedWriteRunner(deps.write_delegate, title)
    facts.update(
        head=gates.expected_head,
        target=TARGET_REPOSITORY,
        target_id=TARGET_REPOSITORY_ID,
        login=EXPECTED_LOGIN,
        write_attempts=0,
    )
    if not initial.passed:
        facts["blocked_before_write"] = "initial preflight failed"
        return RunResult(EXIT_BLOCKED, _render_live(facts, counted, initial), facts)

    try:
        code = _run_lifecycle(deps, gates, run_id, title, counted, facts)
    except HarnessInvariantError as error:
        facts["invariant_failure"] = str(error)
        code = EXIT_ANOMALY if counted.attempts else EXIT_BLOCKED
    facts["write_attempts"] = counted.attempts
    return RunResult(code, _render_live(facts, counted, initial), facts)


# ---- Reports ----------------------------------------------------------------


def _render_non_live(mode: str, run_id: str, title: str, preflight: Preflight) -> list[str]:
    label = "DRY/PREFLIGHT" if mode == "DRY" else "PREFLIGHT-ONLY"
    return [
        f"MODE: {label}",
        "LIVE WRITE ARMED: NO",
        "LIVE WRITE ATTEMPTED: NO",
        f"GITHUB READS PERFORMED: {'YES (read-only GET)' if preflight.github_reads_performed else 'NO'}",
        f"TARGET: {TARGET_REPOSITORY}  (never {CANONICAL_REPOSITORY})",
        f"PLANNED RUN ID: {run_id}",
        f"PLANNED TITLE: {title}",
        f"WRITE BUDGET (live only): {WRITE_BUDGET} POST attempt, no retry",
        "PREFLIGHT CHECKS:",
        *preflight.lines(),
        f"PREFLIGHT RESULT: {'PASS' if preflight.passed else 'BLOCKED'}",
        "THE EXTERNAL EXPERIMENT HAS NOT RUN.",
    ]


def _fact(facts: dict[str, Any], key: str, default: str = "NOT REACHED") -> Any:
    return facts.get(key, default)


def _probe_line(value: Any) -> str:
    if isinstance(value, dict):
        return (
            f"{value['status']} / {value['reason']} / write attempts {value['write_attempts']}"
        )
    return str(value)


def _render_live(facts: dict[str, Any], counted: CountedWriteRunner, initial: Preflight) -> list[str]:
    final = facts.get("final_preflight")
    attempted = counted.attempts > 0
    worktree_clean = all(
        check.ok for check in initial.checks if check.name == "git worktree clean"
    )
    fresh = facts.get("fresh")
    lines = [
        "MODE: LIVE",
        f"HEAD: {_fact(facts, 'head')}",
        f"WORKTREE PRECHECK: {'clean' if worktree_clean else 'NOT CLEAN'}",
        f"AUTHENTICATED LOGIN: {_fact(facts, 'login')}",
        f"TARGET: {_fact(facts, 'target')}",
        f"TARGET REPOSITORY ID: {_fact(facts, 'target_id')}",
        f"RUN ID: {_fact(facts, 'run_id')}",
        f"ISSUE TITLE: {_fact(facts, 'title')}",
        f"X CONSEQUENCE ID: {_fact(facts, 'x_consequence_id')}",
        f"X QUARANTINED: {_fact(facts, 'x_quarantined')}",
        f"B ADOPTED X: {_fact(facts, 'b_adopted_x')}",
        f"C CONSEQUENCE ID: {_fact(facts, 'c_consequence_id')}",
        f"C PARENT ID: {_fact(facts, 'c_parent_id')}",
        f"AUTHORITY PROBE 1: {_probe_line(_fact(facts, 'probe1'))}",
        f"AUTHORITY PROBE 2: {_probe_line(_fact(facts, 'probe2'))}",
        f"FINAL PRE-WRITE PREFLIGHT: {('PASS' if final.passed else 'BLOCKED') if final else 'NOT REACHED'}",
        f"FRESH AUTHORITY RESULT: {_probe_line(_fact(facts, 'fresh'))}",
        f"WRITE ATTEMPTS: {counted.attempts}",
        f"ACTUATOR OUTCOME: {fresh['actuator_outcome'] if isinstance(fresh, dict) else 'NOT REACHED'}",
        f"RELEASE STATE: {_fact(facts, 'release_state')}",
        f"ISSUE NUMBER: {_fact(facts, 'issue_number', 'unknown')}",
        f"ISSUE URL: {_fact(facts, 'issue_url', 'unknown')}",
        f"INDEPENDENT VERIFICATION: {_fact(facts, 'verification', 'NOT RUN')}",
        f"SECOND RELEASE: {_probe_line(_fact(facts, 'second_release', 'NOT RUN'))}",
        f"FINAL WRITE ATTEMPTS: {counted.attempts}",
        "RETRY PERFORMED: NO",
        f"LIVE WRITE ATTEMPTED: {'YES' if attempted else 'NO'}",
    ]
    if "blocked_before_write" in facts:
        lines.append(f"BLOCKED BEFORE WRITE: {facts['blocked_before_write']}")
        for preflight in (facts.get("final_preflight"), initial):
            if preflight is not None and not preflight.passed:
                lines += preflight.lines()
                break
    if "dne" in facts:
        lines.append(f"DEFINITE_NOT_EXECUTED: {facts['dne']}")
    if "invariant_failure" in facts:
        lines.append(f"INVARIANT FAILURE: {facts['invariant_failure']}")
    if counted.refused:
        lines.append(f"HARNESS REFUSED COMMANDS: {counted.refused} (nothing delegated)")
    return lines


# ---- CLI ---------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Step 13B-2B live harness. Default is a safe DRY run that cannot write."
        )
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-disposable-target", action="store_true")
    parser.add_argument("--confirmation-token")
    parser.add_argument("--expected-head")
    parser.add_argument("--preflight-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None, deps: LiveDeps | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    gates = Gates(
        live=arguments.live,
        confirm_disposable_target=arguments.confirm_disposable_target,
        confirmation_token=arguments.confirmation_token,
        expected_head=arguments.expected_head,
        preflight_only=arguments.preflight_only,
    )
    deps = deps if deps is not None else LiveDeps()
    if gates.live and not live_gate_failures(gates) and deps.write_delegate is None:
        # The real write delegate exists only in a fully armed process.
        deps = dataclasses.replace(deps, write_delegate=subprocess.run)

    try:
        result = execute(gates, deps)
    except BaseException:
        if gates.live:
            print(
                "LIVE RUN INTERRUPTED: a POST attempt may or may not have been "
                "started. DO NOT RETRY. Inspect the target manually.",
                file=sys.stderr,
            )
        else:
            print("RUN INTERRUPTED: this mode cannot write.", file=sys.stderr)
        raise

    stream = sys.stderr if result.exit_code == EXIT_GATE else sys.stdout
    print("\n".join(result.lines), file=stream)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
