"""CRM service. The only surface other domains may use."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.domains.crm.adapter import ContactPayload, CrmAdapter
from acme.domains.crm.enums import CrmProvider
from acme.domains.crm.models import CrmConnection, CrmSyncedContact
from acme.domains.crm.registry import adapter_for


class CrmService:
    def __init__(self, session: AsyncSession, user_id: UUID) -> None:
        self._session = session
        self._user_id = user_id

    async def connections(self) -> list[CrmConnection]:
        stmt = select(CrmConnection).where(
            CrmConnection.user_id == self._user_id,
            CrmConnection.deleted_at.is_(None),
        )
        return list((await self._session.execute(stmt)).scalars())

    async def get(self, provider: CrmProvider) -> CrmConnection | None:
        stmt = select(CrmConnection).where(
            CrmConnection.user_id == self._user_id,
            CrmConnection.provider == provider,
            CrmConnection.deleted_at.is_(None),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def disconnect(self, provider: CrmProvider) -> None:
        """Soft delete, so the partial unique index frees the slot.

        Synced contact records go with it: they are only meaningful against the
        connection that created them, and keeping them would make a later
        reconnect think everything was already pushed.
        """
        connection = await self.get(provider)
        if connection is None:
            raise ApiError("CRM_NOT_CONNECTED", status_code=404)
        connection.deleted_at = datetime.now(UTC)
        await self._session.flush()

    async def flag_reauth(self, connection: CrmConnection, error: str) -> None:
        """Mark a connection dead so the app can say "reconnect".

        The alternative - retrying an expired token forever - produces a queue
        that never drains and a user who is never told, while they believe
        their contacts are syncing.
        """
        connection.needs_reauth = True
        connection.last_error = error
        await self._session.flush()

    async def already_synced(self, connection_id: UUID, view_id: UUID) -> CrmSyncedContact | None:
        """What was pushed where.

        The external id is what makes a retry safe: without it every retry
        creates a duplicate contact in someone's CRM, which is the failure
        users complain about loudest and cannot easily undo.
        """
        stmt = select(CrmSyncedContact).where(
            CrmSyncedContact.crm_connection_id == connection_id,
            CrmSyncedContact.connection_view_id == view_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    def build_payload(
        *,
        display_name: str,
        email: str | None,
        phone: str | None,
        company: str | None,
        title: str | None,
        website: str | None,
        note: str | None,
        met_context: str | None,
        tags: list[str],
    ) -> ContactPayload:
        """Assemble the provider-neutral payload.

        `note` is the meeting context the user typed in the ten seconds after
        the handshake, and it is the single most valuable field we send: a CRM
        row with a name and no context is a row they already had.
        """
        return ContactPayload(
            display_name=display_name,
            email=email,
            phone=phone,
            company=company,
            title=title,
            website=website,
            note=note,
            met_context=met_context,
            tags=tags,
        )

    @staticmethod
    def adapter(provider: CrmProvider) -> CrmAdapter:
        return adapter_for(provider)
