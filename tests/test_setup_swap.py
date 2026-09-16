"""Exercise setup's host provisioning with disposable files and fake swap tools.

No real swap is enabled, no root privileges are used, and dd writes only a marker.
"""

import json
import os
from pathlib import Path
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def host(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "host-command"
    shim.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
root = pathlib.Path(os.environ['SWAP_TEST_ROOT'])
args = sys.argv[1:]
with (root / 'calls').open('a') as log:
    log.write(json.dumps([name, *args]) + '\\n')
active = root / 'active'
if name == 'swapon':
    if args == ['--show=NAME', '--noheadings']:
        print(active.read_text(), end='')
    else:
        if os.environ.get('SWAP_TEST_FAIL_ACTIVATION'):
            sys.exit(1)
        active.write_text(active.read_text() + args[0] + '\\n')
elif name == 'df':
    print('Filesystem 1024-blocks Used Available Capacity Mounted on')
    print('test 10000000 0 ' + os.environ.get('SWAP_TEST_AVAILABLE', '9000000') + ' 0% /')
elif name == 'dd':
    path = next(a[3:] for a in args if a.startswith('of='))
    pathlib.Path(path).write_bytes(b'allocated')
elif name == 'mkswap':
    pathlib.Path(args[-1]).write_bytes(b'initialized-swap')
elif name == 'blkid':
    if pathlib.Path(args[-1]).read_bytes() == b'initialized-swap':
        print('swap')
    else:
        sys.exit(2)
elif name != 'chown':
    raise AssertionError(name)
""")
    shim.chmod(0o755)
    for name in ("swapon", "df", "dd", "mkswap", "blkid", "chown"):
        (bin_dir / name).symlink_to(shim)
    (tmp_path / "active").write_text("")
    (tmp_path / "fstab").write_text("# Existing host configuration\nUUID=root / ext4 defaults 0 1\n")
    return tmp_path


def provision(host, **variables):
    return subprocess.run([
        "bash", "-c",
        'source "$1"; privileged() { "$@"; }; configure_host_swap "$2" "$3"',
        "swap-test", str(PROJECT_ROOT / "setup.sh"),
        str(host / "swapfile"), str(host / "fstab"),
    ], env={**os.environ, "PATH": f"{host / 'bin'}:{os.environ['PATH']}",
            "SWAP_TEST_ROOT": str(host), **variables},
       capture_output=True, text=True)


def calls(host, command):
    return [call for line in (host / "calls").read_text().splitlines()
            if (call := json.loads(line))[0] == command]


def test_swap_creation_is_persistent_and_idempotent(host):
    before = (host / "fstab").read_text()
    result = provision(host)
    assert result.returncode == 0, result.stderr
    swap = host / "swapfile"
    assert swap.read_bytes() == b"initialized-swap"
    assert swap.stat().st_mode & 0o777 == 0o600
    assert (host / "active").read_text() == f"{swap}\n"
    expected = before + f"\n{swap} none swap sw 0 0\n"
    assert (host / "fstab").read_text() == expected
    assert "count=2048" in calls(host, "dd")[0]
    for _ in range(2):
        result = provision(host)
        assert result.returncode == 0, result.stderr
    assert (host / "fstab").read_text() == expected
    assert len(calls(host, "dd")) == len(calls(host, "mkswap")) == 1
    assert len([c for c in calls(host, "swapon") if c[1] == str(swap)]) == 1


@pytest.mark.parametrize("active", [False, True])
def test_existing_host_swap_is_preserved(host, active):
    if active:
        (host / "active").write_text("/dev/existing-swap\n")
    else:
        with (host / "fstab").open("a") as file:
            file.write("UUID=existing none swap sw 0 0\n")
    before = (host / "fstab").read_text()
    result = provision(host)
    assert result.returncode == 0, result.stderr
    assert not (host / "swapfile").exists()
    assert (host / "fstab").read_text() == before
    assert not calls(host, "mkswap")
    assert "leaving its configuration unchanged" in result.stdout


def test_existing_managed_swap_is_reenabled_without_reformatting(host):
    swap = host / "swapfile"
    swap.write_bytes(b"initialized-swap")
    (host / "fstab").write_text(f"{swap} none swap sw 0 0\n")
    result = provision(host)
    assert result.returncode == 0, result.stderr
    assert (host / "active").read_text() == f"{swap}\n"
    assert (host / "fstab").read_text() == f"{swap} none swap sw 0 0\n"
    assert not calls(host, "mkswap")
    assert not calls(host, "dd")


@pytest.mark.parametrize("kind", ["file", "symlink", "hardlink"])
def test_unrelated_file_at_swap_path_is_not_overwritten(host, kind):
    original = host / "important"
    original.write_bytes(b"keep this data")
    swap = host / "swapfile"
    if kind == "symlink":
        swap.symlink_to(original)
    elif kind == "hardlink":
        swap.hardlink_to(original)
    else:
        swap.write_bytes(original.read_bytes())
    before = (host / "fstab").read_text()
    result = provision(host)
    assert result.returncode != 0
    assert swap.read_bytes() == original.read_bytes() == b"keep this data"
    assert (host / "fstab").read_text() == before
    assert not calls(host, "dd")
    assert not calls(host, "mkswap")


def test_insufficient_disk_space_does_not_create_swap(host):
    before = (host / "fstab").read_text()
    result = provision(host, SWAP_TEST_AVAILABLE="2000000")
    assert result.returncode != 0
    assert "need at least 3 GiB free" in result.stderr
    assert not (host / "swapfile").exists()
    assert not calls(host, "dd")
    assert (host / "fstab").read_text() == before


def test_activation_failure_leaves_fstab_unchanged_and_retry_reuses_file(host):
    before = (host / "fstab").read_text()
    result = provision(host, SWAP_TEST_FAIL_ACTIVATION="1")
    assert result.returncode != 0
    assert "could not enable" in result.stderr
    assert (host / "fstab").read_text() == before
    assert (host / "active").read_text() == ""
    assert (host / "swapfile").read_bytes() == b"initialized-swap"
    result = provision(host)
    assert result.returncode == 0, result.stderr
    assert len(calls(host, "dd")) == len(calls(host, "mkswap")) == 1
