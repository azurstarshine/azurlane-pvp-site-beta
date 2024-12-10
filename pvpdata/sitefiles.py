"""
Functions and information for working with the site's data files.
"""

from collections.abc import Mapping
import dataclasses
from enum import Enum
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from . import DATA_DIR
from .types import Equipment
from .types import Ship
from .types import ShipUsage


DATA_FILE_BASENAMES : Mapping[type, str] = MappingProxyType({
    Ship: 'ship',
    Equipment: 'equipment',
    ShipUsage: 'ship_usage',
})


def get_data_path(datatype: type):
    basename = DATA_FILE_BASENAMES[datatype]
    return DATA_DIR / f'{basename}.json'


def to_json_serializable(o):
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)

    if isinstance(o, Enum):
        if hasattr(o, 'code'):
            return o.code
        else:
            return str(o)

    raise TypeError(f'Cannot serialize {o} {type(o).__name__})')


def write_pvp_json_data(path: Path, data: Any):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        json.dump(data, f, indent=4, default=to_json_serializable)
        print('Wrote', f.name)
