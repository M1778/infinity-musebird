# Changelog

## 0.1.2 — 2026-11-21

Hardened harness integration. A real Claude Code session (infinity-gptkiller-flash)
now drives the whole flow from the shipped skill: 22/22 assertions for `/inf-muse` and
`/inf-muse-tokenburner`, including a 3-agent fan-out whose cross-reviews caught and
fixed real repetition. OpenCode and Codex were each confirmed to call the tools.

### Added
- `scripts/harness_test.sh` — boots a real harness, asserts a job ran, a review
  branch exists, the deliverable is on it and the working tree is untouched.
- `tests/test_audit.py` — proves the audit log holds prompt hashes and file names
  only, and that cookie values cannot reach it or a vault error.
- Live `needs_input` resume test: Muse asks, the MCP layer answers, a fresh worker
  reattaches to the same thread and lands.
- `MUSEBIRD_DISABLE_KEYRING` so tests can never touch a host keychain.

### Fixed
- Codex dropped a hand-written `[mcp_servers.musebird]` table when it rewrote its
  config; the adapter now uses Codex's own CLI.
- In non-interactive harness mode nothing can approve a tool prompt, so the first
  `muse_status` call stalled. Documented and handled in the harness test.
- A transient 401 during Muse's cookie rotation failed the job; the probe is retried.
- Both skills now require a `git rev-parse` check before choosing `sync`, and forbid
  writing deliverables into the user's tree.
- `lock_status` could block inside `msvcrt.locking` on Windows, deadlocking `doctor`
  while a job held the profile; it now makes one non-blocking attempt.
- Windows path traversal: `..\\escape` and `C:\\evil` passed the archive and
  manifest guards, which only split on `/`.
- Two Windows CI deadlocks/hangs traced to a test patching the global `time` module
  and to tests importing a real keychain.

### Removed
- The `budget` parameter. It was accepted, stored, and never enforced; the skills now
  state a budget in the prompt instead.

CI is green on Linux, macOS and Windows for Python 3.10 and 3.12.

## 0.1.1 — 2026-11-21

First release verified end to end against a real Muse account.

### Fixed (found by live testing, all with regression tests)
- `musebird login` crashed before launching a browser: `headless` was passed twice.
- `__Host-` cookies were rejected on injection ("Cookie should have either url or
  path"); the vault now also refreshes from the live profile, because Muse rotates
  `hatch_sess` mid-session and a login-time copy goes stale.
- Muse's composer has no send button — Enter submits — so the driver now presses
  Enter and records the real `/thread/<uuid>` instead of the `/thread/new` alias.
- VM wake was silently failing: it needs a `User-Agent` plus all three `Sec-Fetch-*`
  headers, a JSON **string** body (a dict is form-encoded and rejected), and the page
  must already be on muse.ai. A 200 acknowledgement is now the success signal;
  `vm_state` stays `UNKNOWN` for a cold sandbox.
- File retrieval: Muse exposes no sandbox file API. Files come from the file-card
  editor's Download control. The old code fetched the card's static icon and saved a
  PNG named `out.tar.gz`.
- Quota: the Settings panel paints nav first and numbers later, so the scrape
  returned text with no percentage. It now waits for a `%`. Empty scrapes are no
  longer cached.
- A finished reply with no sentinel repeated its warning every poll (870 events on
  one job) and never fell through to the manifest check.
- Resuming a thread could baseline on transient hydration bubbles (5 counted where
  the settled DOM had 3), which hid every subsequent reply.
- The sandbox `/workspace/out` persists between jobs, so a new job inherited the
  previous job's files; the contract now clears it first.
- Git landing staged missing parent files as deletions; a review branch could have
  wiped the repository.
- TOML merge/uninstall could delete sections after the musebird table.
- Zip extraction rejected every normal archive (`S_IFREG` intersects `0o120000`).
- Two processes sharing the browser profile raced; there is now a cross-platform
  profile lock with stale-lock recovery and `musebird doctor --fix`.
- `session*` in `.gitignore` excluded `src/musebird/session/` from git and the wheel.

### Added
- `musebird version`, `musebird doctor --fix`, a pre-commit secret hook
  (`scripts/install_hooks.sh`), `scripts/check.sh` as the single gate, and packaging
  tests that fail if a subpackage is missing from the wheel.
- **Hard invariant:** every job runs in a Muse side chat, verified before typing and
  re-checked on resume; the driver fails closed rather than use the main chat.

## 0.1.0 — 2026-11-21

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
