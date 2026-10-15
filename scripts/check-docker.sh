#!/usr/bin/env bash
# Read-only check before installing. Match the mode to the Compose data mount.
set -euo pipefail

case "${1:-}" in
  "") minimum=45; engine=26; storage="named-volume /data" ;;
  --bind-data) minimum=44; engine=25; storage="bind-backed /data" ;;
  *) echo "Usage: bash scripts/check-docker.sh [--bind-data]" >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { echo "Too many arguments" >&2; exit 2; }

api=$(docker version --format '{{.Server.APIVersion}}') || {
  echo "Cannot reach the Docker Engine. Check the Docker service and socket permissions." >&2
  exit 1
}
if [[ ! "$api" =~ ^([0-9]+)\.([0-9]+)$ ]]; then
  echo "Cannot determine Docker Engine API version: $api" >&2
  exit 1
fi
major=$((10#${BASH_REMATCH[1]}))
minor=$((10#${BASH_REMATCH[2]}))
if (( major < 1 || (major == 1 && minor < minimum) )); then
  echo "Docker API $api is too old for $storage; need Engine $engine+ (API 1.$minimum+)." >&2
  if [ "$minimum" = 45 ]; then
    echo "Docker 25 can use docker-compose.bind-data.yml; follow the host-folder storage guide before switching." >&2
  fi
  exit 1
fi
echo "Docker API $api supports $storage."
