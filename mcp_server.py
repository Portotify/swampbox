"""Read-only MCP server for deterministic consequence binding comparison."""

from __future__ import annotations

import math
import os
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from reference.swampbox import ProposedConsequence, same_material_consequence


MCP_MAX_MATERIAL_DEPTH = 256
MCP_MAX_MATERIAL_NODES = 10_000


def _validate_mcp_payload(payload: Any) -> Any:
    """Validate the bounded JSON-like payload domain without recursion."""

    pending: list[tuple[Any, int, bool]] = [(payload, 0, False)]
    active_containers: set[int] = set()
    visited_nodes = 0

    while pending:
        value, parent_depth, exiting = pending.pop()
        value_type = type(value)
        if exiting:
            active_containers.remove(id(value))
            continue

        visited_nodes += 1
        if visited_nodes > MCP_MAX_MATERIAL_NODES:
            raise ValueError(
                f"payload exceeds MCP material node limit {MCP_MAX_MATERIAL_NODES}"
            )

        if value_type is dict or value_type is list:
            container_depth = parent_depth + 1
            if container_depth > MCP_MAX_MATERIAL_DEPTH:
                raise ValueError(
                    f"payload exceeds MCP material depth {MCP_MAX_MATERIAL_DEPTH}"
                )
            container_id = id(value)
            if container_id in active_containers:
                raise ValueError("payload contains a cycle")
            active_containers.add(container_id)
            pending.append((value, parent_depth, True))
            if value_type is dict:
                children = list(value.items())
                for key, child in reversed(children):
                    if type(key) is not str:
                        raise ValueError("payload dictionary keys must be exact str")
                    pending.append((child, container_depth, False))
            else:
                for child in reversed(value):
                    pending.append((child, container_depth, False))
            continue

        if value is None or value_type is bool or value_type is int or value_type is str:
            continue
        if value_type is float:
            if not math.isfinite(value):
                raise ValueError("payload float values must be finite")
            continue
        raise TypeError("payload contains an unsupported value type")

    return payload


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

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, payload: Any) -> Any:
        return _validate_mcp_payload(payload)


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
VERIFIED_PUBLIC_HOST = "swampbox.onrender.com"
VERIFIED_PUBLIC_ORIGIN = "https://swampbox.onrender.com"
TRANSPORT_SECURITY_HOST_ENV = "SWAMPBOX_TRANSPORT_SECURITY_HOST"
TRANSPORT_SECURITY_ORIGIN_ENV = "SWAMPBOX_TRANSPORT_SECURITY_ORIGIN"
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

    settings: dict[str, object] = {
        "host": host,
        "port": port,
        "streamable_http_path": MCP_PATH,
        "stateless_http": STATELESS_HTTP,
    }

    if host not in {"127.0.0.1", "localhost", "::1"}:
        configured_host = os.environ.get(TRANSPORT_SECURITY_HOST_ENV)
        configured_origin = os.environ.get(TRANSPORT_SECURITY_ORIGIN_ENV)
        if (
            configured_host != VERIFIED_PUBLIC_HOST
            or configured_origin != VERIFIED_PUBLIC_ORIGIN
        ):
            raise RuntimeError(
                "FINAL HOSTNAME REQUIRED FOR TRANSPORT SECURITY: "
                f"set {TRANSPORT_SECURITY_HOST_ENV} and "
                f"{TRANSPORT_SECURITY_ORIGIN_ENV} to the exact verified values"
            )

        settings["transport_security"] = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[VERIFIED_PUBLIC_HOST],
            allowed_origins=[VERIFIED_PUBLIC_ORIGIN],
        )

    return settings


def _run_server() -> None:
    """Run with the SDK's local or explicitly configured transport security."""

    settings = _load_server_settings()
    server.run("streamable-http", **settings)


if __name__ == "__main__":
    _run_server()
