# ADR-0028: On-device calendar context, not calendar sync

**Status:** Accepted
**Date:** 2026-09-08
**Related:** ADR-0012 (analytics and privacy), ADR-0027 (rejected `event_sessions`)

## Context

Meeting context is the most valuable thing this product captures. A contact with "wants a
demo of the reporting" attached is worth ten with a job title, and it is the field we push
to a CRM that makes the row better than one the user already had.

Today it is typed by the user in the ten seconds after an exchange. That works, and it is
also the step most likely to be skipped.

A calendar knows some of this already. "Coffee with Sarah @ Blue Bottle, 2pm" plus an
exchange at 2:17pm is enough to tag the connection without anyone typing anything.

The proposal was to sync the user's calendar and match exchanges against it.

## Decision

**Read a single calendar entry ON DEVICE, at the moment of exchange, and offer it as a
suggested tag. Do not sync calendars to the server.**

Concretely:

- At the moment an exchange succeeds, the mobile app queries the device calendar for the
  entry covering **now**
- If there is exactly one, its title is offered as a pre-filled suggestion alongside the
  note prompt
- The user accepts, edits, or ignores it
- **Nothing is stored unless they accept**, and what is stored is an ordinary tag or note
  — there is no new field, no new table and no server-side calendar state

No schema change. A suggested tag is a tag.

## Why not calendar sync

**The permission is enormous and permanent.** Calendar read access exposes every meeting
title, attendee list and location a person has: therapy appointments, job interviews,
medical visits, a divorce lawyer. We would be asking for continuous access to that in
order to improve a text field.

It is also a permission people reason about correctly. "This app wants to read your
calendar" prompts a different decision from "this app wants to know what you are doing
right now", and the difference is not a UI problem to be solved with better copy.

**Matching is fuzzy exactly where the day is busiest.** An exchange at 2:17pm sits inside
however many overlapping entries the person has. This is the same failure that made us
reject an `event_sessions` table in ADR-0027: timestamp-based inference degrades to
useless precisely at the large, dense events where the feature would matter most, and it
degrades silently — producing a plausible wrong answer rather than no answer.

**It does not serve the wedge.** A real estate agent at an expo does not have "booth 47"
in their calendar. Conference networking happens in corridors between sessions, not in
scheduled slots. Where calendar context genuinely helps is scheduled 1:1 business
meetings, which is a real use case but a different one from the event scenario the product
is built around.

**It would put us in scope for data we do not want.** Calendar contents are personal data
belonging to the user AND to every attendee named in them, none of whom have any
relationship with us. Storing that server-side creates processing obligations, subject
access implications and a breach surface, in exchange for a convenience.

## What the on-device version keeps

Most of the value, because the hard question is not "which of my meetings was this" — it
is "what was this about", and at 2:17pm the phone can answer that without searching
anything.

- **No ambiguity.** One entry, covering now, or nothing. There is no matching heuristic to
  get wrong.
- **A narrow permission, at a moment when the reason is obvious.** Asked after an exchange
  rather than at launch, like push (see `handoff/04-mobile-expo.md`).
- **No server-side calendar data at all.** What arrives is a tag the user chose to keep,
  indistinguishable from one they typed.

## Consequences

**Good.** Removes the most-skipped step in the highest-value flow, for a fraction of the
privacy cost. No schema change, no new domain, no migration.

**Good.** If it proves valuable, full sync becomes a decision with evidence behind it
rather than a guess. The reverse — building sync and discovering people decline the
permission — is expensive and unrecoverable.

**Bad.** It only fires when a calendar entry actually covers the moment. At a conference,
where most exchanges happen, that will often be a session title rather than the
conversation, which is weaker context than the user would type. The suggestion has to be
easy to reject or it makes the note field worse.

**Bad.** Calendar APIs differ between iOS and Android, so this is two implementations of
something that looks like one feature.

**Bad.** A session title as a suggestion may ANCHOR the user — they accept "Keynote:
Scaling Postgres" and never type "wants a demo of the reporting", which is the note that
was actually worth having. Worth measuring: if accepted suggestions correlate with shorter
notes, the feature is subtracting value and should be removed.

## Alternatives considered

**Full calendar sync with server-side matching.** Rejected above.

**Location instead of calendar.** "You were at the Berlin Congress Centre" needs continuous
location permission, which is a larger ask than the calendar for weaker context, and the
event is already known when the exchange is event-scoped.

**Asking the user to name the context once per event and reusing it.** Cheaper than
either, and worth doing regardless — but it produces one label for a whole day rather than
per-conversation context, so it complements this rather than replacing it.

## Revisit if

- Accepted suggestions measurably increase the proportion of connections that have notes,
  **and** those notes are not shorter than typed ones
- Users ask for it explicitly, in the 1:1 meeting scenario rather than the event one
- A provider offers a scoped "current event only" permission, which would change the
  privacy calculation entirely
