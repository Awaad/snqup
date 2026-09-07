"""CRM integration domain.

Two providers at launch, chosen because they are SHAPED DIFFERENTLY:

  google_contacts  a personal address book. No companies, no deals, no custom
                   fields. Contacts have names, emails, phones and free-form
                   notes.
  hubspot          contacts, companies, deals, and arbitrary custom properties
                   an admin defines.

That divergence is the point. An adapter interface written against two similar
APIs looks clean and then breaks on the third; written against these two, it
has already met the cases that matter - no custom fields at all, versus
entirely user-defined ones.

Salesforce is deliberately absent. It is a third implementation of a proven
interface once these two exist, and its buyer barely overlaps with real estate
agents or small sales teams.

Other domains may import `service` from here and nothing else.
"""
