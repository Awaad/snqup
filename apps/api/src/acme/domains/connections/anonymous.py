"""The anonymous scan path.

The MAJORITY case, and the growth loop. Most scanners have no account: they
point a native camera at a badge, land on the public page, and save a contact.
Treating that as an edge case would mean the product only works for the small
fraction who already installed it (ADR-0008).

Three things happen here, in increasing order of commitment:

  1. the scan is COUNTED, so organizers can report on it and so the fallback
     page's conversion is measurable
  2. the scanner optionally leaves their own details, creating a pending
     exchange the card owner sees immediately
  3. the scanner later installs and CLAIMS it, becoming a user with the
     connection already waiting
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.domains.cards.service import TokenResolver
from acme.domains.connections.enums import ScanChannel
from acme.domains.connections.models import AnonymousScan


@dataclass(frozen=True, slots=True)
class RecordedScan:
    scan_id: UUID
    card_owner_id: UUID


def truncate_ip(raw: str | None) -> str | None:
    """Keep a /24 (or /48 for IPv6), never the full address.

    Deriving coarse geography is a processing activity needing a lawful basis
    and disclosure (ADR-0012); storing the exact address is a different and
    much larger commitment for the same "viewed in Berlin" feature.
    """
    if not raw:
        return None
    if ":" in raw:
        parts = raw.split(":")
        return ":".join(parts[:3]) + "::"
    parts = raw.split(".")
    if len(parts) != 4:
        return None
    return ".".join(parts[:3]) + ".0"


class AnonymousScanService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._tokens = TokenResolver(session)

    async def record(
        self,
        *,
        token: str,
        channel: ScanChannel,
        event_id: UUID | None = None,
        ip: str | None = None,
        country: str | None = None,
    ) -> RecordedScan:
        """Count a scan by someone with no account.

        Deliberately writes nothing to `connections`: there is no second party
        yet. A pending exchange only exists once they leave their details.
        """
        target = await self._tokens.resolve_public(token)

        scan = AnonymousScan(
            card_id=target.card_id,
            event_id=event_id,
            channel=channel,
            ip_prefix=truncate_ip(ip),
            country=country,
        )
        self._session.add(scan)
        await self._session.flush()
        return RecordedScan(scan_id=scan.id, card_owner_id=target.owner_id)

    async def mark_saved(self, scan_id: UUID, *, wallet: bool = False) -> None:
        """The conversion that matters.

        Views are vanity; a saved vCard means the contact actually landed in
        someone's phone. This is the metric the fallback page is judged on.
        """
        scan = await self._session.get(AnonymousScan, scan_id)
        if scan is None:
            raise ApiError("CARD_NOT_FOUND", status_code=404)
        if wallet:
            scan.added_wallet = True
        else:
            scan.saved_vcard = True
        await self._session.flush()

    async def leave_details(
        self,
        scan_id: UUID,
        *,
        email: str,
        name: str | None,
        payload: dict[str, object] | None = None,
    ) -> None:
        """The optional reply form.

        OPTIONAL is load-bearing: requiring it before the vCard download would
        kill the save flow, which is the one thing this page must get right.

        Creates a pending exchange the card owner sees immediately, and an
        invitation to claim. Anonymous submissions are user-generated data from
        an unauthenticated stranger, so the router rate-limits and captchas
        this route.
        """
        scan = await self._session.get(AnonymousScan, scan_id)
        if scan is None:
            raise ApiError("CARD_NOT_FOUND", status_code=404)
        if scan.reply_email is not None:
            # Idempotent: a double submit must not create a second pending
            # request for the card owner to dismiss twice.
            return
        scan.reply_email = email
        scan.reply_name = name
        scan.reply_payload = payload or {}
        await self._session.flush()

    async def claim_for(self, user_id: UUID, email: str) -> int:
        """Attach unclaimed scans to a user who has just signed up.

        This is what makes the loop close: someone scans a badge, leaves their
        email, installs a week later, and the connection is already waiting
        rather than lost.

        Matched on the email they typed at scan time. That is weaker than a
        verified identity, which is why claiming grants only the pending
        connection and never anything else.
        """
        stmt = select(AnonymousScan).where(
            AnonymousScan.reply_email == email,
            AnonymousScan.claimed_by_user_id.is_(None),
        )
        claimed = 0
        for scan in (await self._session.execute(stmt)).scalars():
            scan.claimed_by_user_id = user_id
            scan.claimed_at = datetime.now(UTC)
            claimed += 1
        await self._session.flush()
        return claimed
