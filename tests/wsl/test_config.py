import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[2] / 'wsl'))
import btop4wsl_config as config


class ConfigTests(unittest.TestCase):
    def test_explicit_environment_and_ini_precedence(self):
        with patch.dict(os.environ, {'BTOP4WSL_INTERVAL': '3'}, clear=True):
            values = {'interval': '5'}
            self.assertEqual(config.setting('interval', values, 2, 10), 2)
            self.assertEqual(config.setting('interval', values, default=10), '3')
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(config.setting('interval', values, default=10), '5')
            self.assertEqual(config.setting('interval', {}, default=10), 10)

    def test_configuration_path_and_url_preserve_percent_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bridge.conf'
            path.write_text('[bridge]\nurl = http://host/sensor%20data\nstate_dir = /tmp/a space\n')
            with patch.dict(os.environ, {'BTOP4WSL_CONFIG': str(path)}, clear=True):
                values = config.load_config()
                self.assertEqual(values['url'], 'http://host/sensor%20data')
                self.assertEqual(config.state_dir(values), Path('/tmp/a space'))

    def test_invalid_durations_and_relative_state_are_rejected(self):
        for value in ('nan', 'inf', '-1', '0', '1000000'):
            with self.assertRaises(ValueError):
                config.duration(value, 'interval')
        with self.assertRaises(ValueError):
            config.state_dir({}, 'relative/path')

    def test_install_dry_run_is_read_only_and_accepts_custom_paths(self):
        script = Path(__file__).parents[2] / 'wsl/install_bridge.py'
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'prefix with spaces'
            process = subprocess.run([sys.executable, str(script), '--dry-run', '--prefix', str(target)],
                                     capture_output=True, text=True, check=True)
            self.assertIn('prefix with spaces', process.stdout)
            self.assertFalse(target.exists())
