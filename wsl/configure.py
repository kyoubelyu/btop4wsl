#!/usr/bin/env python3
"""Enable host sensors and real mounted Windows drives without fstab edits."""
from pathlib import Path
import re
import argparse
import os

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--config', type=Path, help='btop configuration file')
args = parser.parse_args()
path = args.config or Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'btop/btop-wsl.conf'
path.parent.mkdir(parents=True, exist_ok=True)
original = path.with_name('btop.conf')
text = path.read_text() if path.exists() else original.read_text() if original.exists() else ''
settings = {
    'shown_boxes': '"cpu mem net proc"',
    'check_temp': 'True', 'show_coretemp': 'True', 'show_cpu_freq': 'True',
    'show_cpu_watts': 'True', 'cpu_core_map': '""', 'cpu_sensor': '"Auto"',
    'freq_mode': '"highest"', 'cpu_graph_upper': '"total"', 'cpu_graph_lower': '"total"',
    'show_gpu_info': '"On"', 'show_disks': 'True', 'use_fstab': 'False',
    'only_physical': 'True', 'swap_disk': 'False', 'show_io_stat': 'True',
    'disks_filter': '""',
    'presets': '"cpu:0:default,mem:0:default,net:0:default,proc:0:default cpu:0:default,mem:0:default,net:0:default cpu:0:default,mem:0:default,gpu0:0:default cpu:0:default"',
}
for key, value in settings.items():
    line = f'{key} = {value}'
    pattern = rf'^{re.escape(key)}\s*=.*$'
    if re.search(pattern, text, flags=re.M):
        text = re.sub(pattern, lambda _: line, text, flags=re.M)
    else:
        text += '\n' + line + '\n'
path.write_text(text)
print(path)
