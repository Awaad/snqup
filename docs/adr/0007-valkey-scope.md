# ADR-0007: Valkey scope: rate limiting, idempotency, cache, queue

**Status:** Accepted
**Date:** 2026-09-04

## Context

An earlier draft of this design said "no Redis", which conflated two separate arguments.
The intended statement was "no Redis **as a WebSocket backplane**" (see ADR-0006). Redis
is load-bearing for several other things and removing it would break them.

This ADR exists specifically so that a future engineer, seeing that Postgres LISTEN/NOTIFY
handles realtime fan-out, does not conclude that Valkey is redundant and remove it.

## Decision

Run **Valkey** (Redis-compatible, BSD licensed) on the application host. It is required
for:

1. **Rate limiting.** Must be shared state the moment more than one Uvicorn worker runs.
   Sliding window. The layered limit design is in `handoff/03-backend-api.md`.
2. **Idempotency keys.** Client-generated key on every mutating endpoint, stored 24 hours.
   Offline retry (ADR-0016) makes this mandatory, not optional.
3. **Token revocation and denylists.** JWT revocation before natural expiry.
4. **Response cache** for public card and event pages, our highest-traffic endpoints.
5. **Job queue broker.** ARQ. Required for follow-up reminders, post-event digests,
   transactional email, CSV export generation and the soft-delete purge job.

**Explicitly not used for:** realtime fan-out (Postgres LISTEN/NOTIFY), session storage
(stateless JWTs), or as a primary data store.

Valkey persistence is enabled (AOF, everysec) but **nothing in Valkey is treated as
durable**. A total Valkey loss must degrade the system, never corrupt it: rate limits
reset, idempotency windows are lost, caches are cold, queued jobs are re-enqueued from
their database source of truth.

Every queued job must therefore be reconstructible from Postgres. Jobs carry a database
row; Valkey holds only the scheduling.

## Consequences

**Good.** One small dependency covering five needs, colocated on the existing host at
effectively zero incremental cost.

**Good.** The "nothing in Valkey is durable" rule means the disaster recovery story does
not include Valkey at all.

**Bad.** A single Valkey instance is a single point of failure for rate limiting. Rate
limiting must **fail open with logging**, never fail closed — a false block during a live
event in front of 400 people is worse than an unthrottled hour.

**Bad.** Colocating with the API means memory pressure on the API host. Set `maxmemory`
with `allkeys-lru`, and monitor.

**Bad.** Reconstructible-jobs discipline is easy to violate. Code review must catch any
job that carries state only in the queue payload.

## Alternatives considered

**Postgres for everything, including rate limiting.** Rejected: rate limiting is a
high-frequency write path and putting it on the primary database is how you take the
database down during your busiest hour, which is exactly a live event.

**Managed Redis (Upstash, Supabase).** Reconsider if the application moves off a single
host. At current scale the added latency and cost buy nothing.
