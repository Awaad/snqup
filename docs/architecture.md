# Architecture

The system in one document: what runs where, how the code is organised, and the tooling
that enforces the rules. Decisions and their reasoning live in `adr/`; this describes the
result.

## System

```
                        Cloudflare  (WAF, cache, request ID origin)
                             │
        ┌────────────────────┼────────────────────┬──────────────────┐
        │                    │                    │                  │
   example.com          app.example.com      example.net       admin.example.com
   marketing             web dashboard      public / UGC        admin console
   (Vercel)               (Vercel)            (Vercel)            (Vercel)
        │                    │                    │                  │
        └────────────────────┴──────────┬─────────┴──────────────────┘
                                        │  api.example.com
                                 ┌──────┴───────┐
                                 │  Hetzner VM  │
                                 │  api · worker · valkey
                                 └──────┬───────┘
                                        │
                              Supabase (Postgres, Auth, Storage)

   Mobile (Expo, iOS + Android) ────────┘
```

Four web deployments on three registrable domains plus an admin subdomain. The separation
is load-bearing, not organisational (ADR-0008).

| Runtime | Where | Notes |
|---|---|---|
| FastAPI + ARQ worker + Valkey | Hetzner CPX21, Falkenstein | Docker Compose, single host |
| Postgres, Auth, Storage | Supabase Pro, EU | Managed |
| Next.js × 4 | Vercel | marketing, web, public, admin |
| Expo | App Store, Play Store | |

Full topology and cost in ADR-0015.

## Repository

```
apps/
  api/          FastAPI          Python 3.13
  mobile/       Expo             TypeScript
  web/          Next.js          authenticated dashboard
  public/       Next.js          UGC domain, minimal JS
  marketing/    Next.js          no UGC
  admin/        Next.js          internal, SSO + IP allowlist
packages/
  api-client/   GENERATED from openapi.json
  tokens/       GENERATED from tokens.json
  shared/       hand-written cross-platform validation and formatting
infra/          Compose, deploy scripts, SOPS secrets
docs/
```

## Backend structure (ADR-0025)

```
apps/api/src/acme/
  domains/
    identity/     users, organizations, members, domain verification
    cards/        cards, tokens, themes, QR generation
    exchange/     the scan → connection transaction
    connections/  views, notes, tags, reminders, merge
    events/       events, attendees, staff, roster, aggregates
    billing/      entitlements, webhooks, reconciliation
    safety/       reports, blocks, audit log
  core/          db, cache, auth adapter, errors, logging, jobs runtime
```

Each domain: `router.py`, `service.py`, `repository.py`, `models.py`, `schemas.py`.

**Import rules, enforced by `import-linter`:**

| From | May import |
|---|---|
| Any domain | `core/`, and other domains' `service` module **only** |
| Any domain | **Never** another domain's repository, models, or internals |
| `core/` | No domain |

`exchange` orchestrates cards, connections, events and safety in one transaction. It is a
domain in its own right so it is findable.

`billing` exposes exactly `entitlements.check(subject, key)` to other domains. Nothing
else crosses that boundary.

**The repository layer is the tenant chokepoint** (ADR-0018). Every tenant-scoped query
takes an explicit tenant ID. Omitting it must be structurally impossible.

## Client structure (ADR-0026)

Feature directories mirror the API domains in every client.

**Mobile**

```
apps/mobile/src/
  features/  cards/ exchange/ connections/ events/ profile/
  core/      api/ sync/ db/ auth/ i18n/ telemetry/
  ui/
  navigation/
```

- **TanStack Query** for server state
- **`core/sync`** owns the offline outbox. Separate module, separate tests. Never
  TanStack's persistence — see ADR-0026 for why this distinction matters.
- **SQLite** (`expo-sqlite` + Drizzle) for local persistence, not AsyncStorage
- **Zustand** for the small amount of genuine client state
- **Expo Router** for typed file-based routing and deep links

**Web dashboard**

Server Components for fetching; Client Components only where interaction demands it.
`core/sse/` wraps `EventSource` with reconnect and `Last-Event-ID` replay.

**Public pages**

Deliberately minimal and deliberately isolated. Shares tokens, shares nothing else. It has
a sub-one-second budget on hotel wifi and reusing the dashboard's data layer is how that
budget is lost.

## Workspace rules

**pnpm workspaces + Turborepo** for the Node side. **uv** for Python.

Two package managers in one repository is unavoidable and confuses newcomers. It is
documented here so it confuses them for less time.

```yaml
# pnpm-workspace.yaml
packages: ["apps/*", "packages/*"]
```

**Rules:**

1. **Generated packages are never hand-edited.** `packages/api-client` and
   `packages/tokens` output carry a generated header. CI regenerates and fails on drift.
2. **No cross-app imports.** `apps/web` may not import from `apps/admin`. Shared code goes
   in `packages/`. Enforced by ESLint `no-restricted-imports`.
3. **`packages/shared` is for logic with no I/O and no platform assumptions.** Validation,
   formatting, pure functions. If it needs React or a network call, it does not belong
   there.
4. **Dependencies are declared where they are used.** No relying on hoisting.
5. **Exact versions for frameworks** (ADR-0021). No carets, no tildes.
6. **One lockfile per ecosystem**, committed. `pnpm-lock.yaml`, `uv.lock`.

Turborepo's affected-package detection is essential rather than a nicety — without it every
commit rebuilds the marketing site.

## Tooling

### Python

