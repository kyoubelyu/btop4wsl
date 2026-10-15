#!/usr/bin/env python3
"""Export live Windows LHM sensors to hwmon and an atomic btop snapshot."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import shutil
import tempfile
import time
import urllib.request
from btop4wsl_config import DEFAULT_HWMON, boolean, duration, load_config, setting, state_dir

NUMBER = re.compile(r"^\s*([+-]?\d+(?:[.,]\d+)?)")
CORE = re.compile(r"^(P-Core|E-Core|CPU Core|Core) #?(\d+)$", re.I)


def win_gateway():
    try:
        for line in Path('/proc/net/route').read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) > 2 and fields[1] == '00000000':
                return '.'.join(str(b) for b in bytes.fromhex(fields[2])[::-1])
    except (OSError, ValueError):
        pass
    return '127.0.0.1'


def value(sensor):
    """RawValue avoids losing units/precision in LHM's formatted Value."""
    raw = sensor.get('RawValue', sensor.get('Value'))
    if isinstance(raw, bool):
        return None
    unit = ''
    if isinstance(raw, (int, float)):
        result = float(raw)
    elif isinstance(raw, str) and (match := NUMBER.match(raw)):
        result = float(match[1].replace(',', '.'))
        unit = raw[match.end():].strip()
    else:
        return None
    if not math.isfinite(result):
        return None
    typ = sensor.get('Type')
    if typ == 'Throughput':
        result *= {'KB/s': 1024, 'MB/s': 1024**2, 'GB/s': 1024**3}.get(unit, 1)
    elif typ in ('SmallData', 'Data'):
        result *= {'B': 1, 'KB': 1024, 'MB': 1024**2, 'GB': 1024**3,
                   'TB': 1024**4}.get(unit, 1024**2 if typ == 'SmallData' else 1024**3)
    elif typ == 'Temperature' and unit in ('°F', 'F'):
        result = (result - 32) * 5 / 9
    elif typ == 'Clock' and unit == 'GHz':
        result *= 1000
    return result


def hardware_nodes(node):
    if not isinstance(node, dict):
        return
    if 'HardwareId' in node:
        yield node
    for child in node.get('Children') or []:
        yield from hardware_nodes(child)


def sensor_nodes(node):
    for child in node.get('Children') or []:
        if not isinstance(child, dict):
            continue
        if 'SensorId' in child:
            yield child
        elif 'HardwareId' not in child:
            yield from sensor_nodes(child)


def metric(sensors, typ, *names):
    for name in names:
        for s in sensors:
            if s.get('Type') == typ and s.get('Text', '').casefold() == name.casefold():
                if (v := value(s)) is not None:
                    return v
    return None


def actual_temperature(sensor):
    name = str(sensor.get('Text') or '').lower()
    return (sensor.get('Type') == 'Temperature' and value(sensor) is not None
            and not any(word in name for word in ('distance', 'warning', 'critical', 'tjmax')))


def core_key(name):
    if not isinstance(name, str):
        return None
    match = CORE.match(name)
    if not match:
        return None
    group = match[1].lower()
    return (0 if group == 'p-core' else 1 if group == 'e-core' else 2, int(match[2]))


