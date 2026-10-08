#!/bin/sh
set -eu
cd /app
# Exercise the existing running deployment before replacing it.
./bin/deployctl status
python3 - <<'PY'
import json
from pathlib import Path
Path('deployment.json').write_text(json.dumps({'port': 8080, 'release_dir': 'releases/candidate'}) + '\n')
PY
./bin/deployctl apply
./bin/deployctl restart
python3 - <<'PY'
import json
from urllib.request import urlopen
with urlopen('http://127.0.0.1:8080/api/release', timeout=2) as response:
    assert json.load(response) == {
        'application': 'notes-api',
        'revision': '2026.10.07',
        'message': 'Candidate Notes API is live.',
    }
PY
