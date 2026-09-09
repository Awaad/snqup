"""Media service.

Two phases, and the second one is the point:

  request   validate, generate a key, record the intent, return a signed URL
  confirm   the client says it uploaded; we mark the row and hand back the path

Without `confirm`, an abandoned upload is indistinguishable from a completed
one and nothing can ever be cleaned up.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.core.storage import (
    SignedUpload,
    SupabaseStorage,
    UploadPurpose,
    build_storage_key,
)
from acme.domains.media.models import Upload

#: An upload requested and never confirmed is abandoned after this. Generous,
#: because a phone on venue wifi can take a while and deleting a live upload is
#: worse than keeping a dead one for an hour.
ABANDONED_AFTER = timedelta(hours=6)

#: Unconfirmed uploads a user may hold at once. Without a cap, a client that
#: requests URLs in a loop can enumerate storage keys for free.
MAX_PENDING_PER_USER = 10


class MediaService:
    def __init__(self, session: AsyncSession, storage: SupabaseStorage) -> None:
        self._session = session
        self._storage = storage

    async def request_upload(
        self,
        user_id: UUID,
        *,
        purpose: UploadPurpose,
        content_type: str,
        size_bytes: int,
    ) -> tuple[Upload, SignedUpload]:
        """Validate and sign. Rejects BEFORE anything crosses the network."""
        self._storage.validate(purpose, content_type, size_bytes)

        pending = await self._pending_count(user_id)
        if pending >= MAX_PENDING_PER_USER:
            raise ApiError(
                "UPLOAD_TOO_MANY_PENDING",
                status_code=429,
                message="too many uploads in flight; confirm or wait",
            )

        storage_key = build_storage_key(purpose, user_id, content_type)
        signed = await self._storage.signed_upload(purpose, storage_key)

        upload = Upload(
            user_id=user_id,
            purpose=purpose,
            storage_key=storage_key,
            content_type=content_type,
            size_bytes=size_bytes,
        )
        self._session.add(upload)
        await self._session.flush()
        return upload, signed

    async def confirm(self, user_id: UUID, upload_id: UUID) -> Upload:
        """Mark an upload complete and return it.

        Scoped by user: without it, anyone holding an id could confirm someone
        else's upload, and the path would then be attachable to their own card.
        """
        stmt = select(Upload).where(Upload.id == upload_id, Upload.user_id == user_id)
        upload = (await self._session.execute(stmt)).scalar_one_or_none()
        if upload is None:
            raise ApiError("UPLOAD_NOT_FOUND", status_code=404)

        upload.confirmed_at = datetime.now(UTC)
        await self._session.flush()
        return upload

    async def assert_owned(self, user_id: UUID, storage_key: str, purpose: UploadPurpose) -> None:
        """Before attaching a path to a card, event or organization.

        THE ATTACK without this: a client PATCHes `photo_path` to a key it does
        not own. That is minor for a public image and not minor at all once any
        bucket holds something private, so the check goes in now rather than
        after the first private bucket exists.
        """
        stmt = select(Upload).where(
            Upload.storage_key == storage_key,
            Upload.user_id == user_id,
            Upload.purpose == purpose,
            Upload.confirmed_at.is_not(None),
        )
        if (await self._session.execute(stmt)).scalar_one_or_none() is None:
            raise ApiError(
                "UPLOAD_NOT_FOUND",
                status_code=422,
                message="unknown upload, or it is not yours",
            )

    def public_url(self, purpose: UploadPurpose, storage_key: str) -> str:
        return self._storage.public_url(purpose, storage_key)

    async def _pending_count(self, user_id: UUID) -> int:
        stmt = select(Upload).where(
            Upload.user_id == user_id,
            Upload.confirmed_at.is_(None),
            Upload.created_at > datetime.now(UTC) - ABANDONED_AFTER,
        )
        return len(list((await self._session.execute(stmt)).scalars()))

    async def abandoned(self, limit: int = 200) -> list[Upload]:
        """The sweep's queue: requested, never confirmed, old enough."""
        stmt = (
            select(Upload)
            .where(Upload.confirmed_at.is_(None))
            .where(Upload.created_at < datetime.now(UTC) - ABANDONED_AFTER)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def purge_abandoned(self) -> int:
        """Delete the object and the row.

        The object first: a deleted row with a surviving object is an orphan
        nothing will ever find again, while a surviving row with no object is
        merely retried.
        """
        purged = 0
        for upload in await self.abandoned():
            await self._storage.delete(upload.purpose, upload.storage_key)
            await self._session.delete(upload)
            purged += 1
        await self._session.flush()
        return purged
