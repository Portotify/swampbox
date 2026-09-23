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
