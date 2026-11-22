# Harness notes — verified third-party behaviour

Everything here was checked against the sources listed, on the dates shown. The
installer depends on these facts, so when a harness changes, update this file and
`src/musebird/installer/` together.

## Muse (the upstream service)

Verified **2026-11-20** against the live site and
[`www222fff/muse2api`](https://github.com/www222fff/muse2api) at commit `3a20b5e`
(2026-11-01).

| Fact | Value |
|---|---|
| Product | Meta's Muse, `https://muse.ai` (launched 2026-09-08). "hatch" is the internal client codename. |
| Landing / login | `https://muse.ai` — Meta OIDC, SSO and 2FA work in a real browser |
| New thread | `https://muse.ai/thread/new` |
| Thread | `https://muse.ai/thread/<id>` |
| Session probe | `GET /api/session` → 200 with `status == "assigned"`; 401 when signed out |
| VM wake | `POST /api/hatch/vm/wake` with `{vm_id, retry_count, connect_attempt_id}`; confirm by polling `vm_state == "RUNNING"`. A 403 often means "already running" — never trust the wake response alone. |
| Quota | No API. UI scrape: Settings → Usage dialog. Weekly percent + reset date + extra token balance. Cached 30 min. |
| Cookies | `hatch_sess` + `hatch_native_auth_device` required. `hatch_gw`, `hatch_vml` optional since 2026-10 (Muse stopped setting them on every session). `hatch_sess` may be `__Host-` prefixed, in which case it must be set with `url=`, not `domain=`. |
| Chat send | Textarea + native value setter so React sees the change, then the send button; Enter as fallback |
| Completion | Stop button gone + reply text stable for 4 polls. **We use the prompt-contract sentinel as the primary signal and the DOM only as a hint.** |
| Downloads | Attachments are `blob:` URLs valid only inside the document, so download must be an in-page `fetch()` |
| Approvals | Muse shows inline approval cards (e.g. publishing a public link). muse2api denies them by default; we do the same, and log it. |
| Official API | **None for the Muse agent.** `dev.meta.ai` is the Meta Model API (models, not the agent) and `Muse Code` is a separate terminal product. |
| Sandbox internet | **Verified (egress only).** From the sandbox, `curl https://github.com` → 200 and `git ls-remote https://github.com/git/git HEAD` returned a real SHA. |
| Sandbox git push | **Unverified** (credentials never tested). Bundle download is the supported sync path. |
| Sandbox persistence | **Shared across jobs.** `/workspace/out` survives between side chats, so the prompt contract deletes it before starting; a fresh job otherwise lands the previous job's files. |
| File retrieval | **Verified.** An agent-produced file renders as `hatch-chat-file-card-<hex(utf8(path))>` — a span with `role=button` and a static icon, no href or download attribute. Clicking opens `hatch-file-editor`; `hatch-file-editor-unsupported-download` (or the more-actions "Download" menuitem for text) fires a real Playwright download event. Fetching the icon or a `blob:` URL does **not** work for files. |
| Sandbox file API | **Does not exist.** `/api/hatch/vm/{id}/files`, `/api/files`, `/api/library`, `/api/artifacts` and ~10 similar guesses all return `200 text/html` — the Next.js SPA, a soft-404. Check `content-type` before trusting a 200. |
| VM wake | `POST /api/hatch/vm/wake` needs a `User-Agent` **and** all three `Sec-Fetch-*` headers or it answers 403; the body must be a JSON **string** (`data=` form-encodes, Muse replies 400 "Invalid retry_count"). A 200 `{"status":"wake_requested"}` is the success signal: `vm_state` stays `UNKNOWN` for a cold sandbox and `RUNNING` may never appear. |
| Quota | **Verified.** Dock (`hatch-dock-more`) → Settings menuitem → a `[role=dialog]` whose text contains "Usage". The panel paints nav first (no numbers) then content, so wait for a `%`. Live text yielded plan `Free`, `0% used`, `resets on Nov 27`, `1B tokens left`. No usage JSON endpoint exists. |
| Side chat | `/thread/new` **is** a side chat (the header shows "Back to main chat"). Markers: `button[aria-label*="Back to main chat"]`, `hatch-side-chats-panel-shell`, `[data-testid*="side-chat"]`. |
| Send | Muse's composer has **no send button**. Enter submits, the composer clears, and the URL becomes `/thread/<uuid>` a few seconds later. |
| Subagents | Consumer Muse has side chats and parallel tasks but no documented subagent syntax. Fan-out is therefore a **prompt contract** ("decompose, run N agents, cross-review, merge"), not an API feature. |
| Side chat | Use regardless of user preference: every job runs in a side chat so the main conversation is untouched. The control is a UI element, so `SIDE_CHAT_BUTTON` / `side_chat_marker` are selectors that `selftest` reports on. |

Fragile selectors (ranked, from muse2api's `dom.py`, which its authors describe as
the single point of breakage): `div[class*="hatch-agent-bubble-bg"]` (CSS-module
hash), the hydration attribute, attachment testid prefix, settings dock + menu label
match, and the Stop button. All of them live in `upstream/muse.py` and `selftest`
reports which one is missing.

## Claude Code

Verified **2026-11-20** against `claude 2.1.302` and
<https://code.claude.com/docs/en/mcp>, <https://code.claude.com/docs/en/skills>.

| Fact | Value |
|---|---|
| Detection | `claude` on PATH, `~/.claude/`, `~/.claude.json` |
| User-scope MCP file | `~/.claude.json`, **top-level** `mcpServers` |
| CLI | `claude mcp add --transport stdio --scope user musebird -- musebird mcp` |
| `settings.json` | Does **not** store MCP servers (verified: `mcp get` cannot see an entry there) |
| Skills | `~/.claude/skills/<name>/SKILL.md` |
| Frontmatter | all optional; `description` drives auto-invocation; `allowed-tools` supported |
| Invocation | `/inf-muse` |
| Duplicate `mcp add` | exits 1, refuses. Remove first to change a definition. |
| Windows | `%USERPROFILE%\.claude`, `%USERPROFILE%\.claude.json`; `CLAUDE_CONFIG_DIR` relocates both |

We edit `~/.claude.json` directly (with a backup) rather than shelling out, so the
installer works when `claude` is not on PATH. The entry is `{"type":"stdio",
"command":"musebird","args":["mcp"],"env":{}}`.

## Codex CLI

Verified **2026-11-20** against `codex-cli 0.166.0` and
<https://developers.openai.com/codex/extend/mcp>,
<https://developers.openai.com/codex/build-skills>.

| Fact | Value |
|---|---|
| Detection | `codex` on PATH, `$CODEX_HOME` or `~/.codex` |
| Global MCP file | `~/.codex/config.toml`, table `[mcp_servers.musebird]` |
| CLI | `codex mcp add musebird -- musebird mcp` (global only; no scope flag) |
| Skills | **`~/.agents/skills/<name>/SKILL.md`** — not `~/.codex/skills` |
| Frontmatter | `name` and `description` **required** (stricter than Claude Code) |
| Invocation | `$inf-muse` or `/skills` |
| Legacy prompts | `~/.codex/prompts/*.md` → `/prompts:name`, deprecated — we do not install there |
| Duplicate `mcp add` | silently replaces, exit 0 — so check first |
| Output of writes | TOML serializer drops inline comments; we back up before editing |

## OpenCode

Verified **2026-11-20** against <https://opencode.ai/docs/mcp-servers> and
<https://opencode.ai/docs/skills> (V2 docs).

| Fact | Value |
|---|---|
| Detection | `opencode` on PATH, `~/.config/opencode/` |
| Global config | `~/.config/opencode/opencode.json` or `.jsonc` |
| MCP shape (V2) | `mcp.servers.<name> = {"type": "local", "command": ["musebird", "mcp"]}` |
| CLI | `opencode mcp add musebird --global -- musebird mcp` |
| Skills | `~/.config/opencode/skills/<id>/SKILL.md`; also discovers `~/.claude/skills` and `~/.agents/skills` |
| Frontmatter | optional; `description` needed for model discovery; `disable-model-invocation: true` to hide |
| Invocation | `/inf-muse` |
| Config reload | live, no restart needed |

Because OpenCode also reads `~/.claude/skills` and `~/.agents/skills`, a machine
with all three harnesses ends up with the skills installed in three places. That is
intentional: each harness gets its native location, and `uninstall` removes only
paths recorded in `install-state.json`.

## What a real harness run found

Verified 2026-11-21 by running Claude Code against the live MCP server
(`scripts/harness_test.sh`). Each of these was invisible to unit tests:

| Symptom | Cause | Fix |
|---|---|---|
| Model stopped after one call | `-p` has nobody to approve a permission prompt | the harness test grants `mcp__musebird` explicitly; users see the same prompt interactively |
| Model chose `sync=bundle` inside a git repo | the skill described the rule but did not require the check | both skills now require `git rev-parse --is-inside-work-tree` first |
| Model wrote the deliverable itself | the skill forbade merging but not `Write` | skills forbid Write/cp/checkout of deliverables explicitly |
| `codex mcp list` lost our entry | Codex rewrites config.toml and drops tables it did not write | the adapter uses `codex mcp add` whenever it targets the same file |
| One job failed "session not valid" while status was healthy | Muse rotates cookies mid-session; one 401 is not proof | the worker retries the probe before asking the user to log in |

## Approval cards (verified 2026-11-22)

`[data-testid="hatch-inline-approval-card"]` keeps matching cards that were already
**resolved**: they stay in the transcript with no live decision control. Counting the
selector therefore reports a pending approval forever — one live job emitted 128
identical denial events over ten minutes and then failed with no output.

The only reliable pending test is behavioural, so it runs in-page
(`muse.APPROVAL_SCAN`): the card must be on screen, visible, and contain an **enabled**
deny/decline control. A decision is clicked inside that specific card, matched by its
text, and the click result is reported. Cards that cannot be dismissed become
`needs_input` rather than a loop.

### Decision policy (user direction, 2026-11-22)

- **No input controls → approve.** Such a card is an acknowledgement, not a question;
  approving keeps the build moving.
- **Has input controls → decline.** Answering a form would mean inventing the user's
  intent, which the tool must never do.
- **Neither control works → `needs_input`** with the request text, so the user decides.

Verified in-page by counting visible `input`/`textarea`/`select`/`contenteditable`
elements inside the card; no Muse-specific markup assumption beyond that.

## Toolchain

- Package: Python 3.10+, `uv` for install and Python provisioning.
- MCP SDK: `mcp>=2.3,<3` — v2 renamed `FastMCP` to `MCPServer`
  (`from mcp.server.mcpserver import MCPServer`). v1 examples do not apply.
- Browser: Playwright, preferring a system Chrome/Edge channel over a downloaded
  Chromium.
