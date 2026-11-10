"""Settings: a JSON file the person can read and edit, environment variables, and named profiles.

Every setting is looked up in the same order: a flag on the command line, then the setting's environment variable,
then the active profile, then the settings file, then the setting's default. docs/CONFIGURATION.md lists them.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import formats
from .errors import BooksError
from .files import write_atomic

CONFIG_ENV_VAR = "SOLO_BOOKS_CONFIG"
PROFILE_ENV_VAR = "SOLO_BOOKS_PROFILE"
CONFIG_DOC = "docs/CONFIGURATION.md"
# The keys of the settings file that are not settings themselves.
PROFILE_KEY = "profile"
PROFILES_KEY = "profiles"
# A settings file is a few hundred bytes. This refuses a wrong file before it is read into memory.
MAX_CONFIG_BYTES = 1024 * 1024
_MAX_TEXT = 1000
_SWITCH_WORDS = {"1": True, "true": True, "yes": True, "on": True, "0": False, "false": False, "no": False, "off": False}


@dataclass(frozen=True)
class Setting:
    """One setting: its name, type, default, environment variable, the flag that overrides it, and what it does."""

    name: str
    kind: str
    default: Any
    env: str
    help: str
    flag: str = ""
    choices: Tuple[str, ...] = ()

    @property
    def type_name(self) -> str:
        if self.kind == "choice":
            return " or ".join(self.choices)
        return {
            "path": "file or folder path",
            "text": "text",
            "port": "port number, 0 to 65535",
            "count": "whole number, 1 or more",
            "switch": "true or false",
        }[self.kind]

    def _bad(self, value: object) -> BooksError:
        return BooksError(f"{self.name} must be a {self.type_name}, not {value!r}")

    def from_json(self, value: object) -> Any:
        """Check a value as JSON holds it. null means the setting is not set here."""
        if value is None:
            return None
        if self.kind in ("path", "text", "choice"):
            if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
                raise self._bad(value)
            if self.kind == "choice":
                if value.strip().lower() not in self.choices:
                    raise BooksError(f"{self.name} must be one of {', '.join(self.choices)}, not {value!r}")
                return value.strip().lower()
            return value.strip()
        if self.kind == "switch":
            if not isinstance(value, bool):
                raise self._bad(value)
            return value
        if isinstance(value, bool) or not isinstance(value, int):
            raise self._bad(value)
        if self.kind == "port" and not 0 <= value <= 65535:
            raise self._bad(value)
        if self.kind == "count" and not 1 <= value <= 1000:
            raise BooksError(f"{self.name} must be a whole number from 1 to 1000, not {value!r}")
        return value

    def from_text(self, text: str) -> Any:
        """Read a value typed on the command line or set in an environment variable."""
        text = text.strip()
        if self.kind == "switch":
            if text.lower() not in _SWITCH_WORDS:
                raise self._bad(text)
            return _SWITCH_WORDS[text.lower()]
        if self.kind in ("port", "count"):
            if not re.match(r"^\d{1,6}$", text):
                raise self._bad(text)
            return self.from_json(int(text))
        return self.from_json(text)

    def show(self, value: Any) -> str:
        if value is None:
            return "(not set)"
        if self.kind == "switch":
            return "true" if value else "false"
        return str(value)


SETTINGS: Tuple[Setting, ...] = (
    Setting(
        "books", "path", "books.sqlite3", "SOLO_BOOKS_DB",
        "the books file commands work on; a relative path is read from the current folder", flag="--db",
    ),
    Setting(
        "business", "text", None, "SOLO_BOOKS_BUSINESS",
        "the business a command works on when it needs one and --business is left out", flag="--business",
    ),
    Setting(
        "report_format", "choice", "table", "SOLO_BOOKS_REPORT_FORMAT",
        "how every report prints", flag="--format", choices=formats.names(),
    ),
    Setting(
        "date_format", "choice", "iso", "SOLO_BOOKS_DATE_FORMAT",
        "how dates are shown to a person, on screen and in table, html and markdown reports: iso is 2025-03-31, us is "
        "03/31/2025. CSV, JSON, journals, archives and input stay ISO",
        choices=("iso", "us"),
    ),
    Setting("web_port", "port", 8765, "SOLO_BOOKS_WEB_PORT", "the port the review page listens on", flag="--port"),
    Setting(
        "import_runs_rules", "switch", True, "SOLO_BOOKS_IMPORT_RUNS_RULES",
        "whether import books new rows with the rules as they arrive", flag="--rules / --no-rules",
    ),
    Setting(
        "backup_folder", "path", None, "SOLO_BOOKS_BACKUP_FOLDER",
        "where backup puts a dated copy when no file is named; not set means beside the books", flag="FILE",
    ),
    Setting(
        "backup_keep", "count", 10, "SOLO_BOOKS_BACKUP_KEEP",
        "how many dated backups backup --prune keeps", flag="--keep",
    ),
)
_BY_NAME = {setting.name: setting for setting in SETTINGS}
_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")


def lookup(name: str) -> Setting:
    key = name.strip().lower().replace("-", "_")
    if key not in _BY_NAME:
        raise BooksError(f"no setting named {name!r}. 'config list' shows every setting.")
    return _BY_NAME[key]


def config_path(environ: Optional[Mapping[str, str]] = None, platform: Optional[str] = None) -> Path:
    """Where the settings file is: SOLO_BOOKS_CONFIG if set, else the usual place for this system."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if environ.get(CONFIG_ENV_VAR):
        return Path(environ[CONFIG_ENV_VAR]).expanduser()
    if platform == "win32":
        base = Path(environ["APPDATA"]) if environ.get("APPDATA") else Path.home() / "AppData" / "Roaming"
    else:
        xdg = environ.get("XDG_CONFIG_HOME", "")
        # The standard says a relative value is to be ignored.
        base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".config"
    return base / "solo-books" / "config.json"


