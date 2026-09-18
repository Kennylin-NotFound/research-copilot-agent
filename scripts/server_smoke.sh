#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -lt 2 || "$#" -gt 3 ]]; then
  printf 'Usage: server_smoke.sh <https-base-url> <expected-version> [ca-certificate]\n' >&2
  exit 2
fi
base_url="${1%/}"
expected_version="$2"
ca_certificate="${3:-}"
if [[ ! "$base_url" =~ ^https:// ]]; then
  printf 'Production smoke test requires HTTPS.\n' >&2
  exit 2
fi
curl_tls=()
if [[ -n "$ca_certificate" ]]; then
  if [[ ! -s "$ca_certificate" ]]; then
    printf 'CA certificate does not exist or is empty: %s\n' "$ca_certificate" >&2
    exit 2
  fi
  curl_tls=(--cacert "$ca_certificate")
fi

health="$(curl "${curl_tls[@]}" --fail --silent --show-error --retry 12 --retry-delay 5 --retry-all-errors "$base_url/health")"
python3 - "$expected_version" "$health" <<'PY'
import json, sys
expected, raw = sys.argv[1], sys.argv[2]
data = json.loads(raw)
assert data == {"status": "ok", "version": expected, "environment": "production", "mode": "live"}, data
PY

headers="$(curl "${curl_tls[@]}" --fail --silent --show-error --dump-header - --output /dev/null "$base_url/")"
grep -qi '^strict-transport-security:' <<<"$headers"
grep -qi '^x-content-type-options: nosniff' <<<"$headers"
printf 'Production smoke test passed for %s\n' "$base_url"
