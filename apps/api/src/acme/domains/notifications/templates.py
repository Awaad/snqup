"""Email subject and body rendering, per locale.

THE ONE PLACE the backend renders user-visible text, and it needs justifying:
everywhere else the API returns a CODE and the client owns the wording
(ADR-0011). An inbox has no client, so email is the exception.

Four locales, matching the clients (ADR-0011). `en` is the fallback, and
falling back is deliberate: a notification in the wrong language still tells
someone that fourteen people are waiting, while a blank one tells them nothing.

Strings live here rather than in the client locale files because only the
server sends email. When a translator is involved they move to the same
pipeline as the client strings; the SHAPE here — keyed by locale, then by
kind — is what makes that a move rather than a rewrite.
"""

from acme.domains.notifications.models import Notification

DEFAULT_LOCALE = "en"

#: Subject templates. Placeholders are named so a translator can reorder them,
#: which matters for German where the verb moves.
SUBJECTS: dict[str, dict[str, str]] = {
    "en": {
        "post_event_digest": "You met {connections} people at {event_name}",
        "event_announcement": "{event_name}: an update from the organizers",
        "crm_sync_failed": "Your CRM sync needs attention",
        "reminder_due": "Time to follow up",
        "reciprocity_nudge": "Someone is waiting to hear from you",
        "pending_request": "Someone saved your card",
    },
    "de": {
        "post_event_digest": "Sie haben {connections} Personen bei {event_name} getroffen",
        "event_announcement": "{event_name}: Neuigkeiten von den Veranstaltern",
        "crm_sync_failed": "Ihre CRM-Synchronisierung benötigt Aufmerksamkeit",
        "reminder_due": "Zeit für ein Follow-up",
        "reciprocity_nudge": "Jemand wartet auf Ihre Antwort",
        "pending_request": "Jemand hat Ihre Karte gespeichert",
    },
    "tr": {
        "post_event_digest": "{event_name} etkinliğinde {connections} kişiyle tanıştınız",
        "event_announcement": "{event_name}: organizatörlerden bir güncelleme",
        "crm_sync_failed": "CRM senkronizasyonunuz ilgi bekliyor",
        "reminder_due": "Takip zamanı",
        "reciprocity_nudge": "Biri sizden haber bekliyor",
        "pending_request": "Biri kartınızı kaydetti",
    },
    "ar": {
        "post_event_digest": "قابلت {connections} أشخاص في {event_name}",
        "event_announcement": "{event_name}: تحديث من المنظمين",
        "crm_sync_failed": "مزامنة CRM تحتاج إلى انتباهك",
        "reminder_due": "حان وقت المتابعة",
        "reciprocity_nudge": "شخص ما ينتظر ردك",
        "pending_request": "قام أحدهم بحفظ بطاقتك",
    },
}

FALLBACK_SUBJECT: dict[str, str] = {
    "en": "You have a notification",
    "de": "Sie haben eine Benachrichtigung",
    "tr": "Bir bildiriminiz var",
    "ar": "لديك إشعار",
}


def resolve_locale(locale: str | None) -> str:
    """`de-AT` resolves to `de`.

    Clients send full BCP 47 tags and we translate by language, so matching on
    the exact tag would silently fall back to English for every regional
    variant - which is most real traffic.
    """
    if not locale:
        return DEFAULT_LOCALE
    language = locale.strip().lower().replace("_", "-").split("-")[0]
    return language if language in SUBJECTS else DEFAULT_LOCALE


def subject_for(notification: Notification, locale: str | None = None) -> str:
    resolved = resolve_locale(locale)
    kind = str(notification.kind)
    template = SUBJECTS[resolved].get(kind) or SUBJECTS[DEFAULT_LOCALE].get(kind)
    if template is None:
        return FALLBACK_SUBJECT[resolved]
    try:
        return template.format(**notification.payload)
    except (KeyError, IndexError):
        # A payload missing a field must not stop the send. A generic subject
        # delivers the notification; a KeyError loses it.
        return FALLBACK_SUBJECT[resolved]


