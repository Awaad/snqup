# Engineering Documentation

This tree is the contract between the teams building this product. It is written so that
five teams can work in parallel without meeting, and so that a new engineer can be
productive on any layer within a day.

## Reading order

If you are new, read in this order:

1. `00-context/product-scope.md` — what we are building and what we are deliberately not building
2. `architecture.md` — the system, the code structure, and the tooling that enforces the rules
3. `00-context/pricing.md` — the frozen commercial model, referenced by the billing layer
4. `00-context/naming.md` — the placeholder policy (the product has no final name yet)
5. `adr/` — every architectural decision, why it was made, and what it costs us
6. `handoff/00-shared-contracts.md` — the rules every team must obey
7. Your own handoff document in `handoff/`

## Layout

```
architecture.md System, code structure, workspace, tooling, CI, linting
00-context/     Product scope, pricing, versions, glossary, naming policy
adr/            Architecture Decision Records (immutable once accepted)
handoff/        One document per team. Scope, interfaces, definition of done.
schema/         Canonical schema, migration policy, review record
contracts/      API contract rules, error codes, codegen pipeline
runbooks/       Operational procedures. Written to be followed at 3am.
```

## Rules for this tree

**ADRs are immutable.** Once an ADR is `Accepted`, it is never edited except to change
its status to `Superseded by ADR-XXXX`. Decisions change by writing a new ADR, not by
rewriting history. This is the whole point of the format.

**Handoffs are living documents.** They change as teams discover reality. Any change that
contradicts an ADR requires a new ADR first.

**Runbooks are tested, not written.** A runbook that has never been executed end to end is
a draft. Each runbook carries a `Last verified` date. If that date is more than 90 days
old, treat the runbook as untrusted and verify before relying on it.

**If a document and the code disagree, the code is wrong or the document is stale.** Fix
one of them in the same pull request that discovered the gap. Do not leave it.

For anything describing the API specifically, assume the **code** is right: it is built,
tested and generating the client types the other apps consume.

**`handoff/04-mobile-expo.md` Part 1 is the product brief.** It explains who this is for,
what the activation metric is, and the three facts that shape every interface decision.
Anyone building a user-facing surface should read it, not just the mobile team.

## Status

| Layer | Document | Status |
|---|---|---|
| Context | `00-context/product-scope.md` | Frozen for v1 |
| Context | `00-context/pricing.md` | Frozen, pending final confirmation |
| Context | `00-context/versions.md` | Verified 2026-09-05 |
| Context | `00-context/naming.md` | **Open decision, has a deadline** |
| Architecture | `architecture.md` | Current |
| Decisions | `adr/` | 0001–0026 accepted |
| Schema | `schema/schema.sql` | v1 canonical, reviewed 2026-09-05 |
| Schema | `schema/review-2026-09-05.md` | 8 defects found and fixed |
| Handoffs | `handoff/` | Each carries its own status line |
| Backend | `apps/api/` | **Built.** 47 endpoints, 304 tests |
| Mobile | `apps/mobile/` | Scaffold. `handoff/04-mobile-expo.md` is the brief |
| Web | `apps/{web,public,marketing,admin}/` | Scaffold. `handoff/05-web-next.md` is the brief |
| Runbooks | `runbooks/` | Written, **none yet verified** |

## Where this stands

**The backend is done.** 47 endpoints, 304 tests, 28 ADRs, 29 tables, four enforced import
contracts and five CI guards. Domain, endpoint and end-to-end reviews are complete.

**Nothing has shipped.** No store submission, no production deploy, and every runbook still
reads `Last verified: never` — backed by code is not the same as executed against real
infrastructure, and for `backup-restore` that distinction is the whole point.

### What blocks launch, in order

1. **The product name.** Blocks App Store Connect, Play Console, both OAuth app
   registrations, and two domain purchases. Everything else here can proceed without it;
   nothing external can.
2. **Mobile and web builds.** Both handoffs are written against the live schema and
   verified against it (`04-mobile-expo.md`, `05-web-next.md`).
3. **Runbook verification.** Execute each one against real infrastructure once it exists.
4. **A pilot event.** The whole design assumes things about how people behave in a hall
   with bad wifi. One real event will correct more assumptions than another month of
   building.

### Two habits worth keeping

**Documented is not implemented.** Twice, something this repo promised turned out not to
exist: `updated_at` never moved, and `Idempotency-Key` was accepted and ignored. Both were
found by looking, not by a test failing. When a document claims a guarantee, check it.

**A green result can mean the check did not run.** Mutation tests reported success five
times because reformatting had silently swallowed the edit, and the first version of
`check-doc-counts.sh` passed a document whose test count had been deliberately corrupted, because it
could not reach the database and skipped silently. Verify that a check can fail before
trusting that it passed.
