"""All extractors, and which of them are enabled.

The SOURCES environment variable lists the enabled extractors by name,
comma-separated, e.g. SOURCES=arbeitnow,csv. To add a new source, write an
Extractor subclass and add it to EXTRACTORS below.
"""

import os

from src.extractors.arbeitnow import ArbeitnowExtractor
from src.extractors.base import Extractor, RawPosting
from src.extractors.csv_extractor import CsvExtractor

EXTRACTORS: dict[str, type[Extractor]] = {
    cls.name: cls for cls in (ArbeitnowExtractor, CsvExtractor)
}

DEFAULT_SOURCES = "arbeitnow,csv"


def get_enabled_extractors() -> list[Extractor]:
    """Create one extractor object per name in SOURCES, in the given order.

    An unknown name is a configuration mistake, so it raises ValueError before
    any source runs, instead of being silently ignored.
    """
    value = os.environ.get("SOURCES", DEFAULT_SOURCES)
    names = []
    for name in value.split(","):
        name = name.strip().lower()
        if name and name not in names:  # skip empty parts and repeats
            names.append(name)

    unknown = [name for name in names if name not in EXTRACTORS]
    if unknown:
        raise ValueError(
            f"Unknown source(s) in SOURCES: {', '.join(unknown)}. Known: {', '.join(EXTRACTORS)}"
        )
    if not names:
        raise ValueError("SOURCES is empty: enable at least one source, e.g. SOURCES=arbeitnow")
    return [EXTRACTORS[name]() for name in names]


__all__ = ["EXTRACTORS", "Extractor", "RawPosting", "get_enabled_extractors"]
