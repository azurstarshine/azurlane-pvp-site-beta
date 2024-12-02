from collections import defaultdict
from collections.abc import Collection
import json
from operator import attrgetter
import re
from time import sleep
from types import MappingProxyType
from urllib.parse import unquote as urlunquote
from urllib.parse import urlparse
import warnings

from bs4 import BeautifulSoup
from mediawiki import MediaWiki
from mediawiki import MediaWikiPage
import more_itertools as mit

from . import PROJECT_ROOT
from . import SITE_SOURCE
from .external import AnyData
from .external import DATA_TYPE_CATEGORIES
from .external import EQUIPMENT_CATEGORY
from .external import HULL_CLASS_BY_CATEGORY
from .external import RETROFIT_CATEGORY
from .external import SHIP_CATEGORY
from .external import SHIP_RARITY_BY_CATEGORY
from .external import get_client
from .sitefiles import write_pvp_json_data
from .types import EQUIP_RANK_BY_COLOR
from .types import EQUIP_RARITY_BY_STARS
from .types import EquipWithRank
from .types import Equipment
from .types import HullClass
from .types import Ship
from .types import ShipUsage
from .types import TechLevel

# Manual overrides for broken page names
PAGE_NAME_FIXES = {
    'F6F_Hellcat_(HVAR_equipped)': 'Grumman F6F Hellcat (HVAR-Mounted)'
}


