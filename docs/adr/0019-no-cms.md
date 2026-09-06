# ADR-0019: No CMS; constrained editor writing to our own database

**Status:** Accepted, extended by ADR-0027
**Date:** 2026-09-04

## Context

Event pages need editable content, which raises the question of a headless CMS (Sanity,
Payload, Strapi).

An event page has roughly eight fields: title, description, banner image, date, venue, an
optional rich-text block, and links. That is a form.

## Decision

**No CMS for event pages.** A form in the existing organizer dashboard writes to the
existing Postgres tables, using the existing authentication and authorization.

Rich text uses **Tiptap**, constrained to a small node set (headings, paragraph, list,
link, bold, italic), storing **sanitized JSON** — never raw HTML. Raw user HTML on a public
domain is an XSS hole, and the UGC domain is already the highest-risk surface (ADR-0008).

Rendering sanitizes again on output. Sanitize on write and on read; assume the stored data
is hostile.

**The marketing site is a separate concern.** MDX files in the repository, edited by pull
request. This is sufficient for a long time and requires no additional service.

Revisit only if non-technical staff need to publish marketing content without a deploy.

**ADR-0027 makes that revisit cheap.** Presentational content lives in `event_content`,
separate from the operational fields in `events`, and is read through
`EventsService.content_for()` rather than joined from a router. Swapping the storage for a
CMS, or for structured session tables, becomes a resolver change that touches neither
`events`, the dashboard, nor the API shape.

## Consequences

**Good.** No additional service, no second authentication system, no webhook to invalidate
caches, no additional failure mode in the path of our highest-value customer surface.

**Good.** Event content is in the same database as event data, so a single query renders a
page and no consistency problem exists between content and data.

**Good.** A constrained node set closes the XSS surface far more effectively than
sanitizing arbitrary HTML.

**Bad.** Any new content field is a schema migration plus a form change, where a CMS would
be a configuration change. Accepted: the field set is small and stable.

**Bad.** No editorial workflow — no drafts, no scheduled publishing, no revision history.
If organizers ask for these, revisit.

**Bad.** Marketing content changes require a deploy, which is friction for whoever writes
the copy.

## Alternatives considered

**Sanity or Payload.** Rejected: a second service, second auth system, cache invalidation
webhooks, and a new failure mode, to replace a form.

**Storing rich text as HTML.** Rejected: XSS on the UGC domain is the highest-consequence
vulnerability available in this product.
