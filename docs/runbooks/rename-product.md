# Runbook: Rename from Placeholder

**Last verified:** never
**Run once**, when the name is chosen.
**Estimated:** 20 minutes mechanical, plus external account setup.

## Preconditions

The name is only settled once all of these pass. Do not start before they do.

- [ ] USPTO TESS and EUIPO cleared, classes 9 and 42
- [ ] **Both** domains available: application domain and the separate UGC domain (ADR-0008)
- [ ] App Store and Play Store name collisions checked
- [ ] Social handles available

## Why this is cheap

The placeholder policy (`00-context/naming.md`) kept `acme` out of everything expensive:
no durable external identifiers were created, no user-visible strings contain it, and it is
absent from the database schema. CI has been enforcing this.

If any of those were violated, this runbook takes hours instead of minutes. Check first:

```
./scripts/check-placeholder.sh
```

This is the same check CI runs. It verifies the placeholder never leaked into locale
files or migrations, which are the two places it would be expensive. Everything else
(`infra/`, application code, package names) is handled by the grep-and-replace below and
costs nothing.

## Mechanical rename

```
grep -rln "acme" . --exclude-dir=node_modules --exclude-dir=.git
```

Then replace across:

- `@acme/*` package scope in every `package.json` and import
- Python package `acme` and its imports
- `com.acme.app` in `app.json` (Expo), `build.gradle`, Xcode project
- Docker image prefixes
- Database name in Compose and connection strings
- `example.com` / `example.net` throughout

Verify:
```
grep -rn "acme" . --exclude-dir=node_modules --exclude-dir=.git
```
Should return nothing.

Then remove the placeholder lint rule from CI, since it will now fail on nothing and
becomes noise.

## User-visible name

One variable, one place per locale:

```json
{ "productName": "TheName" }
```

Four locale files: `en`, `de`, `tr`, `ar`. Nothing else changes, because no string was
ever typed inline.

## External accounts (the slow part)

These are created **after** the name is final and take real calendar time:

- [ ] Register both domains; DNS through Cloudflare
- [ ] App Store Connect application record with the final bundle ID
- [ ] Play Console application record
- [ ] **Start the 14-day closed testing clock immediately** — this is calendar time you do
      not control
- [ ] Apple push certificates against the final bundle ID
- [ ] Stripe products and prices
- [ ] Sentry and PostHog project renames
- [ ] Email sending domain: **SPF, DKIM and DMARC configured before sending anything to a
      real user**, or Gmail will drop the mail

## Verify

- [ ] Full CI passes
- [ ] Mobile builds on both platforms with the new identifiers
- [ ] Deep links resolve
- [ ] Deploy to staging succeeds
- [ ] Product name renders correctly in all four locales, including RTL
