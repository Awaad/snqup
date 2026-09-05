# ADR-0006: SSE over Postgres LISTEN/NOTIFY for realtime, not WebSockets

**Status:** Accepted
**Date:** 2026-09-04

## Context

The live organizer dashboard is a selling feature and is not being cut. The original
specification proposed WebSockets via Pusher or Socket.IO.

The dashboard's data flow is strictly one-directional: the server pushes updated counts,
the browser never sends anything back over that channel. WebSockets are a bidirectional
transport being used for a unidirectional problem.

Choosing WebSockets with FastAPI implies a Redis pub/sub backplane for fan-out across
workers, sticky sessions at the load balancer, and a separate long-lived process. That is
real operational cost for a capability the feature does not use.

## Decision

**Server-Sent Events** for the organizer dashboard, with **Postgres LISTEN/NOTIFY** for
fan-out.

SSE is a plain HTTP GET that stays open. It reconnects automatically in every browser via
the built-in `EventSource` API, passes through every proxy that handles HTTP, and is
roughly twenty lines in FastAPI using `StreamingResponse`.

Fan-out: the exchange transaction issues `NOTIFY event_<id>`. A listener connection per
worker receives it and pushes to that worker's connected SSE clients. No extra
infrastructure.

Reconnection uses the `Last-Event-ID` header to replay missed aggregate updates, which is
cheap because the payload is a snapshot of counts, not a delta stream.

**If a future feature genuinely needs upstream messaging** — organizer-to-attendee chat,
for example — a WebSocket channel is added for that specific feature. The dashboard is
not migrated.

## Consequences

**Good.** No Redis pub/sub backplane, no sticky sessions, no separate WebSocket process,
no Pusher bill. Identical user-visible feature.

**Good.** Debuggable with `curl`. A WebSocket stream is not.

**Good.** Survives corporate proxies and hotel wifi better than WebSockets, which matters
because organizers demo this at venues.

**Bad.** SSE holds an open HTTP connection per viewer. With a low worker count this
constrains concurrent dashboard viewers. Acceptable: dashboard viewers are staff, measured
in single digits per event. Documented in `runbooks/event-day.md`.

**Bad.** LISTEN requires a dedicated connection per worker, outside the pooler.
Supavisor in session mode, or a direct connection, is required for the listener
specifically. This is a real deployment footgun and is called out in
`handoff/01-platform-infra.md`.

**Bad.** No native browser SSE support in some older environments and no SSE in React
Native without a polyfill. Irrelevant: the dashboard is web-only.

## Alternatives considered

**WebSockets via Socket.IO.** Rejected: bidirectional transport for a unidirectional
problem, plus backplane and sticky session cost.

**Pusher or Ably.** Rejected: recurring cost and a third-party dependency in the demo path
for our highest-value customer interaction.

**Polling every 5 seconds.** Rejected: the visible tick-up on a projector is the feature.
Polling looks like polling.
