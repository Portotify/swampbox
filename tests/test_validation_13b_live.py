"""Offline tests for the Step 13B-2B live harness.

Nothing here touches GitHub, spawns a process, or arms a real write: git, the
GitHub read runner, the write delegate, and gh resolution are all fakes. The
lifecycle tests use a separate offline-only target configuration; the tracked
public synthetic target is tested separately for delegate-independent closure.
"""

from __future__ import annotations

import asyncio
import io
import itertools
import json
import os
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from unittest.mock import patch

from experiments import validation_13b_live as live
from reference.swampbox import ActuatorOutcome, ReleaseResult, ReleaseState


HEAD = "a" * 40
OTHER_HEAD = "b" * 40
NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
RUN_ID = "20260101T120000Z-abc123"
TITLE = f"{live.TITLE_PREFIX}{RUN_ID}"
FAKE_GH = os.path.abspath(os.path.join(os.sep, "offline-fake", "gh.exe"))
SECRET = "ghp_TESTSECRET1234567890abcdef"
C_ID = "consequence-C"
X_ID = "consequence-X"


_PUBLIC_TARGET_CONFIGURATION = {
    "EXPECTED_LOGIN": live.EXPECTED_LOGIN,
    "TARGET_REPOSITORY": live.TARGET_REPOSITORY,
    "TARGET_REPOSITORY_ID": live.TARGET_REPOSITORY_ID,
    "TARGET_NODE_ID": live.TARGET_NODE_ID,
    "PUBLIC_TARGET_IS_SYNTHETIC": live.PUBLIC_TARGET_IS_SYNTHETIC,
    "_ISSUE_URL_PREFIX": live._ISSUE_URL_PREFIX,
}
_OFFLINE_TARGET_CONFIGURATION = {
    "EXPECTED_LOGIN": "offline-operator",
    "TARGET_REPOSITORY": "offline.example.invalid/swampbox-effect-test",
    "TARGET_REPOSITORY_ID": 987654321,
    "TARGET_NODE_ID": "R_offline_target",
    "PUBLIC_TARGET_IS_SYNTHETIC": False,
    "_ISSUE_URL_PREFIX": (
        "https://github.com/offline.example.invalid/swampbox-effect-test/issues/"
    ),
}
_OFFLINE_TARGET_PATCHER = None


def setUpModule() -> None:
    global _OFFLINE_TARGET_PATCHER
    _OFFLINE_TARGET_PATCHER = patch.multiple(live, **_OFFLINE_TARGET_CONFIGURATION)
    _OFFLINE_TARGET_PATCHER.start()


def tearDownModule() -> None:
    _OFFLINE_TARGET_PATCHER.stop()


def _url(number: int) -> str:
    return f"https://github.com/{live.TARGET_REPOSITORY}/issues/{number}"


def _completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


class Env:
    """A complete fake world: git, GitHub reads, the write delegate, gh."""

    def __init__(self, **overrides) -> None:
        self.head = HEAD
        self.status = ""
        self.login = live.EXPECTED_LOGIN
        self.target = {
            "full_name": live.TARGET_REPOSITORY,
            "id": live.TARGET_REPOSITORY_ID,
            "node_id": live.TARGET_NODE_ID,
            "private": True,
            "archived": False,
            "has_issues": True,
        }
        self.canonical = {
            "full_name": live.CANONICAL_REPOSITORY,
            "id": live.CANONICAL_REPOSITORY_ID,
            "node_id": live.CANONICAL_NODE_ID,
        }
        self.titles_sequence: list[list[str]] = [[]]
        self.gh: str | None = FAKE_GH
        self.actuator_gh: str | None = FAKE_GH
        self.issue_number = 7431
        self.write_returncode = 0
        self.write_stdout: str | None = None
        self.write_raises: BaseException | None = None
        self.issue_get_override: dict | None = None
        self.read_fail_endpoints: set[str] = set()
        self.run_id = RUN_ID

        self.events: list[tuple] = []
        self.git_calls: list[list[str]] = []
        self.read_calls: list[list[str]] = []
        self.write_calls: list[list[str]] = []
        self.emitted: list[str] = []
        self.stores: list = []
        self._title_reads = 0
        for key, value in overrides.items():
            setattr(self, key, value)

    # -- fakes ----------------------------------------------------------------
    def git(self, argv, **kwargs):
        self.git_calls.append(list(argv))
        if argv[1:] == ["rev-parse", "HEAD"]:
            return _completed(argv, 0, self.head + "\n")
        if argv[1:] == ["status", "--porcelain"]:
            return _completed(argv, 0, self.status)
        raise AssertionError(f"unexpected git command: {argv}")

    def read(self, argv, **kwargs):
        self.read_calls.append(list(argv))
        endpoint = argv[-1]
        self.events.append(("read", endpoint))
        if endpoint in self.read_fail_endpoints:
            return _completed(argv, 1, "", f"error token {SECRET}")
        if endpoint == "user":
            return _completed(argv, 0, json.dumps({"login": self.login, "id": 254327027}))
        if endpoint == f"repos/{live.TARGET_REPOSITORY}":
            return _completed(argv, 0, json.dumps(self.target))
        if endpoint == f"repos/{live.CANONICAL_REPOSITORY}":
            return _completed(argv, 0, json.dumps(self.canonical))
        if endpoint.startswith(f"repos/{live.TARGET_REPOSITORY}/issues?"):
            index = min(self._title_reads, len(self.titles_sequence) - 1)
            self._title_reads += 1
            titles = self.titles_sequence[index]
            return _completed(argv, 0, "\n".join(json.dumps(t) for t in titles))
        match = re.fullmatch(rf"repos/{live.TARGET_REPOSITORY}/issues/(\d+)", endpoint)
        if match:
            number = int(match.group(1))
            body = self.issue_get_override or {
                "number": number,
                "html_url": _url(number),
                "title": TITLE,
                "repository_url": f"https://api.github.com/repos/{live.TARGET_REPOSITORY}",
            }
            return _completed(argv, 0, json.dumps(body))
        raise AssertionError(f"unexpected read endpoint: {endpoint}")

    def write(self, argv, **kwargs):
        self.write_calls.append(list(argv))
        self.events.append(("write",))
        if self.write_raises is not None:
            raise self.write_raises
        stdout = self.write_stdout
        if stdout is None:
            stdout = json.dumps(
                {"number": self.issue_number, "html_url": _url(self.issue_number),
                 "token": SECRET}
            )
        return _completed(argv, self.write_returncode, stdout, f"stderr {SECRET}")

    def make_store(self):
        store = live.ExecutionScopedConsequenceStore()
        self.stores.append(store)
        return store

    def deps(self, *, write: bool = True) -> live.LiveDeps:
        return live.LiveDeps(
            git_runner=self.git,
            read_runner=self.read,
            write_delegate=self.write if write else None,
            resolve_executable=lambda name: self.gh,
            actuator_resolver=lambda name: self.actuator_gh,
            store_factory=self.make_store,
            clock=lambda: NOW,
            run_id_factory=lambda: self.run_id,
            emit=self.emitted.append,
        )

    def all_argv(self) -> list[list[str]]:
        return self.git_calls + self.read_calls + self.write_calls


