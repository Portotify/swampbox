"""Read-only MCP server for deterministic consequence binding comparison."""

from __future__ import annotations

import os
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from reference.swampbox import ProposedConsequence, same_material_consequence


class ConsequenceInput(BaseModel):
    """Transport schema for one modeled consequence declaration."""

    model_config = ConfigDict(extra="forbid")

    consequence_type: StrictStr = Field(
        description="Type of consequence to compare. Do not include personal or sensitive personal data."
    )
    target: StrictStr = Field(
        description="Target of the consequence to compare. Do not include personal or sensitive personal data."
    )
    payload: Any = Field(
        description="Application-specific material to compare. Do not include personal or sensitive personal data."
    )


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
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
MCP_PATH = "/mcp"
STATELESS_HTTP = True
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
        "or permission to execute. Do not include personal or sensitive personal "
        "data in consequence inputs."
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


def _load_server_settings() -> dict[str, object]:
    """Load the narrow Streamable HTTP startup contract."""

    host = os.environ.get("HOST", DEFAULT_HOST)
    raw_port = os.environ.get("PORT")
    if raw_port is None:
        port = DEFAULT_PORT
    else:
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ValueError("PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be between 1 and 65535")

    return {
        "host": host,
        "port": port,
        "streamable_http_path": MCP_PATH,
        "stateless_http": STATELESS_HTTP,
    }


def _run_server() -> None:
    """Run locally until the final hosted Host/Origin allowlist is known."""

    settings = _load_server_settings()
    if settings["host"] not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            "FINAL HOSTNAME REQUIRED FOR TRANSPORT SECURITY: "
            "configure explicit Host/Origin protection before non-local bind"
        )
    server.run("streamable-http", **settings)


if __name__ == "__main__":
    _run_server()
