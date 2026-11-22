# MuseBird live-job issues — 2026-11-22

## Repair-agent handoff

This report records observed failures and source-level findings. It does not authorize bypassing approvals, automatically granting permissions, deploying anything, retrying the failed job, or merging/implementing its missing deliverable.

User direction: record Muse/MuseBird issues in this repository or GitHub issues rather than Claude Code's SendFeedback, so the repair agent can act on them.

## Environment and reproduction

- Installed MuseBird: `0.1.2`, browser driver.
- Local source checkout: `c6ca150` — `fix(mcp): run browser work off the server's anyio loop`.
- Host: Linux / WSL2; Claude Code `2.1.302`.
- Muse login and side-chat isolation passed `musebird doctor --selftest` before launch.
- Job: `job_20261122_072110_f6f857`.
- Task shape: a fresh playable 3D browser game, built in Muse's sandbox with eight approved fan-out agents.
- Start arguments: `mode="fanout"`, `agents=8`, `sync="git"`, `upload_paths=[]`, `confirm_upload=false`, `driver="browser"`.
- Start succeeded: eight agents accepted, `files_sent=0`, `ignored=[]`.
- The unrelated local project had no commits. No local source was uploaded.

To inspect the preserved record without starting another job:

```sh
musebird jobs show job_20261122_072110_f6f857
musebird jobs show job_20261122_072110_f6f857 --events
```

The record contains the existing side-chat reference. Do not create a new side chat or retry the paid/quota-consuming job merely to inspect this failure.

## 1. High priority: approval detection loops without an actionable question

### Observed behavior

1. Muse reported that `out/plan.json` had been written and all eight workstreams had started. This was a remote progress claim; their actual files were not retrieved or verified.
2. The driver began repeatedly emitting:

   ```text
   Muse is asking for approval; denying (privacy default)
   Muse is working
   ```

3. These messages continued approximately every five seconds for more than ten minutes.
4. Repeated MCP status calls still reported `state="running"`, `pending_question=null`, and no error. They did not identify the approval's operation or affected agent.
5. A steering message asking Muse to identify the exact approval and pause the blocked operation was queued and confirmed sent. No corresponding question or new substantive progress appeared in the captured events.

### Verified source behavior

- `src/musebird/drivers/browser/driver.py:434-439` checks whether any approval-card selector matches, logs a denial, then attempts to click the first globally matched deny/decline button.
- The click is inside `contextlib.suppress(Exception)`. A missing button, timeout, or other click failure is not exposed, and the log is emitted before a successful denial is established.
- `src/musebird/upstream/muse.py:103-104` defines the card selector and global button selector. Detection does not distinguish a visible unresolved request from an existing historical/hidden card.
- This approval branch emits an informational event, not `needs_input`, and does not extract a sanitized question or request identifier.
- `src/musebird/drivers/browser/driver.py:459-464` can still emit generic progress while generation is detected.
- `src/musebird/jobs/worker.py:171-196` updates `last_activity` for every returned event. Repeated approval/progress events therefore count as activity even when no actionable work is visible.
- The worker only changes to `NEEDS_INPUT` when a driver event explicitly has that kind; a UI approval card does not trigger that transition in the inspected branch.

### What remains unknown

The captured data does not establish the requested operation, the affected agent, whether any deny click succeeded, or whether the selector repeatedly matched the same stale card versus distinct new approvals. Do not infer that all approvals were successfully denied or that the remote build was progressing.

### Requested repair behavior

- Preserve a fail-closed approval boundary; do not solve this by granting every request.
- Distinguish active visible unresolved cards from historical/resolved matches.
- Capture a sanitized operation summary and stable request identity; include agent identity when the UI supplies it, without exposing credentials or sensitive payloads.
- Surface a genuine user-decision request through `needs_input`, or terminate with a clear policy-blocked error when the action cannot be approved. Do not leave it indefinitely represented as healthy work.
- Deduplicate unchanged approval events and report click outcomes/errors honestly. Scope any decision button to the identified card.
- Do not replay denied operations or let repetitive generic telemetry indefinitely reset a blocked/no-progress deadline.
- Confirm whether the present selector matches are stale before changing selector values; keep Muse-specific selectors in `upstream/muse.py`.

