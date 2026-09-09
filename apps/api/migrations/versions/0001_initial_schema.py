"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-05

The baseline. One revision, deliberately (schema/migration-policy.md).

Expand-contract exists to protect deployed clients from schema changes. At
baseline there are none, so splitting this into two dozen revisions would
manufacture a history that never ran anywhere and that all have to keep working
forever for a `downgrade base` nobody will use.

Every change AFTER this one is its own migration under the full policy.

The DDL is embedded verbatim rather than read from schema/schema.sql. A
migration must be immutable: if it read the file, editing that file would
retroactively change what this revision does. schema/schema.sql is the
human-readable reference; this is the executable truth.

This schema was reviewed against PostgreSQL 16.15 before being written, and
eight defects were found and fixed. See schema/review-2026-09-05.md. The
invariants behind those findings are enforced by
tests/test_schema_invariants.py, which runs in CI.

Migration checklist:

  [x] Backwards-compatible with the oldest supported app version
      Baseline. There is no older version.
  [x] Locks: creates only. No existing table is touched.
  [x] Indexes: created with the tables, before any data exists. CONCURRENTLY is
      not needed here and would be slower.
  [x] Down-migration: drops everything. Safe only because this is the baseline.
  [x] Tables over 1M rows: none.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCHEMA = """\
-- =============================================================================
-- CANONICAL SCHEMA v1
-- =============================================================================
-- This file is the reference. The executable source of truth is the Alembic
-- migration chain in apps/api/migrations/. If they disagree, this file is stale
-- and must be corrected in the same PR that discovered the gap.
--
-- Conventions, enforced in review:
--   - Primary keys are UUIDv7, generated in the application (ADR-0010)
--   - All timestamps are timestamptz, always UTC. Never `timestamp`. (ADR-0012)
--   - Money is integer minor units with an explicit currency code. Never float.
--   - Soft delete everywhere: deleted_at. The purge job hard-deletes at 30 days
--     (ADR-0020). Soft delete alone does not satisfy erasure.
--   - Table and column names describe the domain, never the product name
--     (00-context/naming.md)
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "citext";
-- Enabled but unused in v1. Event-scoped discovery will want embeddings for
-- industry similarity (ADR-0023). Enabling later is trivial but easy to forget.
CREATE EXTENSION IF NOT EXISTS "vector";


-- =============================================================================
-- TRIGGERS
-- =============================================================================

-- updated_at was previously set only at INSERT and never moved, which made it
-- decorative. Several things depend on it being real: sync conflict resolution
-- (ADR-0016), cache invalidation on public card pages, and "details changed
-- since you met" (ADR-0004). Enforced in the database because an application
-- that forgets one UPDATE path produces silently stale data.
-- clock_timestamp(), NOT now(). now() returns the TRANSACTION start time, so a
-- row inserted and then updated inside one transaction gets an updated_at
-- identical to its created_at, and the change is invisible to anything
-- comparing them. clock_timestamp() is actual wall time at trigger execution,
-- so updated_at always reflects a real modification.
--
-- Found by writing the test for this trigger, not by reading it.
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = clock_timestamp();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- =============================================================================
-- CONSTRAINED VALUE SETS
-- =============================================================================
-- text + CHECK, NOT Postgres enums.
--
-- We are pre-launch and still discovering the domain, and enums are the wrong
-- shape for that. ALTER TYPE ... ADD VALUE cannot run inside a transaction,
-- and a value can never be REMOVED or safely renamed - get a name wrong today
-- and you carry it forever, or run a three-release add-migrate-remove dance.
--
-- A CHECK constraint is dropped and re-added in one ordinary transactional
-- migration, and it enforces exactly the same set. Storage costs a few bytes
-- per row, which is irrelevant here.
--
-- The Python StrEnums in each domain's enums.py remain the typed source, and
-- tests/test_enum_sync.py compares them against these constraints in both
-- directions.
--
-- Constraint naming is `<table>_<column>_check_values` so the drift test can
-- find them and so a violation message names the column.

-- =============================================================================
-- IDENTITY
-- =============================================================================

-- Identity anchor. Holds NO personal data, ever. Exists so that connections,
-- audit entries and moderation history can reference a participant without
-- that reference keeping personal data alive.
--
-- This row is never deleted while a connection references it.
CREATE TABLE users (
    id                  uuid PRIMARY KEY,
    created_at          timestamptz NOT NULL DEFAULT now(),
    -- Account deletion: set immediately, processed by the purge job at +30d.
    deleted_at          timestamptz,
    purge_after         timestamptz
);
CREATE INDEX users_purge_idx ON users (purge_after) WHERE deleted_at IS NOT NULL;

-- ALL personal data about a user. Separated from `users` deliberately.
--
-- Erasure is a single DELETE of this row. Not a column-by-column anonymise.
-- That matters because the anonymise approach depends on a purge job that
-- nulls EVERY personal column: add one later, forget to add it to the list,
-- and there is a silent compliance defect with no test that catches it.
--
-- The rule this encodes: if a column is personal data, it belongs here. A new
-- column is then covered by erasure automatically, by construction.
--
-- Deleting this row leaves the `users` anchor intact, so the counterparty's
-- connection, their private notes and the card snapshot all survive, which is
-- the retention position in ADR-0020.
CREATE TABLE user_profiles (
    user_id             uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    auth_subject        text NOT NULL UNIQUE,      -- JWT `sub` from the IdP
    email               citext NOT NULL,
    display_name        text,
    locale              text NOT NULL DEFAULT 'en',
    -- Marketing consent is separate from transactional. GDPR requires opt-in
    -- for marketing, and retrofitting the distinction later is unpleasant.
    consent_transactional  boolean NOT NULL DEFAULT true,
    consent_marketing      boolean NOT NULL DEFAULT false,
    consent_policy_version text,
    -- Age gating. Career fairs mean students; the GDPR minimum varies by country.
    birth_year          smallint,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX user_profiles_email_idx ON user_profiles (email);

CREATE TABLE organizations (
    id                  uuid PRIMARY KEY,
    name                text NOT NULL,
    slug                citext NOT NULL,
    -- Functional, so a column rather than a key in `brand`. It seeds
    -- domain_verifications, which gates public indexed event pages and the
    -- verified badge. Anything queried or acted on gets a column; `brand` is
    -- for presentation only, or it becomes a junk drawer nobody can query.
    website             text,
    description         text,
    logo_path           text,                       -- object storage key
    -- Purely presentational: colours, typography, enforced card styling.
    brand               jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- A solo organizer's personal organization, created automatically at
    -- organizer signup. See events.organization_id for why this exists.
    is_personal         boolean NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz
);
-- Partial, so a soft-deleted organization does not hold its slug forever.
-- NOTE: organizations.slug lost its UNIQUE when this index was introduced and
-- the index itself failed to land, leaving no uniqueness at all for a while.
-- Caught by tests/test_model_schema_sync.py, not by review.
CREATE UNIQUE INDEX organizations_slug_idx ON organizations (slug)
    WHERE deleted_at IS NULL;

-- Surrogate PK, not (organization_id, user_id). With a natural PK, a member who
-- leaves and is later re-invited collides with their own soft-deleted row and
-- the invite fails with a constraint error.
CREATE TABLE organization_members (
    id                  uuid PRIMARY KEY,
    organization_id     uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role                text NOT NULL,
    invited_at          timestamptz NOT NULL DEFAULT now(),
    accepted_at         timestamptz,
    deleted_at          timestamptz,
    CONSTRAINT organization_members_role_check_values CHECK (role IN ('owner', 'admin', 'member'))
);
CREATE UNIQUE INDEX org_members_active_idx
    ON organization_members (organization_id, user_id) WHERE deleted_at IS NULL;
CREATE INDEX org_members_user_idx ON organization_members (user_id) WHERE deleted_at IS NULL;

-- Proof of control over an email domain. This is our verification mechanism.
-- We deliberately do NOT do KYC: storing government ID is a compliance burden and
-- a breach risk that dwarfs everything else in this product.
CREATE TABLE domain_verifications (
    id                  uuid PRIMARY KEY,
    organization_id     uuid REFERENCES organizations(id) ON DELETE CASCADE,
    user_id             uuid REFERENCES users(id) ON DELETE CASCADE,
    domain              citext NOT NULL,
    verified_at         timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT domain_verification_subject CHECK (
        (organization_id IS NOT NULL) <> (user_id IS NOT NULL)
    )
);
CREATE UNIQUE INDEX domain_verifications_unique_idx
    ON domain_verifications (domain, COALESCE(organization_id, user_id));

-- Route collisions, brand squatting, profanity. Checked before any slug is issued.
CREATE TABLE reserved_slugs (
    slug                citext PRIMARY KEY,
    reason              text NOT NULL
);


-- =============================================================================
-- CARDS
-- =============================================================================

CREATE TABLE cards (
    id                  uuid PRIMARY KEY,
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    organization_id     uuid REFERENCES organizations(id) ON DELETE SET NULL,
    kind                text NOT NULL DEFAULT 'personal',
    is_default          boolean NOT NULL DEFAULT false,

    display_name        text NOT NULL,
    headline            text,
    company             text,
    email               citext,
    phone               text,
    website             text,
    photo_path          text,                       -- object storage key, not a URL
    socials             jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Custom links: [{label, url, position}].
    --
    -- An array rather than link_1/link_2 columns, which cannot be reordered or
    -- relabelled and force a migration for a third. Socials are identity and
    -- stay uncapped - capping them makes a card look broken rather than free -
    -- while custom links are where the link-in-bio value sits, so that is the
    -- honest paywall (`link.custom_limit`).
    --
    -- These render on the UGC domain, so URLs are validated to https only at
    -- write time. A javascript: or data: link here is stored XSS on the
    -- highest-risk surface we have (ADR-0008).
    links               jsonb NOT NULL DEFAULT '[]'::jsonb,
    custom_fields       jsonb NOT NULL DEFAULT '[]'::jsonb,   -- paid entitlement

    -- User data, versioned. NOT the app design system (ADR-0017).
    theme               jsonb NOT NULL DEFAULT '{}'::jsonb,
    theme_version       smallint NOT NULL DEFAULT 1,
    -- QR appearance (paid). Contrast is validated at write time; a pale-on-white
    -- code scans badly and generates support tickets.
    qr_style            jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- Link-in-bio slug. Public, human-chosen, checked against reserved_slugs.
    slug                citext,

    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    CONSTRAINT cards_kind_check_values CHECK (kind IN ('personal', 'business', 'custom'))
);
CREATE UNIQUE INDEX cards_slug_idx ON cards (slug) WHERE slug IS NOT NULL AND deleted_at IS NULL;
CREATE UNIQUE INDEX cards_one_default_idx ON cards (user_id) WHERE is_default AND deleted_at IS NULL;
CREATE INDEX cards_user_idx ON cards (user_id) WHERE deleted_at IS NULL;

-- Live and static tokens (ADR-0002). Public value is opaque, never a UUID: a v7
-- would leak creation time and 36 chars makes a denser, less reliable QR (ADR-0010).
CREATE TABLE card_tokens (
    id                  uuid PRIMARY KEY,
    card_id             uuid NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    kind                text NOT NULL,
    token               text NOT NULL UNIQUE,       -- secrets.token_urlsafe(16)
    -- Live tokens carry a 10-15 min TTL. Static tokens are NULL here and are
    -- killed by revoked_at instead, which is what makes a leaked badge photo
    -- recoverable without reprinting.
    expires_at          timestamptz,
    revoked_at          timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT live_tokens_expire CHECK (kind <> 'live' OR expires_at IS NOT NULL),
    CONSTRAINT card_tokens_kind_check_values CHECK (kind IN ('live', 'static'))
);
CREATE INDEX card_tokens_card_idx ON card_tokens (card_id, kind) WHERE revoked_at IS NULL;
CREATE INDEX card_tokens_expiry_idx ON card_tokens (expires_at) WHERE expires_at IS NOT NULL;


-- =============================================================================
-- EVENTS
-- =============================================================================

CREATE TABLE events (
    id                  uuid PRIMARY KEY,
    -- NOT NULL. Every event has an organizing entity; a solo organizer gets a
    -- personal organization created for them at signup (organizations.is_personal).
    --
    -- This reverses the original nullable design. A nullable owner meant every
    -- organization-scoped query needed a second branch
    -- (`OR organization_id IS NULL AND created_by = ?`), which is a branch
    -- someone forgets - and forgetting it is either a leak or a silently empty
    -- result. It also made organizer billing attach to a user sometimes and an
    -- organization other times, doubling the paths through the entitlements
    -- resolver.
    --
    -- The personal organization gets its OWN independent UUIDv7, never one
    -- derived from the user id: entitlements.subject_id is polymorphic with no
    -- foreign key, so a shared id would make `subject_id = X` ambiguous between
    -- user X and organization X - a silent billing bug in the one column with
    -- no referential integrity to catch it.
    organization_id     uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    created_by          uuid NOT NULL REFERENCES users(id),

    name                text NOT NULL,
    venue               text,
    code                citext NOT NULL,            -- join code
    slug                citext,                     -- public URL, primary domain
    visibility          text NOT NULL DEFAULT 'unlisted',

    starts_at           timestamptz NOT NULL,
    ends_at             timestamptz NOT NULL,
    -- IANA name, e.g. 'Europe/Berlin'. NOT an offset: peak-activity analytics are
    -- meaningless without it and DST makes offsets wrong twice a year (ADR-0012).
    timezone            text NOT NULL,

    -- Off by default, organizer-enabled, per-attendee opt-out. Gamifying scan
    -- counts produces farmed connections.
    leaderboard_enabled boolean NOT NULL DEFAULT false,

    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    CONSTRAINT events_time_order CHECK (ends_at > starts_at),
    CONSTRAINT events_visibility_check_values CHECK (visibility IN ('private', 'unlisted', 'public'))
);
-- Partial, so a soft-deleted event does not hold its join code hostage forever.
CREATE UNIQUE INDEX events_code_idx ON events (code) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX events_slug_idx ON events (slug)
    WHERE slug IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX events_org_idx ON events (organization_id) WHERE deleted_at IS NULL;
CREATE INDEX events_public_idx ON events (starts_at DESC)
    WHERE visibility = 'public' AND deleted_at IS NULL;

-- Presentational content, split from `events` on purpose.
--
-- `events` holds operational data the product owns forever: join code,
-- timezone, visibility, attendee limits. This holds what an organizer writes,
-- which is the part a CMS might one day own instead (ADR-0019 said not now, and
-- this split is what makes "revisit later" cheap rather than aspirational).
--
-- Read through events.service.content_for(), never joined directly from a
-- router. Storage is then an implementation detail: structured session tables
-- or an external CMS become a resolver change, touching neither `events`, the
-- dashboard, nor the API shape.
--
-- Also keeps `events` narrow. The organizer dashboard queries it constantly and
-- should not drag JSONB blobs along for the ride.
CREATE TABLE event_content (
    event_id            uuid PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    -- Tiptap JSON, sanitized on write AND on read. Never raw HTML (ADR-0019).
    body                jsonb,
    banner_path         text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);


-- Separate from organization_members: a scanner hired for one day must see one
-- event and nothing else (ADR-0018).
CREATE TABLE event_staff (
    id                  uuid PRIMARY KEY,
    event_id            uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role                text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    CONSTRAINT event_staff_role_check_values CHECK (role IN ('owner', 'manager', 'scanner', 'viewer'))
);
CREATE UNIQUE INDEX event_staff_active_idx
    ON event_staff (event_id, user_id) WHERE deleted_at IS NULL;

-- Surrogate PK for the same reason as organization_members: an attendee who
-- leaves an event and rejoins must not collide with their soft-deleted row.
CREATE TABLE event_attendees (
    id                  uuid PRIMARY KEY,
    event_id            uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    card_id             uuid REFERENCES cards(id) ON DELETE SET NULL,
    joined_at           timestamptz NOT NULL DEFAULT now(),
    -- Per-attendee opt-out from the leaderboard.
    leaderboard_opt_out boolean NOT NULL DEFAULT false,

    -- UNUSED IN v1. Event-scoped discovery ships in v1.1 (ADR-0023). Present now
    -- so v1.1 needs no migration. Do not remove.
    discoverable_at     timestamptz,
    discovery_prefs     jsonb NOT NULL DEFAULT '{}'::jsonb,

    deleted_at          timestamptz
);
CREATE UNIQUE INDEX event_attendees_active_idx
    ON event_attendees (event_id, user_id) WHERE deleted_at IS NULL;
CREATE INDEX event_attendees_user_idx ON event_attendees (user_id) WHERE deleted_at IS NULL;

-- Attendee list export requires a per-event consent record carrying the exact
-- wording shown (ADR-0012). "Only with consent" is not a specification.
-- Note: we cannot claw back an already-downloaded CSV. This is stated in the
-- consent copy and the organizer terms. It is a documented limitation, not a gap.
CREATE TABLE event_export_consents (
    id                  uuid PRIMARY KEY,
    event_id            uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope               text NOT NULL,
    consent_text        text NOT NULL,
    policy_version      text NOT NULL,
    granted_at          timestamptz NOT NULL DEFAULT now(),
    revoked_at          timestamptz
);
CREATE UNIQUE INDEX event_export_consents_unique_idx
    ON event_export_consents (event_id, user_id, scope) WHERE revoked_at IS NULL;

-- Organizer-uploaded registration list. Lets the dashboard report against
-- expected attendance rather than only actuals, which is what makes it saleable.
CREATE TABLE event_roster_entries (
    id                  uuid PRIMARY KEY,
    event_id            uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    email               citext,
    display_name        text,
    matched_user_id     uuid REFERENCES users(id) ON DELETE SET NULL,
    -- WHO put this row here. The denominator on the organizer dashboard means
    -- something different depending on the answer: "78% of your attendees
    -- connected" against an uploaded registration list is a real conversion
    -- number, and against a list half-filled by people who self-registered on
    -- a landing page it is a different claim entirely.
    --
    -- Without this column the two are indistinguishable and the dashboard
    -- quietly overstates.
    source              text NOT NULL DEFAULT 'organizer_import',
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT event_roster_entries_source_check_values CHECK (
        source IN ('organizer_import', 'self_registered', 'app_join')
    )
);
CREATE INDEX event_roster_event_idx ON event_roster_entries (event_id);
-- Organizers re-upload the same registration list. Without this, the second
-- import silently doubles the expected-attendance denominator on the dashboard.
CREATE UNIQUE INDEX event_roster_email_idx ON event_roster_entries (event_id, email)
    WHERE email IS NOT NULL;


-- =============================================================================
-- CONNECTIONS
-- =============================================================================

-- The immutable edge (ADR-0003). Written once. NEVER carries per-user private
-- data: notes live in connection_views, in a different row with a different owner.
CREATE TABLE connections (
    id                  uuid PRIMARY KEY,

    -- Canonical ordering prevents the same pair being stored twice reversed.
    -- RESTRICT, not CASCADE. The identity anchor must survive as long as any
    -- connection references it. Erasure deletes user_profiles, not this row,
    -- so the counterparty keeps their record, their notes and the snapshot.
    user_low_id         uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    user_high_id        uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    card_low_id         uuid REFERENCES cards(id) ON DELETE SET NULL,
    card_high_id        uuid REFERENCES cards(id) ON DELETE SET NULL,

    -- Immutable record of what was actually exchanged (ADR-0004). Survives card
    -- edits, card deletion, and account erasure of the counterpart.
    card_low_snapshot   jsonb NOT NULL,
    card_high_snapshot  jsonb NOT NULL,
    snapshot_version    smallint NOT NULL DEFAULT 1,

    event_id            uuid REFERENCES events(id) ON DELETE SET NULL,
    channel             text NOT NULL,
    state               text NOT NULL DEFAULT 'confirmed',

    -- UNUSED IN v1. Required by discovery to know which edges may contribute to
    -- mutual-connection counts (ADR-0023). Do not remove.
    visibility          text NOT NULL DEFAULT 'private',

    -- When the meeting HAPPENED, supplied by the client.
    --
    -- Distinct from created_at, which is when the server learned about it. An
    -- offline exchange can sync hours later (ADR-0016), and using created_at
    -- for anything time-based would attribute it to the moment the wifi came
    -- back. That would make the peak-activity chart we sell to organizers
    -- (ADR-0012) show a spike at reconnection - worse than no chart, because it
    -- looks plausible.
    --
    -- Client-supplied means device clocks, which drift and can be set
    -- deliberately, so the service validates it against the event window before
    -- accepting it.
    occurred_at         timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,

    CONSTRAINT connections_ordered CHECK (user_low_id < user_high_id),
    CONSTRAINT connections_channel_check_values CHECK (channel IN ('qr_live', 'qr_static', 'nfc', 'link', 'wallet')),
    CONSTRAINT connections_state_check_values CHECK (state IN ('pending', 'confirmed', 'declined')),
    CONSTRAINT connections_visibility_check_values CHECK (visibility IN ('private', 'discoverable'))
);

-- Two partial indexes, NOT one constraint. Postgres treats NULLs as distinct in
-- unique indexes, so a single constraint over a nullable event_id would permit
-- unlimited duplicates for non-event connections (ADR-0003).
CREATE UNIQUE INDEX connections_pair_event_idx
    ON connections (user_low_id, user_high_id, event_id)
    WHERE event_id IS NOT NULL AND deleted_at IS NULL;
CREATE UNIQUE INDEX connections_pair_noevent_idx
    ON connections (user_low_id, user_high_id)
    WHERE event_id IS NULL AND deleted_at IS NULL;

CREATE INDEX connections_event_idx ON connections (event_id, created_at)
    WHERE event_id IS NOT NULL AND deleted_at IS NULL;

-- One row per participant. This is where private data lives. Each user owns their
-- own row and can never read the other's. Deletion is per-view; the edge survives
-- until both views are deleted.
CREATE TABLE connection_views (
    id                  uuid PRIMARY KEY,
    connection_id       uuid NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    note                text,
    -- Field-level merge with a conflict marker, never silent overwrite (ADR-0016).
    note_updated_at     timestamptz,
    note_conflict       text,
    tags                text[] NOT NULL DEFAULT '{}',
    reminder_at         timestamptz,
    reminder_done_at    timestamptz,

    -- Duplicate detection: same person met at three events is one contact with
    -- three event tags, not three contacts.
    merged_into_id      uuid REFERENCES connection_views(id) ON DELETE SET NULL,

    archived_at         timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,

    UNIQUE (connection_id, user_id)
);
-- The connection list screen's access pattern.
CREATE INDEX connection_views_user_idx
    ON connection_views (user_id, created_at DESC)
    WHERE deleted_at IS NULL AND archived_at IS NULL;
CREATE INDEX connection_views_reminder_idx
    ON connection_views (reminder_at)
    WHERE reminder_at IS NOT NULL AND reminder_done_at IS NULL AND deleted_at IS NULL;

-- A scan by someone with no account: the majority path (ADR-0008 fallback page).
-- Counted so organizers can report on it and so we can measure fallback conversion.
CREATE TABLE anonymous_scans (
    id                  uuid PRIMARY KEY,
    card_id             uuid NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    token_id            uuid REFERENCES card_tokens(id) ON DELETE SET NULL,
    event_id            uuid REFERENCES events(id) ON DELETE SET NULL,
    channel             text NOT NULL,
    -- Optional reply form. Creates a pending exchange and an invitation to claim.
    reply_email         citext,
    reply_name          text,
    reply_payload       jsonb,
    claimed_by_user_id  uuid REFERENCES users(id) ON DELETE SET NULL,
    claimed_at          timestamptz,
    -- Truncated. Never store a raw IP: geolocation is a processing activity
    -- requiring a lawful basis and disclosure.
    ip_prefix           inet,
    country             text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT anonymous_scans_channel_check_values CHECK (channel IN ('qr_live', 'qr_static', 'nfc', 'link', 'wallet'))
);
CREATE INDEX anonymous_scans_card_idx ON anonymous_scans (card_id, created_at DESC);

-- What a scanner actually DID, one row per interaction.
--
-- Replaces the saved_vcard/added_wallet booleans, which were too narrow: they
-- counted only the strongest signal and scored a scanner who tapped the phone
-- number, opened a LinkedIn link or copied an address as a non-conversion.
--
-- KNOWN LIMIT, and analytics copy must reflect it: screenshots are
-- undetectable, and on mobile that is a common way people keep a card. Every
-- number derived from this table undercounts, so it is reported as "at least".
--
-- `target` names WHICH element was used (e.g. 'phone', 'linkedin'). We record
-- that an interaction happened, never its content - a copy event carries no
-- copied text, which is the line between measuring engagement and reading over
-- someone's shoulder.
CREATE TABLE scan_interactions (
    id                  uuid PRIMARY KEY,
    scan_id             uuid NOT NULL REFERENCES anonymous_scans(id) ON DELETE CASCADE,
    kind                text NOT NULL,
    target              text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT scan_interactions_kind_check_values CHECK (
        kind IN ('vcard_save', 'wallet_add', 'link_click', 'copy', 'call', 'email')
    )
);
CREATE INDEX scan_interactions_scan_idx ON scan_interactions (scan_id);
-- Reporting reads by kind across a window, not by scan.
CREATE INDEX scan_interactions_kind_idx ON scan_interactions (kind, created_at DESC);
CREATE INDEX anonymous_scans_event_idx ON anonymous_scans (event_id) WHERE event_id IS NOT NULL;


-- =============================================================================
-- BILLING AND ENTITLEMENTS
-- =============================================================================

-- Raw records from each billing source. Kept for reconciliation and support.
CREATE TABLE subscriptions (
    id                  uuid PRIMARY KEY,
    subject_kind        text NOT NULL,
    subject_id          uuid NOT NULL,
    source              text NOT NULL,
    source_ref          text NOT NULL,              -- Apple originalTransactionId / Stripe sub id
    plan_key            text NOT NULL,
    status              text NOT NULL,
    current_period_end  timestamptz,
    raw                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, source_ref),
    CONSTRAINT subscriptions_source_check_values CHECK (source IN ('apple', 'stripe', 'google', 'manual')),
    CONSTRAINT subscriptions_status_check_values CHECK (status IN ('active', 'grace', 'expired', 'revoked')),
    CONSTRAINT subscriptions_subject_kind_check_values CHECK (subject_kind IN ('user', 'organization'))
);
CREATE INDEX subscriptions_subject_idx ON subscriptions (subject_kind, subject_id);

-- The ONLY thing authorization ever reads (ADR-0009). No code anywhere asks
-- "does this user have a Stripe subscription". Adding Google Play later is one
-- webhook handler and zero changes here.
CREATE TABLE entitlements (
    id                  uuid PRIMARY KEY,
    subject_kind        text NOT NULL,
    subject_id          uuid NOT NULL,
    entitlement_key     text NOT NULL,             -- see 00-context/pricing.md
    value_int           integer,
    value_bool          boolean,
    source              text NOT NULL,
    subscription_id     uuid REFERENCES subscriptions(id) ON DELETE SET NULL,
    status              text NOT NULL DEFAULT 'active',
    -- Comped pilot partners are a manual grant with an expiry, using the same
    -- mechanism as paid. No special-case code.
    expires_at          timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    -- Exactly one value. Without this, a row with both NULL is accepted and
    -- reads as "entitled to nothing" or "unlimited" depending on the caller.
    CONSTRAINT entitlements_one_value CHECK (
        (value_int IS NOT NULL)::int + (value_bool IS NOT NULL)::int = 1
    ),
    CONSTRAINT entitlements_source_check_values CHECK (source IN ('apple', 'stripe', 'google', 'manual')),
    CONSTRAINT entitlements_status_check_values CHECK (status IN ('active', 'grace', 'expired', 'revoked')),
    CONSTRAINT entitlements_subject_kind_check_values CHECK (subject_kind IN ('user', 'organization'))
);
-- KNOWN LIMITATION: subject_id is polymorphic (user or organization), so no
-- foreign key can enforce it and orphaned entitlements are accepted at the
-- database level. Referential integrity here is the service layer's job, and
-- the reconciliation job must detect orphans.
CREATE UNIQUE INDEX entitlements_active_idx
    ON entitlements (subject_kind, subject_id, entitlement_key, source)
    WHERE status IN ('active', 'grace');
CREATE INDEX entitlements_lookup_idx ON entitlements (subject_kind, subject_id, entitlement_key);

-- Webhook deliveries. Apple notifications are eventually consistent and
-- occasionally duplicated, so handlers must be idempotent against this table.
CREATE TABLE billing_events (
    id                  uuid PRIMARY KEY,
    source              text NOT NULL,
    external_id         text NOT NULL,
    payload             jsonb NOT NULL,
    processed_at        timestamptz,
    error               text,
    received_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, external_id),
    CONSTRAINT billing_events_source_check_values CHECK (source IN ('apple', 'stripe', 'google', 'manual'))
);
CREATE INDEX billing_events_unprocessed_idx ON billing_events (received_at)
    WHERE processed_at IS NULL;

-- NOT IN v1. Ticketing makes us a marketplace: Stripe Connect, per-organizer KYC,
-- payouts, chargeback liability, multi-jurisdiction tax. The core schema is shaped
-- so a ticket_types table can be added later without touching anything above.


-- A connected CRM, per user or per organization.
--
-- Two providers at launch, chosen because they are SHAPED DIFFERENTLY: Google
-- Contacts is a personal address book with no CRM concepts, HubSpot has
-- contacts, companies, deals and custom properties. An adapter tested against
-- both is tested against real divergence, so a third provider is an
-- implementation of a proven interface rather than the thing that reshapes it.
--
-- `field_mapping` is per-connection, not per-provider: two HubSpot accounts
-- put "how we met" in different custom properties, and hardcoding one is how
-- the integration silently writes into the wrong field.
CREATE TABLE crm_connections (
    id                  uuid PRIMARY KEY,
    user_id             uuid REFERENCES users(id) ON DELETE CASCADE,
    organization_id     uuid REFERENCES organizations(id) ON DELETE CASCADE,
    provider            text NOT NULL,
    -- Encrypted at rest by the application, never plaintext. A CRM token is a
    -- write credential into someone's customer database.
    credentials         bytea NOT NULL,
    field_mapping       jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- A CRM that silently stops syncing is worse than one never connected,
    -- because the user believes their contacts are safe. These three are what
    -- let the app say "reconnect" instead of failing quietly.
    last_sync_at        timestamptz,
    last_error          text,
    needs_reauth        boolean NOT NULL DEFAULT false,

    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,

    CONSTRAINT crm_connections_provider_check_values CHECK (
        provider IN ('google_contacts', 'hubspot')
    ),
    CONSTRAINT crm_connections_subject CHECK (
        (user_id IS NOT NULL) <> (organization_id IS NOT NULL)
    )
);
-- One live connection per provider per subject. Two would double-write every
-- contact and neither would obviously be the wrong one.
CREATE UNIQUE INDEX crm_connections_user_idx
    ON crm_connections (user_id, provider)
    WHERE user_id IS NOT NULL AND deleted_at IS NULL;
CREATE UNIQUE INDEX crm_connections_org_idx
    ON crm_connections (organization_id, provider)
    WHERE organization_id IS NOT NULL AND deleted_at IS NULL;

-- What was pushed where, so a retry does not create a duplicate contact in
-- someone's CRM - the failure users notice and complain about loudest.
CREATE TABLE crm_synced_contacts (
    id                  uuid PRIMARY KEY,
    crm_connection_id   uuid NOT NULL REFERENCES crm_connections(id) ON DELETE CASCADE,
    connection_view_id  uuid NOT NULL REFERENCES connection_views(id) ON DELETE CASCADE,
    external_id         text,
    synced_at           timestamptz NOT NULL DEFAULT now(),
    error               text
);
CREATE UNIQUE INDEX crm_synced_contacts_unique_idx
    ON crm_synced_contacts (crm_connection_id, connection_view_id);


-- =============================================================================
-- SAFETY AND COMPLIANCE
-- =============================================================================

CREATE TABLE reports (
    id                  uuid PRIMARY KEY,
    reporter_user_id    uuid REFERENCES users(id) ON DELETE SET NULL,
    -- SET NULL, not CASCADE. Suspending a card and then purging it must not
    -- erase the record of WHY it was suspended. Repeat-offender detection and
    -- any appeal both depend on that history surviving.
    subject_card_id     uuid REFERENCES cards(id) ON DELETE SET NULL,
    subject_user_id     uuid REFERENCES users(id) ON DELETE SET NULL,
    subject_event_id    uuid REFERENCES events(id) ON DELETE SET NULL,
    -- Denormalised so the report stays readable after the subject is gone.
    subject_label       text,
    reason              text NOT NULL,
    detail              text,
    status              text NOT NULL DEFAULT 'open',
    resolved_at         timestamptz,
    resolution_note     text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT reports_status_check_values CHECK (status IN ('open', 'actioned', 'dismissed'))
);
CREATE INDEX reports_open_idx ON reports (created_at) WHERE status = 'open';

CREATE TABLE blocks (
    blocker_user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    blocked_user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (blocker_user_id, blocked_user_id),
    CONSTRAINT blocks_not_self CHECK (blocker_user_id <> blocked_user_id)
);

-- Any cross-tenant access, any admin action, any data export. Append-only.
CREATE TABLE audit_log (
    id                  uuid PRIMARY KEY,
    actor_user_id       uuid REFERENCES users(id) ON DELETE SET NULL,
    action              text NOT NULL,
    subject_kind        text,
    subject_id          uuid,
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    request_id          text,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_actor_idx ON audit_log (actor_user_id, created_at DESC);
CREATE INDEX audit_log_subject_idx ON audit_log (subject_kind, subject_id, created_at DESC);

-- Article 20 requests. ALWAYS free, distinct from the paid workflow export
-- (ADR-0020). Different table, different entry point, different copy.
CREATE TABLE data_export_requests (
    id                  uuid PRIMARY KEY,
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    requested_at        timestamptz NOT NULL DEFAULT now(),
    completed_at        timestamptz,
    download_path       text,
    expires_at          timestamptz
);


-- =============================================================================
-- NOTIFICATIONS
-- =============================================================================

CREATE TABLE device_tokens (
    id                  uuid PRIMARY KEY,
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expo_token          text NOT NULL,
    platform            text NOT NULL,
    locale              text,
    last_seen_at        timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    revoked_at          timestamptz,
    UNIQUE (expo_token)
);
CREATE INDEX device_tokens_user_idx ON device_tokens (user_id) WHERE revoked_at IS NULL;

-- Push delivery is best-effort and NOT guaranteed. Every push must have an in-app
-- equivalent, so a missed follow-up reminder still appears in the app. This table
-- is that in-app equivalent, and the push is a notification of a row here.
CREATE TABLE notifications (
    id                  uuid PRIMARY KEY,
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind                text NOT NULL,
    payload             jsonb NOT NULL DEFAULT '{}'::jsonb,
    read_at             timestamptz,
    pushed_at           timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    -- Constrained like every other value set. It was free text, which meant a
    -- typo in a job produced a notification nothing renders and nothing
    -- complains about - the row exists, the user sees a blank, and the only
    -- symptom is silence.
    CONSTRAINT notifications_kind_check_values CHECK (
        kind IN (
            'reminder_due', 'post_event_digest', 'reciprocity_nudge',
            'pending_request', 'event_announcement', 'crm_sync_failed'
        )
    )
);
CREATE INDEX notifications_user_idx ON notifications (user_id, created_at DESC)
    WHERE read_at IS NULL;


-- =============================================================================
-- TRIGGER ATTACHMENT
-- =============================================================================
-- Every table carrying updated_at. Adding a column without adding the trigger
-- reintroduces the decorative-timestamp bug, so this list is checked in review.

CREATE TRIGGER user_profiles_updated_at BEFORE UPDATE ON user_profiles
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER organizations_updated_at BEFORE UPDATE ON organizations
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER cards_updated_at BEFORE UPDATE ON cards
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER events_updated_at BEFORE UPDATE ON events
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER event_content_updated_at BEFORE UPDATE ON event_content
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER connection_views_updated_at BEFORE UPDATE ON connection_views
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER subscriptions_updated_at BEFORE UPDATE ON subscriptions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER entitlements_updated_at BEFORE UPDATE ON entitlements
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER crm_connections_updated_at BEFORE UPDATE ON crm_connections
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
"""

# Reverse creation order so foreign keys unwind cleanly.
TABLES = [
    "notifications",
    "device_tokens",
    "data_export_requests",
    "audit_log",
    "blocks",
    "reports",
    "crm_synced_contacts",
    "crm_connections",
    "billing_events",
    "entitlements",
    "subscriptions",
    "scan_interactions",
    "anonymous_scans",
    "connection_views",
    "connections",
    "event_roster_entries",
    "event_export_consents",
    "event_attendees",
    "event_staff",
    "event_content",
    "events",
    "card_tokens",
    "cards",
    "reserved_slugs",
    "domain_verifications",
    "organization_members",
    "organizations",
    "user_profiles",
    "users",
]

TYPES: list[str] = []  # ADR-0027: no native enum types


def upgrade() -> None:
    op.execute(SCHEMA)


def downgrade() -> None:
    """Drop everything.

    Only safe because this is the baseline and there is nothing before it. Do
    NOT copy this pattern into a later migration: a down-migration that
    silently drops user data is worse than none, because it looks safe.
    """
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for type_name in TYPES:
        op.execute(f"DROP TYPE IF EXISTS {type_name} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at() CASCADE")
