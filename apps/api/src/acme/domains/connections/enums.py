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


class InteractionKind(StrEnum):
    """What a scanner did with a card.

    Ordered loosely by strength of signal. VCARD_SAVE and WALLET_ADD mean the
    contact is kept; the rest mean they acted on it, which is weaker but real -
    and treating those as non-conversions is what the two old booleans got
    wrong.
    """

    VCARD_SAVE = "vcard_save"
    WALLET_ADD = "wallet_add"
    LINK_CLICK = "link_click"
    COPY = "copy"
    CALL = "call"
    EMAIL = "email"


#: Interactions that mean the contact was KEPT, not merely touched.
SAVED_KINDS = frozenset({InteractionKind.VCARD_SAVE, InteractionKind.WALLET_ADD})