def armed_gates(**overrides) -> live.Gates:
    values = dict(
        live=True,
        confirm_disposable_target=True,
        confirmation_token=live.CONFIRMATION_TOKEN,
        expected_head=HEAD,
    )
    values.update(overrides)
    return live.Gates(**values)


def run_cli(argv, deps):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = live.main(argv, deps)
    return code, out.getvalue(), err.getvalue()


ARMED_ARGV = [
    "--live",
    "--confirm-disposable-target",
    "--confirmation-token",
    live.CONFIRMATION_TOKEN,
    "--expected-head",
    HEAD,
]


class PublicSyntheticTargetGateTests(unittest.TestCase):
    def test_live_synthetic_target_fails_before_injected_delegate(self) -> None:
        with patch.multiple(live, **_PUBLIC_TARGET_CONFIGURATION):
            env = Env()
            result = live.execute(armed_gates(), env.deps())

        self.assertEqual(result.exit_code, live.EXIT_GATE)
        self.assertEqual(env.read_calls, [])
        self.assertEqual(env.write_calls, [])
        self.assertIn(
            "LIVE TARGET CONFIGURATION REQUIRED: the public repository contains only a sanitized synthetic target",
            result.lines,
        )
        self.assertIn("LIVE WRITE ATTEMPTED: NO", result.lines)

    def test_live_synthetic_marker_ignores_target_spelling(self) -> None:
        with patch.multiple(live, **_PUBLIC_TARGET_CONFIGURATION):
            with patch.object(live, "TARGET_REPOSITORY", "another.synthetic.target"):
                env = Env()
                result = live.execute(armed_gates(), env.deps())

        self.assertEqual(result.exit_code, live.EXIT_GATE)
        self.assertEqual(env.read_calls, [])
        self.assertEqual(env.write_calls, [])


