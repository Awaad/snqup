# ADR-0024: Observability: Sentry, PostHog, structured logs, no OTel yet

**Status:** Accepted
**Date:** 2026-09-04

## Context

Three runtimes, one small team. Observability tooling that nobody looks at is worse than
none, because it creates the illusion of coverage.

## Decision

**Sentry.** Errors and crashes across Expo, Next.js and FastAPI, correlated by release.
The Expo integration handles native crashes and source maps. This is the tool that turns
"it broke" into a stack trace. Not optional.

**PostHog.** Product analytics, funnels, session replay, and **feature flags** — which
ADR-0022 depends on. EU-hostable.

**Structured JSON logging**, one schema across all runtimes. `structlog` for FastAPI,
`pino` for Next.js, batched device logs from Expo (console logs on a device are invisible).

Every log line carries: `request_id`, `user_id`, `trace_id`, `service`, `env`, `version`.
The request ID originates at Cloudflare, propagates through Next.js to FastAPI in a header,
and is returned to the client. One identifier across the whole path.

Field names follow **OpenTelemetry semantic conventions** (`http.method`, not `method`)
even though OTel itself is not used. Free now, and migration later becomes configuration.

**Redaction is enforced by a structlog processor, not by developer discipline.** Emails,
phone numbers and tokens must never reach logs. This product's logs would otherwise be a
contact database.

**Uptime monitoring** hitting `/health` externally every minute. Sentry reports errors; it
does not report a host that is down.

**OpenTelemetry is deliberately deferred.** It is the right long-term answer and the wrong
day-one answer: roughly a week of instrumentation for value that arrives with a
distributed system and a team debugging it. Structured logs with a propagated request ID
deliver most of the benefit at a fraction of the cost.

**Instrument the activation funnel from the first build:**

```
install → account created → first card created → first QR shown
       → FIRST SUCCESSFUL EXCHANGE → returned within 7 days
```

Step five is the activation metric. Everything else is vanity. Also instrument scan channel
attribution, exchange success/failure by channel, and time-to-first-exchange.

## Consequences

**Good.** Two tools that get used, rather than five that do not.

**Good.** Request ID propagation means a user report maps to a single trace across three
runtimes without distributed tracing infrastructure.

**Good.** OTel-compatible naming makes the eventual migration cheap.

**Bad.** No distributed tracing means latency attribution across service boundaries is
manual. Acceptable with three services on two hosts.

**Bad.** Session replay is a privacy surface. It must mask all input fields by default and
be disclosed in the privacy policy.

**Bad.** Log redaction processors are easy to bypass accidentally by logging a whole
object. Code review must watch for it, and the processor should redact by key name
recursively.

## Alternatives considered

**Full OpenTelemetry from day one.** Deferred, not rejected. Revisit when there are more
than two services or more than two engineers.

**Self-hosted (Grafana, Loki, Tempo).** Rejected: operating an observability stack is
itself an operational burden, which is what we are trying to reduce.
