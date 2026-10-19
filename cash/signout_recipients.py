"""Disk-backed canonical signout recipients.

Vouchers store the recipient as free text. This small configuration file only
canonicalizes names for the recipient index: it groups spelling variants and
fixes the display name and slug. It never restricts who can have a signout.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import unicodedata

from .commands import ValidationFailure
from .files import atomic_write
from .paper import NAME

FORMAT = 1
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_SLUG = 80


def normalize(value) -> str:
    """Fold accents, case, punctuation, and spacing into a URL-safe key."""
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def slug_for(name) -> str:
    key = normalize(name)
    if not key:
        raise ValidationFailure(
            "bad_slug", "Enter a name with Latin letters.",
            {"name": "enter a name with Latin letters"})
    if len(key) > MAX_SLUG:
        raise ValidationFailure(
            "bad_slug", f"Enter a name of at most {MAX_SLUG} URL characters.",
            {"name": f"at most {MAX_SLUG} URL characters"})
    return key


def validate_name(value) -> str:
    if not isinstance(value, str):
        raise ValidationFailure("name_required", "Recipient name is required",
                                {"name": "required"})
    name = value.strip()
    if not name:
        raise ValidationFailure("name_required", "Recipient name is required",
                                {"name": "required"})
    if len(name) > NAME or any(ord(char) < 32 for char in name):
        raise ValidationFailure(
            "bad_name",
            f"Enter a recipient name of at most {NAME} characters on one line.",
            {"name": f"single-line name, at most {NAME} characters"})
    if not normalize(name):
        raise ValidationFailure(
            "bad_slug", "Enter a name with Latin letters.",
            {"name": "enter a name with Latin letters"})
    return name


def validate_aliases(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationFailure(
            "bad_aliases", "Aliases must be a list of names.",
            {"aliases": "send a list of names"})
    result = []
    seen = set()
    for alias in value:
        text = validate_name(alias)
        key = normalize(text)
        if key not in seen:
            seen.add(key)
            result.append(text)
    return result


def validate_people(people) -> list[dict]:
    if not isinstance(people, list):
        raise ValidationFailure(
            "bad_recipients", "Signout recipients must be a list.")
    cleaned = []
    owner = {}
    for index, raw in enumerate(people):
        if not isinstance(raw, dict):
            raise ValidationFailure(
                "bad_recipients", "Each signout recipient must be an object.")
        slug = raw.get("slug")
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug) \
                or len(slug) > MAX_SLUG:
            raise ValidationFailure(
                "bad_slug", "Enter a valid URL slug for the signout recipient.",
                {"slug": "lowercase letters, numbers, and single hyphens"})
        name = validate_name(raw.get("name"))
        aliases = validate_aliases(raw.get("aliases"))
        keys = {slug, normalize(name), *(normalize(alias) for alias in aliases)}
        for key in keys:
            previous = owner.get(key)
            if previous is not None and previous != index:
                raise ValidationFailure(
                    "duplicate_recipient",
                    "That name or alias already belongs to another signout recipient.",
                    {"name": "already in use"})
            owner[key] = index
        cleaned.append({"slug": slug, "name": name, "aliases": aliases})
    return cleaned


class Registry:
    """Read-only lookup over validated entries."""

    def __init__(self, people: list[dict]):
        self.people = people
        self._by_key = {}
        for person in people:
            self._by_key[person["slug"]] = person
            self._by_key[normalize(person["name"])] = person
            for alias in person["aliases"]:
                self._by_key[normalize(alias)] = person

    def find(self, key) -> dict | None:
        return self._by_key.get(normalize(key))

    def resolve(self, recipient) -> tuple[str, str]:
        """Return the canonical slug and display name for a voucher recipient."""
        person = self.find(recipient)
        if person:
            return person["slug"], person["name"]
        return normalize(recipient), (recipient.strip()
                                      if isinstance(recipient, str) else "")


def _path(data_root) -> Path:
    return Path(data_root) / "config" / "signout-recipients.json"


def load(data_root) -> Registry:
    path = _path(data_root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Registry([])
    except (OSError, ValueError, TypeError) as exc:
        raise ValidationFailure(
            "signout_recipient_config",
            "Could not read the signout recipient configuration.") from exc
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise ValidationFailure(
            "signout_recipient_config",
            "The signout recipient configuration has an unsupported format.")
    return Registry(validate_people(raw.get("people")))


def save(data_root, people) -> Registry:
    cleaned = validate_people(people)
    path = _path(data_root)
    try:
        atomic_write(path, (json.dumps(
            {"format": FORMAT, "people": cleaned},
            ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except OSError as exc:
        raise ValidationFailure(
            "signout_recipient_config",
            "Could not save the signout recipient configuration.") from exc
    return Registry(cleaned)
