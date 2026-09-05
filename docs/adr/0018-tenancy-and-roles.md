# ADR-0018: Two tenant types: organizations and events

**Status:** Accepted
**Date:** 2026-09-04

## Context

The product has team/company cards with enforced branding, and event organizers with
staff. These look like the same "team" concept but are not.

An organization is durable and owns brand, seats and member cards. An event is temporary,
may be run by an organization or by a solo user, and has its own staff — including
temporary staff hired for the day, who must see exactly one event and nothing else.

Collapsing them into one role model means either giving a door scanner access to company
branding, or giving a company admin no way to delegate a single event.

## Decision

**Two distinct tenant types with separate membership tables.**

```
organizations
organization_members   role: owner | admin | member
events                 organization_id NULLABLE (solo organizers exist)
event_staff            role: owner | manager | scanner | viewer
```

Powers are deliberately separate. "Can edit the organization's brand" and "can view this
event's dashboard" are different, and neither implies the other.

**Seats are enforced at invite time, not at request time.** Checking seat count on every
request means a downgrade silently locks existing members out, which produces support
tickets and looks like a bug. Enforce on the write path; warn on the read path when over
limit.

**Every tenant-scoped query goes through a repository layer that requires an explicit
tenant identifier as a parameter.** Not RLS (ADR-0005), but a single chokepoint where
omitting the tenant filter is structurally impossible rather than merely discouraged.

Cross-tenant access is only possible through explicit, audited elevation, and every such
access writes to `audit_log`.

## Consequences

**Good.** A temporary event scanner cannot see company data. A company admin can delegate
one event without granting organization access.

**Good.** Solo organizers work without a shell organization record.

**Good.** The repository chokepoint means tenant isolation is testable with a single suite
rather than audited query by query.

**Bad.** Two role models to explain, two invitation flows, two permission-check helpers.

**Bad.** A user can hold roles in both, and the UI must make it clear which hat they are
wearing.

**Bad.** Nullable `organization_id` on events means every organization-scoped query needs
a null branch.

**Bad.** Enforcing seats only at invite time allows an over-limit state to persist after a
downgrade. Deliberate, and it needs a visible warning surface plus a documented support
procedure.

## Alternatives considered

**One `teams` table with a type discriminator.** Rejected: the role sets have nothing in
common, so every permission check would branch on type anyway.

**Postgres RLS for tenant isolation.** Rejected in ADR-0005. Authorization belongs in code
that is reviewable and testable.
