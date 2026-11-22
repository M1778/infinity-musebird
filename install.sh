#!/usr/bin/env bash
# infinity-musebird bootstrapper.
#
# This script does exactly three things: ensure `uv` exists, install the pinned
# package as an isolated tool, and hand off to `musebird install`. All real logic
# is in Python so it is testable and identical on every platform.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/M1778/infinity-musebird/main/install.sh | bash
#   ./install.sh --dry-run
#
# Environment:
#   MUSEBIRD_VERSION   version to install (default: the release tag this script ships in)
#   MUSEBIRD_UV        path to a uv binary (default: discovered on PATH)

set -euo pipefail

MUSEBIRD_VERSION="${MUSEBIRD_VERSION:-0.1.3}"
REPO="M1778/infinity-musebird"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -eq 0 ] && [ -z "${ALLOW_ROOT:-}" ]; then
  say "warning: running as root installs into root's home. Set ALLOW_ROOT=1 to continue."
  exit 1
fi

found_uv=""
if [ -n "${MUSEBIRD_UV:-}" ] && [ -x "${MUSEBIRD_UV}" ]; then
  found_uv="${MUSEBIRD_UV}"
elif command -v uv >/dev/null 2>&1; then
  found_uv="$(command -v uv)"
fi

if [ -z "$found_uv" ]; then
  say "Installing uv (provides Python, no sudo needed)..."
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    die "need curl or wget to install uv"
  fi
  for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -x "$candidate" ]; then found_uv="$candidate"; break; fi
  done
  [ -n "$found_uv" ] || die "uv installed but not found; add ~/.local/bin to PATH and re-run"
fi

say "Using $($found_uv --version)"

# Prefer the published wheel; fall back to the git tag so the script works before
# the first PyPI release. Both are version-pinned.
say "Installing infinity-musebird ${MUSEBIRD_VERSION}..."
# [all] pulls Playwright and keyring: without them `musebird login` and the
# browser driver cannot run.
if ! "$found_uv" tool install --force "infinity-musebird[all]==${MUSEBIRD_VERSION}" 2>/dev/null; then
  "$found_uv" tool install --force \
    "infinity-musebird[all] @ git+https://github.com/${REPO}.git@v${MUSEBIRD_VERSION}"
fi

MUSEBIRD_BIN="$("$found_uv" tool dir 2>/dev/null)/infinity-musebird/bin/musebird"
if [ ! -x "$MUSEBIRD_BIN" ]; then
  MUSEBIRD_BIN="$(command -v musebird || true)"
fi
[ -n "$MUSEBIRD_BIN" ] || die "musebird installed but not on PATH; run: $found_uv tool update-shell"

say "Configuring harnesses..."
"$MUSEBIRD_BIN" install "$@"
