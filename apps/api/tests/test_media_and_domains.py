"""Uploads, domain verification, and email locale.

Three gaps that all had the same shape: a schema column or a table with no code
that could ever populate it.

  photo_path / logo_path / banner_path  columns read by the events router,
                                        written by nothing. No storage config,
                                        no upload endpoint.
  domain_verifications                  table read by the entitlement gate,
                                        created by nothing - so public indexed
                                        event pages were unreachable for
                                        everyone, paid or not.
  email templates                       a single English dictionary, and a
                                        docstring claiming it used the profile
                                        locale. A German user got English.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from acme.core.errors import ApiError
from acme.core.storage import (
    ALLOWED_CONTENT_TYPES,
    SIZE_LIMITS,
    SupabaseStorage,
    UploadPurpose,
    build_storage_key,
)
from acme.domains.identity.domains import (
    CHALLENGE_PREFIX,
    instructions_for,
    new_challenge_token,
    normalise_domain,
)
from acme.domains.identity.models import User, UserProfile
from acme.domains.identity.service import IdentityService
from acme.domains.notifications.enums import NotificationKind
from acme.domains.notifications.models import Notification
from acme.domains.notifications.templates import (
    body_for,
    is_rtl,
    resolve_locale,
    subject_for,
)

pytestmark = pytest.mark.integration


class TestStorageKeys:
    def test_keys_are_namespaced_by_owner(self) -> None:
        """So one user cannot overwrite another's file even if a key leaks."""
        from uuid import uuid4

        owner = uuid4()
        key = build_storage_key(UploadPurpose.CARD_PHOTO, owner, "image/jpeg")
        assert key.startswith(f"card_photo/{owner}/")

    def test_keys_are_not_enumerable(self) -> None:
        """These end up in public URLs. A predictable key lets anyone walk the
        bucket."""
        from uuid import uuid4

        owner = uuid4()
        keys = {build_storage_key(UploadPurpose.CARD_PHOTO, owner, "image/png") for _ in range(50)}
        assert len(keys) == 50

    def test_the_client_never_chooses_the_key(self) -> None:
        """A client-supplied key is path traversal: `../../avatars/someone`."""
        import inspect

        signature = inspect.signature(build_storage_key)
        assert "storage_key" not in signature.parameters
        assert "path" not in signature.parameters


class TestUploadValidation:
    @pytest.fixture
    def storage(self) -> SupabaseStorage:
        return SupabaseStorage(url="https://x.supabase.co", service_key="k")

    def test_svg_is_refused(self, storage: SupabaseStorage) -> None:
        """SVG executes script. An SVG avatar on the UGC domain is stored XSS,
        which is the highest-consequence vulnerability in this product."""
        assert "image/svg+xml" not in ALLOWED_CONTENT_TYPES
        with pytest.raises(ApiError) as exc:
            storage.validate(UploadPurpose.CARD_PHOTO, "image/svg+xml", 1000)
        assert exc.value.code == "UPLOAD_TYPE_NOT_ALLOWED"

    @pytest.mark.parametrize(
        "content_type", ["text/html", "application/pdf", "application/javascript"]
    )
    def test_non_images_are_refused(self, storage: SupabaseStorage, content_type: str) -> None:
        with pytest.raises(ApiError):
            storage.validate(UploadPurpose.CARD_PHOTO, content_type, 1000)

    def test_oversized_files_are_refused_before_upload(self, storage: SupabaseStorage) -> None:
        """A file rejected at confirm time has already crossed the network and
        the user has already waited."""
        with pytest.raises(ApiError) as exc:
            storage.validate(
                UploadPurpose.CARD_PHOTO,
                "image/jpeg",
                SIZE_LIMITS[UploadPurpose.CARD_PHOTO] + 1,
            )
        assert exc.value.code == "UPLOAD_TOO_LARGE"
        assert exc.value.details["limit_bytes"] > 0

    def test_a_banner_may_be_larger_than_an_avatar(self) -> None:
        """An avatar is never displayed large, and a 10MB one would drag down
        every public card page."""
        assert SIZE_LIMITS[UploadPurpose.EVENT_BANNER] > SIZE_LIMITS[UploadPurpose.CARD_PHOTO]

    async def test_signing_without_configuration_fails_loudly(self) -> None:
        """Silence here means uploads appear to work and nothing is stored."""
        unconfigured = SupabaseStorage(url="", service_key="")
        with pytest.raises(ApiError) as exc:
            await unconfigured.signed_upload(UploadPurpose.CARD_PHOTO, "card_photo/x/y.jpg")
        assert exc.value.status_code == 503