class DataCache:
    _data_cache: dict[str, AnyData]
    _nicknames: dict[str, set[str]]
    _ship_skin_data: dict[str, dict]

    def __init__(self, skin_data: dict[str, dict]):
        self._data_cache = {}
        self._nicknames = defaultdict(set)
        self._ship_skin_data = skin_data

    def _fetch_data(self, page: MediaWikiPage) -> AnyData:
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
            if len(hull_class_cats) <= 1:
                hull_class = HULL_CLASS_BY_CATEGORY[mit.one(hull_class_cats, ValueError(f'No hull class category found for {page.title}'))]
            elif retrofit and len(hull_class_cats) == 2:
                if retro_hullclass := re.search(r'\|\s*subtyperetro\s*=([^|]+)\|', page.wikitext, re.IGNORECASE):
                    hull_class = HullClass.find_by_long_name(retro_hullclass[1].strip())
                else:
                    raise ValueError(
                        f'2 hull type categories found for retrofit ship {page.title} ({hull_class_cats}),'
                        'but unable to find SubtypeRetro data'
                    )
            else:
                ValueError(f'Unable to determine hull class from multiple categories for {page.title}: {hull_class_cats}'),

            skin_type = 'retrofit' if retrofit else 'default'

            skin = mit.one(
                [s for s in self._ship_skin_data[str(gid)]['skins'].values() if s['type'].lower() == skin_type],
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
            EQUIP_RARITY_BY_STARS[stars]

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

    # Returns data and whether it came from cache or not
    def _resolve_data(self, client: MediaWiki, url: str) -> tuple[AnyData, bool]:
        url = urlparse(url)
        original_page_name = urlunquote(url.path.removeprefix('/').removeprefix('wiki').removeprefix('/'))

        if cached := self._data_cache.get(original_page_name, None):
            return cached, True

        page_name = PAGE_NAME_FIXES.get(original_page_name, original_page_name)

        if cached := self._data_cache.get(page_name, None):
            return cached, True

        p = client.page(page_name, auto_suggest=False)

        if cached := self._data_cache.get(p.title, None):
            return cached, True

        # All possible names checked. Data is not cached.

        result = self._fetch_data(p)

        self._data_cache[result.name] = result
        if page_name != result.name:
            self._data_cache[page_name] = result
        if original_page_name != page_name:
            self._data_cache[original_page_name] = result

        if url.fragment and not urlparse(result.url).fragment:
            warnings.warn(f'url fragment lost: {url.fragment}')

        return result, False

    def get_data(self, client: MediaWiki, url: str, nickname: str) -> tuple[AnyData, bool]:
        '''
        Fetches data, using cache if possible and reading from wiki if not.

        Returns: data and whether data was cached
        '''
        result, cached = self._resolve_data(client, url)
        self._nicknames[result.name].add(nickname)
        return result, cached

    @property
    def alldata(self) -> Collection[AnyData]:
        return set(self._data_cache.values())

    @property
    def nicknames(self) -> MappingProxyType[str, set[str]]:
        return MappingProxyType(self._nicknames)


def try_parse_json(json_text):
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        return None


def parse_equip_table(
    table,
    client,
    cache,
):
    usages = []
    failures = []
    current_usage = None

    for rownum, row in enumerate(table.find_all('tr'), start=1):
        for colnum, cell in enumerate(row.find_all('td'), start=1):
            try:
                page_data, cached = None, None

                link_children = cell.find_all('a')

                if len(link_children) == 1:
                    url = link_children[0].attrs['href']
                    nickname = link_children[0].text

                    page_data, cached = cache.get_data(client, url, nickname)

                    if isinstance(page_data, Ship):
                        if current_usage:
                            current_usage.sort_slots()
                            current_usage.validate()
                            usages.append(current_usage)
                            print('Completed ship equip', current_usage)

                        current_usage = ShipUsage(page_data)
                        print()
                    elif isinstance(page_data, Equipment):
                        if current_usage:
                            slot = colnum // 2
                            if slot in (4,5):
                                slot = 'aux'

                            rank = EQUIP_RANK_BY_COLOR[cell.attrs['bgcolor'].lower()]

                            current_usage.slots[slot].append(EquipWithRank(page_data, rank))
                        else:
                            warnings.warn(f'Found equipment outside ship: {page_data.name}')

                    print(rownum, colnum, page_data, '(cached)' if cached else '')
                elif current_usage:
                    if cell.attrs.get('data-sheets-formula', '').lower().startswith('=image'):
                        print(rownum, colnum, 'is an image')
                    elif (
                        cell.text
                        and not link_children
                        and (parsed_value := try_parse_json(cell.attrs.get('data-sheets-value')))
                        and parsed_value.get('2')
                    ):
                        # Not empty, no link, not an image, and has JSON in value attr.
                        # Must be description?

                        if current_usage.description:
                            # Programming error. Need to distinguish description better.
                            raise Exception(f'Treating cell at ({rownum}, {colnum}) as second description for {current_usage.ship.name}')

                        # Using the parsed JSON from data-sheets-value preserves all newlines
                        # and other characters the HTML escapes without having to transform it back.
                        description = parsed_value['2']
                        # Convert manual bullets to Markdown list
                        description = description.replace('\u2022', '*')
                        current_usage.description = description
                        print(rownum, colnum, 'Description:', current_usage.desc_preview)
                    elif not cell.text:
                        # Check this last to avoid accidentally missing other possibilities
                        # At present, images have an error message as the cell value, but this could
                        # potentially change to an empty value later, so check attribute based
                        # possibilities first.
                        print(rownum, colnum, 'is empty')
                    else:
                        # Inside a ship, but no idea what this cell contains
                        raise NotImplementedError(f'Unrecognized cell content at ({rownum}, {colnum})')
                else:
                    print(rownum, colnum, 'No ship found yet')
            except Exception as ex:
                print('Error:', rownum, colnum, cell, ex)
                sleep(5)
                failures.append((rownum, colnum, cell, current_usage, ex))
                # Skip over current ship
                current_usage = None

    return usages, failures


def main():
    with open((PROJECT_ROOT / 'exports/Azur Lane EN PvP Guide 2024-10-20.html').resolve(), encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'lxml')

    skin_data_file = PROJECT_ROOT / 'gamefiles/ship_skin.json'

    if not skin_data_file.is_file():
        raise Exception('{skin_data_file} does not exist or is not a file. Update gamefiles.')

    with open(skin_data_file, encoding='utf-8') as f:
        ship_skin_data = json.load(f)

    client = get_client()
    cache = DataCache(ship_skin_data)

    usages = []
    failures = []

    for table_name in ['table4', 'table5']:
        table_element = soup.find('a', {'name': table_name}).find_next('table')
        cur_uses, cur_fails = parse_equip_table(table_element, client, cache)
        usages.extend(cur_uses)
        failures.extend((table_name, *f) for f in cur_fails)

    print()

    print()
    print('Multiple names:')
    for name, nicknames in cache.nicknames.items():
        if len(nicknames) > 1:
            print(f'{name}: ' + ','.join(nicknames))

    print()
    print('Conflicting nicknames:')
    by_nickname = defaultdict(set)
    for name, nicknames in cache.nicknames.items():
        for n in nicknames:
            by_nickname[n].add(name)

    for nickname, names in by_nickname.items():
        if len(names) > 1:
            print('{nickname}: ' + ','.join(names))

    print('Failed to load:')
    for f in failures:
        print(*f)

    data_by_types = mit.map_reduce(
        cache.alldata,
        keyfunc=lambda d: type(d).__name__.lower(),
        # Ensure output is sorted to minimize diffs
        reducefunc=lambda data: {d.name: d for d in sorted(data, key=attrgetter('name'))}
    )
    for t, data in data_by_types.items():
        write_pvp_json_data((SITE_SOURCE / f'_data/{t}.json'), data)

    write_pvp_json_data((SITE_SOURCE / '_data/ship_usage.json'), usages)


if '__main__' == __name__:
    main()
