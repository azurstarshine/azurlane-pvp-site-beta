from collections import defaultdict
from collections.abc import Collection
from collections.abc import Mapping
from collections.abc import Sequence
import dataclasses
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta
import enum
from enum import Enum
from functools import total_ordering
import json
from pathlib import Path
import re
from time import sleep
from types import MappingProxyType
from typing import Any
from urllib.parse import urlparse
from urllib.parse import unquote as urlunquote
import warnings

from bs4 import BeautifulSoup
import more_itertools as mit
from mediawiki import MediaWiki
from mediawiki import MediaWikiPage


PROJECT_ROOT = Path(__file__).resolve().parent
SITE_SOURCE = PROJECT_ROOT / 'sitesource'


def get_client():
    return MediaWiki(
        'https://azurlane.koumakan.jp/w/api.php',
        rate_limit=True,
        rate_limit_wait=timedelta(seconds=.25),
        user_agent='custom script/0.0 azurstarshine (Please contact me if there is a problem.)'
    )


class RarityColor(Enum):
    GRAY = enum.auto()
    BLUE = enum.auto()
    PURPLE = enum.auto()
    GOLD = enum.auto()
    RAINBOW = enum.auto()


class ShipRarity(Enum):
    N  = ('Normal', RarityColor.GRAY, True, False)
    R  = ('Rare', RarityColor.BLUE, True, False)
    E  = ('Elite', RarityColor.PURPLE, True, False)
    SR = ('Super Rare', RarityColor.GOLD, True, False)
    UR = ('Ultra Rare', RarityColor.RAINBOW, False, False)

    PR = ('Priority', RarityColor.GOLD, False, True)
    DR = ('Decisive', RarityColor.RAINBOW, False, True)

    def __init__(self,
        long_name: str,
        color: RarityColor,
        can_retrofit: bool,
        research: bool,
    ):
        self.code = self.name
        self.long_name = long_name
        self.color = color
        self.can_retrofit = can_retrofit
        self.research = research

        self.category_name = self.long_name.lower() + ' ships'

    @property
    def retrofit_rarity(self):
        if not self.can_retrofit:
            return None

        match self:
            case self.N:
                return self.R
            case self.R:
                return self.E
            case self.E:
                return self.SR
            case self.SR:
                return self.UR
            case _:
                raise NotImplementedError(f'Programming error: no retrofit rarity found. Fix {type(self).__name__} enum definition.')

    def __str__(self):
        return self.long_name

    def __repr__(self):
        return f'<{type(self).__name__}:{self.code}>'

SHIP_RARITY_BY_CATEGORY = {r.category_name: r for r in ShipRarity}

RETROFIT_CATEGORY = 'ships with retrofit'


class HullClass(Enum):
    CV  = ('Aircraft carrier')
    CVL = ('Light aircraft carrier')
    BBV = ('Aviation battleship')
    BC  = ('Battlecruiser')
    BB  = ('Battleship')
    DD  = ('Destroyer')
    CA  = ('Heavy cruiser')
    CB  = ('Large cruiser')
    CL  = ('Light cruiser')
    BM  = ('Monitor')
    AE  = ('Munition ship')
    AR  = ('Repair ship')
    IXs = ('Sailing Frigate (Submarine)', 'Sailing Frigates (Submarine)')
    IXv = ('Sailing Frigate (Vanguard)', 'Sailing Frigates (Vanguard)')
    IXm = ('Sailing Frigate (Main)', 'Sailing Frigates (Main)')
    SS  = ('Submarine')
    SSV = ('Submarine carrier')

    def __init__(self, long_name, cat_name=None):
        if not cat_name:
            cat_name = long_name + 's'
        cat_name = cat_name.lower()

        self.code = self.name
        self.long_name = long_name
        self.category_name = cat_name

    @classmethod
    def find_by_long_name(cls, search_name):
        search_name = search_name.lower()

        for hc in cls:
            if hc.long_name.lower() == search_name:
                return hc

        raise ValueError('No hull class found with name {search_name}')

    def __str__(self):
        return self.long_name

    def __repr__(self):
        return f'<{type(self).__name__}: {self.code}>'


HULL_CLASS_BY_CATEGORY = {hc.category_name: hc for hc in HullClass}


@dataclass(frozen=True)
class Ship:
    name: str
    gid: int
    url: str
    rarity: ShipRarity
    retrofitted: bool
    hull_class: HullClass
    skin_id: int


class EquipmentRarity(Enum):
    N  = ('Normal', RarityColor.GRAY)
    R  = ('Rare', RarityColor.BLUE)
    E  = ('Elite', RarityColor.PURPLE)
    SR = ('Super Rare', RarityColor.GOLD)
    UR = ('Ultra Rare', RarityColor.RAINBOW)

    def __init__(self,
        long_name: str,
        color: RarityColor,
    ):
        self.code = self.name
        self.long_name = long_name
        self.color = color

    def __str__(self):
        return f'{self.long_name} {self.stars}*'

    def __repr__(self):
        return f'<{type(self).__name__}:{self.code} {self.stars}*>'

