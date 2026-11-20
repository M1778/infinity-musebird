# Changelog

## 0.1.0 — unreleased

First working release.

### Added
- `musebird` CLI: `install`, `update`, `uninstall`, `login`, `logout`, `status`,
  `doctor`, `mcp`, `jobs`, `job-run`.
- MCP stdio server with 10 async tools (`muse_status` through `muse_files_push`).
- Installer with adapters for Claude Code, Codex CLI and OpenCode, timestamped
  backups, merge-only edits, `--dry-run`, `--harness`, idempotent re-runs, and
  uninstall that leaves hand-written skills alone.
- Skills `/inf-muse` and `/inf-muse-tokenburner`.
- Driver interface with a deterministic `mock` driver (all tests) and a Playwright
  `browser` driver (real Muse session).
- Durable job registry with a detached worker per job, incremental event log,
  resume by thread URL, and `needs_input` surfacing.
- Prompt contract with `INF-MUSEBIRD-DONE` / `-NEEDS-INPUT` sentinels and a
  `MANIFEST.json` fallback.
- Sync: sha256 manifest verification, hostile-archive-safe extraction, landing in
  `.musebird/landing/<job>/`, and `musebird/<job>` branch landing that never touches
  the working tree.
- Guard: `.musebirdignore`, regex + entropy secret scanning, path allowlist,
  symlink rejection, size caps, first-upload approval, fan-out caps,
  quota-aware scale-down, and an append-only audit log.
- Session: real-browser login, keyring-or-encrypted-file vault, quota cache,
  `__Host-` cookie handling, VM wake with state polling.
- CI on Linux/macOS/Windows with a repository secret scan and an installer smoke
  test against fixture homes.
