"""Shared bridge configuration. CLI overrides environment, then INI, then defaults."""
import configparser
import math
import os
from pathlib import Path

DEFAULT_CONFIG = '/etc/btop4wsl.conf'
DEFAULT_STATE = '/var/lib/wsl-hwmon-state'  # Preserve existing installations.
DEFAULT_HWMON = '/var/lib/wsl-hwmon'


def load_config(path=None):
    path = Path(path or os.environ.get('BTOP4WSL_CONFIG', DEFAULT_CONFIG)).expanduser()
    parser = configparser.ConfigParser(interpolation=None)
    if path.exists():
        with path.open(encoding='utf-8') as stream:
            parser.read_file(stream)
    return dict(parser['bridge']) if parser.has_section('bridge') else {}


def setting(name, values, explicit=None, default=None):
    if explicit is not None:
        return explicit
    return os.environ.get('BTOP4WSL_' + name.upper(), values.get(name, default))


def duration(value, name, minimum=0.1, maximum=86400):
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be between {minimum} and {maximum} seconds')
    return value


def boolean(value):
    if isinstance(value, bool):
        return value
    text = str(value).lower()
    if text not in ('true', 'false', 'yes', 'no', '1', '0'):
        raise ValueError('expected a boolean value')
    return text in ('true', 'yes', '1')


def state_dir(values=None, explicit=None):
    path = Path(setting('state_dir', values or {}, explicit, DEFAULT_STATE)).expanduser()
    if not path.is_absolute():
        raise ValueError('state_dir must be an absolute path')
    return path
