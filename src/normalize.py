"""Normalization and fingerprinting: recognising the SAME job across sources.

The same job arrives in different spellings, e.g.
    Arbeitnow:  "Data Engineer (m/w/d)"  at "ErdSoft GmbH"   in "Subotica"
    my CSV:     "Data engineer"          at "ErdSoft d.o.o." in "Szabadka"
The normalize_* functions turn each part into one canonical form, and
make_fingerprint() hashes company|title|city. Equal fingerprint = same job.

These values are only used for MATCHING. What we show (jobs.title etc.) keeps
the original, readable spelling.

IMPORTANT: changing any rule here changes fingerprints, so existing jobs would
no longer match their new postings (they'd show up as "new" once). Change
rules on purpose, and expect test_fingerprint_is_stable to fail when you do.
"""

import hashlib
import re
import unicodedata

from src.cleaning import GENDER_MARKER_PATTERN

# --- Folding: lowercase, Latin script, no accents ---------------------------

# Serbian Cyrillic -> Serbian Latin (Gaj's alphabet). Diacritics like č/ć/ž are
# kept here and removed by fold() below, so "Нови Сад" and "Novi Sad" end up equal.
CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ђ": "đ", "е": "e", "ж": "ž",
    "з": "z", "и": "i", "ј": "j", "к": "k", "л": "l", "љ": "lj", "м": "m", "н": "n",
    "њ": "nj", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "ћ": "ć", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "č", "џ": "dž", "ш": "š",
}

# Letters that are NOT "letter + accent" in Unicode, so stripping accents
# would not change them. đ -> dj is the usual Serbian ASCII spelling.
SPECIAL_LETTERS = {"đ": "dj", "ß": "ss", "ł": "l", "ø": "o", "æ": "ae", "œ": "oe"}


def fold(text: str | None) -> str:
    """Lowercase, transliterate Cyrillic, and remove accents: "Újvidék" -> "ujvidek"."""
    text = (text or "").lower()  # lower() first, so the dicts only need lowercase keys
    text = "".join(CYRILLIC_TO_LATIN.get(char, char) for char in text)
    text = "".join(SPECIAL_LETTERS.get(char, char) for char in text)
    # NFKD splits "é" into "e" + a separate accent mark; then we drop the marks.
    text = unicodedata.normalize("NFKD", text)
    return "".join(char for char in text if not unicodedata.combining(char))


def collapse(text: str) -> str:
    """Trim and turn every run of whitespace into one space."""
    return " ".join(text.split())


# --- Company ------------------------------------------------------------------

# Legal forms that are never part of a real name: removed wherever they appear,
# e.g. "Nordeus d.o.o. Beograd" -> "nordeus beograd".
LEGAL_FORMS_ANYWHERE = {
    "doo",                    # Serbian d.o.o.
    "kft", "zrt", "nyrt",     # Hungarian Kft., Zrt., Nyrt.
    "gmbh", "mbh",            # German
    "ltd", "llc", "plc",      # English
    "srl", "sro", "bv",       # Romanian/Italian s.r.l., Czech/Slovak s.r.o., Dutch B.V.
}
# Short forms that could also be a word or part of a name ("AD Systems",
# "AS Roma"): only removed at the END of the name.
LEGAL_FORMS_AT_END = {
    "ad", "ag", "as", "bt", "kkt", "kg", "co", "corp", "inc", "sa", "se", "spa",
    "nv", "oy", "ab", "ug", "limited", "company",
}


def normalize_company(name: str | None) -> str:
    """'ErdSoft d.o.o.' / 'ErdSoft Kft.' / 'ERDSOFT GmbH & Co. KG' -> 'erdsoft'."""
    text = fold(name)
    text = text.replace(".", "")              # "d.o.o." -> "doo", "s.r.l." -> "srl"
    text = re.sub(r"[^\w\s]", " ", text)      # "&", ",", "-", "(" ... -> space
    tokens = text.split()

    kept = [token for token in tokens if token not in LEGAL_FORMS_ANYWHERE]
    while len(kept) > 1 and kept[-1] in LEGAL_FORMS_AT_END:
        kept.pop()  # "erdsoft co kg" -> "erdsoft"
    # A "name" that is ONLY a legal form ("Kft.") keeps it, rather than becoming "".
    return " ".join(kept or tokens)


# --- Title --------------------------------------------------------------------

def normalize_title(title: str | None) -> str:
    """'Python Fejlesztő (m/w/d)' -> 'python fejleszto'; 'Програмер (м/ж)' -> 'programer'."""
    # fold() first: Cyrillic markers like "(м/ж)" become "(m/z)", which the
    # pattern knows. The pattern needs the brackets and slashes, so it runs
    # before punctuation is removed.
    text = GENDER_MARKER_PATTERN.sub(" ", fold(title))
    # Punctuation -> space, so "Engineer - Python" and "Engineer – Python" match.
    # + and # are kept: "C# Developer" and "C Developer" are different jobs.
    text = re.sub(r"[^\w\s+#]", " ", text)
    return collapse(text)


# --- City ---------------------------------------------------------------------

# Every spelling (already folded) -> one canonical name.
CITY_ALIASES = {
    "subotica": "subotica", "szabadka": "subotica",
    "novi sad": "novi sad", "ujvidek": "novi sad",
    "beograd": "beograd", "belgrade": "beograd", "belgrad": "beograd",
    "szeged": "szeged", "segedin": "szeged",
    "budapest": "budapest", "budimpesta": "budapest",
    "kula": "kula",
}
# Longest first, so "novi sad" is tried before a shorter alias inside it.
ALIASES_LONGEST_FIRST = sorted(CITY_ALIASES, key=len, reverse=True)

# The whole location says "remote" (Hungarian távmunka, Serbian rad od kuće...).
# Only an EXACT match counts: "Budapest (remote possible)" is still Budapest.
REMOTE_WORDS = {
    "remote", "full remote", "fully remote", "100 remote", "remote only", "home office",
    "tavmunka", "rad od kuce", "anywhere", "worldwide",
}

REMOTE = "remote"


def normalize_city(city: str | None, remote: bool | None = None) -> str:
    """'Szabadka' / 'Суботица' -> 'subotica'; '1134 Budapest, XIII.' -> 'budapest'.

    remote=True (the source says the job is remote) -> 'remote'.
    """
    if remote is True:
        return REMOTE
    text = fold(city)
    text = re.sub(r"[^\w\s]", " ", text)   # punctuation -> space
    text = re.sub(r"\d+", " ", text)       # postal codes, district numbers
    text = collapse(text)

    if text in REMOTE_WORDS:
        return REMOTE
    # A known city anywhere in the text, as whole words:
    # "budapest xiii kerulet" -> "budapest", "novi sad serbia" -> "novi sad".
    for alias in ALIASES_LONGEST_FIRST:
        if re.search(r"\b" + re.escape(alias) + r"\b", text):
            return CITY_ALIASES[alias]
    return text  # unknown city: its folded spelling


# --- Fingerprint ----------------------------------------------------------------

def make_fingerprint(company_name: str | None, title: str | None, location: str | None,
                     remote: bool | None = None) -> str:
    """SHA-256 of 'company|title|city', all normalized. Equal fingerprint = same job."""
    key = "|".join([
        normalize_company(company_name),
        normalize_title(title),
        normalize_city(location, remote),
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
