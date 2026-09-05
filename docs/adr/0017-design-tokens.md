# ADR-0017: Design tokens generated from a single source

**Status:** Accepted
**Date:** 2026-09-04

## Context

Two front-end runtimes with different styling systems: Tailwind on the web, StyleSheet
objects in React Native. Hand-syncing colour and spacing values between them means drift
within weeks, and the drift is visible to users as a product that looks subtly different
on each platform.

## Decision

**One source of truth: `packages/tokens/tokens.json`.** Style Dictionary generates:

- A Tailwind theme extension for the Next.js applications
- A typed TypeScript theme object for Expo
- CSS custom properties for the public and marketing sites

Generated output is committed. CI regenerates and fails if the committed output is stale.

**Token categories:** colour (semantic names only — `surface.raised`, `text.muted`, never
`blue-500`), spacing on a 4pt scale, typography, radius, elevation, motion duration.

**Dark mode from day one.** Semantic tokens resolve per theme. Retrofitting dark mode is
more expensive than including it, because it forces the semantic naming discipline that
should exist anyway.

**Card themes are a different concept and are kept separate.** UI tokens style *our
application*. Card themes are *user data*: stored per card, versioned, and rendered inside
card snapshots (ADR-0004). A snapshot from six months ago must still render with the theme
it had. Conflating the two would couple our design system's version history to user data.

## Consequences

**Good.** A colour change is one edit and a regeneration, applied everywhere consistently.

**Good.** Semantic naming makes dark mode and future theming mechanical.

**Good.** Card theme versioning is isolated from the design system.

**Bad.** Style Dictionary is a build step, and a stale generated file is a confusing build
failure until the developer learns the workflow. Document it prominently in the
design-system handoff.

**Bad.** Semantic naming is slower to work with than literal colour names, and requires
discipline when someone just wants "that blue".

**Bad.** Tailwind and React Native do not express every concept identically. Shadows in
particular differ, and elevation tokens need a per-platform mapping rather than a shared
value.

## Alternatives considered

**Tamagui or a unified cross-platform styling library.** Rejected: significant framework
lock-in and a performance profile that needs proving, to solve a problem that a build step
solves.

**Manual synchronisation with a review checklist.** Rejected: this is the drift.