### Acceptance and regression coverage

- A visible unresolved card causes one actionable event/state transition, with no automatic allow action.
- Hidden/resolved/historical cards do not repeatedly trigger pending-approval handling.
- A failed decision click produces diagnostics rather than an unqualified successful-denial log.
- An unchanged card does not flood the event log or mask a blocked state with generic progress.
- Any legitimate answer resumes the existing thread without duplicating the task or increasing agent count.
- Driver-level tests exercise the actual approval branch, including successful/failed clicks and stale cards; existing mock-driver `needs_input` tests alone do not cover it.
- Validate the fix with a narrowly scoped, explicitly approved live side-chat test. Do not silently restart this eight-agent job.

## 2. No-output terminal failure lacks enough MCP diagnostic context

### Observed terminal result

After approximately 930 seconds from creation, the same job failed:

```text
reply finished without a sentinel
Muse finished but no files were retrievable (no attachments and no manifest)
```

The preserved job record reports:

```json
{
  "state": "failed",
  "error_code": "no_output",
  "error": "Muse reported completion but attached no retrievable files. Open the thread to see what it produced.",
  "uploads_sent": 0,
  "messages_sent": 1,
  "pending_question": null,
  "branch": null,
  "quota_after": null
}
```

A landing-directory path was recorded, but no retrievable deliverable or review branch was available. This does not prove that no files existed remotely; it means the retrieval path produced none. Gameplay, per-agent output, cross-reviews, and the merged build could not be validated.

### Verified diagnostic gaps

- `src/musebird/drivers/browser/driver.py:440-457` permits a settled reply without a sentinel to emit `completed`.
- `src/musebird/jobs/worker.py:175-179` treats `completed` as a signal to attempt landing.
- `src/musebird/jobs/worker.py:294-316` correctly fails with `error_code="no_output"` if there is neither a manifest nor any landed file. Preserve this guard; do not turn an empty result into success.
- `src/musebird/mcp_server.py:269-294` returns `error` but omits the stored `error_code` and existing `thread_url` from `muse_job_status`, so the MCP consumer cannot see all the diagnostic context available through the CLI.
- The no-output branch returns before the following success-path quota capture, and the final record's `quota_after` is null. Actual quota consumed by this failed fan-out is unknown.

### Requested follow-up

- Expose the stored error code and existing side-chat reference through MCP status so an agent/user can distinguish failure types and inspect the right preserved conversation.
- Preserve sanitized retrieval diagnostics: whether completion had a sentinel, which retrieval mechanisms were attempted, and why files/manifest could not be obtained.
- Capture best-effort terminal quota for failed jobs, or explicitly label it unavailable. A failed job must not imply zero quota cost.
- Investigate the remote reply/attachments separately from approval handling. The approval loop and missing output occurred in the same job, but causation between them is not proven.
- Keep side-chat isolation, path/hash validation, explicit approval for local execution/merge, and failure-on-empty-output intact.

### Acceptance and regression coverage

- The existing `tests/test_worker.py::test_completion_with_no_files_fails_loudly` remains green.
- An MCP status test verifies error-code and preserved-thread diagnostics on a failed record.
- Failure-path tests verify terminal quota is collected when available and represented honestly when unavailable.
- Missing/failed attachments and an absent manifest produce `failed/no_output`, never a completed empty deliverable.
- Approval-blocked and retrieval-failed outcomes remain distinguishable; neither is silently retried.

## Scope and exclusions

- No repair was implemented and no replacement job was started by this report.
- Claude Code's earlier Auto Mode classifier errors and MCP disconnections happened outside MuseBird's job logic; they are not established MuseBird defects.
- The separate Playwright MCP's missing Google Chrome binary prevented an independent UI inspection. It is not evidence of a Muse browser-driver failure: Muse's own Chromium health checks passed.
- Do not include account details, cookies, tokens, or the full user task payload in public issues. The job ID, versions, sanitized events, and repository-relative source locations above are sufficient starting evidence.
