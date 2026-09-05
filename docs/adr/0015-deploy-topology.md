# ADR-0015: Deploy topology: Hetzner, Vercel, Supabase

**Status:** Accepted
**Date:** 2026-09-04

## Context

Budget is up to €200/month pre-revenue, with a stated preference for the lower end
sustained over a long runway. The entity is a US LLC with a Mercury account, so payment
processing and app store enrolment are unconstrained.

Users are targeted internationally, with pilot testing local to the operator. GDPR applies
because users are in the EU, regardless of where the entity sits — which requires standard
contractual clauses in the DPA, not EU-only hosting.

## Decision

| Component | Host | Region | Approx cost |
|---|---|---|---|
| Postgres, Auth, Storage | Supabase Pro | EU (Frankfurt) | $25/mo |
| FastAPI, Valkey, ARQ worker | Hetzner CPX21 | Falkenstein | ~€8/mo |
| Web, public, marketing | Vercel | Edge | $0–20/mo |
| CDN, WAF, DNS | Cloudflare | Global | $0 |
| Email | Resend | — | $0–20/mo |
| Errors | Sentry | EU | $0–26/mo |
| Product analytics | PostHog | EU | $0 |
| Uptime | UptimeRobot / Better Stack | — | $0 |

**Total: approximately $55–100/month**, leaving headroom within budget.

**Deployment method:** Docker Compose on the Hetzner host, images built in CI and pushed
to GHCR, deployed over SSH. Not Kubernetes — a single host running three containers does
not need an orchestrator, and the operational cost of one is not repaid at this scale.

**Cloudflare sits in front of everything**, providing WAF, bot filtering, rate limit
pre-filtering and caching. This matters most for the public card endpoint, which is the
highest-volume and most abuse-prone surface.

**Environments:** `production` and `staging`. Staging is a separate Supabase project and a
separate Hetzner host, deliberately smaller. No shared state of any kind between them.

**The known single point of failure is the Hetzner host.** Accepted at this stage. The
recovery path is a documented rebuild from the Compose file plus SOPS secrets, targeting
under 30 minutes. See `runbooks/incident-response.md`. Revisit when revenue justifies a
second host and a load balancer.

## Consequences

**Good.** Comfortably inside budget with room to scale Postgres before anything else needs
attention.

**Good.** Frankfurt for both Supabase and Hetzner keeps database latency in single-digit
milliseconds.

**Good.** Vercel handles the traffic-spiky public pages with zero operational work, which
is exactly where spikes occur — an event going live.

**Bad.** Single application host means downtime during deploys unless handled. Mitigated
by health-check-gated rolling replacement in the deploy script.

**Bad.** Hetzner requires patching, monitoring and disk management that a PaaS would
absorb. This is the cost of the price point and is explicitly accepted.

**Bad.** Four vendors means four status pages and four billing relationships.

**Bad.** US entity plus EU users requires SCCs in the DPA and a documented transfer basis.
Tracked in the compliance checklist.

## Alternatives considered

**Fly.io or Railway for everything.** Simpler operationally, roughly 2–4x the cost at this
size, and less predictable at scale. Reasonable to revisit if operations become the
bottleneck.

**AWS.** Rejected: disproportionate operational and cognitive overhead for a small team,
with a cost profile that is hard to predict.

**Self-hosting Postgres on the same Hetzner box.** Rejected in ADR-0005. Backups, PITR and
pooling are worth $25/month.
