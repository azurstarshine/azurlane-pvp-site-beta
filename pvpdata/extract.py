"""
Program to extract data from the spreadsheet version of the guide and
generate data for the site.

This program reads from an HTML export of the spreadsheet. It is only tested
against an export from LibreOffice 24.8.2.1.
"""

from collections.abc import Iterator
from collections.abc import Mapping
from dataclasses import dataclass
import json
from operator import attrgetter
from time import sleep
from urllib.parse import unquote as urlunquote
from urllib.parse import urlparse
from urllib.parse import ParseResult as UrlParseResult
import warnings

import bs4
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
from .util import LazyValue
from .util import MultikeyCache

# Manual overrides for broken page names
PAGE_NAME_FIXES = {
    'F6F_Hellcat_(HVAR_equipped)': 'Grumman F6F Hellcat (HVAR-Mounted)'
}


@dataclass(frozen=True)
class CellLocation:
    row: int
    column: int

    def __str__(self):
        return f'({self.row}, {self.column})'


def table_cells(table: bs4.Tag) -> Iterator[tuple[CellLocation, bs4.Tag]]:
    for rownum, row in enumerate(table.find_all('tr'), start=1):
        for colnum, cell in enumerate(row.find_all('td'), start=1):
            yield CellLocation(row=rownum, column=colnum), cell


def extract_page_name(wikiurl: UrlParseResult) -> str:
    return urlunquote(wikiurl.path.removeprefix('/').removeprefix('wiki').removeprefix('/'))


def extract_data_sheets_value(cell) -> str | None:
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
    client: MediaWiki,
    ship_skin_data,
    cache: MultikeyCache[str, ExternalData],
    table: bs4.Tag,
):
    usages = []
    failures = []
    current_usage = None

    for loc, cell in table_cells(table):
        try:
            link_children = cell.find_all('a')

            if len(link_children) == 1:
                #region External resource cell
                urltext: str = link_children[0].attrs['href']
                nickname: str = link_children[0].text

                url: UrlParseResult = urlparse(urltext)

                raw_page_name = extract_page_name(url)
                page_name = PAGE_NAME_FIXES.get(raw_page_name, raw_page_name)
                lazypage = LazyValue(lambda: client.page(page_name, auto_suggest=False))

                # Use a generator function to avoid loading the page if not needed
                def names():
                    if page_name != raw_page_name:
                        yield raw_page_name
                    yield page_name
                    # Resolves wiki redirects
                    yield lazypage.value.title

                page_data, cached = cache.get(
                    names(),
                    # Must NOT use partial to ensure page is loaded lazily
                    lambda: load_external_data(ship_skin_data, nickname, lazypage.value)
                )

                if page_data.nickname != nickname:
                    warnings.warn(f'Nickname mismatch: {page_data.nickname} (first) != {nickname} (new) ({page_data.name} data)')

                loaded_fragment = urlparse(page_data.url).fragment
                if url.fragment != loaded_fragment:
                    if not loaded_fragment:
                        warnings.warn(f'url fragment lost: {url.fragment}')
                    elif not url.fragment:
                        warnings.warn(f'url fragment added: {loaded_fragment}')
                    else:
                        warnings.warn(f'url fragment changed: {url.fragment} to {loaded_fragment}')

                if isinstance(page_data, Ship):
                    if current_usage:
                        current_usage.sort_slots()
                        current_usage.validate()
                        usages.append(current_usage)
                        print('Completed ship usage', current_usage)
                        sleep(1)

                    current_usage = ShipUsage(page_data)
                    print()
                elif isinstance(page_data, Equipment):
                    if current_usage:
                        slot = loc.column // 2
                        if slot in (4,5):
                            slot = 'aux'

                        rank = EQUIP_RANK_BY_COLOR[cell.attrs['bgcolor'].lower()]

                        current_usage.slots[slot].append(EquipWithRank(page_data, rank))
                    else:
                        warnings.warn(f'Found equipment outside ship: {page_data.name}')

                print(loc, page_data, '(cached)' if cached else '')
                #endregion
            elif current_usage:
                if cell.attrs.get('data-sheets-formula', '').lower().startswith('=image'):
                    print(loc, 'is an image')
                elif (
                    cell.text
                    and not link_children
                    and (data_sheets_val := extract_data_sheets_value(cell))
                ):
                    # Not empty, no link, not an image, and has JSON in value attr.
                    # Must be description?

                    if current_usage.description:
                        # Programming error. Need to distinguish description better.
                        raise Exception(f'Treating cell at {loc} as second description for {current_usage.ship.name}')

                    # Using the parsed JSON from data-sheets-value preserves all newlines
                    # and other characters the HTML escapes without having to transform it back.
                    # Convert manual bullets to Markdown list
                    current_usage.description = data_sheets_val.replace('\u2022', '*')
                    print(loc, 'Description:', current_usage.desc_preview)
                elif not cell.text:
                    # Check this last to avoid accidentally missing other possibilities
                    # At present, images have an error message as the cell value, but this could
                    # potentially change to an empty value later, so check attribute based
                    # possibilities first.
                    print(loc, 'is empty')
                else:
                    # Inside a ship, but no idea what this cell contains
                    raise NotImplementedError(f'Unrecognized cell content at {loc}')
            else:
                print(loc, 'No ship found yet')
        except Exception as ex:
            print('Error:', loc, cell, ex)
            sleep(5)
            failures.append((loc, cell, current_usage, ex))
            # Skip over current ship
            current_usage = None

    return usages, failures


def main():
    with open((PROJECT_ROOT / 'exports/Azur Lane EN PvP Guide 2024-10-20.html').resolve(), encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'lxml')

    client = get_wiki_client()
    ship_skin_data = load_skin_data()
    cache = MultikeyCache()

    usages = []
    failures = []

    for table_name in ['table4', 'table5']:
        table_element: bs4.Tag = soup.find('a', {'name': table_name}).find_next('table')
        cur_uses, cur_fails = parse_equip_table(client, ship_skin_data, cache, table_element)
        usages.extend(cur_uses)
        failures.extend((table_name, *f) for f in cur_fails)

    print()

    print()
    print('Conflicting nicknames:')
    by_nickname = mit.map_reduce(
        cache.allvalues,
        keyfunc=attrgetter('nickname'),
        valuefunc=attrgetter('name'),
    )
    for nickname, names in by_nickname.items():
        if len(names) > 1:
            print('{nickname}: ' + ','.join(names))

    print()
    print('Failed to load:')
    for f in failures:
        print(*f)

    print()
    data_by_types = mit.map_reduce(
        cache.allvalues,
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
