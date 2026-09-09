"""Object storage: Supabase Storage.

THE API NEVER PROXIES BYTES. The client asks for a signed upload URL and
uploads directly to storage. Proxying would put every image through our memory
and bandwidth for no benefit, and at an event that is exactly when both are
scarce.

THE STORAGE KEY IS GENERATED HERE, never supplied by the client. A
client-chosen key is a path traversal and an overwrite of someone else's avatar
waiting to happen — `../../avatars/someone-else.jpg` is the whole attack.

WHAT WE CANNOT DO, and must therefore constrain around: because bytes never
reach us, we cannot verify a file is really an image. Mitigations are the
content-type allowlist below, a bucket size cap, and serving with an explicit
content type. SVG is excluded deliberately: it is a document format that
executes script, and an SVG avatar on the UGC domain is stored XSS.
"""

import mimetypes
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

import httpx
import structlog

from acme.core.errors import ApiError
from acme.core.ids import new_id

log = structlog.get_logger()


class UploadPurpose(StrEnum):
    CARD_PHOTO = "card_photo"
    ORG_LOGO = "org_logo"
    EVENT_BANNER = "event_banner"
    LINK_IMAGE = "link_image"


#: Raster only. SVG executes script and would be stored XSS on the UGC domain.
ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/avif"}
)

#: Per purpose, in bytes. A banner is displayed large; an avatar never is, and
#: allowing a 10MB avatar means every public card page drags it down.
SIZE_LIMITS: dict[UploadPurpose, int] = {
    UploadPurpose.CARD_PHOTO: 2 * 1024 * 1024,
    UploadPurpose.ORG_LOGO: 2 * 1024 * 1024,
    UploadPurpose.EVENT_BANNER: 8 * 1024 * 1024,
    UploadPurpose.LINK_IMAGE: 4 * 1024 * 1024,
}

#: Which bucket each purpose lives in.
#:
#: `public` is genuinely public: card photos and event banners render on pages
#: served to strangers, so signed read URLs would buy nothing and cost a round
#: trip per image on the surface with a sub-one-second budget.
BUCKETS: dict[UploadPurpose, str] = {
    UploadPurpose.CARD_PHOTO: "public",
    UploadPurpose.ORG_LOGO: "public",
    UploadPurpose.EVENT_BANNER: "public",
    UploadPurpose.LINK_IMAGE: "public",
}


@dataclass(frozen=True, slots=True)
class SignedUpload:
    upload_url: str
    storage_key: str
    #: Supabase returns a token the client passes back on upload.
    token: str


def build_storage_key(purpose: UploadPurpose, owner_id: UUID, content_type: str) -> str:
    """`<purpose>/<owner>/<random>.<ext>`.

    Namespaced by owner so one user cannot overwrite another's file even if a
    key leaks, and random rather than sequential so keys are not enumerable —
    these end up in public URLs, and a predictable one lets anyone walk the
    bucket.
    """
    extension = mimetypes.guess_extension(content_type) or ".bin"
    if extension == ".jpe":
        extension = ".jpg"
    return f"{purpose.value}/{owner_id}/{new_id().hex}{extension}"


class SupabaseStorage:
    def __init__(self, *, url: str, service_key: str) -> None:
        self._url = url.rstrip("/")
        self._service_key = service_key

    def _require_config(self) -> None:
        if not self._url or not self._service_key:
            raise ApiError(
                "SERVICE_UNAVAILABLE",
                status_code=503,
                message=("storage is not configured; set SUPABASE_URL and SUPABASE_SERVICE_KEY"),
            )

    def validate(self, purpose: UploadPurpose, content_type: str, size: int) -> None:
        """Reject before signing, not after uploading.

        A file rejected at confirm time has already crossed the network and
        occupies a bucket, and the user has already waited.
        """
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ApiError(
                "UPLOAD_TYPE_NOT_ALLOWED",
                status_code=422,
                message=f"{content_type} is not an allowed image type",
                details={"allowed": sorted(ALLOWED_CONTENT_TYPES)},
            )
        limit = SIZE_LIMITS[purpose]
        if size > limit:
            raise ApiError(
                "UPLOAD_TOO_LARGE",
                status_code=422,
                message=f"{purpose.value} is limited to {limit // (1024 * 1024)}MB",
                details={"limit_bytes": limit},
            )

    async def signed_upload(self, purpose: UploadPurpose, storage_key: str) -> SignedUpload:
        """Ask Supabase for a one-time upload URL.

        The service key never leaves the server. Handing it to a client would
        grant write access to every bucket, which is the failure mode this
        whole indirection exists to avoid.
        """
        self._require_config()
        bucket = BUCKETS[purpose]

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self._url}/storage/v1/object/upload/sign/{bucket}/{storage_key}",
                headers={"Authorization": f"Bearer {self._service_key}"},
            )
        if response.status_code >= 400:
            log.error(
                "storage.sign_failed",
                status=response.status_code,
                purpose=purpose.value,
            )
            raise ApiError(
                "SERVICE_UNAVAILABLE",
                status_code=503,
                message="could not create an upload URL; retry shortly",
            )

        payload = response.json()
        return SignedUpload(
            upload_url=f"{self._url}/storage/v1{payload['url']}",
            storage_key=storage_key,
            token=str(payload.get("token", "")),
        )

    def public_url(self, purpose: UploadPurpose, storage_key: str) -> str:
        """The CDN URL for a stored object.

        A path is stored, never a URL. URLs contain a project reference and a
        CDN host, both of which change - a stored URL becomes a broken image
        the day either does, across every row that has one.
        """
        return f"{self._url}/storage/v1/object/public/{BUCKETS[purpose]}/{storage_key}"

    async def delete(self, purpose: UploadPurpose, storage_key: str) -> None:
        """Remove an object.

        Failure is logged rather than raised: this is called from a sweep and
        from erasure, and an orphaned object costs pennies while a failed
        erasure is a compliance problem.
        """
        self._require_config()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.delete(
                    f"{self._url}/storage/v1/object/{BUCKETS[purpose]}/{storage_key}",
                    headers={"Authorization": f"Bearer {self._service_key}"},
                )
        except Exception as exc:
            log.warning("storage.delete_failed", key=storage_key, error=str(exc))
