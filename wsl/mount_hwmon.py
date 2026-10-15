#!/usr/bin/env python3
"""Optional hwmon compatibility; default installs do not mount over sysfs."""
import argparse, subprocess
from pathlib import Path
from btop4wsl_config import DEFAULT_HWMON, boolean, load_config, setting
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('action',choices=['mount']);p.add_argument('--config');a=p.parse_args()
s=load_config(a.config)
if not boolean(setting('export_hwmon',s,default=False)):raise SystemExit(0)
source=Path(setting('hwmon_dir',s,default=DEFAULT_HWMON)).expanduser()
source.mkdir(parents=True,exist_ok=True);target=Path('/sys/class/hwmon')
if subprocess.run(['mountpoint','-q',str(target)]).returncode==0:
    if not source.samefile(target):raise SystemExit('hwmon is already mounted from another location')
else:subprocess.run(['mount','--bind',str(source),str(target)],check=True)