class DefaultSafetyTests(unittest.TestCase):
    def test_default_invocation_cannot_write_and_makes_no_github_call(self) -> None:
        env = Env()

        code, out, _ = run_cli([], env.deps())

        self.assertEqual(code, 0)
        self.assertEqual(env.write_calls, [])
        self.assertEqual(env.read_calls, [])
        self.assertEqual(env.stores, [])
        self.assertIn("MODE: DRY/PREFLIGHT", out)
        self.assertIn("LIVE WRITE ARMED: NO", out)
        self.assertIn("LIVE WRITE ATTEMPTED: NO", out)
        self.assertIn("THE EXTERNAL EXPERIMENT HAS NOT RUN.", out)

    def test_preflight_only_reads_with_get_and_never_writes(self) -> None:
        env = Env()

        code, out, _ = run_cli(["--preflight-only"], env.deps())

        self.assertEqual(code, 0)
        self.assertGreater(len(env.read_calls), 0)
        self.assertEqual(env.write_calls, [])
        self.assertEqual(env.stores, [])
        self.assertIn("MODE: PREFLIGHT-ONLY", out)
        self.assertIn("LIVE WRITE ATTEMPTED: NO", out)

    def test_missing_live_flag_cannot_write_even_with_other_gates(self) -> None:
        env = Env()

        code, out, _ = run_cli(ARMED_ARGV[1:], env.deps())

        self.assertEqual(code, 0)
        self.assertEqual(env.write_calls, [])
        self.assertEqual(env.stores, [])
        self.assertIn("LIVE WRITE ARMED: NO", out)

    def test_missing_disposable_confirmation_cannot_write(self) -> None:
        env = Env()
        argv = [a for a in ARMED_ARGV if a != "--confirm-disposable-target"]

        code, _, err = run_cli(argv, env.deps())

        self.assertEqual(code, live.EXIT_GATE)
        self.assertEqual(env.write_calls, [])
        self.assertEqual(env.read_calls, [])
        self.assertIn("--confirm-disposable-target", err)

    def test_wrong_or_missing_confirmation_token_cannot_write(self) -> None:
        for token in ("WRONG-TOKEN-123", "", live.CONFIRMATION_TOKEN.lower(), None):
            with self.subTest(token=token):
                env = Env()
                argv = ["--live", "--confirm-disposable-target", "--expected-head", HEAD]
                if token is not None:
                    argv += ["--confirmation-token", token]

                code, out, err = run_cli(argv, env.deps())

                self.assertEqual(code, live.EXIT_GATE)
                self.assertEqual(env.write_calls, [])
                self.assertEqual(env.read_calls, [])
                if token:
                    self.assertNotIn(token, out + err)

    def test_missing_or_malformed_expected_head_cannot_write(self) -> None:
        for head in (None, "", "abc", "A" * 40, "g" * 40, HEAD[:-1]):
            with self.subTest(head=head):
                env = Env()
                argv = ["--live", "--confirm-disposable-target",
                        "--confirmation-token", live.CONFIRMATION_TOKEN]
                if head is not None:
                    argv += ["--expected-head", head]

                code, _, err = run_cli(argv, env.deps())

                self.assertEqual(code, live.EXIT_GATE)
                self.assertEqual(env.write_calls, [])
                self.assertIn("--expected-head", err)

    def test_live_and_preflight_only_are_mutually_exclusive(self) -> None:
        env = Env()

        code, _, err = run_cli(ARMED_ARGV + ["--preflight-only"], env.deps())

        self.assertEqual(code, live.EXIT_GATE)
        self.assertEqual(env.write_calls, [])
        self.assertIn("mutually exclusive", err)

    def test_environment_variables_alone_never_arm_a_write(self) -> None:
        env = Env()
        fake_env = {
            "SWAMPBOX_13B_LIVE": "1",
            "SWAMPBOX_13B_CONFIRM": live.CONFIRMATION_TOKEN,
            "LIVE": "1",
        }

        with patch.dict(os.environ, fake_env):
            code, out, _ = run_cli([], env.deps())

        self.assertEqual(code, 0)
        self.assertEqual(env.write_calls, [])
        self.assertIn("LIVE WRITE ARMED: NO", out)

    def test_real_write_delegate_is_wired_only_when_fully_armed(self) -> None:
        env = Env()
        calls = []

        def fake_subprocess_run(argv, **kwargs):
            calls.append(list(argv))
            return env.write(argv, **kwargs)

        with patch.object(subprocess, "run", fake_subprocess_run):
            run_cli([], env.deps(write=False))
            run_cli(["--preflight-only"], env.deps(write=False))
            self.assertEqual(calls, [])

            code, _, _ = run_cli(ARMED_ARGV, env.deps(write=False))

        self.assertEqual(code, live.EXIT_OK)
        self.assertEqual(len(calls), 1)
        self.assertIn("POST", calls[0])


class DelegateWiringTests(unittest.TestCase):
    def _delegate_passed_to_execute(self, argv) -> object:
        env = Env()
        captured = {}
        real_execute = live.execute

        def spy(gates, deps):
            captured["delegate"] = deps.write_delegate
            return real_execute(gates, deps)

        sentinel = object()
        with patch.object(subprocess, "run", sentinel), patch.object(live, "execute", spy):
            run_cli(argv, env.deps(write=False))
        return captured["delegate"], sentinel

    def test_no_real_delegate_in_dry_preflight_or_partially_armed_runs(self) -> None:
        partial = [a for a in ARMED_ARGV if a != "--confirm-disposable-target"]
        for argv in ([], ["--preflight-only"], ARMED_ARGV[1:], partial):
            with self.subTest(argv=argv):
                delegate, _ = self._delegate_passed_to_execute(argv)
                self.assertIsNone(delegate)

    def test_real_delegate_appears_only_when_every_live_gate_is_met(self) -> None:
        delegate, sentinel = self._delegate_passed_to_execute(ARMED_ARGV)

        self.assertIs(delegate, sentinel)


