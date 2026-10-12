"""Tests for normalization and fingerprints (src/normalize.py)."""

import pytest

from src.normalize import (
    fold,
    make_fingerprint,
    normalize_city,
    normalize_company,
    normalize_title,
)


# --- fold ---------------------------------------------------------------------

def test_fold_removes_accents_and_transliterates_cyrillic():
    assert fold("Újvidék") == "ujvidek"
    assert fold("Fejlesztő") == "fejleszto"
    assert fold("Нови Сад") == "novi sad"
    assert fold("Ђорђе Чаворић") == "djordje cavoric"  # ђ -> dj, ч/ћ -> c
    assert fold("Đurđevo") == "djurdjevo"              # Latin đ -> dj as well
    assert fold(None) == ""


# --- company --------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "ErdSoft d.o.o.", "ErdSoft Kft.", "ERDSOFT DOO", "ErdSoft GmbH & Co. KG",
    "ErdSoft Ltd", "ErdSoft, Inc.", "  ErdSoft   Zrt. ", "ErdSoft AD",
])
def test_legal_forms_are_removed(name):
    assert normalize_company(name) == "erdsoft"


def test_company_name_words_are_kept():
    assert normalize_company("Nordeus d.o.o. Beograd") == "nordeus beograd"  # doo removed anywhere
    assert normalize_company("AD Systems") == "ad systems"   # short forms only at the end
    assert normalize_company("Example.com") == "examplecom"
    assert normalize_company("Példa Bt.") == "pelda"
    assert normalize_company("Kft.") == "kft"                 # nothing else left: keep it
    assert normalize_company(None) == ""


# --- title ----------------------------------------------------------------------

def test_title_is_lowercase_without_accents_and_gender_markers():
    assert normalize_title("Python Fejlesztő (m/w/d)") == "python fejleszto"
    assert normalize_title("  Data   Engineer  ") == "data engineer"
    assert normalize_title("Senior Data Engineer – Python") == normalize_title("Senior Data Engineer - Python")


def test_cyrillic_title_and_marker():
    assert normalize_title("Програмер (м/ж)") == "programer"
    assert normalize_title("Програмер (м/ж)") == normalize_title("Programer (m/ž)")


def test_title_keeps_plus_and_hash():
    assert normalize_title("C# Developer") != normalize_title("C Developer")
    assert normalize_title("C++ Developer") == "c++ developer"


# --- city -----------------------------------------------------------------------

@pytest.mark.parametrize("spelling, expected", [
    ("Subotica", "subotica"), ("Szabadka", "subotica"), ("Суботица", "subotica"),
    ("Novi Sad", "novi sad"), ("Újvidék", "novi sad"), ("Нови Сад", "novi sad"),
    ("Novi Sad, Serbia", "novi sad"),
    ("Beograd", "beograd"), ("Belgrade", "beograd"), ("Belgrád", "beograd"), ("Београд", "beograd"),
    ("Szeged", "szeged"), ("Kula", "kula"), ("Кула", "kula"),
    ("Budapest", "budapest"), ("1134 Budapest, XIII. kerület", "budapest"),
])
def test_city_aliases(spelling, expected):
    assert normalize_city(spelling) == expected


def test_szabadka_equals_subotica():
    assert normalize_city("Szabadka") == normalize_city("Subotica") == normalize_city("Суботица")


def test_remote():
    assert normalize_city("Remote") == "remote"
    assert normalize_city("Távmunka") == "remote"
    assert normalize_city("Rad od kuće") == "remote"
    assert normalize_city("Berlin", remote=True) == "remote"   # the source's remote flag wins
    assert normalize_city("Budapest (remote possible)") == "budapest"  # not fully remote


def test_unknown_city_is_folded():
    assert normalize_city("Pécs") == "pecs"
    assert normalize_city(None) == ""


# --- fingerprint ---------------------------------------------------------------

def test_same_job_in_different_spellings_has_the_same_fingerprint():
    a = make_fingerprint("ErdSoft GmbH", "Data Engineer (m/w/d)", "Subotica")
    b = make_fingerprint("ErdSoft d.o.o.", "data engineer", "Szabadka")
    c = make_fingerprint("ErdSoft Kft.", "Data Engineer", "Суботица")
    assert a == b == c


def test_different_jobs_have_different_fingerprints():
    base = make_fingerprint("ErdSoft", "Data Engineer", "Subotica")
    assert make_fingerprint("Other", "Data Engineer", "Subotica") != base
    assert make_fingerprint("ErdSoft", "Data Analyst", "Subotica") != base
    assert make_fingerprint("ErdSoft", "Data Engineer", "Szeged") != base
    assert make_fingerprint("ErdSoft", "Data Engineer", "Subotica", remote=True) != base


def test_fingerprint_is_stable():
    # A fixed expected value: if this fails, a normalization rule changed, and
    # every existing job would get a new fingerprint (and show up as new once).
    # Only update this value if that is really what you want.
    # The value is sha256("erdsoft|data engineer|subotica").
    assert make_fingerprint("ErdSoft d.o.o.", "Data Engineer (m/ž)", "Szabadka") == (
        "af0c36fc794c89e56faec297241639c87e6a9565473d197600f56f00210e2b60"
    )
