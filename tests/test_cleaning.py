"""Tests for the value cleaners in src/cleaning.py (from the v1 transform step).

All tests use small hand-made values: no network, no files, no database.
The v1 tests of the pandas transform() function were ported to
tests/test_mappers.py, because that function was replaced by the mappers.
"""

from src.cleaning import clean_bool, clean_description, clean_text, clean_title


# --- clean_description ------------------------------------------------------

def test_description_html_is_removed_and_entities_unescaped():
    html = "<p>Tom &amp; Jerry&nbsp;&nbsp;GmbH</p><ul><li>Python &lt;3</li><li>SQL</li></ul>"
    assert clean_description(html) == "Tom & Jerry GmbH\nPython <3\nSQL"


def test_description_inline_tags_do_not_split_sentences():
    html = "<p>We use <strong>Python</strong> and <a href='#'>SQL</a> daily.</p>"
    assert clean_description(html) == "We use Python and SQL daily."


def test_description_double_escaped_html_is_cleaned():
    # Some API descriptions are HTML that was escaped a second time.
    html = "&lt;p&gt;Hello&lt;/p&gt;&lt;p&gt;World&lt;/p&gt;"
    assert clean_description(html) == "Hello\nWorld"


def test_description_blank_lines_are_collapsed():
    html = "<p>First</p><p>   </p><br><br><p>Second</p>"
    assert clean_description(html) == "First\nSecond"


def test_description_empty_becomes_none():
    assert clean_description("<p>  </p>") is None
    assert clean_description(None) is None


# --- clean_title --------------------------------------------------------------

def test_gender_markers_are_removed_from_titles():
    assert clean_title("Python Developer (m/w/d)") == "Python Developer"
    assert clean_title("Python Developer (M/F/D)") == "Python Developer"
    assert clean_title("(w/m/d) Python Developer") == "Python Developer"
    assert clean_title("Python Developer (f/m/d) - Berlin") == "Python Developer - Berlin"
    assert clean_title("  Python Developer ( m / w / x )  ") == "Python Developer"


def test_other_gender_markers_are_removed_from_titles():
    assert clean_title("Software Engineer (all genders)") == "Software Engineer"
    assert clean_title("Software Engineer (All Genders)") == "Software Engineer"
    assert clean_title("Software Engineer (all gender)") == "Software Engineer"
    assert clean_title("Softwareentwickler (gn)") == "Softwareentwickler"
    assert clean_title("Coordinateur Supply Chain (H/F/N)") == "Coordinateur Supply Chain"
    assert clean_title("Comptable auxiliaire (F/H)") == "Comptable auxiliaire"


def test_gender_markers_without_brackets_are_removed():
    assert clean_title("Security Lead - m/f/d") == "Security Lead"
    assert clean_title("Account Executive - m/f/d - France") == "Account Executive - France"
    assert clean_title("Senior Manager MES | m/w/d") == "Senior Manager MES"
    assert clean_title("Chargé RH en alternance H/F/X") == "Chargé RH en alternance"
    assert clean_title("Coordinateur Supply Chain - (H/F/N)") == "Coordinateur Supply Chain"


def test_slashes_that_are_not_gender_markers_are_kept():
    assert clean_title("UX/UI Designer") == "UX/UI Designer"
    assert clean_title("Business/Demand Planner") == "Business/Demand Planner"
    assert clean_title("Werkstudent (Vollzeit/Minijob)") == "Werkstudent (Vollzeit/Minijob)"


def test_title_keeps_other_brackets():
    assert clean_title("Senior Developer (Backend)") == "Senior Developer (Backend)"


def test_title_with_only_marker_becomes_none():
    assert clean_title(" (m/w/d) ") is None


def test_serbian_gender_markers_are_removed_from_titles():
    assert clean_title("Programer (m/ž)") == "Programer"
    assert clean_title("Programer (M/Ž)") == "Programer"


# --- clean_text / clean_bool ----------------------------------------------------

def test_clean_text_strips_and_turns_empty_into_none():
    assert clean_text("  Example GmbH  ") == "Example GmbH"
    assert clean_text("   ") is None
    assert clean_text(None) is None
    assert clean_text(42) is None


def test_clean_bool_only_accepts_real_booleans():
    assert clean_bool(True) is True
    assert clean_bool(False) is False
    assert clean_bool("true") is None
    assert clean_bool(None) is None
