#!/usr/bin/env bash
set -euo pipefail
umask 077

deploy_root="${DEPLOY_ROOT:-/opt/research-copilot}"
current_dir="$deploy_root/current"
env_file="$deploy_root/.env.production"
backup_root="$deploy_root/backups"

if [[ ! -L "$current_dir" || ! -f "$current_dir/compose.production.yaml" ]]; then
  printf 'No active release; backup skipped.\n'
  exit 0
fi
if [[ ! -f "$env_file" ]]; then
  printf 'Missing %s\n' "$env_file" >&2
  exit 2
fi

compose=(docker compose -p "${PRODUCT_COMPOSE_PROJECT:-research-copilot}" --env-file "$env_file" -f "$current_dir/compose.production.yaml")
db_id="$("${compose[@]}" ps -q db)"
api_id="$("${compose[@]}" ps -q api)"
if [[ -z "$db_id" || -z "$api_id" ]]; then
  printf 'Active database/API containers were not found; backup aborted.\n' >&2
  exit 3
fi

active_runs="$(docker exec "$db_id" psql -U copilot -d copilot -Atc "SELECT count(*) FROM runs WHERE status IN ('queued','running','retry_wait','cancelling');")"
if [[ "$active_runs" != "0" ]]; then
  printf 'Backup refused: %s active run(s).\n' "$active_runs" >&2
  exit 4
fi

quiesced=0
resume_services() {
  if [[ "$quiesced" == "1" ]]; then
    "${compose[@]}" start api worker >/dev/null 2>&1 || true
  fi
}
trap resume_services EXIT
if [[ "${BACKUP_QUIESCE:-0}" == "1" ]]; then
  "${compose[@]}" stop api worker
  quiesced=1
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="$backup_root/$timestamp"
mkdir -p "$backup_dir"

docker exec "$db_id" pg_dump -U copilot -d copilot -Fc > "$backup_dir/copilot.dump"
storage_volume="$(docker inspect "$api_id" --format '{{range .Mounts}}{{if eq .Destination "/var/lib/research-copilot"}}{{.Name}}{{end}}{{end}}')"
if [[ -z "$storage_volume" ]]; then
  printf 'Product storage volume was not found.\n' >&2
  exit 5
fi
docker run --rm -v "$storage_volume:/data:ro" -v "$backup_dir:/backup" alpine:3.22 \
  tar -czf /backup/product_storage.tgz -C /data .
docker run --rm -v "$storage_volume:/data:ro" alpine:3.22 \
  sh -c 'cd /data && find . -type f -print | LC_ALL=C sort | while IFS= read -r file; do sha256sum "$file"; done' \
  > "$backup_dir/storage-files.sha256"

(
  cd "$backup_dir"
  sha256sum copilot.dump product_storage.tgz storage-files.sha256 > SHA256SUMS
)
printf 'created_at=%s\n' "$timestamp" > "$backup_dir/manifest.env"
if [[ -f "$deploy_root/current.env" ]]; then
  grep -E '^(PRODUCT_IMAGE|PRODUCT_APP_VERSION|RELEASE_DIR)=' "$deploy_root/current.env" >> "$backup_dir/manifest.env"
fi
printf 'Backup created: %s\n' "$backup_dir"
