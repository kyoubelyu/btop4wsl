#!/bin/bash
set -euo pipefail
bridge_source=$(cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$bridge_source/install_bridge.py" "$@"
