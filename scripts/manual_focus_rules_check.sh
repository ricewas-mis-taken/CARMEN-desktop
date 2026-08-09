#!/usr/bin/env bash
# Manual, curl-based smoke test for GET/POST /api/focus/rules -- verifies
# the cross-browser whitelist sync endpoints standalone, independent of any
# browser extension. Not part of the app and not picked up by pytest.
#
# Requires the desktop app (or api_server.run_server()) already running on
# 127.0.0.1:5847 -- WARNING: POSTing here overwrites the real, live
# domainWhitelist config (private/config.json), same as the extension
# popup would. Don't run this against a real running instance unless you're
# fine with that, or point API_BASE at a scratch instance instead.
#
# Run from anywhere:
#   bash scripts/manual_focus_rules_check.sh

set -euo pipefail

API_BASE="${API_BASE:-http://127.0.0.1:5847}"

echo "--- GET /api/focus/rules (current ruleset) ---"
curl -s "$API_BASE/api/focus/rules" | tee /tmp/carmen_rules_before.json
echo

echo "--- POST /api/focus/rules (extension origin, should succeed + bump version) ---"
curl -s -X POST "$API_BASE/api/focus/rules" \
  -H "Content-Type: application/json" \
  -H "Origin: chrome-extension://example0000000000000000000000000" \
  -d '{"domainWhitelist": ["docs.google.com", "gmail.com", "notion.so"]}' \
  -i | tee /tmp/carmen_rules_after.txt
echo

echo "--- GET /api/focus/rules (confirms version/updatedAt advanced) ---"
curl -s "$API_BASE/api/focus/rules"
echo

echo "--- CORS preflight check: extension origin should be echoed back ---"
curl -s -i -X OPTIONS "$API_BASE/api/focus/rules" \
  -H "Origin: moz-extension://11111111-2222-3333-4444-555555555555" \
  -H "Access-Control-Request-Method: POST" | grep -i "access-control-allow-origin" || echo "NO CORS HEADER (unexpected)"

echo "--- CORS check: non-extension origin should NOT get an allow-origin header ---"
curl -s -i "$API_BASE/api/focus/rules" -H "Origin: https://evil.example.com" \
  | grep -i "access-control-allow-origin" && echo "UNEXPECTED: origin was allowed" || echo "OK: origin not allowed"

echo "--- Validation: rejects a non-list body ---"
curl -s -X POST "$API_BASE/api/focus/rules" \
  -H "Content-Type: application/json" \
  -d '{"domainWhitelist": "not-a-list"}' -w "\nHTTP %{http_code}\n"
