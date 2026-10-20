"""Contract for custom Flux data source connectors."""
from abc import ABC, abstractmethod


class BaseConnector(ABC):
    """Return normalized reading dictionaries from a configured source.

    Each record must include site_id, reading_date (YYYY-MM-DD), and energy_kwh.
    production_units is optional. A connector may include `_item_key` to make
    source events idempotent; Flux generates one from the record if omitted.
    """

    @abstractmethod
    def fetch(self, config: dict, seen_item_keys: set[str]) -> list[dict]:
        raise NotImplementedError
