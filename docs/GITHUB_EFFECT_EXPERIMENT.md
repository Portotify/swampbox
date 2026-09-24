# Controlled GitHub Effect Experiment

## Purpose

This document describes a controlled reference experiment for one consequence
boundary. An exact proposed GitHub issue consequence is admitted, checked
against this experiment's exact consequence contract, and allowed to cross to
an external actuator only through this experiment's demonstrated SwampBox
path.

> Scope: this is a separate controlled experiment path. It is not universal
> interception and it is not the provider-neutral `ReleaseBoundary` runtime.
> Its synthetic local admission remains specific to this experiment.

This is a narrow reference experiment. It does not generalize to arbitrary
external effects or to production containment.

> Records in this document: the sections from "Purpose" through "Non-claims"
> describe the earlier controlled experiment, and the scope note above applies
> to that record only. The final major section, "2026-09-24 — Step 13B live
> canonical release validation", records a later run that did use the
> provider-neutral `ReleaseBoundary` path. The two records are kept separate;
> neither record's claims are merged into the other's.

## Consequence contract

The material consequence is exactly:

```text
repository
title
body
```

The repository, title, and body are compared literally. Matching is exact;
semantic or fuzzy comparison is not used. There is no fourth consequence
field.

## Controlled flow

```text
exact proposed consequence
    -> complete pre-create observation
    -> synthetic admission
    -> exact consequence gate
    -> at most one create attempt
    -> fresh external observation
```

The pre-create observation establishes the starting external state. It is not
admission. In this experiment, admission is a synthetic local result and is
not organizational authorization. The phrase "fresh external observation"
below refers to an external-state read, not authority freshness.

## Experiment result

In a controlled reference experiment, SwampBox admitted one exact GitHub issue consequence and crossed the create boundary once. The immediate post-create read returned zero exact matches, so the run remained uncertain; a later independent read found exactly one matching issue in GitHub external state.

The factual chronology was:

- pre-create exact count = 0;
- synthetic admission = `ALLOW`;
- exact gate = `PASS`;
- create boundary crossed once;
- immediate post-create exact count = 0;
- create retries = 0;
- the historical run remained `UNCERTAIN`;
- a later separate forensic read found exactly one open exact repository/title/body match.

The later forensic read is separate evidence. It does not convert the
immediate zero-match observation into an immediate success result.

## Why no retry

The create boundary had already been crossed while the immediate external
state did not establish success. A second create could have created a duplicate
consequence. Therefore this controlled run did not retry the create.
This describes the behavior of this experiment; it is not a universal
duplicate-prevention claim.

## Verification semantics

Here, an "independent read" means a fresh GitHub external-state read separate
from create-return data. It does not mean a third-party audit, cryptographic
proof, or formal verification.

## Limitations

The experiment has these limits:

- the target was a controlled disposable GitHub repository;
- admission was synthetic and local;
- matching used the exact repository/title/body contract;
- immediate verification remained uncertain;
- later evidence was separate forensic evidence;
- create attempts = 1;
- there were zero create retries;
- the experiment makes no exactly-once claim;
- it makes no production containment claim; and
- a direct bypass remains possible outside the demonstrated path.

The cause of the immediate-zero / later-one observation is not established.
The create exit class is not established. Read-after-write delay may be
possible, but it is not proven.

The live runner is an explicit, manual path. This document records the frozen
experiment result and does not provide an operational mutation command. Any
reference here to a release contract is specific to this controlled experiment
unless explicitly stated otherwise.

## Non-claims

This experiment does not claim:

- historical `VERIFIED_SUCCESS`;
- immediate verification success;
- exactly-once behavior;
- guaranteed duplicate prevention;
- production containment;
- direct-bypass prevention;
- sandbox escape prevention;
- universal agent security;
- cryptographic causality;
- organizational authorization;
- or eventual consistency as the proven cause.

## 2026-09-24 — Step 13B live canonical release validation

