"""Contract tests for the read-only consequence-binding MCP tool."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from mcp.server.mcpserver.exceptions import ToolError

from mcp_server import TOOL_ANNOTATIONS, TOOL_NAME, server


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
            patch("reference.swampbox.SwampBoxBoundary.commit") as commit,
            patch("reference.swampbox.SyntheticAdmissionProvider.admit") as admit,
            patch("reference.swampbox.SimulatedActuator.commit") as actuator,
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
