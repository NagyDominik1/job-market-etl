"""Cleaning helpers for single values (one value in, one value out).

These come from the v1 pandas transform step (src/transform.py in v1). The
pandas DataFrame part was removed in v2: the transform step now works row by
row, so one bad posting can be skipped without stopping the others. The
mappers (src/mappers/) use these functions to build a clean NormalizedJob.
"""

import re

from bs4 import BeautifulSoup

# Inline HTML tags that sit INSIDE a sentence, e.g. "We use <strong>Python</strong>".
# We remove these tags (but keep their text) before extracting text, otherwise
# the newline separator would split the sentence into three lines.
INLINE_TAGS = ["a", "abbr", "b", "code", "em", "font", "i", "small", "span", "strong", "sub", "sup", "u"]

# Gender markers that job titles in Germany, France, Hungary, Serbia etc. often contain.
#
# 1. In brackets: (m/w/d), (f/m/x), (h/f/n) (French: homme/femme/non-binaire),
#    (m/ž) (Serbian: muški/ženski), (all genders), (alle Geschlechter),
#    (gn) (German "geschlechtsneutral").
#      [mwfdxhnzž]            one of these letters, in any order
#      (?:/[mwfdxhnzž]){1,2}  followed by one or two more "/letter" parts
# 2. Without brackets, e.g. "Security Lead - m/f/d" or "Chargé RH H/F/X".
#    Here the letters must stand alone (not part of a word like "UX/UI").
# re.IGNORECASE makes it also match (M/W/D) or (All Genders).
LETTER_MARKER = r"[mwfdxhnzž]\s*(?:/\s*[mwfdxhnzž]\s*){1,2}"
GENDER_MARKER_PATTERN = re.compile(
    r"\(\s*(?:" + LETTER_MARKER + r"|all\s+genders?|alle\s+geschlechter|gn\*?)\s*\)"
    r"|(?<![\w/])" + LETTER_MARKER + r"(?![\w/])",
    re.IGNORECASE,
)

# Separators that can be left dangling after removing a marker,
# e.g. "Security Lead - m/f/d" -> "Security Lead -".
DANGLING_SEPARATOR_AT_END = re.compile(r"\s*[-–|,]\s*$")
DOUBLE_SEPARATOR = re.compile(r"([-–|])\s*[-–|]")  # "Engineer - - France" -> "Engineer - France"

# Looks for something like "<p>" or "</strong>" in text.
HTML_TAG_PATTERN = re.compile(r"</?[a-zA-Z][^>]*>")


def clean_text(value) -> str | None:
    """Strip whitespace from a text value. Empty strings and non-text become None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None  # "" counts as False, so empty text becomes None


def clean_bool(value) -> bool | None:
    """Return the value if it's True/False, otherwise None."""
    return value if isinstance(value, bool) else None


def _html_to_text(html: str) -> str:
    """Extract the text from HTML, putting each block (paragraph, list item...) on its own line."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(INLINE_TAGS):
        tag.unwrap()  # remove the tag itself but keep the text inside it
    soup.smooth()  # merge text pieces that are now next to each other
    # get_text() also turns HTML entities into characters, e.g. "&amp;" -> "&".
    return soup.get_text(separator="\n")


def clean_description(html) -> str | None:
    """Convert an HTML (or plain text) job description into clean plain text."""
    if not isinstance(html, str):
        return None

    text = _html_to_text(html)

    # Some descriptions in the API are escaped twice: the raw value is
    # "&lt;p&gt;Hello&lt;/p&gt;" instead of "<p>Hello</p>". After the first pass
    # these still contain tags, so we run the HTML-to-text step once more.
    if HTML_TAG_PATTERN.search(text):
        text = _html_to_text(text)

    # Clean each line: turn any run of whitespace (spaces, tabs, non-breaking
    # spaces from "&nbsp;") into one space, and trim both ends.
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    # Drop blank lines and join the rest with single newlines.
    text = "\n".join(line for line in lines if line)
    return text or None


def clean_title(title) -> str | None:
    """Strip whitespace and remove gender markers like "(m/w/d)" from a job title."""
    if not isinstance(title, str):
        return None
    title = GENDER_MARKER_PATTERN.sub("", title)
    # Removing the marker can leave double spaces or separators behind,
    # e.g. "Engineer  - Berlin" or "Engineer - - France" or "Engineer -".
    title = re.sub(r"\s+", " ", title).strip()
    title = DOUBLE_SEPARATOR.sub(r"\1", title)
    title = DANGLING_SEPARATOR_AT_END.sub("", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title or None
