#!/usr/bin/env bash
set -euo pipefail

deploy_root="${DEPLOY_ROOT:-/opt/research-copilot}"
env_file="$deploy_root/.env.production"
state_file="$deploy_root/current.env"
previous_file="$deploy_root/previous.env"

if [[ ! -f "$previous_file" || ! -f "$env_file" ]]; then
  printf 'Rollback state or production environment is missing.\n' >&2
  exit 2
fi

previous_image="$(awk -F= '$1=="PRODUCT_IMAGE"{print substr($0,index($0,"=")+1)}' "$previous_file")"
previous_version="$(awk -F= '$1=="PRODUCT_APP_VERSION"{print substr($0,index($0,"=")+1)}' "$previous_file")"
previous_dir="$(awk -F= '$1=="RELEASE_DIR"{print substr($0,index($0,"=")+1)}' "$previous_file")"
if [[ ! "$previous_image" =~ ^[a-z0-9][a-z0-9.-]+/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ || ! -f "$previous_dir/compose.production.yaml" ]]; then
  printf 'Rollback state is invalid.\n' >&2
  exit 3
fi

cp "$state_file" "$deploy_root/rollback-source.env"
export PRODUCT_IMAGE="$previous_image"
export PRODUCT_APP_VERSION="$previous_version"
compose=(docker compose -p "${PRODUCT_COMPOSE_PROJECT:-research-copilot}" --env-file "$env_file" -f "$previous_dir/compose.production.yaml")
"${compose[@]}" pull
"${compose[@]}" up -d --remove-orphans
"${compose[@]}" exec -T api python -c "import json,os,urllib.request; request=urllib.request.Request('http://127.0.0.1:8080/health',headers={'Host':os.environ['PRODUCT_PUBLIC_HOST']}); data=json.load(urllib.request.urlopen(request,timeout=5)); assert data['status']=='ok'"
ln -sfnT "$previous_dir" "$deploy_root/current"
cp "$previous_file" "$state_file"
printf 'Rollback completed: %s\n' "$previous_version"
