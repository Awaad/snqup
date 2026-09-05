# Runbook: GDPR and Data Subject Requests

**Last verified:** never
**Statutory deadline:** one month from receipt. Do not let it drift.

## Two exports that must never be confused

| | GDPR export | Workflow export |
|---|---|---|
| Basis | Article 20 obligation | Product feature |
| Price | **Always free** | Paid tier |
| Scope | Complete account data | Filtered selections |
| Format | JSON, machine-readable | vCard / CSV |
| Location | Settings → Privacy | Connections screen |

**Never return `CONNECTION_EXPORT_NOT_ENTITLED` for a GDPR request.** Gating the paid
export must never read as gating a legal right. They have different names, different entry
points and different copy for exactly this reason (ADR-0020).

## Access / portability (Article 15, 20)

Self-service in-app. It writes `data_export_requests`, a job assembles the archive, and a
signed expiring link is emailed.

Contents: profile, cards, tokens, connections **from the requester's side**, their
`connection_views`, event memberships, consent records, notifications, audit entries.

**Not included:** the other party's `connection_views`. Those are the counterpart's
personal data, not the requester's.

If a request arrives by email rather than in-app, verify identity before acting — an
unverified request is itself an attack vector. Verification is confirming control of the
registered email, not asking for ID.

## Erasure (Article 17)

Self-service in-app. Apple guideline 5.1.1(v) requires it and there must be no dark
patterns.

1. **Immediate:** account disabled, tokens revoked, public pages 404, cards unresolvable
2. **30-day grace period**, clearly communicated, during which the user can restore
3. **After 30 days:** the purge job hard-deletes account, cards, tokens, views, analytics

**Soft delete alone does not satisfy erasure.** The purge is what makes it real. If the
purge job has been failing, erasure has not happened, whatever the UI said. It alerts on
failure for this reason.

### What survives, and why

**Card snapshots held by counterparts are retained** (ADR-0020). Our documented position:
a snapshot is equivalent to a paper business card voluntarily handed over, and the
recipient retains it. Their connection history is their own record of a meeting that
happened.

This is disclosed in the privacy policy and at exchange time. **It may be challenged.** If
it is, the fallback is to tombstone the snapshot to name only — do not improvise a
different answer under pressure, and do not agree to full deletion without consulting
counsel, because it silently rewrites other users' records.

## Rectification (Article 16)

Users edit their own cards. If the complaint concerns a snapshot held by someone else,
explain the retention position above. The live card is always correct; the UI shows
"details changed since you met".

## Objection to processing (Article 21)

Most commonly: marketing email. `users.consent_marketing` is separate from
`consent_transactional` precisely so this is a one-field change without disabling
account-essential mail.

## Organizer attendee exports

An organizer may only export attendees with a `event_export_consents` record in scope.
Consent is revocable, and revocation removes the attendee from **future** exports.

**We cannot claw back an already-downloaded CSV.** This is stated in the consent copy and
the organizer terms. If a user asks, say so plainly — it is a documented limitation, not
something to talk around.

## Breach notification

A notifiable breach must reach the supervisory authority **within 72 hours of awareness**.
The clock starts when you become aware, not when you fix it. See
`incident-response.md`.

## Log

Every request is recorded in `audit_log`: type, subject, received, completed, outcome.
This is the evidence that the process works.
