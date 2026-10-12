"""The common interface every source (extractor) implements.

An extractor only DOWNLOADS or READS postings and returns them unchanged.
It does not clean them and does not touch the database: main.py stores the
result in raw_postings, and the transform step (Phase 2) cleans it later.
Because all extractors look the same from the outside, main.py can run them
in a simple loop, and adding a new source means adding one new class.
"""

from abc import ABC, abstractmethod
from typing import TypedDict

# Must match the CHECK constraint on sources.kind in migrations/001_initial_v2.sql.
SOURCE_KINDS = ("email", "ats", "csv", "api")


class RawPosting(TypedDict):
    """One posting as returned by an extractor."""

    external_id: str  # the posting's ID at the source; unique per source
    payload: dict     # the raw posting, unchanged (stored as JSONB)


class Extractor(ABC):
    """Base class for all extractors. Subclasses set `name` and `kind` and implement fetch()."""

    name: str  # unique source name, stored in sources.name, used in the SOURCES setting
    kind: str  # one of SOURCE_KINDS

    def __init_subclass__(cls, **kwargs):
        # Runs when a subclass is DEFINED (at import time), so a typo in `kind`
        # fails immediately, not later as a database CHECK violation.
        super().__init_subclass__(**kwargs)
        if getattr(cls, "kind", None) not in SOURCE_KINDS:
            raise TypeError(f"{cls.__name__}.kind must be one of {SOURCE_KINDS}")
        if not getattr(cls, "name", None):
            raise TypeError(f"{cls.__name__} must set a name")

    @abstractmethod
    def fetch(self) -> list[RawPosting]:
        """Return all postings currently available from this source.

        Raise an exception if the source can't be read; main.py records the
        failure for this source only and continues with the other sources.
        """
