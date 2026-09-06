# Version Manifest

**Verified against npm, PyPI and GitHub on 2026-09-05.** Re-verify before the first
commit; these move.

Policy is ADR-0021: **newest stable minus one for runtimes, exact pins for frameworks.**
No caret or tilde ranges anywhere.

## Runtimes

| | Version | Reasoning |
|---|---|---|
| **Node** | **24** (Krypton) | Active LTS. Node 26 becomes LTS in October 2026 — take it at the next upgrade window, not now. |
| **Python** | **3.13** | 3.14 is fine on paper. The dependency chain (asyncpg, SQLAlchemy, Pydantic and transitives) will have a wheel gap, and compiling from source at the wrong moment costs a day. Move at ~3.14.4. |
| **pnpm** | **11.25.0** | **pnpm 12 shipped 2026-08-26 as a full Rust rewrite.** npm `latest` still points at the 11 line. This is exactly the case ADR-0021 exists for. |
| **uv** | 0.12.10 | |

## Two decisions ADR-0021 made for us

**TypeScript 7.0.2 — take it.** ADR-0021 left this open pending verification. TS 7 (the
native Go compiler) went stable on 2026-07-08 and has had two months of patch releases.
The speed difference is real and the toolchain supports it. **This closes the open
question in ADR-0021.**

**Vitest 4.1.11, not 5.0.0.** Vitest 5 shipped 2026-09-03, two days before this manifest.
Newest stable minus one.

## JavaScript

| Package | Version |
|---|---|
| typescript | 7.0.2 |
| next | 16.3.4 |
| react / react-dom | 19.2.8 |
| turbo | 2.10.12 |
| eslint | 10.10.0 |
| prettier | 3.9.6 |
| vitest | 4.1.11 |
| @playwright/test | 1.63.0 |
| @tanstack/react-query | 5.102.8 |
| zustand | 5.0.15 |
| zod | 4.5.4 |
| next-intl | 4.14.2 |
| i18next / react-i18next | 26.4.2 / 17.0.13 |
| openapi-typescript | 7.13.0 |
| style-dictionary | 5.5.2 |
| tailwindcss | 4.3.3 |
| @sentry/nextjs | 10.73.0 |
| posthog-js | 1.427.2 |
| drizzle-orm | 0.45.2 |
| @types/node | 26.4.1 |

Versions live in the **pnpm catalog** in `pnpm-workspace.yaml`, so a bump is a one-line
change rather than a search-and-replace across every `package.json`.

## Expo

| Package | Version |
|---|---|
| expo (SDK 57) | 57.0.20 |
| expo-router | 57.0.19 |
| react-native | 0.87.1 |
| expo-sqlite | 57.0.2 |
| expo-camera | 57.0.4 |
| expo-localization | 57.0.1 |
| expo-notifications | 57.0.17 |
| expo-secure-store | 57.0.3 |

**Expo dictates the React Native version.** It is not independently choosable, and the
Expo SDK upgrade is the one that must be taken regularly — falling far behind makes future
upgrades worse, not better.

**Expo Go is not sufficient.** NFC and other native modules require a custom dev client.
Set this up on day one rather than discovering it in week six.

## Python

| Package | Version | Note |
|---|---|---|
| fastapi | 0.141.1 | |
| pydantic | 2.13.5 | |
| pydantic-settings | 2.15.0 | |
| sqlalchemy | 2.0.52 | |
| alembic | 1.19.2 | |
| asyncpg | 0.31.0 | |
| uvicorn[standard] | 0.52.4 | |
| redis[hiredis] | **5.3.1** | **Capped by arq**, see below |
| psycopg[binary] | 3.3.2 | Migrations only, see below |
| arq | 0.28.0 | Job queue |
| structlog | 26.1.0 | |
| orjson | 3.12.0 | |
| pyjwt[crypto] | 2.13.0 | |
| cryptography | 50.0.1 | |
| sentry-sdk[fastapi] | 2.68.1 | |
| uuid-utils | 0.17.0 | UUIDv7 in the application (ADR-0010). **Native code — verify wheels for the deploy platform.** |
| ruff | 0.16.6 | |
| mypy | **2.3.1** | **Major version, 1.x → 2.x.** Expect stricter defaults. Budget time on the first `--strict` run. |
| pytest | 9.1.1 | |
| pytest-asyncio | 1.4.0 | |
| testcontainers[postgres] | 4.15.0 | |
| import-linter | 2.15 | Enforces ADR-0025 |
| pre-commit | 4.6.2 | |

## GitHub Actions

| Action | Version |
|---|---|
| actions/checkout | **v7** (7.0.1, July 2026) |
| actions/setup-node | v7 |
| astral-sh/setup-uv | v10 |
| docker/login-action | v3 |
| docker/build-push-action | v6 |

**pnpm is installed via `npm install -g pnpm@11.25.0`, not a third-party action.** One
less supply-chain surface and fully deterministic.

**Hardening step not yet taken:** pin actions by commit SHA rather than tag. Tags are
mutable; the tj-actions compromise is why this matters. Do it before handling production
secrets in CI.

## Two pins that are not "latest", and why

**`redis[hiredis]==5.3.1`, not 8.1.0.** `arq==0.28.0` (the current release) requires
`redis[hiredis]>=4.2.0,<6`. Pinning both at latest produces an unsatisfiable resolution:

```
Because arq==0.28.0 depends on redis[hiredis]>=4.2.0,<6 and your project depends
on redis==8.1.0, we can conclude that your project's requirements are
unsatisfiable.
```

Nothing we do needs a newer client. The sliding-window rate limiter, idempotency keys,
response cache and ARQ broker all work on 5.x, and redis-py 5.3.1 talks to a Valkey 8
server fine — the 6/7/8 client lines exist to track Redis *server* version numbering.
arq itself is current (April 2026, supports 3.13 and 3.14), so this is a client cap, not
an abandoned dependency. Revisit when arq lifts it.

Alternatives if the cap ever becomes a real constraint: `taskiq` + `taskiq-redis`
(asyncio-native, allows `redis>=8.0`) or `dramatiq` (allows `redis<9`, but not
asyncio-native).

**`psycopg[binary]` alongside `asyncpg`.** Not duplication. Migrations run synchronously
on psycopg because asyncpg sends every statement as a prepared statement and PostgreSQL
refuses multiple commands in one:

```
asyncpg.exceptions.PostgresSyntaxError:
    cannot insert multiple commands into a prepared statement
```

A baseline migration containing 600 lines of DDL cannot run on asyncpg at all. Alembic
has no reason to be async, so `migrations/env.py` rewrites the URL and runs sync.

## Services

| | Version |
|---|---|
| Postgres | **`pgvector/pgvector:pg17`**, not plain `postgres` |
| Valkey | 8 |

The baseline enables the `vector` extension (unused in v1, present so ADR-0023 discovery
needs no migration). `CREATE EXTENSION` fails outright when the extension is unavailable
— `IF NOT EXISTS` does not help — so `postgres:17-alpine` cannot run our migration.
Supabase ships pgvector in production.

## Upgrade policy

Monthly window, never reactive, except security patches. One upgrade per PR so a
regression is bisectable. Renovate or Dependabot opens PRs; they are batched and reviewed
in the window, not merged on arrival.

**Freeze all dependencies before any store submission.** Do not upgrade between building a
release candidate and shipping it.
