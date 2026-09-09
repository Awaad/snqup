# Runbook: Verify auth against a real Supabase project

**Last verified: never.** Every step below is written from the API's actual behaviour, but
none has been run against a live Supabase project. Expect at least one surprise, and
correct this document when you find it.

**Why this exists.** The auth path has three moving parts that only meet in production:
Supabase's key type, the claims it actually issues, and our provisioning-on-first-request.
A bug in any of them shows up as "nobody can sign in", which is the worst possible time to
be discovering how the pieces fit.

---

## Step 0 — Find out which kind of project you have

**Do this first. It decides your entire configuration**, and getting it wrong fails every
request rather than some of them.

Supabase projects come in two shapes:

| | Signing | JWKS endpoint | What to set |
|---|---|---|---|
| **Asymmetric** (default for new projects) | ES256 / RS256 | yes | `JWT_ISSUER` only |
| **Legacy JWT secret** | HS256, shared secret | **none** | `JWT_SHARED_SECRET` |

```bash
curl -s https://<project-ref>.supabase.co/auth/v1/.well-known/jwks.json | head -20
```

A key set means asymmetric. A 404 or an empty `keys` array means the legacy secret, and
`JwksVerifier` would fail every request with `unknown signing key` — which reads like a key
rotation problem and is not.

**Prefer asymmetric.** A shared secret lets anyone holding it *mint* tokens, not merely
verify them. 

## Step 1 — Configure

```bash
# apps/api/.env
JWT_ISSUER=https://<project-ref>.supabase.co/auth/v1
JWT_AUDIENCE=authenticated          # Supabase's default. Confirm in Step 3.

# Asymmetric projects: nothing else. JWKS is derived from the issuer.
# Legacy projects only:
# JWT_SHARED_SECRET=<Settings → API → JWT Secret>
```

`JWT_ISSUER` must match the `iss` claim **exactly**, including `/auth/v1`. A trailing
slash mismatch fails verification with a message about the issuer, which is at least
honest.

## Step 2 — Get a real token

Easiest is the Supabase dashboard: create a test user under Authentication → Users, then:

```bash
curl -s -X POST 'https://<project-ref>.supabase.co/auth/v1/token?grant_type=password' \
  -H "apikey: <anon-key>" \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"<password>"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
```

Keep that token in a shell variable; every step below uses it.

## Step 3 — Read the claims before trusting anything

```bash
python3 - "$TOKEN" <<'EOF'
import base64, json, sys
header, payload, _ = sys.argv[1].split('.')
pad = lambda s: s + '=' * (-len(s) % 4)
print("alg:", json.loads(base64.urlsafe_b64decode(pad(header)))["alg"])
print(json.dumps(json.loads(base64.urlsafe_b64decode(pad(payload))), indent=2))
EOF
```

**Check four things**, because each one silently breaks a different part of the system:

- `alg` — `ES256`/`RS256` means asymmetric, `HS256` means the legacy secret. It must match
  what you concluded in Step 0
- `iss` — must equal `JWT_ISSUER` character for character
- `aud` — usually `authenticated`. If it differs, set `JWT_AUDIENCE` to match
- **`email` must be present.** We refuse to provision without one, because every
  downstream feature — digests, CRM sync, account recovery — assumes it. Phone and
  anonymous sign-in produce tokens with no email and are rejected by design

## Step 4 — The test that matters: a brand-new user

```bash
curl -i http://localhost:8000/v1/me -H "Authorization: Bearer $TOKEN"
```

**Expect `200` and a profile**, for a user that has never hit the API before.

This is the step worth doing carefully, because it is where the API was broken:
`provision()` existed and nothing called it, so a valid token returned
`AUTH_ACCOUNT_DISABLED` and **nobody could sign up**. Every test in the suite called
`provision()` directly, which is exactly why none of them caught it.

Confirm the rows exist:

```sql
SELECT u.id, p.auth_subject, p.email, p.created_at
FROM users u JOIN user_profiles p ON p.user_id = u.id
ORDER BY p.created_at DESC LIMIT 5;
```

`auth_subject` must equal the `sub` claim. If it does not, tokens and users will diverge
the moment anyone signs in on a second device.

## Step 5 — Prove enforcement, not just success

A working happy path proves very little. Each of these must fail:

```bash
# No token
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/v1/cards
# → 401

# Garbage token
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/v1/cards \
  -H "Authorization: Bearer not-a-token"
# → 401

# A token from a DIFFERENT Supabase project, signed by a real key that is not yours.
# This is the important one: it proves we verify the issuer and the signature,
# not merely that a token parses.
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/v1/cards \
  -H "Authorization: Bearer $OTHER_PROJECT_TOKEN"
# → 401

# An expired token (wait it out, or set a short JWT expiry in the dashboard)
# → 401
```

Then confirm the public surface still works with **no** token at all:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8000/v1/scan/some-token
# → 404, NOT 401. Public routes must not require auth.
```

A `401` there means a public route slipped behind the auth dependency, and the anonymous
scan path — the majority of all traffic — is broken.

## Step 6 — Deletion must not be reversible by signing in again

```bash
curl -X DELETE http://localhost:8000/v1/privacy/account -H "Authorization: Bearer $TOKEN"
curl -i http://localhost:8000/v1/me -H "Authorization: Bearer $TOKEN"
# → 403 AUTH_ACCOUNT_DISABLED
```

**This is the subtle one.** Provisioning-on-first-request means a deleted account could be
silently recreated by the next request, handing someone a fresh empty profile carrying
their old id. The API distinguishes "no row" (provision) from "row with `deleted_at`"
(refuse). Verify it rather than trusting it.

## Step 7 — Key rotation, if you are on asymmetric keys

Rotate the signing key in the Supabase dashboard, get a fresh token, and call `/v1/me`
again. It should succeed **without a redeploy**.

`JwksVerifier` caches keys for ten minutes and refetches once when it sees an unknown
`kid`. If this fails, the deployment is pinned to a static key somewhere and the next real
rotation will log every user out at once.

---

## Things that will bite

**`JWT_AUDIENCE` is not always `authenticated`.** Some configurations issue a different
audience, and the failure message says "verification failed" without saying which claim.
Step 3 is how you avoid an hour on this.

**The anon key is not a JWT you can use here.** It is a Supabase API key with its own
format and role, and passing it as a bearer token produces a confusing failure. Use a real
user's `access_token`.

**Service-role tokens must never reach this API.** They carry `role: service_role` and
bypass Supabase's own row-level security. Nothing here checks for that, because nothing
here should ever see one — but if you find one working, treat it as a finding rather than
a convenience.

**JWKS is fetched at runtime**, so the API needs egress to `<project-ref>.supabase.co`. In
a locked-down network that fails as "unknown signing key" rather than as a network error,
which sends you looking in the wrong place entirely.
