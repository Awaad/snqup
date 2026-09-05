#!/usr/bin/env bash
# ADR-0009: prices live in App Store Connect and Stripe, never in our code.
# StoreKit returns localised prices; Stripe Price objects are read at render
# time. The database stores plan definitions and entitlements only.
set -euo pipefail

matches=$(grep -rnE '[$€£][0-9]+\.[0-9]{2}' \
  --include="*.ts" --include="*.tsx" --include="*.py" \
  --exclude-dir=node_modules --exclude-dir=.next --exclude-dir=dist \
  apps packages 2>/dev/null || true)

if [ -n "$matches" ]; then
  echo "error: hardcoded price string found"
  echo "$matches"
  echo
  echo "Prices come from StoreKit or Stripe at render time (ADR-0009)."
  exit 1
fi
