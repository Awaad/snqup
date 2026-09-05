# ADR-0022: Single-release strategy with feature flags

**Status:** Accepted
**Date:** 2026-09-04

## Context

A phased release was recommended: ship the exchange and public card surface first, then
the native app, then events and organizers.

That decision is recorded here with its consequences, because those consequences need
active mitigation rather than passive acceptance.

The risks are real. Apple review becomes the last gate for the entire product rather than
one surface, and an app whose core function is exchanging third-party contact data will be
read carefully under guideline 5.1.2. If it is rejected, the web product, the organizer
dashboard and billing all sit finished and unlaunched.

Separately, the organizer dashboard is designed before any organizer has been observed
using anything.

## Decision

Ship all surfaces in one release, with these mandatory mitigations:

**1. Every major surface ships behind a feature flag** (PostHog, ADR-0024). The binary can
be submitted while individual surfaces are still being finished, and a broken surface can
be turned off without a store resubmission. This is the single most important mitigation
and it is not optional.

**2. An early throwaway build is submitted to Apple review** well before feature
completion, purely to discover objections. A rejection at that point is cheap information.
A rejection two days before a pilot event is not.

**3. Store review requirements are treated as engineering work with owners**, not
paperwork discovered at submission: in-app account deletion, accurate privacy nutrition
labels, a working demo account for the reviewer, a live privacy policy URL, and explicit
consent flows visible in the UI.

**4. The organizer dashboard's analytics surface is explicitly a hypothesis.** Budget a
rewrite after the first real event. Do not gold-plate it before then.

**5. A pilot runs at the halfway point** with whatever exists, rather than waiting for
completion. The category-validation question (do people install, do they exchange, do they
return) is answerable with a partial product and the answer changes what gets built in the
second half.

## Consequences

**Good.** One coherent launch, one marketing moment, no partial product to explain.

**Good.** Feature flags are useful permanently, not just for this.

**Bad.** Longest possible time to first real user feedback. Every surface is designed
against assumptions.

**Bad.** Apple review gates everything. A rejection blocks the entire product.

**Bad.** The largest possible scope before the first validation signal. If the category
does not work, the maximum amount of work has been done before finding out.

**Bad.** Integration risk concentrates at the end, when all surfaces meet for the first
time.

## Alternatives considered

**Phased: web and exchange first, then mobile, then events.** Recommended and rejected by
the product owner. It would have reached a real user in weeks rather than months, and
worked for 100% of scanners rather than only those with the app installed. Recorded here
because if the timeline slips badly, this remains the available fallback — the schema and
API are designed to support it without rework.
