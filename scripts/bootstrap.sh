#!/usr/bin/env bash
# One command from a clean clone to a working stack.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Checking toolchain"
command -v node >/dev/null || { echo "Install Node 24 (see .nvmrc)"; exit 1; }
command -v docker >/dev/null || { echo "Install Docker"; exit 1; }

node_major=$(node -v | sed 's/v\([0-9]*\).*/\1/')
if [ "$node_major" != "24" ]; then
  echo "warning: Node $node_major detected, expected 24 (Active LTS). See ADR-0021."
fi

if ! command -v pnpm >/dev/null; then
  echo "==> Installing pnpm"
  npm install -g pnpm@11.25.0
fi

if ! command -v uv >/dev/null; then
  echo "==> Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

echo "==> Installing Node dependencies"
pnpm install --frozen-lockfile

echo "==> Installing Python dependencies"
(cd apps/api && uv sync --all-groups)

echo "==> Installing pre-commit hooks"
(cd apps/api && uv run pre-commit install --install-hooks)

if [ ! -f .env ]; then
  echo "==> Creating .env from template"
  cp .env.example .env
fi

echo "==> Starting Postgres and Valkey"
docker compose -f infra/compose/docker-compose.dev.yml up -d --wait

echo "==> Running migrations"
(cd apps/api && uv run alembic upgrade head)

echo "==> Generating tokens and API client"
pnpm generate

cat <<'MSG'

Ready.

  pnpm dev        all web apps + API
  pnpm mobile     Expo dev client (Expo Go is NOT sufficient - NFC needs native)
  pnpm test       everything

Read docs/handoff/00-shared-contracts.md before your first PR.
MSG
