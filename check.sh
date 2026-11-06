#!/bin/sh
# Runs every check: syntax for Python 3.9, repository rules, and all tests.
set -eu
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1 && python3 -c "" >/dev/null 2>&1; then
    PYTHON=python3
else
    PYTHON=python
fi
exec "$PYTHON" -m unittest discover
