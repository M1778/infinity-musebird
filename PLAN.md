# PLAN — infinity-musebird

Building against `Design.md`. Order follows its milestones. Offline-first: every automated test uses
the `mock` driver; anything requiring a real Muse session is a human gate.

## Milestones

| # | Milestone | Deliverable | Human gate |
|---|---|---|---|
| 1 | Scaffold | pyproject, CLI, `MuseDriver` base + mock, job registry (persisted state machine), MCP server exposing all 10 tools, tests | none |
| 2 | Installer | `musebird install/update/uninstall/doctor`, Claude/Codex/OpenCode adapters, backups, dry-run, `install.sh`, `install.ps1`, fixture tests | none (repo creation after README/license/tests) |
| 3 | Session | login flow (headed browser), encrypted vault (keyring → 0600 file fallback), keepalive, quota cache, VM wake | `musebird login` by user |
| 4 | Browser driver | Playwright/CDP persistent thread per job, sentinel completion, resume, `selftest` | live run |
| 5 | Sync | git-branch landing + bundle fallback, manifest/sha256/path verification, landing worktree | live run |
| 6 | Skills + guard | both `SKILL.md`, `.musebirdignore`, secret scan, first-upload confirmation, audit log | none |
| 7 | Fan-out | `tokenburner` mode, agent caps, quota-aware downgrade | live run |
| 8 | Release | docs, TROUBLESHOOTING, checksums, tag + GitHub release | user confirms publish |

## Key design decisions (with reasons)

1. **Single source for Muse's surface.** Every URL, cookie name, selector, and sentinel lives in
   `src/musebird/upstream/muse.py`. The browser driver imports nothing site-specific from elsewhere.
2. **Jobs are files.** `~/.infinity-musebird/jobs/<id>.json` is the source of truth. A background
   worker process (`musebird job-run <id>`) drives the browser so the MCP server can be restarted
   freely. The registry uses an atomic write (temp + rename) plus a lock file.
3. **Mock driver is a first-class driver.** It implements the same `MuseDriver` protocol, simulates
   wake/quota/fanout/sentinel and failure modes, and is what CI exercises end to end.
4. **Never merge into the working tree.** `muse_files_pull` writes into
   `<project>/.musebird/landing/<job>/` and can create a git branch from it; the reviewer merges.
5. **Installer logic is Python only.** `install.sh`/`install.ps1` bootstrap `uv` then delegate to
   `musebird install`; all merge/backup/idempotency logic is unit tested with fixture homes.
6. **Guard fails closed.** Secret scan hit, path escape, symlink escape, oversize file, or missing
   first-upload approval ⇒ refuse and explain; no silent downgrade.
7. **No secrets in logs.** Vault redacts values; audit log stores prompt hashes and file *names*,
   never contents.

## Layout of the work

```
src/musebird/
  cli.py mcp_server.py config.py errors.py audit.py
  installer/{detect,backup,merge,adapters,installer,doctor}.py
  session/{vault,login,keepalive,quota}.py
  drivers/{base,mock}.py drivers/browser/{driver,selectors}.py
  upstream/muse.py
  jobs/{registry,worker,state}.py
  sync/{manifest,landing,bundle,git}.py
  guard/{ignore,secrets,paths}.py
  prompts/contract.md
```

## Verification per milestone

- `uv run pytest`, `uv run ruff check`, `uv run mypy src` (or pyright) all green.
- Milestone 2: install → doctor → uninstall on fixture homes for each harness, twice (idempotency),
  plus `--dry-run` writes nothing.
- Milestone 1/6: end-to-end MCP client test starts `musebird mcp` and drives a full mock job.

## Open questions tracked in docs/HARNESS_NOTES.md

Third-party details are verified against current docs (subagent research + direct fetch) with dates.
Anything live-account dependent is left behind a human gate and marked `@pytest.mark.live`.
