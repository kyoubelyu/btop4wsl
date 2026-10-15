#!/bin/bash
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
install_prefix="$HOME/.local"
configure=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --prefix) install_prefix="$2"; shift 2 ;;
    --configure) configure=true; shift ;;
    *) printf 'usage: %s [--prefix DIR] [--configure]\n' "$0" >&2; exit 2 ;;
  esac
done
if [ ! -x "$source_dir/bin/btop" ]; then
  printf 'Build first: make -j4 GPU_SUPPORT=true\n' >&2; exit 1
fi
mkdir -p "$install_prefix/bin" "$install_prefix/lib/btop4wsl" "$install_prefix/share/btop" "$install_prefix/share/doc/btop4wsl"
install -m 755 "$source_dir/bin/btop" "$install_prefix/bin/btop"
ln -sfn btop "$install_prefix/bin/btop4wsl"
for file in btop-hardware.py btop4wsl_config.py; do
  install -m 755 "$source_dir/wsl/$file" "$install_prefix/lib/btop4wsl/$file"
done
ln -sfn ../lib/btop4wsl/btop-hardware.py "$install_prefix/bin/btop-hardware"
cp -R "$source_dir/themes" "$install_prefix/share/btop/"
install -m 644 "$source_dir/README.md" "$install_prefix/share/doc/btop4wsl/README.md"
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
if [ "$configure" = true ] || [ ! -f "$config_home/btop/btop-wsl.conf" ]; then
  python3 "$source_dir/wsl/configure.py" --config "$config_home/btop/btop-wsl.conf"
fi
printf 'Installed btop4wsl to %s/bin\n' "$install_prefix"
