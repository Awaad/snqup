"""Enum types owned by the connections domain."""

from enum import StrEnum


class ScanChannel(StrEnum):
    """How an exchange arrived.

    Recorded from v1 even though NFC ships in v1.1: retrofitting loses the
    attribution history, which is how we learn whether NFC or QR is actually
    used.
    """

    QR_LIVE = "qr_live"
    QR_STATIC = "qr_static"
    NFC = "nfc"
    LINK = "link"
    WALLET = "wallet"


class ConnectionState(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DECLINED = "declined"


class ConnectionVisibility(StrEnum):
    """UNUSED IN v1. Event-scoped discovery needs to know which edges may
    contribute to mutual-connection counts (ADR-0023)."""

    PRIVATE = "private"
    DISCOVERABLE = "discoverable"
