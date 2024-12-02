import json
from pathlib import Path
from typing import Any

from .types import to_json_serializable


def write_pvp_json_data(path: Path, data: Any):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        json.dump(data, f, indent=4, default=to_json_serializable)
        print('Wrote', f.name)
