"""The CRM adapter interface.

Deliberately SMALL. A wide interface written before two implementations exist
gets shaped by whichever one the author imagined, and the first real integration
then does not fit. This is the intersection of what Google Contacts and HubSpot
can both actually do.

Everything provider-specific stays behind it: field naming, custom properties,
company objects, pagination, rate limits.
"""

from dataclasses import dataclass, field
from typing import Protocol

from acme.domains.crm.enums import SyncOutcome


@dataclass(frozen=True, slots=True)
class ContactPayload:
    """One contact, in OUR shape.

    Provider-neutral on purpose: mapping this onto Google's `person` or
    HubSpot's `properties` is the adapter's job, and putting either provider's
    vocabulary here would leak it into the rest of the system.

    `note` carries the meeting context - what the user typed in the ten seconds
    after the handshake - which is the single most valuable field we send. A
    CRM row with a name and no context is a row they already had.
    """

    display_name: str
    email: str | None = None
    phone: str | None = None
    company: str | None = None
    title: str | None = None
    website: str | None = None
    note: str | None = None
    #: Where and when they met. Distinct from `note` because some providers
    #: have a real field for it and some only have free text.
    met_at: str | None = None
    met_context: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SyncResult:
    outcome: SyncOutcome
    #: The provider's id, stored so a retry updates rather than duplicating.
    #: Duplicate contacts in someone's CRM is the failure users complain about
    #: loudest, and the one they cannot easily undo.
    external_id: str | None = None
    error: str | None = None


class CrmAdapter(Protocol):
    """What every provider must implement.

    `push_contact` is the only required operation. Reading FROM a CRM is
    deliberately absent: it is a much larger surface (pagination, incremental
    sync, conflict resolution) that nothing in the product needs, and adding it
    speculatively would double the work per provider forever.
    """

    provider: str

    async def push_contact(
        self,
        credentials: dict[str, str],
        contact: ContactPayload,
        external_id: str | None,
    ) -> SyncResult:
        """Create or update one contact.

        `external_id` is present when this contact has been pushed before, and
        the adapter must UPDATE rather than create - that is what makes a retry
        safe.

        Must raise CrmAuthError when the credentials are dead, so the caller
        can flag needs_reauth. A CRM that silently stops syncing is worse than
        one never connected, because the user believes their contacts are safe.
        """
        ...

    async def verify(self, credentials: dict[str, str]) -> bool:
        """Cheap credential check, for the reconnect surface."""
        ...


class CrmAuthError(Exception):
    """Credentials are dead. The user must reconnect.

    Distinct from a transient failure on purpose: retrying an expired token
    forever produces a queue that never drains and a user who is never told.
    """


class CrmTransientError(Exception):
    """Rate limit or provider outage. Retry later, do not flag the connection."""
