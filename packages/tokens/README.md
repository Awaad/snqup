# @acme/tokens

Single source of truth for design tokens (ADR-0017).

`tokens.json` -> Style Dictionary -> Tailwind theme, React Native theme object, CSS
custom properties.

## Rules

**Never hand-edit `dist/`.** CI regenerates and fails on drift; a hand edit is silently
reverted on the next build.

**Semantic names only.** `surface.raised`, never `blue-500`. This is slower when you just
want "that blue", and it is the discipline that makes dark mode and future theming
mechanical rather than a rewrite.

**Card themes are NOT design tokens.** UI tokens style *our application*. Card themes are
*user data*: stored per card, versioned, and rendered inside card snapshots (ADR-0004). A
snapshot from six months ago must render with the theme it had.

## Platform divergence

Tailwind and React Native do not express shadows identically. Elevation tokens need a
per-platform mapping rather than one shared value. That mapping lives in `config.json` -
do not "fix" the difference.