This section is the evidence record of one controlled live run. It separates
what was observed from how it is interpreted and from what it does not
establish. It is not a security assessment and not a certification.

### 1. Question

Can a consequence survive the end of one execution, remain unable to propagate
through the canonical custody/release path, be explicitly adopted by another
execution, derive a child consequence, and produce one real external effect
only after authority bound to that child passes the release boundary?

The question is deliberately narrow. It does not ask whether SwampBox makes
agent systems safe.

### 2. Experimental boundary

- controlled experiment, one run, one external effect;
- disposable private GitHub repository, no production data;
- no real sandbox or process-isolation runtime was involved or compared;
- no production workload;
- no retry, and a budget of one POST attempt;
- the offline baseline (`experiments/validation_13b.py`) remained offline, and
  no second live issue was created for comparison;
- existing historical issues in the target repository were not modified, and
  the harness issued no command against them.

### 3. Setup

- Harness: `experiments/validation_13b_live.py` at commit
  `bc297d5e5293c49fb437cb4211bc7c1029e0672d`. It is experiment code, not a
  SwampBox capability. Its offline behavior is covered by
  `tests/test_validation_13b_live.py`.
- Target: `Portotify/swampbox-effect-lab`, repository ID `1377745355`, private.
  The canonical repository `Portotify/swampbox` was checked to be a different
  repository and was excluded as a target.
- Effect: one GitHub issue creation through the experiment adapter
  `experiments/github_release_actuator.py`, which runs `gh api` with a POST
  method. The adapter reports effect finality only; it does not decide
  authority, custody, or lineage.
- Authority: a scripted `AuthorityProvider` implemented inside the harness and
  running in the same process. It is an experiment stand-in, not an
  independent policy engine or a human approver.
- Write counting: a wrapper around the adapter's subprocess runner counted POST
  attempts, incrementing before the command that could perform the POST.
  Counts below are that wrapper's counts, not network captures or GitHub audit
  data.
- Pre-write checks, run once up front and again immediately before the fresh
  authority evaluation: HEAD equals the supplied reviewed commit; clean
  worktree; the active GitHub login (from a read-only API query) is the pinned
  login; target full name, repository ID, and node ID match the pinned values;
  target private and not archived; issues enabled; canonical repository
  identity verified and distinct; planned title unused among open and closed
  issues; the `gh` executable resolves to one the adapter accepts.
- Run ID: `20260924T091934Z-b933fb`, generated locally before the run and used
  in the issue title.

### 4. Canonical path

```text
exec-A
  -> submit X
  -> end
  -> X quarantined

exec-B
  -> inspect X
  -> adopt X
  -> submit C (parent = X)

ReleaseBoundary(C)
  -> authority probe 1: rejected
  -> authority probe 2: rejected
  -> fresh C-bound authority
  -> GitHubReleaseActuator
  -> GitHub
```

In this path:

- inspection was not custody: inspecting X did not let B read or release it;
- adoption was not authority: after adopting X, B held custody but C still
  needed its own authority decision;
- lineage was not authority: recording X as C's parent did not release C.

The harness asserts, before any authority evaluation or write, that B cannot
read X before adoption, cannot release X, and cannot derive C from X before
adopting it. A failed assertion would have stopped the run with no write, and
the run reported no invariant failure. The printed run record does not list
these assertions individually.

### 5. Authority probes

Both probes went through the real `ReleaseBoundary`. The harness did not
compare identifiers itself.

| Probe | Decision presented for C | Result | Reason | POST attempts |
|---|---|---|---|---|
| 1 | ALLOW bound to X's consequence identity and material, produced for X before A ended | `CONTAINED` | `authority_consequence_mismatch` | 0 |
| 2 | ALLOW rebound to C's consequence identity but carrying X's material | `CONTAINED` | `authority_material_mismatch` | 0 |
| Fresh | ALLOW bound to C's identity and C's material, evaluated at release time | `RELEASED` | `actuator_succeeded` | 1 |

