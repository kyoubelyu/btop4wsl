#!/bin/sh
set -eu
bridge_script=$(readlink -f -- "$0")
exec python3 "$(dirname -- "$bridge_script")/mount_hwmon.py" "$@"