def normalize(data, drive_map=None, now=None, cpu_temp_limit=None, topology=None):
    if not isinstance(data, dict):
        raise ValueError('LHM response must be an object')
    result = {'version': 1, 'updated': time.time() if now is None else now,
              'cpu': {}, 'cores': [], 'threads': [], 'gpus': [], 'disks': {}, 'extra': [], 'sensors': []}
    drive_map = drive_map or {}
    for hardware in hardware_nodes(data):
        hid, name = hardware['HardwareId'], hardware.get('Text', '')
        sensors = list(sensor_nodes(hardware))
        for sensor in sensors:
            result['sensors'].append({'hardware': name, 'hardware_id': hid,
                                      'name': sensor.get('Text', ''), 'type': sensor.get('Type', ''),
                                      'id': sensor['SensorId'], 'value': value(sensor)})
        temperatures = [s for s in sensors if actual_temperature(s)]
        if hid.startswith(('/intelcpu/', '/amdcpu/')):
            cpu = result['cpu']
            cpu.update(name=name, temperature=metric(sensors, 'Temperature', 'CPU Package', 'Core (Tctl/Tdie)', 'Tdie', 'Tctl', 'Core Max'),
                       power=metric(sensors, 'Power', 'CPU Package', 'Package'),
                       load=metric(sensors, 'Load', 'CPU Total'), max_temperature=cpu_temp_limit)
            limits = []
            for s in sensors:
                suffix = ' Distance to TjMax'
                if s.get('Type') == 'Temperature' and s.get('Text', '').endswith(suffix):
                    temp = metric(sensors, 'Temperature', s['Text'][:-len(suffix)])
                    headroom = value(s)
                    if temp is not None and headroom is not None and temp + headroom > 0:
                        limits.append(temp + headroom)
            if cpu_temp_limit is None and limits:
                cpu['max_temperature'] = min(limits)
            keys = sorted({key for s in sensors if s.get('Type') in ('Temperature', 'Clock')
                           and (key := core_key(s.get('Text', ''))) is not None})
            thread_groups = []
            for physical_index, key in enumerate(keys, 1):
                members = [s for s in sensors if core_key(s.get('Text', '')) == key]
                group, number = key
                # A physical core's load is the average of its hardware threads.
                # Non-hybrid names can have gaps; never renumber C4 as C2.
                load_index = number if group == 2 else physical_index
                load_sensors = [s for s in sensors if s.get('Type') == 'Load'
                         and re.match(rf'^CPU Core #{load_index}(?: Thread #\d+)?$', s.get('Text', ''))
                         and value(s) is not None]
                load_sensors.sort(key=lambda s: int(s.get('Text', '').rsplit('#', 1)[-1]))
                loads = [value(s) for s in load_sensors]
                core = {'label': ('P' if group == 0 else 'E' if group == 1 else 'C') + str(number),
                        'temperature': next((value(s) for s in members if s.get('Type') == 'Temperature'), None),
                        'clock': next((value(s) for s in members if s.get('Type') == 'Clock'), None),
                        'load': sum(loads) / len(loads) if loads else None}
                result['cores'].append(core)
                thread_groups.append((core, loads))
            # btop4win shows logical processors and shares the physical sensor
            # between SMT siblings. Windows affinity avoids guessing WSL IDs.
            host_cores = sorted((topology or {}).get('cores', []),
                                key=lambda c: (-c['Efficiency'], min(c['Logical'])))
            use_affinity = len(host_cores) == len(thread_groups)
            for index, (core, loads) in enumerate(thread_groups):
                logical = host_cores[index]['Logical'] if use_affinity else list(range(len(result['threads']), len(result['threads']) + len(loads)))
                for thread_index, logical_index in enumerate(logical):
                    result['threads'].append({**core, 'label': str(logical_index),
                                               'load': loads[thread_index] if thread_index < len(loads) else None})
            result['threads'].sort(key=lambda t: int(t['label']))
            cpu['clocks'] = [c['clock'] for c in result['cores'] if c['clock'] is not None]
            if cpu['temperature'] is None:
                cpu['temperature'] = metric(sensors, 'Temperature', 'Core Average')
            if cpu['temperature'] is None:
                core_temps = [c['temperature'] for c in result['cores'] if c['temperature'] is not None]
                if core_temps:
                    cpu['temperature'] = sum(core_temps) / len(core_temps)
            for s in temperatures:
                if core_key(s.get('Text', '')) is None and s.get('Text') != 'CPU Package':
                    result['extra'].append((s.get('Text', ''), value(s)))
        elif hid.startswith('/gpu'):
            loads = [value(s) for s in sensors if s.get('Type') == 'Load'
                     and value(s) is not None and s.get('Text', '').startswith(('GPU Core', 'D3D'))]
            used = metric(sensors, 'SmallData', 'GPU Memory Used', 'D3D Dedicated Memory Used', 'D3D Shared Memory Used')
            total = metric(sensors, 'SmallData', 'GPU Memory Total', 'D3D Dedicated Memory Total', 'D3D Shared Memory Total')
            result['gpus'].append({'id': hid, 'name': name, 'temperature': metric(sensors, 'Temperature', 'GPU Core', 'GPU Temperature'),
                                   'clock': metric(sensors, 'Clock', 'GPU Core'),
                                   'memory_clock': metric(sensors, 'Clock', 'GPU Memory'),
                                   'power': metric(sensors, 'Power', 'GPU Power', 'GPU Package'),
                                   'memory_used': used, 'memory_total': total,
                                   'shared_memory': not any(s.get('Text') in ('GPU Memory Total', 'D3D Dedicated Memory Total') for s in sensors),
                                   'load': max(loads) if loads else None,
                                   'encoder': metric(sensors, 'Load', 'D3D Video Encode', 'GPU Video Engine'),
                                   'decoder': metric(sensors, 'Load', 'D3D Video Decode')})
            for s in temperatures:
                if s.get('Text') not in ('GPU Core', 'GPU Temperature'):
                    result['extra'].append((name + ' ' + s.get('Text', ''), value(s)))
        elif hid.startswith(('/nvme/', '/hdd/', '/ssd/')):
            disk = {'name': name, 'temperatures': [value(s) for s in temperatures],
                    'read': metric(sensors, 'Throughput', 'Read Rate'),
                    'write': metric(sensors, 'Throughput', 'Write Rate'),
                    'activity': metric(sensors, 'Load', 'Total Activity')}
            disk_index = hid.rsplit('/', 1)[-1]
            for letter, index in drive_map.items():
                if str(index) == disk_index:
                    result['disks'][letter.upper()] = disk
        else:
            result['extra'].extend((name + ' ' + s.get('Text', ''), value(s)) for s in temperatures)
        result['extra'].extend((s.get('Text', '') + ' RPM', value(s)) for s in sensors
                               if s.get('Type') == 'Fan' and value(s) is not None)
    return result


