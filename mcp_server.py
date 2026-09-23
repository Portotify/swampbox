"""Read-only MCP server for deterministic consequence binding comparison."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, StrictStr

from reference.swampbox import ProposedConsequence, same_material_consequence


class ConsequenceInput(BaseModel):
    """Transport schema for one modeled consequence declaration."""

    model_config = ConfigDict(extra="forbid")

    consequence_type: StrictStr
    target: StrictStr
    payload: Any


class ComparisonReceipt(BaseModel):
    """Receipt for the comparison itself, not for execution or admission."""

    comparator: Literal["same_material_consequence"]
    fields: tuple[
        Literal["consequence_type"],
        Literal["target"],
        Literal["payload"],
    ]


class ComparisonResult(BaseModel):
    """Minimal structured result for the read-only comparison."""

    result: Literal["MATERIAL_MATCH", "MATERIAL_MISMATCH"]
    comparison_receipt: ComparisonReceipt


TOOL_NAME = "compare_consequence_binding"
TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

server = MCPServer(
    name="swampbox-consequence-binding",
    version="0.1.0",
    description="Compare modeled consequence material fields without executing them.",
)


@server.tool(
    name=TOOL_NAME,
    description=(
        "Compare a supplied consequence declaration with a proposed consequence "
        "using exact modeled material fields. This does not establish authority "
        "or permission to execute."
    ),
    annotations=TOOL_ANNOTATIONS,
    structured_output=True,
)
def compare_consequence_binding(
    declaration: ConsequenceInput,
    proposed: ConsequenceInput,
) -> ComparisonResult:
    """Return whether the declaration and proposal have identical material fields."""

    declared_consequence = ProposedConsequence(
        consequence_type=declaration.consequence_type,
        target=declaration.target,
        payload=declaration.payload,
    )
    proposed_consequence = ProposedConsequence(
        consequence_type=proposed.consequence_type,
        target=proposed.target,
        payload=proposed.payload,
    )

    matches = same_material_consequence(
        declared_consequence,
        proposed_consequence,
    )
    return ComparisonResult(
        result="MATERIAL_MATCH" if matches else "MATERIAL_MISMATCH",
        comparison_receipt=ComparisonReceipt(
            comparator="same_material_consequence",
            fields=("consequence_type", "target", "payload"),
        ),
    )


if __name__ == "__main__":
    server.run("streamable-http")
