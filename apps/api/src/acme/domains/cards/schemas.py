"""Cards request and response schemas.

Field names are snake_case, matching the database and the generated TypeScript
client one-for-one (contracts/api-conventions.md). No camelCase transform layer:
it would break the mapping that makes the generated types trustworthy.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from acme.domains.cards.enums import CardKind, TokenKind


class CardLink(BaseModel):
    """One custom link.

    `label` is stored, never derived. Auto-titling would mean fetching
    arbitrary user-supplied URLs server-side, which is an SSRF vector for a
    cosmetic gain.
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=60)
    #: https only. These render on the UGC domain, so a javascript: or data:
    #: URL here is stored XSS on the highest-risk surface we have (ADR-0008).
    url: HttpUrl
    position: int = Field(default=0, ge=0, le=99)

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("links must be https")
        return value


class CardFields(BaseModel):
    """The editable surface of a card. Shared by create and update."""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=120)
    headline: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    website: str | None = Field(default=None, max_length=500)
    photo_path: str | None = None
    socials: dict[str, str] = Field(default_factory=dict)
    #: Capped by `link.custom_limit` (2 on the free tier). Socials are
    #: uncapped: they are identity, and capping them makes a card look broken
    #: rather than free.
    links: list[CardLink] = Field(default_factory=list, max_length=50)
    # Paid. Rejected with BILLING_ENTITLEMENT_MISSING when not entitled.
    custom_fields: list[dict[str, str]] = Field(default_factory=list)
    theme: dict[str, str] = Field(default_factory=dict)
    # Paid, and contrast-validated at save time (ADR-0017).
    qr_style: dict[str, str] = Field(default_factory=dict)


class CardCreate(CardFields):
    kind: CardKind = CardKind.PERSONAL
    slug: str | None = None
    is_default: bool = False


class CardUpdate(BaseModel):
    """Every field optional: a PATCH must not require resending the card.

    `None` and "absent" are different here. Absent means leave it alone;
    explicit null means clear it. Pydantic's exclude_unset gives us that
    distinction, and the service relies on it.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    headline: str | None = None
    company: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    photo_path: str | None = None
    socials: dict[str, str] | None = None
    links: list[CardLink] | None = None
    custom_fields: list[dict[str, str]] | None = None
    theme: dict[str, str] | None = None
    qr_style: dict[str, str] | None = None
    kind: CardKind | None = None
    slug: str | None = None
    is_default: bool | None = None


class CardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: CardKind
    is_default: bool
    display_name: str
    headline: str | None
    company: str | None
    email: str | None
    phone: str | None
    website: str | None
    photo_path: str | None
    socials: dict[str, str]
    links: list[CardLink]
    custom_fields: list[dict[str, str]]
    theme: dict[str, str]
    theme_version: int
    qr_style: dict[str, str]
    slug: str | None
    created_at: datetime
    updated_at: datetime


class PublicCardOut(BaseModel):
    """What a STRANGER sees. Deliberately narrower than CardOut.

    No internal id: a UUIDv7 leaks its creation timestamp, and public surfaces
    must never expose one (ADR-0010). No theme_version, no is_default - those
    are ours, not theirs.
    """

    model_config = ConfigDict(from_attributes=True)

    display_name: str
    headline: str | None
    company: str | None
    email: str | None
    phone: str | None
    website: str | None
    photo_path: str | None
    socials: dict[str, str]
    links: list[CardLink]
    custom_fields: list[dict[str, str]]
    theme: dict[str, str]


class TokenOut(BaseModel):
    token: str
    kind: TokenKind
    expires_at: datetime | None