class TestUploadLifecycle:
    async def _user(self, session: AsyncSession, name: str) -> User:
        user = User()
        session.add(user)
        await session.flush()
        session.add(
            UserProfile(user_id=user.id, auth_subject=f"m-{name}", email=f"{name}@example.com")
        )
        await session.flush()
        return user

    async def test_attaching_someone_elses_upload_is_refused(self, session: AsyncSession) -> None:
        """THE ATTACK: PATCH `photo_path` to a key you do not own. Minor for a
        public image, and not minor at all once any bucket holds something
        private."""
        from acme.domains.media.models import Upload
        from acme.domains.media.service import MediaService

        owner = await self._user(session, "own")
        other = await self._user(session, "other")

        session.add(
            Upload(
                user_id=owner.id,
                purpose=UploadPurpose.CARD_PHOTO,
                storage_key="card_photo/owner/abc.jpg",
                content_type="image/jpeg",
                confirmed_at=None,
            )
        )
        await session.flush()

        service = MediaService(session, SupabaseStorage(url="https://x", service_key="k"))
        with pytest.raises(ApiError):
            await service.assert_owned(
                other.id, "card_photo/owner/abc.jpg", UploadPurpose.CARD_PHOTO
            )

    async def test_an_unconfirmed_upload_cannot_be_attached(self, session: AsyncSession) -> None:
        """A row without confirmation may have no bytes behind it at all."""
        from acme.domains.media.models import Upload
        from acme.domains.media.service import MediaService

        owner = await self._user(session, "unconf")
        session.add(
            Upload(
                user_id=owner.id,
                purpose=UploadPurpose.CARD_PHOTO,
                storage_key="card_photo/unconf/abc.jpg",
                content_type="image/jpeg",
            )
        )
        await session.flush()

        service = MediaService(session, SupabaseStorage(url="https://x", service_key="k"))
        with pytest.raises(ApiError):
            await service.assert_owned(
                owner.id, "card_photo/unconf/abc.jpg", UploadPurpose.CARD_PHOTO
            )

    def test_a_path_is_stored_not_a_url(self) -> None:
        """A URL contains a project reference and a CDN host. Storing one makes
        every row a broken image the day either changes."""
        storage = SupabaseStorage(url="https://proj.supabase.co", service_key="k")
        url = storage.public_url(UploadPurpose.CARD_PHOTO, "card_photo/u/a.jpg")
        assert url.endswith("card_photo/u/a.jpg")
        assert url.startswith("https://proj.supabase.co")


class TestDomainNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("https://www.example.com/", "example.com"),
            ("http://example.com/path?q=1", "example.com"),
            ("  Example.COM  ", "example.com"),
            ("example.com.", "example.com"),
            ("sub.example.com", "sub.example.com"),
        ],
    )
    def test_pasted_urls_become_domains(self, raw: str, expected: str) -> None:
        """People paste URLs. Treating one as a domain produces a lookup that
        can never succeed and an error that does not explain why."""
        assert normalise_domain(raw) == expected


