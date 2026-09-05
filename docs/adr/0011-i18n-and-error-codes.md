# ADR-0011: i18n and RTL from day one; backend returns error codes

**Status:** Accepted
**Date:** 2026-09-04

## Context

The product targets international markets. Retrofitting right-to-left support means
auditing every margin, padding, icon direction, animation and gesture in two front-end
codebases. Doing it at the start costs a linting rule and some discipline.

Separately: if the backend returns user-facing English strings, translation is spread
across three runtimes and the backend becomes a presentation layer.

## Decision

**Locales at launch:** `en`, `de`, `tr`. **`ar` is scaffolded** and ships with English
fallback content rather than machine translation. Arabic exists specifically so RTL
support is exercised in reality rather than in theory.

**Mobile:** `i18next` + `react-i18next`, detection via `expo-localization`, RTL via
`I18nManager`.

**Web:** `next-intl` with locale-prefixed routes, `dir` attribute on `<html>`.

**RTL discipline, enforced by lint, not by review:**

- Styles use `start`/`end`, never `left`/`right`. ESLint rule fails the build.
- Web CSS uses logical properties: `margin-inline-start`, never `margin-left`.
- Directional icons are mirrored via a wrapper component, never hardcoded.

**Backend returns error codes, never user-facing strings.** `CARD_LIMIT_REACHED`, not
"You've reached your card limit." Clients own all user-facing text. The full code registry
is in `contracts/error-codes.md`.

**Formatting** goes through `Intl` for dates, times, numbers and currency. Never manual.
Pluralization uses ICU rules — Arabic has six plural forms, German differs from English.

**User-generated content is never translated.** A card written in Turkish stays Turkish
regardless of viewer locale.

**The product name is `{{productName}}`,** interpolated from one variable per locale file,
never typed inline (see `00-context/naming.md`).

## Consequences

**Good.** Adding a locale is a translation file, not an engineering project.

**Good.** RTL works on day one instead of being a quarter of remediation later.

**Good.** Backend has zero presentation concerns and never needs a translation dependency.

**Bad.** Every string must be extracted from the moment it is written. This is friction on
every UI commit and it will be violated. The lint rule catching untranslated literals is
essential, not optional.

**Bad.** Error codes are less immediately helpful in logs and API debugging than
sentences. Mitigated by a developer-facing `message` field alongside the code, explicitly
documented as never being shown to users.

**Bad.** Four locales means four times the copy review for every feature.

## Alternatives considered

**English only at launch, i18n later.** Rejected explicitly on the
correct reasoning that RTL retrofitting is disproportionately expensive.

**Backend-rendered localized strings via `Accept-Language`.** Rejected: puts presentation
in the API, requires the backend to know client locale, and makes the same error render
differently in a log and on screen.
