# Naming Placeholder Policy

**Status:** Open decision. Blocking for store submission only, not for development.

## The problem

The product has no final name. The name blocks:

- iOS bundle identifier and Android package name
- Two domain purchases (application domain and the separate UGC domain, see ADR-0008)
- App Store Connect and Play Console application records
- Design system package names and the monorepo scope

None of these block writing code today, provided we are disciplined about placeholders.

## The policy

Use `acme` as the placeholder in every code identifier. It is short, obviously fake,
never a substring of a real word we use, and trivially greppable.

| Context | Placeholder | Example |
|---|---|---|
| Monorepo package scope | `@acme` | `@acme/tokens`, `@acme/api-client` |
| Python package | `acme` | `from acme.domain.cards import Card` |
| iOS bundle ID | `com.acme.app` | |
| Android package | `com.acme.app` | |
| Application domain | `example.com` | `app.example.com`, `api.example.com` |
| UGC / public card domain | `example.net` | `example.net/c/<token>` |
| Docker image prefix | `acme/` | `acme/api:sha-abc123` |
| Database name | `acme` | |

**Never invent a second placeholder.** One token, everywhere. The rename must be a single
mechanical pass.

## Rules that keep the rename cheap

1. **No placeholder in a durable external identifier.** Do not create the App Store
   Connect record, do not buy domains, do not register a Stripe product, and do not
   create the Firebase/Apple push certificates until the name is chosen. These are
   difficult or impossible to rename.

2. **No placeholder in user-visible strings.** All user-facing text goes through i18n
   from day one (ADR-0011). The product name is a single interpolated variable,
   `{{productName}}`, defined once per locale file. It is never typed inline.

3. **No placeholder in the database.** Table and column names describe the domain, not the
   product. There is no `acme_users` table.

4. **CI enforces the two places it actually matters.** A lint step fails the build if
   `acme` appears in a **locale file** (meaning a user-visible string was typed inline
   instead of using `{{productName}}` — an i18n defect) or in a **migration** (meaning a
   table or column was named after the product; migrations are append-only, so this is
   genuinely expensive to undo).

   It deliberately does **not** check `infra/` or application code. A Docker Compose
   project name or a deploy path containing `acme` is covered by the mechanical
   grep-and-replace in `runbooks/rename-product.md` and costs nothing. What is genuinely
   expensive lives outside the repository entirely — App Store Connect records, domains,
   Stripe products, push certificates — and the rule there is rule 1 above, which no
   script can enforce.

## Rename procedure

See `runbooks/rename-product.md`. Estimated 20 minutes of mechanical work plus the
external account setup, provided the rules above were followed.

## Candidate names

Recorded for the decision, not endorsed. **None have been trademark-checked.**

| Name | For | Against |
|---|---|---|
| Kadr | Short, distinctive, trademarkable. Reads near Turkish *kadro* (squad/roster). | Requires explanation |
| Cardli | `-li` is a real Turkish suffix meaning "with"; *kartlı* = "with a card". Multilingual by accident. | Descriptive names are hard to trademark; crowded space |
| Nudge | Warm, verb-able, points at the follow-up hook which is the retention mechanic | Existing products, harder trademark path |
| Nomi | Soft, works in EN/DE/TR | Crowded namespace |
| Kartu | Reads as a card word to Turkish speakers, unusual and ownable | Obscure origin |
| Pocketed | Self-explanatory in English | EN-only meaning, weak in DE/TR |
| Passe | Short, evokes a handoff | "passé" means outdated in English. Real liability. |

## Required checks before committing

- USPTO TESS and EUIPO, classes 9 and 42
- Domain availability for **both** the application domain and the UGC domain
- App Store and Play Store name collisions
- Social handle availability
