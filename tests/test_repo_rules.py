"""Rules the repository keeps about itself."""
from __future__ import annotations

import ast
import re
import shlex
import unittest
from pathlib import Path

from solo_books.cli import build_parser
from tests.helpers import REPO_ROOT

SOURCE_DIRS = ("solo_books", "tests")
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv"}
TEXT_SUFFIXES = {".py", ".md", ".json", ".html", ".csv", ".sh", ".toml", ".txt", ""}


def repo_files():
    for path in REPO_ROOT.rglob("*"):
        if path.is_file() and not SKIP_DIRS.intersection(path.relative_to(REPO_ROOT).parts):
            yield path


def python_files():
    for folder in SOURCE_DIRS:
        yield from (REPO_ROOT / folder).rglob("*.py")


class Repo(unittest.TestCase):
    def test_all_source_parses_as_python_3_9(self) -> None:
        count = 0
        for path in python_files():
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 9))
            count += 1
        self.assertGreater(count, 10)

    def test_no_em_or_en_dashes_anywhere(self) -> None:
        for path in repo_files():
            if path.suffix not in TEXT_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for code_point in (0x2013, 0x2014):
                self.assertNotIn(chr(code_point), text, str(path))

    def test_only_the_standard_library_is_imported(self) -> None:
        allowed = {
            "argparse", "ast", "calendar", "codecs", "contextlib", "csv", "dataclasses", "datetime", "hashlib", "html", "http",
            "io", "json", "os", "pathlib", "re", "secrets", "shlex", "shutil", "socket", "sqlite3", "stat", "subprocess", "sys", "tempfile",
            "threading", "time", "typing", "unittest", "urllib", "webbrowser", "collections", "__future__",
            "solo_books", "tests",
        }
        for path in python_files():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module or ""]
                for name in names:
                    self.assertIn(name.split(".")[0], allowed, f"{path}: {name}")

    def test_money_code_uses_no_floats(self) -> None:
        for path in (REPO_ROOT / "solo_books").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant):
                    self.assertNotIsInstance(node.value, float, f"{path}:{node.lineno}")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotEqual(node.func.id, "float", f"{path}:{node.lineno}")

    def test_readme_commands_never_chain(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for block in re.findall(r"```.*?```", readme, flags=re.DOTALL):
            self.assertNotIn("&&", block)
            self.assertNotIn("||", block)
            self.assertNotRegex(block, r";\s*\n|;\s+\S")

    def test_readme_commands_are_real_commands(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        parser = build_parser()
        seen = 0
        for line in re.findall(r"^python -m solo_books (.+)$", readme, flags=re.MULTILINE):
            if "--help" not in line:
                parser.parse_args(shlex.split(line))
                seen += 1
        self.assertGreater(seen, 20)

    def test_license_is_mit(self) -> None:
        text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", text)
        self.assertIn("Copyright (c) 2026 The solo-books authors", text)

    def test_sample_data_stays_synthetic(self) -> None:
        for path in (REPO_ROOT / "samples").rglob("*.csv"):
            self.assertRegex(path.name, r"^(example-|rules-|statement)", str(path))
