"""Settings, the settings file, precedence, damaged files, and profiles."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
from pathlib import Path
from typing import Tuple
from unittest import mock

from solo_books import config, db
from solo_books.cli import main
from solo_books.errors import BooksError
from tests.helpers import REPO_ROOT, BooksCase, parse_csv


class ConfigCase(BooksCase):
    """Each test gets its own settings file, which starts out missing, and no SOLO_BOOKS_ variables."""

    def setUp(self) -> None:
        super().setUp()
        self.config_path = self.dir / "settings" / "config.json"
        patcher = mock.patch.dict(os.environ, {config.CONFIG_ENV_VAR: str(self.config_path)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def raw(self, *args: str, stdin: str = "") -> Tuple[int, str, str]:
        """Run a command without the --db flag every other test passes."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with mock.patch("sys.stdin", io.StringIO(stdin)):
                try:
                    code = main(list(args))
                except SystemExit as exc:
                    code = exc.code
        return code, out.getvalue(), err.getvalue()

    def raw_ok(self, *args: str) -> str:
        code, out, err = self.raw(*args)
        self.assertEqual(code, 0, f"{args} failed: {err or out}")
        return out

    def write_config(self, data) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        text = data if isinstance(data, str) else json.dumps(data)
        self.config_path.write_text(text, encoding="utf-8")

    def saved(self) -> dict:
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def env(self, **values: str):
        return mock.patch.dict(os.environ, values)


