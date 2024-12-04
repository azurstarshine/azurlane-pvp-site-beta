"""
Data types for data represented in the site's files.
"""

from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
import enum
from enum import Enum
from functools import total_ordering


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


class HullClass(Enum):
    CV  = 'Aircraft carrier'
    CVL = 'Light aircraft carrier'
    BBV = 'Aviation battleship'
    BC  = 'Battlecruiser'
    BB  = 'Battleship'
    DD  = 'Destroyer'
    CA  = 'Heavy cruiser'
    CB  = 'Large cruiser'
    CL  = 'Light cruiser'
    BM  = 'Monitor'
    AE  = 'Munition ship'
    AR  = 'Repair ship'
    IXs = 'Sailing Frigate (Submarine)'
    IXv = 'Sailing Frigate (Vanguard)'
    IXm = 'Sailing Frigate (Main)'
    SS  = 'Submarine'
    SSV = 'Submarine carrier'

    def __init__(self, long_name):
        self.code = self.name
        self.long_name = long_name

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
        return f'{self.long_name}'

    def __repr__(self):
        return f'<{type(self).__name__}:{self.code}>'


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
    slots: dict[int | str, list[EquipWithRank]] = field(default_factory=lambda: defaultdict(list))

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
