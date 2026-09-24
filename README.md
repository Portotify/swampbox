# SwampBox

SwampBox is a provider-neutral reference model for containing modeled
consequences, not a process sandbox. A process sandbox isolates running code;
SwampBox models where a consequence may be visible and which execution holds
custody.

The current reference implementation demonstrates execution-scoped consequence
containment, detached snapshots, explicit custody transfer, and a provider-
neutral release boundary. Authority comes from an external provider. SwampBox
binds that decision to the consequence and current custodian, checks material
and finite freshness conditions, and passes the bound snapshot to an actuator.

This is about consequences surviving a process lifetime, not traditional
sandbox escape.

Persistence is not authority.

## Current reference implementation

The in-memory consequence store provides execution-scoped visibility and
custody. A contained consequence has an origin execution and a current
custodian. Reading, submitting, and transferring are scoped to registered
executions, and returned consequence snapshots are detached from internal
mutable state.

Explicit transfer moves custody and visibility from one registered execution to
another. It does not release or execute the consequence, grant permission, or
delegate authority.

`TRANSFER != RELEASE`.

## Consequence lineage

The reference model also preserves a narrow single-parent relationship for
derived consequences. A consequence may name one existing parent consequence
when the submitting execution currently holds that parent's custody and the
parent is still contained. A quarantined parent must first be explicitly
adopted; inspection alone is not custody.

In the minimal lifecycle, A creates X, A ends, B adopts X, and B may submit C
with X as its parent. The parent edge is preserved when custody later moves,
but it is provenance metadata, not material effect identity. Persistence is
not inheritance, adoption is not authority, and lineage is not authority.

X's authority does not become C's authority. A derived consequence still
requires its own fresh external authority decision before it can cross the
supported release boundary. Multi-parent lineage and ancestry traversal are
not supported.

## Supported release boundary

A contained consequence does not become released merely because it exists,
matches material, or has a current custodian. The supported `ReleaseBoundary`
path requires an external, provider-neutral authority decision bound to the
consequence, current custodian, and exact modeled material. The boundary
rechecks custody, material, and finite authority freshness immediately before
supported actuation. An `ALLOW` result alone is not actuator success.

The actuator reports the result of the supported attempt:

- `CONTAINED` means the consequence did not successfully cross the supported
  actuator boundary;
- `RELEASED` means the actuator reported definite success;
- `UNCERTAIN` means the external effect may have occurred, but its outcome
  could not be established. `UNCERTAIN` is not equivalent to failure and is
  not an automatic retry signal.

This release path is an in-memory, cooperative reference model. It does not
authenticate execution identities, provide durable crash reconciliation or
distributed atomicity, guarantee exactly-once external effects, or enforce
universal routing of every effect-capable path through SwampBox.

## Why this problem matters

The earlier inheritance scenario remains useful evidence for the narrower
consequence-survival thesis:

An agent process may end while an artifact containing state, an instruction,
or a proposed task remains available to another consumer. The artifact can
survive the process lifetime without carrying the originating agent's
permission into the future.

## Legacy inheritance scenario

Agent A persists a consequence-capable task and its modeled role ends. Agent B
later encounters the task. The artifact's persistence and origin do not grant
permission to execute it; Agent B must obtain a new admission result before the
downstream consequence may commit.

See the [inheritance experiment](docs/INHERITANCE_EXPERIMENT.md) for the full
synthetic sequence and acceptance matrix.

The legacy synthetic prototype models one synthetic flow:

```text
proposed consequence
    -> admission
    -> controlled commit
    -> receipt
```

Only an explicit `ALLOW` for the exact proposed consequence may reach the
simulated actuator. Missing, malformed, `HOLD`, `DENY`, and mismatched
admission states fail closed. This legacy admission path is separate from the
provider-neutral `ReleaseBoundary` path described above.

SwampBox is an open-source project by Portotify. The reference experiments
remain provider-neutral and have no dependency on Portotify Core.

## Optional MCP surface

