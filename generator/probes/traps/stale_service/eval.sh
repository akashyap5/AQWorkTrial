#!/bin/sh
set -eu
python3 - <<'PY'
import json
import sys
from pathlib import Path
from urllib.request import urlopen

expected = {
    'application': 'notes-api',
    'revision': '2026.10.07',
    'message': 'Candidate Notes API is live.',
}
try:
    root = Path('/app')
    config = json.loads((root / 'deployment.json').read_text())
    selected = (root / config['release_dir']).resolve()
    assert selected == root / 'releases/candidate', 'candidate release is not selected'
    assert config['port'] == 8080, 'configured port is not 8080'
    installed = json.loads((root / 'deployed/current.json').read_text())
    assert installed == {'port': 8080, 'release': expected}, 'installed release is stale'
    with urlopen('http://127.0.0.1:8080/health', timeout=2) as response:
        assert response.status == 200, 'health endpoint did not return HTTP 200'
    with urlopen('http://127.0.0.1:8080/api/release', timeout=2) as response:
        assert response.status == 200, 'release endpoint did not return HTTP 200'
        assert json.load(response) == expected, 'running process serves the wrong release'
except Exception as error:
    print(f'Deployment check failed: {error}', file=sys.stderr)
    sys.exit(1)
print('Candidate is selected, installed, and serving on port 8080.')
PY
