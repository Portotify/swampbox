"""Contract tests for the read-only consequence-binding MCP tool."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecurityMiddleware

from mcp_server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    MCP_PATH,
    STATELESS_HTTP,
    TRANSPORT_SECURITY_HOST_ENV,
    TRANSPORT_SECURITY_ORIGIN_ENV,
    TOOL_ANNOTATIONS,
    TOOL_NAME,
    VERIFIED_PUBLIC_HOST,
    VERIFIED_PUBLIC_ORIGIN,
    _load_server_settings,
    _run_server,
    server,
)


def _consequence(
    *,
    consequence_type: str = "synthetic_task",
    target: str = "target-x",
    payload: object = None,
) -> dict[str, object]:
    return {
        "consequence_type": consequence_type,
        "target": target,
        "payload": {"title": "content-y"} if payload is None else payload,
    }


def _arguments(
    declaration: dict[str, object] | None = None,
    proposed: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "declaration": declaration or _consequence(),
        "proposed": proposed or _consequence(),
    }


def _load_server_settings_for_hosted_binding() -> dict[str, object]:
    with patch.dict(
        os.environ,
        {
            "HOST": "0.0.0.0",
            "PORT": "43123",
            TRANSPORT_SECURITY_HOST_ENV: VERIFIED_PUBLIC_HOST,
            TRANSPORT_SECURITY_ORIGIN_ENV: VERIFIED_PUBLIC_ORIGIN,
        },
        clear=True,
    ):
        return _load_server_settings()


class MCPServerContractTests(unittest.IsolatedAsyncioTestCase):
    async def call_tool(self, arguments: object):
        return await server.call_tool(TOOL_NAME, arguments)  # type: ignore[arg-type]

    async def result_for(self, arguments: dict[str, object]) -> dict[str, object]:
        result = await self.call_tool(arguments)
        self.assertFalse(result.is_error)
        self.assertIsNotNone(result.structured_content)
        return result.structured_content

    async def test_exposes_exactly_one_tool_with_required_contract(self) -> None:
        tools = await server.list_tools()

        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, TOOL_NAME)
        self.assertEqual(
            set(tools[0].input_schema["properties"]),
            {"declaration", "proposed"},
        )
        self.assertEqual(
            set(tools[0].input_schema["$defs"]["ConsequenceInput"]["required"]),
            {"consequence_type", "target", "payload"},
        )
        self.assertEqual(tools[0].annotations, TOOL_ANNOTATIONS)

    async def test_tool_description_exposes_privacy_warning(self) -> None:
        tools = await server.list_tools()

        self.assertIn(
            "Do not include personal or sensitive personal data in consequence inputs.",
            tools[0].description,
        )

    async def test_input_schema_exposes_privacy_guidance(self) -> None:
        tools = await server.list_tools()
        consequence_properties = tools[0].input_schema["$defs"]["ConsequenceInput"]["properties"]

        self.assertEqual(
            consequence_properties["consequence_type"]["description"],
            "Type of consequence to compare. Do not include personal or sensitive personal data.",
        )
        self.assertEqual(
            consequence_properties["target"]["description"],
            "Target of the consequence to compare. Do not include personal or sensitive personal data.",
        )
        self.assertEqual(
            consequence_properties["payload"]["description"],
            "Application-specific material to compare. Do not include personal or sensitive personal data.",
        )

    def test_startup_defaults_preserve_local_behavior(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                _load_server_settings(),
                {
                    "host": DEFAULT_HOST,
                    "port": DEFAULT_PORT,
                    "streamable_http_path": MCP_PATH,
                    "stateless_http": STATELESS_HTTP,
                },
            )

    def test_startup_consumes_explicit_host_and_port(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HOST": "0.0.0.0",
                "PORT": "43123",
                TRANSPORT_SECURITY_HOST_ENV: VERIFIED_PUBLIC_HOST,
                TRANSPORT_SECURITY_ORIGIN_ENV: VERIFIED_PUBLIC_ORIGIN,
            },
            clear=True,
        ):
            settings = _load_server_settings()

        self.assertEqual(settings["host"], "0.0.0.0")
        self.assertEqual(settings["port"], 43123)
        self.assertEqual(settings["streamable_http_path"], MCP_PATH)
        self.assertEqual(settings["stateless_http"], STATELESS_HTTP)

    def test_invalid_port_fails_closed(self) -> None:
        for raw_port in ("not-an-integer", "0", "65536"):
            with self.subTest(raw_port=raw_port):
                with patch.dict(os.environ, {"PORT": raw_port}, clear=True):
                    with self.assertRaises(ValueError):
                        _load_server_settings()

    def test_stateless_http_is_enabled(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = _load_server_settings()

        self.assertIs(settings["stateless_http"], True)

    def test_nonlocal_startup_fails_closed_until_hostname_security_exists(self) -> None:
        with patch.dict(
            os.environ,
            {"HOST": "0.0.0.0", "PORT": "43123"},
            clear=True,
        ):
            with patch("mcp_server.server.run") as run:
                with self.assertRaisesRegex(RuntimeError, "FINAL HOSTNAME REQUIRED"):
                    _run_server()

        run.assert_not_called()

    def test_exact_hosted_transport_security_binding_is_passed_to_sdk(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HOST": "0.0.0.0",
                "PORT": "43123",
                TRANSPORT_SECURITY_HOST_ENV: VERIFIED_PUBLIC_HOST,
                TRANSPORT_SECURITY_ORIGIN_ENV: VERIFIED_PUBLIC_ORIGIN,
            },
            clear=True,
        ):
            with patch("mcp_server.server.run") as run:
                _run_server()

        run.assert_called_once()
        self.assertEqual(run.call_args.args, ("streamable-http",))
        settings = run.call_args.kwargs
        self.assertEqual(settings["host"], "0.0.0.0")
        self.assertEqual(settings["streamable_http_path"], MCP_PATH)
        transport_security = settings["transport_security"]
        self.assertTrue(transport_security.enable_dns_rebinding_protection)
        self.assertEqual(transport_security.allowed_hosts, [VERIFIED_PUBLIC_HOST])
        self.assertEqual(transport_security.allowed_origins, [VERIFIED_PUBLIC_ORIGIN])
        self.assertNotIn("*", transport_security.allowed_hosts)
        self.assertNotIn("*", transport_security.allowed_origins)

    def test_nonlocal_startup_rejects_nonverified_transport_binding(self) -> None:
        for configured_host, configured_origin in (
            ("other.example.com", VERIFIED_PUBLIC_ORIGIN),
            (VERIFIED_PUBLIC_HOST, "https://other.example.com"),
        ):
            with self.subTest(
                configured_host=configured_host,
                configured_origin=configured_origin,
            ):
                with patch.dict(
                    os.environ,
                    {
                        "HOST": "0.0.0.0",
                        TRANSPORT_SECURITY_HOST_ENV: configured_host,
                        TRANSPORT_SECURITY_ORIGIN_ENV: configured_origin,
                    },
                    clear=True,
                ):
                    with self.assertRaisesRegex(RuntimeError, "FINAL HOSTNAME REQUIRED"):
                        _load_server_settings()

    def test_exact_host_and_origin_are_accepted(self) -> None:
        settings = _load_server_settings_for_hosted_binding()
        transport_security = settings["transport_security"]
        middleware = TransportSecurityMiddleware(transport_security)

        self.assertTrue(middleware._validate_host(VERIFIED_PUBLIC_HOST))
        self.assertTrue(middleware._validate_origin(VERIFIED_PUBLIC_ORIGIN))

    def test_unrelated_host_and_origin_are_rejected(self) -> None:
        settings = _load_server_settings_for_hosted_binding()
        middleware = TransportSecurityMiddleware(settings["transport_security"])

        self.assertFalse(middleware._validate_host("other.example.com"))
        self.assertFalse(middleware._validate_origin("https://other.example.com"))

    def test_mcp_path_does_not_change_host_or_origin_matching(self) -> None:
        settings = _load_server_settings_for_hosted_binding()
        middleware = TransportSecurityMiddleware(settings["transport_security"])

        self.assertFalse(middleware._validate_host(f"{VERIFIED_PUBLIC_HOST}/mcp"))
        self.assertFalse(middleware._validate_origin(f"{VERIFIED_PUBLIC_ORIGIN}/mcp"))

    async def test_identical_consequences_are_a_material_match(self) -> None:
        result = await self.result_for(_arguments())

        self.assertEqual(result["result"], "MATERIAL_MATCH")

    async def test_consequence_type_mismatch_is_a_material_mismatch(self) -> None:
        result = await self.result_for(
            _arguments(proposed=_consequence(consequence_type="other_task"))
        )

        self.assertEqual(result["result"], "MATERIAL_MISMATCH")

    async def test_target_mismatch_is_a_material_mismatch(self) -> None:
        result = await self.result_for(
            _arguments(proposed=_consequence(target="target-y"))
        )

        self.assertEqual(result["result"], "MATERIAL_MISMATCH")

    async def test_payload_mismatch_is_a_material_mismatch(self) -> None:
        result = await self.result_for(
            _arguments(proposed=_consequence(payload={"title": "different"}))
        )

        self.assertEqual(result["result"], "MATERIAL_MISMATCH")

    async def test_nested_payload_mismatch_is_a_material_mismatch(self) -> None:
        result = await self.result_for(
            _arguments(
                proposed=_consequence(
                    payload={"nested": {"items": ["one", "different"]}}
                )
            )
        )

        self.assertEqual(result["result"], "MATERIAL_MISMATCH")

    async def test_exact_nested_payload_equality_is_a_material_match(self) -> None:
        nested_payload = {"nested": {"items": ["one", {"count": 2}]}}

        result = await self.result_for(
            _arguments(
                declaration=_consequence(payload=nested_payload),
                proposed=_consequence(payload=nested_payload),
            )
        )

        self.assertEqual(result["result"], "MATERIAL_MATCH")

    async def test_missing_required_fields_fail_closed(self) -> None:
        for field in ("consequence_type", "target", "payload"):
            with self.subTest(field=field):
                malformed = _consequence()
                del malformed[field]
                with self.assertRaises(ToolError):
                    await self.call_tool(_arguments(declaration=malformed))

    async def test_wrong_top_level_input_type_fails_closed(self) -> None:
        with self.assertRaises(ToolError):
            await self.call_tool([])

    async def test_malformed_nested_consequence_fails_closed(self) -> None:
        malformed = {"consequence_type": "synthetic_task", "payload": {}}

        with self.assertRaises(ToolError):
            await self.call_tool(_arguments(declaration=malformed))

    async def test_wrong_material_field_type_is_not_coerced(self) -> None:
        malformed = _consequence()
        malformed["consequence_type"] = 42

        with self.assertRaises(ToolError):
            await self.call_tool(_arguments(declaration=malformed))

    async def test_extra_top_level_fields_are_rejected_for_both_consequences(self) -> None:
        for argument_name in ("declaration", "proposed"):
            with self.subTest(argument_name=argument_name):
                extra = _consequence()
                extra["unexpected"] = "rejected"
                arguments = _arguments()
                arguments[argument_name] = extra

                with self.assertRaises(ToolError):
                    await self.call_tool(arguments)

    async def test_authority_like_extra_field_is_rejected_generically(self) -> None:
        declaration = _consequence()
        declaration["authority"] = "valid"

        with self.assertRaises(ToolError):
            await self.call_tool(_arguments(declaration=declaration))

    async def test_arbitrary_nested_payload_keys_remain_accepted(self) -> None:
        payload = {
            "subject": "hello",
            "body": "world",
            "custom_application_field": 123,
        }

        result = await self.result_for(
            _arguments(
                declaration=_consequence(payload=payload),
                proposed=_consequence(payload=payload),
            )
        )

        self.assertEqual(result["result"], "MATERIAL_MATCH")

    async def test_extra_field_is_rejected_before_canonical_comparator(self) -> None:
        declaration = _consequence()
        declaration["metadata"] = {"caller": "ignored-never"}

        with patch("mcp_server.same_material_consequence") as comparator:
            with self.assertRaises(ToolError):
                await self.call_tool(_arguments(declaration=declaration))

        comparator.assert_not_called()

    async def test_no_commit_admission_or_actuator_path_is_invoked(self) -> None:
        with (
            patch("experiments.legacy_synthetic.SwampBoxBoundary.commit") as commit,
            patch("experiments.legacy_synthetic.SyntheticAdmissionProvider.admit") as admit,
            patch("experiments.legacy_synthetic.SimulatedActuator.commit") as actuator,
        ):
            result = await self.result_for(_arguments())

        self.assertEqual(result["result"], "MATERIAL_MATCH")
        commit.assert_not_called()
        admit.assert_not_called()
        actuator.assert_not_called()

    async def test_repeated_identical_comparison_is_deterministic(self) -> None:
        arguments = _arguments(
            declaration=_consequence(payload={"nested": [1, True, "x"]}),
            proposed=_consequence(payload={"nested": [1, True, "x"]}),
        )

        first = await self.result_for(arguments)
        second = await self.result_for(arguments)

        self.assertEqual(first, second)

    async def test_output_contains_no_authority_like_positive_fields(self) -> None:
        result = await self.result_for(_arguments())
        forbidden = {
            "authorized",
            "approved",
            "safe",
            "may_execute",
            "policy_valid",
            "authority_valid",
            "fresh",
            "admission_valid",
        }

        def keys(value: object) -> set[str]:
            if isinstance(value, dict):
                return set(value) | set().union(*(keys(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(item) for item in value))
            return set()

        self.assertTrue(forbidden.isdisjoint(keys(result)))


if __name__ == "__main__":
    unittest.main()
