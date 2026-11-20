# PLAN — infinity-musebird

Built against `DESIGN.md`. Offline-first: every automated test uses the `mock`
driver; anything needing a real Muse session is a human gate.

## Status at v0.1.0

| # | Milestone | State | Notes |
|---|---|---|---|
| 1 | Scaffold | done | pyproject, CLI, `MuseDriver`, mock driver, durable job registry, MCP server with 10 tools |
| 2 | Installer | done | 3 adapters, backups, dry-run, idempotent, uninstall, fixture tests, repo created and published |
| 3 | Session | implemented, live-gated | login, AES-GCM vault or keyring, quota cache, VM wake; needs one human sign-in |
| 4 | Browser driver | implemented, live-gated | persistent profile, side-chat isolation, sentinel completion, resume, selftest |
| 5 | Sync | done | manifest verify, hostile-archive extraction, review branch that never deletes |
| 6 | Skills + guard | done | both `SKILL.md`, `.musebirdignore`, secret scan, first-upload approval, audit log |
| 7 | Fan-out | done | `tokenburner`, agent cap 8, quota-aware scale-down |
| 8 | Release | done | v0.1.0 tagged, assets + SHA256SUMS, docs, troubleshooting |

## Hard invariants (do not regress)

1. **Side chat only.** Every job runs in a Muse side chat. The driver verifies the
   marker and raises rather than writing into the main conversation. Resume
   re-checks it. Covered by `tests/test_side_chat.py`.
2. **Landing only adds and modifies.** A review branch never stages a deletion, so
   merging it cannot wipe a repository. Covered by
   `tests/test_worker.py::test_git_landing_never_deletes_existing_files`.
3. **Muse output is untrusted.** Paths validated, hashes required (no empty-field
   bypass), unlisted files fail the job, archives checked for traversal and links,
   nothing returned is ever executed.
4. **Secure defaults cannot be un-ignored.** A repository's `.musebirdignore` can
   ignore more, never less.
5. **Secrets never leave by accident.** Regex + entropy scan before every upload and
   push; audit log stores hashes and file names only.
6. **`doctor` tells the truth.** Session validity is `null` until probed live; every
   failure carries a one-line fix.

## Key decisions

1. **Jobs are files.** `~/.infinity-musebird/jobs/<id>.json` is the truth; a
   detached worker (`musebird job-run <id>`) drives the browser. The MCP server and
   harness can restart freely; resume reattaches to the saved thread.
2. **Mock driver is first class.** CI proves the full job lifecycle without network
   or an account.
3. **Installer logic is Python.** `install.sh`/`install.ps1` only bootstrap `uv`;
   all merge/backup/idempotency logic is unit tested against fixture homes.
4. **Prompts carry a contract.** Sentinels plus `MANIFEST.json` with sha256 per file;
   the last sentinel in the text wins, so a resumed thread's old question is ignored.

## Open / human-gated

- First live run against a signed-in account: `musebird doctor --selftest` and
  `MUSEBIRD_LIVE=1 pytest tests/test_live.py -s`. This names any selector or endpoint
  that changed, including the side-chat control.
- Sandbox `git push` is unverified (Muse gates egress); bundle download is the
  supported sync path.
- Muse's UI could change at any time; `upstream/muse.py` is the single file to edit.

## Verification

`./scripts/check.sh` = ruff, pyright, wheel build, pytest, repository secret scan.
Same as CI on Linux/macOS/Windows. 131 tests, 3 live tests skipped by design.
