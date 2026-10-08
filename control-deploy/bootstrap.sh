#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ $(uname -s) == Linux && $(id -u) == 0 ]] || { echo 'Requires Linux root, Docker and Python >=3.10.' >&2;exit 1; }
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
home=/opt/ctl-platform image=ctl-control:local
args=()
while (($#));do
  (($#>=2)) || { echo 'Missing parameter value' >&2;exit 1; }
  case $1 in
    --home) home=$2;; --image) image=$2;;
    --origin|--registry-host|--api-port|--registry-port|--database-url-file) args+=("$1" "$2");;
    *) echo 'Unknown bootstrap parameter' >&2;exit 1;;
  esac
  shift 2
done
docker compose version >/dev/null
[[ $image != ctl-control:local ]] || docker build -f "$source_dir/control-deploy/Dockerfile" -t "$image" "$source_dir"
profile=$(python3 "$source_dir/control-deploy/bootstrap_config.py" --home "$home" --image "$image" "${args[@]}")
# init uses O_EXCL; existing keys/owner identity are never replaced.
docker run --rm --user 0:0 -v "$home/keys:/run/ctl-keys" "$image" init
mkdir -p -- "$home/artifacts" "$home/registry"
chown 10001:10001 -- "$home/keys" "$home/keys/database.url" "$home/keys/encryption.key" "$home/keys/signing.key" "$home/keys/signing.crt" "$home/keys/owner.token" "$home/artifacts"
chmod 700 -- "$home/keys" "$home/artifacts"
compose=(docker compose --project-name ctl-platform --env-file "$home/compose.env" -f "$source_dir/control-deploy/compose.yaml")
if [[ $profile == database ]];then compose+=(--profile database);fi
"${compose[@]}" up -d --wait --wait-timeout 120
printf 'ctl platform started. Configure HTTPS proxy for API and Registry. Owner credential: %s/keys/owner.token\n' "$home"
