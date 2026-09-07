"""Enum types owned by the CRM domain."""

from enum import StrEnum


class CrmProvider(StrEnum):
    GOOGLE_CONTACTS = "google_contacts"
    HUBSPOT = "hubspot"


class SyncOutcome(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    #: Already synced. Not a failure - the whole point of tracking external
    #: ids is that a retry does not create a duplicate in someone's CRM.
    SKIPPED = "skipped"
    FAILED = "failed"
