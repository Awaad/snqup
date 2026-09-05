#!/usr/bin/env bash
# ADR-0009: Apple requires IAP for digital subscriptions consumed in the app.
# Organizer and organization plans are business software sold on the web to a
# different buyer. The mobile app must contain no organizer upsell, no link,
# and no mention of organizer pricing.
#
# This will be tempting when building the events screen. It is an App Store
# rejection risk, not a design preference.
set -euo pipefail

[ -d apps/mobile ] || exit 0

patterns='upgrade.*event|organizer.*plan|event.*pricing|pricing.*organizer|/pricing|billing/organizer'

matches=$(grep -rniE "$patterns" apps/mobile/src 2>/dev/null || true)

if [ -n "$matches" ]; then
  echo "error: possible organizer upsell in the mobile app"
  echo "$matches"
  echo
  echo "Organizer plans are web-only (ADR-0009). If this is a false positive,"
  echo "adjust the pattern in this script and say why in the commit message."
  exit 1
fi
