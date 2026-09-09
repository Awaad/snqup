"""CRM service. The only surface other domains may use."""

from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.domains.crm.adapter import ContactPayload, CrmAdapter, CrmAuthError
from acme.domains.crm.enums import CrmProvider
from acme.domains.crm.models import CrmConnection, CrmSyncedContact
from acme.domains.crm.oauth import OAuthTokens, TokenCipher
from acme.domains.crm.registry import adapter_for

log = structlog.get_logger()


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

    async def connect(
        self,
        provider: CrmProvider,
        tokens: OAuthTokens,
        cipher: TokenCipher,
    ) -> CrmConnection:
        """Store a completed authorization.

        Replaces any existing connection for this provider rather than adding
        one: the partial unique index allows a single live row per subject per
        provider, and two would double-write every contact with neither
        obviously wrong.

        Credentials are encrypted before they touch the row. A CRM refresh
        token is write access to someone's customer database, so a database
        dump alone must not yield working credentials.
        """
        existing = await self.get(provider)
        if existing is not None:
            existing.deleted_at = datetime.now(UTC)
            await self._session.flush()

        connection = CrmConnection(
            user_id=self._user_id,
            provider=provider,
            credentials=cipher.encrypt(tokens),
        )
        self._session.add(connection)
        await self._session.flush()
        return connection

    async def credentials(self, connection: CrmConnection, cipher: TokenCipher) -> OAuthTokens:
        return cipher.decrypt(connection.credentials)

    async def store_credentials(
        self, connection: CrmConnection, tokens: OAuthTokens, cipher: TokenCipher
    ) -> None:
        """Persist refreshed tokens.

        Called after every refresh. Skipping it means the next run refreshes
        again, and a provider that rotates refresh tokens would invalidate the
        stored one and break the connection permanently.
        """
        connection.credentials = cipher.encrypt(tokens)
        connection.needs_reauth = False
        connection.last_error = None
        await self._session.flush()

    async def set_field_mapping(
        self, provider: CrmProvider, mapping: dict[str, str]
    ) -> CrmConnection:
        connection = await self.get(provider)
        if connection is None:
            raise ApiError("CRM_NOT_CONNECTED", status_code=404)
        connection.field_mapping = mapping
        await self._session.flush()
        return connection

    async def record_sync(
        self,
        connection: CrmConnection,
        view_id: UUID,
        *,
        external_id: str | None,
        error: str | None = None,
    ) -> None:
        """What was pushed where.

        The external id is what makes a retry safe: without it every retry
        creates a duplicate contact in someone's CRM, which is the failure
        users complain about loudest and cannot easily undo.
        """
        existing = await self.already_synced(connection.id, view_id)
        if existing is not None:
            existing.external_id = external_id or existing.external_id
            existing.synced_at = datetime.now(UTC)
            existing.error = error
        else:
            self._session.add(
                CrmSyncedContact(
                    crm_connection_id=connection.id,
                    connection_view_id=view_id,
                    external_id=external_id,
                    error=error,
                )
            )
        connection.last_sync_at = datetime.now(UTC)
        await self._session.flush()

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


class CrmSyncService:
    """Pushing contacts. Separate from CrmService because it acts for the JOB
    runner rather than for a signed-in user: it starts from a connection id and
    resolves the owner from it, where CrmService starts from a user."""

    def __init__(self, session: AsyncSession, token_key: str) -> None:
        self._session = session
        self._cipher = TokenCipher(token_key)

    async def push(self, connection_id: UUID, view_ids: list[UUID]) -> int:
        connection = await self._session.get(CrmConnection, connection_id)
        if connection is None or connection.deleted_at is not None:
            return 0
        if connection.needs_reauth:
            # Already flagged. Retrying a revoked grant forever is how a queue
            # stops draining.
            return 0

        adapter = adapter_for(connection.provider)
        try:
            tokens = self._cipher.decrypt(connection.credentials)
        except CrmAuthError as exc:
            await self._flag_reauth(connection, str(exc))
            return 0

        contacts = await self._contacts_for(connection, view_ids)

        pushed = 0
        for view_id, payload in contacts:
            already = await self._already_synced(connection.id, view_id)
            external_id = already.external_id if already else None
            try:
                result = await adapter.push_contact(
                    {"access_token": tokens.access_token},
                    payload,
                    external_id,
                )
            except CrmAuthError as exc:
                # The user revoked access, or the grant expired. Stop the whole
                # run: every remaining contact would fail the same way.
                await self._flag_reauth(connection, str(exc))
                return pushed
            except NotImplementedError as exc:
                # Credentials for this provider do not exist yet. Recorded as a
                # connection error rather than crashing the worker, so the
                # state is visible instead of silent.
                connection.last_error = str(exc)
                await self._session.flush()
                return pushed
            except Exception as exc:
                await self._record(connection, view_id, None, str(exc))
                log.warning(
                    "crm.push_failed",
                    connection_id=str(connection.id),
                    error=str(exc),
                )
                continue

            await self._record(connection, view_id, result.external_id, None)
            pushed += 1

        connection.last_sync_at = datetime.now(UTC)
        await self._session.flush()
        log.info("crm.synced", connection_id=str(connection.id), count=pushed)
        return pushed

    async def _flag_reauth(self, connection: CrmConnection, error: str) -> None:
        """Tell the user, rather than retrying silently.

        A CRM that stopped syncing is worse than one never connected, because
        they believe their contacts are safe.
        """
        connection.needs_reauth = True
        connection.last_error = error
        await self._session.flush()

        from acme.domains.notifications.enums import NotificationKind
        from acme.domains.notifications.service import NotificationsService

        if connection.user_id is not None:
            await NotificationsService(self._session).create(
                connection.user_id,
                NotificationKind.CRM_SYNC_FAILED,
                {"provider": str(connection.provider)},
            )

    async def _already_synced(self, connection_id: UUID, view_id: UUID) -> CrmSyncedContact | None:
        stmt = select(CrmSyncedContact).where(
            CrmSyncedContact.crm_connection_id == connection_id,
            CrmSyncedContact.connection_view_id == view_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _record(
        self,
        connection: CrmConnection,
        view_id: UUID,
        external_id: str | None,
        error: str | None,
    ) -> None:
        existing = await self._already_synced(connection.id, view_id)
        if existing is not None:
            existing.external_id = external_id or existing.external_id
            existing.synced_at = datetime.now(UTC)
            existing.error = error
        else:
            self._session.add(
                CrmSyncedContact(
                    crm_connection_id=connection.id,
                    connection_view_id=view_id,
                    external_id=external_id,
                    error=error,
                )
            )
        await self._session.flush()

    async def _contacts_for(
        self, connection: CrmConnection, view_ids: list[UUID]
    ) -> list[tuple[UUID, ContactPayload]]:
        """Build payloads from the owner's own connection views.

        Goes through the connections domain rather than querying its tables:
        the note is the single most valuable field we send, and it lives in a
        per-user row for exactly the reasons ADR-0003 gives.
        """
        from acme.domains.connections.service import CrmExportService

        if connection.user_id is None:
            return []
        return await CrmExportService(self._session).contacts_for_sync(connection.user_id, view_ids)