def clean(text):
    return re.sub(r'[\x00-\x1f\x7f]', ' ', str(text))[:200]


def number(value, scale=1):
    return '-1' if value is None else str(round(value * scale))


def serialize(snapshot):
    cpu = snapshot['cpu']
    lines = ['version\t1', 'updated\t' + str(int(snapshot['updated'])),
             '\t'.join(('cpu', clean(cpu.get('name', '')), number(cpu.get('temperature'), 1000),
                        number(cpu.get('power'), 1000), number(cpu.get('load'))))]
    lines.append('cpu_limit\t' + number(cpu.get('max_temperature'), 1000))
    lines.append('valid_for\t' + str(snapshot.get('stale_after', 10)))
    for core in snapshot['cores']:
        lines.append('\t'.join(('core', core['label'], number(core['temperature'], 1000),
                                number(core['clock']), number(core['load']))))
    for thread in snapshot.get('threads', []):
        lines.append('\t'.join(('thread', thread['label'], number(thread['temperature'], 1000),
                                number(thread['clock']), number(thread['load']))))
    for index, gpu in enumerate(snapshot['gpus']):
        lines.append('\t'.join(('gpu', clean(gpu['name']), number(gpu['temperature'], 1000),
                                number(gpu['clock']), number(gpu['power'], 1000),
                                number(gpu['memory_used']), number(gpu['memory_total']),
                                number(gpu['load']), number(gpu['encoder']), number(gpu['decoder']),
                                number(gpu['memory_clock']), str(int(gpu['shared_memory'])))))
        lines.append('\t'.join(('gpu_id', str(index), clean(gpu['id']))))
    for letter, disk in sorted(snapshot['disks'].items()):
        lines.append('\t'.join(('disk', letter, clean(disk['name']),
                                ','.join(number(t, 1000) for t in disk['temperatures']),
                                number(disk['read']), number(disk['write']), number(disk['activity']))))
    lines.extend('\t'.join(('extra', clean(name), number(v))) for name, v in snapshot['extra'])
    return '\n'.join(lines) + '\n'


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never use "input" in the temporary filename: btop scans by substring.
    descriptor, name = tempfile.mkstemp(prefix='.wtmp-', dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(content)
        tmp.chmod(0o644)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def write_hwmon(snapshot, out):
    files = {}
    cpu = snapshot['cpu']
    if cpu.get('temperature') is not None:
        files.update({'coretemp0/name': 'coretemp', 'coretemp0/temp1_label': 'Package id 0',
                      'coretemp0/temp1_input': number(cpu['temperature'], 1000)})
        if cpu.get('max_temperature') is not None:
            files['coretemp0/temp1_max'] = number(cpu['max_temperature'], 1000)
            files['coretemp0/temp1_crit'] = number(cpu['max_temperature'], 1000)
        for index, core in enumerate(snapshot['cores']):
            if core['temperature'] is not None:
                files[f'coretemp0/temp{index+2}_input'] = number(core['temperature'], 1000)
                files[f'coretemp0/temp{index+2}_label'] = f'Core {index}'
    grouped = {}
    for sensor in snapshot['sensors']:
        if sensor['type'] not in ('Temperature', 'Fan') or sensor['value'] is None:
            continue
        if sensor['type'] == 'Temperature' and any(w in sensor['name'].lower() for w in ('distance', 'warning', 'critical', 'tjmax')):
            continue
        if sensor['hardware_id'].startswith(('/intelcpu/', '/amdcpu/')) and sensor['type'] == 'Temperature':
            continue
        grouped.setdefault(sensor['hardware_id'], []).append(sensor)
    for hid, sensors in grouped.items():
        directory = 'hwmon_' + hashlib.sha256(hid.encode()).hexdigest()[:12]
        files[f'{directory}/name'] = clean(sensors[0]['hardware'])
        counters = {'Temperature': 0, 'Fan': 0}
        for sensor in sensors:
            typ = sensor['type']; counters[typ] += 1
            prefix = ('temp' if typ == 'Temperature' else 'fan') + str(counters[typ])
            files[f'{directory}/{prefix}_input'] = number(sensor['value'], 1000 if typ == 'Temperature' else 1)
            files[f'{directory}/{prefix}_label'] = clean(sensor['name'])
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        atomic_write(out / relative, content + '\n')
    # Remove only the bridge's directories; leave any unrelated entries alone.
    for directory in out.iterdir():
        if directory.is_dir() and directory.name.startswith(('coretemp', 'hwmon')):
            for path in directory.iterdir():
                if path.is_file() and str(path.relative_to(out)) not in files:
                    path.unlink()
            if not any(directory.iterdir()):
                directory.rmdir()


def discover_powershell(explicit='auto'):
    if explicit and explicit != 'auto':
        command = shutil.which(explicit) or (explicit if Path(explicit).is_file() else None)
        if not command:
            raise FileNotFoundError(f'PowerShell executable not found: {explicit}')
        return command
    for name in ('powershell.exe', 'pwsh.exe'):
        if command := shutil.which(name):
            return command
    # WSL may omit Windows from PATH. Inspect actual drive mounts, not C:.
    mounts = Path('/proc/self/mounts').read_text().splitlines()
    for line in mounts:
        fields = line.split()
        if len(fields) < 3 or not re.match(r'^[A-Za-z]:', fields[0]):
            continue
        root = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[1]))
        for relative in ('Windows/System32/WindowsPowerShell/v1.0/powershell.exe',
                         'Program Files/PowerShell/7/pwsh.exe'):
            path = root / relative
            if path.is_file():
                return str(path)
    raise FileNotFoundError('Windows PowerShell not found; set powershell in the bridge configuration')


