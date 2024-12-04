"""
Functions and information for working with external data sources, including
the wiki and the resources repository.
"""

from datetime import timedelta
import json

from mediawiki import MediaWiki

from . import GAME_RESOURCES_DIR
from .types import Equipment
from .types import Ship
from .types import ShipRarity
from .types import HullClass


def get_wiki_client() -> MediaWiki:
    return MediaWiki(
        'https://azurlane.koumakan.jp/w/api.php',
        rate_limit=True,
        rate_limit_wait=timedelta(seconds=.25),
        user_agent='custom script/0.0 PVP site data maintenance (Please contact azurstarshine if there is a problem.)'
    )


SHIP_RARITY_BY_CATEGORY = {r.long_name.lower() + ' ships': r for r in ShipRarity}

RETROFIT_CATEGORY = 'ships with retrofit'

def hull_class_category_name(hc: HullClass):
    name, *clarifier = hc.long_name.lower().split('(', 1)
    cat_name = name.rstrip() + 's'

    if clarifier:
        cat_name += ' (' + ''.join(clarifier)

    return cat_name


HULL_CLASS_BY_CATEGORY = {hull_class_category_name(hc): hc for hc in HullClass}

ExternalData = Ship | Equipment

EQUIPMENT_CATEGORY = 'equipment'
SHIP_CATEGORY = 'ships'

DATA_TYPE_CATEGORIES = {EQUIPMENT_CATEGORY, SHIP_CATEGORY}


def load_skin_data():
    skin_data_path = GAME_RESOURCES_DIR / 'ship_skin.json'

    if not skin_data_path.is_file():
        raise Exception(f'{skin_data_path} does not exist or is not a file. Update gamefiles.')

    with open(skin_data_path, encoding='utf-8') as f:
        return json.load(f)