class TestDomainChallenge:
    def test_tokens_are_random_not_derived(self) -> None:
        """A derived token is predictable, and a predictable token lets anyone
        work out what to publish and claim a domain they do not control."""
        assert len({new_challenge_token() for _ in range(100)}) == 100

    def test_the_record_goes_on_a_dedicated_subdomain(self) -> None:
        """Apex TXT records are crowded with SPF and DMARC, and there is a
        255-character limit."""
        instructions = instructions_for("example.com", "tok")
        assert instructions.host == "_acme-verify.example.com"
        assert instructions.record_type == "TXT"
        assert instructions.value == f"{CHALLENGE_PREFIX}tok"

    async def test_a_challenge_is_reissued_not_duplicated(self, session: AsyncSession) -> None:
        """A user who lost the instructions should get the SAME token, not a
        second pending record to publish."""
        from acme.domains.identity.models import Organization

        org = Organization(name="Acme", slug="acme-dv")
        session.add(org)
        await session.flush()

        service = IdentityService(session)
        first, _ = await service.start_domain_verification(org.id, "example.com")
        second, _ = await service.start_domain_verification(org.id, "https://www.example.com/")
        assert first.challenge_token == second.challenge_token
        assert first.id == second.id

    async def test_an_unverified_domain_does_not_satisfy_the_gate(
        self, session: AsyncSession
    ) -> None:
        """Creating a challenge must not grant anything. Otherwise the gate on
        public indexed pages is bypassed by asking for it."""
        from acme.domains.identity.models import Organization

        org = Organization(name="Acme", slug="acme-dv2")
        session.add(org)
        await session.flush()

        service = IdentityService(session)
        await service.start_domain_verification(org.id, "example.com")
        assert await service.has_verified_domain(org.id) is False


class TestEmailLocale:
    """The bug: a single English dictionary, and a docstring claiming it used
    the profile locale."""

    def _notification(self, kind: NotificationKind, **payload: object) -> Notification:
        return Notification(kind=kind, payload=payload, user_id=None)  # type: ignore[arg-type]

    @pytest.mark.parametrize("locale", ["de", "tr", "ar"])
    def test_subjects_are_translated(self, locale: str) -> None:
        notification = self._notification(
            NotificationKind.POST_EVENT_DIGEST, connections=14, event_name="DevCon"
        )
        english = subject_for(notification, "en")
        translated = subject_for(notification, locale)
        assert translated != english
        assert "14" in translated

    def test_a_regional_tag_resolves_to_its_language(self) -> None:
        """Clients send full BCP 47 tags. Matching the exact tag would fall
        back to English for every regional variant, which is most traffic."""
        assert resolve_locale("de-AT") == "de"
        assert resolve_locale("ar_EG") == "ar"

    def test_an_unknown_locale_falls_back_to_english(self) -> None:
        """A notification in the wrong language still says fourteen people are
        waiting. A blank one says nothing."""
        assert resolve_locale("kl") == "en"
        assert resolve_locale(None) == "en"

    def test_bodies_are_translated(self) -> None:
        notification = self._notification(
            NotificationKind.POST_EVENT_DIGEST,
            connections=3,
            without_notes=2,
            event_name="DevCon",
        )
        assert body_for(notification, "de") != body_for(notification, "en")

    def test_arabic_is_flagged_rtl(self) -> None:
        """Direction belongs to the document, so the caller wraps it — but it
        has to know."""
        assert is_rtl("ar") is True
        assert is_rtl("ar-EG") is True
        assert is_rtl("en") is False

    def test_a_missing_payload_field_still_sends(self) -> None:
        """A generic subject delivers the notification; a KeyError loses it."""
        notification = self._notification(NotificationKind.POST_EVENT_DIGEST)
        assert subject_for(notification, "de")

    def test_every_locale_covers_every_email_kind(self) -> None:
        """A missing string falls back to English silently, so the gap would
        only be visible to the person receiving it."""
        from acme.domains.notifications.templates import SUBJECTS

        english = set(SUBJECTS["en"])
        for locale, strings in SUBJECTS.items():
            assert set(strings) == english, f"{locale} is missing keys"
