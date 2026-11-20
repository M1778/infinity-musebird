# Architecture

```
Harness (Claude Code / Codex / OpenCode)          only orchestrates: reads a skill,
  └─ skills /inf-muse, /inf-muse-tokenburner      calls MCP tools, reviews results
       └─ MCP server (stdio): `musebird mcp`
            ├─ session/   login, vault, keepalive, quota cache
            ├─ drivers/   mock | browser (Playwright) | http (reserved)
            ├─ jobs/      durable registry + detached worker per job
            ├─ sync/      manifest verify, bundle extract, git landing
            └─ guard/     ignore rules, secret scan, path allowlist
                  └─ Muse.ai  (thread -> sandbox VM -> agent)
```

## Why a separate worker process

MCP calls must return in seconds; Muse jobs run for minutes or hours. So:

1. `muse_job_start` validates, guards the uploads, writes a job record, and spawns
   `musebird job-run <id>` detached. It returns the id immediately.
2. The worker owns the driver and the browser for the job's lifetime, appending
   every event to `jobs/<id>.jsonl`.
3. Status tools read files. The MCP server can be restarted, the harness can be
   restarted, and the job continues.

A crashed worker leaves the job `running` with an old `updated_at`; `musebird doctor`
lists those as stuck. The thread URL is persisted, so a resumed worker reattaches
to the same Muse conversation instead of starting over.

## Data model

`jobs/<id>.json` — one record, written atomically:

```json
{
  "id": "job_20261121_101500_a1b2c3",
  "prompt": "...", "mode": "single|fanout", "agents": 1,
  "sync": "bundle|git", "project": "/abs/path",
  "state": "queued|waking|running|needs_input|done|failed|cancelled",
  "cursor": 12, "thread_url": "...", "landing_dir": "...", "branch": null,
  "quota_before": {...}, "quota_after": {...}, "pending_question": null
}
```

The state machine is in `jobs/state.py` and refuses backward transitions. Only the
worker decides when a running job stops; `muse_job_cancel` writes a flag file.

## Side-chat isolation (hard invariant)

Every job runs in its own Muse **side chat**. The driver opens a fresh thread, uses
the side-chat control, and then verifies Muse marks the thread as a side chat before
typing anything. If that check fails it raises rather than writing into the account's
main conversation, so a Muse UI change breaks jobs loudly instead of silently
polluting the user's main chat. Resuming a saved job re-checks the same marker.

## Prompt contract

Every job's prompt is the user's task plus `prompts/contract.md`, which tells Muse to
work in `/workspace/out/`, write `MANIFEST.json` with sha256 per file, and print
`INF-MUSEBIRD-DONE <job_id>` (or `INF-MUSEBIRD-NEEDS-INPUT <job_id>: <question>`).
Sentinel parsing takes the last match, and the worker falls back to tracing
`MANIFEST.json` if the sentinel never appears.

## Landing

`sync=bundle` (default, works without git):

```
Muse sandbox -> out.tar.zst + MANIFEST.json -> in-page download
  -> <project>/.musebird/landing/<job>/  -> verify sha256/size/path
  -> review -> human merges
```

`sync=git`: the verified landing directory is committed to
`musebird/<job_id>` using a temporary index and a borrowed work tree, so the user's
working tree, index, and current branch are never touched even with uncommitted
changes. Review with `git diff HEAD...musebird/<job_id>`.

The sandbox's own `git push` is **not** relied on: Muse gates egress through
Sentinel and git push from the VM is unverified. The bundle path is the contract.

## Guard

- `.musebirdignore` (gitignore subset) plus secure defaults (`.env*`, keys, `.git`,
  `node_modules`, cookie/session files).
- Regex + entropy secret scan before upload; a hit is a hard block that reports
  file, line, and rule name — never the value.
- Path allowlist rooted at the project; symlinks are rejected, not followed.
- Size and file-count caps, and manifest/archive traversal checks.
- First upload per project requires explicit confirmation; the approval is stored in
  `approvals.json`.
- Fan-out is capped at 8 agents and scaled down when quota is low.
- Audit log stores prompt hashes and file names only.

## What is deliberately not here

- No OpenAI-compatible API. That is muse2api's job.
- No multi-account rotation or limit circumvention. One account, quota-aware.
- No bundled or hosted credentials. No telemetry.
- No auto-execution of returned code, ever.
