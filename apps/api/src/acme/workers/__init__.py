"""Background jobs (ARQ).

THE RULE, from ADR-0007: every job must be reconstructible from Postgres.
Valkey holds scheduling only. A total Valkey loss must degrade the system - jobs
re-enqueue from their database source - never corrupt it.

A job that carries state only in its queue payload is a bug, and code review is
what catches it.
"""