The fresh decision carried a finite validity window (five minutes, a constant
in the harness helper). The final pre-write preflight passed before the fresh
evaluation.

### 6. Live effect

The pre-write plan printed by the harness named target
`Portotify/swampbox-effect-lab`, target repository ID `1377745355`, the
pinned login, the exact title, a budget of one POST attempt, and retry
disabled. The run then produced one POST attempt. The adapter reported
`SUCCEEDED` and the consequence state became `RELEASED`.

### 7. Independent verification

After the effect, the harness made a read-only GET for the returned issue
number and compared number, URL, title, and repository. It reported `PASS`.

During this documentation step, issue #3 was read once more with read-only
GETs: state `open`, no comments, no labels, last-updated time equal to the
creation time. Listing the target's issues showed exactly three: #1
(historical, closed), #2 (historical, open), and #3. Nothing was changed.

"Independent" here means a GitHub state read separate from the create response.
It is not a third-party audit or cryptographic proof, and it does not
establish authenticated causality: the issue was created by a process holding
the operator's credentials, and the link between that process and the issue
rests on the run record plus matching content.

### 8. What was observed

Reported by the harness run (self-reported by the process that performed it):

1. Execution A ended and X was quarantined; X was not released.
2. Execution B adopted X explicitly.
3. C was submitted by B with X recorded as its parent.
4. Probe 1 and probe 2 were rejected with the reasons above, with zero POST
   attempts each.
5. The final pre-write preflight passed.
6. A fresh C-bound ALLOW reached the actuator. Exactly one GitHub
   issue-creation POST attempt occurred. The adapter outcome was `SUCCEEDED`
   and C became `RELEASED`.
7. A second release of C returned `consequence_not_releasable`, and the write
   attempt count stayed at 1. The `CONTAINED` status shown on that result is
   the label the boundary gives every pre-actuation refusal; the boundary does
   not change the stored release state when it refuses.
8. No retry occurred.

Observed on GitHub:

9. Issue #3 exists in the target repository with the title
   `[SwampBox 13B] canonical release 20260924T091934Z-b933fb` and the body
   `Controlled SwampBox Step 13B validation issue.` /
   `Disposable test repository. No production data.`, state `open`, created
   `2026-09-24T09:19:40Z` by the authenticated login.

### 9. What this supports

Stated as conclusions of this experiment, not as general laws:

- In this experiment, lineage recorded where C came from but did not itself
  authorize release. An X-bound decision and a decision carrying X's material
  did not release C; a decision bound to C did.
- In this experiment, custody transition and consequence-bound authority were
  distinct gates. Adopting X gave B custody, and C was still not released
  until a fresh decision bound to C passed the boundary.
- In this experiment, execution lifetime and consequence lifetime were
  distinct. Ending A did not require deleting X; X stayed represented in the
  consequence lifecycle as quarantined state, and propagation needed a later
  explicit custody transition.
- In this experiment, release used a fresh evaluation bound to C at release
  time.
- Bounded containment statement: a pending consequence X remained contained
  across an execution handoff, and the derived consequence C crossed the
  external boundary through the canonical release path only after explicit
  adoption of X and a fresh C-bound release decision. Only that path was
  exercised. The containment is the in-memory modeled containment of the
  reference implementation, not runtime isolation.
- The canonical release path produced one real external GitHub effect: an
  issue creation in a disposable private repository, later observed by
  read-only reads.

Counts observed: one POST attempt, one issue created by this run, no retry,
and no additional POST caused by the second release. This is not a
delivery-semantics claim.

### 10. What this does not establish

- Authority freshness or revocation under real change. The fresh decision was
  evaluated at release time, but no real revocation or change of authority was
  injected between approval and actuation, and no expired-authority case was
  run live.
- Independence of the authority. The provider was scripted in the harness.
- Any comparison with a real sandbox or process-isolation runtime.
- That every consequence is contained, or that any other effect path is
  contained. A direct bypass of the boundary was not tested and remains outside
  the demonstrated path.
