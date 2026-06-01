"""Abstract base class for PairList plugins."""

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite


class PairList(abc.ABC):
    """Abstract base class for PairList plugins."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique identifier for this PairList, e.g. 'top_coins'."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """Short human-readable description."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_pairs(self, db: "aiosqlite.Connection") -> list[str]:
        """Read the exchange_symbols cache, filter, return generic-format symbols."""
        raise NotImplementedError