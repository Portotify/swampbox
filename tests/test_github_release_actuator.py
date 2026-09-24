"""Offline tests for the Step 13B GitHub release actuator and harness.

No test spawns a process, calls gh, or uses the network: every runner and
executable resolver is an injected fake.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from unittest.mock import patch

from experiments import validation_13b
from experiments.github_release_actuator import (
    CREATE_GITHUB_ISSUE,
    GitHubReleaseActuator,
)
from reference.swampbox import (
    ActuatorOutcome,
    ExecutionScopedConsequenceStore,
    ProposedConsequence,
    ReleaseBoundary,
    ReleaseState,
)


TARGET = "Portotify/swampbox-effect-lab"
FAKE_GH = os.path.abspath(os.path.join(os.sep, "offline-fake", "gh.exe"))
NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _url(number: int, repository: str = TARGET) -> str:
    return f"https://github.com/{repository}/issues/{number}"


def _valid_stdout(number: int = 42) -> str:
    return json.dumps({"number": number, "html_url": _url(number)})


class FakeRunner:
    """Injected stand-in for subprocess.run; never spawns anything."""

    def __init__(self, *, returncode=0, stdout=None, raises=None) -> None:
        self.returncode = returncode
        self.stdout = _valid_stdout() if stdout is None else stdout
        self.raises = raises
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


class Consequence:
    """Minimal duck-typed consequence snapshot."""

    def __init__(
        self,
        consequence_type=CREATE_GITHUB_ISSUE,
        target=TARGET,
        payload=None,
    ) -> None:
        self.consequence_type = consequence_type
        self.target = target
        self.payload = {"title": "t", "body": "b"} if payload is None else payload


def _actuator(runner: FakeRunner, resolver=None) -> GitHubReleaseActuator:
    return GitHubReleaseActuator(
        TARGET,
        disposable_target_confirmed=True,
        runner=runner,
        resolve_executable=resolver or (lambda name: FAKE_GH),
    )


class ActuatorConstructionTests(unittest.TestCase):
    def test_requires_explicit_disposable_confirmation(self) -> None:
        with self.assertRaises(ValueError):
            GitHubReleaseActuator(TARGET)
        with self.assertRaises(ValueError):
            GitHubReleaseActuator(TARGET, disposable_target_confirmed=1)

    def test_requires_exact_owner_repo_target(self) -> None:
        for bad in ("", "owner", "owner/repo/extra", "*/repo", "owner/*", "a b/c", None):
            with self.subTest(target=bad):
                with self.assertRaises(ValueError):
                    GitHubReleaseActuator(bad, disposable_target_confirmed=True)

    def test_requires_positive_timeout(self) -> None:
        for bad in (0, -1, None, "60", True):
            with self.subTest(timeout=bad):
                with self.assertRaises(ValueError):
                    GitHubReleaseActuator(
                        TARGET,
                        disposable_target_confirmed=True,
                        timeout_seconds=bad,
                    )


class ActuatorSucceededTests(unittest.TestCase):
    def test_valid_consequence_and_valid_post_response_succeeds(self) -> None:
        runner = FakeRunner()

        outcome = _actuator(runner).actuate(Consequence())

        self.assertIs(outcome, ActuatorOutcome.SUCCEEDED)
        self.assertEqual(len(runner.calls), 1)

    def test_argv_is_fixed_and_shell_free(self) -> None:
        runner = FakeRunner()
        _actuator(runner).actuate(Consequence(payload={"title": "T", "body": "B"}))

        argv, kwargs = runner.calls[0]
        self.assertEqual(
            argv,
            [
                FAKE_GH,
                "api",
                f"repos/{TARGET}/issues",
                "--method",
                "POST",
                "--hostname",
                "github.com",
                "-f",
                "title=T",
                "-f",
                "body=B",
            ],
        )
        self.assertIs(kwargs["shell"], False)
        self.assertIs(kwargs["check"], False)
        self.assertEqual(kwargs["timeout"], 60)
        self.assertIsInstance(argv, list)

    def test_no_read_or_verification_command_is_issued(self) -> None:
        runner = FakeRunner()
        _actuator(runner).actuate(Consequence())

        self.assertEqual(len(runner.calls), 1)
        argv = runner.calls[0][0]
        self.assertEqual(argv[1], "api")
        self.assertIn("POST", argv)
        self.assertNotIn("GET", argv)
        self.assertNotIn("view", argv)
        self.assertNotIn("list", argv)

    def test_shell_like_text_stays_single_argv_data_values(self) -> None:
        title = 'a"; rm -rf / & echo $(x) `y` %PATH% | > out'
        body = "line1\nline2 --method GET -f evil=1 ; && ||"
        runner = FakeRunner()

        outcome = _actuator(runner).actuate(
            Consequence(payload={"title": title, "body": body})
        )

        self.assertIs(outcome, ActuatorOutcome.SUCCEEDED)
        argv, kwargs = runner.calls[0]
        self.assertEqual(len(argv), 11)
        self.assertEqual(argv[8], f"title={title}")
        self.assertEqual(argv[10], f"body={body}")
        self.assertEqual(argv[:8], [FAKE_GH, "api", f"repos/{TARGET}/issues",
                                    "--method", "POST", "--hostname", "github.com", "-f"])
        self.assertIs(kwargs["shell"], False)


class ActuatorDefiniteNotExecutedTests(unittest.TestCase):
    def _assert_dne(self, consequence, resolver=None) -> None:
        runner = FakeRunner()

        outcome = _actuator(runner, resolver).actuate(consequence)

        self.assertIs(outcome, ActuatorOutcome.DEFINITE_NOT_EXECUTED)
        self.assertEqual(runner.calls, [])

    def test_invalid_consequence_type(self) -> None:
        for bad in ("OTHER", "", None, 7):
            with self.subTest(consequence_type=bad):
                self._assert_dne(Consequence(consequence_type=bad))

    def test_target_mismatch(self) -> None:
        for bad in ("Portotify/other", "portotify/swampbox-effect-lab", TARGET + "/", None):
            with self.subTest(target=bad):
                self._assert_dne(Consequence(target=bad))

    def test_malformed_payload(self) -> None:
        bad_payloads = {
            "tuple": ("t", "b"),
            "list": ["t", "b"],
            "none": None,
            "string": "title=t",
            "extra key": {"title": "t", "body": "b", "labels": "x"},
            "missing body": {"title": "t"},
            "missing title": {"body": "b"},
            "empty title": {"title": "", "body": "b"},
            "empty body": {"title": "t", "body": ""},
            "blank title": {"title": "  ", "body": "b"},
            "non-str title": {"title": 1, "body": "b"},
            "non-str body": {"title": "t", "body": None},
            "nul in title": {"title": "a\x00b", "body": "b"},
            "nul in body": {"title": "t", "body": "a\x00b"},
        }
        for name, payload in bad_payloads.items():
            with self.subTest(payload=name):
                consequence = Consequence()
                consequence.payload = payload
                self._assert_dne(consequence)

    def test_gh_lookup_missing(self) -> None:
        self._assert_dne(Consequence(), resolver=lambda name: None)
        self._assert_dne(Consequence(), resolver=lambda name: "")

    def test_gh_lookup_exception_before_spawn_is_definite_not_executed(self) -> None:
        def broken_resolver(name):
            raise OSError("lookup failed")

        self._assert_dne(Consequence(), resolver=broken_resolver)

    def test_object_without_consequence_attributes_is_definite_not_executed(self) -> None:
        self._assert_dne(object())

    @unittest.skipUnless(sys.platform == "win32", "Windows shim rule")
    def test_windows_cmd_shim_is_not_accepted_as_gh(self) -> None:
        for shim in ("C:\\tools\\gh.cmd", "C:\\tools\\gh.bat", "C:\\tools\\gh"):
            with self.subTest(shim=shim):
                self._assert_dne(Consequence(), resolver=lambda name, s=shim: s)


class ActuatorUncertainTests(unittest.TestCase):
    def _assert_uncertain(self, runner: FakeRunner) -> None:
        outcome = _actuator(runner).actuate(Consequence())

        self.assertIs(outcome, ActuatorOutcome.UNCERTAIN)
        self.assertEqual(len(runner.calls), 1)

    def test_nonzero_exit(self) -> None:
        for code in (1, 2, 4, -1, 128):
            with self.subTest(returncode=code):
                self._assert_uncertain(FakeRunner(returncode=code))

    def test_nonzero_exit_with_valid_looking_stdout_is_not_success(self) -> None:
        self._assert_uncertain(FakeRunner(returncode=1, stdout=_valid_stdout()))

    def test_non_int_returncode(self) -> None:
        for bad in (True, None, "0", 0.0):
            with self.subTest(returncode=bad):
                self._assert_uncertain(FakeRunner(returncode=bad))

    def test_empty_stdout(self) -> None:
        self._assert_uncertain(FakeRunner(stdout=""))

    def test_non_str_stdout(self) -> None:
        self._assert_uncertain(FakeRunner(stdout=_valid_stdout().encode("utf-8")))

    def test_malformed_json(self) -> None:
        for bad in ("{", "not json", "{'number': 1}", '{"number": 1,'):
            with self.subTest(stdout=bad):
                self._assert_uncertain(FakeRunner(stdout=bad))

    def test_json_not_an_object(self) -> None:
        for bad in ("[]", '[{"number": 1}]', "42", '"text"', "null", "true"):
            with self.subTest(stdout=bad):
                self._assert_uncertain(FakeRunner(stdout=bad))

    def test_number_missing_or_invalid(self) -> None:
        cases = {
            "missing": {"html_url": _url(1)},
            "bool true": {"number": True, "html_url": _url(1)},
            "bool false": {"number": False, "html_url": _url(0)},
            "zero": {"number": 0, "html_url": _url(0)},
            "negative": {"number": -5, "html_url": _url(-5)},
            "float": {"number": 1.0, "html_url": _url(1)},
            "string": {"number": "1", "html_url": _url(1)},
            "null": {"number": None, "html_url": _url(1)},
        }
        for name, payload in cases.items():
            with self.subTest(number=name):
                self._assert_uncertain(FakeRunner(stdout=json.dumps(payload)))

    def test_html_url_missing_or_invalid(self) -> None:
        cases = {
            "missing": {"number": 7},
            "null": {"number": 7, "html_url": None},
            "non-string": {"number": 7, "html_url": 7},
            "empty": {"number": 7, "html_url": ""},
        }
        for name, payload in cases.items():
            with self.subTest(html_url=name):
                self._assert_uncertain(FakeRunner(stdout=json.dumps(payload)))

    def test_wrong_repository_url(self) -> None:
        for repository in ("Portotify/other", "other/swampbox-effect-lab",
                           "portotify/swampbox-effect-lab"):
            with self.subTest(repository=repository):
                stdout = json.dumps({"number": 7, "html_url": _url(7, repository)})
                self._assert_uncertain(FakeRunner(stdout=stdout))

    def test_bool_number_is_rejected_even_with_matching_string_url(self) -> None:
        # str(True) == "True": a URL built from the bool would otherwise match.
        stdout = '{"number": true, "html_url": "%s"}' % _url("True")

        self._assert_uncertain(FakeRunner(stdout=stdout))

    def test_url_number_must_match_number(self) -> None:
        stdout = json.dumps({"number": 7, "html_url": _url(8)})
        self._assert_uncertain(FakeRunner(stdout=stdout))

    def test_url_variants_are_not_accepted(self) -> None:
        base = _url(7)
        variants = {
            "query": base + "?x=1",
            "fragment": base + "#issuecomment-1",
            "trailing slash": base + "/",
            "http": base.replace("https://", "http://"),
            "other host": base.replace("github.com", "example.com"),
            "www host": base.replace("github.com", "www.github.com"),
            "api url": f"https://api.github.com/repos/{TARGET}/issues/7",
            "pull path": base.replace("/issues/", "/pull/"),
            "whitespace": base + " ",
            "leading whitespace": " " + base,
        }
        for name, url in variants.items():
            with self.subTest(url=name):
                stdout = json.dumps({"number": 7, "html_url": url})
                self._assert_uncertain(FakeRunner(stdout=stdout))

    def test_ordinary_exception_after_boundary(self) -> None:
        for error in (RuntimeError("boom"), OSError("pipe"), BrokenPipeError(),
                      FileNotFoundError("gh"), PermissionError("denied"),
                      UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")):
            with self.subTest(error=type(error).__name__):
                self._assert_uncertain(FakeRunner(raises=error))

    def test_timeout_after_boundary(self) -> None:
        self._assert_uncertain(
            FakeRunner(raises=subprocess.TimeoutExpired(cmd="gh", timeout=60))
        )

    def test_no_retry_after_uncertain(self) -> None:
        runner = FakeRunner(returncode=1)
        _actuator(runner).actuate(Consequence())

        self.assertEqual(len(runner.calls), 1)

    def test_exit_zero_json_with_extra_fields_still_succeeds(self) -> None:
        stdout = json.dumps({"number": 9, "html_url": _url(9), "title": "x", "id": 1})

        outcome = _actuator(FakeRunner(stdout=stdout)).actuate(Consequence())

        self.assertIs(outcome, ActuatorOutcome.SUCCEEDED)


class CustomBaseException(BaseException):
    pass


class ActuatorBaseExceptionTests(unittest.TestCase):
    def test_runner_base_exceptions_propagate_unchanged(self) -> None:
        signals = (
            KeyboardInterrupt(),
            SystemExit(3),
            GeneratorExit(),
            asyncio.CancelledError(),
            CustomBaseException(),
        )
        for signal in signals:
            with self.subTest(signal=type(signal).__name__):
                runner = FakeRunner(raises=signal)

                with self.assertRaises(type(signal)) as caught:
                    _actuator(runner).actuate(Consequence())

                self.assertIs(caught.exception, signal)
                self.assertEqual(len(runner.calls), 1)

    def test_keyboard_interrupt_through_release_boundary_marks_uncertain(self) -> None:
        signal = KeyboardInterrupt()
        runner = FakeRunner(raises=signal)
        store = ExecutionScopedConsequenceStore()
        store.register_execution("exec-1")
        store.submit(
            "exec-1",
            "consequence-1",
            ProposedConsequence(
                CREATE_GITHUB_ISSUE, TARGET, {"title": "t", "body": "b"}
            ),
        )
        provider = validation_13b.ScriptedAuthorityProvider()
        provider.queue(
            lambda snapshot, at: validation_13b._allow_decision(
                "decision-1",
                snapshot.consequence_id,
                snapshot.current_execution_id,
                validation_13b._material(snapshot),
                at,
            )
        )
        boundary = ReleaseBoundary(store, provider, _actuator(runner), clock=lambda: NOW)

        with self.assertRaises(KeyboardInterrupt) as caught:
            boundary.release("exec-1", "consequence-1")

        self.assertIs(caught.exception, signal)
        self.assertEqual(store.release_state("consequence-1"), ReleaseState.UNCERTAIN)
        self.assertEqual(len(runner.calls), 1)


class ValidationHarnessTests(unittest.TestCase):
    def test_harness_runs_offline_and_reports_deterministically(self) -> None:
        first = validation_13b.run()
        second = validation_13b.run()

        self.assertEqual(first, second)
        text = "\n".join(first)
        self.assertIn("fake actuator count: 1", text)
        self.assertIn("authority_consequence_mismatch", text)
        self.assertIn("authority_material_mismatch", text)
        self.assertIn("C final release state: RELEASED", text)
        self.assertIn("second release: consequence_not_releasable", text)
        self.assertIn("no real sandbox claim", text)

    def test_harness_main_is_zero_on_success_and_nonzero_on_failure(self) -> None:
        with patch.object(validation_13b, "run", return_value=["ok"]):
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(validation_13b.main(), 0)
        self.assertIn("all invariants held", out.getvalue())

        failing = patch.object(
            validation_13b,
            "run",
            side_effect=validation_13b.HarnessInvariantError("forced"),
        )
        with failing, redirect_stdout(io.StringIO()) as out, redirect_stderr(
            io.StringIO()
        ) as err:
            self.assertEqual(validation_13b.main(), 1)
        self.assertNotIn("all invariants held", out.getvalue())
        self.assertIn("forced", err.getvalue())


if __name__ == "__main__":
    unittest.main()
