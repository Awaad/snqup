"""Identity request and response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl

from acme.domains.identity.enums import OrgRole


class ProfileOut(BaseModel):
    """The caller's own profile.

    Everything here lives on `user_profiles`, which is where every personal
    column belongs - that is what makes erasure a single DELETE and a GDPR
    export automatic for columns added later (ADR-0027).
    """

    model_config = ConfigDict(from_attributes=True)

    email: EmailStr
    display_name: str | None
    locale: str
    consent_marketing: bool
    consent_transactional: bool
    created_at: datetime


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=120)
    locale: str | None = Field(default=None, max_length=10)
    #: Marketing consent is separate from transactional and must stay that way.
    #: GDPR requires opt-in for marketing, and one combined flag means either
    #: spamming people or being unable to send a password reset.
    consent_marketing: bool | None = None


class OrganizationCreate(BaseModel):
    """Organizer signup asks for a name; everything else is prompted later.

    Not friction for its own sake: at signup they have not seen the product
    yet, so a logo request is an abstract chore. Right after they create an
    event, when the page is visibly plain, it is obviously worth doing.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=3, max_length=40)


class OrganizationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    #: Functional, not decorative: it seeds domain verification, which gates
    #: public indexed event pages and the verified badge.
    website: HttpUrl | None = None
    description: str | None = Field(default=None, max_length=2000)
    logo_path: str | None = None
    #: Purely presentational - colours, typography, enforced card styling.
    #: Anything queried or acted on gets its own column instead.
    brand: dict[str, str] | None = None


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    website: str | None
    description: str | None
    logo_path: str | None
    brand: dict[str, str]
    #: True for the organization created automatically for a solo organizer,
    #: so the UI can hide team management it would never need (ADR-0027).
    is_personal: bool


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    role: OrgRole
    accepted_at: datetime | None


class MemberInvite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: OrgRole = OrgRole.MEMBER
