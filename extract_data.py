from collections import defaultdict
from collections.abc import Collection
from collections.abc import Mapping
from collections.abc import Sequence
import csv
import dataclasses
from dataclasses import dataclass
from dataclasses import field
from datetime import timedelta
import enum
from enum import Enum
from functools import total_ordering
from functools import partial
from itertools import islice
import json
from pathlib import Path
import re
from types import MappingProxyType
# from typing import assert_never
from urllib.parse import urlparse
from urllib.parse import unquote as urlunquote
# from urllib.parse import ParseResult as ParsedUrl
import warnings

import more_itertools as mit
# import mwclient
from mediawiki import MediaWiki
from mediawiki import MediaWikiPage


HYPERLINK_PATTERN = re.compile(r'=HYPERLINK\("([^"]+)"\s*,\s*"([^"]+)"\)', flags=re.IGNORECASE)


def load_cells(p) -> list[list[str]]:
    with open(p, encoding='utf-8') as f:
        return list(csv.reader(f))


# def find_hyperlinks(p):
    # with open(p, encoding='utf-8') as f:
        # reader = csv.reader(f)

        # return [
            # cell
            # for row in reader
            # for cell in row
            # if cell.lower().startswith('=hyperlink')
        # ]


# def get_client():
    # api_url = urlparse('https://azurlane.koumakan.jp/w/api.php')

    # return mwclient.Site(
        # api_url.netloc,
        # scheme=api_url.scheme,
        # path=api_url.path.removesuffix('api.php').removesuffix('/') + '/',
    # )


def get_client():
    return MediaWiki(
        'https://azurlane.koumakan.jp/w/api.php',
        rate_limit=True,
        rate_limit_wait=timedelta(seconds=.25),
        user_agent='custom script/0.0 azurstarshine (Please contact me if there is a problem.)'
    )


def to_json_serializable(o):
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)

    if isinstance(o, Enum):
        return o.name

    raise TypeError(f'Cannot serialize {o} {type(o).__name__})')


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
    url: str
    rarity: ShipRarity
    retrofitted: bool
    hull_class: HullClass


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
    # equip_type: str

    @property
    def rarity(self) -> EquipmentRarity:
        return EQUIP_RARITY_BY_STARS[self.stars]


AnyData = Ship | Equipment


class EquipmentRank(Enum):
    OPTIMAL = enum.auto()
    VIABLE = enum.auto()
    SITUATIONAL = enum.auto()


@dataclass(frozen=True)
class EquipWithRank:
    equip: Equipment
    rank: EquipmentRank


