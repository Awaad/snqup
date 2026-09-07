"""GDPR data export (Article 20).

ALWAYS FREE. Distinct from the paid workflow export, and the distinction is not
cosmetic: gating a legal right behind a subscription is the thing that turns a
support ticket into a regulatory one.

  this                       CONNECTION_EXPORT_NOT_ENTITLED must NEVER be
                             returned here
  workflow export            filtered vCard/CSV, a product feature, paid

They have different entry points and different names in the UI for the same
reason.
"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.repository import Tenant
from acme.domains.cards.service import CardsService
from acme.domains.connections.service import ConnectionListService
from acme.domains.identity.service import IdentityService


class DataExportService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build(self, user_id: UUID) -> dict[str, Any]:
        """Everything we hold about this person, machine-readable.

        WHAT IS NOT INCLUDED, and why: the counterpart's `connection_views`.
        Those are the other person's private notes about a meeting, not this
        person's data, and returning them would leak exactly what the
        edge/view split exists to protect (ADR-0003).

        Card snapshots held by OTHER users are also absent - they are the
        recipient's record of a meeting that happened, equivalent to a paper
        card handed over (ADR-0020). That position is disclosed in the privacy
        policy rather than discovered here.
        """
        identity = IdentityService(self._session)
        profile = await identity.export_profile(user_id)

        cards = await CardsService(self._session, Tenant.user(user_id)).list_cards()

        connections = await ConnectionListService(self._session, user_id).page(
            limit=10_000, include_archived=True
        )

        return {
            "exported_at": datetime.now(UTC).isoformat(),
            "format_version": 1,
            "profile": profile,
            "cards": [
                {
                    "display_name": c.display_name,
                    "headline": c.headline,
                    "company": c.company,
                    "email": c.email,
                    "phone": c.phone,
                    "website": c.website,
                    "socials": dict(c.socials),
                    "slug": c.slug,
                    "created_at": c.created_at.isoformat(),
                }
                for c in cards
            ],
            # The caller's OWN view of each connection: their note, their tags,
            # their reminder. Never the counterpart's.
            "connections": [
                {
                    "met": item.counterpart.display_name,
                    "company": item.counterpart.company,
                    "occurred_at": item.occurred_at.isoformat(),
                    "channel": item.channel.value,
                    "my_note": item.note,
                    "my_tags": item.tags,
                    "reminder_at": (item.reminder_at.isoformat() if item.reminder_at else None),
                }
                for item in connections.items
            ],
        }

    async def build_json(self, user_id: UUID) -> str:
        return json.dumps(await self.build(user_id), indent=2, sort_keys=True)
