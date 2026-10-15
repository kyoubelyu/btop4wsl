# btop4wsl

Windows host hardware monitoring inside WSL, built on [btop 1.4.7](https://github.com/aristocratos/btop/tree/v1.4.7).

CPU utilization follows [btop4win](https://github.com/aristocratos/btop4win): one row per logical processor, physical temperatures shared by sibling threads, and the highest reported core clock by default. Windows processor affinity supplies the mapping; WSL virtual core IDs are not used as physical sensor IDs.

[中文说明](wsl/README.zh-CN.md) · [Upstream documentation](README.upstream.md) · [Apache-2.0 license](LICENSE)

## Features

- Windows CPU package/core temperature, logical processor load, real clocks and package power.
- GPU load, clocks, reported temperatures, power, and dedicated/shared memory. Missing readings remain unavailable.
- Mounted Windows drive capacity, actual SSD temperatures and physical disk read/write rates.
- WSL memory, network and Linux processes, including the original btop process controls.
- Atomic local snapshots, configurable sampling/expiry, late sensor startup and automatic reconnection.

Drive letters, CPU counts, CPU temperature limits, PowerShell locations and Windows host addresses are discovered. Multiple partitions on the same physical drive share that drive's temperature and IO measurements.

This is a WSL/Linux executable. Windows hardware is provided by LibreHardwareMonitor; process and network panels monitor WSL.

## Install

On Windows, install [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases), run it with the privileges needed by your hardware, and enable its remote web server. Its usual port is 8085. Allow your WSL instance to reach that server. LHM startup and firewall rules remain under your Windows configuration.

Inside WSL2 with systemd and Python 3.10+:

```bash
git clone https://github.com/kyoubelyu/btop4wsl.git
cd btop4wsl
make -j4 GPU_SUPPORT=true
bash wsl/install.sh
sudo bash wsl/wsl-root-setup.sh
hash -r
btop4wsl
```

Builds require GCC 13+ and make. GPU support must be enabled. Release bundles contain a Linux x86-64 binary built on Ubuntu 24.04; unpack one and run the same two installation scripts, without the build step.

The user installer defaults to `~/.local`, supports `--prefix`, and preserves an existing configuration unless `--configure` is passed. The original distribution binary is not replaced.

## Configure

Bridge settings are shared by the collector, btop and `btop-hardware` in `/etc/btop4wsl.conf`. See [the example](wsl/bridge.conf.example).

```ini
[bridge]
host = auto
port = 8085
interval = 2
stale_after = 10
state_dir = /var/lib/wsl-hwmon-state
powershell = auto
export_hwmon = false
```

Set `url` for a complete LHM endpoint. Auto mode tries the Windows gateway and localhost, covering NAT and mirrored networking. `powershell` accepts an explicit executable if automatic discovery is unsuitable. `cpu_temp_limit` can override a hardware limit; otherwise Intel TjMax is derived from reported temperature/headroom pairs. Unknown limits are not presented as detected hardware values.

CLI arguments override `BTOP4WSL_*` environment variables, which override the INI file. Useful overrides include `BTOP4WSL_CONFIG`, `BTOP4WSL_STATE_DIR`, `BTOP4WSL_URL`, `BTOP4WSL_INTERVAL` and `BTOP4WSL_STALE_AFTER`. The C++ client reads the config/state/expiry settings and accepts `BTOP4WSL_SNAPSHOT` for an explicit snapshot. `BTOP_WSL_SNAPSHOT` remains compatible with older installations. Use absolute state paths.

The bridge installer supports `--prefix`, `--config`, `--state-dir`, `--url`, `--no-start` and `--dry-run`. When using a non-default config path, set `BTOP4WSL_CONFIG` for the client too. Restart `wsl-hwmon` after changing collector settings.

The enhanced btop configuration is `${XDG_CONFIG_HOME:-~/.config}/btop/btop-wsl.conf`, separate from the distribution's `btop.conf`. Legacy hwmon/sysfs mounting is optional via `--compat-hwmon`; it is not required by btop4wsl.

## Use and diagnose

`1`–`4` toggle CPU/memory/network/process panels; `5` opens the first GPU panel; `i` toggles disk IO graphs; `p` changes presets. `btop4wsl --preset 4` provides more space for CPU readings in a small terminal.

```bash
btop-hardware
btop-hardware --json
systemctl status wsl-hwmon
journalctl -u wsl-hwmon -n 30
```

The bridge can also run in the foreground with `python3 wsl/wsl-hwmon-bridge.py --url http://HOST:PORT/data.json --state /absolute/writable/directory`; configure the client with that same state directory. No credentials or machine snapshots are distributed in this repository.

## Test

```bash
python3 -m unittest discover -s tests/wsl -v
g++ -std=c++23 -Wall -Wextra -pedantic tests/wsl/test_snapshot.cpp -o /tmp/btop4wsl-snapshot-test
/tmp/btop4wsl-snapshot-test
python3 -m venv /tmp/btop4wsl-tests
/tmp/btop4wsl-tests/bin/pip install pyte==0.8.2
/tmp/btop4wsl-tests/bin/python tests/wsl/verify_terminal.py --columns 120 --rows 40
/tmp/btop4wsl-tests/bin/python tests/wsl/verify_terminal.py --columns 80 --rows 24
/tmp/btop4wsl-tests/bin/python tests/wsl/verify_terminal.py --offline --recover --late-start
/tmp/btop4wsl-tests/bin/python tests/wsl/verify_terminal.py --mutate-gpu
```

Terminal tests use synthetic sensors by default and do not require LHM or particular hardware. `--snapshot /path/to/btop.tsv` exercises a captured live snapshot. Tests cover shared physical temperatures, Windows affinity, missing readings, malformed snapshots, startup/reconnection and GPU identity/capability changes.

## Attribution

Upstream btop is Copyright Aristocratos and contributors, Apache-2.0. Upstream source history and license are retained. The Windows collector behavior is based on studying the Apache-2.0 btop4win implementation. LibreHardwareMonitor is an external dependency and is not bundled.
