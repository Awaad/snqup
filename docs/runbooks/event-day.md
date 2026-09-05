# Runbook: Customer Event Day

**Last verified:** never

## Why this runbook exists

An event is the one time a paying customer watches the product work in real time, in front
of their attendees, with no opportunity to fix things quietly. **Severity escalates by one
during an event.** A broken dashboard is SEV3 normally and SEV2 here.

Most failures on event day are self-inflicted: a deploy, an expired credential, a rate
limit doing its job at the wrong moment.

## The week before

- [ ] Confirm the event in the calendar so nobody deploys into it
- [ ] Verify the organizer's plan covers expected attendance (`event.attendee_limit`).
      Hitting the cap mid-event is a terrible customer moment.
- [ ] Verify their entitlements resolve: dashboard, export, announcements
- [ ] If they imported a roster, confirm it loaded and matched
- [ ] Confirm the event's IANA timezone is correct — the post-event digest fires 24h after
      `ends_at` **in local time**, and analytics windows depend on it
- [ ] Check certificate expiry (alerts fire at 14 days; confirm anyway)

## The day before

- [ ] **Freeze deploys.** No production changes from now until the event ends.
- [ ] Verify Valkey memory headroom
- [ ] Verify the worker is consuming and the queue is empty
- [ ] Load-check the expected scan volume against rate limits. **Scan limits fail open by
      design** (ADR-0007), so the failure mode is an unthrottled endpoint rather than
      blocked attendees — confirm that is still true after any recent change.
- [ ] Confirm the venue's rough network conditions if you can. Offline mode exists for
      this, but knowing in advance changes how you triage.

## During

Watch:
- Exchange success rate by channel
- SSE dashboard connection count
- API error rate in Sentry
- Queue depth

**If the dashboard freezes:** the LISTEN connection is the usual cause (ADR-0006). Restart
the API container; SSE clients reconnect automatically via `Last-Event-ID` and counts
resume correctly. This is fast and safe.

**If exchanges are failing:** check whether it is network (attendees offline, which the
app handles — connections queue and sync later) or server. Offline failures are invisible
to us and resolve themselves.

**If a rate limit is firing on legitimate traffic:** it should be failing open. If it is
not, disable the specific limit rather than raising it. Do not debug it live.

**Do not deploy to fix something mid-event** unless it is SEV1 and the fix is a feature
flag. A flag toggle is instant and reversible; a deploy is neither.

## After

- [ ] Confirm the post-event digest fires 24h after `ends_at` in the event's timezone. This
      is the single best retention mechanic in the product and it only gets one chance.
- [ ] Check the organizer's export works if they have consent records
- [ ] Record the numbers against the pilot criteria (`handoff/08-qa-release.md`):
      install rate, exchange rate, 7-day return
- [ ] Unfreeze deploys
- [ ] Ask the organizer what was confusing. This is the only time you get honest,
      specific feedback about the dashboard, which was built before any organizer had used
      it (ADR-0022).
