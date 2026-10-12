"""Tests for loading the profile (src/user_profile.py)."""

import logging

import pytest

from src import user_profile
from src.user_profile import EXAMPLE_PROFILE_FILE, load_profile


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_example_profile_is_valid():
    profile = load_profile(EXAMPLE_PROFILE_FILE)
    assert profile.skills and profile.cities and profile.preferred_roles


def test_missing_profile_falls_back_to_the_example(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("PROFILE_PATH", raising=False)
    monkeypatch.setattr(user_profile, "PROFILE_FILE", tmp_path / "profile.yaml")  # does not exist
    with caplog.at_level(logging.WARNING):
        profile = load_profile()
    assert profile.path == EXAMPLE_PROFILE_FILE
    assert "using the example profile" in caplog.text


def test_explicit_missing_file_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_profile(tmp_path / "nope.yaml")


def test_profile_values_are_read(tmp_path):
    path = write(tmp_path / "p.yaml", """
skills: {C#: 3, .NET: 2, HTML/CSS: 1}
seniority: [Intern, junior]
cities: [Szeged, remote]
languages: {Hungarian: native}
preferred_roles: [backend]
dealbreakers:
  title_keywords: [senior]
  min_years_required: 3
  required_languages_missing: [German]
""")
    profile = load_profile(path)
    assert profile.skills == {"C#": 3, ".NET": 2, "HTML/CSS": 1}  # "#" right after C is not a comment
    assert profile.seniority == ["intern", "junior"]
    assert profile.languages == {"hungarian": "native"}
    assert profile.dealbreakers.min_years_required == 3
    assert profile.dealbreakers.required_languages_missing == ["german"]


def test_dealbreakers_are_optional(tmp_path):
    profile = load_profile(write(tmp_path / "p.yaml", "skills: {Python: 1}\n"))
    assert profile.dealbreakers.title_keywords == []
    assert profile.dealbreakers.min_years_required is None


@pytest.mark.parametrize("text, message", [
    ("skills: {Python: 5}", "level must be 1, 2 or 3"),
    ("skills: {Python: yes}", "level must be 1, 2 or 3"),
    ("seniority: [expert]", "unknown seniority"),
    ("cities: Szeged", "'cities' must be a list"),
    ("dealbreakers: {min_years_required: three}", "min_years_required"),
    ("- just a list", "YAML mapping"),
])
def test_invalid_profiles_are_rejected_with_a_clear_message(tmp_path, text, message):
    with pytest.raises(ValueError, match=message):
        load_profile(write(tmp_path / "p.yaml", text))