class Registry(ConfigCase):
    def test_every_setting_is_complete_and_its_default_is_valid(self) -> None:
        names = [setting.name for setting in config.SETTINGS]
        self.assertEqual(len(names), len(set(names)))
        for setting in config.SETTINGS:
            self.assertTrue(setting.env.startswith("SOLO_BOOKS_"), setting.name)
            self.assertTrue(setting.help, setting.name)
            self.assertIn(setting.kind, ("path", "text", "choice", "port", "count", "switch"))
            self.assertEqual(setting.from_json(setting.default), setting.default, setting.name)

    def test_every_setting_is_documented_with_its_variable(self) -> None:
        doc = (REPO_ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")
        for setting in config.SETTINGS:
            self.assertIn(f"`{setting.name}`", doc)
            self.assertIn(f"`{setting.env}`", doc)

    def test_values_typed_as_text_are_checked(self) -> None:
        good = {
            "books": ("my-books.sqlite3", "my-books.sqlite3"),
            "business": (" Business A ", "Business A"),
            "report_format": ("CSV", "csv"),
            "date_format": ("us", "us"),
            "web_port": ("8080", 8080),
            "import_runs_rules": ("no", False),
            "backup_folder": ("backups", "backups"),
            "backup_keep": ("5", 5),
        }
        bad = {
            "books": "  ",
            "business": "",
            "report_format": "pdf",
            "date_format": "european",
            "web_port": "70000",
            "import_runs_rules": "maybe",
            "backup_folder": "",
            "backup_keep": "0",
        }
        self.assertEqual(set(good), {setting.name for setting in config.SETTINGS})
        for name, (text, value) in good.items():
            self.assertEqual(config.lookup(name).from_text(text), value, name)
        for name, text in bad.items():
            with self.assertRaises(BooksError, msg=name):
                config.lookup(name).from_text(text)

    def test_switches_read_the_usual_words(self) -> None:
        setting = config.lookup("import_runs_rules")
        for text in ("1", "true", "YES", "on"):
            self.assertIs(setting.from_text(text), True)
        for text in ("0", "false", "No", "OFF"):
            self.assertIs(setting.from_text(text), False)

    def test_json_values_must_have_the_right_type(self) -> None:
        for name, value in (
            ("web_port", "8080"), ("web_port", True), ("web_port", -1), ("import_runs_rules", "true"),
            ("backup_keep", 1001), ("backup_keep", 2.5), ("report_format", 3), ("books", ["a"]),
        ):
            with self.assertRaises(BooksError, msg=(name, value)):
                config.lookup(name).from_json(value)
        self.assertIsNone(config.lookup("web_port").from_json(None))

    def test_names_take_dashes_and_any_case(self) -> None:
        self.assertEqual(config.lookup("Report-Format").name, "report_format")
        with self.assertRaises(BooksError):
            config.lookup("colour")


class WhereTheFileIs(ConfigCase):
    def test_the_environment_variable_names_the_file(self) -> None:
        self.assertEqual(config.config_path({config.CONFIG_ENV_VAR: "x/c.json"}), Path("x/c.json"))

    def test_windows_uses_appdata(self) -> None:
        found = config.config_path({"APPDATA": str(self.dir)}, "win32")
        self.assertEqual(found, self.dir / "solo-books" / "config.json")
        self.assertEqual(config.config_path({}, "win32").parts[-4:], ("AppData", "Roaming", "solo-books", "config.json"))

    def test_elsewhere_uses_xdg_or_dot_config(self) -> None:
        absolute = str(self.dir.resolve())
        self.assertEqual(config.config_path({"XDG_CONFIG_HOME": absolute}, "linux"), Path(absolute) / "solo-books" / "config.json")
        self.assertEqual(config.config_path({"XDG_CONFIG_HOME": "relative"}, "linux"), Path.home() / ".config" / "solo-books" / "config.json")
        self.assertEqual(config.config_path({}, "darwin"), Path.home() / ".config" / "solo-books" / "config.json")

    def test_config_path_prints_it(self) -> None:
        self.assertEqual(self.raw_ok("config", "path").strip(), str(self.config_path))


class Precedence(ConfigCase):
    """Each setting, at each step of the order: flag, environment, profile, file, default."""

    VALUES = {
        "books": ("from-file.sqlite3", "from-profile.sqlite3", "from-env.sqlite3"),
        "business": ("File Co", "Profile Co", "Env Co"),
        "report_format": ("csv", "table", "csv"),
        "date_format": ("us", "iso", "us"),
        "web_port": (9001, 9002, 9003),
        "import_runs_rules": (False, True, False),
        "backup_folder": ("file-backups", "profile-backups", "env-backups"),
        "backup_keep": (3, 4, 5),
    }

    def test_each_step_wins_over_the_ones_after_it(self) -> None:
        self.assertEqual(set(self.VALUES), {setting.name for setting in config.SETTINGS})
        in_file = {name: values[0] for name, values in self.VALUES.items()}
        in_profile = {name: values[1] for name, values in self.VALUES.items()}
        self.write_config({**in_file, "profiles": {"p": in_profile}})
        environ = {config.CONFIG_ENV_VAR: str(self.config_path)}
        for setting in config.SETTINGS:
            name = setting.name
            plain = config.resolve(environ={config.CONFIG_ENV_VAR: str(self.dir / "none.json")})
            self.assertEqual((plain[name], plain.sources[name]), (setting.default, "default"), name)
            from_file = config.resolve(environ=environ)
            self.assertEqual((from_file[name], from_file.sources[name]), (self.VALUES[name][0], "settings file"), name)
            from_profile = config.resolve(profile_flag="p", environ=environ)
            self.assertEqual((from_profile[name], from_profile.sources[name]), (self.VALUES[name][1], "profile p"), name)
            env_value = self.VALUES[name][2]
            env_text = ("true" if env_value else "false") if isinstance(env_value, bool) else str(env_value)
            from_env = config.resolve(profile_flag="p", environ={**environ, setting.env: env_text})
            self.assertEqual((from_env[name], from_env.sources[name]), (env_value, f"environment {setting.env}"), name)
            from_flag = config.resolve({name: "flag value"}, "p", {**environ, setting.env: env_text})
            self.assertEqual((from_flag[name], from_flag.sources[name]), ("flag value", f"flag {setting.flag}"), name)

    def test_an_empty_environment_variable_counts_as_unset(self) -> None:
        self.write_config({"web_port": 9001})
        with self.env(SOLO_BOOKS_WEB_PORT=""):
            self.assertEqual(config.resolve()["web_port"], 9001)

    def test_books_follow_the_order_on_the_command_line(self) -> None:
        self.assertEqual(self.raw_ok("config", "get", "books").strip(), "books.sqlite3")
        file_books = self.dir / "file.sqlite3"
        self.raw_ok("config", "set", "books", str(file_books))
        self.assertEqual(self.raw_ok("config", "get", "books").strip(), str(file_books))
        profile_books = self.dir / "profile.sqlite3"
        self.raw_ok("profile", "add", "p", "--db", str(profile_books))
        self.assertEqual(self.raw_ok("--profile", "p", "config", "get", "books").strip(), str(profile_books))
        with self.env(SOLO_BOOKS_DB=str(self.dir / "env.sqlite3")):
            self.assertEqual(self.raw_ok("--profile", "p", "config", "get", "books").strip(), str(self.dir / "env.sqlite3"))
            flag = str(self.dir / "flag.sqlite3")
            self.assertEqual(self.raw_ok("--profile", "p", "config", "get", "books", "--db", flag).strip(), flag)

    def test_the_books_setting_is_the_file_commands_open(self) -> None:
        self.raw_ok("config", "set", "books", str(self.db_path))
        self.ok("business", "add", "Business A")
        self.assertIn(f"Books: {self.db_path}", self.raw_ok("status"))
        self.assertIn("Business A", self.raw_ok("business", "list"))

    def test_the_default_business_fills_in_a_missing_business(self) -> None:
        self.ok("business", "add", "Business A")
        self.ok("business", "add", "Business B")
        self.assertIn("say which business", self.fails("account", "add", "X1", "--kind", "checking"))

        def business_of(account: str) -> str:
            return self.conn.execute(
                "SELECT b.name FROM accounts a JOIN businesses b ON b.id = a.business_id WHERE a.name = ?", (account,)
            ).fetchone()[0]

        self.raw_ok("config", "set", "business", "Business B")
        self.ok("account", "add", "X2", "--kind", "checking")
        self.assertEqual(business_of("X2"), "Business B")
        self.raw_ok("profile", "add", "p", "--db", str(self.db_path), "--set", "business=Business A")
        self.ok("--profile", "p", "account", "add", "X3", "--kind", "checking")
        self.assertEqual(business_of("X3"), "Business A")
        with self.env(SOLO_BOOKS_BUSINESS="Business B"):
            self.ok("--profile", "p", "account", "add", "X4", "--kind", "checking")
            self.ok("--profile", "p", "account", "add", "X5", "--kind", "checking", "--business", "Business A")
        self.assertEqual((business_of("X4"), business_of("X5")), ("Business B", "Business A"))
        # Reports still cover every business when --business is left out.
        out = self.ok("report", "pnl", "--period", "2025")
        self.assertIn("All businesses", out)

    def _one_row(self) -> None:
        self.ok("business", "add", "Business A")
        self.ok("account", "add", "A Checking", "--kind", "checking", "--date-format", "%Y-%m-%d")
        self.ok("txn", "add", "A Checking", "--date", "2025-03-31", "--amount", "-5.00", "--description", "CAFE", "--category", "Meals")

    def test_report_format_follows_the_order(self) -> None:
        self._one_row()
        self.assertTrue(self.ok("report", "pnl", "--period", "2025").startswith("Profit and loss"))
        self.raw_ok("config", "set", "report_format", "csv")
        self.assertTrue(self.ok("report", "pnl", "--period", "2025").startswith("business,"))
        self.assertEqual(parse_csv(self.ok("report", "balances", "--period", "2025"))[0]["business"], "Business A")
        self.assertTrue(self.ok("report", "schedule-c", "--year", "2025").startswith("tax_year,"))
        self.assertTrue(self.ok("report", "pnl", "--period", "2025", "--format", "table").startswith("Profit and loss"))
        with self.env(SOLO_BOOKS_REPORT_FORMAT="table"):
            self.assertTrue(self.ok("report", "pnl", "--period", "2025").startswith("Profit and loss"))

    def test_date_format_changes_row_listings_only(self) -> None:
        self._one_row()
        self.assertIn("2025-03-31", self.ok("txn", "list"))
        self.raw_ok("config", "set", "date_format", "us")
        self.assertIn("03/31/2025", self.ok("txn", "list"))
        self.assertIn("03/31/2025", self.ok("txn", "show", "1"))
        self.assertIn("03/31/2025", self.ok("status"))
        csv_out = self.ok("txn", "list", "--format", "csv")
        self.assertIn("2025-03-31", csv_out)
        self.assertNotIn("03/31/2025", csv_out)
        with self.env(SOLO_BOOKS_DATE_FORMAT="iso"):
            self.assertIn("2025-03-31", self.ok("txn", "list"))

    def test_date_format_reaches_the_review_queue_and_imports(self) -> None:
        self.ok("business", "add", "Business A")
        self.ok("account", "add", "A Checking", "--kind", "checking", "--date-format", "%Y-%m-%d")
        path = self.write("a.csv", "Date,Description,Amount\n2025-04-01,MYSTERY,-9.00\n")
        self.raw_ok("config", "set", "date_format", "us")
        self.assertIn("04/01/2025", self.ok("import", "A Checking", str(path), "--dry-run"))
        self.ok("import", "A Checking", str(path))
        self.assertIn("04/01/2025", self.ok("review", "--list"))
        self.assertIn("04/01/2025", self.ok("review", stdin="q\n"))

    def test_web_port_follows_the_order(self) -> None:
        used = []

        class FakeServer:
            def __init__(self, path, port, verbose=False, settings=None) -> None:
                used.append(port)
                self.settings = settings
                self.origin = f"http://127.0.0.1:{port}"
                self.url = f"{self.origin}/"
                self.dashboard_url = f"{self.origin}/app"

            def serve_forever(self) -> None:
                raise KeyboardInterrupt

            def server_close(self) -> None:
                pass

        with mock.patch("solo_books.cli.web_page.web.ReviewServer", FakeServer):
            self.ok("web")
            self.raw_ok("config", "set", "web_port", "9001")
            self.ok("web")
            with self.env(SOLO_BOOKS_WEB_PORT="9002"):
                self.ok("web")
                self.ok("web", "--port", "9003")
        self.assertEqual(used, [8765, 9001, 9002, 9003])

    def test_import_runs_rules_follows_the_order(self) -> None:
        self.ok("business", "add", "Business A")
        self.ok("account", "add", "A Checking", "--kind", "checking", "--date-format", "%Y-%m-%d")
        self.ok("rules", "add", "--contains", "COFFEE", "--category", "Meals")

        def waiting_after(day: int, *flags: str) -> int:
            path = self.write(f"f{day}.csv", f"Date,Description,Amount\n2025-01-{day:02d},COFFEE,-4.00\n")
            out = self.ok("import", "A Checking", str(path), *flags)
            return int(out.split("waiting for review:")[1].split()[0])

        self.assertEqual(waiting_after(1), 0)
        self.raw_ok("config", "set", "import_runs_rules", "false")
        self.assertEqual(waiting_after(2), 1)
        self.assertEqual(waiting_after(3, "--rules"), 0)
        with self.env(SOLO_BOOKS_IMPORT_RUNS_RULES="yes"):
            self.assertEqual(waiting_after(4), 0)
            self.assertEqual(waiting_after(5, "--no-rules"), 1)
        self.assertIn("booked 2", self.ok("rules", "apply"))

    def test_backup_folder_follows_the_order(self) -> None:
        self.ok("business", "add", "Business A")
        beside = self.ok("backup")
        self.assertIn(str(self.dir), beside)
        folder = self.dir / "backups"
        folder.mkdir()
        self.raw_ok("config", "set", "backup_folder", str(folder))
        self.ok("backup")
        self.assertEqual(len(list(folder.glob("books-backup-*.sqlite3"))), 1)
        other = self.dir / "other"
        other.mkdir()
        with self.env(SOLO_BOOKS_BACKUP_FOLDER=str(other)):
            self.ok("backup")
        self.assertEqual(len(list(other.glob("books-backup-*.sqlite3"))), 1)
        named = self.dir / "named.sqlite3"
        self.ok("backup", str(named))
        self.assertTrue(named.is_file())

    def test_backup_keep_follows_the_order(self) -> None:
        self.ok("business", "add", "Business A")
        for stamp in ("20240101-000000", "20240201-000000", "20240301-000000", "20240401-000000", "20240501-000000"):
            db.backup(self.conn, self.dir / f"books-backup-{stamp}.sqlite3", remember=False)

        def dated() -> int:
            return len(list(self.dir.glob("books-backup-*.sqlite3")))

        self.raw_ok("config", "set", "backup_keep", "5")
        self.ok("backup", "--prune")
        self.assertEqual(dated(), 5)
        self.assertFalse((self.dir / "books-backup-20240101-000000.sqlite3").exists())
        with self.env(SOLO_BOOKS_BACKUP_KEEP="4"):
            self.ok("backup", "--prune")
        self.assertEqual(dated(), 4)
        out = self.ok("backup", "--prune", "--keep", "2")
        self.assertIn("removed 3", out)
        self.assertEqual(dated(), 2)
        self.assertFalse((self.dir / "books-backup-20240501-000000.sqlite3").exists())


class Pruning(ConfigCase):
    def setUp(self) -> None:
        super().setUp()
        self.ok("business", "add", "Business A")

    def test_prune_touches_only_dated_backups_of_these_books(self) -> None:
        keep_names = ["books-backup-20240101-000000.sqlite3", "mine.sqlite3", "other-backup-20240101-000000.sqlite3"]
        for name in keep_names:
            db.backup(self.conn, self.dir / name, remember=False)
        not_books = self.dir / "books-backup-20230101-000000.sqlite3"
        not_books.write_text("not a database", encoding="utf-8")
        out = self.ok("backup", "--prune", "--keep", "1")
        self.assertTrue((self.dir / "mine.sqlite3").exists())
        self.assertTrue((self.dir / "other-backup-20240101-000000.sqlite3").exists())
        self.assertFalse((self.dir / "books-backup-20240101-000000.sqlite3").exists())
        self.assertTrue(not_books.exists())
        self.assertIn("does not hold a database", out)

    def test_two_backups_in_one_second_both_stay(self) -> None:
        with mock.patch("solo_books.safety._stamp", return_value="20250101-120000"):
            self.ok("backup")
            self.ok("backup")
            self.ok("backup", "--prune", "--keep", "2")
        left = sorted(path.name for path in self.dir.glob("books-backup-*"))
        self.assertEqual(left, ["books-backup-20250101-120000-2.sqlite3", "books-backup-20250101-120000-3.sqlite3"])

    def test_prune_and_keep_misuse_is_refused(self) -> None:
        self.assertIn("give --prune", self.fails("backup", "--keep", "3"))
        self.assertIn("no FILE", self.fails("backup", str(self.dir / "x.sqlite3"), "--prune"))
        self.assertNotEqual(self.run_cli("backup", "--prune", "--keep", "0")[0], 0)


class Commands(ConfigCase):
    def test_set_writes_checked_json_atomically_and_keeps_other_keys(self) -> None:
        self.write_config({"note_to_self": "kept", "date_format": "iso"})
        out = self.raw_ok("config", "set", "date_format", "us")
        self.assertIn("Set date_format to us", out)
        self.assertEqual(self.saved(), {"note_to_self": "kept", "date_format": "us"})
        self.assertEqual([p.name for p in self.config_path.parent.iterdir()], ["config.json"])

    def test_set_creates_the_folder_and_refuses_bad_values_without_writing(self) -> None:
        code, _, err = self.raw("config", "set", "web_port", "lots")
        self.assertEqual(code, 2)
        self.assertIn("web_port must be a port number", err)
        self.assertFalse(self.config_path.exists())
        self.raw_ok("config", "set", "web_port", "8800")
        self.assertEqual(self.saved(), {"web_port": 8800})

    def test_set_saves_whole_paths_and_refuses_the_wrong_kind_of_path(self) -> None:
        os.chdir(str(self.dir))
        self.addCleanup(os.chdir, str(REPO_ROOT))
        self.raw_ok("config", "set", "books", "relative.sqlite3")
        self.assertEqual(self.saved()["books"], str((self.dir / "relative.sqlite3").resolve()))
        self.assertIn("is a folder", self.raw("config", "set", "books", str(self.dir))[2])
        self.assertIn("is a file", self.raw("config", "set", "backup_folder", str(self.db_path))[2])

    def test_get_unset_and_list(self) -> None:
        self.assertEqual(self.raw_ok("config", "get", "report_format").strip(), "table")
        self.assertEqual(self.raw_ok("config", "get", "business").strip(), "(not set)")
        self.raw_ok("config", "set", "report_format", "csv")
        listing = self.raw_ok("config", "list")
        self.assertRegex(listing, r"report_format\s+csv\s+settings file")
        self.assertRegex(listing, r"web_port\s+8765\s+default")
        self.assertIn("Profile: none", listing)
        self.assertIn("Removed report_format", self.raw_ok("config", "unset", "report_format"))
        self.assertIn("Nothing changed", self.raw_ok("config", "unset", "report_format"))
        self.assertEqual(self.saved(), {})
        self.assertEqual(self.raw("config", "get", "colour")[0], 2)

    def test_list_says_when_there_is_no_file_yet(self) -> None:
        self.assertIn("not created yet", self.raw_ok("config", "list"))

    def test_set_warns_when_the_environment_comes_first(self) -> None:
        with self.env(SOLO_BOOKS_DATE_FORMAT="iso"):
            self.assertIn("comes first", self.raw_ok("config", "set", "date_format", "us"))

    def test_set_and_unset_inside_a_profile(self) -> None:
        self.raw_ok("profile", "add", "p", "--db", str(self.db_path))
        self.raw_ok("--profile", "p", "config", "set", "date_format", "us")
        self.assertEqual(self.saved()["profiles"]["p"]["date_format"], "us")
        self.assertNotIn("date_format", {k for k in self.saved() if k != "profiles"})
        self.raw_ok("config", "unset", "date_format", "--profile", "p")
        self.assertNotIn("date_format", self.saved()["profiles"]["p"])
        self.assertIn("no profile named", self.raw("config", "set", "date_format", "us", "--profile", "q")[2])

    def test_check_on_a_good_file_and_on_no_file(self) -> None:
        out = self.raw_ok("config", "check")
        self.assertIn("no settings file", out)
        self.raw_ok("config", "set", "date_format", "us")
        self.assertIn("no problems found", self.raw_ok("config", "check"))


class Damaged(ConfigCase):
    def status(self, *flags: str) -> int:
        return self.raw("--db", str(self.db_path), *flags, "status")[0]

    def assert_stops_plainly(self, says: str) -> None:
        code, out, err = self.raw("status")
        self.assertEqual(code, 2, out)
        self.assertIn(says, err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertEqual(self.raw("config", "path")[0], 0)

    def check_finds(self, says: str) -> str:
        code, out, err = self.raw("config", "check")
        self.assertEqual(code, 1, err)
        self.assertIn(says, out)
        self.assertIn("PROBLEMS FOUND", out)
        return out

    def test_invalid_json(self) -> None:
        self.write_config('{"date_format": "us",')
        self.assert_stops_plainly("is not valid JSON (line 1")
        self.check_finds("not valid JSON")
        self.assertIn("not valid JSON", self.raw("config", "set", "date_format", "us")[2])

    def test_not_an_object(self) -> None:
        self.write_config("[1, 2]")
        self.assert_stops_plainly("must hold one JSON object")
        self.check_finds("must hold one JSON object")

    def test_not_utf8(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_bytes(b'{"date_format": "\xff"}')
        self.assert_stops_plainly("not UTF-8")
        self.check_finds("not UTF-8")

    def test_a_folder_where_the_file_should_be(self) -> None:
        self.config_path.mkdir(parents=True)
        self.assert_stops_plainly("is a folder")
        self.check_finds("is a folder")

    def test_far_too_large(self) -> None:
        self.write_config(" " * (config.MAX_CONFIG_BYTES + 1))
        self.assert_stops_plainly("too large")

    def test_a_bad_value_stops_commands_and_can_be_fixed_in_place(self) -> None:
        self.write_config({"web_port": "eighty"})
        self.assert_stops_plainly("web_port must be a port number")
        self.check_finds("web_port must be a port number")
        self.raw_ok("config", "set", "web_port", "8080")
        self.raw_ok("config", "check")
        self.assertEqual(self.status(), 0)

    def test_an_unknown_key_is_reported_but_does_not_stop_commands(self) -> None:
        self.write_config({"date_fromat": "us"})
        self.assertEqual(self.status(), 0)
        self.check_finds("'date_fromat' is not a setting")

    def test_profile_problems(self) -> None:
        self.write_config({"profile": "gone", "profiles": {"bad name!": {"books": 3}, "ok": {"colour": "red"}}})
        self.assert_stops_plainly("no profile named 'gone'")
        out = self.check_finds("names 'gone'")
        self.assertIn("cannot be a profile name", out)
        self.assertIn("books must be a file or folder path", out)
        self.assertIn("'colour' is not a setting", out)

    def test_profiles_that_are_not_objects(self) -> None:
        self.write_config({"profile": "x", "profiles": ["x"]})
        self.assert_stops_plainly("not an object of profiles")
        self.check_finds("must be an object")

    def test_bad_environment_values(self) -> None:
        with self.env(SOLO_BOOKS_WEB_PORT="abc"):
            self.assert_stops_plainly("the environment variable SOLO_BOOKS_WEB_PORT")
        with self.env(SOLO_BOOKS_PROFILE="nobody"):
            self.assert_stops_plainly("no profile named 'nobody'")
        with self.env(SOLO_BOOKS_WEB_PORT="abc", SOLO_BOOKS_PROFILE="nobody"):
            out = self.check_finds("environment variable SOLO_BOOKS_WEB_PORT")
            self.assertIn("SOLO_BOOKS_PROFILE names 'nobody'", out)

    def test_a_bad_value_in_a_profile_not_in_use_does_not_stop_commands(self) -> None:
        self.write_config({"profiles": {"spare": {"books": "x.sqlite3", "backup_keep": 0}}})
        self.assertEqual(self.status(), 0)
        self.check_finds("backup_keep must be a whole number from 1 to 1000")
        self.assertEqual(self.status("--profile", "spare"), 2)


class Profiles(ConfigCase):
    def test_add_list_show_use_and_remove(self) -> None:
        other = self.dir / "other.sqlite3"
        out = self.raw_ok("profile", "add", "test-copy", "--db", str(other), "--set", "date_format=us", "--set", "web_port=9000")
        self.assertIn("Added profile test-copy", out)
        self.assertEqual(self.saved()["profiles"]["test-copy"], {"books": str(other), "date_format": "us", "web_port": 9000})
        self.raw_ok("profile", "add", "main", "--db", str(self.db_path))
        listing = self.raw_ok("profile", "list")
        self.assertRegex(listing, r"test-copy\s+.*date_format=us, web_port=9000")
        self.assertNotIn("default", listing)
        self.assertIn("in use", self.raw_ok("--profile", "main", "profile", "list"))
        self.raw_ok("profile", "use", "test-copy")
        self.assertEqual(self.saved()["profile"], "test-copy")
        self.assertRegex(self.raw_ok("profile", "list"), r"test-copy.*in use, default")
        shown = self.raw_ok("profile", "show")
        self.assertIn("Profile test-copy", shown)
        self.assertIn("web_port: 9000", shown)
        self.assertIn(f"books: {self.db_path}", self.raw_ok("profile", "show", "main"))
        self.assertIn("no default profile now", self.raw_ok("profile", "remove", "test-copy"))
        self.assertNotIn("profile", self.saved())
        self.assertIn("No default profile", self.raw_ok("profile", "use", "--none"))
        self.assertIn("no profile is in use", self.raw("profile", "show")[2])

    def test_mistakes_are_refused(self) -> None:
        self.assertIn("--db FILE", self.raw("profile", "add", "p")[2])
        self.assertIn("cannot be a profile name", self.raw("profile", "add", "../p", "--db", "x.sqlite3")[2])
        self.assertIn("NAME=VALUE", self.raw("profile", "add", "p", "--db", "x.sqlite3", "--set", "web_port")[2])
        self.assertIn("web_port must be", self.raw("profile", "add", "p", "--db", "x.sqlite3", "--set", "web_port=x")[2])
        self.assertIn("with --db", self.raw("profile", "add", "p", "--db", "x.sqlite3", "--set", "books=y")[2])
        self.assertFalse(self.config_path.exists())
        self.raw_ok("profile", "add", "p", "--db", "x.sqlite3")
        self.assertIn("already a profile", self.raw("profile", "add", "p", "--db", "y.sqlite3")[2])
        self.assertIn("no profile named", self.raw("profile", "use", "q")[2])
        self.assertIn("no profile named", self.raw("profile", "remove", "q")[2])
        self.assertIn("no profile named", self.raw("profile", "show", "q")[2])
        self.assertIn("--none", self.raw("profile", "use")[2])
        self.assertIn("no profile named 'q'", self.raw("--profile", "q", "status")[2])

    def test_a_profile_chooses_the_books_every_command_opens(self) -> None:
        year = self.dir / "books-2026.sqlite3"
        self.raw_ok("profile", "add", "2026", "--db", str(year))
        self.raw_ok("--profile", "2026", "init")
        self.raw_ok("--profile", "2026", "business", "add", "Business C")
        conn = sqlite3.connect(str(year))
        self.assertEqual(conn.execute("SELECT name FROM businesses").fetchall(), [("Business C",)])
        conn.close()
        self.assertNotIn("Business C", self.ok("business", "list"))
        status = self.raw_ok("status", "--profile", "2026")
        self.assertIn(f"Books: {year}", status)
        self.assertIn("Profile: 2026 (from --profile)", status)
        self.raw_ok("profile", "use", "2026")
        self.assertIn("Profile: 2026 (from the settings file)", self.raw_ok("status"))
        self.raw_ok("profile", "add", "main", "--db", str(self.db_path))
        with self.env(SOLO_BOOKS_PROFILE="main"):
            self.assertIn("Profile: main (from environment SOLO_BOOKS_PROFILE)", self.raw_ok("status"))
        self.assertIn("Profile: main (from --profile)", self.raw_ok("--profile", "main", "status"))

    def test_status_says_when_no_profile_is_in_use(self) -> None:
        self.assertIn("Profile: none", self.ok("status"))

    def test_removing_a_profile_leaves_its_books(self) -> None:
        self.raw_ok("profile", "add", "p", "--db", str(self.db_path))
        self.raw_ok("profile", "remove", "p")
        self.assertTrue(self.db_path.is_file())
