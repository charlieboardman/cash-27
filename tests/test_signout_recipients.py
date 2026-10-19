"""The optional disk-backed signout recipient registry."""

from __future__ import annotations

import json

import pytest

from cash import signout_recipients
from cash.commands import ValidationFailure


def test_slug_folds_accents_case_and_spacing():
    assert signout_recipients.slug_for("Germán González") == "german-gonzalez"
    assert signout_recipients.slug_for("  GERMAN   GONZALEZ  ") == \
        "german-gonzalez"
    assert signout_recipients.slug_for("María-José O'Connor") == \
        "maria-jose-o-connor"


def test_registry_resolves_names_aliases_and_unconfigured_people():
    registry = signout_recipients.Registry([{
        "slug": "german-gonzalez",
        "name": "Germán González",
        "aliases": ["German G.", "G. González"],
    }])
    assert registry.resolve("Germán González") == (
        "german-gonzalez", "Germán González")
    assert registry.resolve("german gonzalez") == (
        "german-gonzalez", "Germán González")
    assert registry.resolve("German G.") == (
        "german-gonzalez", "Germán González")
    assert registry.resolve("Someone Else") == ("someone-else", "Someone Else")


def test_save_load_roundtrip_keeps_names_and_restricts_the_file(tmp_path):
    registry = signout_recipients.save(tmp_path, [{
        "slug": "german-gonzalez",
        "name": "Germán González",
        "aliases": ["German Gonzalez", "German G."],
    }])
    assert registry.people[0]["name"] == "Germán González"
    path = tmp_path / "config" / "signout-recipients.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "format": 1,
        "people": [{"slug": "german-gonzalez", "name": "Germán González",
                    "aliases": ["German Gonzalez", "German G."]}],
    }
    assert path.stat().st_mode & 0o077 == 0
    loaded = signout_recipients.load(tmp_path)
    assert loaded.people == registry.people
    assert loaded.resolve("German G.")[0] == "german-gonzalez"


def test_missing_config_is_an_empty_registry(tmp_path):
    registry = signout_recipients.load(tmp_path)
    assert registry.people == []
    assert registry.resolve("Ana") == ("ana", "Ana")


def test_duplicate_aliases_and_names_are_rejected(tmp_path):
    with pytest.raises(ValidationFailure) as duplicate:
        signout_recipients.save(tmp_path, [
            {"slug": "german-gonzalez", "name": "Germán González",
             "aliases": ["German Gonzalez"]},
            {"slug": "german-g", "name": "German G.",
             "aliases": ["Germán González"]},
        ])
    assert duplicate.value.code == "duplicate_recipient"
    assert not (tmp_path / "config" / "signout-recipients.json").exists()


def test_corrupt_config_raises_a_clear_error(tmp_path):
    path = tmp_path / "config" / "signout-recipients.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValidationFailure) as error:
        signout_recipients.load(tmp_path)
    assert error.value.code == "signout_recipient_config"


def test_names_that_cannot_make_a_slug_are_rejected():
    with pytest.raises(ValidationFailure) as error:
        signout_recipients.slug_for("東京")
    assert error.value.code == "bad_slug"
