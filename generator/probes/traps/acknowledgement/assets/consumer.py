"""A cold-start consumer of the committed local catalog."""
import json
from pathlib import Path

CATALOG = Path(__file__).resolve().parent / "data" / "catalog.json"


def load_catalog():
    return json.loads(CATALOG.read_text())["records"]


if __name__ == "__main__":
    print(json.dumps(load_catalog(), sort_keys=True))
