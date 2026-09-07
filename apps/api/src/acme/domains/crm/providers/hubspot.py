"""HubSpot CRM.

The COMPLEX end of the interface: contacts, companies, deals, and custom
properties an admin defines per portal.

That last one is why `field_mapping` is stored per CONNECTION rather than per
provider. Two HubSpot accounts put "how we met" in differently named custom
properties, and hardcoding one means silently writing into the wrong field -
or into nothing, which is worse because it looks like it worked.
"""

from acme.domains.crm.adapter import ContactPayload, SyncResult
from acme.domains.crm.enums import SyncOutcome

CRM_API = "https://api.hubapi.com/crm/v3"

#: Sensible defaults. Overridden per connection, because custom property names
#: are chosen by whoever set up that portal.
DEFAULT_FIELD_MAPPING: dict[str, str] = {
    "note": "hs_content",
    "met_context": "hs_lead_status",
}


class HubSpotAdapter:
    provider = "hubspot"

    def to_properties(
        self, contact: ContactPayload, field_mapping: dict[str, str] | None = None
    ) -> dict[str, str]:
        """Map onto HubSpot's flat `properties` dict.

        Standard properties are fixed; anything contextual goes through the
        per-connection mapping. An unmapped field is DROPPED rather than
        guessed at - writing "how we met" into a property that happens to exist
        is worse than not writing it, because nobody notices.
        """
        mapping = {**DEFAULT_FIELD_MAPPING, **(field_mapping or {})}
        properties: dict[str, str] = {}

        first, _, last = contact.display_name.partition(" ")
        properties["firstname"] = first
        if last:
            properties["lastname"] = last
        if contact.email:
            properties["email"] = contact.email
        if contact.phone:
            properties["phone"] = contact.phone
        if contact.company:
            properties["company"] = contact.company
        if contact.title:
            properties["jobtitle"] = contact.title
        if contact.website:
            properties["website"] = contact.website

        for source, value in (("note", contact.note), ("met_context", contact.met_context)):
            target = mapping.get(source)
            if target and value:
                properties[target] = value

        return properties

    async def push_contact(
        self,
        credentials: dict[str, str],
        contact: ContactPayload,
        external_id: str | None,
    ) -> SyncResult:
        """NOT IMPLEMENTED YET.

        Same reasoning as Google Contacts: the mapping is the reviewable part
        and is testable now; the HTTP half needs a HubSpot app that cannot be
        registered until the product has a name.
        """
        raise NotImplementedError(
            "HubSpot sync needs an OAuth app. Blocked on the product name (00-context/naming.md)."
        )

    async def verify(self, credentials: dict[str, str]) -> bool:
        raise NotImplementedError

    @staticmethod
    def outcome_for(external_id: str | None) -> SyncOutcome:
        return SyncOutcome.UPDATED if external_id else SyncOutcome.CREATED
