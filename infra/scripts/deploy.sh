#!/usr/bin/env bash
# Health-gated rolling replacement. If readiness never passes, the old
# container keeps serving and the deploy aborts (runbooks/deploy.md).
set -euo pipefail

: "${TAG:?TAG is required}"
: "${HOST:?HOST is required}"
: "${SSH_KEY:?SSH_KEY is required}"

key=$(mktemp)
trap 'rm -f "$key"' EXIT
printf '%s' "$SSH_KEY" > "$key"
chmod 600 "$key"

ssh -i "$key" -o StrictHostKeyChecking=accept-new "$HOST" bash -s <<REMOTE
set -euo pipefail
cd /srv/acme

# Secrets are SOPS-encrypted in the repo and decrypted at deploy time with a
# key that exists only here and in the operator's password manager (ADR-0013).
sops -d infra/secrets/\${ENVIRONMENT:-production}.enc.yaml > .env

export TAG="$TAG"
docker compose pull api worker
docker compose up -d --no-deps --wait --wait-timeout 90 api worker
docker image prune -f --filter "until=168h"
REMOTE