def check_profile_name(name: str) -> str:
    if not _PROFILE_NAME.match(name or ""):
        raise BooksError(
            f"{name!r} cannot be a profile name. Use up to 40 letters, digits, dots, dashes or underscores, "
            "starting with a letter or digit."
        )
    return name


class ConfigFile:
    """The settings file as JSON holds it. Nothing here is checked until a value is asked for."""

    def __init__(self, path: Path, data: Dict[str, Any], exists: bool) -> None:
        self.path = path
        self.data = data
        self.exists = exists

    @classmethod
    def load(cls, path: Path, hint: bool = True) -> "ConfigFile":
        """Read the file. A missing file is an empty one; a damaged one is one plain sentence, never a traceback."""
        path = Path(path)
        hint = f" Fix or delete it, or run 'config check' to see what is wrong. {CONFIG_DOC} describes the file." if hint else ""
        try:
            if path.is_dir():
                raise BooksError(f"the settings file {path} is a folder.{hint}")
            if not path.exists():
                return cls(path, {}, False)
            if path.stat().st_size > MAX_CONFIG_BYTES:
                raise BooksError(f"the settings file {path} is too large to be a settings file.{hint}")
            raw = path.read_bytes()
        except OSError as exc:
            raise BooksError(f"cannot read the settings file {path}: {exc.strerror or exc}.{hint}") from None
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except UnicodeDecodeError:
            raise BooksError(f"the settings file {path} is not UTF-8 text.{hint}") from None
        except json.JSONDecodeError as exc:
            raise BooksError(
                f"the settings file {path} is not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg}).{hint}"
            ) from None
        except RecursionError:
            raise BooksError(f"the settings file {path} is nested too deeply to read.{hint}") from None
        if not isinstance(data, dict):
            raise BooksError(f"the settings file {path} must hold one JSON object, in braces.{hint}")
        return cls(path, data, True)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BooksError(f"cannot create the folder {self.path.parent}: {exc.strerror or exc}") from None
        write_atomic(self.path, (json.dumps(self.data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))

    def profiles(self) -> Dict[str, Dict[str, Any]]:
        found = self.data.get(PROFILES_KEY, {})
        if not isinstance(found, dict) or not all(isinstance(value, dict) for value in found.values()):
            raise BooksError(
                f"the settings file {self.path} has a {PROFILES_KEY!r} entry that is not an object of profiles. "
                "Run 'config check'."
            )
        return found

    def default_profile(self) -> Optional[str]:
        value = self.data.get(PROFILE_KEY)
        if value is None:
            return None
        if not isinstance(value, str):
            raise BooksError(
                f"the settings file {self.path} has a {PROFILE_KEY!r} entry that is not a name. Run 'config check'."
            )
        return value

    def values(self, scope: Mapping[str, Any], where: str) -> Dict[str, Any]:
        """The settings set in one part of the file, checked. Keys that are not settings are left to 'config check'."""
        found = {}
        for setting in SETTINGS:
            if setting.name not in scope:
                continue
            try:
                value = setting.from_json(scope[setting.name])
            except BooksError as exc:
                raise BooksError(f"{where} in {self.path}: {exc}. Fix it with 'config set' or run 'config check'.") from None
            if value is not None:
                found[setting.name] = value
        return found


@dataclass
class Settings:
    """Every setting's value for one command, and where each value came from."""

    values: Dict[str, Any]
    sources: Dict[str, str]
    path: Path
    profile: Optional[str] = None
    profile_source: str = ""
    file_exists: bool = False

    def __getitem__(self, name: str) -> Any:
        return self.values[name]

    def pick(self, name: str, flag_value: Any) -> Any:
        """A flag given on the command line wins over everything else."""
        return flag_value if flag_value is not None else self.values[name]

    def show_date(self, value: Any) -> str:
        """A date for a row listing on screen, in the chosen style. Anything that is not a date shows as it is."""
        if value is None:
            return ""
        text = value.isoformat() if isinstance(value, date) else str(value)
        if self.values.get("date_format") != "us" or not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
            return text
        return f"{text[5:7]}/{text[8:10]}/{text[:4]}"


def _environment_value(setting: Setting, environ: Mapping[str, str]) -> Any:
    try:
        return setting.from_text(environ[setting.env])
    except BooksError as exc:
        raise BooksError(f"the environment variable {setting.env}: {exc}") from None


def active_profile(file: ConfigFile, flag: Optional[str], environ: Mapping[str, str]) -> Tuple[Optional[str], str]:
    """Which profile is in use and why: --profile, then SOLO_BOOKS_PROFILE, then the file's default."""
    if flag:
        return flag, "--profile"
    if environ.get(PROFILE_ENV_VAR):
        return environ[PROFILE_ENV_VAR], f"environment {PROFILE_ENV_VAR}"
    default = file.default_profile()
    if default:
        return default, "the settings file"
    return None, ""


def resolve(
    flags: Optional[Mapping[str, Any]] = None,
    profile_flag: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Settings:
    """Every setting's value, in the order flag, environment, profile, settings file, default."""
    environ = os.environ if environ is None else environ
    flags = flags or {}
    file = ConfigFile.load(config_path(environ))
    top = file.values(file.data, "a setting")
    profile, profile_source = active_profile(file, profile_flag, environ)
    in_profile: Dict[str, Any] = {}
    if profile:
        profiles = file.profiles()
        if profile not in profiles:
            have = ", ".join(sorted(profiles)) or "none yet"
            raise BooksError(f"no profile named {profile!r} (have: {have}). 'profile list' shows them.")
        in_profile = file.values(profiles[profile], f"profile {profile}")
    values: Dict[str, Any] = {}
    sources: Dict[str, str] = {}
    for setting in SETTINGS:
        if flags.get(setting.name) is not None:
            values[setting.name], sources[setting.name] = flags[setting.name], f"flag {setting.flag}"
        elif environ.get(setting.env):
            values[setting.name] = _environment_value(setting, environ)
            sources[setting.name] = f"environment {setting.env}"
        elif setting.name in in_profile:
            values[setting.name], sources[setting.name] = in_profile[setting.name], f"profile {profile}"
        elif setting.name in top:
            values[setting.name], sources[setting.name] = top[setting.name], "settings file"
        else:
            values[setting.name], sources[setting.name] = setting.default, "default"
        if setting.kind == "path" and values[setting.name] is not None:
            values[setting.name] = os.path.expanduser(str(values[setting.name]))
    return Settings(values, sources, file.path, profile, profile_source, file.exists)


def stored_form(setting: Setting, text: str) -> Any:
    """What 'config set' and 'profile add' write: a checked value, with a path made whole so it works from any folder."""
    value = setting.from_text(text)
    if setting.kind == "path":
        value = os.path.abspath(os.path.expanduser(value))
        if setting.name == "books" and os.path.isdir(value):
            raise BooksError(f"{value} is a folder. The books setting names a file.")
        if setting.name == "backup_folder" and os.path.isfile(value):
            raise BooksError(f"{value} is a file. The backup_folder setting names a folder.")
    return value


def _scope(file: ConfigFile, profile: Optional[str]) -> Dict[str, Any]:
    if profile is None:
        return file.data
    profiles = file.profiles()
    if profile not in profiles:
        raise BooksError(f"no profile named {profile!r}. Add it with 'profile add'.")
    return profiles[profile]


def set_value(
    name: str, text: str, profile: Optional[str] = None, environ: Optional[Mapping[str, str]] = None
) -> Tuple[Setting, Any, Path]:
    """Check one value and write it into the settings file, or into one profile there."""
    setting = lookup(name)
    value = stored_form(setting, text)
    file = ConfigFile.load(config_path(environ))
    _scope(file, profile)[setting.name] = value
    file.save()
    return setting, value, file.path


def unset_value(
    name: str, profile: Optional[str] = None, environ: Optional[Mapping[str, str]] = None
) -> Tuple[Setting, bool, Path]:
    """Remove one value from the settings file, or from one profile there, so the next place in the order decides."""
    setting = lookup(name)
    file = ConfigFile.load(config_path(environ))
    scope = _scope(file, profile)
    if setting.name not in scope:
        return setting, False, file.path
    del scope[setting.name]
    file.save()
    return setting, True, file.path


def _scope_problems(scope: Mapping[str, Any], where: str) -> List[str]:
    problems = []
    for key, value in scope.items():
        if key not in _BY_NAME:
            problems.append(f"{where}: {key!r} is not a setting. 'config list' shows every setting.")
            continue
        try:
            _BY_NAME[key].from_json(value)
        except BooksError as exc:
            problems.append(f"{where}: {exc}.")
    return problems


def check(environ: Optional[Mapping[str, str]] = None) -> Tuple[Path, bool, List[str]]:
    """Every problem with the settings file and the environment, without stopping at the first."""
    environ = os.environ if environ is None else environ
    path = config_path(environ)
    problems: List[str] = []
    try:
        file = ConfigFile.load(path, hint=False)
    except BooksError as exc:
        return path, True, [str(exc)]
    top = {key: value for key, value in file.data.items() if key not in (PROFILE_KEY, PROFILES_KEY)}
    problems.extend(_scope_problems(top, "settings file"))
    profiles = file.data.get(PROFILES_KEY, {})
    if not isinstance(profiles, dict):
        problems.append(f"settings file: {PROFILES_KEY!r} must be an object holding one object per profile.")
        profiles = {}
    for name, scope in profiles.items():
        if not _PROFILE_NAME.match(name):
            problems.append(f"profile {name!r}: that cannot be a profile name.")
        if not isinstance(scope, dict):
            problems.append(f"profile {name!r}: must be an object of settings.")
            continue
        problems.extend(_scope_problems(scope, f"profile {name}"))
    default = file.data.get(PROFILE_KEY)
    if default is not None and (not isinstance(default, str) or default not in profiles):
        problems.append(f"settings file: {PROFILE_KEY!r} names {default!r}, which is not one of the profiles.")
    for setting in SETTINGS:
        if environ.get(setting.env):
            try:
                setting.from_text(environ[setting.env])
            except BooksError as exc:
                problems.append(f"environment variable {setting.env}: {exc}.")
    if environ.get(PROFILE_ENV_VAR) and environ[PROFILE_ENV_VAR] not in profiles:
        problems.append(
            f"environment variable {PROFILE_ENV_VAR} names {environ[PROFILE_ENV_VAR]!r}, which is not one of the profiles."
        )
    return path, file.exists, problems
