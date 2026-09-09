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


class RosterSource(StrEnum):
    """How someone got onto the attendee list.

    The organizer dashboard's denominator means something different for each,
    and collapsing them would let "78% of your attendees connected" quietly
    overstate a number the organizer repeats to sponsors.
    """

    #: Uploaded registration list. The real expected attendance.
    ORGANIZER_IMPORT = "organizer_import"
    #: Self-registered on the public event page, no account required.
    SELF_REGISTERED = "self_registered"
    #: Joined by code in the app. Already a user.
    APP_JOIN = "app_join"
