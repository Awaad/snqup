"""Provider registry.

A dict, deliberately. A plugin system with entry points would be more
impressive and would make the set of providers a runtime question - and we want
it to be a compile-time one, so an unknown provider fails at startup rather
than at 2am when someone's sync breaks.
"""

from acme.domains.crm.adapter import CrmAdapter
from acme.domains.crm.enums import CrmProvider
from acme.domains.crm.providers.google_contacts import GoogleContactsAdapter
from acme.domains.crm.providers.hubspot import HubSpotAdapter

_ADAPTERS: dict[CrmProvider, CrmAdapter] = {
    CrmProvider.GOOGLE_CONTACTS: GoogleContactsAdapter(),
    CrmProvider.HUBSPOT: HubSpotAdapter(),
}


def adapter_for(provider: CrmProvider) -> CrmAdapter:
    try:
        return _ADAPTERS[provider]
    except KeyError as exc:
        # Unreachable if the CHECK constraint and the enum agree, which
        # test_enum_sync asserts. Raising loudly beats returning None and
        # failing three frames later.
        raise ValueError(f"no adapter registered for {provider!r}") from exc


def supported() -> list[CrmProvider]:
    return sorted(_ADAPTERS)
