"""Enum types owned by the cards domain.

Values must match the Postgres labels in schema/schema.sql exactly.
tests/test_enum_sync.py enforces that.
"""

from enum import StrEnum


class CardKind(StrEnum):
    PERSONAL = "personal"
    BUSINESS = "business"
    CUSTOM = "custom"


class TokenKind(StrEnum):
    """The security model, as a type (ADR-0002).

    LIVE produces a symmetric exchange; STATIC never does. Confusing the two is
    the harvesting vector the whole design exists to close, so it is worth
    having the compiler know the difference.
    """

    LIVE = "live"
    STATIC = "static"
