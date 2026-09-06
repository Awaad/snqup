"""Enum types owned by the billing domain."""

from enum import StrEnum


class SubjectKind(StrEnum):
    USER = "user"
    ORGANIZATION = "organization"


class EntitlementSource(StrEnum):
    """Multiple sources feed one entitlements model (ADR-0009).

    MANUAL is how comped pilot partners work: same table, same resolver, with
    an expiry. No special-case code.
    """

    APPLE = "apple"
    STRIPE = "stripe"
    GOOGLE = "google"
    MANUAL = "manual"


class EntitlementStatus(StrEnum):
    ACTIVE = "active"
    GRACE = "grace"
    EXPIRED = "expired"
    REVOKED = "revoked"
