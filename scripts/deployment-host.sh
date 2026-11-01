#!/usr/bin/env bash
# Host adapter for the Python state machine. Requires only Bash and Docker.
set -euo pipefail
cd "$(dirname "$0")/.."
if command -v python3 >/dev/null && python3 -c 'import hashlib,sys; assert sys.version_info >= (3,11)' 2>/dev/null; then
    exec python3 scripts/deployment.py "$@"
fi
# Container transport supports the local Unix daemon only. Preserve host paths:
# Compose resolves bind mounts on the daemon, not in the interpreter container.
if [[ -n "${DOCKER_CONTEXT:-}" ]]; then
    endpoint="$(docker context inspect "$DOCKER_CONTEXT" --format '{{.Endpoints.docker.Host}}')"
else
    endpoint="${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}"
fi
case "$endpoint" in unix:///*) socket="${endpoint#unix://}" ;; *)
    echo 'Docker-only helper requires a local Unix socket; remote daemons need an operator procedure.' >&2; exit 1 ;;
esac
root="$(pwd -P)"
backup="${TRELLUM_BACKUP_DIR:-$root/upgrade-backups}"
case "$backup" in /*) ;; *) backup="$root/$backup" ;; esac
mkdir -p "$backup"
backup="$(cd "$backup" && pwd -P)"
# A unique sentinel refuses Docker Desktop/remote filesystem path mismatches.
sentinel="$(mktemp "$root/.deployment-host-XXXXXXXX")"
trap 'rm -f "$sentinel"' EXIT
printf '%s\n' "${sentinel##*/}" > "$sentinel"
image=trellum-deployment-helper:1
timeout --kill-after=5s 300s docker build --quiet --tag "$image" --file scripts/deployment.Dockerfile scripts >/dev/null
mounts=(--mount "type=bind,src=$root,dst=$root" --mount "type=bind,src=$socket,dst=$socket")
if [[ "$backup" != "$root" ]]; then mounts+=(--mount "type=bind,src=$backup,dst=$backup"); fi
# Preserve registry authentication without changing the host's Docker config.
config="${DOCKER_CONFIG:-$HOME/.docker}"
if [[ -d "$config" ]]; then mounts+=(--mount "type=bind,src=$config,dst=$config,readonly"); fi
environment=()
while IFS= read -r name; do environment+=(--env "$name"); done < <(compgen -e)
if [[ "${1:-}" == rollback && -f "${2:-}" ]]; then
    dump_parent="$(cd "$(dirname "$2")" && pwd -P)"
    if [[ "$dump_parent" != "$root" && "$dump_parent" != "$backup" ]]; then
        mounts+=(--mount "type=bind,src=$dump_parent,dst=$dump_parent")
    fi
fi
helper_user="$(id -u):$(id -g)"
security="$(timeout --kill-after=5s 60s docker info --format '{{json .SecurityOptions}}')"
# Root in a rootless daemon maps back to the invoking host user. For a
# rootful daemon, disable any daemon-wide remapping for this host adapter.
case "$security" in *name=rootless*) helper_user=0:0 ;; esac
groups=()
for group in $(id -G); do groups+=(--group-add "$group"); done
groups+=(--group-add "$(stat -c %g "$socket")")
transport=(--rm -i --userns=host --user "$helper_user" "${groups[@]}" --network none --memory 256m --cpus 1 --workdir "$root" "${mounts[@]}" "${environment[@]}" --env "DOCKER_HOST=$endpoint" --env DOCKER_CONTEXT= --env "DOCKER_CONFIG=$config")
if [[ -t 0 && -t 1 ]]; then transport+=(-t); fi
docker run "${transport[@]}" "$image" sh -ec '
    test "$(cat "$1")" = "${1##*/}" || exit 1
    shift
    exec python scripts/deployment.py "$@"
' sh "$sentinel" "$@"
