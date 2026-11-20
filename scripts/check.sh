#!/usr/bin/env bash
# The full gate. Run this before every commit; CI runs the same commands.
#
#   ./scripts/check.sh            # lint, types, tests, secret scan
#   ./scripts/check.sh --fast     # skip the wheel build/packaging test
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PY:-.venv/bin/python}"
fail() { printf '\nFAILED: %s\n' "$*" >&2; exit 1; }

echo "== ruff =="
"${PY%python}ruff" check . || fail "ruff"

echo "== pyright =="
"${PY%python}pyright" || fail "pyright"

if [ "${1:-}" != "--fast" ]; then
  echo "== wheel =="
  rm -rf dist
  uv build --wheel . -q || fail "wheel build"
fi

echo "== pytest =="
"$PY" -m pytest -q || fail "pytest"

echo "== secret scan =="
"$PY" scripts/scan_repo.py || fail "secret scan"

echo
echo "all checks passed"
