from collections.abc import Mapping
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from . import DATA_DIR
from .types import Equipment
from .types import Ship
from .types import ShipUsage
from .types import to_json_serializable

DATA_FILE_BASENAMES : Mapping[type, str] = MappingProxyType({
    Ship: 'ship',
    Equipment: 'equipment',
    ShipUsage: 'ship_usage',
})


def get_data_path(datatype: type):
    basename = DATA_FILE_BASENAMES[datatype]
    return DATA_DIR / f'{basename}.json'


def write_pvp_json_data(path: Path, data: Any):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        json.dump(data, f, indent=4, default=to_json_serializable)
        print('Wrote', f.name)
