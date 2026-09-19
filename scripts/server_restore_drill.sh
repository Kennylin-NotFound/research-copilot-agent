#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  printf 'Usage: server_restore_drill.sh <backup-directory>\n' >&2
  exit 2
fi

backup_dir="$(cd "$1" && pwd)"
deploy_root="${DEPLOY_ROOT:-/opt/research-copilot}"
current_dir="$deploy_root/current"
env_file="$deploy_root/.env.production"
state_file="$deploy_root/current.env"

for required in copilot.dump product_storage.tgz storage-files.sha256 SHA256SUMS; do
  if [[ ! -f "$backup_dir/$required" ]]; then
    printf 'Backup is incomplete: %s\n' "$required" >&2
    exit 3
  fi
done
(
  cd "$backup_dir"
  sha256sum --check SHA256SUMS
)

image_ref="$(awk -F= '$1=="PRODUCT_IMAGE"{print substr($0,index($0,"=")+1)}' "$state_file")"
app_version="$(awk -F= '$1=="PRODUCT_APP_VERSION"{print substr($0,index($0,"=")+1)}' "$state_file")"
if [[ ! "$image_ref" =~ ^[a-z0-9][a-z0-9.-]+/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]]; then
  printf 'Current immutable image state is invalid.\n' >&2
  exit 3
fi

suffix="$(date -u +%H%M%S)-$$"
project="research-copilot-restore-$suffix"
export PRODUCT_COMPOSE_PROJECT="$project"
export PRODUCT_IMAGE="$image_ref"
export PRODUCT_APP_VERSION="$app_version-restore"
export PRODUCT_HTTP_PORT=28081
export PRODUCT_PUBLIC_HTTP_PORT=28080
export PRODUCT_PUBLIC_HTTPS_PORT=28443
export PRODUCT_PUBLIC_HOST=localhost
export PRODUCT_ALLOWED_HOSTS=localhost
compose=(docker compose --env-file "$env_file" -f "$current_dir/compose.production.yaml")

cleanup() {
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${compose[@]}" up -d db
for _ in $(seq 1 45); do
  db_id="$("${compose[@]}" ps -q db)"
  if [[ -n "$db_id" ]] && [[ "$(docker inspect "$db_id" --format '{{.State.Health.Status}}')" == "healthy" ]]; then
    break
  fi
  sleep 2
done
db_id="$("${compose[@]}" ps -q db)"
if [[ -z "$db_id" ]] || [[ "$(docker inspect "$db_id" --format '{{.State.Health.Status}}')" != "healthy" ]]; then
  printf 'Restore database did not become healthy.\n' >&2
  exit 4
fi

docker cp "$backup_dir/copilot.dump" "$db_id:/tmp/copilot.dump"
docker exec "$db_id" pg_restore -U copilot -d copilot --clean --if-exists --no-owner /tmp/copilot.dump

"${compose[@]}" create api >/dev/null
api_id="$("${compose[@]}" ps -aq api)"
storage_volume="$(docker inspect "$api_id" --format '{{range .Mounts}}{{if eq .Destination "/var/lib/research-copilot"}}{{.Name}}{{end}}{{end}}')"
if [[ -z "$storage_volume" ]]; then
  printf 'Restore storage volume was not created.\n' >&2
  exit 5
fi
docker run --rm -v "$storage_volume:/data" -v "$backup_dir:/backup:ro" alpine:3.22 \
  tar -xzf /backup/product_storage.tgz -C /data
docker run --rm -v "$storage_volume:/data:ro" alpine:3.22 \
  sh -c 'cd /data && find . -type f -print | LC_ALL=C sort | while IFS= read -r file; do sha256sum "$file"; done' \
  > "$backup_dir/restored-storage-files.sha256"
diff -u "$backup_dir/storage-files.sha256" "$backup_dir/restored-storage-files.sha256"

"${compose[@]}" up -d api worker
for _ in $(seq 1 45); do
  if "${compose[@]}" exec -T api python -c "import json,os,urllib.request; request=urllib.request.Request('http://127.0.0.1:8080/health',headers={'Host':os.environ['PRODUCT_PUBLIC_HOST']}); assert json.load(urllib.request.urlopen(request,timeout=3))['status']=='ok'" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
"${compose[@]}" exec -T api python -c "import json,os,urllib.request; request=urllib.request.Request('http://127.0.0.1:8080/health',headers={'Host':os.environ['PRODUCT_PUBLIC_HOST']}); print(json.load(urllib.request.urlopen(request,timeout=3)))"
docker exec "$db_id" psql -U copilot -d copilot -Atc \
  "SELECT json_build_object('migrations',(SELECT count(*) FROM schema_migrations),'users',(SELECT count(*) FROM users),'projects',(SELECT count(*) FROM projects),'files',(SELECT count(*) FROM files),'file_versions',(SELECT count(*) FROM file_versions),'runs',(SELECT count(*) FROM runs),'spans',(SELECT count(*) FROM trace_spans),'orphan_file_versions',(SELECT count(*) FROM file_versions v LEFT JOIN files f ON f.id=v.file_id WHERE f.id IS NULL));"
printf 'Restore drill passed for %s; temporary project %s will be removed.\n' "$backup_dir" "$project"
