"""Controlled one-page normal-scan experiment; restores the user's daemon.

Run using the scanner daemon's installed Python environment. No permanent config
changes. A short receipt must be inserted after waiting_for_paper is printed.
"""
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import os
import signal
import subprocess

from ds640_daemon.config import Config
from ds640_daemon.runtime import build_runtime_command

cfg = Config.load('/home/charlie/.config/brother-ds640/config.toml')
state = Path.home() / '.local/state/brother-ds640-normal-scan-experiment' / datetime.now().strftime('%Y%m%d-%H%M%S')
cfg = replace(cfg, runtime=replace(cfg.runtime, state_directory=str(state)))
script = Path(__file__).with_name('driver_presence_probe.py').resolve()
command = build_runtime_command(cfg, ['/usr/bin/python3', str(script), '--wait', '180',
                                     '--scan', str(state / 'normal-autosize.png')])
index = command.index('/usr/bin/python3')
command[index:index] = ['--setenv', 'LD_PRELOAD', str(cfg.runtime.tap_path),
                        '--setenv', 'DS640_USB_TAP_PATCH_ATCN', '0',
                        '--setenv', 'DS640_USB_TAP_LOG_COMMANDS', '1',
                        '--setenv', 'DS640_USB_TAP_LOG', str(state / 'commands.log')]
active = subprocess.run(['systemctl', '--user', 'is-active', '--quiet', 'brother-ds640.service']).returncode == 0
print(f'experiment_directory={state}', flush=True)
try:
    if active:
        subprocess.run(['systemctl', '--user', 'stop', 'brother-ds640.service'], check=True)
    with (state / 'result.log').open('w') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            process.wait(timeout=240)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
    print((state / 'result.log').read_text(), flush=True)
finally:
    if active:
        subprocess.run(['systemctl', '--user', 'start', 'brother-ds640.service'], check=True)
        print('normal_daemon_restored=true', flush=True)
