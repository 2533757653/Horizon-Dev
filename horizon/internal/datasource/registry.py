"""Data source registry stub."""

from typing import Optional


class DataSourceRegistry:
    """Registry stub."""

    def __init__(self):
        self._sources = {}

    def register(self, name: str, adapter):
        self._sources[name] = adapter

    def get(self, name: str):
        return self._sources.get(name)