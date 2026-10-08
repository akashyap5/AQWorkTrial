#!/bin/bash
set -euo pipefail
cd /app
python3 - <<'PY'
import json
from pathlib import Path
p=Path('packaging.json');d=json.loads(p.read_text());d['accepted_schema']=3;p.write_text(json.dumps(d)+'\n')
p=Path('build.sh');p.write_text(p.read_text().replace('#!/bin/bash\n', '#!/bin/bash\nset -euo pipefail\n', 1))
PY
bash build.sh >/tmp/release-build.log 2>&1
