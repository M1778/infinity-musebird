# Troubleshooting

Start with `musebird doctor`. Every failing line prints a one-line fix.

## Install

**`musebird: command not found` after install**

`uv tool install` puts binaries in a directory that may not be on PATH.

```bash
uv tool update-shell   # then open a new terminal
# or:
export PATH="$HOME/.local/bin:$PATH"
```

**Installer says a harness config "is not parseable"**

That file has a syntax error and musebird refused to touch it, on purpose. Fix the
JSON/TOML by hand (a backup of the previous version is in
`~/.infinity-musebird/backups/`), then run `musebird install --yes` again.

**A harness was not detected**

Detection is a PATH check plus a config directory. If the binary is in a non-standard
place, force it:

```bash
musebird install --yes --harness claude,codex,opencode
```

**The MCP server does not appear in the harness**

- Claude Code: `claude mcp list` should show `musebird`. Restart the app.
- Codex: `codex mcp list --json`.
- OpenCode: `opencode mcp list`.

Manual registration is always possible:

```bash
claude mcp add --transport stdio --scope user musebird -- musebird mcp
codex mcp add musebird -- musebird mcp
opencode mcp add musebird --global -- musebird mcp
```

## Login

**`login_required`**

Run `musebird login`. It opens a real browser; complete SSO/2FA there. The window
closes on its own once the session is captured.

**Login never completes**

Muse sets `hatch_sess` and `hatch_native_auth_device`. If either is missing, the
sign-in did not finish. Watch the checkbox/consent screens — they are part of login.

**Login worked, the next command says not signed in**

The browser profile and the vault disagree. Re-run `musebird login`. On Linux, a
locked keyring can silently fall back to the file vault; `musebird status` shows
which backend is in use.

**I want to start over**

```bash
musebird logout            # delete the stored session
rm -rf ~/.infinity-musebird/browser-profile   # delete the browser profile
musebird login
```

## Jobs

**Job sits in `queued`**

No worker started. Start it manually to see the error:

```bash
musebird job-run <job_id>
```

**Job is stuck in `running` and nothing is happening**

`musebird jobs show <id> --events` shows the last events. If there are none for an
hour, the worker died. `musebird jobs cancel <id>`, then start a new job. The Muse
thread still exists at the saved `thread_url`.

**Job failed with `manifest_error`**

Muse wrote a manifest that does not match the files it delivered (missing file,
size or hash mismatch). The landing directory is kept for inspection; the job is
not merged. Ask Muse to regenerate with a follow-up message, or re-run the job.

**Job failed with `upstream_error`**

Muse reported an error, or went quiet without the sentinel for 45 minutes. Check the
thread in the Muse UI; the record and thread URL are preserved.

**`muse_job_status` says `needs_input`**

Muse asked a question. Relay the exact `pending_question` to the user, then send
their answer with `muse_job_message`. The worker resumes automatically.

## Files

**`muse_files_pull` refuses with `guard_violation`**

The project is not a git repository, or a path escaped the landing area. Use
`sync=bundle` (default) or `git init` the project first. Traversal and symlinks are
rejected by design.

**Nothing landed**

`muse_files_list` shows what the driver saw. For the browser driver, downloads only
work for attachments Muse actually rendered in the conversation. If Muse placed
files in the sandbox but never attached them, ask it (via `muse_job_message`) to
write `out.tar.zst` and `MANIFEST.json` and attach the bundle.

**The diff includes files I did not want**

Add them to `.musebirdignore` and re-run. Push never re-adds ignored paths.
