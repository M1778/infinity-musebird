# infinity-musebird

**Use [Muse](https://muse.ai) as a subagent inside Claude Code, Codex, or OpenCode.**
Type `/inf-muse build me a thing`; Muse does the work in its own sandbox, and the
results land in your repo on a review branch.

Unofficial. Not affiliated with Meta or Muse.

```bash
curl -fsSL https://raw.githubusercontent.com/M1778/infinity-musebird/main/install.sh | bash
```

Then click through one Muse sign-in. That is the whole setup: the installer registers
the MCP server and both skills in every harness it finds, and `musebird doctor` tells
you what is green.

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/M1778/infinity-musebird/main/install.ps1 | iex
```

Prefer not to pipe a script into a shell? Download it, read it (~60 lines), run it. Or
`uvx infinity-musebird install`. Every real action lives in Python so it is testable
and identical across platforms.

Release assets ship with `SHA256SUMS`; verify with
`sha256sum -c SHA256SUMS --ignore-missing`. Each `install.sh` pins the release tag it
ships in, so the script and the package it installs always match.

> **Before you use this, read [DISCLAIMER.md](DISCLAIMER.md).** This automates a web
> session on your own account. That may violate Muse's terms of service. Anything you
> send leaves your machine and is processed in a third-party sandbox. Returned code is
> untrusted: review it before running or merging.

The disclaimer in full, in brief:

- **Unofficial.** Not affiliated with, endorsed by, or sponsored by Muse or its operators. "Muse" and related names are trademarks of their respective owners.
- **Terms of service.** This tool automates a web session on your own account. Automating a website may violate its terms of service and could result in rate limiting, suspension, or termination of your account. **You are solely responsible** for reading and complying with Muse's terms and for any consequences. This project does not and will not support multi-account rotation or circumventing usage limits.
- **Your data.** Anything you send (source code, files, prompts) is transmitted to a third-party service and processed in its sandbox. Do not send secrets, regulated data, or code you are not permitted to share. Use `.musebirdignore`.
- **Credentials.** The session is stored locally (OS keychain or a restricted-permission file). Treat it like a password; `musebird logout` deletes it.
- **Untrusted output.** Review all returned code. This tool never auto-executes it.
- **No warranty.** MIT, "as is". It relies on an unofficial interface that can break at any time.

## What you get

| Command | What it does |
|---|---|
| `/inf-muse <task>` | Muse works in its sandbox; you get a branch to review |
| `/inf-muse-tokenburner <task> [--agents N]` | Muse fans out to N subagents, cross-reviews, merges |

The harness model only orchestrates: it reads the skill, calls MCP tools, relays
progress, and reviews the diff. Your harness can be running any model.

Ten MCP tools: `muse_status`, `muse_login`, `muse_job_start`, `muse_job_status`,
`muse_job_events`, `muse_job_message`, `muse_job_cancel`, `muse_files_list`,
`muse_files_pull`, `muse_files_push`.

## Requirements

- macOS, Linux, or WSL (Windows native is supported through PowerShell).
- Chrome, Edge, or Chromium. Playwright's Chromium is downloaded only if no system
  browser is found.
- A Muse account in a supported region.
- Python is provided by `uv`; you do not need to install it.

## Manual setup

```bash
uv tool install 'infinity-musebird[all]'
musebird install --yes
musebird login
musebird doctor
```

Useful flags:

```bash
musebird install --dry-run            # print every planned change, write nothing
musebird install --harness claude     # limit to one harness
musebird install --skip-login         # log in later
musebird uninstall                    # remove MCP entries + skills we added
musebird uninstall --purge            # also delete the session, jobs, and logs
musebird update                       # refresh skills and config after an upgrade
```

## How it works

1. The skill checks `muse_status` and decides which files Muse needs (honouring
   `.musebirdignore`, secret scan, and a first-upload confirmation).
2. `muse_job_start` returns a job id immediately. A detached worker drives a real
   browser, wakes the Muse sandbox VM, sends the task plus a prompt contract, and
   streams events to disk.
3. Every job runs in its own Muse **side chat**, never your main conversation, and
   the driver refuses to run if it cannot verify that isolation. Muse writes output
   to `out/` with a `MANIFEST.json` (sha256 per file) and prints a completion sentinel.
4. `muse_files_pull` verifies every hash and commits the result to
   `musebird/<job_id>` — never into your working tree.
5. You review the diff and decide whether to merge.

If a job asks a question, the job state becomes `needs_input` and your harness asks
you. Nothing auto-approves anything.

## Verified in real harnesses

A hardened integration test drives the **actual harness**, not just the MCP server:

```bash
./scripts/harness_test.sh          # /inf-muse and /inf-muse-tokenburner
```

It launches Claude Code with the real MCP server and the real skill, lets the model
decide what to call, and asserts observable state afterwards: a job was created, it
reached `done`, a review branch exists, the deliverable is **on the branch**, the
working tree is untouched, and no deletions were staged. **22/22 assertions pass**,
including a 3-agent fan-out where two of three cross-reviews caught real repetition
issues that were then fixed in the merge.

The same MCP server was also exercised from OpenCode (different model, tool call
succeeded) and Codex (`musebird` registered and callable).

## Verified against a real account

Everything below was exercised live on 2026-11-21, not inferred:

- `musebird login` captures a session and `musebird doctor --selftest` is all green.
- A job opens a side chat, sends the prompt, waits for the sentinel, downloads the
  files Muse attached, verifies their sha256, and commits them to a review branch. A
  full run landed exactly `final.txt` with the requested content, no stale files, and
  an untouched working tree.
- Quota reads correctly (plan, percentage, reset date) and the VM wake is acknowledged.

File retrieval works through Muse's file-card editor: there is no sandbox file API,
and the attachment's static icon is not the file. `docs/HARNESS_NOTES.md` records the
exact selectors and endpoints, with the dates they were verified.

Known limits: sandbox `git push` is unverified because Muse gates egress, so bundle
download is the supported sync path. Muse's UI can change at any time; when a selector
breaks, `musebird doctor --selftest` names it and `src/musebird/upstream/muse.py` is
the only file to edit.

Re-verify any time:

```bash
musebird doctor --selftest
MUSEBIRD_LIVE=1 .venv/bin/python -m pytest tests/test_live.py -v -s
```

## Reviewing results

```bash
musebird jobs list
musebird jobs show <id> --events
git diff HEAD...musebird/<id>
git merge musebird/<id>
```

Files also remain in `.musebird/landing/<id>/` for inspection. Nothing is executed.

## Configuration

| Variable | Purpose |
|---|---|
| `MUSEBIRD_HOME` | state directory (default `~/.infinity-musebird`) |
| `MUSEBIRD_PROJECT` | project root override |
| `MUSEBIRD_MOCK_SCENARIO` | `ok`, `needs_input`, `fail`, `wake_fail`, `manifest_mismatch` for the mock driver |

`.musebirdignore` in your project uses gitignore syntax and is additive to safe
defaults (`.env*`, keys, `.git`, `node_modules`, cookie files, `data/`).

## Development

```bash
uv venv && uv pip install -e '.[all]'
uv pip install pytest pytest-asyncio ruff pyright
.venv/bin/python -m pytest          # 71 tests, no network, no account
```

The entire suite runs against the mock driver. Live tests are marked `@pytest.mark.live`
and skipped by default; run them deliberately against your own account to check that
Muse's selectors still match:

```bash
MUSEBIRD_LIVE=1 .venv/bin/python -m pytest tests/test_live.py -v -s
```

`./scripts/check.sh` is the full gate (lint, types, wheel, tests, secret scan).
See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/HARNESS_NOTES.md](docs/HARNESS_NOTES.md)
for the verified harness/Muse details.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, job lifecycle, landing
- [docs/HARNESS_NOTES.md](docs/HARNESS_NOTES.md) — verified config formats and dates
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — every failure, with a fix
- [SECURITY.md](SECURITY.md) — reporting, data flow, what is stored where

## Credits

Inspired by [www222fff/muse2api](https://github.com/www222fff/muse2api) (MIT), which
does the opposite job: exposing Muse as an OpenAI-compatible API. The browser
automation approach, cookie handling, VM-wake confirmation, and quota parsing derive
from it. See [NOTICE](NOTICE).

## License

MIT. See [LICENSE](LICENSE).
