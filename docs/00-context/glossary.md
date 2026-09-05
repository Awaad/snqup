# Glossary

Terms with a specific meaning in this codebase. Where two things sound similar, the
distinction is usually the point.

| Term | Meaning |
|---|---|
| **Identity anchor** | The `users` row. Id and timestamps only, **no personal data**. Survives erasure so the counterparty keeps their record. |
| **User profile** | The `user_profiles` row. All personal data about a user. Erasure is a single DELETE of it. If a column is personal data, it belongs here. |
| **Card** | A user's context-specific identity (personal, business, custom). A user has several. |
| **Snapshot** | Immutable copy of a card as it was at exchange time, stored on the connection. Not the live card (ADR-0004). |
| **Live token** | Short-TTL token rendered in-app only. Scanning it produces a **symmetric** exchange (ADR-0002). |
| **Static token** | Long-lived revocable token for badges, exports, NFC, Wallet. **One-way only.** |
| **Connection** | The immutable edge between two users. Never carries private data (ADR-0003). |
| **Connection view** | One per participant. Notes, tags, reminders. Private to its owner. |
| **Anonymous scan** | A scan by someone with no account. The majority path. |
| **Pending exchange** | A one-way scan awaiting the owner's action, or a non-user reply awaiting claim. |
| **Claim** | A non-user converting their reply into an account and taking ownership of the pending connection. |
| **Entitlement** | A resolved capability, e.g. `connection.export`. The **only** thing authorization reads (ADR-0009). |
| **Subject** | A user or an organization. Entitlements attach to subjects, not to users specifically. |
| **Organization** | Durable tenant owning brand, seats and member cards. |
| **Event** | Temporary tenant with its own staff. May belong to an organization or to a solo user (ADR-0018). |
| **Event staff** | Roles scoped to one event. Distinct from organization roles. A day-hire scanner is event staff. |
| **Roster** | Organizer-uploaded registration list, used to report against expected attendance. |
| **Scan channel** | How an exchange arrived: `qr_live`, `qr_static`, `nfc`, `link`, `wallet`. Recorded from v1; retrofitting loses history. |
| **UGC domain** | The separate domain serving public card and scan pages. Isolated so a Safe Browsing hit cannot take down the API (ADR-0008). |
| **Link-in-bio page** | Public, guessable, indexable page at `/u/<slug>`. |
| **Scan-resolution page** | Non-guessable `noindex` page at `/c/<token>`. Different page, different exposure. |
| **GDPR export** | Article 20 obligation. **Always free.** |
| **Workflow export** | Product feature. Paid. Different name and entry point on purpose (ADR-0020). |
| **Purge job** | Daily hard-delete of soft-deleted rows. **Soft delete alone is not erasure.** |
| **Activation** | First successful exchange. The metric that matters. |
| **Placeholder** | `acme` / `example.com`. Removed by `runbooks/rename-product.md`. |
