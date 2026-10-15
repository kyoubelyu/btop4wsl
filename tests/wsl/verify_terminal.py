"""Exercise the actual TUI in a PTY; pyte is a verification-only dependency."""
import argparse
import codecs
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import tempfile
import termios
import time

import pyte

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--binary', default='bin/btop')
parser.add_argument('--output', default='/tmp/btop-terminal-evidence')
parser.add_argument('--columns', type=int, default=160)
parser.add_argument('--rows', type=int, default=50)
parser.add_argument('--snapshot', type=Path, help='live snapshot to exercise instead of synthetic data')
parser.add_argument('--mutate-gpu', action='store_true')
parser.add_argument('--offline', action='store_true')
parser.add_argument('--recover', action='store_true')
parser.add_argument('--late-start', action='store_true')
args = parser.parse_args()
output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
scratch = tempfile.TemporaryDirectory(prefix='btop-terminal-')
config = Path(scratch.name) / 'btop/btop-wsl.conf'; config.parent.mkdir()
config.write_text('''shown_boxes = "cpu mem net proc"
check_temp = True
show_coretemp = True
show_cpu_freq = True
show_cpu_watts = True
freq_mode = "highest"
cpu_graph_upper = "total"
cpu_graph_lower = "total"
show_gpu_info = "On"
only_physical = True
use_fstab = False
disks_filter = ""
swap_disk = False
update_ms = 500
truecolor = True
background_update = False
log_level = "DEBUG"
''')
env = dict(os.environ, TERM='xterm-256color', LANG='C.UTF-8', XDG_CONFIG_HOME=scratch.name,
           XDG_STATE_HOME=scratch.name + '/state')
# Synthetic defaults make CI independent of the maintainer's CPU, disks and LHM.
import re
mounts = []
for line in Path('/proc/self/mounts').read_text().splitlines():
    fields = line.split()
    if len(fields) >= 3 and re.match(r'^[A-Za-z]:', fields[0]) and fields[1] not in [x[1] for x in mounts]:
        mounts.append((fields[0][0].upper(), fields[1]))

def synthetic(order=('a',), partial=False):
    lines = ['version\t1', 'updated\t1', 'cpu\tTest CPU\t80000\t49000\t20', 'cpu_limit\t95000']
    for i in range(4):
        lines.append(f'core\tC{i}\t75000\t4100\t40')
    for i in range(6):
        lines.append(f'thread\t{i}\t75000\t4100\t40')
    for index, device in enumerate(order):
        clock = -1 if partial and device == 'a' else 1111 if device == 'a' else 2222
        load = -1 if partial and device == 'a' else 20
        lines.append(f'gpu\tGPU {device.upper()}\t70000\t{clock}\t5000\t1048576\t1073741824\t{load}\t-1\t0\t-1\t1')
        lines.append(f'gpu_id\t{index}\t/gpu-intel/test-{device}')
    for letter, mount in mounts:
        lines.append(f'disk\t{letter}\tTest SSD\t35000,45000,40000\t1024\t2048\t15')
    return '\n'.join(lines) + '\n'

source_text = args.snapshot.read_text() if args.snapshot else synthetic(('a','b') if args.mutate_gpu else ('a',))
thread_labels = ['C'+line.split('\t')[1] for line in source_text.splitlines() if line.startswith('thread\t')]
physical_labels = [line.split('\t')[1] for line in source_text.splitlines() if line.startswith('core\t')]
expected_labels = thread_labels or physical_labels
fixture = Path(scratch.name) / 'fixture.tsv'
online = not args.offline
publish_after = 0

def publish():
    text = source_text
    timestamp = int(time.time()) if online else 1
    text = re.sub(r'^updated\t.*$', 'updated\t'+str(timestamp), text, flags=re.M)
    if args.late_start and not online and not has_started:
        text = 'version\t1\nupdated\t1\n'
    temporary = fixture.with_suffix('.tmp'); temporary.write_text(text); temporary.replace(fixture)

has_started = False
publish()
env['BTOP4WSL_SNAPSHOT'] = str(fixture)
isolated_bridge_config = Path(scratch.name) / 'bridge.conf'
isolated_bridge_config.write_text('[bridge]\nstale_after = 10\n')
env['BTOP4WSL_CONFIG'] = str(isolated_bridge_config)
master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', args.rows, args.columns, 0, 0))
process = subprocess.Popen([str(Path(args.binary).resolve())], stdin=slave, stdout=slave,
                           stderr=slave, env=env, start_new_session=True)
os.close(slave)
screen = pyte.Screen(args.columns, args.rows)
stream = pyte.Stream(screen)
decoder = codecs.getincrementaldecoder('utf-8')('replace')
raw_output = bytearray()


def pump(seconds):
    global publish_after
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if online and time.monotonic() >= publish_after:
            publish(); publish_after = time.monotonic() + 1
        if select.select([master], [], [], min(0.2, max(0, deadline-time.monotonic())))[0]:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            raw_output.extend(data)
            if b'\x1b[6n' in data:
                os.write(master, b'\x1b[1;1R')
            stream.feed(decoder.decode(data))
        if process.poll() is not None:
            break


