import importlib.util
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).parents[2] / 'wsl'))

spec = importlib.util.spec_from_file_location('bridge', Path(__file__).parents[2] / 'wsl/wsl-hwmon-bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def sensor(name, typ, raw, display=None):
    return {'SensorId': '/' + name, 'Text': name, 'Type': typ, 'RawValue': raw,
            'Value': raw if display is None else display}


def hardware(identifier, name, sensors):
    return {'HardwareId': identifier, 'Text': name,
            'Children': [{'Text': 'group', 'Children': sensors}]}


class BridgeTests(unittest.TestCase):
    def test_rates_and_gpu_memory_keep_units_and_precision(self):
        self.assertEqual(bridge.value(sensor('Read Rate', 'Throughput', '1048576.0 B/s', '1.0 MB/s')), 1048576)
        self.assertEqual(bridge.value({'Type': 'Throughput', 'Value': '1.5 MB/s'}), 1572864)
        self.assertEqual(bridge.value(sensor('GPU Memory Used', 'SmallData', '750.0 MB')), 786432000)
        self.assertEqual(bridge.value(sensor('Temperature', 'Temperature', '122 °F')), 50)
        self.assertIsNone(bridge.value(sensor('Temperature', 'Temperature', 'NaN')))
        self.assertIsNone(bridge.value(sensor('Temperature', 'Temperature', None)))

    def test_physical_cores_do_not_use_virtual_cpu_mapping(self):
        data = {'Children': [hardware('/intelcpu/0', 'CPU', [
            sensor('CPU Package', 'Temperature', '80 °C'),
            sensor('CPU Package', 'Power', '0 W'),
            sensor('P-Core #1', 'Temperature', '75 °C'),
            sensor('P-Core #1', 'Clock', '4100 MHz'),
            sensor('E-Core #1', 'Temperature', '65 °C'),
            sensor('E-Core #1', 'Clock', '2500 MHz'),
            sensor('CPU Core #1 Thread #1', 'Load', '60 %'),
            sensor('CPU Core #1 Thread #2', 'Load', '20 %'),
            sensor('CPU Core #2', 'Load', '30 %'),
            sensor('P-Core #1 Distance to TjMax', 'Temperature', '25 °C'),
        ])]}
        result = bridge.normalize(data, now=100)
        self.assertEqual([(c['label'], c['temperature'], c['clock'], c['load']) for c in result['cores']],
                         [('P1', 75, 4100, 40), ('E1', 65, 2500, 30)])
        self.assertEqual(result['cpu']['power'], 0)
        self.assertEqual(result['cpu']['max_temperature'], 100)
        self.assertEqual([t['load'] for t in result['threads']], [60, 20, 30])
        self.assertEqual([t['temperature'] for t in result['threads']], [75, 75, 65])
        self.assertFalse(result['extra'])

    def test_unknown_temperature_limit_is_not_invented(self):
        data = {'Children': [hardware('/amdcpu/0', 'CPU', [sensor('Tdie', 'Temperature', '70 °C')])]}
        result = bridge.normalize(data)
        self.assertIsNone(result['cpu']['max_temperature'])
        with tempfile.TemporaryDirectory() as directory:
            bridge.write_hwmon(result, directory)
            self.assertFalse(list(Path(directory).glob('*/temp*_crit')))
        self.assertEqual(bridge.normalize(data, cpu_temp_limit=95)['cpu']['max_temperature'], 95)

    def test_noncontiguous_core_numbers_keep_correct_loads(self):
        data = {'Children': [hardware('/intelcpu/0', 'CPU', [
            sensor('CPU Core #1', 'Clock', '4000 MHz'),
            sensor('CPU Core #4', 'Clock', '3000 MHz'),
            sensor('CPU Core #1', 'Load', '10 %'),
            sensor('CPU Core #4', 'Load', '40 %')])]}
        result = bridge.normalize(data)
        self.assertEqual([(c['label'], c['load']) for c in result['cores']], [('C1', 10), ('C4', 40)])

    def test_windows_affinity_maps_logical_processors_to_physical_sensors(self):
        data = {'Children': [hardware('/intelcpu/0', 'CPU', [
            sensor('P-Core #1', 'Temperature', '80 °C'),
            sensor('E-Core #1', 'Temperature', '60 °C'),
            sensor('CPU Core #1 Thread #1', 'Load', '10 %'),
            sensor('CPU Core #1 Thread #2', 'Load', '30 %'),
            sensor('CPU Core #2', 'Load', '50 %')])]}
        topology = {'cores': [{'Efficiency': 0, 'Logical': [1]}, {'Efficiency': 1, 'Logical': [0, 2]}]}
        result = bridge.normalize(data, topology=topology)
        self.assertEqual([(t['label'], t['temperature'], t['load']) for t in result['threads']],
                         [('0', 80, 10), ('1', 60, 50), ('2', 80, 30)])

    def test_disk_mapping_is_by_physical_index_not_enumeration_order(self):
        data = {'Children': [hardware('/nvme/1', 'Disk W', [sensor('Composite Temperature', 'Temperature', '40 °C')]),
                             hardware('/nvme/0', 'Disk C', [sensor('Composite Temperature', 'Temperature', '35 °C'),
                                                           sensor('Temperature #1', 'Temperature', '49.9 °C'),
                                                           sensor('Critical Temperature', 'Temperature', '87 °C'),
                                                           sensor('Read Rate', 'Throughput', '1234 B/s')])]}
        result = bridge.normalize(data, {'C': 0, 'W': 1}, now=100)
        self.assertEqual(result['disks']['C']['name'], 'Disk C')
        self.assertEqual(result['disks']['C']['temperatures'], [35, 49.9])
        self.assertEqual(result['disks']['C']['read'], 1234)
        self.assertEqual(result['disks']['W']['temperatures'], [40])
        self.assertNotIn('D', result['disks'])

    def test_unreported_gpu_temperature_is_missing_and_shared_memory_is_labelled(self):
        data = {'Children': [hardware('/gpu-intel-integrated/0', 'Iris Xe', [
            sensor('GPU Core', 'Clock', '300 MHz'),
            sensor('D3D 3D', 'Load', '2 %'),
            sensor('D3D Video Decode', 'Load', '10 %'),
            sensor('D3D Shared Memory Used', 'SmallData', '750 MB'),
            sensor('D3D Shared Memory Total', 'SmallData', '16216 MB')])]}
        result = bridge.normalize(data, now=100)
        gpu = result['gpus'][0]
        self.assertIsNone(gpu['temperature'])
        self.assertTrue(gpu['shared_memory'])
        self.assertEqual(gpu['load'], 10)
        self.assertIn('gpu\tIris Xe\t-1\t300', bridge.serialize(result))

    def test_hwmon_removes_disappeared_sensors_instead_of_retaining_old_values(self):
        snapshot = bridge.normalize({'Children': [hardware('/nvme/0', 'SSD', [
            sensor('Composite Temperature', 'Temperature', '35 °C')])]}, now=100)
        with tempfile.TemporaryDirectory() as folder:
            bridge.write_hwmon(snapshot, folder)
            self.assertEqual(len(list(Path(folder).glob('*/temp*_input'))), 1)
            bridge.write_hwmon(bridge.normalize({'Children': []}, now=102), folder)
            self.assertFalse(list(Path(folder).iterdir()))

    def test_protocol_cannot_include_control_sequences_from_sensor_names(self):
        self.assertEqual(bridge.clean('SSD\t\n\x1b[31m'), 'SSD   [31m')


if __name__ == '__main__':
    unittest.main()
