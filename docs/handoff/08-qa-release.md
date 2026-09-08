# Handoff: QA and Release

**Status: current.** Nothing has been submitted to either store. The name still blocks App Store Connect and Play Console records.

**Owns:** the release checklist, store submission, the pilot event, activation
instrumentation.

## Why this is a separate document

ADR-0022 records the decision to ship everything in one release. That makes **Apple review
the last gate for the entire product**. A rejection blocks the web product, the organizer
dashboard and billing, all of which will be finished and waiting.

The mitigations below are not optional. They are the price of the single-release decision.

## Mandatory mitigations

**1. Feature flags on every major surface** (PostHog). The binary can be submitted while
surfaces are still being finished, and a broken surface can be disabled without a
resubmission. This is the single most important mitigation.

**2. Submit a throwaway build to Apple review early**, well before feature completion,
purely to discover objections. A rejection at that point is cheap information. A rejection
two days before the pilot is not.

**3. Run a pilot at the halfway point** with whatever exists. The category question — do
people install, do they exchange, do they return — is answerable with a partial product,
and the answer changes what gets built in the second half.

## App Store review risk

This app's core function is exchanging third-party personal data. **Guideline 5.1.2 will be
read carefully.** Budget for at least one rejection.

Treat these as engineering work with owners, not paperwork discovered at submission:

- [ ] **In-app account deletion**, no support contact, no dark patterns (5.1.1(v))
- [ ] **Privacy nutrition labels accurate.** We collect contact data, a scrutinised
      category. An inaccurate label is a rejection and a trust problem.
- [ ] **Working demo account** for the reviewer, with seeded connections and an event
- [ ] **Live privacy policy URL** before submission, not after
- [ ] **Explicit consent flows visible in the UI**, not buried in terms
- [ ] Reviewer notes explaining the two-tier QR model, because it is unusual and
      reviewers will otherwise assume the worst

Google Play requires **14 days of closed testing** before production access. Start that
clock early; it is calendar time you do not control.

## Activation instrumentation

Instrument from the first build, not before launch (ADR-0024):

```
install → account created → first card created → first QR shown
       → FIRST SUCCESSFUL EXCHANGE → returned within 7 days
```

**Step five is activation. Everything before it is setup, everything after is retention.**
Everything else is vanity.

Also: scan channel attribution, exchange success/failure by channel, time-to-first-
exchange, and fallback-page conversion (scan → vCard save).

## Release checklist

**Before a release candidate:**
- [ ] All dependencies frozen (ADR-0021). No upgrades between RC and ship.
- [ ] Migration run against a production-sized copy in staging
- [ ] **Recovery from fresh install with only an email verified** — the entire value of
      the app is the contacts
- [ ] Offline exchange verified in airplane mode, including force-quit during sync
- [ ] RTL verified in `ar` on both platforms
- [ ] Scan-resolution page under 1s on throttled 3G
- [ ] vCard save verified on iOS Safari, Android Chrome, **and in-app browsers**
      (Instagram, LinkedIn) — these break things and are the common path
- [ ] Purge job verified on staging data
- [ ] Apple sandbox and Stripe test purchases both grant entitlements
- [ ] No price string in the codebase; no organizer upsell in the mobile bundle
- [ ] Sentry receiving readable native crashes with source maps

**Before production deploy:** see `runbooks/deploy.md`.

## Pilot success criteria

At the first live event (`runbooks/event-day.md`):

- \>30% of attendees install
- \>50% of installs complete an exchange
- \>20% return within a week without a push

Below 10% install with no return usage means the problem is the category, not the
execution, and no amount of engineering fixes it. That is a finding worth having early,
which is the entire argument for the halfway pilot.

## Definition of done

- [ ] Release checklist executed and dated for every submission
- [ ] Feature flags cover every major surface and have been tested off
- [ ] Activation funnel visible in PostHog, verified against real events
- [ ] Demo account seeded and working
- [ ] Pilot run, results recorded against the criteria above
