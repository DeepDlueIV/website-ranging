#!/usr/bin/env sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ ! -x .venv/bin/python ]; then
    python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ is required"'
    python3 -m venv .venv
fi
if ! cmp -s requirements.txt .venv/installed-requirements.txt; then
    .venv/bin/python -m pip install -r requirements.txt
    cp requirements.txt .venv/installed-requirements.txt
fi
exec .venv/bin/python app.py "$@"
