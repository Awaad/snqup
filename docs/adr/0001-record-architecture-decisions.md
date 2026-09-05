# ADR-0001: Record architecture decisions

**Status:** Accepted
**Date:** 2026-09-04

## Context

This system is being built by parallel teams against a specification that changed
substantially during design review. Several decisions were reversed during that review
(auth provider, realtime transport, Redis scope). Without a record, those reversals will
be re-litigated by whoever joins next, and the reasoning will be lost.

The specific failure mode we are guarding against: a future engineer sees Valkey in the
stack, concludes it is redundant with Postgres LISTEN/NOTIFY, and removes it — not knowing
it is load-bearing for rate limiting, idempotency and the job queue.

## Decision

Every architecturally significant decision is recorded as an ADR in `docs/adr/`, using the
format described in `docs/adr/README.md`.

A decision is architecturally significant if reversing it later would require changing more
than one layer, or would require a data migration.

ADRs are immutable once accepted. Superseding is done by writing a new record.

## Consequences

**Good.** Reasoning survives personnel changes. Parallel teams can resolve disagreements by
reading rather than meeting. Rejected alternatives are recorded, so they are not
re-proposed.

**Bad.** Writing an ADR is friction on every real decision, and some decisions will be
made without one because the friction won. Accept this: partial coverage of the important
decisions beats complete coverage of trivial ones.

**Bad.** The index in `README.md` must be maintained by hand and will drift. Accepted
because automating it is not worth the tooling.

## Alternatives considered

**Wiki pages.** Rejected: mutable by design, so the historical reasoning is overwritten
rather than superseded, which is precisely what we need to prevent.

**Comments in code.** Rejected: cross-cutting decisions have no single file to live in.
