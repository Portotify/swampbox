"""Step 13B-1: offline validation of the canonical consequence lifecycle.

Two phases run against the same offline GitHubReleaseActuator and the same
fake ``gh`` runner semantics:

BASELINE
    A host-owned pending-effect record outlives execution A and execution B
    propagates it.  This is an execution-lifetime baseline, not a sandbox.

SWAMPBOX
    The real ExecutionScopedConsequenceStore and ReleaseBoundary are used:
    A submits X and ends; X is quarantined; B cannot use it through custody
    paths, inspects it, explicitly adopts it, derives C (parent X), and C is
    released only after its own fresh authority decision reaches the
    actuator through AuthorityProvider.evaluate.

No network, no subprocess, no real ``gh``.  Run from the repository root:

    .\\.venv\\Scripts\\python.exe experiments\\validation_13b.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments.github_release_actuator import (  # noqa: E402
    CREATE_GITHUB_ISSUE,
    GitHubReleaseActuator,
)
from reference.swampbox import (  # noqa: E402
    ActuatorOutcome,
    AuthorityDecision,
    AuthorityVerdict,
    ContainedConsequence,
    ExecutionScopedConsequenceStore,
    ProposedConsequence,
    ReleaseBoundary,
    ReleaseState,
)


TARGET_REPOSITORY = "example-org/swampbox-effect-test"
FAKE_GH_PATH = os.path.abspath(os.path.join(os.sep, "offline-fake", "gh.exe"))
FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
AUTHORITY_VALIDITY = timedelta(minutes=5)

X_TITLE = "[13B] pending issue proposed by execution A"
X_BODY = "Offline validation record X (pending, never released)."
C_TITLE = "[13B] issue derived by execution B"
C_BODY = "Offline validation record C (derived from X, own authority)."
BASELINE_TITLE = "[13B-baseline] host-owned pending issue"
BASELINE_BODY = "Offline baseline record."


class HarnessInvariantError(AssertionError):
    """A required experiment invariant was false."""


def _require(condition: bool, description: str) -> None:
    if not condition:
        raise HarnessInvariantError(description)


def _expect_error(
    error_type: type[BaseException],
    description: str,
    action: Callable[[], Any],
) -> BaseException:
    try:
        action()
    except error_type as error:
        return error
    raise HarnessInvariantError(f"{description}: expected {error_type.__name__}")


def _issue_url(number: int) -> str:
    return f"https://github.com/{TARGET_REPOSITORY}/issues/{number}"


class FakeGitHubRunner:
    """Offline stand-in for subprocess.run: records argv, returns a fake POST."""

    def __init__(self, issue_number: int) -> None:
        self.issue_number = issue_number
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        body = json.dumps(
            {"number": self.issue_number, "html_url": _issue_url(self.issue_number)}
        )
        return subprocess.CompletedProcess(argv, 0, body, "")


def _build_actuator(runner: FakeGitHubRunner) -> GitHubReleaseActuator:
    return GitHubReleaseActuator(
        TARGET_REPOSITORY,
        disposable_target_confirmed=True,
        runner=runner,
        resolve_executable=lambda name: FAKE_GH_PATH,
    )


def _expected_argv(title: str, body: str) -> list[str]:
    return [
        FAKE_GH_PATH,
        "api",
        f"repos/{TARGET_REPOSITORY}/issues",
        "--method",
        "POST",
        "--hostname",
        "github.com",
        "-f",
        f"title={title}",
        "-f",
        f"body={body}",
    ]


class ScriptedAuthorityProvider:
    """Smallest scripted provider: each evaluate() pops one queued responder.

    ReleaseBoundary must actually call evaluate(); every call is recorded.  An
    unscripted call fails closed (DENY) and is counted so the harness can
    reject it.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.unscripted_calls = 0
        self._responders: list[
            Callable[[ContainedConsequence, datetime], AuthorityDecision]
        ] = []

    def queue(
        self,
        responder: Callable[[ContainedConsequence, datetime], AuthorityDecision],
    ) -> None:
        self._responders.append(responder)

    def issue_allow(
        self,
        snapshot: ContainedConsequence,
        issued_at: datetime,
        *,
        decision_id: str,
    ) -> AuthorityDecision:
        """Produce a decision outside ReleaseBoundary (not an evaluate call)."""

        return _allow_decision(
            decision_id,
            snapshot.consequence_id,
            snapshot.current_execution_id or snapshot.origin_execution_id,
            _material(snapshot),
            issued_at,
        )

    def evaluate(
        self,
        consequence: ContainedConsequence,
        evaluated_at: datetime,
    ) -> AuthorityDecision:
        self.calls.append((consequence.consequence_id, consequence.current_execution_id))
        if not self._responders:
            self.unscripted_calls += 1
            return AuthorityDecision(
                "decision-unscripted",
                AuthorityVerdict.DENY.value,
                consequence.consequence_id,
                consequence.current_execution_id or "",
                _material(consequence),
                evaluated_at,
                evaluated_at + AUTHORITY_VALIDITY,
            )
        return self._responders.pop(0)(consequence, evaluated_at)