- Persistence, crash recovery, reconciliation, or idempotency.
- Exactly-once or any distributed delivery guarantee.
- Multi-parent lineage, concurrent or distributed execution, or authenticated
  or semantic causality between X and C.
- Behavior against a malicious or misbehaving GitHub.
- Production readiness, security, or any general theorem. This is one run with
  one effect against one disposable target.

Live-observed versus offline-tested:

| Behavior | Status |
|---|---|
| `SUCCEEDED` result, `RELEASED` state, second release refused | Live-observed in this run |
| Both authority probe rejections | Live-observed in this run |
| `DEFINITE_NOT_EXECUTED` handling | Offline-tested only |
| `UNCERTAIN` handling (timeout, non-zero exit, malformed response) | Offline-tested only |
| Interruption (`BaseException`) after actuation begins | Offline-tested only |
| Independent-read failure after success | Offline-tested only |
| Expired or not-yet-valid authority | Not exercised live |

### 11. Reproduction safety

Running the harness without arguments is a dry run and cannot write. The live
mode is intentionally guarded because it can create an external effect. It
requires a disposable target, an exact reviewed commit hash, and several
independent explicit confirmations, and it allows one POST attempt. After an
`UNCERTAIN` result the harness does not retry, and it does not clean up the
created issue. This document deliberately gives no run command; read the
harness source for the exact gates. Issue #3 is retained as evidence and should
not be edited, commented on, or closed as part of this record.

### 12. Evidence identifiers

| Item | Value |
|---|---|
| Date | 2026-09-24 |
| Harness commit | `bc297d5e5293c49fb437cb4211bc7c1029e0672d` |
| Run ID | `20260924T091934Z-b933fb` |
| Target | `Portotify/swampbox-effect-lab` |
| Target repository ID | `1377745355` |
| Issue | #3 |
| Issue URL | https://github.com/Portotify/swampbox-effect-lab/issues/3 |
| Issue title | `[SwampBox 13B] canonical release 20260924T091934Z-b933fb` |
| Issue created at | `2026-09-24T09:19:40Z` |
| X / C consequence IDs | `consequence-X` / `consequence-C` (C parent: `consequence-X`) |
| Probe 1 | `CONTAINED`, `authority_consequence_mismatch`, 0 POST attempts |
| Probe 2 | `CONTAINED`, `authority_material_mismatch`, 0 POST attempts |
| Fresh C | `RELEASED`, `actuator_succeeded`, 1 POST attempt |
| Second release | `consequence_not_releasable`, write attempts still 1 |
| Final write attempts | 1 |
| Retry | none |
| Independent verification | PASS |

### Claim audit

| Claim | Status | Evidence / limitation |
|---|---|---|
| Execution lifetime and consequence lifetime were distinct in this experiment | SUPPORTED | A ended and X stayed quarantined in the lifecycle; custody moved only by explicit adoption. In-memory reference model. |
| Lineage alone authorized release | NOT SUPPORTED — observed false in this experiment | X-bound and X-material decisions did not release C. One run, single-parent lineage only. |
| Fresh C-bound authority was required by this experiment's release path | SUPPORTED | Only the C-bound decision reached the actuator. The authority provider was scripted in the harness. |
| One real GitHub effect crossed the boundary | SUPPORTED | Issue #3 observed on GitHub. Causality rests on the run record and matching content. |
| Second release caused another external write | OBSERVED FALSE | Refused with `consequence_not_releasable`; write attempts stayed 1. |
| SwampBox was compared against a real sandbox | NOT TESTED | No sandbox runtime was involved. |
| Temporal authority revocation was live-tested | NOT TESTED | No revocation or expiry was injected during the run. |
| Crash recovery/persistence was live-tested | NOT TESTED | In-memory store, single process. |
| Exactly-once distributed delivery was proven | NOT ESTABLISHED | One attempt and one issue were observed; no delivery guarantee follows. |
| Production readiness was established | NOT ESTABLISHED | One controlled run against a disposable target. |
