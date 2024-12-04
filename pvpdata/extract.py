"""
Program to extract data from the spreadsheet version of the guide and
generate data for the site.

This program reads from an HTML export of the spreadsheet. It is only tested
against an export from LibreOffice 24.8.2.1.
"""

from collections import defaultdict
from collections.abc import Collection
from collections.abc import Mapping
import json
from operator import attrgetter
from time import sleep
from types import MappingProxyType
from urllib.parse import unquote as urlunquote
from urllib.parse import urlparse
import warnings

from bs4 import BeautifulSoup
from mediawiki import MediaWiki
import more_itertools as mit

from . import PROJECT_ROOT
from .external import ExternalData
from .external import get_wiki_client
from .external import load_external_data
from .external import load_skin_data
from .sitefiles import get_data_path
from .sitefiles import write_pvp_json_data
from .types import EQUIP_RANK_BY_COLOR
from .types import EquipWithRank
from .types import Equipment
from .types import Ship
from .types import ShipUsage

# Manual overrides for broken page names
PAGE_NAME_FIXES = {
    'F6F_Hellcat_(HVAR_equipped)': 'Grumman F6F Hellcat (HVAR-Mounted)'
}


class DataCache:
    _data_cache: dict[str, ExternalData]
    _nicknames: dict[str, set[str]]
    _ship_skin_data: dict[str, dict]

    def __init__(self, skin_data: dict[str, dict]):
        self._data_cache = {}
        self._nicknames = defaultdict(set)
        self._ship_skin_data = skin_data

    # Returns data and whether it came from cache or not
    def _resolve_data(self, client: MediaWiki, url: str) -> tuple[ExternalData, bool]:
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

        result = load_external_data(self._ship_skin_data, p)

        self._data_cache[result.name] = result
        if page_name != result.name:
            self._data_cache[page_name] = result
        if original_page_name != page_name:
            self._data_cache[original_page_name] = result

        if url.fragment and not urlparse(result.url).fragment:
            warnings.warn(f'url fragment lost: {url.fragment}')

        return result, False

    def get_data(self, client: MediaWiki, url: str, nickname: str) -> tuple[ExternalData, bool]:
        """
        Fetches data, using cache if possible and reading from wiki if not.

        Returns: data and whether data was cached
        """
        result, cached = self._resolve_data(client, url)
        self._nicknames[result.name].add(nickname)
        return result, cached

    @property
    def alldata(self) -> Collection[ExternalData]:
        return set(self._data_cache.values())

    @property
    def nicknames(self) -> MappingProxyType[str, set[str]]:
        return MappingProxyType(self._nicknames)


def extract_data_sheets_value(cell):
    json_text = cell.attrs.get('data-sheets-value')

    if not json_text:
        return None

    try:
        data_sheets_value = json.loads(json_text)
    except json.JSONDecodeError:
        return None

    if not isinstance(data_sheets_value, Mapping):
        return None

    return data_sheets_value.get('2')


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
                        and (data_sheets_val := extract_data_sheets_value(cell))
                    ):
                        # Not empty, no link, not an image, and has JSON in value attr.
                        # Must be description?

                        if current_usage.description:
                            # Programming error. Need to distinguish description better.
                            raise Exception(f'Treating cell at ({rownum}, {colnum}) as second description for {current_usage.ship.name}')

                        # Using the parsed JSON from data-sheets-value preserves all newlines
                        # and other characters the HTML escapes without having to transform it back.
                        # Convert manual bullets to Markdown list
                        current_usage.description = data_sheets_val.replace('\u2022', '*')
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

    client = get_wiki_client()
    cache = DataCache(load_skin_data())

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
        keyfunc=type,
        # Ensure output is sorted to minimize diffs
        # dict preserves insertion order in current version of Python.
        reducefunc=lambda typegroup: {d.name: d for d in sorted(typegroup, key=attrgetter('name'))}
    )
    for t, data in data_by_types.items():
        write_pvp_json_data(get_data_path(t), data)

    write_pvp_json_data(get_data_path(type(usages[0])), usages)


if '__main__' == __name__:
    main()
