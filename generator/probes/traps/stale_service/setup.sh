#!/bin/sh
set -eu
cd /app
mkdir -p deployed run
chmod +x bin/deployctl
python3 - <<'PY'
import json
from pathlib import Path

root = Path('/app')
release = json.loads((root / 'releases/previous/release.json').read_text())
(root / 'deployed/current.json').write_text(json.dumps({'port': 8080, 'release': release}) + '\n')
PY
