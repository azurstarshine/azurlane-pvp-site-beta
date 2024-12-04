"""
Functions and information for working with external data sources, including
the wiki and the resources repository.
"""

from datetime import timedelta
import json
import re
from urllib.parse import urlparse

from mediawiki import MediaWiki
from mediawiki import MediaWikiPage
import more_itertools as mit

from . import GAME_RESOURCES_DIR
from .types import EQUIP_RARITY_BY_STARS
from .types import Equipment
from .types import Ship
from .types import ShipRarity
from .types import HullClass
from .types import TechLevel


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


def load_external_data(ship_skin_data, page: MediaWikiPage) -> ExternalData:
    categories = {c.lower() for c in page.categories}

    recognized = categories.intersection(DATA_TYPE_CATEGORIES)
    if len(recognized) != 1:
        raise ValueError(f'Unable to determine type of data for {page.title}')

    data_type = mit.one(recognized)

    resolved_url = urlparse(page.url)

    if data_type == SHIP_CATEGORY:
        available_gids = [
            int(m)
            # Python automatically extracts the capture group
            for m in re.findall(r'\|\s*groupid\s*=\s*(\d+).*?\|', page.wikitext, re.IGNORECASE | re.DOTALL)
        ]

        gid = mit.one(
            available_gids,
            ValueError(f'No GroupID found in {page.title}'),
            ValueError(f'Multiple GroupIDs found in {page.title}: {available_gids}')
        )

        retrofit = RETROFIT_CATEGORY in categories

        rarity_cat = categories.intersection(SHIP_RARITY_BY_CATEGORY)
        rarity_cat = mit.one(
            rarity_cat,
            ValueError(f'No rarity category found for {page.title}'),
            ValueError(f'Multiple rarity categories for {page.title}: {rarity_cat}'),
        )
        rarity = SHIP_RARITY_BY_CATEGORY[rarity_cat]
        if retrofit:
            rarity = rarity.retrofit_rarity

        hull_class_cats = categories.intersection(HULL_CLASS_BY_CATEGORY)
        if retrofit and len(hull_class_cats) == 2:
            if retro_hullclass := re.search(r'\|\s*subtyperetro\s*=([^|]+)\|', page.wikitext, re.IGNORECASE):
                hull_class = HullClass.find_by_long_name(retro_hullclass[1].strip())
            else:
                raise ValueError(
                    f'2 hull type categories found for retrofit ship {page.title} ({hull_class_cats}),'
                    'but unable to find SubtypeRetro data'
                )
        else:
            hull_class = HULL_CLASS_BY_CATEGORY[mit.one(
                hull_class_cats,
                ValueError(f'No hull class category found for {page.title}'),
                ValueError(
                    f'Unable to determine hull class from multiple categories for {page.title}: {hull_class_cats}'
                ),
            )]

        skin_type = 'retrofit' if retrofit else 'default'

        skin = mit.one(
            [s for s in ship_skin_data[str(gid)]['skins'].values() if s['type'].lower() == skin_type],
            ValueError(f'No {skin_type} skin found for {page.title} ({gid})'),
            ValueError(f'Multiple {skin_type} skins found for {page.title} ({gid})'),
        )

        return Ship(
            page.title,
            gid,
            resolved_url.geturl(),
            rarity,
            retrofit,
            hull_class,
            int(skin['id']),
        )
    elif data_type == EQUIPMENT_CATEGORY:
        available_stars = [
            int(m)
            # Python automatically extracts the capture group
            for m in re.findall(r'\|\s*stars\s*=\s*(\d+).*?\|', page.wikitext, re.IGNORECASE | re.DOTALL)
        ]

        if not available_stars:
            raise ValueError('"Stars" parameter not found in {page.title} page text')

        stars = max(available_stars)
        # Validate number of stars
        if stars not in EQUIP_RARITY_BY_STARS:
            raise ValueError(f'{stars} is not a valid number of equipment stars')

        available_tech_levels = [
            TechLevel(int(m))
            # Python automatically extracts the capture group
            for m in re.findall(r'\|\s*tech\s*=\s*T(\d+).*?\|', page.wikitext, re.IGNORECASE | re.DOTALL)
        ]

        if not available_tech_levels:
            raise ValueError('"Tech" parameter not found in {page.title} page text')

        tech_level = max(available_tech_levels)

        if len(available_tech_levels) > 1:
            resolved_url = resolved_url._replace(fragment=tech_level.url_fragment)

        available_image_ids = {
            int(m)
            # Python automatically extracts the capture group
            for m in re.findall(r'\|\s*Image\s*=\s*(\d+).*?\|', page.wikitext, re.IGNORECASE | re.DOTALL)
        }

        image_id = mit.one(
            available_image_ids,
            ValueError(f'No image ID found in {page.title}'),
            ValueError(f'Multiple image IDs found in {page.title}: {available_image_ids}')
        )

        return Equipment(
            page.title,
            resolved_url.geturl(),
            stars,
            tech_level,
            image_id,
        )
    else:
        raise NotImplementedError(f'Extracting data from {data_type} not yet implemented')


def load_skin_data():
    skin_data_path = GAME_RESOURCES_DIR / 'ship_skin.json'

    if not skin_data_path.is_file():
        raise Exception(f'{skin_data_path} does not exist or is not a file. Update gamefiles.')

    with open(skin_data_path, encoding='utf-8') as f:
        return json.load(f)
