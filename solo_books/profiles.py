"""Profiles: named sets of settings, for someone who keeps more than one set of books.

A profile lives in the settings file under "profiles". It always names a books file and can hold any other setting.
"profile" at the top of the file names the profile used when neither --profile nor SOLO_BOOKS_PROFILE says otherwise.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .config import PROFILE_KEY, PROFILES_KEY, ConfigFile, check_profile_name, config_path, lookup, stored_form
from .errors import BooksError


def _file(environ: Optional[Mapping[str, str]]) -> ConfigFile:
    return ConfigFile.load(config_path(environ))


def parse_assignments(assignments: Sequence[str]) -> Dict[str, Any]:
    """NAME=VALUE pairs from --set, each checked as 'config set' checks it."""
    found: Dict[str, Any] = {}
    for assignment in assignments:
        name, equals, text = assignment.partition("=")
        if not equals or not name.strip():
            raise BooksError(f"--set needs NAME=VALUE, such as report_format=csv, not {assignment!r}")
        setting = lookup(name)
        if setting.name == "books":
            raise BooksError("give the profile's books file with --db, not --set books=")
        found[setting.name] = stored_form(setting, text)
    return found


def add_profile(
    name: str, books: str, assignments: Sequence[str] = (), environ: Optional[Mapping[str, str]] = None
) -> Tuple[Dict[str, Any], ConfigFile]:
    check_profile_name(name)
    values = {"books": stored_form(lookup("books"), books)}
    values.update(parse_assignments(assignments))
    file = _file(environ)
    profiles = file.profiles()
    if name in profiles:
        raise BooksError(f"there is already a profile named {name!r}. Change it with 'config set NAME VALUE --profile {name}'.")
    profiles[name] = values
    file.data[PROFILES_KEY] = profiles
    file.save()
    return values, file


def list_profiles(environ: Optional[Mapping[str, str]] = None) -> Tuple[List[Tuple[str, Dict[str, Any]]], Optional[str], ConfigFile]:
    file = _file(environ)
    return sorted(file.profiles().items()), file.default_profile(), file


def get_profile(name: str, environ: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    profiles = _file(environ).profiles()
    if name not in profiles:
        have = ", ".join(sorted(profiles)) or "none yet"
        raise BooksError(f"no profile named {name!r} (have: {have})")
    return profiles[name]


def use_profile(name: Optional[str], environ: Optional[Mapping[str, str]] = None) -> ConfigFile:
    """Make a profile the default, or with None go back to no default profile."""
    file = _file(environ)
    if name is None:
        file.data.pop(PROFILE_KEY, None)
    else:
        if name not in file.profiles():
            have = ", ".join(sorted(file.profiles())) or "none yet"
            raise BooksError(f"no profile named {name!r} (have: {have})")
        file.data[PROFILE_KEY] = name
    file.save()
    return file


def remove_profile(name: str, environ: Optional[Mapping[str, str]] = None) -> bool:
    """Forget a profile. Its books file is left as it is. Returns whether it was the default."""
    file = _file(environ)
    profiles = file.profiles()
    if name not in profiles:
        raise BooksError(f"no profile named {name!r}")
    del profiles[name]
    was_default = file.data.get(PROFILE_KEY) == name
    if was_default:
        del file.data[PROFILE_KEY]
    file.save()
    return was_default
