# ADR-0013: Secrets: SOPS with age

**Status:** Accepted
**Date:** 2026-09-04

## Context

Production credentials must not live in `.env` files on a laptop, and a solo or small team
needs a secrets story that works at 3am during a rotation that broke something.

The application server is a Hetzner VM, so platform-native secret stores (as offered by
Vercel and Supabase) cover only part of the surface.

## Decision

**Local development:** `.env`, gitignored, with a committed `.env.example` listing every
variable name and no values. `detect-secrets` runs as a pre-commit hook so a real
credential cannot be committed.

**CI:** GitHub Actions encrypted secrets, scoped to environments. Staging deploys read
from the `staging` environment, production from `production` with a required manual
approval on the job.

**Production runtime: SOPS with age.** Secrets are encrypted and committed to the
repository. Decryption happens at deploy time using a key that exists in exactly two
places: on the server, and in the operator's password manager.

**Platform-native stores** are used directly for anything only that platform needs —
Vercel environment variables, Supabase project secrets.

Every secret has a documented rotation procedure (`runbooks/secret-rotation.md`).
Rotation-critical: JWT signing keys, database password, Stripe webhook signing secret,
Apple notification credentials.

## Consequences

**Good.** Version history and rollback for secrets. When a rotation breaks production at
2am, `git revert` is the recovery path.

**Good.** No runtime dependency. Deploy works when third-party services are down.

**Good.** Secret *names* are reviewable in pull requests; values are not.

**Bad.** Key loss is unrecoverable. The age key must exist in the password manager and in
a documented offline backup. This is the single highest-severity operational risk in the
system and is called out in `runbooks/backup-restore.md`.

**Bad.** Encrypted blobs in git are opaque to review. A diff shows that a secret changed,
never which.

**Bad.** Onboarding a second engineer means adding their age public key and re-encrypting.
Straightforward, but a step people forget.

## Alternatives considered

**Infisical or Doppler.** Nicer interface, generous free tier. Rejected: adds a runtime
dependency in the deploy path, which can be down exactly when a deploy is urgent.

**HashiCorp Vault.** Rejected: operationally disproportionate at this scale.

**Environment variables set manually on the host.** Rejected: no history, no review, no
recovery, and it drifts silently.