#: Body fragments. Only the kinds that actually send email have one; the rest
#: are push-only and fall through to the generic body.
BODIES: dict[str, dict[str, str]] = {
    "en": {
        "digest_intro": "<p>You made <strong>{count}</strong> connections at {event}.</p>",
        "digest_missing": (
            "<p>{missing} of them have no notes yet — adding one now, while you "
            "still remember, is the difference between a contact and a name.</p>"
        ),
        "crm_failed": (
            "<p>We could not sync your contacts to your CRM. Your connections "
            "are safe here, but they are not reaching your CRM until you "
            "reconnect it.</p>"
        ),
        "generic": "<p>You have a new notification.</p>",
    },
    "de": {
        "digest_intro": "<p>Sie haben bei {event} <strong>{count}</strong> Kontakte geknüpft.</p>",
        "digest_missing": (
            "<p>{missing} davon haben noch keine Notizen. Jetzt eine "
            "hinzuzufügen, solange Sie sich erinnern, macht aus einem Namen "
            "einen Kontakt.</p>"
        ),
        "crm_failed": (
            "<p>Ihre Kontakte konnten nicht mit Ihrem CRM synchronisiert "
            "werden. Sie sind hier sicher, erreichen Ihr CRM aber erst nach "
            "einer erneuten Verbindung.</p>"
        ),
        "generic": "<p>Sie haben eine neue Benachrichtigung.</p>",
    },
    "tr": {
        "digest_intro": "<p>{event} etkinliğinde <strong>{count}</strong> bağlantı kurdunuz.</p>",
        "digest_missing": (
            "<p>Bunların {missing} tanesinde henüz not yok. Hatırlarken not "
            "eklemek, bir ismi gerçek bir bağlantıya dönüştürür.</p>"
        ),
        "crm_failed": (
            "<p>Kişilerinizi CRM'inize aktaramadık. Bağlantılarınız burada "
            "güvende, ancak yeniden bağlanana kadar CRM'inize ulaşmayacak.</p>"
        ),
        "generic": "<p>Yeni bir bildiriminiz var.</p>",
    },
    "ar": {
        "digest_intro": "<p>لقد كوّنت <strong>{count}</strong> اتصالات في {event}.</p>",
        "digest_missing": (
            "<p>{missing} منها بلا ملاحظات. إضافة ملاحظة الآن، بينما تتذكر، "
            "هي الفرق بين جهة اتصال ومجرد اسم.</p>"
        ),
        "crm_failed": (
            "<p>تعذّرت مزامنة جهات اتصالك مع CRM. اتصالاتك آمنة هنا، لكنها لن "
            "تصل إلى CRM حتى تعيد الربط.</p>"
        ),
        "generic": "<p>لديك إشعار جديد.</p>",
    },
}


def body_for(notification: Notification, locale: str | None = None) -> str:
    """Minimal HTML.

    Deliberately plain: email clients render a decade of inconsistent CSS, and
    a digest that arrives readable everywhere beats one that looks designed in
    two clients and broken in nine.

    For `ar` the caller wraps this in `dir="rtl"`. It is not set here because
    direction belongs to the document, not to a fragment.

    EVERY non-transactional email needs an unsubscribe link. That is a legal
    requirement, and the link is added by the caller from the recipient's token
    rather than built here, so this function never sees one.
    """
    resolved = resolve_locale(locale)
    strings = BODIES[resolved]
    kind = str(notification.kind)
    payload = notification.payload

    if kind == "post_event_digest":
        parts = [
            strings["digest_intro"].format(
                count=payload.get("connections", 0),
                event=payload.get("event_name", ""),
            )
        ]
        missing = payload.get("without_notes", 0)
        if missing:
            # The actionable half. It turns a digest from a summary into a
            # prompt.
            parts.append(strings["digest_missing"].format(missing=missing))
        return "".join(parts)

    if kind == "crm_sync_failed":
        return strings["crm_failed"]

    if kind == "event_announcement":
        message = payload.get("message", "")
        return f"<p>{message}</p>"

    return strings["generic"]


def is_rtl(locale: str | None) -> bool:
    """Whether the caller should wrap the body in `dir="rtl"`."""
    return resolve_locale(locale) == "ar"
