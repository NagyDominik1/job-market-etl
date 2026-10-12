"""Arbeitnow API payload -> NormalizedJob."""

from datetime import datetime, timezone

from src.cleaning import clean_bool, clean_description, clean_text, clean_title
from src.mappers.base import NormalizedJob


def unix_to_datetime(value) -> datetime | None:
    """Unix time (seconds since 1970-01-01 UTC) -> timezone-aware UTC datetime.

    A missing or invalid value gives None instead of failing the posting:
    the date is useful, but not essential.
    """
    # bool is a subclass of int in Python, so True would count as 1 second.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def map_arbeitnow(payload: dict) -> NormalizedJob:
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object, got {type(payload).__name__}")
    return NormalizedJob(
        title=clean_title(payload.get("title")),
        company_name=clean_text(payload.get("company_name")),
        location=clean_text(payload.get("location")),
        remote=clean_bool(payload.get("remote")),
        url=clean_text(payload.get("url")),
        description=clean_description(payload.get("description")),
        posted_at=unix_to_datetime(payload.get("created_at")),
        deadline=None,  # Arbeitnow has no application deadline
    )