The repository also includes an optional MCP material-comparison surface with
exactly one tool, `compare_consequence_binding`. It deterministically compares
the modeled `consequence_type`, `target`, and `payload` fields of a supplied
declaration and proposal, returning `MATERIAL_MATCH` or
`MATERIAL_MISMATCH`. It is outside the containment and release runtime: it
does not expose the consequence store or `ReleaseBoundary`, establish
authority, grant permission, release a consequence, or invoke an actuator.

See the [MCP contract](docs/MCP.md) for the exact schema, semantics, and
local Streamable HTTP instructions.

From the repository root, install the pinned MCP runtime in a local virtual
environment and start the server with:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-mcp.txt
.\.venv\Scripts\python.exe mcp_server.py
```

## What is included

### Synthetic inheritance experiment

The synthetic inheritance path is the simplest conceptual entry point. It is
implemented with the Python standard library, in-memory artifact storage, a
deterministic synthetic admission provider, an exact-match boundary, a
non-delivering simulated actuator, and generic receipts.

This synthetic path is intentionally in-memory and does not contact real APIs,
source-control systems, ticket systems, queues, mail systems, cloud services,
or business systems. Its [inheritance experiment](docs/INHERITANCE_EXPERIMENT.md)
documents the original synthetic sequence and acceptance matrix. Its legacy
admission and simulated-actuation code lives in
`experiments/legacy_synthetic.py`, outside the canonical `reference.swampbox`
release surface.

### Controlled GitHub effect experiment

This is a separate controlled experiment path, not universal interception and
not the provider-neutral `ReleaseBoundary` runtime. SwampBox admitted an exact
GitHub issue consequence, crossed the create boundary once without retry, and a
later independent read found exactly one matching issue; immediate post-create
verification remained uncertain.

The [controlled GitHub effect experiment](docs/GITHUB_EFFECT_EXPERIMENT.md) is
an explicit opt-in reference path. The default tests remain offline. The live
path requires GitHub CLI (`gh`) to already be available and authenticated for
the operator-controlled target. Authentication is not modeled as consequence
authority. The consequence is the exact repository/title/body contract; the
path performs no automatic create retry. A direct bypass remains outside the
demonstrated containment.

### Live external-effect validation

A later controlled run used the canonical consequence lifecycle and the
provider-neutral `ReleaseBoundary` against a disposable private GitHub
repository. An authority decision bound to the parent consequence, and one
rebound to the derived consequence but carrying the parent's material, were
each rejected by the boundary with no external write. A fresh decision bound to
the derived consequence then permitted one GitHub issue-creation attempt, and a
read-only check observed the issue. In this experiment, lineage did not
substitute for release authority.

This was one run with one effect and a scripted in-process authority provider.
It did not compare a real sandbox, inject authority revocation, exercise crash
recovery, or establish production readiness. See the
[experiment record](docs/GITHUB_EFFECT_EXPERIMENT.md) for methodology, evidence
identifiers, and limits.

## Direct actuator bypass

The simulated actuator exposes a low-level method so the bypass assumption is
visible. The normal flow calls it only through the boundary.

If a real actuator can be reached directly while bypassing the SwampBox
boundary, consequence containment has not been established. The synthetic
prototype does not claim to solve that deployment problem. The controlled
GitHub experiment likewise does not prevent a direct bypass outside the
demonstrated path.

## Non-claims

These reference paths do not claim to solve sandbox escape, prevent all
downstream effects, provide a secure sandbox, provide complete agent security,
grant or independently determine authority, establish production-grade
containment, be production ready, or be an industry standard. They do not
claim exactly-once behavior, prevention of every duplicate consequence,
universal interception, direct-bypass prevention, or immediate verified
success. The synthetic and GitHub paths retain the narrower scopes described
in their experiment documents.

## Run

From this directory:

```text
python -m unittest tests.test_swampbox -v
```

The complete public offline suite is:

```text
python -m unittest discover -s tests -v
```

The live GitHub path is explicit and manual. Public documentation does not
provide a copy-paste mutation command.
