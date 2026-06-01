"""PairList registry and plugin discovery."""

import logging
import pkgutil
import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import PairList

logger = logging.getLogger(__name__)


class PairListRegistry:
    """Discovers and manages PairList plugins."""

    def __init__(self) -> None:
        self._pairlists: dict[str, type["PairList"]] = {}

    def discover(self, package_name: str = "horizon.pairlists") -> None:
        """Scan the pairlists package and register all PairList subclasses."""
        try:
            package = importlib.import_module(package_name)
        except ImportError as e:
            logger.warning("Could not import pairlists package: %s", e)
            return

        for _, module_name, _ in pkgutil.iter_modules(package.__path__):
            full_name = f"{package_name}.{module_name}"
            try:
                module = importlib.import_module(full_name)
            except Exception as e:
                logger.error("Failed to import pairlist module %s: %s", full_name, e)
                continue

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and issubclass(attr, PairList) and attr is not PairList):
                    instance = attr()
                    name = instance.name
                    if name in self._pairlists:
                        logger.warning("PairList '%s' skipped — already registered", name)
                    else:
                        self._pairlists[name] = attr
                        logger.info("Discovered PairList: %s (%s)", name, full_name)

    def list_all(self) -> list[dict]:
        """List all registered PairLists with their metadata."""
        result = []
        for name, cls in sorted(self._pairlists.items()):
            instance = cls()
            result.append({"name": instance.name, "description": instance.description})
        return result

    def create(self, name: str) -> "PairList":
        """Create an instance of the named PairList."""
        cls = self._pairlists[name]
        return cls()

    def exists(self, name: str) -> bool:
        return name in self._pairlists