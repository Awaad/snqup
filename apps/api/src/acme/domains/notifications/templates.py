"""Email subject and body rendering.

THE ONE PLACE the backend renders user-visible text, and it needs justifying:
everywhere else the API returns a CODE and the client owns the wording
(ADR-0011). An inbox has no client, so email is the exception.

Kept here rather than in the worker so the strings sit together, and structured
so they can move to translation files without touching delivery logic.

Locale comes from the user's profile. Falling back to English is deliberate:
a notification in the wrong language still tells someone that fourteen people
are waiting, and a blank one tells them nothing.
"""

from acme.domains.notifications.models import Notification

#: Subject lines per kind. Templates rather than sentences, so a translator can
#: reorder without touching code.
SUBJECTS: dict[str, str] = {
    "post_event_digest": "You met {connections} people at {event_name}",
    "event_announcement": "{event_name}: an update from the organizers",
    "crm_sync_failed": "Your CRM sync needs attention",
    "reminder_due": "Time to follow up",
    "reciprocity_nudge": "Someone is waiting to hear from you",
    "pending_request": "Someone saved your card",
}


def subject_for(notification: Notification) -> str:
    template = SUBJECTS.get(str(notification.kind), "You have a notification")
    try:
        return template.format(**notification.payload)
    except (KeyError, IndexError):
        # A payload missing a field must not stop the send. A generic subject
        # delivers the notification; a KeyError loses it.
        return "You have a notification"


def body_for(notification: Notification) -> str:
    """Minimal HTML.

    Deliberately plain: email clients render a decade of inconsistent CSS, and
    a digest that arrives readable everywhere beats one that looks designed in
    two clients and broken in nine.

    EVERY non-transactional email needs an unsubscribe link. That is a legal
    requirement, and the link is added by the caller from the recipient's
    token rather than built here, so this function never sees one.
    """
    kind = str(notification.kind)
    payload = notification.payload

    if kind == "post_event_digest":
        count = payload.get("connections", 0)
        missing = payload.get("without_notes", 0)
        event = payload.get("event_name", "the event")
        lines = [
            f"<p>You made <strong>{count}</strong> connections at {event}.</p>",
        ]
        if missing:
            # The actionable half. It is what turns a digest from a summary
            # into a prompt.
            lines.append(
                f"<p>{missing} of them have no notes yet — adding one now, "
                "while you still remember, is the difference between a contact "
                "and a name.</p>"
            )
        return "".join(lines)

    if kind == "crm_sync_failed":
        return (
            "<p>We could not sync your contacts to your CRM. Your connections "
            "are safe here, but they are not reaching your CRM until you "
            "reconnect it.</p>"
        )

    if kind == "event_announcement":
        message = payload.get("message", "")
        return f"<p>{message}</p>"

    return "<p>You have a new notification.</p>"
