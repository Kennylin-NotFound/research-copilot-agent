#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  printf 'Usage: server_smoke.sh <https-base-url> <expected-version>\n' >&2
  exit 2
fi
base_url="${1%/}"
expected_version="$2"
if [[ ! "$base_url" =~ ^https:// ]]; then
  printf 'Production smoke test requires HTTPS.\n' >&2
  exit 2
fi

health="$(curl --fail --silent --show-error --retry 12 --retry-delay 5 --retry-all-errors "$base_url/health")"
python3 - "$expected_version" "$health" <<'PY'
import json, sys
expected, raw = sys.argv[1], sys.argv[2]
data = json.loads(raw)
assert data == {"status": "ok", "version": expected, "environment": "production", "mode": "live"}, data
PY

headers="$(curl --fail --silent --show-error --dump-header - --output /dev/null "$base_url/")"
grep -qi '^strict-transport-security:' <<<"$headers"
grep -qi '^x-content-type-options: nosniff' <<<"$headers"
printf 'Production smoke test passed for %s\n' "$base_url"
