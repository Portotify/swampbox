# SwampBox

SwampBox is a small, provider-neutral reference prototype for one consequence
boundary question:

An agent process may end while an artifact containing state, an instruction,
or a proposed task remains available to another consumer. The artifact can
survive the process lifetime without carrying the originating agent's
permission into the future.

This is about consequences surviving a process lifetime, not traditional
sandbox escape.

Persistence is not authority.

## The inheritance scenario

Agent A persists a consequence-capable task and its modeled role ends. Agent B
later encounters the task. The artifact's persistence and origin do not grant
permission to execute it; Agent B must obtain a new admission result before the
downstream consequence may commit.

See the [inheritance experiment](docs/INHERITANCE_EXPERIMENT.md) for the full
synthetic sequence and acceptance matrix.

The prototype models one synthetic flow:

```text
proposed consequence
    -> admission
    -> controlled commit
    -> receipt
```

Only an explicit `ALLOW` for the exact proposed consequence may reach the
simulated actuator. Missing, malformed, `HOLD`, `DENY`, and mismatched
admission states fail closed.

This reference prototype was initiated by Portotify and has no dependency on
Portotify Core.

## What is included

- Python standard library only;
- in-memory artifact storage;
- a deterministic synthetic admission provider;
- an exact-match boundary;
- a non-delivering simulated actuator;
- generic receipts; and
- eight small acceptance tests.

The prototype is intentionally synthetic and in-memory. It does not contact
real APIs, source-control systems, ticket systems, queues, mail systems,
cloud services, or business systems.

## Direct actuator bypass

The simulated actuator exposes a low-level method so the bypass assumption is
visible. The normal flow calls it only through the boundary.

If a real actuator can be reached directly while bypassing the SwampBox
boundary, consequence containment has not been established. This prototype
does not claim to solve that deployment problem.

## Non-claims

This prototype does not claim to solve sandbox escape, prevent all downstream
effects, provide a secure sandbox, provide complete agent security, guarantee
authority, establish production-grade containment, be production ready, or be
an industry standard. It demonstrates only the synthetic boundary behavior
covered by its tests.

## Run

From this directory:

```text
python -m unittest tests.test_swampbox -v
```
