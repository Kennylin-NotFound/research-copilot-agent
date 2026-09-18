#!/usr/bin/env bash
set -euo pipefail

os_id="unknown"
os_like=""
os_version="unknown"
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  os_id="$(. /etc/os-release; printf '%s' "${ID:-unknown}")"
  # shellcheck disable=SC1091
  os_like="$(. /etc/os-release; printf '%s' "${ID_LIKE:-}")"
  # shellcheck disable=SC1091
  os_version="$(. /etc/os-release; printf '%s' "${VERSION_ID:-unknown}")"
fi

printf 'os_id=%s\n' "$os_id"
printf 'os_like=%s\n' "$os_like"
printf 'os_version=%s\n' "$os_version"
printf 'architecture=%s\n' "$(uname -m)"
printf 'kernel=%s\n' "$(uname -sr)"
printf 'cpu_count=%s\n' "$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf unknown)"
if command -v free >/dev/null 2>&1; then
  printf 'memory_mb=%s\n' "$(free -m | awk '/^Mem:/{print $2}')"
fi
printf 'root_free_kb=%s\n' "$(df -Pk / | awk 'NR==2{print $4}')"

if command -v docker >/dev/null 2>&1; then
  printf 'docker_version=%s\n' "$(docker version --format '{{.Server.Version}}' 2>/dev/null || printf unavailable)"
  printf 'docker_arch=%s\n' "$(docker version --format '{{.Server.Arch}}' 2>/dev/null || printf unavailable)"
  printf 'compose_version=%s\n' "$(docker compose version --short 2>/dev/null || printf unavailable)"
else
  printf 'docker_version=missing\n'
  printf 'docker_arch=missing\n'
  printf 'compose_version=missing\n'
fi

for command_name in curl tar sha256sum; do
  if command -v "$command_name" >/dev/null 2>&1; then
    printf '%s=present\n' "$command_name"
  else
    printf '%s=missing\n' "$command_name"
  fi
done

if sudo -n true >/dev/null 2>&1; then
  printf 'passwordless_sudo=yes\n'
else
  printf 'passwordless_sudo=no\n'
fi

if command -v ss >/dev/null 2>&1; then
  for port in 80 443 18081; do
    if ss -H -ltn "sport = :$port" 2>/dev/null | grep -q .; then
      printf 'tcp_%s=in_use\n' "$port"
    else
      printf 'tcp_%s=free\n' "$port"
    fi
  done
fi
