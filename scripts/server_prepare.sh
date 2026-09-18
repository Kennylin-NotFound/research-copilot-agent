#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  printf 'Run this once with sudo: sudo bash scripts/server_prepare.sh <deploy-user>\n' >&2
  exit 2
fi

deploy_user="${1:-${SUDO_USER:-}}"
if [[ -z "$deploy_user" ]] || ! id "$deploy_user" >/dev/null 2>&1; then
  printf 'A valid existing deploy user is required.\n' >&2
  exit 2
fi
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  printf 'Docker Engine and the Compose v2 plugin must be installed first.\n' >&2
  exit 3
fi

install -d -m 0750 -o "$deploy_user" -g "$deploy_user" /opt/research-copilot
install -d -m 0750 -o "$deploy_user" -g "$deploy_user" /opt/research-copilot/releases
install -d -m 0700 -o "$deploy_user" -g "$deploy_user" /opt/research-copilot/backups
usermod -aG docker "$deploy_user"

printf 'Prepared /opt/research-copilot for %s. Re-login so Docker group membership takes effect.\n' "$deploy_user"