EQUIP_RARITY_BY_STARS = {
    1: EquipmentRarity.N,
    2: EquipmentRarity.N,
    3: EquipmentRarity.R,
    4: EquipmentRarity.E,
    5: EquipmentRarity.SR,
    6: EquipmentRarity.UR,
}

@total_ordering
class TechLevel(Enum):
    T1 = 1
    T2 = 2
    T3 = 3
    T0 = 0

    def __lt__(self, other):
        if type(self) is not type(other):
            return NotImplemented

        if self.value == 0:
            # Other value doesn't matter; it cannot be less than 0
            return False
        elif other.value == 0:
            # self is not 0, so it must be less than 0
            return True
        else:
            return self.value < other.value

    @property
    def url_fragment(self):
        return f'Type_{self.value}-0'

    def __str__(self):
        return f'T{self.value}'

    def __repr__(self):
        return f'<{type(self).__name__}:{self}>'


@dataclass(frozen=True)
class Equipment:
    name: str
    url: str
    stars: int
    tech_level: TechLevel
    image_id: int
    # equip_type: str

    @property
    def rarity(self) -> EquipmentRarity:
        return EQUIP_RARITY_BY_STARS[self.stars]


AnyData = Ship | Equipment


@total_ordering
class EquipmentRank(Enum):
    OPTIMAL = ('#5AD766', 1)
    VIABLE = ('#FFCE32', 2)
    SITUATIONAL = ('#E02F2F', 3)

    def __init__(self, bgcolor, numeric):
        self.bgcolor = bgcolor.lower()
        self.numeric = numeric

    def __lt__(self, other):
        if type(self) is not type(other):
            return NotImplemented

        return self.numeric < other.numeric

    def __str__(self):
        return self.name

    def __repr__(self):
        return f'<{type(self).__name__}:{self}>'

EQUIP_RANK_BY_COLOR = {r.bgcolor: r for r in EquipmentRank}



@dataclass(frozen=True)
class EquipWithRank:
    equip: Equipment
    rank: EquipmentRank

    def __str__(self):
        return f'{self.equip.name} ({self.rank.name.lower()})'


EQUIPMENT_SLOT_KEYS = {1, 2, 3, 'aux'}

@dataclass
class ShipUsage:
    ship: Ship
    description: str | None = None
    slots: dict[int | str, Sequence[EquipWithRank]] = field(default_factory=lambda: defaultdict(list))

    def sort_slots(self):
        for s in self.slots.values():
            s.sort(key=lambda ewr: ewr.rank)

    def validate(self):
        if not self.ship:
            raise ValueError('No ship')

        if not self.description:
            raise ValueError('No description')

        if not self.slots:
            raise ValueError('No equipment slot data')

        missing = EQUIPMENT_SLOT_KEYS.difference(self.slots.keys())
        extra = set(self.slots.keys()) - EQUIPMENT_SLOT_KEYS

        if missing or extra:
            raise ValueError(f'{missing} slots missing, extra slots {extra}')

        if empty_slots := [slot for slot, equip in self.slots.items() if not equip]:
            raise ValueError(f'Emtpy slots {empty_slots}')

    @property
    def desc_preview(self):
        preview = self.description
        preview = ' '.join(preview.splitlines())

        if len(preview) < 30:
            return preview
        else:
            return preview[:30] + '...'


    def __str__(self):
        data_repr = ', '.join([
            f'ship={self.ship.name}',
            f'description={self.desc_preview}',
            'slots={'
            + ','.join([
                f'{slot}: [' + ','.join([str(e) for e in equips]) + ']'
                for slot, equips in self.slots.items()
            ])
            + '}',
        ])

        return f'{type(self).__name__}({data_repr})'


EQUIPMENT_CATEGORY = 'equipment'
SHIP_CATEGORY = 'ships'

DATA_TYPE_CATEGORIES = {EQUIPMENT_CATEGORY, SHIP_CATEGORY}

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


def to_json_serializable(o):
    if isinstance(o, EquipWithRank):
        return {'name': o.equip.name, 'rank': str(o.rank)}

    if isinstance(o, ShipUsage):
        return {
            'ship': o.ship.name,
            'description': o.description,
            'equipment': o.slots,
        }

    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)

    if isinstance(o, Enum):
        return o.name

    raise TypeError(f'Cannot serialize {o} {type(o).__name__})')


def write_pvp_json_data(path: Path, data: Any):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, default=to_json_serializable)
        print('Wrote', f.name)


if '__main__' == __name__:
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

    if failures:
        print('Failed to load:')
        for f in failures:
            print(*f)

    data_by_types = mit.bucket(cache.alldata, lambda d: type(d).__name__.lower())
    for t in data_by_types:
        data = {d.name: d for d in data_by_types[t]}

        write_pvp_json_data((SITE_SOURCE / f'_data/{t}.json'), data)

    write_pvp_json_data((SITE_SOURCE / '_data/ship_usage.json'), usages)