def capture(name):
    text = '\n'.join(screen.display)
    (output / (name + '.txt')).write_text(text)
    try:
        from PIL import Image, ImageDraw, ImageFont
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 14)
        img = Image.new('RGB', (args.columns * 9 + 20, args.rows * 18 + 20), '#101418')
        draw = ImageDraw.Draw(img)
        colors = {'black':'#30363d', 'red':'#ff7b72', 'green':'#7ee787', 'brown':'#d29922',
                  'blue':'#79c0ff', 'magenta':'#d2a8ff', 'cyan':'#a5d6ff', 'white':'#c9d1d9',
                  'brightblack':'#8b949e', 'brightred':'#ffa198', 'brightgreen':'#aff5b4',
                  'brightbrown':'#e3b341', 'brightblue':'#a5d6ff', 'brightmagenta':'#d2a8ff',
                  'brightcyan':'#a5d6ff', 'brightwhite':'#ffffff', 'default':'#c9d1d9'}
        for y, row in screen.buffer.items():
            for x, cell in row.items():
                fg = colors.get(cell.fg, '#' + cell.fg if len(cell.fg) == 6 else '#c9d1d9')
                draw.text((10+x*9, 10+y*18), cell.data, font=font, fill=fg)
        img.save(output / (name + '.png'))
    except (ImportError, OSError):
        pass
    return text


def wait_for_screen(name, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and process.poll() is None:
        pump(0.4)
        if predicate('\n'.join(screen.display)):
            return capture(name)
    state = capture(name)
    raise AssertionError(f'{name} did not reach the expected state; exit={process.poll()}')


try:
    pump(4)
    assert process.poll() is None, 'btop exited during startup'
    dashboard = capture('dashboard')
    if args.offline:
        assert 'offline' in dashboard
        assert '49.0W' not in dashboard
    elif args.columns >= 120 and args.rows >= 40:
        for expected in (expected_labels[0], expected_labels[-1], 'Host sensors'):
            assert expected in dashboard, f'{expected} absent from dashboard'
        for letter, mount in mounts:
            if any(line.startswith('disk\t'+letter+'\t') for line in source_text.splitlines()):
                assert mount in dashboard or letter+':' in dashboard
        if mounts:
            assert '/s W ' in dashboard
    if args.recover:
        assert args.offline
        online = True; has_started = True; publish(); pump(2)
        recovered = capture('recovered')
        assert expected_labels[-1] in recovered and 'offline' not in recovered
        online = False; publish(); pump(2)
        assert 'Host sensors offline' in capture('disconnected')
        fixture.unlink(); pump(2)
        assert 'Host sensors offline' in capture('missing-file')
        online = True; publish(); pump(2)
        assert expected_labels[-1] in capture('recovered-again')
    if args.columns < 120 or args.rows < 40:
        for key in (b'2', b'3', b'4'):
            os.write(master, key); pump(0.5)
        sensors = capture('cpu-only')
        if online:
            assert expected_labels[-1] in sensors
    os.write(master, b'5'); pump(2)
    gpu = capture('gpu')
    if args.offline and not args.recover:
        assert 'Windows sensors offline' in gpu
    else:
        assert 'MHz' in gpu and 'shared' in gpu
    if args.mutate_gpu:
        assert 'GPU A' in gpu and '1111 MHz' in gpu
        source_text = synthetic(('b','a')); publish()
        wait_for_screen('gpu-reordered', lambda t: 'GPU A' in t and '1111 MHz' in t)
        source_text = synthetic(('b','a'), partial=True); publish()
        wait_for_screen('gpu-partial', lambda t: 'GPU A' in t and '1111 MHz' not in t and 'TMP' in t)
        source_text = synthetic(('b',)); publish()
        wait_for_screen('gpu-removed', lambda t: 'Windows sensors offline' in t)
        source_text = synthetic(('b','a')); publish()
        wait_for_screen('gpu-returned', lambda t: '1111 MHz' in t)
    os.write(master, b'5'); pump(0.6)
    os.write(master, b'i'); pump(1.5)
    capture('disk-io')
    os.write(master, b'm'); pump(0.7)
    capture('menu')
    os.write(master, b'\r'); pump(0.7)
    assert 'theme' in capture('options').lower()
    os.write(master, b'\x1b'); pump(0.6)
    for attempt in range(3):
        if process.poll() is not None:
            break
        os.write(master, b'q'); pump(0.6)
    process.wait(timeout=5)
    assert process.returncode == 0
    logs = list(Path(scratch.name).rglob('btop.log'))
    assert logs, 'verification log was not created'
    for log in logs:
        contents = log.read_text(); (output/log.name).write_text(contents)
        assert '| ERROR:' not in contents, contents
    (output/'terminal.ansi').write_bytes(raw_output)
    print(json.dumps({'ok':True,'columns':args.columns,'rows':args.rows,'offline':args.offline,
                      'gpu_mutations':args.mutate_gpu,'evidence':str(output)}))

finally:
    (output/'process-status.json').write_text(json.dumps({'exit_code':process.poll()}))
    (output/'terminal.ansi').write_bytes(raw_output)
    if fixture.exists():
        (output/'last-snapshot.tsv').write_text(fixture.read_text())
    for log in Path(scratch.name).rglob('btop.log'):
        (output/log.name).write_text(log.read_text())
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
    os.close(master)
    scratch.cleanup()
