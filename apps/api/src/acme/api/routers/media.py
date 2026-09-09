"""Upload endpoints.

Two phases, because the API never proxies image bytes:

  POST /v1/uploads              validate, return a signed URL
  (client PUTs to storage)
  POST /v1/uploads/{id}/confirm mark it done, get the path back

The returned `storage_key` is then attached to a card, organization or event
via that resource's own PATCH. Attaching a key you do not own is refused.
"""

from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field

from acme.api.deps import CurrentUserDep, SessionDep
from acme.core.storage import SupabaseStorage, UploadPurpose
from acme.domains.media.service import MediaService

router = APIRouter(prefix="/v1", tags=["media"])


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: UploadPurpose
    #: Validated against an allowlist. SVG is excluded: it executes script, and
    #: an SVG avatar on the UGC domain is stored XSS.
    content_type: str = Field(max_length=100)
    #: Declared up front so an oversized file is refused BEFORE it crosses the
    #: network rather than after.
    size_bytes: int = Field(gt=0)


class UploadTicket(BaseModel):
    upload_id: UUID
    #: PUT the file here. One-time, and the service key never leaves the server.
    upload_url: str
    token: str
    storage_key: str


class UploadConfirmed(BaseModel):
    #: Store this on the card, organization or event. NOT the URL: a URL
    #: contains a project reference and a CDN host, and a stored one becomes a
    #: broken image the day either changes.
    storage_key: str
    public_url: str


def _storage(request: Request) -> SupabaseStorage:
    settings = request.app.state.settings
    return SupabaseStorage(url=settings.supabase_url, service_key=settings.supabase_service_key)


@router.post("/uploads", response_model=UploadTicket, status_code=status.HTTP_201_CREATED)
async def request_upload(
    payload: UploadRequest,
    request: Request,
    session: SessionDep,
    user: CurrentUserDep,
) -> UploadTicket:
    """Ask for somewhere to put a file.

    The storage key is generated HERE, never supplied by the client. A
    client-chosen key is a path traversal and an overwrite of someone else's
    avatar waiting to happen.
    """
    service = MediaService(session, _storage(request))
    upload, signed = await service.request_upload(
        user.id,
        purpose=payload.purpose,
        content_type=payload.content_type,
        size_bytes=payload.size_bytes,
    )
    return UploadTicket(
        upload_id=upload.id,
        upload_url=signed.upload_url,
        token=signed.token,
        storage_key=signed.storage_key,
    )


@router.post("/uploads/{upload_id}/confirm", response_model=UploadConfirmed)
async def confirm_upload(
    upload_id: UUID,
    request: Request,
    session: SessionDep,
    user: CurrentUserDep,
) -> UploadConfirmed:
    """Tell us the upload finished.

    Without this step an abandoned upload is indistinguishable from a completed
    one and nothing can ever be cleaned up — the bucket grows forever with
    files nobody references.
    """
    storage = _storage(request)
    service = MediaService(session, storage)
    upload = await service.confirm(user.id, upload_id)
    return UploadConfirmed(
        storage_key=upload.storage_key,
        public_url=storage.public_url(upload.purpose, upload.storage_key),
    )
