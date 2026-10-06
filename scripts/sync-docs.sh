#!/usr/bin/env bash
# Copy product documentation from stable releases into the static site.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
DOCS_ROOT="$ROOT/website/content/docs"
SOURCE_PATH="docs/customer"
KEEP=5
ALL=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --all) ALL=1; shift ;;
    --keep) [ "$#" -ge 2 ] || { echo "--keep needs a number" >&2; exit 2; }; KEEP="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ "$KEEP" =~ ^[1-9][0-9]*$ ]] || { echo "--keep must be a positive integer" >&2; exit 2; }
[ "$DOCS_ROOT" = "$ROOT/website/content/docs" ] || { echo "unsafe documentation output" >&2; exit 1; }
[ ! -L "$DOCS_ROOT" ] || { echo "documentation output must not be a symlink" >&2; exit 1; }
mkdir -p "$DOCS_ROOT"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
if [ -n "${TRELLUM_REPO:-}" ]; then
  SOURCE="$(cd "$TRELLUM_REPO" && pwd -P)"
  git -C "$SOURCE" rev-parse --git-dir >/dev/null
else
  SOURCE="$ROOT"
  git -C "$SOURCE" rev-parse --git-dir >/dev/null
fi

mapfile -t TAGS < <(
  git -C "$SOURCE" tag --list --sort=-v:refname |
    grep -E '^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' || true
)

RELEASES=()
for tag in "${TAGS[@]}"; do
  git -C "$SOURCE" cat-file -e "$tag:$SOURCE_PATH" 2>/dev/null && RELEASES+=("$tag")
done
if [ "$ALL" -eq 0 ] && [ "${#RELEASES[@]}" -gt "$KEEP" ]; then
  RELEASES=("${RELEASES[@]:0:$KEEP}")
fi

copy_docs() {
  local ref="$1" target="$2" kind="$3" stage
  stage="$TMP/$target"
  rm -rf "$stage"
  mkdir -p "$stage"
  git -C "$SOURCE" archive "$ref" "$SOURCE_PATH" |
    tar -x -C "$stage" --strip-components=2
  printf '%s %s %s\n' "$kind" "$ref" "$(git -C "$SOURCE" rev-parse "$ref^{commit}")" > "$stage/.trellum-docs-version"
  [ "$kind" = "unreleased" ] && git -C "$SOURCE" rev-parse HEAD > "$stage/.unreleased"
  rm -rf "$DOCS_ROOT/$target"
  mv "$stage" "$DOCS_ROOT/$target"
}

PUBLISHED=(latest)
if [ "${#RELEASES[@]}" -eq 0 ]; then
  git -C "$SOURCE" cat-file -e "HEAD:$SOURCE_PATH" 2>/dev/null || {
    echo "HEAD has no $SOURCE_PATH" >&2; exit 1;
  }
  echo "No stable release tags; publishing HEAD as unreleased latest." >&2
  copy_docs HEAD latest unreleased
else
  for tag in "${RELEASES[@]}"; do
    copy_docs "$tag" "$tag" release
    PUBLISHED+=("$tag")
  done
  rm -rf "$DOCS_ROOT/latest"
  cp -R "$DOCS_ROOT/${RELEASES[0]}" "$DOCS_ROOT/latest"
fi

for path in "$DOCS_ROOT"/*; do
  [ -d "$path" ] || continue
  [ -f "$path/.trellum-docs-version" ] || continue
  name="$(basename "$path")"
  keep=0
  for published in "${PUBLISHED[@]}"; do
    [ "$name" = "$published" ] && keep=1
  done
  [ "$keep" -eq 1 ] || rm -rf "$path"
done

printf 'Published documentation:'
printf ' %s' "${PUBLISHED[@]}"
printf '\n'
