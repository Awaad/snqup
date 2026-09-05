# Error Code Registry

The backend returns codes; clients own the text (ADR-0011). This registry is the contract
between them.

**Adding a code:** add it here in the same PR as the implementation, and add the string to
all four locale files (`en`, `de`, `tr`, `ar`). CI fails if a code exists in the API but
not in the locale files.

**Never change a code's meaning.** Deprecate and add a new one.

## Format

`DOMAIN_CONDITION`, SCREAMING_SNAKE. The domain prefix lets clients group handling.

## Auth

| Code | HTTP | Meaning |
|---|---|---|
| `AUTH_TOKEN_INVALID` | 401 | JWT malformed, expired or signature failed |
| `AUTH_TOKEN_REVOKED` | 401 | Present in the denylist |
| `AUTH_INSUFFICIENT_SCOPE` | 403 | Authenticated but not permitted |
| `AUTH_ACCOUNT_DISABLED` | 403 | Account is in the deletion grace period |
| `AUTH_AGE_RESTRICTED` | 403 | Below the minimum age for the region |

## Cards

| Code | HTTP | Meaning |
|---|---|---|
| `CARD_NOT_FOUND` | 404 | |
| `CARD_LIMIT_REACHED` | 403 | Entitlement `card.limit` exceeded |
| `CARD_SLUG_TAKEN` | 409 | |
| `CARD_SLUG_RESERVED` | 422 | Matches `reserved_slugs` |
| `CARD_SLUG_INVALID` | 422 | Charset or length |
| `CARD_CUSTOM_FIELDS_NOT_ENTITLED` | 403 | |
| `CARD_QR_CONTRAST_INSUFFICIENT` | 422 | Chosen colours would scan unreliably |

## Tokens and scanning

| Code | HTTP | Meaning |
|---|---|---|
| `TOKEN_NOT_FOUND` | 404 | |
| `TOKEN_EXPIRED` | 410 | Live token past TTL |
| `TOKEN_REVOKED` | 410 | Rotated after a leak |
| `SCAN_SELF` | 422 | Scanning your own card |
| `SCAN_BLOCKED` | 403 | One party has blocked the other |
| `SCAN_RATE_LIMITED` | 429 | Rarely returned: scan limits fail open |

## Connections

| Code | HTTP | Meaning |
|---|---|---|
| `CONNECTION_NOT_FOUND` | 404 | Also returned when the caller is not a participant. Never distinguish — that would confirm the connection exists. |
| `CONNECTION_ALREADY_EXISTS` | 409 | Returns the existing connection |
| `CONNECTION_NOTE_CONFLICT` | 409 | Concurrent edit; both texts preserved |
| `CONNECTION_REMINDER_LIMIT_REACHED` | 403 | Free tier: 3 active |
| `CONNECTION_EXPORT_NOT_ENTITLED` | 403 | Workflow export. **Never returned for the GDPR export, which is always free.** |
| `CONNECTION_MERGE_INVALID` | 422 | Target is not a valid merge destination |

## Events

| Code | HTTP | Meaning |
|---|---|---|
| `EVENT_NOT_FOUND` | 404 | |
| `EVENT_CODE_INVALID` | 404 | |
| `EVENT_ATTENDEE_LIMIT_REACHED` | 403 | Organizer plan limit |
| `EVENT_NOT_STARTED` | 422 | |
| `EVENT_ENDED` | 422 | |
| `EVENT_EXPORT_NO_CONSENT` | 403 | No consent records in scope |
| `EVENT_COHORT_TOO_SMALL` | 422 | Aggregates suppressed below 10 attendees (ADR-0012) |

## Organizations

| Code | HTTP | Meaning |
|---|---|---|
| `ORG_NOT_FOUND` | 404 | |
| `ORG_SEAT_LIMIT_REACHED` | 403 | Enforced at invite time (ADR-0018) |
| `ORG_DOMAIN_NOT_VERIFIED` | 403 | |
| `ORG_ROLE_INSUFFICIENT` | 403 | |

## Billing

| Code | HTTP | Meaning |
|---|---|---|
| `BILLING_ENTITLEMENT_MISSING` | 403 | Generic; prefer a specific code where one exists |
| `BILLING_SOURCE_CONFLICT` | 409 | Active subscription on another source |
| `BILLING_WEBHOOK_INVALID` | 400 | Signature failed |

## Generic

| Code | HTTP | Meaning |
|---|---|---|
| `VALIDATION_FAILED` | 422 | `details` carries per-field errors |
| `RATE_LIMITED` | 429 | |
| `IDEMPOTENCY_KEY_REUSED` | 409 | Same key, different payload |
| `INTERNAL_ERROR` | 500 | `request_id` is the only useful field |
| `SERVICE_UNAVAILABLE` | 503 | Dependency down; client should retry with backoff |
