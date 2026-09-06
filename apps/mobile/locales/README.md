# Locales

`en`, `de`, `tr` ship translated. **`ar` ships with English fallback content**, not
machine translation.

Arabic exists specifically so RTL support is exercised in reality rather than in theory.
Shipping it half-empty is deliberate: the point is the layout, not the copy.

## Rules (ADR-0011)

- **No user-facing string is ever typed inline.** Lint-enforced.
- The product name is `{{productName}}`, defined once per file.
- Dates, times, numbers and currency go through `Intl`. Never manual formatting.
- Pluralization uses ICU rules. **Arabic has six plural forms**; German differs from
  English. A naive `count === 1` check is wrong in three of our four locales.
- **User-generated content is never translated.** A card written in Turkish stays Turkish
  regardless of viewer locale.
- Every error code in `contracts/error-codes.md` must have a string in **all four** files.
  CI fails if a code exists in the API but not here.
