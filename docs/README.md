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
| Handoffs | `handoff/` | Ready to start |
| Runbooks | `runbooks/` | Written, **none yet verified** |
