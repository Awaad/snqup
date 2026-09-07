"""Enum types owned by the notifications domain."""

from enum import StrEnum


class NotificationKind(StrEnum):
    """What a notification is about.

    Every value here needs a matching string in all four locale files - the
    backend returns the kind, the client owns the wording (ADR-0011).
    """

    REMINDER_DUE = "reminder_due"
    POST_EVENT_DIGEST = "post_event_digest"
    RECIPROCITY_NUDGE = "reciprocity_nudge"
    PENDING_REQUEST = "pending_request"
    EVENT_ANNOUNCEMENT = "event_announcement"
    #: A CRM that silently stops syncing is worse than one never connected,
    #: because the user believes their contacts are safe.
    CRM_SYNC_FAILED = "crm_sync_failed"
