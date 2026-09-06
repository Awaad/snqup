"""Notifications domain.

An eighth domain, not in the original ADR-0025 list. Device tokens and the
notification inbox are used by connections (follow-up reminders, reciprocity
nudges), events (announcements, post-event digest) and identity (account
mail). Putting them inside any one of those would make the other two import
across a boundary for something none of them owns.

Other domains may import `service` from here and nothing else.
"""