def get_topology(powershell='auto'):
    env = dict(os.environ)
    # A login shell's interop socket can disappear while this service stays up.
    # Select a live socket again for each background topology refresh.
    if not env.get('WSL_INTEROP') or not Path(env['WSL_INTEROP']).exists():
        sockets = sorted(Path('/run/WSL').glob('*_interop'), key=lambda p: p.stat().st_mtime, reverse=True)
        if sockets:
            env['WSL_INTEROP'] = str(sockets[0])
    script = Path(__file__).resolve().with_name('windows-topology.ps1')
    process = subprocess.run([discover_powershell(powershell), '-NoProfile', '-NonInteractive', '-Command', script.read_text(encoding='utf-8')],
                             capture_output=True, timeout=20, check=True, env=env)
    document = json.loads(process.stdout.decode('utf-8-sig').strip())
    if not isinstance(document, dict):
        raise ValueError('invalid Windows topology response')
    rows = document.get('drives', [])
    rows = rows if isinstance(rows, list) else [rows]
    result = {}
    for row in rows:
        letter, index = row.get('DriveLetter'), row.get('DiskNumber')
        if isinstance(letter, str) and re.fullmatch('[A-Za-z]', letter) and isinstance(index, int) and index >= 0:
            result[letter.upper()] = index
    return {'drives': result, 'cores': document.get('cores', [])}


