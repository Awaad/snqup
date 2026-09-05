# ADR-0014: Monorepo with generated cross-language contracts

**Status:** Accepted
**Date:** 2026-09-04

## Context

Three runtimes: Expo/TypeScript, Next.js/TypeScript, FastAPI/Python. Two of them consume
an API defined by the third.

Without a generated contract, the TypeScript view of the API drifts from the Python
models. Experience says this happens within about a month(in lingua happend in a week), and the failures are silent:
an optional field becomes required, a nullable becomes non-nullable, and the mobile client
crashes for users on an old version.

Design tokens have the same problem across two styling systems (ADR-0017).

## Decision

**A single repository** containing all applications, packages and infrastructure.

```
apps/
  api/          FastAPI
  mobile/       Expo
  web/          Next.js (dashboard + authenticated app)
  public/       Next.js (UGC domain: card pages, scan resolution)
  marketing/    Next.js (primary domain, no UGC)
packages/
  api-client/   GENERATED from OpenAPI. Never hand-edited.
  tokens/       GENERATED from tokens.json by Style Dictionary.
  shared/       Hand-written cross-platform logic (validation, formatting)
infra/
docs/
```

**The contract pipeline is CI-enforced:**

1. FastAPI emits `openapi.json` from Pydantic models.
2. `openapi-typescript` generates types into `packages/api-client`.
3. CI regenerates and fails the build if the checked-in output differs.

Generated directories carry a header marking them generated and are excluded from review.

Task running via Turborepo for caching and affected-package detection, so a change to the
API does not rebuild the marketing site.

## Consequences

**Good.** Contract drift becomes a build failure rather than a production incident.

**Good.** An atomic commit can change an API and both consumers together.

**Good.** One CI configuration, one dependency policy, one lint configuration.

**Bad.** Two package managers in one repository (uv for Python, pnpm for Node). Unavoidable
and manageable, but it confuses newcomers.

**Bad.** CI must be careful about what it rebuilds or every commit runs everything.
Turborepo's affected detection is essential rather than a nicety.

**Bad.** Repository size grows and clone times increase. Acceptable at this scale.

**Bad.** Access control is all-or-nothing. Relevant if contractors are ever engaged for a
single app.

## Alternatives considered

**Separate repositories with a published API client package.** Rejected: version skew
between repositories is exactly the failure being prevented, and it makes cross-cutting
changes a multi-repository dance.

**Hand-written API types.** Rejected: this is the drift.

**gRPC or tRPC for end-to-end type safety.** Rejected: tRPC requires a TypeScript backend;
gRPC is disproportionate and awkward from React Native.
