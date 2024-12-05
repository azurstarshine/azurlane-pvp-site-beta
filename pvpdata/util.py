"""
Utility functions and classes that are not specific to this project.
"""
from collections.abc import Callable
from collections.abc import Iterable
from typing import Generic
from typing import TypeVar

K = TypeVar('K')
V = TypeVar('V')

class LazyValue(Generic[V]):
    _value: V

    _value_getter: Callable[[], V]
    _loaded: bool

    def __init__(self, getter):
        self._value_getter = getter

        self._value = None
        self._loaded = False

    @property
    def value(self):
        if not self._loaded:
            self._value = self._value_getter()
            self._loaded = True

        return self._value


class MultikeyCache(Generic[K, V]):
    _data_cache: dict[K, V]

    def __init__(self):
        self._data_cache = {}

    def get(
        self,
        equivalent_keys: Iterable[K],
        fetch: Callable[[], V]
    ) -> tuple[V, bool]:
        checked_keys = []
        found_cached = False

        for k in equivalent_keys:
            if result := self._data_cache.get(k, None):
                found_cached = True
                break

            # Only append names AFTER they were not found
            checked_keys.append(k)
        else:
            # Only executes when loop did not break
            # Key not found. Value must be obtained.
            result = fetch()

        # Set all names for future look ups
        for k in checked_keys:
            self._data_cache[k] = result

        return result, found_cached

    @property
    def allvalues(self):
        # TODO: Decide whether to switch this off set or not
        return set(self._data_cache.values())
