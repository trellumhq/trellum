#!/usr/bin/env bash
# Paired online upgrade; see docs/customer/install/upgrades.md.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 scripts/deployment.py upgrade "$@"
