#!/usr/bin/env bash
# Restores a database snapshot and its paired images. Discards later DB writes.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 scripts/deployment.py rollback "$@"
