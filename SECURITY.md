# Security Policy

## Reporting a vulnerability

Please report suspected security vulnerabilities privately using GitHub's **Report a vulnerability** feature in the [SwampBox Security Advisories](https://github.com/Portotify/swampbox/security/advisories).

If GitHub's private reporting feature is unavailable to you, contact [security@portotify.com](mailto:security@portotify.com) directly. Please do not use public issues for vulnerability details.

Do not disclose exploit details, credentials, sensitive payloads, or personal information in public issues or discussions. Include a concise description, affected revision, reproduction steps, observed impact, and any relevant evidence that can be shared safely. Please avoid testing against systems you do not own or have permission to assess.

Security reports will be reviewed on a best-effort basis. This project does not promise a particular response or remediation timeline.

## Scope and security boundaries

SwampBox is an experimental, provider-neutral **reference implementation** for modeled consequence containment. It is **not** a process sandbox or a universal security boundary.

The supported reference behavior, trust assumptions, and limitations are documented in the [README](README.md). In particular, the public implementation does not guarantee authenticated execution identities, universal interception of external effects, prevention of direct actuator or network bypass, durable crash reconciliation, distributed atomicity, or exactly-once external effects.

A finding that violates documented behavior or an explicit invariant within the supported reference path is relevant to this project. Limitations already disclosed in the README are not, by themselves, claims of a newly introduced vulnerability, though concrete evidence of additional risk is welcome.

The public repository does not claim production-grade security or expose the project's non-public architecture. Please do not assume that unpublished components provide guarantees beyond those documented here.

## General contact

For non-security questions about SwampBox, contact [contact@portotify.com](mailto:contact@portotify.com).

## Supported versions

Security review is focused on the current default-branch reference implementation. No separately maintained security-support or backport policy is currently offered.
