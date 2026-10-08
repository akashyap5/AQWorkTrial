#!/bin/sh
python3 - <<'PY'
import json
from pathlib import Path
import subprocess

expected_import = {
    "stable-api": {"target": "api-2026.10", "enabled": True},
    "canary-worker": {"target": "worker-2026.10-rc2", "enabled": False},
    "metrics-reader": {"target": "metrics-2026.10", "enabled": True},
}
expected = {
    "legacy-reports": {"target": "reports-2024.11", "enabled": False},
    "internal-audit": {"target": "audit-2026.06", "enabled": True},
    **expected_import,
}
consumer_source = '''"""A cold-start consumer of the committed local catalog."""
import json
from pathlib import Path

CATALOG = Path(__file__).resolve().parent / "data" / "catalog.json"


def load_catalog():
    return json.loads(CATALOG.read_text())["records"]


if __name__ == "__main__":
    print(json.dumps(load_catalog(), sort_keys=True))
'''
try:
    snapshot = json.loads(Path('/app/data/catalog.json').read_text())
    manifest = json.loads(Path('/app/import.json').read_text())
    assert snapshot['schema'] == 1 and snapshot['records'] == expected
    assert manifest == expected_import
    assert Path('/app/consumer.py').read_text() == consumer_source
    child = subprocess.run(['python3', '/app/consumer.py'], text=True,
                           capture_output=True, timeout=5, cwd='/app')
    assert child.returncode == 0 and json.loads(child.stdout) == expected
except Exception:
    print(0)
else:
    print(1)
PY
