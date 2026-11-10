"""Settings and profiles: config list, get, set, unset, path and check, and the profile commands."""
from __future__ import annotations

import os

from .. import config, profiles, render
from ..errors import BooksError
from .common import EXIT_FINDING, EXIT_OK, add_command, add_group, program_name


def _where(profile) -> str:
    return f"profile {profile}" if profile else "the settings file"


def cmd_config_list(args) -> int:
    settings = args.settings
    state = "" if settings.file_exists else " (not created yet; every value below comes from elsewhere)"
    print(f"Settings file: {settings.path}{state}")
    print(f"Profile: {settings.profile} (from {settings.profile_source})" if settings.profile else "Profile: none")
    rows = [
        [setting.name, setting.show(settings[setting.name]), settings.sources[setting.name]]
        for setting in config.SETTINGS
    ]
    print(render.table(["Setting", "Value", "From"], rows, max_cell=72))
    return EXIT_OK


def cmd_config_get(args) -> int:
    setting = config.lookup(args.name)
    print(setting.show(args.settings[setting.name]))
    return EXIT_OK


def cmd_config_set(args) -> int:
    setting, value, path = config.set_value(args.name, args.value, args.profile)
    print(f"Set {setting.name} to {setting.show(value)} in {_where(args.profile)} ({path}).")
    if os.environ.get(setting.env):
        print(f"Note: {setting.env} is set in the environment, and it comes first.")
    return EXIT_OK


def cmd_config_unset(args) -> int:
    setting, removed, path = config.unset_value(args.name, args.profile)
    if removed:
        print(f"Removed {setting.name} from {_where(args.profile)} ({path}).")
    else:
        print(f"{setting.name} was not set in {_where(args.profile)}. Nothing changed.")
    return EXIT_OK


def cmd_config_path(args) -> int:
    print(config.config_path())
    return EXIT_OK


def cmd_config_check(args) -> int:
    path, exists, problems = config.check()
    print(f"Checked {path}")
    if not exists and not problems:
        print("There is no settings file, so every setting has its default. 'config set' creates one.")
    for problem in problems:
        print(f"  PROBLEM: {problem}")
    if problems:
        print(f"RESULT: PROBLEMS FOUND. Commands other than 'config path' and 'config check' stop until they are fixed. "
              f"{config.CONFIG_DOC} describes the file.")
        return EXIT_FINDING
    print("RESULT: no problems found.")
    return EXIT_OK


def cmd_profile_add(args) -> int:
    if not args.db:
        raise BooksError("a profile needs its books file: give it with --db FILE")
    values, file = profiles.add_profile(args.name, args.db, args.set or ())
    print(f"Added profile {args.name} to {file.path}. Its books: {values['books']}")
    print(f"Use it once with --profile {args.name}, or make it the default with '{program_name()} profile use {args.name}'.")
    return EXIT_OK


def cmd_profile_list(args) -> int:
    found, default, file = profiles.list_profiles()
    if not found:
        print(f"No profiles yet. Add one with '{program_name()} profile add NAME --db FILE'.")
        return EXIT_OK
    active, _ = config.active_profile(file, args.profile, os.environ)
    rows = []
    for name, values in found:
        marks = [word for word, applies in (("in use", name == active), ("default", name == default)) if applies]
        others = ", ".join(f"{key}={values[key]}" for key in values if key != "books")
        rows.append([name, str(values.get("books", "")), others, ", ".join(marks)])
    print(render.table(["Profile", "Books", "Other settings", ""], rows, max_cell=72))
    return EXIT_OK


def cmd_profile_show(args) -> int:
    name = args.name
    if name is None:
        name, _ = config.active_profile(config.ConfigFile.load(config.config_path()), args.profile, os.environ)
    if name is None:
        raise BooksError("no profile is in use. Name the one to show: profile show NAME")
    values = profiles.get_profile(name)
    print(f"Profile {name}")
    for setting in config.SETTINGS:
        if setting.name in values:
            print(f"  {setting.name}: {values[setting.name]}")
    unknown = [key for key in values if key not in {s.name for s in config.SETTINGS}]
    for key in unknown:
        print(f"  {key}: {values[key]}  (not a setting; 'config check' reports it)")
    return EXIT_OK


def cmd_profile_use(args) -> int:
    if args.none == bool(args.name):
        raise BooksError("name the profile to use, or give --none to stop using a default profile")
    file = profiles.use_profile(None if args.none else args.name)
    if args.none:
        print(f"No default profile now. Commands use the settings file's own values ({file.path}).")
    else:
        print(f"Commands now use profile {args.name} unless --profile or ${config.PROFILE_ENV_VAR} says otherwise.")
    return EXIT_OK


def cmd_profile_remove(args) -> int:
    was_default = profiles.remove_profile(args.name)
    print(f"Removed profile {args.name}. Its books file was not touched.")
    if was_default:
        print("It was the default profile, so there is no default profile now.")
    return EXIT_OK


def register(commands) -> None:
    conf = add_group(commands, "config", "settings: the defaults commands use, kept in a file you can edit")
    add_command(conf, "list", cmd_config_list, "show every setting, its value and where the value came from", needs_conn=False)
    p = add_command(conf, "get", cmd_config_get, "print one setting's value", needs_conn=False)
    p.add_argument("name")
    p = add_command(
        conf, "set", cmd_config_set, "check a value and save it in the settings file, or in the profile --profile names",
        needs_conn=False, uses_settings=False,
    )
    p.add_argument("name")
    p.add_argument("value")
    p = add_command(
        conf, "unset", cmd_config_unset, "remove a value from the settings file, or from the profile --profile names",
        needs_conn=False, uses_settings=False,
    )
    p.add_argument("name")
    add_command(conf, "path", cmd_config_path, "print where the settings file is", needs_conn=False, uses_settings=False)
    add_command(
        conf, "check", cmd_config_check, "check the settings file for unknown keys and bad values; exit status 1 if any",
        needs_conn=False, uses_settings=False,
    )

    prof = add_group(commands, "profile", "named sets of settings, such as one books file per year")
    p = add_command(
        prof, "add", cmd_profile_add, "add a profile: its books file with --db, other settings with --set",
        needs_conn=False, uses_settings=False,
    )
    p.add_argument("name")
    p.add_argument("--set", action="append", metavar="NAME=VALUE", help="another setting for the profile; can be repeated")
    add_command(prof, "list", cmd_profile_list, "list profiles, and say which is in use", needs_conn=False, uses_settings=False)
    p = add_command(prof, "show", cmd_profile_show, "show a profile's settings (default: the one in use)", needs_conn=False, uses_settings=False)
    p.add_argument("name", nargs="?")
    p = add_command(prof, "use", cmd_profile_use, "make a profile the default for every command", needs_conn=False, uses_settings=False)
    p.add_argument("name", nargs="?")
    p.add_argument("--none", action="store_true", help="stop using a default profile")
    p = add_command(prof, "remove", cmd_profile_remove, "forget a profile; its books file is not touched", needs_conn=False, uses_settings=False)
    p.add_argument("name")
