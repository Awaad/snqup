"""Safety request and response schemas."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from acme.domains.safety.enums import ReportStatus


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Exactly one subject. Enforced by the service, not just by shape.
    subject_card_id: UUID | None = None
    subject_user_id: UUID | None = None
    subject_event_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=100)
    detail: str | None = Field(default=None, max_length=4000)


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    reason: str
    status: ReportStatus


class BlockCreate(BaseModel):
    """Blocks prevent exchange in BOTH directions.

    Private: the blocked party is never told. Revealing a block turns a safety
    feature into a signal, and the response to blocking someone must be
    indistinguishable from the response to not blocking them.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