| Tool | Purpose |
|---|---|
| **uv** | Dependency resolution and virtualenv. Fast, lockfile-based. |
| **Ruff** | Lint and format. Replaces black, isort, flake8, pyupgrade. |
| **mypy** | `--strict`. Non-negotiable on `domains/` and `core/`. |
| **import-linter** | Enforces ADR-0025 boundaries |
| **pytest** | `pytest-asyncio`, `pytest-cov` |
| **Alembic** | Migrations (`schema/migration-policy.md`) |
| **testcontainers** | Real Postgres in tests, never SQLite |

Testing against SQLite when production is Postgres means partial indexes, `citext`, JSONB
operators and `LISTEN/NOTIFY` are all untested. Use a real container.

### TypeScript

| Tool | Purpose |
|---|---|
| **TypeScript** | `strict: true`. Also `noUncheckedIndexedAccess`. |
| **ESLint** | Flat config, shared base in `packages/` |
| **Prettier** | Formatting only; ESLint does not format |
| **Vitest** | Unit tests |
| **Playwright** | E2E on web |
| **Maestro** | E2E on mobile |
| **openapi-typescript** | Generates `api-client` |
| **Style Dictionary** | Generates `tokens` |

### Custom lint rules

These encode rules from `handoff/00-shared-contracts.md` that would otherwise rely on
memory:

| Rule | Prevents |
|---|---|
| No `left`/`right` in RN styles | RTL breakage (ADR-0011) |
| No `margin-left`/`padding-right` in CSS | Same, on web |
| No untranslated string literals in JSX | i18n gaps |
| `no-restricted-imports` on `apps/*` | Cross-app coupling |
| No `localStorage`/`sessionStorage` in mobile | Wrong persistence layer |
| Ban `console.log` in `apps/api` and `apps/mobile` | Use structured logging (ADR-0024) |

## Pre-commit

`pre-commit` framework, one config, installed by the bootstrap script.

```yaml
repos:
  - ruff (lint + format)          # Python
  - mypy                          # changed files only
  - eslint --fix, prettier        # TypeScript
  - detect-secrets                # ADR-0013
  - end-of-file-fixer, trailing-whitespace, check-merge-conflict
  - check-added-large-files       # keeps binaries out
  - local: placeholder-check      # `acme` in locales/migrations/infra
  - local: generated-freshness    # api-client and tokens up to date
```

**Fast only.** Anything over a few seconds belongs in CI, or people bypass the hook and
then bypass it habitually.

`detect-secrets` is the one that matters most. A credential committed in plaintext must be
rotated even after a history rewrite, because it may already be cloned or cached
(`runbooks/secret-rotation.md`).

## CI

GitHub Actions. Every job scoped to affected packages via Turborepo.

**On pull request:**

```
lint            ruff, eslint, prettier --check
typecheck       mypy --strict, tsc --noEmit
boundaries      import-linter (ADR-0025)
test-unit       pytest, vitest
test-integration pytest with testcontainers Postgres
generated       regenerate api-client + tokens, fail on diff
migrations      lint against migration-policy; run up then down on a scratch DB
secrets         detect-secrets
placeholder     `acme` absent from locales and migrations
build           all apps build
```

**The `generated` job is the one that prevents contract drift** (ADR-0014). Without it the
TypeScript view of the API diverges from the Python models within about a month, and the
failures are silent.

**On merge to `main`:** build and push images to GHCR, deploy to staging, run smoke tests.

**On manual approval:** deploy to production (`runbooks/deploy.md`). Migration first,
alone; application second.

**Scheduled:**

| Job | Cadence | Purpose |
|---|---|---|
| Dependency PRs | Weekly | Batched, reviewed monthly (ADR-0021) |
| Security audit | Daily | `pnpm audit`, `uv pip audit` |
| Backup restore drill | Quarterly | `runbooks/backup-restore.md` |
| Reconciliation check | Daily | Billing divergence alert |

### Required checks

`main` is protected. These must pass: lint, typecheck, boundaries, unit, integration,
generated, migrations, secrets.

No force push. No merge without review. Squash merge only.

## Local development

One command from a clean clone:

```bash
./scripts/bootstrap.sh
```

Installs uv, pnpm, pre-commit hooks; starts Postgres and Valkey containers; runs
migrations; seeds `reserved_slugs` and a demo dataset.

```bash
pnpm dev          # all web apps + API
pnpm dev --filter web
pnpm mobile       # Expo dev client (Expo Go is not sufficient — NFC needs native)
```

**Local Postgres is a Docker container**, not Supabase (ADR-0005). Staging and production
use Supabase.

## Conventions

**Commits:** conventional commits, scoped to package. `feat(api): add exchange endpoint`.

**Branches:** short-lived from `main`. `feat/`, `fix/`, `chore/`.

**PRs:** one logical change. Migration PRs answer the checklist in
`schema/migration-policy.md` in the description.

**Code review:** the owning team approves per `CODEOWNERS`. Cross-boundary changes need the
owning team.

## Where things are enforced

A summary, because "we agreed to" is not enforcement.

| Rule | Enforced by |
|---|---|
| Domain boundaries | `import-linter`, CI |
| Contract drift | `generated` CI job |
| Design token drift | `generated` CI job |
| RTL correctness | ESLint rules |
| i18n coverage | ESLint rule + locale key check |
| Secrets in code | `detect-secrets`, pre-commit + CI |
| Placeholder leakage | custom check, pre-commit + CI |
| Tenant isolation | repository chokepoint + dedicated test suite |
| Migration safety | migration lint + review checklist |
| Ownership | `CODEOWNERS` |

Everything else is convention and will drift. That is acceptable for style; it is not
acceptable for the rows in this table.