def _material(consequence: ContainedConsequence) -> ProposedConsequence:
    return ProposedConsequence(
        consequence.consequence_type,
        consequence.target,
        consequence.payload,
    )


def _allow_decision(
    decision_id: str,
    consequence_id: str,
    execution_id: str,
    bound: ProposedConsequence,
    issued_at: datetime,
) -> AuthorityDecision:
    return AuthorityDecision(
        decision_id=decision_id,
        verdict=AuthorityVerdict.ALLOW.value,
        consequence_id=consequence_id,
        execution_id=execution_id,
        bound_consequence=bound,
        issued_at=issued_at,
        valid_until=issued_at + AUTHORITY_VALIDITY,
    )


def _proposed_issue(title: str, body: str, parent: str | None = None) -> ProposedConsequence:
    return ProposedConsequence(
        CREATE_GITHUB_ISSUE,
        TARGET_REPOSITORY,
        {"title": title, "body": body},
        parent_consequence_id=parent,
    )


def _run_baseline() -> list[str]:
    """Execution-lifetime baseline: a host-owned record outlives execution A."""

    runner = FakeGitHubRunner(issue_number=136)
    actuator = _build_actuator(runner)
    host_pending_records: list[ProposedConsequence] = []

    execution_a_alive = True
    host_pending_records.append(_proposed_issue(BASELINE_TITLE, BASELINE_BODY))
    execution_a_alive = False

    _require(not execution_a_alive, "baseline execution A must have ended")
    _require(
        len(host_pending_records) == 1,
        "baseline pending record must survive execution A (host-owned record)",
    )
    _require(len(runner.calls) == 0, "baseline: no effect while A alive/just ended")

    # Baseline execution B: consumes the surviving host record and propagates
    # it through the same actuator.  No consequence-level lifecycle exists.
    consumed = host_pending_records.pop(0)
    outcome = actuator.actuate(consumed)

    _require(outcome is ActuatorOutcome.SUCCEEDED, "baseline actuation must succeed")
    _require(len(runner.calls) == 1, "baseline fake effect count must be 1")
    _require(
        runner.calls[0] == _expected_argv(BASELINE_TITLE, BASELINE_BODY),
        "baseline argv must be the fixed command vector",
    )

    return [
        "BASELINE (execution-lifetime baseline; no sandbox, no SwampBox):",
        "  A ended: yes",
        "  consequence survived A: yes (host-owned record; by construction)",
        "  B consumed: yes",
        f"  fake effect count: {len(runner.calls)}",
    ]


