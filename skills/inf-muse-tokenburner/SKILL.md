---
name: inf-muse-tokenburner
description: Fan a task out across several Muse subagents that plan, work in parallel, cross-review and merge. Use when the user types /inf-muse-tokenburner or wants maximum parallel effort on a large task. Consumes quota quickly; confirm before starting.
---

# /inf-muse-tokenburner — parallel Muse fan-out

Same flow as `/inf-muse`, with one difference: Muse decomposes the task, runs N agents in parallel,
has them cross-review, then merges. That costs roughly N× the quota of a single job.

**Muse does the work. You orchestrate and review only.**

## Step 1 — check and warn

1. Call `muse_status`. Handle `login_required` exactly as `/inf-muse` does.
2. Read the quota in the response.
3. Tell the user, before anything else:
   - how many agents you plan to use (default 4),
   - that this costs several times a single job,
   - the current quota if known.
4. Ask them to confirm the agent count. If quota `remaining_ratio` is below `0.25`, say so and
   suggest 2 agents or a single-mode job instead.

The server caps `agents` at 8 and will silently scale down under low quota; tell the user the
effective number you used.

## Step 2 — uploads

Identical to `/inf-muse`: first-upload confirmation is required, guard findings block, and
`.musebirdignore` is honoured. Do not skip the confirmation because the job is bigger.

## Step 3 — start

Call `muse_job_start` with `mode: "fanout"` and the confirmed `agents` count. Pass `budget` if the
user gave one.

## Step 4 — poll and relay

`muse_job_wait` in a loop, as in `/inf-muse`. Relay per-agent progress when the events name an
agent; the merged result only matters at the end.

- `needs_input` mid-fanout: ask the user; the answer goes to the whole job.
- If the job fails because one agent failed, report which one and offer to retry that subtask as a
  single job. Do not silently retry.

## Step 5 — review

`muse_files_list` → `muse_files_pull`.

The landing area should contain `plan.json`, one directory per agent, `REVIEW.md` files, and
`merged/`. Check:

1. `plan.json` matches what the user asked for — no invented scope.
2. Each agent's directory has real output, not a stub.
3. Cross-reviews actually flag issues; if every review is empty, say so — it usually means the
   agents agreed too easily.
4. `merged/` is coherent: no duplicated files, no conflicting versions of the same module.
5. Report a compact per-agent table: agent, what it produced, review verdict.

Then ask before merging, same as `/inf-muse`.

## Rules

- Never raise `agents` above the user's confirmed number.
- Never start a fan-out job without the quota warning.
- Never merge or execute returned code without explicit approval.

- **Every job runs in a Muse side chat, never the main chat.** The server enforces
  this and returns `upstream_error` ("refusing to run in the main chat") if it cannot
  open one. Do not work around it, do not tell the user to paste the task into Muse
  directly, and do not retry in a loop — report the error and point at
  `musebird doctor --selftest`.
