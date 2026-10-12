"""Mappers: one per source, turning that source's raw payload into a NormalizedJob.

Extractors decide HOW to get postings; mappers decide what the fields MEAN.
Keeping them apart means a mapping bug can be fixed and re-run from the raw
data in the database, without downloading anything again.
"""

from collections.abc import Callable

from src.mappers.arbeitnow import map_arbeitnow
from src.mappers.base import NormalizedJob
from src.mappers.csv_mapper import map_csv

# Key = source name (sources.name, the same as Extractor.name).
MAPPERS: dict[str, Callable[[dict], NormalizedJob]] = {
    "arbeitnow": map_arbeitnow,
    "csv": map_csv,
}


def get_mapper(source_name: str) -> Callable[[dict], NormalizedJob]:
    """Return the mapper for a source; ValueError if there is none."""
    try:
        return MAPPERS[source_name]
    except KeyError:
        raise ValueError(f"no mapper for source {source_name!r}") from None


__all__ = ["MAPPERS", "NormalizedJob", "get_mapper"]
