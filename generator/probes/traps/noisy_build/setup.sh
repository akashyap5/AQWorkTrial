#!/bin/bash
set -euo pipefail
cd /app
mkdir -p dist sources
python3 - <<'PY'
import json
from pathlib import Path
rows = [{'id': f'route-{n:03}', 'path': f'/v3/items/{n}', 'quota': 17*n+3} for n in range(64)]
Path('sources/routes.json').write_text(json.dumps({'schema': 3, 'routes': rows}, separators=(',', ':'))+'\n')
Path('packaging.json').write_text(json.dumps({'accepted_schema': 2}, indent=2)+'\n')
Path('dist/routes.bundle.json').write_text(json.dumps({'schema': 2, 'routes': rows[:32]})+'\n')
PY
chmod +x build.sh