def get_drive_map(powershell='auto'):
    return get_topology(powershell)['drives']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', help='INI file; defaults to BTOP4WSL_CONFIG or /etc/btop4wsl.conf')
    parser.add_argument('--out', help='optional legacy hwmon directory')
    parser.add_argument('--state', help='snapshot state directory')
    parser.add_argument('--url')
    parser.add_argument('--host')
    parser.add_argument('--port', type=int)
    parser.add_argument('--interval', type=float)
    parser.add_argument('--stale-after', type=float)
    parser.add_argument('--timeout', type=float)
    parser.add_argument('--powershell')
    parser.add_argument('--cpu-temp-limit', type=float)
    parser.add_argument('--export-hwmon', action='store_true', default=None)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    try:
        settings = load_config(args.config)
        state = state_dir(settings, args.state)
        interval = duration(setting('interval', settings, args.interval, 2), 'interval')
        stale_after = duration(setting('stale_after', settings, args.stale_after, 10), 'stale_after', 1)
        timeout = duration(setting('timeout', settings, args.timeout, 3), 'timeout')
        port = int(setting('port', settings, args.port, 8085))
        if not 1 <= port <= 65535:
            raise ValueError('port must be between 1 and 65535')
        cpu_limit = setting('cpu_temp_limit', settings, args.cpu_temp_limit)
        cpu_limit = None if cpu_limit in (None, '') else float(cpu_limit)
        if cpu_limit is not None and (not math.isfinite(cpu_limit) or not 0 < cpu_limit <= 250):
            raise ValueError('cpu_temp_limit must be between 0 and 250 degrees Celsius')
        export_hwmon = boolean(setting('export_hwmon', settings, args.export_hwmon, False)) or args.out is not None
        out = Path(setting('hwmon_dir', settings, args.out, DEFAULT_HWMON)).expanduser()
        powershell = setting('powershell', settings, args.powershell, 'auto')
        host = setting('host', settings, args.host, 'auto')
        endpoint = setting('url', settings, args.url) or None
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    state.mkdir(parents=True, exist_ok=True)
    try:
        drive_map = json.loads((state / 'drive-map.json').read_text())
    except (OSError, ValueError):
        drive_map = {}
    try:
        topology = json.loads((state / 'topology.json').read_text())
    except (OSError, ValueError):
        topology = {}
    # Interop may be absent from systemd's environment; discover its socket.
    if not os.environ.get('WSL_INTEROP'):
        sockets = sorted(Path('/run/WSL').glob('*_interop'), key=lambda p: p.stat().st_mtime, reverse=True)
        if sockets:
            os.environ['WSL_INTEROP'] = str(sockets[0])
    next_map_refresh = 0
    map_future = None
    executor = ThreadPoolExecutor(max_workers=1)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    erring = False
    while True:
        start = time.monotonic()
        try:
            if map_future is not None and map_future.done():
                try:
                    topology = map_future.result()
                    drive_map = topology['drives']
                    atomic_write(state / 'topology.json', json.dumps(topology))
                    atomic_write(state / 'drive-map.json', json.dumps(drive_map))
                    next_map_refresh = start + 300
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    print(f'disk mapping refresh: {exc}', flush=True)
                    next_map_refresh = start + 30
                map_future = None
            if map_future is None and start >= next_map_refresh:
                if args.once:
                    try:
                        topology = get_topology(powershell)
                        drive_map = topology['drives']
                        atomic_write(state / 'topology.json', json.dumps(topology))
                        atomic_write(state / 'drive-map.json', json.dumps(drive_map))
                    except (OSError, ValueError, subprocess.SubprocessError) as exc:
                        print(f'Windows topology unavailable: {type(exc).__name__}; using available LHM metadata', flush=True)
                    next_map_refresh = start + 300
                else:
                    map_future = executor.submit(get_topology, powershell)
            # Mirrored networking uses localhost; NAT uses the WSL gateway.
            candidates = [endpoint] if endpoint else [f'http://{address}:{port}/data.json' for address in
                         ([host] if host != 'auto' else dict.fromkeys((win_gateway(), '127.0.0.1')))]
            failure = None
            for url in candidates:
                try:
                    with opener.open(url, timeout=timeout) as response:
                        payload = response.read(8 * 1024 * 1024 + 1)
                        if len(payload) > 8 * 1024 * 1024:
                            raise ValueError('LHM response exceeds 8 MiB')
                        data = json.loads(payload)
                    break
                except (OSError, ValueError, TimeoutError) as exc:
                    failure = exc
            else:
                raise OSError(f'LHM endpoints unavailable: {failure}')
            snapshot = normalize(data, drive_map, cpu_temp_limit=cpu_limit, topology=topology)
            snapshot['stale_after'] = stale_after
            if not snapshot['sensors']:
                raise ValueError('LHM returned no sensors')
            if export_hwmon:
                write_hwmon(snapshot, out)
            atomic_write(state / 'snapshot.json', json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
            atomic_write(state / 'btop.tsv', serialize(snapshot))
            atomic_write(state / 'updated_at', str(int(snapshot['updated'])) + '\n')
            atomic_write(state / 'status.json', json.dumps({'ok': True, 'updated': snapshot['updated'], 'url': url}))
            if erring:
                print(f'LHM recovered: {url}', flush=True)
            erring = False
        except (OSError, ValueError, TimeoutError) as exc:
            atomic_write(state / 'status.json', json.dumps({'ok': False, 'error': str(exc), 'checked': time.time()}))
            if not erring:
                print(f'LHM unavailable: {exc}; btop expires readings after {stale_after:g} seconds', flush=True)
            erring = True
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(max(0.1, interval - (time.monotonic() - start)))


if __name__ == '__main__':
    raise SystemExit(main())
