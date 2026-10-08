#!/bin/bash
set -uo pipefail
python3 - <<'PY'
import json
import shutil
import subprocess
from pathlib import Path

expected={'schema':3,'routes':[{'id':f'route-{n:03}','path':f'/v3/items/{n}','quota':17*n+3} for n in range(64)]}
try:
    assert json.loads(Path('/app/sources/routes.json').read_text())==expected, 'release input changed'
    assert json.loads(Path('/app/dist/routes.bundle.json').read_text())==expected, 'submitted bundle is stale or incomplete'
    shutil.rmtree('/app/dist')
    with open('/tmp/verifier-build.log','w') as out:
        p=subprocess.run(['bash','/app/build.sh'],cwd='/app',stdout=out,stderr=subprocess.STDOUT,timeout=20)
    assert p.returncode==0, 'clean build failed'
    assert json.loads(Path('/app/dist/routes.bundle.json').read_text())==expected, 'clean build did not reproduce release'
except Exception as exc:
    print(type(exc).__name__+': '+str(exc));raise SystemExit(1)
print('Release bundle and clean rebuild verified.')
PY
