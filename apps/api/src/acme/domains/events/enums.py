"""Enum types owned by the events domain."""

from enum import StrEnum


class EventVisibility(StrEnum):
    """Three levels, deliberately (ADR-0008).

    PUBLIC requires organizer verification, which is both the SEO gate and the
    anti-spam filter: nobody pays to host a phishing page.
    """

    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


class EventStaffRole(StrEnum):
    """Separate from OrgRole (ADR-0018).

    A SCANNER hired for one day must see one event and nothing else.
    """

    OWNER = "owner"
    MANAGER = "manager"
    SCANNER = "scanner"
    VIEWER = "viewer"