def _run_swampbox() -> list[str]:
    runner = FakeGitHubRunner(issue_number=137)
    actuator = _build_actuator(runner)
    provider = ScriptedAuthorityProvider()
    store = ExecutionScopedConsequenceStore()
    boundary = ReleaseBoundary(store, provider, actuator, clock=lambda: FIXED_NOW)

    # ---- Execution A submits pending consequence X, then ends -------------
    store.register_execution("exec-A")
    store.register_execution("exec-B")
    x = store.submit("exec-A", "consequence-X", _proposed_issue(X_TITLE, X_BODY))
    x_id = x.consequence_id
    x_decision = provider.issue_allow(x, FIXED_NOW, decision_id="decision-X")
    _require(x_decision.consequence_id == x_id, "retained decision must be X-bound")
    _require(
        store.read("exec-A", x_id).current_execution_id == "exec-A",
        "A must hold X while active",
    )

    store.end_execution("exec-A")

    _expect_error(KeyError, "A must not read X after ending", lambda: store.read("exec-A", x_id))
    _expect_error(
        ValueError,
        "ended execution A must not be re-registered",
        lambda: store.register_execution("exec-A"),
    )

    # ---- X is quarantined ------------------------------------------------
    quarantined = store.inspect_quarantined(x_id)
    _require(quarantined.current_execution_id is None, "X must have no custodian")
    _require(
        store.release_state(x_id) is ReleaseState.CONTAINED,
        "X must remain CONTAINED (never released)",
    )

    # ---- Execution B before adoption -------------------------------------
    _expect_error(KeyError, "B must not read X before adoption", lambda: store.read("exec-B", x_id))
    _expect_error(
        KeyError,
        "B must not transfer X from ended A",
        lambda: store.transfer("exec-A", "exec-B", x_id),
    )
    pre_release = boundary.release("exec-B", x_id)
    _require(
        pre_release.reason == "consequence_not_in_active_custody"
        and pre_release.status is ReleaseState.CONTAINED,
        f"B must not release X before adoption (got {pre_release.reason})",
    )
    _expect_error(
        ValueError,
        "B must not derive C from X before adoption",
        lambda: store.submit(
            "exec-B", "consequence-C", _proposed_issue(C_TITLE, C_BODY, parent=x_id)
        ),
    )
    _require(len(provider.calls) == 0, "no authority evaluation before adoption")
    _require(len(runner.calls) == 0, "no effect before adoption")

    # ---- Inspection is not custody ---------------------------------------
    inspected = store.inspect_quarantined(x_id)
    _require(inspected.payload == x.payload, "inspection must show X's material")
    _expect_error(
        KeyError,
        "inspection must not grant custody",
        lambda: store.read("exec-B", x_id),
    )

    # ---- Explicit adoption (custody, not authority) ----------------------
    adopted = store.adopt("exec-B", x_id)
    _require(adopted.current_execution_id == "exec-B", "B must hold X after adoption")
    _require(len(provider.calls) == 0, "adoption must not evaluate authority")
    _require(len(runner.calls) == 0, "adoption must not cause an effect")

    # ---- B derives C with parent X ---------------------------------------
    c = store.submit(
        "exec-B", "consequence-C", _proposed_issue(C_TITLE, C_BODY, parent=x_id)
    )
    c_id = c.consequence_id
    _require(c.parent_consequence_id == x_id, "C must record X as its parent")
    _require(
        (c.consequence_type, c.target, c.payload)
        != (x.consequence_type, x.target, x.payload),
        "C must be materially different from X",
    )
    _require(store.release_state(c_id) is ReleaseState.CONTAINED, "C starts CONTAINED")
    _require(len(provider.calls) == 0, "derivation must not evaluate authority")

    # ---- Attempt 1: X-bound authority replayed for C ---------------------
    provider.queue(lambda snapshot, at: x_decision)
    replay = boundary.release("exec-B", c_id)
    _require(
        replay.status is ReleaseState.CONTAINED
        and replay.reason == "authority_consequence_mismatch",
        f"X-bound decision must not release C (got {replay.reason})",
    )
    _require(len(provider.calls) == 1, "replay attempt must invoke the provider once")
    _require(len(runner.calls) == 0, "replay must not reach the actuator")

    # ---- Attempt 2: X's ALLOW relabeled for C, still carrying X material -
    provider.queue(
        lambda snapshot, at: _allow_decision(
            "decision-X-relabeled",
            snapshot.consequence_id,
            snapshot.current_execution_id or "",
            _proposed_issue(X_TITLE, X_BODY),
            at,
        )
    )
    relabeled = boundary.release("exec-B", c_id)
    _require(
        relabeled.status is ReleaseState.CONTAINED
        and relabeled.reason == "authority_material_mismatch",
        f"relabeled X-material decision must not release C (got {relabeled.reason})",
    )
    _require(len(provider.calls) == 2, "relabeled attempt must invoke the provider")
    _require(len(runner.calls) == 0, "relabeled attempt must not reach the actuator")

    # ---- Attempt 3: fresh authority bound to C ---------------------------
    provider.queue(
        lambda snapshot, at: _allow_decision(
            "decision-C-fresh",
            snapshot.consequence_id,
            snapshot.current_execution_id or "",
            _material(snapshot),
            at,
        )
    )
    released = boundary.release("exec-B", c_id)
    _require(
        released.status is ReleaseState.RELEASED
        and released.actuator_outcome is ActuatorOutcome.SUCCEEDED
        and released.authority_decision_id == "decision-C-fresh",
        f"fresh C authority must release C (got {released.status}/{released.reason})",
    )
    _require(store.release_state(c_id) is ReleaseState.RELEASED, "C must be RELEASED")
    _require(len(provider.calls) == 3, "three release attempts, three evaluate calls")
    _require(len(runner.calls) == 1, "actuator must be invoked exactly once")
    _require(
        runner.calls[0] == _expected_argv(C_TITLE, C_BODY),
        "actuator must receive C's material, not X's",
    )
    _require(
        store.release_state(x_id) is ReleaseState.CONTAINED,
        "X must remain unreleased; only C crossed the effect boundary",
    )
    _require(provider.unscripted_calls == 0, "no unscripted provider calls")

    # ---- Second release uses the existing terminal guard -----------------
    second = boundary.release("exec-B", c_id)
    _require(
        second.reason == "consequence_not_releasable" and not second.released,
        f"second release must be refused (got {second.reason})",
    )
    _require(len(provider.calls) == 3, "second release must not call the provider")
    _require(len(runner.calls) == 1, "second release must not call the actuator")

    return [
        "SWAMPBOX (real ExecutionScopedConsequenceStore + ReleaseBoundary):",
        "  A ended: yes (read and re-registration refused)",
        "  X quarantined: yes (no custodian, state CONTAINED)",
        "  B custody before adoption: none (read, transfer, release refused)",
        "  pre-adoption child derivation blocked: yes",
        "  inspection granted custody: no",
        "  adoption performed: yes (explicit; provider calls: 0)",
        f"  C parent id: {c.parent_consequence_id}",
        f"  X-bound authority replay: {replay.reason}",
        f"  X-material relabeled authority: {relabeled.reason}",
        f"  fresh C authority: {released.status.value} ({released.reason})",
        f"  provider evaluate calls: {len(provider.calls)}",
        f"  fake actuator count: {len(runner.calls)}",
        f"  C final release state: {store.release_state(c_id).value}",
        f"  X final release state: {store.release_state(x_id).value}",
        f"  second release: {second.reason}; effect count: {len(runner.calls)}",
    ]


def run() -> list[str]:
    """Run both phases; raise HarnessInvariantError if any invariant fails."""

    report = _run_baseline() + _run_swampbox()
    report += [
        "CLAIM:",
        "  execution lifetime ended; the consequence persisted",
        "  SwampBox introduced an explicit consequence lifecycle boundary:",
        "  quarantine, explicit adoption, derivation, and fresh per-consequence authority",
        "LIMIT:",
        "  offline fake GitHub transport; no live GitHub effect yet",
        "  baseline survival is by construction of a host-owned record",
        "  no real sandbox claim; no authenticated semantic causality claim",
    ]
    return report


def main() -> int:
    try:
        report = run()
    except HarnessInvariantError as error:
        print(f"13B-1 HARNESS FAILED: {error}", file=sys.stderr)
        return 1
    print("\n".join(report))
    print("13B-1 OFFLINE HARNESS: all invariants held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
