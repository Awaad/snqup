# Handoff: Design System

**Owns:** `packages/tokens/tokens.json` and the generation pipeline.

**Blocks:** Mobile and Web visual work. Start early.

## Single source, generated output (ADR-0017)

`tokens.json` → Style Dictionary →

- Tailwind theme extension for the Next.js apps
- Typed TypeScript theme object for Expo
- CSS custom properties for `public` and `marketing`

Generated output is committed. **CI regenerates and fails if it is stale.** Nobody
hand-edits generated files; the edit is silently reverted on the next build.

## Semantic naming only

`surface.raised`, `text.muted`, `border.subtle`. **Never `blue-500`.**

This is slower to work with when you just want "that blue", and it is the discipline that
makes dark mode and future theming mechanical rather than a rewrite.

## Categories

Colour (semantic), spacing on a 4pt scale, typography, radius, elevation, motion duration.

**Dark mode from day one.** Semantic tokens resolve per theme. Retrofitting is more
expensive than including it, because it forces the naming discipline that should exist
anyway.

## Platform divergence

Tailwind and React Native do not express everything identically. Shadows in particular
differ. Elevation tokens need a per-platform mapping rather than one shared value —
document the mapping in the package README so nobody "fixes" the difference.

## Card themes are NOT design tokens

This distinction matters and is easy to get wrong.

| | UI tokens | Card themes |
|---|---|---|
| What | Styles **our application** | **User data** |
| Stored | `tokens.json` | `cards.theme`, versioned |
| Changes | By us, in a PR | By users, any time |
| Old versions | Irrelevant | **Must still render** (ADR-0004) |

A card snapshot from six months ago must render with the theme it had. Conflating the two
would couple our design system's version history to user data.

`theme_version` exists on `cards` for exactly this. The renderer handles old versions.

## QR customisation (paid)

- **Error correction goes to level H when a centre logo is present**, or scanning degrades
  badly. That increases module count, which is another reason public tokens must stay short
  (ADR-0010).
- **Contrast is validated at save time and refused if insufficient.** Users will pick pale
  yellow on white and then report that scanning is broken.
- **Render client-side. Never store QR images.** Storing them means CDN invalidation on
  every colour change.
- Print export: SVG and 300 DPI PNG, quiet zone included.

## RTL

Every component must work mirrored. `start`/`end` in React Native, CSS logical properties
on web. Lint-enforced (ADR-0011).

Directional icons go through a mirroring wrapper, never hardcoded.

## Definition of done

- [ ] `tokens.json` generates cleanly to all three targets
- [ ] CI fails on stale generated output
- [ ] Dark mode complete, not partial
- [ ] Elevation mapping documented per platform
- [ ] QR renderer validates contrast and refuses bad combinations
- [ ] QR with centre logo uses error correction level H
- [ ] Every component verified mirrored in `ar`
- [ ] Card theme renderer handles `theme_version` 1 and an intentionally malformed input
