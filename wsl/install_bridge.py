#!/usr/bin/env python3
"""Install the bridge and systemd unit with configurable paths."""
import argparse, configparser, json, os, shutil, subprocess
from pathlib import Path
from btop4wsl_config import DEFAULT_CONFIG, DEFAULT_STATE
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--prefix',type=Path,default=Path('/usr/local'))
p.add_argument('--config',type=Path,default=Path(DEFAULT_CONFIG))
p.add_argument('--state-dir',type=Path)
p.add_argument('--url')
p.add_argument('--compat-hwmon',action='store_true')
p.add_argument('--no-start',action='store_true')
p.add_argument('--dry-run',action='store_true')
a=p.parse_args(); source=Path(__file__).resolve().parent
library=a.prefix.resolve()/'lib/btop4wsl'; config=a.config.resolve()
for path in (library, config, a.state_dir):
    if path is not None and any(ord(c)<32 for c in str(path)): p.error('invalid path')
settings=configparser.ConfigParser(interpolation=None)
settings.read(source/'bridge.conf.example'); settings.read(config)
if a.state_dir: settings['bridge']['state_dir']=str(a.state_dir.resolve())
if a.url: settings['bridge']['url']=a.url
if a.compat_hwmon: settings['bridge']['export_hwmon']='true'
plan={'library':str(library),'config':str(config),'state':settings['bridge'].get('state_dir',DEFAULT_STATE)}
if a.dry_run: print(json.dumps(plan,indent=2)); raise SystemExit(0)
if os.geteuid()!=0: p.error('run this system installation with sudo')
library.mkdir(parents=True,exist_ok=True)
for name in ('wsl-hwmon-bridge.py','btop4wsl_config.py','windows-topology.ps1','wsl-hwmon-mount.sh','mount_hwmon.py'):
    shutil.copy2(source/name,library/name)
    (library/name).chmod(0o755 if name.endswith('.sh') else 0o644)
config.parent.mkdir(parents=True,exist_ok=True)
if config.exists(): shutil.copy2(config,config.with_suffix(config.suffix+'.bak'))
with config.open('w') as f: settings.write(f)
Path(plan['state']).mkdir(parents=True,exist_ok=True)
def quoted(value):
    # systemd accepts C-style quoted paths; escape its specifier prefix too.
    return json.dumps(str(value).replace('%','%%'))
unit='\n'.join(['[Unit]','Description=Windows host hardware sensors for btop4wsl','After=network.target','','[Service]','Type=simple',
    'ExecStartPre='+quoted(library/'wsl-hwmon-mount.sh')+' mount --config '+quoted(config),
    'ExecStart=/usr/bin/python3 '+quoted(library/'wsl-hwmon-bridge.py')+' --config '+quoted(config),
    'Restart=always','RestartSec=3','','[Install]','WantedBy=multi-user.target',''])
unit_path=Path('/etc/systemd/system/wsl-hwmon.service')
if unit_path.exists(): shutil.copy2(unit_path,unit_path.with_suffix('.service.bak'))
unit_path.write_text(unit)
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','wsl-hwmon.service'],check=True)
if not a.no_start: subprocess.run(['systemctl','restart','wsl-hwmon.service'],check=True)
print(json.dumps(plan,indent=2))
