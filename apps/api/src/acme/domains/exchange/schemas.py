"""Exchange request and response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from acme.domains.cards.schemas import PublicCardOut
from acme.domains.connections.enums import ConnectionState, ScanChannel


class ExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The scanned token. Live or static; the SERVER decides which semantics
    #: apply, never the client (ADR-0002).
    token: str

    #: Which of the scanner's own cards to present.
    card_id: UUID

    #: How the scan arrived. Recorded from v1 even though NFC ships in v1.1 -
    #: retrofitting loses the attribution history.
    channel: ScanChannel = ScanChannel.QR_LIVE

    #: When the meeting HAPPENED, not when this request was sent. An offline
    #: exchange syncs hours later (ADR-0016), and every time-based organizer
    #: metric depends on this rather than on created_at.
    occurred_at: datetime | None = None

    #: Present when scanning inside an event.
    event_id: UUID | None = None

    #: Captured in the ten seconds after the handshake. Everyone intends to add
    #: notes later and nobody does.
    note: str | None = Field(default=None, max_length=2000)


class ExchangeResult(BaseModel):
    """What the scanner gets back.

    `symmetric` tells the client whether the other party received their card
    too. It is derived from the TOKEN TYPE, not requested: a static token never
    produces a symmetric exchange, however the client asks.
    """

    connection_id: UUID
    state: ConnectionState
    symmetric: bool
    card: PublicCardOut
    occurred_at: datetime
    #: True when this exchange already existed. The response is identical
    #: either way, so an offline replay is indistinguishable from the original.
    duplicate: bool = False
