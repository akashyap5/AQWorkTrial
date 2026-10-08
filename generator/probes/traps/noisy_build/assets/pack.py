"""Release bundle packer; configuration is in packaging.json."""
import json
import sys
from pathlib import Path

source = json.loads(Path('/app/sources/routes.json').read_text())
config = json.loads(Path('/app/packaging.json').read_text())
if source['schema'] != config['accepted_schema']:
    print(f"ERROR: manifest schema {source['schema']} does not match packaging accepted_schema={config['accepted_schema']}", file=sys.stderr)
    sys.exit(2)
Path('/app/dist').mkdir(exist_ok=True)
Path('/app/dist/routes.bundle.json').write_text(json.dumps(source, sort_keys=True) + '\n')
print('Wrote dist/routes.bundle.json')
