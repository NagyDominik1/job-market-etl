"""Manual CSV row (see data/manual/README.md) -> NormalizedJob."""

from datetime import date, datetime

from src.cleaning import clean_description, clean_text, clean_title
from src.mappers.base import NormalizedJob
from src.normalize import REMOTE, normalize_city

# Accepted deadline formats: ISO, Hungarian and Serbian/German style.
DATE_FORMATS = ["%Y-%m-%d", "%Y.%m.%d", "%Y.%m.%d.", "%d.%m.%Y", "%d.%m.%Y."]


def parse_deadline(value) -> date | None:
    """'2026-11-15', '2026.11.15.' or '15.11.2026.' -> date(2026, 11, 15); empty -> None.

    A value that is not a date raises ValueError: I typed it by hand, so I want
    to see the mistake (as a transform_error), not lose the deadline silently.
    """
    text = clean_text(value)
    if text is None:
        return None
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(text.replace(" ", ""), date_format).date()
        except ValueError:
            continue
    raise ValueError(f"deadline {text!r} is not a date (use e.g. 2026-11-15)")


def map_csv(payload: dict) -> NormalizedJob:
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object, got {type(payload).__name__}")
    city = clean_text(payload.get("city"))
    return NormalizedJob(
        title=clean_title(payload.get("title")),
        company_name=clean_text(payload.get("company")),
        location=city,
        # The CSV has no remote column; "Remote" / "Távmunka" as city means remote.
        remote=True if city and normalize_city(city) == REMOTE else None,
        url=clean_text(payload.get("url")),
        description=clean_description(payload.get("description")),
        posted_at=None,
        deadline=parse_deadline(payload.get("deadline")),
    )
