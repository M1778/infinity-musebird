# Security policy

## Reporting a vulnerability

Use GitHub's private security advisories:
<https://github.com/M1778/infinity-musebird/security/advisories/new>

Do **not** open a public issue for a vulnerability. Do not paste cookies, session
files, JWTs, or account identifiers into any issue, PR, or discussion. Reproduce
with obviously fake values (`hatch_sess=FAKE`).

We aim to acknowledge within 72 hours and to ship a fix or mitigation within 30
days for confirmed issues in this project's own code.

## What is in scope

- The `musebird` package: installer, guard, vault, sync/landing, MCP server, CLI.
- Path traversal, symlink escape, or arbitrary write via bundles/manifests.
- Secret-scan bypasses that let credential-shaped data reach an upload.
- Vault weaknesses: plaintext leakage, world-readable files, cookie values in logs
  or errors.
- Installer behaviour that overwrites or corrupts an unrelated harness config.

## What is out of scope

- Muse's own service, its terms of service, and any rate limiting or account
  action that follows from automating your own account.
- Selector breakage when Muse changes its UI. That is expected; report it as a bug,
  not a vulnerability.
- The fact that data you upload leaves your machine. That is the documented
  purpose of the tool.

## Data flow

Upload path: `project files -> musebird guard -> Muse sandbox`.

Local storage, all under `~/.infinity-musebird/` (or `%LOCALAPPDATA%\infinity-musebird`):

| Path | Contents | Protection |
|---|---|---|
| `session.enc` / OS keychain | Muse cookies | keyring, else AES-GCM + 0600, else 0600 plaintext with `doctor` warning |
| `browser-profile/` | Chromium profile including the live session | user-owned directory |
| `jobs/*.json` | job state, prompt text, event log | 0700 directory |
| `logs/audit.jsonl` | prompt hashes, file names, timings | 0600 |
| `backups/` | copies of harness configs before edits | 0600 |

Nothing is sent anywhere except to Muse, and no telemetry is collected.
