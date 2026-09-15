"""Read-only SANE setup and Brother ADF presence query; never starts a scan.

Run inside the scanner project's driver runtime, with its normal daemon stopped.
Uses an undocumented API from the pinned brscan5 library.
"""
import argparse
import ctypes as C
import json
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument('--wait', type=float, default=0, help='Wait for insertion, at most this many seconds')
parser.add_argument('--scan', help='Output PNG for one normal auto-sized scan after insertion')
args = parser.parse_args()
if args.scan and not args.wait:
    parser.error('--scan requires --wait')


class Device(C.Structure):
    _fields_ = [(name, C.c_char_p) for name in ('name', 'vendor', 'model', 'type')]


lib = C.CDLL('libsane-brother5.so.1.0.7', mode=C.RTLD_GLOBAL)
lib.sane_init.argtypes = [C.POINTER(C.c_int), C.c_void_p]
lib.sane_get_devices.argtypes = [C.POINTER(C.POINTER(C.POINTER(Device))), C.c_int]
lib.sane_open.argtypes = [C.c_char_p, C.POINTER(C.c_void_p)]
lib.sane_close.argtypes = [C.c_void_p]
lib.bssdkwrapper_IsAdfInserted.argtypes = []
lib.bssdkwrapper_IsAdfInserted.restype = C.c_bool
core = C.CDLL('libLxBsScanCoreApi.so.3.2.1')
query = core._ZN6bscore9CScanCore13QueryAdfStateEv
query.argtypes = [C.c_void_p]
query.restype = C.c_int
version = C.c_int()
handle = C.c_void_p()


def checked(status):
    if status:
        raise RuntimeError(f'SANE status {status}')


checked(lib.sane_init(C.byref(version), None))
detected = False
try:
    devices = C.POINTER(C.POINTER(Device))()
    checked(lib.sane_get_devices(C.byref(devices), 1))
    if not devices or not devices[0]:
        raise RuntimeError('No scanner found')
    name = devices[0].contents.name
    print(json.dumps({'device': name.decode()}), flush=True)
    checked(lib.sane_open(name, C.byref(handle)))
    deadline = time.monotonic() + args.wait
    previous = None
    attempts = 0
    print(json.dumps({'status': 'waiting_for_paper', 'scan_on_insertion': bool(args.scan)}), flush=True)
    while True:
        start = time.monotonic()
        # Preserve the enum: the boolean wrapper conflates errors with no paper.
        state = query(C.c_void_p.in_dll(lib, 'pp'))
        present = state == 2  # Verified from bssdkwrapper_IsAdfInserted.
        attempts += 1
        if state != previous or not args.wait:
            print(json.dumps({'paper_present': present, 'raw_state': state,
                              'seconds': time.monotonic()-start}), flush=True)
            previous = state
        if args.wait and present:
            detected = True
            break
        if (not args.wait and attempts >= 3) or (args.wait and time.monotonic() >= deadline):
            break
        time.sleep(.5)
finally:
    if handle:
        lib.sane_close(handle)
    lib.sane_exit()

if args.scan and detected:
    command = ['scanimage', '--format=png', '--batch='+args.scan, '--batch-count=1',
               '--resolution=300', '--AutoDocumentSize=yes']
    print(json.dumps({'status': 'starting_normal_scan', 'command': command}), flush=True)
    start = time.monotonic()
    result = subprocess.run(command, capture_output=True, text=True, timeout=45)
    print(json.dumps({'status': 'scan_finished', 'exit_code': result.returncode,
                      'seconds': time.monotonic()-start, 'stderr': result.stderr}), flush=True)
    raise SystemExit(result.returncode)
elif args.wait:
    print(json.dumps({'status': 'paper_detected' if detected else 'insertion_timeout'}), flush=True)
    raise SystemExit(0 if detected else 2)
