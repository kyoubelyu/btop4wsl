#!/usr/bin/env python3
"""Print every Windows host sensor exposed by the btop bridge."""
import argparse
import json
from pathlib import Path
import time
from btop4wsl_config import duration, load_config, setting, state_dir

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--json', action='store_true')
parser.add_argument('--config')
parser.add_argument('--state')
args = parser.parse_args()
try:
    settings = load_config(args.config)
    snapshot = json.loads((state_dir(settings, args.state) / 'snapshot.json').read_text())
except (OSError, ValueError) as exc:
    print(f'Host sensors unavailable: {exc}')
    raise SystemExit(1)
delta = time.time() - snapshot['updated']
age = max(0, delta)
fresh = -2 <= delta <= duration(snapshot.get('stale_after', setting('stale_after', settings, default=10)), 'stale_after', 1)
if args.json:
    print(json.dumps({'fresh': fresh, 'age_seconds': round(age, 1), **snapshot}, ensure_ascii=False, indent=2))
else:
    print(f'Windows host sensors · {age:.1f}s old' + (' · OFFLINE' if not fresh else ''))
    units = {'Temperature': '°C', 'Clock': 'MHz', 'Power': 'W', 'Voltage': 'V',
             'Load': '%', 'Fan': 'RPM', 'Throughput': 'B/s', 'Data': 'B', 'SmallData': 'B'}
    previous = None
    for sensor in snapshot['sensors']:
        if sensor['hardware'] != previous:
            print('\n' + sensor['hardware'])
            previous = sensor['hardware']
        value = sensor['value']
        text = '--' if value is None else f'{value:g} {units.get(sensor["type"], "")}'
        print(f'  {sensor["name"]:<36} {text}')
raise SystemExit(0 if fresh else 2)
