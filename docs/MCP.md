# SwampBox MCP

## Purpose

The optional MCP server performs one narrow, deterministic computation: it
compares a supplied consequence declaration with a proposed consequence using
the existing modeled material fields. MCP is a material-comparison surface
outside the containment and release runtime.

The server constructs the existing `ProposedConsequence` objects and calls
`same_material_consequence(...)`. It does not invoke an actuator, admission
provider, persistence layer, or external service.

## Tool

The server exposes exactly one application tool:

`compare_consequence_binding`

## Input

The tool input has this shape:

```json
{
  "declaration": {
    "consequence_type": "message",
    "target": "demo-inbox",
    "payload": {
      "subject": "hello",
      "body": "world"
    }
  },
  "proposed": {
    "consequence_type": "message",
    "target": "demo-inbox",
    "payload": {
      "subject": "hello",
      "body": "world"
    }
  }
}
```

Both `declaration` and `proposed` require these top-level fields:

- `consequence_type` — a string;
- `target` — a string;
- `payload` — modeled consequence data.

Undeclared top-level fields are rejected. The allowed top-level fields are
exactly `consequence_type`, `target`, and `payload`. The `payload` value is
not recursively restricted by this wrapper, so nested application-specific
keys remain allowed.

## Output

The structured result has exactly this shape:

```json
{
  "result": "MATERIAL_MATCH",
  "comparison_receipt": {
    "comparator": "same_material_consequence",
    "fields": [
      "consequence_type",
      "target",
      "payload"
    ]
  }
}
```

`MATERIAL_MATCH` means the three modeled material fields are equal under the
existing exact comparator. `MATERIAL_MISMATCH` means at least one of those
fields differs.

## Non-authority boundary

A material match does not establish authorization, current authority, policy
validity, safety, or permission to execute. It is only a report that the
modeled material fields match. The tool does not expose
`ExecutionScopedConsequenceStore` or `ReleaseBoundary`, release a contained
consequence, or invoke an actuator.

## Determinism

The same valid modeled inputs produce the same semantic comparison result.
The comparison is read-only and has no external side effect.

## Tool annotations

The tool advertises these MCP annotations:

- read-only: `readOnlyHint=true`;
- non-destructive: `destructiveHint=false`;
- idempotent: `idempotentHint=true`;
- closed-world: `openWorldHint=false`.

## Installation

From the repository root in Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-mcp.txt
```

The requirements file pins the runtime to `mcp==2.2.0`. The commands use the
repository-local virtual environment for installation and execution.

## Running

Start the local server with:

```powershell
.\.venv\Scripts\python.exe mcp_server.py
```

The server uses Streamable HTTP. `mcp_server.py` selects that transport, and
the MCP SDK defaults it to `http://127.0.0.1:8000/mcp` when no host, port, or
path override is supplied.

The repository supports local operation by default and includes guarded hosted
transport configuration. A non-local bind requires the exact configured
transport-security Host and Origin values. Repository code alone does not
prove that a hosted endpoint is deployed.

## Examples

An exact material match:

```json
{
  "declaration": {
    "consequence_type": "message",
    "target": "demo-inbox",
    "payload": {"subject": "hello", "body": "world"}
  },
  "proposed": {
    "consequence_type": "message",
    "target": "demo-inbox",
    "payload": {"subject": "hello", "body": "world"}
  }
}
```

This returns `MATERIAL_MATCH`.

A material mismatch:

```json
{
  "declaration": {
    "consequence_type": "message",
    "target": "demo-inbox",
    "payload": {"subject": "hello", "body": "world"}
  },
  "proposed": {
    "consequence_type": "message",
    "target": "demo-inbox",
    "payload": {"subject": "hello", "body": "different"}
  }
}
```

This returns `MATERIAL_MISMATCH`. Neither example performs an external action.

## Testing

Using the repository-local Python:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_swampbox.py"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_mcp_server_contract.py"
.\.venv\Scripts\python.exe -c "import mcp_server"
```
