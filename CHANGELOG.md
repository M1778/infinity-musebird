# Changelog

## 0.1.5 — 2026-11-22

A question is not a result. From `job_20261122_172637_45f539`, where Muse asked
"do you confirm you want me to spend the 8 agents / quota?" and the job failed 60
seconds later with `no_output` — the question was lost and could not be answered.

### Fixed
- **A sentinel-less reply asking a question becomes `needs_input`.** Classification is
  conservative and looks for evidence of asking: explicit confirmation phrases
  ("do you confirm", "should I proceed", "let me know"), a trailing question together
  with waiting language ("I haven't started", "until you confirm"), or a short reply
  that is only a question. A bare `?` is never enough, and questions inside code fences
  or block quotes are ignored. Anything unproven is reported as `ambiguous`, not
  silently completed.
- **The failure message no longer claims completion.** It said "Muse reported
  completion" even when the reply said it had not started; it now says the turn ended
  without retrievable files and points at `muse_job_message` to resume.
- **An answered job can be resumed.** `muse_job_message` accepts a `FAILED` job when it
  has a saved thread and the failure was `no_output`/`upstream_error`, moves it back to
  `running` on that same thread, and does not re-send the task or add agents. Guard and
  validation failures stay terminal, because retrying those only fails again.
- **The prompt states pre-approved scope.** Muse was stopping to confirm agent count,
  quota spend and sandbox limits the user had already approved. The job prompt now says
  so explicitly and instructs it not to ask again.

### Added
- `tests/test_reply_classification.py` — the live two-chunk reply, an explicit sentinel,
  a deliverable containing a question, a question in a code fence, a quoted
  instruction, a plain finished reply, and the resume/refuse-resume paths.

Verified against the preserved job: reconstructing its two text events through the new
classifier yields `needs_input` with the real question.

## 0.1.4 — 2026-11-22

Correctness of what the tool *claims*, after review of the 0.1.3 fixes.

### Changed
- `quota_charged` becomes an honest `quota` report: `{observed, charge, delta_used,
  note}`. `charge` is `unknown` unless a comparable before/after pair exists, and a
  missing snapshot is explicitly not-free.
- Corrected the false claim that Python 3.10's `asyncio.TimeoutError` is
  `CancelledError`. They are unrelated; the dual catch is still required because the
  two timeout classes differ until 3.11.

### Added
- `tests/test_quota_report.py` — missing, unchanged, increased, reset and partial
  snapshots.
- A test that caller cancellation stays a cancellation.

## 0.1.3 — 2026-11-22

Fixes from a live 8-agent job that failed with no output
(`job_20261122_072110_f6f857`, report in `docs/MUSE_ISSUES_2026-11-22.md`).

### Fixed
- **Approval detection looped forever.** The card selector also matched *resolved*
  cards left in the transcript, so every poll logged a decision again — 128 identical
  events in one job, while `muse_job_status` reported healthy `running`. Approvals are
  now scanned in-page: only visible cards with a live decision control count, each
  distinct card is reported once, and the click is scoped to that specific card with
  its outcome reported. Verified against the failed thread: 0 pending now, where the
  old count reported one on every poll.
- **Approval policy (user direction).** A card with no input controls is an
  acknowledgement, so it is **approved** automatically to keep a build moving. A card
  that asks for input is a real decision and is **declined**, because answering it
  would mean inventing the user's intent. A card that can be neither approved nor
  declined becomes `needs_input` with the request text.
- **Python 3.10 timeout.** `run_off_loop` caught only the builtin `TimeoutError`, but
  until 3.11 `asyncio.TimeoutError` is a *separate* `Exception` subclass and
  `asyncio.wait_for` raises that flavour, so the timeout escaped unhandled and failed
  CI on every 3.10 job. Both spellings are now caught; `asyncio.CancelledError` is a
  `BaseException` and is deliberately not caught, so real cancellation still
  propagates as cancellation.

### Corrected after review
- The explanation for the 3.10 fix above originally claimed `asyncio.TimeoutError` is
  `CancelledError` on 3.10. It is not: they are unrelated classes (`Exception` vs
  `BaseException`). The code was right, the comment was wrong. Verified against
  CPython 3.10.22 and documented in a test that asserts the class relationship.
- `quota_charged` renamed to an honest `quota` report. The old boolean measured whether
  a snapshot existed, not whether the job cost anything: a missing snapshot is
  **unknown**, not free, and a present one may be unchanged or reflect other activity.
  It now returns `{observed, charge, delta_used, note}`, where `charge` is
  `unknown` unless a comparable before/after pair exists, and even then is labelled an
  account-level delta rather than a per-job cost.
- **A blocked job looked alive.** Generic `progress` events reset the no-activity
  deadline, so a stuck job could run indefinitely. Only real output now counts.
- **Failures were undiagnosable from MCP.** `muse_job_status` omitted the stored
  `error_code` and the preserved `thread_url`; it now returns both plus a `next_step`
  and `quota_charged`.
- **A failed job reported no quota.** Terminal quota is captured on every failure
  path, because an 8-agent run costs real quota even when it returns nothing.
- **Retrieval attempts were invisible.** The events now record how many files the
  download returned and what was found in the landing directory.

### Added
- `tests/test_approval_diagnostics.py` — driver-level coverage of the approval branch
  (pending, resolved, failed click, distinct cards, scan failure) and of the failure
  diagnostics, including that generic progress cannot mask a blocked job.

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
