# ADR-0026: Client architecture: feature-first, TanStack for server state, separate sync

**Status:** Accepted
**Date:** 2026-09-04

## Context

Three client applications (mobile, web dashboard, public pages) with different constraints,
sharing an API and a design system.

The mobile app has a requirement most apps do not: a durable offline queue that survives
force-quit and syncs hours later (ADR-0016). There is a strong temptation to express this
with a data-fetching library's built-in retry and persistence, because it looks like the
same problem. It is not.

TanStack Query's mutation retry and persistence are built for "this request will probably
succeed shortly". Our requirement is "this exchange happened in a basement, hold it
reliably, send it in order with an idempotency key, possibly tomorrow." Conflating them
loses exchanges, which is the one failure this product cannot have.

## Decision

**Feature-first directories in every client, mirroring the API domains** (ADR-0025). Same
mental model across three codebases.

### Mobile

```
apps/mobile/src/
  features/     cards/ exchange/ connections/ events/ profile/
                  (each: screens/ components/ hooks/ api/)
  core/         api/ sync/ db/ auth/ i18n/ telemetry/
  ui/           design system primitives, token-driven
  navigation/   Expo Router
```

**TanStack Query for server state**: cards, connections, events, entitlements. Caching,
background refetch, optimistic updates.

**A separate `core/sync` module owns the offline queue.** An outbox table in SQLite,
ordered dispatch, exponential backoff, idempotency keys, conflict resolution per ADR-0016.
TanStack reads from local state; the sync module reconciles local against server. The
boundary is explicit and must not blur, or there are two sources of truth.

**Local persistence is SQLite** (`expo-sqlite`), not AsyncStorage. The connections list
needs indexed queries, filtering and search; AsyncStorage is a key-value blob that degrades
at a few hundred connections.

**Zustand for the small amount of genuine client state** (scan session, UI flags). Server
data never goes in it.

**Expo Router** for file-based typed routing. Deep links carry event codes and claim
tokens, and Router handles them better than hand-configured navigation.

### Web dashboard (`apps/web`)

Server Components for data fetching. Client Components only where interaction demands it.
TanStack Query on the client only for genuinely interactive surfaces such as dashboard
filters.

**SSE gets its own module** in `core/sse/`, wrapping `EventSource` with reconnect and
`Last-Event-ID` replay. It is the demo-critical feature and must not be scattered across
components.

### Public pages (`apps/public`)

**Deliberately different and deliberately minimal.** Server-rendered, aggressively cached,
minimal JavaScript. It has a sub-one-second budget on hotel wifi
(`handoff/05-web-next.md`).

It shares design tokens with the other apps. It does **not** share the dashboard's data
layer, component library, or state management. Reusing them is the obvious convenience and
it is how the performance budget gets lost.

## Consequences

**Good.** One structural idea across API, mobile and web.

**Good.** The sync module being separate means it can be tested in isolation against
force-quit, restart and partial-sync scenarios, which is where offline implementations
actually fail.

**Good.** SQLite makes the connections list fast at realistic contact counts and gives us
somewhere to put the outbox.

**Bad.** Two state systems on mobile (TanStack + sync module) is more to understand than
one, and the boundary needs documenting for every new engineer.

**Bad.** SQLite plus Drizzle is heavier than AsyncStorage and adds a native dependency and
migration story for the local database.

**Bad.** Keeping `apps/public` isolated means some duplication with `apps/web`. This is
intentional and must be defended in review, because the pressure to share will be constant.

**Bad.** Server Components and TanStack Query coexisting in one app requires a clear rule
about which fetches where, or it becomes inconsistent.

## Alternatives considered

**TanStack Query persistence as the offline queue.** Rejected for the reason in Context.
This is the most likely mistake and it is recorded so it is not made later.

**Redux Toolkit with RTK Query.** Workable. Rejected as more boilerplate than the app needs
now that server state is handled separately.

**One shared Next.js app for dashboard and public pages.** Rejected: different domains
(ADR-0008), different performance budgets, different risk profiles.
