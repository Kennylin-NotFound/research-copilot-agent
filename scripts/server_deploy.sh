#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ "$#" -ne 2 ]]; then
  printf 'Usage: server_deploy.sh <image@sha256:digest> <version>\n' >&2
  exit 2
fi

image_ref="$1"
app_version="$2"
deploy_root="${DEPLOY_ROOT:-/opt/research-copilot}"
release_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$deploy_root/.env.production"
state_file="$deploy_root/current.env"
previous_file="$deploy_root/previous.env"

if [[ ! "$image_ref" =~ ^[a-z0-9][a-z0-9.-]+/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]]; then
  printf 'Deployment requires an immutable registry digest reference.\n' >&2
  exit 2
fi
if [[ ! "$app_version" =~ ^v?[0-9A-Za-z._-]+$ ]]; then
  printf 'Invalid application version.\n' >&2
  exit 2
fi
if [[ ! -f "$env_file" ]]; then
  printf 'Missing server-only configuration: %s\n' "$env_file" >&2
  exit 3
fi
if [[ ! -f "$release_dir/compose.production.yaml" || ! -f "$release_dir/deploy/Caddyfile" ]]; then
  printf 'Incomplete deployment bundle in %s\n' "$release_dir" >&2
  exit 3
fi

if [[ -L "$deploy_root/current" ]]; then
  BACKUP_QUIESCE=1 "$release_dir/scripts/server_backup.sh"
fi

if [[ -f "$state_file" ]]; then
  cp "$state_file" "$previous_file"
fi

export PRODUCT_IMAGE="$image_ref"
export PRODUCT_APP_VERSION="$app_version"
compose=(docker compose --env-file "$env_file" -f "$release_dir/compose.production.yaml")
"${compose[@]}" config --quiet
"${compose[@]}" pull
"${compose[@]}" up -d --remove-orphans

healthy=0
for attempt in $(seq 1 60); do
  if "${compose[@]}" exec -T api python -c "import json,os,urllib.request; request=urllib.request.Request('http://127.0.0.1:8080/health',headers={'Host':os.environ['PRODUCT_PUBLIC_HOST']}); data=json.load(urllib.request.urlopen(request,timeout=3)); assert data['status']=='ok' and data['environment']=='production' and data['mode']=='live'" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  if (( attempt % 10 == 0 )); then
    printf 'Waiting for production health check (%s/60)...\n' "$attempt"
  fi
  sleep 2
done

if [[ "$healthy" != "1" ]]; then
  "${compose[@]}" logs --tail 120 api worker proxy >&2 || true
  if [[ -f "$previous_file" ]]; then
    previous_image="$(awk -F= '$1=="PRODUCT_IMAGE"{print substr($0,index($0,"=")+1)}' "$previous_file")"
    previous_version="$(awk -F= '$1=="PRODUCT_APP_VERSION"{print substr($0,index($0,"=")+1)}' "$previous_file")"
    previous_dir="$(awk -F= '$1=="RELEASE_DIR"{print substr($0,index($0,"=")+1)}' "$previous_file")"
    if [[ "$previous_image" =~ ^[a-z0-9][a-z0-9.-]+/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ && -f "$previous_dir/compose.production.yaml" ]]; then
      export PRODUCT_IMAGE="$previous_image"
      export PRODUCT_APP_VERSION="$previous_version"
      docker compose --env-file "$env_file" -f "$previous_dir/compose.production.yaml" up -d --remove-orphans
      ln -sfn "$previous_dir" "$deploy_root/current"
    fi
  fi
  printf 'Deployment health check failed.\n' >&2
  exit 5
fi

ln -sfn "$release_dir" "$deploy_root/current"
{
  printf 'PRODUCT_IMAGE=%s\n' "$image_ref"
  printf 'PRODUCT_APP_VERSION=%s\n' "$app_version"
  printf 'RELEASE_DIR=%s\n' "$release_dir"
  printf 'DEPLOYED_AT=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$state_file"

"${compose[@]}" ps
printf 'Deployment completed: %s (%s)\n' "$app_version" "$image_ref"
