"""The common output of all mappers."""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class NormalizedJob:
    """One posting in the same shape for every source, with cleaned values.

    The fields match the columns of the `jobs` table. None = the source
    doesn't have this information (never an empty string).
    """

    title: str | None
    company_name: str | None
    location: str | None
    remote: bool | None
    url: str | None
    description: str | None
    posted_at: datetime | None
    deadline: date | None