@dataclass
class ShipEquipment:
    ship: Ship
    description: str | None = None
    slots: dict[int, Sequence[EquipWithRank]] = field(default_factory=lambda: defaultdict(list))

    def validate(self):
        if not self.ship:
            raise ValueError('No ship')

        if not self.description:
            raise ValueError('No description')

        if not self.slots:
            raise ValueError('No equipment slot data')

        EXPECTED_SLOT_KEYS = set(range(1,5+1))
        missing = EXPECTED_SLOT_KEYS.difference(self.slots.keys())
        extra = set(self.slots.keys()) - EXPECTED_SLOT_KEYS

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


    def __repr__(self):
        data_repr = ', '.join([
            f'ship={self.ship.name}',
            f'description={self.desc_preview}',
            'slots=' + ','.join([
                f'{slot}: ' + ','.join([e.name for e in equips])
                for slot, equips in self.slots.items()
            ]),
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

    def __init__(self):
        self._data_cache = {}
        self._nicknames = defaultdict(set)

    def _fetch_data(self, page: MediaWikiPage) -> AnyData:
        categories = {c.lower() for c in page.categories}

        recognized = categories.intersection(DATA_TYPE_CATEGORIES)
        if len(recognized) != 1:
            raise ValueError(f'Unable to determine type of data for {page.title}')

        data_type = mit.one(recognized)

        resolved_url = urlparse(page.url)

        if data_type == SHIP_CATEGORY:
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

            return Ship(
                page.title,
                resolved_url.geturl(),
                rarity,
                retrofit,
                hull_class,
            )
        elif data_type == EQUIPMENT_CATEGORY:
            available_stars = [
                int(m)
                for m in re.findall(r'\|\s*stars\s*=\s*(\d+).*?\|', page.wikitext, re.IGNORECASE | re.DOTALL)
            ]

            if not available_stars:
                raise ValueError('"Stars" parameter not found in {page.title} page text')

            stars = max(available_stars)
            # Validate number of stars
            EQUIP_RARITY_BY_STARS[stars]

            available_tech_levels = [
                TechLevel(int(m))
                for m in re.findall(r'\|\s*tech\s*=\s*T(\d+).*?\|', page.wikitext, re.IGNORECASE | re.DOTALL)
            ]

            if not available_tech_levels:
                raise ValueError('"Tech" parameter not found in {page.title} page text')

            tech_level = max(available_tech_levels)

            if len(available_tech_levels) > 1:
                resolved_url = resolved_url._replace(fragment=tech_level.url_fragment)

            return Equipment(
                page.title,
                resolved_url.geturl(),
                stars,
                tech_level,
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
        result, cached = self._resolve_data(client, url)
        self._nicknames[result.name].add(nickname)
        return result, cached

    @property
    def alldata(self) -> Collection[AnyData]:
        return self._data_cache.values()

    @property
    def nicknames(self) -> MappingProxyType[str, set[str]]:
        return MappingProxyType(self._nicknames)


if '__main__' == __name__:
    fpath = Path('./Azur Lane EN PvP Guide Gear (Main Fleet) 2024-10-20.csv').resolve()
    client = get_client()
    cache = DataCache()
    failed_hyperlinks = []
    hyperlink_count = 0

    all_ship_equip = []
    ship_equip = None

    for rownum, row in enumerate(load_cells(fpath), start=1):
        for colnum, celltext in enumerate(row):
            page_data, cached = None, None

            if celltext.lower().startswith('=hyperlink'):
                # try:
                url, nickname = HYPERLINK_PATTERN.search(celltext).groups()
                page_data, cached = cache.get_data(client, url, nickname)

                if isinstance(page_data, Ship):
                    if ship_equip:
                        ship_equip.validate()
                        all_ship_equip.append(ship_equip)
                        print('Completed ship equip', ship_equip)

                    ship_equip = ShipEquipment(page_data)
                    print()
                elif isinstance(page_data, Equipment):
                    if not ship_equip:
                        raise Exception('Found equipment outside ship')

                    slot = 1 + (colnum - 1) // 2
                    ship_equip.slots[slot].append(page_data)

                print(rownum, colnum, page_data, '(cached)' if cached else '')
                # except Exception as ex:
                #     failed_hyperlinks.append((rownum, colnum, celltext, page_data, cached, ex))
                #     hyperlink_count = hyperlink_count + 1
                #     # Prevents having to wait for whole script if everything is broken
                #     if hyperlink_count >= 10 and len(failed_hyperlinks) / hyperlink_count >= 0.75:
                #         print('Failed hyperlink at', *failed_hyperlinks[-1])
                #         raise
            elif ship_equip:
                if celltext.lower().startswith('=image'):
                    print(rownum, colnum, 'is an image')
                elif celltext:
                    # Not empty, but not an image. Must be description.

                    if ship_equip.description:
                        # Programming error. Unknown content type
                        raise Exception(f'Treating second cell at ({rownum}, {colnum}) as description for {ship_equip.ship.name}')

                    ship_equip.description = celltext
                    print(rownum, colnum, 'Description:', ship_equip.desc_preview)
                elif not celltext:
                    print(rownum, colnum, 'is empty')
                else:
                    # Inside a ship, but no idea what this cell contains
                    raise NotImplementedError(f'Unrecognized cell content at ({rownum}, {colnum})')
            else:
                print('No ship found yet')

        # if len(all_ship_equip) >= 5:
            # break

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

    print()
    print('Failed hyperlinks:')
    for f in failed_hyperlinks:
        print(*f)


    print()
    print('Equipment loadouts:')
    for se in all_ship_equip:
        print(se)


    pvp_json_dump = partial(json.dump, indent=4, default=to_json_serializable)

    equipment = [d for d in cache.alldata if isinstance(d, Equipment)]
    with open(Path('./_data/equipment.json'), 'w', encoding='utf-8') as f:
        pvp_json_dump(equipment, f)
        print('Wrote', f.name)

    ships = [d for d in cache.alldata if isinstance(d, Ship)]
    with open(Path('./_data/ships.json'), 'w', encoding='utf-8') as f:
        pvp_json_dump(ships, f)
        print('Wrote', f.name)