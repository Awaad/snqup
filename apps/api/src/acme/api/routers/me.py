"""The caller's own profile.

"""

from fastapi import APIRouter

from acme.api.deps import CurrentUserDep, SessionDep
from acme.domains.identity.schemas import ProfileOut, ProfileUpdate
from acme.domains.identity.service import IdentityService

router = APIRouter(prefix="/v1", tags=["identity"])


@router.get("/me", response_model=ProfileOut)
async def get_me(session: SessionDep, user: CurrentUserDep) -> ProfileOut:
    """Who am I.

    Also the endpoint a client calls immediately after sign-in to PROVISION the
    account: resolving the token creates the user on first use, so a successful
    response here means the account exists.
    """
    return await IdentityService(session).profile(user.id)


@router.patch("/me", response_model=ProfileOut)
async def update_me(
    payload: ProfileUpdate, session: SessionDep, user: CurrentUserDep
) -> ProfileOut:
    """Display name, locale, timezone and marketing consent.

    Marketing consent is separate from transactional and stays that way: GDPR
    requires opt-in for marketing, and one combined flag means either spamming
    people or being unable to send a password reset.

    Timezone matters more than it looks - quiet hours for push are computed in
    the recipient's local time, and without it the server falls back to UTC,
    which is wrong for most of the world.
    """
    return await IdentityService(session).update_profile(user.id, payload)
