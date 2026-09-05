#!/usr/bin/env bash
# The product has no final name yet (00-context/naming.md). `acme` is fine in
# code identifiers, compose files and deploy scripts: all of those are covered
# by the mechanical grep-and-replace in runbooks/rename-product.md.
#
# This check targets the two places where the placeholder means an actual bug:
#
#   locale files  -> a user-visible string was typed inline instead of using
#                    {{productName}}. That is an i18n defect (ADR-0011).
#   migrations/versions -> a table or column named after the product. Migrations
#                    are append-only, so this is genuinely expensive to undo.
#                    Only versions/ is checked: env.py legitimately imports the
#                    `acme` package, which is a code identifier.
#
# What is truly expensive to rename lives OUTSIDE this repo entirely: App Store
# Connect records, domains, Stripe products, push certificates. The rule there
# is "do not create them until the name is final", which no script can enforce.
set -euo pipefail

fail=0

for dir in apps/*/locales apps/*/src/locales apps/*/messages; do
  [ -d "$dir" ] || continue
  if grep -rniq --binary-files=without-match "acme" "$dir" 2>/dev/null; then
    echo "error: placeholder 'acme' in locale files under $dir"
    grep -rni --binary-files=without-match "acme" "$dir" | head -5
    echo "  -> use the {{productName}} interpolation instead"
    fail=1
  fi
done

# Only migrations/versions/: that is where schema DDL lives. The Alembic
# harness (env.py) legitimately imports from the `acme` package, which is a
# code identifier and therefore fine.
if [ -d apps/api/migrations/versions ]; then
  found=$(grep -rni --binary-files=without-match "acme" apps/api/migrations/versions 2>/dev/null || true)
  if [ -n "$found" ]; then
    echo "error: placeholder 'acme' in a migration"
    echo "$found" | head -5
    echo "  -> table and column names describe the domain, not the product"
    fail=1
  fi
fi

if [ "$fail" -ne 0 ]; then
  echo
  echo "See docs/00-context/naming.md"
  exit 1
fi
