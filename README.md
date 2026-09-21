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

**Controlled external-effect experiment:** SwampBox admitted an exact GitHub issue consequence, crossed the create boundary once without retry, and a later independent read found exactly one matching issue; immediate post-create verification remained uncertain.

[Read the controlled GitHub effect experiment ](docs/GITHUB_EFFECT_EXPERIMENT.md)

## The inheritance scenario

Agent A persists a consequence-capable task and its modeled role ends. Agent B
later encounters the task. The artifact's persistence and origin do not grant
permission to execute it; Agent B must obtain a new admission result before the
downstream consequence may commit.

See the [inheritance experiment](docs/INHERITANCE_EXPERIMENT.md) for the full
synthetic sequence and acceptance matrix.

The synthetic prototype models one synthetic flow:

```text
proposed consequence
    -> admission
    -> controlled commit
    -> receipt
```

Only an explicit `ALLOW` for the exact proposed consequence may reach the
simulated actuator. Missing, malformed, `HOLD`, `DENY`, and mismatched
admission states fail closed.

SwampBox was initiated by Portotify. The reference experiments remain
provider-neutral and have no dependency on Portotify Core.

## What is included

### Synthetic inheritance experiment

The synthetic inheritance path is the simplest conceptual entry point. It is
implemented with the Python standard library, in-memory artifact storage, a
deterministic synthetic admission provider, an exact-match boundary, a
non-delivering simulated actuator, and generic receipts.

This synthetic path is intentionally in-memory and does not contact real APIs,
source-control systems, ticket systems, queues, mail systems, cloud services,
or business systems. Its [inheritance experiment](docs/INHERITANCE_EXPERIMENT.md)
documents the original synthetic sequence and acceptance matrix.

### Controlled GitHub effect experiment

SwampBox admitted an exact GitHub issue consequence, crossed the create boundary once without retry, and a later independent read found exactly one matching issue; immediate post-create verification remained uncertain.

The [controlled GitHub effect experiment](docs/GITHUB_EFFECT_EXPERIMENT.md) is
an explicit opt-in reference path. The default tests remain offline. The live
path requires GitHub CLI (`gh`) to already be available and authenticated for
the operator-controlled target. Authentication is not modeled as consequence
authority. The consequence is the exact repository/title/body contract; the
path performs no automatic create retry. A direct bypass remains outside the
demonstrated containment.

## Direct actuator bypass

The simulated actuator exposes a low-level method so the bypass assumption is
visible. The normal flow calls it only through the boundary.

If a real actuator can be reached directly while bypassing the SwampBox
boundary, consequence containment has not been established. The synthetic
prototype does not claim to solve that deployment problem. The controlled
GitHub experiment likewise does not prevent a direct bypass outside the
demonstrated path.

## Non-claims

These reference experiments do not claim to solve sandbox escape, prevent all
downstream effects, provide a secure sandbox, provide complete agent security,
guarantee authority, establish production-grade containment, be production
ready, or be an industry standard. They do not claim exactly-once behavior,
prevention of every duplicate consequence, direct-bypass prevention, or
immediate verified success. The synthetic path demonstrates only its covered
boundary behavior; the controlled GitHub path reports the limited result and
uncertainty described in its experiment document.

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
