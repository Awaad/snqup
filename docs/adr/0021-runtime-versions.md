# ADR-0021: Runtime versions and upgrade policy

**Status:** Accepted
**Date:** 2026-09-04

## Context

The temptation on a greenfield project is to take the newest version of everything. On a
small team this trades a small performance gain for an unbounded debugging cost when a
transitive dependency lacks a wheel or a framework minor release breaks the build.

## Decision

**Policy: newest stable minus one for runtimes; exact pins for frameworks.**

| Component | Version | Reasoning |
|---|---|---|
| Python | **3.13** | 3.14 is fine on paper, but the dependency chain (asyncpg, SQLAlchemy, Pydantic, and transitives) will have a wheel gap. Move at ~3.14.4. |
| TypeScript | **Current stable** | Verify where TS 7 (the native Go compiler) actually landed before committing. If stable and the toolchain supports it, take it — the speed difference is real. If not, 5.x costs nothing. |
| Next.js | **Exact pin** | Minor releases have broken builds before. No ranges. |
| Expo SDK | **Exact pin** | Expo dictates the React Native version; this is not independently choosable. |
| Postgres | Supabase default | Managed. |
| Node | Active LTS | |

**All framework versions are pinned exactly. No caret or tilde ranges anywhere,
including transitive lockfiles.**

**Upgrades happen in a scheduled monthly window**, never reactively, with the sole
exception of security patches. One upgrade per pull request so a regression is bisectable.

Renovate or Dependabot opens PRs; they are batched and reviewed in the window, not merged
on arrival.

**Before any store submission, freeze all dependencies.** Do not upgrade between building
a release candidate and shipping it.

## Consequences

**Good.** Debugging time goes to product problems rather than toolchain archaeology.

**Good.** Exact pins mean the build is reproducible and a broken CI run is a real signal.

**Bad.** We are deliberately behind on performance improvements, notably whatever the new
TypeScript compiler offers if we defer it.

**Bad.** A monthly batch of upgrades is a larger, riskier change than continuous small
ones. Mitigated by one-per-PR.

**Bad.** Someone will want a library that requires a newer runtime. That is a conversation
and possibly an ADR, not a unilateral bump.

## Alternatives considered

**Always latest.** Rejected: on a small team, one bad afternoon of wheel-compilation
erases a year of marginal performance gains.

**Long-term-support-only, aggressively conservative.** Rejected: React Native and Expo move
too quickly for this to be viable, and falling far behind on Expo makes upgrades worse, not
better.
