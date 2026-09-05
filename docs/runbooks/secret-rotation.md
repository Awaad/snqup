# Runbook: Secret Rotation

**Last verified:** never
**Cadence:** annually, and immediately on suspected exposure.

## Why this is version-controlled

Secrets are SOPS-encrypted and committed (ADR-0013), so when a rotation breaks production
at 2am, `git revert` is the recovery path. That is the entire reason for the design.

## General procedure

```
sops infra/secrets/production.enc.yaml     # edit, encrypts on save
git commit && git push
gh workflow run deploy.yml -f environment=production
```

Verify `/health/ready` after every rotation.

## Order matters for dual-secret rotations

Anything with a verifying counterpart (JWT keys, webhook secrets) rotates in two phases.
Doing it in one phase causes an outage.

## JWT signing key

**Two-phase. One phase logs everyone out.**

1. Add the new key as a **secondary verification key**. Deploy. The API now accepts tokens
   signed with either.
2. Wait for the longest token TTL to elapse.
3. Promote the new key to primary for signing. Deploy.
4. Remove the old key. Deploy.

## Database password

1. Supabase dashboard → rotate
2. Update the SOPS secret
3. Deploy

Brief connection errors during the swap are expected. Do this outside a customer event.

## Stripe webhook signing secret

**Two-phase.** Stripe supports multiple active endpoint secrets.

1. Create the new secret in Stripe. Add it to our config as an **additional** accepted
   secret. Deploy.
2. Verify a real webhook is being accepted against the new secret.
3. Remove the old one from Stripe and from config. Deploy.

Reversing this order means rejecting live webhooks, which silently corrupts entitlement
state (`handoff/06-billing-entitlements.md`).

## Apple notification credentials

Follow Apple's key rotation. Test in **sandbox first**. A broken notification path means
subscriptions silently stop updating — there is no error, just stale entitlements. Verify
with a sandbox purchase after rotating.

## The age key

**Highest severity. Read `backup-restore.md` first.**

1. Generate the new keypair
2. Re-encrypt every secrets file to **both** old and new public keys
3. Distribute the new private key: server, password manager, offline backup
4. **Verify decryption with the new key on the server**, before removing the old
5. Only then re-encrypt to the new key alone

Never remove the old key before step 4 succeeds.

## After exposure

If a secret leaked (committed in plaintext, pasted somewhere, in a log):

1. **Rotate immediately.** Do not wait for a window.
2. Assume it was used. Check `audit_log` and access logs for the exposure period.
3. If it was committed to git, rotating is mandatory — history rewriting is not
   sufficient, because the value may already be cloned or cached.
4. `detect-secrets` should have caught it pre-commit. If it did not, fix the pattern.

## Checklist

- [ ] Rotated outside a customer event window
- [ ] Two-phase order followed where applicable
- [ ] `/health/ready` verified
- [ ] Webhook path verified with a real delivery, where relevant
- [ ] Sandbox purchase verified, for Apple credentials
- [ ] `Last verified` updated
