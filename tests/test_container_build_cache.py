"""Opt-in Podman regression for revision changes invalidating dependencies.

Run with CASH_TEST_CONTAINER_CACHE=1. Uses the real Containerfile's instruction
order, replacing expensive RUNs with unique markers and COPY sources with tiny
fixtures. Requires the Python base image to be cached; never downloads packages.
"""
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest


@pytest.mark.skipif(os.environ.get('CASH_TEST_CONTAINER_CACHE') != '1',
                    reason='Opt-in local Podman cache check')
def test_new_revision_reuses_build_layers_and_updates_image_identity(tmp_path):
    source = (Path(__file__).resolve().parents[1] / 'Containerfile').read_text()
    instructions = source.replace('\\\n', '').splitlines()
    rewritten = []
    marker_count = 0
    before_revision = True
    for line in instructions:
        if line.startswith('ARG CASH_BUILD_REVISION'):
            before_revision = False
        if line.startswith('RUN ') and before_revision:
            marker_count += 1
            line = (f'RUN python -c "from pathlib import Path; import uuid; '
                    f"Path('/layer-{marker_count}').write_text(str(uuid.uuid4()))\"")
        elif line.startswith('COPY '):
            for name in line.split()[1:-1]:
                path = tmp_path / name
                if (Path(__file__).resolve().parents[1] / name).is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                    (path / 'placeholder').touch()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('cache fixture\n')
        rewritten.append(line)
    (tmp_path / 'Containerfile').write_text('\n'.join(rewritten) + '\n')
    suffix = uuid.uuid4().hex
    tags = [f'localhost/cash27-cache-test:{suffix}-{i}' for i in (1, 2)]
    snapshots = []
    try:
        for revision, tag in zip(('revision-one', 'revision-two'), tags):
            result = subprocess.run([
                'podman', 'build', '--layers', '--pull=never', '--network=none',
                '--build-arg', f'CASH_BUILD_REVISION={revision}',
                '--tag', tag, str(tmp_path)], capture_output=True, text=True, timeout=180)
            assert result.returncode == 0, result.stdout + result.stderr
            inspected = subprocess.run(['podman', 'image', 'inspect', tag],
                                       capture_output=True, text=True, check=True)
            config = json.loads(inspected.stdout)[0]['Config']
            assert config['Labels']['org.opencontainers.image.revision'] == revision, result.stdout
            assert f'CASH_BUILD_REVISION={revision}' in config['Env']
            markers = subprocess.run([
                'podman', 'run', '--rm', '--network=none', '--read-only',
                '--entrypoint=python', tag, '-c',
                "from pathlib import Path; import json; "
                "print(json.dumps({p.name:p.read_text() for p in Path('/').glob('layer-*')}))"],
                capture_output=True, text=True, check=True)
            snapshots.append(json.loads(markers.stdout))
        assert len(snapshots[0]) == marker_count
        assert snapshots[0] == snapshots[1], 'A revision-only change rebuilt installation layers'
    finally:
        subprocess.run(['podman', 'image', 'rm', *tags], capture_output=True)
