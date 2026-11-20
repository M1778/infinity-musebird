#!/usr/bin/env bash
# Install the local secret-scan hook. No dependencies, no pre-commit needed.
set -euo pipefail
cd "$(dirname "$0")/.."
hook=".git/hooks/pre-commit"
cat > "$hook" <<'HOOK'
#!/bin/sh
# Installed by scripts/install_hooks.sh — blocks commits containing secrets.
exec python scripts/scan_repo.py --staged
HOOK
chmod +x "$hook"
echo "installed $hook"