class PreBlockTests(unittest.TestCase):
    """Every preflight failure must stop the run with zero writes."""

    def _assert_blocked(self, env: Env, *, expect_lifecycle: bool = False) -> live.RunResult:
        result = live.execute(armed_gates(), env.deps())

        self.assertEqual(result.exit_code, live.EXIT_BLOCKED)
        self.assertEqual(env.write_calls, [])
        self.assertIn("LIVE WRITE ATTEMPTED: NO", result.lines)
        if not expect_lifecycle:
            self.assertEqual(env.stores, [])
        return result

    def test_head_mismatch_blocks_before_write(self) -> None:
        self._assert_blocked(Env(head=OTHER_HEAD))

    def test_matching_head_is_required_exactly(self) -> None:
        env = Env()
        result = live.execute(armed_gates(expected_head=HEAD), env.deps())
        self.assertEqual(result.exit_code, live.EXIT_OK)
        self.assertIn(["git", "rev-parse", "HEAD"], env.git_calls)

    def test_dirty_worktree_blocks_before_write(self) -> None:
        for status in (" M reference/swampbox.py\n", "?? stray.txt\n"):
            with self.subTest(status=status):
                self._assert_blocked(Env(status=status))

    def test_login_mismatch_blocks_before_write(self) -> None:
        self._assert_blocked(Env(login="Portotify"))

    def test_target_repository_id_mismatch_blocks_before_write(self) -> None:
        for bad in (live.TARGET_REPOSITORY_ID + 1, str(live.TARGET_REPOSITORY_ID), True, None):
            with self.subTest(id=bad):
                env = Env()
                env.target["id"] = bad
                self._assert_blocked(env)

    def test_target_node_id_mismatch_blocks_before_write(self) -> None:
        env = Env()
        env.target["node_id"] = "R_other"
        self._assert_blocked(env)

    def test_target_full_name_mismatch_blocks_before_write(self) -> None:
        env = Env()
        env.target["full_name"] = live.CANONICAL_REPOSITORY
        self._assert_blocked(env)

    def test_public_target_blocks_before_write(self) -> None:
        for value in (False, None, "true"):
            with self.subTest(private=value):
                env = Env()
                env.target["private"] = value
                self._assert_blocked(env)

    def test_archived_target_blocks_before_write(self) -> None:
        for value in (True, None):
            with self.subTest(archived=value):
                env = Env()
                env.target["archived"] = value
                self._assert_blocked(env)

    def test_issues_disabled_blocks_before_write(self) -> None:
        env = Env()
        env.target["has_issues"] = False
        self._assert_blocked(env)

    def test_canonical_identity_collision_blocks_before_write(self) -> None:
        cases = {
            "same numeric id": {"id": live.TARGET_REPOSITORY_ID},
            "same node id": {"node_id": live.TARGET_NODE_ID},
            "canonical id drift": {"id": 1},
            "canonical name drift": {"full_name": live.TARGET_REPOSITORY},
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                env = Env()
                env.canonical.update(change)
                self._assert_blocked(env)

    def test_exact_title_collision_blocks_before_write(self) -> None:
        env = Env(titles_sequence=[["unrelated", TITLE]])
        self._assert_blocked(env)

    def test_similar_title_is_not_a_collision(self) -> None:
        env = Env(titles_sequence=[[TITLE + " ", TITLE.lower(), "x " + TITLE]])
        result = live.execute(armed_gates(), env.deps())
        self.assertEqual(result.exit_code, live.EXIT_OK)

    def test_unreadable_github_facts_block_before_write(self) -> None:
        endpoints = [
            "user",
            f"repos/{live.TARGET_REPOSITORY}",
            f"repos/{live.CANONICAL_REPOSITORY}",
        ]
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                self._assert_blocked(Env(read_fail_endpoints={endpoint}))

    def test_non_adapter_compatible_gh_blocks_before_write(self) -> None:
        self._assert_blocked(Env(gh=None))
        self._assert_blocked(Env(gh=""))

    @unittest.skipUnless(sys.platform == "win32", "Windows shim rule")
    def test_windows_cmd_shim_blocks_before_write(self) -> None:
        self._assert_blocked(Env(gh="C:\\tools\\gh.cmd"))

    def test_unsafe_run_id_is_rejected_before_anything_else(self) -> None:
        for run_id in ('x"; rm -rf /', "short", "a b c d e f", "..\\..\\x", "é" * 8):
            with self.subTest(run_id=run_id):
                env = Env(run_id=run_id)
                result = live.execute(armed_gates(), env.deps())
                self.assertEqual(result.exit_code, live.EXIT_BLOCKED)
                self.assertEqual(env.write_calls, [])
                self.assertEqual(env.read_calls, [])

    def test_second_preflight_blocks_a_change_after_the_probes(self) -> None:
        env = Env(titles_sequence=[[], [TITLE]])

        result = live.execute(armed_gates(), env.deps())

        self.assertEqual(result.exit_code, live.EXIT_BLOCKED)
        self.assertEqual(env.write_calls, [])
        self.assertIn("blocked_before_write", result.facts)
        self.assertEqual(result.facts["probe1"]["write_attempts"], 0)
        self.assertEqual(result.facts["probe2"]["write_attempts"], 0)

    def test_final_preflight_catches_head_change_after_the_probes(self) -> None:
        env = Env()
        # First preflight sees the pinned HEAD; every later read sees a new one.
        heads = itertools.chain([HEAD], itertools.repeat(OTHER_HEAD))
        original = env.git

        def drifting_git(argv, **kwargs):
            if argv[1:] == ["rev-parse", "HEAD"]:
                env.head = next(heads)
            return original(argv, **kwargs)

        deps = env.deps()
        deps.git_runner = drifting_git
        result = live.execute(armed_gates(), deps)

        self.assertEqual(result.exit_code, live.EXIT_BLOCKED)
        self.assertEqual(env.write_calls, [])


class CanonicalLifecycleTests(unittest.TestCase):
    def test_full_fake_run_releases_through_canonical_boundary(self) -> None:
        env = Env()

        result = live.execute(armed_gates(), env.deps())

        self.assertEqual(result.exit_code, live.EXIT_OK)
        facts = result.facts
        self.assertEqual(facts["x_quarantined"], "yes")
        self.assertEqual(facts["b_adopted_x"], "yes")
        self.assertEqual(facts["c_parent_id"], X_ID)
        self.assertEqual(facts["release_state"], "RELEASED")
        self.assertEqual(facts["fresh"]["actuator_outcome"], "SUCCEEDED")
        self.assertEqual(env.stores[0].release_state(C_ID), ReleaseState.RELEASED)
        self.assertEqual(env.stores[0].release_state(X_ID), ReleaseState.CONTAINED)

    def test_authority_probes_are_rejected_by_the_canonical_boundary(self) -> None:
        env = Env()

        result = live.execute(armed_gates(), env.deps())

        probe1, probe2 = result.facts["probe1"], result.facts["probe2"]
        self.assertEqual(probe1["reason"], "authority_consequence_mismatch")
        self.assertEqual(probe1["status"], "CONTAINED")
        self.assertEqual(probe1["write_attempts"], 0)
        self.assertEqual(probe2["reason"], "authority_material_mismatch")
        self.assertEqual(probe2["status"], "CONTAINED")
        self.assertEqual(probe2["write_attempts"], 0)

    def test_fresh_c_authority_is_the_first_path_reaching_the_write_runner(self) -> None:
        env = Env()

        live.execute(armed_gates(), env.deps())

        write_index = env.events.index(("write",))
        self.assertEqual(env.events.count(("write",)), 1)
        self.assertGreaterEqual(write_index, 8)  # two full preflights precede it
        self.assertTrue(env.events[write_index - 1][1].startswith(
            f"repos/{live.TARGET_REPOSITORY}/issues?"))

    def test_provider_is_evaluated_three_times_and_second_release_adds_none(self) -> None:
        env = Env()

        result = live.execute(armed_gates(), env.deps())

        self.assertEqual(result.facts["provider_calls"], 3)
        second = result.facts["second_release"]
        self.assertEqual(second["reason"], "consequence_not_releasable")
        self.assertEqual(second["provider_calls"], 3)
        self.assertEqual(second["write_attempts"], 1)
        self.assertEqual(len(env.write_calls), 1)

    def test_issue_number_is_taken_from_the_response_never_assumed(self) -> None:
        for number in (5, 90210, 1):
            with self.subTest(number=number):
                env = Env(issue_number=number)

                result = live.execute(armed_gates(), env.deps())

                self.assertEqual(result.facts["issue_number"], number)
                self.assertEqual(result.facts["issue_url"], _url(number))
                self.assertIn(("read", f"repos/{live.TARGET_REPOSITORY}/issues/{number}"),
                              env.events)

    def test_independent_get_uses_the_returned_number_and_is_get_only(self) -> None:
        env = Env(issue_number=8642)

        result = live.execute(armed_gates(), env.deps())

        self.assertEqual(result.facts["verification"], "PASS")
        gets = [c for c in env.read_calls if c[-1].endswith("/issues/8642")]
        self.assertEqual(len(gets), 1)
        self.assertEqual(gets[0][gets[0].index("--method") + 1], "GET")

    def test_independent_get_failure_does_not_change_state_or_retry(self) -> None:
        env = Env(read_fail_endpoints={f"repos/{live.TARGET_REPOSITORY}/issues/7431"})

        result = live.execute(armed_gates(), env.deps())

        self.assertEqual(result.exit_code, live.EXIT_ANOMALY)
        self.assertEqual(result.facts["verification"], "UNAVAILABLE")
        self.assertEqual(env.stores[0].release_state(C_ID), ReleaseState.RELEASED)
        self.assertEqual(len(env.write_calls), 1)
        self.assertEqual(result.facts["final_write_attempts"], 1)
        self.assertEqual(result.facts["second_release"]["write_attempts"], 1)

    def test_independent_get_mismatch_is_reported_without_state_change(self) -> None:
        env = Env(issue_get_override={
            "number": 7431, "html_url": _url(7431), "title": "someone else's title",
            "repository_url": f"https://api.github.com/repos/{live.TARGET_REPOSITORY}",
        })

        result = live.execute(armed_gates(), env.deps())

        self.assertEqual(result.exit_code, live.EXIT_ANOMALY)
        self.assertEqual(result.facts["verification"], "FAIL")
        self.assertEqual(env.stores[0].release_state(C_ID), ReleaseState.RELEASED)
        self.assertEqual(len(env.write_calls), 1)

    def test_a_second_release_that_is_not_refused_is_reported_as_an_anomaly(self) -> None:
        env = Env()
        calls = {"n": 0}

        class LeakyBoundary(live.ReleaseBoundary):
            def release(self, execution_id, consequence_id):
                calls["n"] += 1
                if calls["n"] == 5:  # X pre-adoption, probe 1, probe 2, fresh, second
                    return ReleaseResult(
                        consequence_id, ReleaseState.RELEASED, "actuator_succeeded",
                        "decision-x", ActuatorOutcome.SUCCEEDED,
                    )
                return super().release(execution_id, consequence_id)

        with patch.object(live, "ReleaseBoundary", LeakyBoundary):
            result = live.execute(armed_gates(), env.deps())

        self.assertEqual(result.exit_code, live.EXIT_ANOMALY)
        self.assertTrue(any(line.startswith("INVARIANT FAILURE") for line in result.lines))
        self.assertEqual(len(env.write_calls), 1)

    def test_get_failure_is_reported_when_gh_becomes_unresolvable_after_the_write(self) -> None:
        env = Env()
        calls = {"n": 0}

        def flaky_resolver(name):
            calls["n"] += 1
            return FAKE_GH if calls["n"] <= 2 else None  # preflights only

        deps = env.deps()
        deps.resolve_executable = flaky_resolver
        deps.actuator_resolver = lambda name: FAKE_GH

        result = live.execute(armed_gates(), deps)

        self.assertEqual(result.facts["verification"], "UNAVAILABLE")
        self.assertEqual(len(env.write_calls), 1)
        self.assertEqual(result.exit_code, live.EXIT_ANOMALY)


class UncertainAndRefusedTests(unittest.TestCase):
    def _assert_uncertain_single_attempt(self, env: Env) -> live.RunResult:
        result = live.execute(armed_gates(), env.deps())

        self.assertEqual(result.exit_code, live.EXIT_UNCERTAIN)
        self.assertEqual(len(env.write_calls), 1)
        self.assertEqual(result.facts["final_write_attempts"], 1)
        self.assertEqual(env.stores[0].release_state(C_ID), ReleaseState.UNCERTAIN)
        self.assertEqual(result.facts["second_release"], "NOT RUN")
        self.assertEqual(result.facts["verification"], "NOT RUN")
        self.assertFalse(
            [c for c in env.read_calls if re.search(r"/issues/\d+$", c[-1])]
        )
        self.assertIn("RETRY PERFORMED: NO", result.lines)
        return result

    def test_nonzero_exit_is_uncertain_counts_one_attempt_and_never_retries(self) -> None:
        self._assert_uncertain_single_attempt(Env(write_returncode=1))

    def test_timeout_is_uncertain_counts_one_attempt_and_never_retries(self) -> None:
        self._assert_uncertain_single_attempt(
            Env(write_raises=subprocess.TimeoutExpired(cmd="gh", timeout=60))
        )

    def test_malformed_success_response_is_uncertain(self) -> None:
        for stdout in ("not json", "[]", "{}", json.dumps({"number": 1, "html_url": "https://x"}),
                       json.dumps({"number": True, "html_url": _url(1)})):
            with self.subTest(stdout=stdout):
                self._assert_uncertain_single_attempt(Env(write_stdout=stdout))

    def test_ordinary_exception_from_delegate_is_uncertain(self) -> None:
        self._assert_uncertain_single_attempt(Env(write_raises=OSError("pipe")))

    def test_base_exceptions_propagate_and_canonical_state_becomes_uncertain(self) -> None:
        class Custom(BaseException):
            pass

        for signal in (KeyboardInterrupt(), SystemExit(3), GeneratorExit(),
                       asyncio.CancelledError(), Custom()):
            with self.subTest(signal=type(signal).__name__):
                env = Env(write_raises=signal)

                with self.assertRaises(type(signal)) as caught:
                    live.execute(armed_gates(), env.deps())

                self.assertIs(caught.exception, signal)
                self.assertEqual(len(env.write_calls), 1)
                self.assertEqual(env.stores[0].release_state(C_ID), ReleaseState.UNCERTAIN)

    def test_cli_reports_interruption_without_swallowing_it(self) -> None:
        env = Env(write_raises=KeyboardInterrupt())
        out, err = io.StringIO(), io.StringIO()

        with redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(KeyboardInterrupt):
                live.main(ARMED_ARGV, env.deps())

        self.assertIn("DO NOT RETRY", err.getvalue())
        self.assertEqual(len(env.write_calls), 1)

    def test_adapter_dne_means_zero_attempts_and_no_second_attempt(self) -> None:
        env = Env(actuator_gh=None)

        result = live.execute(armed_gates(), env.deps())

        self.assertEqual(result.exit_code, live.EXIT_BLOCKED)
        self.assertEqual(env.write_calls, [])
        self.assertEqual(result.facts["fresh"]["reason"], "actuator_definitely_not_executed")
        self.assertIn("adapter refused before any subprocess attempt", result.facts["dne"])
        self.assertEqual(env.stores[0].release_state(C_ID), ReleaseState.CONTAINED)
        self.assertEqual(result.facts["second_release"], "NOT RUN")
        self.assertIn("LIVE WRITE ATTEMPTED: NO", result.lines)

    def test_harness_issues_no_release_call_after_an_uncertain_or_refused_result(self) -> None:
        cases = {
            "uncertain": (Env(write_returncode=1), 4),  # X pre-adoption, probes, fresh
            "dne": (Env(actuator_gh=None), 4),
            "released": (Env(), 5),  # plus the one refused second release
        }
        for name, (env, expected_releases) in cases.items():
            with self.subTest(case=name):
                calls = {"n": 0}

                class CountingBoundary(live.ReleaseBoundary):
                    def release(self, execution_id, consequence_id):
                        calls["n"] += 1
                        return super().release(execution_id, consequence_id)

                with patch.object(live, "ReleaseBoundary", CountingBoundary):
                    live.execute(armed_gates(), env.deps())

                self.assertEqual(calls["n"], expected_releases)
                self.assertLessEqual(len(env.write_calls), 1)

    def test_write_budget_is_exhausted_after_a_single_attempt(self) -> None:
        env = Env(write_returncode=1)
        runner = live.CountedWriteRunner(env.write, TITLE)
        argv = [FAKE_GH, "api", f"repos/{live.TARGET_REPOSITORY}/issues", "--method", "POST",
                "--hostname", "github.com", "-f", f"title={TITLE}", "-f", f"body={live.ISSUE_BODY}"]

        runner(list(argv), shell=False)
        with self.assertRaises(live.WriteRefused):
            runner(list(argv), shell=False)

        self.assertEqual(runner.attempts, 1)
        self.assertEqual(runner.refused, 1)
        self.assertEqual(len(env.write_calls), 1)


class CountedWriteRunnerTests(unittest.TestCase):
    def _argv(self, **changes) -> list[str]:
        argv = [FAKE_GH, "api", f"repos/{live.TARGET_REPOSITORY}/issues", "--method", "POST",
                "--hostname", "github.com", "-f", f"title={TITLE}", "-f", f"body={live.ISSUE_BODY}"]
        for index, value in changes.items():
            argv[int(index)] = value
        return argv

    def test_attempt_is_counted_before_the_delegate_runs(self) -> None:
        seen = []
        runner = None

        def delegate(argv, **kwargs):
            seen.append(runner.attempts)
            raise OSError("boom")

        runner = live.CountedWriteRunner(delegate, TITLE)

        with self.assertRaises(OSError):
            runner(self._argv(), shell=False)

        self.assertEqual(seen, [1])
        self.assertEqual(runner.attempts, 1)

    def test_only_the_exact_fixed_command_is_ever_delegated(self) -> None:
        bad_commands = {
            "other repo": self._argv(**{"2": f"repos/{live.CANONICAL_REPOSITORY}/issues"}),
            "issue 1 patch": self._argv(**{"2": f"repos/{live.TARGET_REPOSITORY}/issues/1",
                                           "4": "PATCH"}),
            "get": self._argv(**{"4": "GET"}),
            "other host": self._argv(**{"6": "example.com"}),
            "other title": self._argv(**{"8": "title=other"}),
            "other body": self._argv(**{"10": "body=other"}),
            "extra arg": self._argv() + ["--input", "-"],
            "not gh": ["bash", *self._argv()[1:]],
        }
        for name, argv in bad_commands.items():
            with self.subTest(command=name):
                calls = []
                runner = live.CountedWriteRunner(lambda a, **k: calls.append(a), TITLE)

                with self.assertRaises(live.WriteRefused):
                    runner(argv, shell=False)

                self.assertEqual(calls, [])
                self.assertEqual(runner.attempts, 0)

    def test_shell_true_is_refused(self) -> None:
        runner = live.CountedWriteRunner(lambda a, **k: None, TITLE)

        with self.assertRaises(live.WriteRefused):
            runner(self._argv(), shell=True)

    def test_unarmed_runner_refuses_everything(self) -> None:
        runner = live.CountedWriteRunner(None, TITLE)

        with self.assertRaises(live.WriteRefused):
            runner(self._argv(), shell=False)

        self.assertEqual(runner.attempts, 0)

    def test_title_and_body_stay_single_argv_data_values(self) -> None:
        env = Env()
        result = live.execute(armed_gates(), env.deps())

        argv = env.write_calls[0]
        self.assertEqual(result.exit_code, live.EXIT_OK)
        self.assertEqual(len(argv), 11)
        self.assertEqual(argv[8], f"title={TITLE}")
        self.assertEqual(argv[10], f"body={live.ISSUE_BODY}")
        self.assertIn(" ", argv[8])  # spaces/brackets did not split the argument


class CommandAndSecretTests(unittest.TestCase):
    def test_live_write_command_is_exactly_the_fixed_target_issue_post(self) -> None:
        env = Env()

        live.execute(armed_gates(), env.deps())

        self.assertEqual(
            env.write_calls,
            [[FAKE_GH, "api", f"repos/{live.TARGET_REPOSITORY}/issues", "--method", "POST",
              "--hostname", "github.com", "-f", f"title={TITLE}", "-f",
              f"body={live.ISSUE_BODY}"]],
        )

    def test_read_only_commands_contain_no_mutation(self) -> None:
        env = Env()

        live.execute(armed_gates(), env.deps())

        self.assertGreater(len(env.read_calls), 0)
        for argv in env.read_calls:
            with self.subTest(argv=argv[-1]):
                self.assertEqual(argv[argv.index("--method") + 1], "GET")
                for token in ("POST", "PUT", "PATCH", "DELETE", "-f", "-F", "--input"):
                    self.assertNotIn(token, argv)

    def test_no_historical_issue_mutation_command_exists(self) -> None:
        env = Env(issue_number=7431)

        live.execute(armed_gates(), env.deps())

        mutating = [a for a in env.all_argv() if any(t in a for t in ("POST", "PUT", "PATCH", "DELETE"))]
        self.assertEqual(len(mutating), 1)
        self.assertEqual(mutating[0][2], f"repos/{live.TARGET_REPOSITORY}/issues")
        for argv in env.all_argv():
            self.assertNotIn(f"repos/{live.TARGET_REPOSITORY}/issues/1", argv)
            self.assertNotIn(f"repos/{live.TARGET_REPOSITORY}/issues/2", argv)

    def test_source_contains_no_mutation_verbs_repo_creation_or_cleanup(self) -> None:
        with open(live.__file__, encoding="utf-8") as handle:
            source = handle.read()

        for forbidden in ('"PATCH"', '"DELETE"', '"PUT"', "repo create", "issue create",
                          "issue close", "gh issue", "state=closed", '"close"', "auth switch",
                          "auth refresh", "auth login"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertEqual(source.count('"POST"'), 1)
        self.assertIsNone(re.search(r"issues/\d", source), "no hard-coded issue numbers")

    def test_reports_never_expose_token_like_secrets(self) -> None:
        env_ok = Env()
        result_ok = live.execute(armed_gates(), env_ok.deps())
        env_fail = Env(read_fail_endpoints={"user"})
        result_fail = live.execute(armed_gates(), env_fail.deps())
        env_write_fail = Env(write_returncode=1)
        result_write_fail = live.execute(armed_gates(), env_write_fail.deps())

        for result, env in ((result_ok, env_ok), (result_fail, env_fail),
                            (result_write_fail, env_write_fail)):
            text = "\n".join(result.lines + env.emitted)
            self.assertNotIn(SECRET, text)
            self.assertNotIn("ghp_", text)
            self.assertNotIn("gho_", text)

    def test_plan_is_emitted_before_the_write(self) -> None:
        env = Env()
        write_seen = []
        original_write = env.write

        def recording_write(argv, **kwargs):
            write_seen.append(list(env.emitted))
            return original_write(argv, **kwargs)

        deps = env.deps()
        deps.write_delegate = recording_write
        live.execute(armed_gates(), deps)

        before_write = "\n".join(write_seen[0])
        for expected in (
            f"LIVE TARGET: {live.TARGET_REPOSITORY}",
            f"TARGET REPOSITORY ID: {live.TARGET_REPOSITORY_ID}",
            f"AUTHENTICATED LOGIN: {live.EXPECTED_LOGIN}",
            f"ISSUE TITLE: {TITLE}",
            "WRITE BUDGET: 1 POST ATTEMPT",
            "RETRY: DISABLED",
            f"CANONICAL REPOSITORY: {live.CANONICAL_REPOSITORY}",
            "LIVE WRITE ATTEMPTED: NO",
        ):
            self.assertIn(expected, before_write)

    def test_live_report_contains_the_required_fields(self) -> None:
        env = Env()

        result = live.execute(armed_gates(), env.deps())

        text = "\n".join(result.lines)
        for field in ("MODE: LIVE", "AUTHORITY PROBE 1:", "AUTHORITY PROBE 2:",
                      "FRESH AUTHORITY RESULT:", "WRITE ATTEMPTS: 1", "ACTUATOR OUTCOME: SUCCEEDED",
                      "RELEASE STATE: RELEASED", "INDEPENDENT VERIFICATION: PASS",
                      "SECOND RELEASE:", "FINAL WRITE ATTEMPTS: 1", "RETRY PERFORMED: NO",
                      "LIVE WRITE ATTEMPTED: YES", f"RUN ID: {RUN_ID}", f"ISSUE TITLE: {TITLE}"):
            self.assertIn(field, text)


if __name__ == "__main__":
    unittest.main()
