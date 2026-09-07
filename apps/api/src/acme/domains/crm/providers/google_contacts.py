"""Google Contacts (People API).

The SIMPLE end of the interface, and useful precisely because it is missing
things: no companies, no deals, no custom properties. Everything contextual has
to go into free text.

That constraint is why `ContactPayload` keeps `note` and `met_context`
separate - a provider with real fields uses both, and this one concatenates.
"""

from acme.domains.crm.adapter import ContactPayload, SyncResult
from acme.domains.crm.enums import SyncOutcome

PEOPLE_API = "https://people.googleapis.com/v1"


class GoogleContactsAdapter:
    provider = "google_contacts"

    def to_person(self, contact: ContactPayload) -> dict[str, object]:
        """Map onto Google's `person` resource.

        Meeting context is folded into biography, because there is nowhere else
        for it. Losing it would leave a contact indistinguishable from one
        typed in by hand, which is the whole value we add.
        """
        person: dict[str, object] = {
            "names": [{"displayName": contact.display_name}],
        }
        if contact.email:
            person["emailAddresses"] = [{"value": contact.email}]
        if contact.phone:
            person["phoneNumbers"] = [{"value": contact.phone}]
        if contact.company or contact.title:
            person["organizations"] = [{"name": contact.company, "title": contact.title}]
        if contact.website:
            person["urls"] = [{"value": contact.website}]

        biography = [part for part in (contact.met_context, contact.note) if part]
        if contact.tags:
            biography.append("Tags: " + ", ".join(contact.tags))
        if biography:
            person["biographies"] = [{"value": "\n\n".join(biography)}]

        return person

    async def push_contact(
        self,
        credentials: dict[str, str],
        contact: ContactPayload,
        external_id: str | None,
    ) -> SyncResult:
        """NOT IMPLEMENTED YET.

        The mapping above is the part worth reviewing and is fully testable
        without a network call. The HTTP half needs OAuth credentials that do
        not exist until the product has a name and a Google Cloud project, so
        it is deliberately absent rather than stubbed with something that looks
        like it works.
        """
        raise NotImplementedError(
            "Google Contacts sync needs OAuth credentials. Blocked on the "
            "product name (00-context/naming.md)."
        )

    async def verify(self, credentials: dict[str, str]) -> bool:
        raise NotImplementedError

    @staticmethod
    def outcome_for(external_id: str | None) -> SyncOutcome:
        return SyncOutcome.UPDATED if external_id else SyncOutcome.CREATED
