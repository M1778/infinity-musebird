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

## Resolution — 0.1.3 (2026-11-22)

Both issues are fixed and covered by `tests/test_approval_diagnostics.py`.

1. **Approval loop.** The selector matched resolved cards. Pending is now decided
   behaviourally in-page (visible + live decision control). Re-checked against the
   preserved thread: 0 pending, where the old code reported one on every poll.
   Policy per user direction: a card with **no input controls is approved** (an
   acknowledgement, not a question); a card with **input controls is declined**
   (answering it would invent the user's intent); anything unresolvable becomes
   `needs_input` with the request text.
2. **Failure diagnostics.** `muse_job_status` now returns `error_code`, `thread_url`,
   `next_step` and `quota_charged`; terminal quota is captured on every failure path;
   retrieval events record how many files the download returned; generic progress no
   longer resets the no-activity deadline.

The job remains failed and was **not** retried: the 8-agent run cost real quota, and
retrying it is the user's decision, not the repair agent's.

## Follow-up verification — 0.1.3 (2026-11-22)

### Installed release versus the running MCP process

- The CLI reports `musebird 0.1.3`; GitHub release `v0.1.3` is published and is not a draft or prerelease.
- Both the checkout and installed `mcp_server.py` contain the new failure diagnostic fields.
- This existing Claude Code sessions `muse_job_status` response still omits `error_code`, `thread_url`, `next_step`, `agents`, and `quota_charged`. A Muse MCP process that started before the update is still present. These observations are consistent with the session using the old loaded server; the specific process-to-session mapping was not established.
- Reconnect `musebird` in `/mcp` before any retry and verify the new status fields through this sessions MCP connection. Installing a new package does not reload modules in an already running process. Do not kill unrelated sessions processes.

### Remaining issue: `quota_charged` represents snapshot availability, not a charge

Verified in the shipped tag and installed source at `src/musebird/mcp_server.py:329`:

```python
quota_charged=job.quota_after is not None
```

For the preserved failed job, `quota_after` is null. The new server therefore reports `quota_charged=false`, although no terminal quota measurement exists. Conversely, any non-null snapshot produces true even if it is unchanged from `quota_before`. The boolean measures whether a snapshot was collected, not whether this job consumed quota.

Requested repair:

- Expose measurement availability under an accurate name such as `quota_observed`.
- Represent the actual charge as unknown unless supported by comparable before/after measurements or an authoritative per-job billing signal. Account-level quota deltas can also include other concurrent jobs, resets, rounding, or cached values; do not silently treat them as exact per-job cost.
- Keep historical jobs with no terminal snapshot explicitly unknown, not free.
- Cover missing, unchanged, increased, stale, and reset/incomparable snapshots in tests. Preserve failure-path best-effort capture.

### Timeout fix is sensible; its class-relationship explanation is incorrect

`run_off_loop` now catches both `(asyncio.TimeoutError, TimeoutError)` and normalizes to the built-in exception. That is appropriate for supported versions. However, `src/musebird/offloop.py:56-58`, `tests/test_offloop.py:69`, and the release handoff claim that Python 3.10 `asyncio.TimeoutError` is `CancelledError`.

Verified against the official Python 3.10 documentation and CPython 3.10 source:

- `asyncio.TimeoutError` is a separate `Exception` subclass in 3.10, different from the built-in `TimeoutError`.
- `asyncio.CancelledError` is a separate `BaseException` subclass. It is not an alias for `asyncio.TimeoutError`.
- Catching both timeout spellings addresses the pre-3.11 distinction; it does not mean that timeouts and cancellations are the same exception.

References:

- https://docs.python.org/3.10/library/asyncio-exceptions.html
- https://github.com/python/cpython/blob/v3.10.14/Lib/asyncio/exceptions.py

Requested correction: fix the comment/test docstring/handoff explanation without undoing the working dual-timeout catch. Verify separately that true caller cancellation remains cancellation rather than being converted into a timeout.

### Verification limits

The release and source/installed-file checks above were performed in this session. CI totals, checksums, the new live pending-card behavior, and gameplay were not independently rerun. No replacement job, live pending approval, or quota-consuming game retry was started.

## Resolution of the follow-up review — 0.1.4

Both corrections accepted; verified before changing anything.

### `quota_charged` was measuring the wrong thing

Correct. `quota_after is not None` reports snapshot availability, not a charge. It is
replaced by:

```json
"quota": { "observed": false, "charge": "unknown", "delta_used": null,
           "note": "no terminal quota snapshot was captured, so this job's cost is
                    unknown, not zero" }
```

`charge` is `observed-delta` only when a before/after pair exists *and* the quota
window is the same; even then the note states it is an account-level delta, not
per-job billing. Falling usage, a reset, or a missing percentage all yield `unknown`.
Covered by `tests/test_quota_report.py` for missing, unchanged, increased, reset and
partial snapshots. Checked against the preserved job: `charge: unknown`.

### The 3.10 explanation was wrong

Correct, and re-verified on CPython 3.10.22:

```
asyncio.TimeoutError is builtin TimeoutError: False
asyncio.TimeoutError is CancelledError:        False
MRO: TimeoutError -> Exception -> BaseException
CancelledError subclasses Exception:           False
```

The real failure was simpler: until 3.11 the two timeout classes are distinct and
`asyncio.wait_for` raises the *asyncio* flavour, so catching only the builtin let it
escape. The dual catch stays; the comment, the test docstring and the changelog are
corrected. `asyncio.CancelledError` is a `BaseException` and is deliberately not
caught, and a new test asserts that caller cancellation still surfaces as
cancellation.

### Stale MCP process

Confirmed as the cause of the old fields appearing in that session: an MCP server
started before the update keeps its loaded modules. A reconnect picks up the new
fields; the freshly started server returns `error_code` and `quota` for the preserved
job.

## Live retry failure — 0.1.4 (2026-11-22)

### Summary: a plain-text confirmation request becomes terminal `no_output`

The user explicitly authorized a new eight-agent attempt after the fixes. The installed CLI reports `musebird 0.1.4`, and this sessions active MCP connection now exposes the updated error/thread/quota fields. Login was verified before launch; no local project files were uploaded.

- Retry job: `job_20261122_172637_45f539`.
- Source revision: `74a3da4` — `fix: report quota honestly and correct the 3.10 timeout explanation`.
- Start: fanout, eight agents accepted, zero files sent, `sync=git`.
- Terminal state after 60.6 seconds: `failed`, `error_code=no_output`, `pending_question=null`, no review branch or retrievable deliverable.
- Before/after account quota snapshots: 5.0% used / 5.0% used. The new quota report correctly labels the observed delta of 0.0 as account-level, not exact per-job billing; this does not prove the run was free.

### Preserved event sequence

The entire run has 12 events. The substantive remote reply was split across two text events:

> I havent started this one — stopping to check first, like the last pasted MuseBird contracts.
>
> This contract (job_20261122_172637_45f539) asks for a full 8-agent fan-out to rebuild ZORO from scratch, all work only in out/, with a merged game + manifest + out.tar.gz attached.
>
> In your own words: what is this retry actually testing, and do you confirm you want me to spend the 8 agents / quota and follow those sandbox limits?
>
> I wont spawn agents or create files until you confirm that scope.

No `INF-MUSEBIRD-NEEDS-INPUT` or completion sentinel appeared. The next events were:

```text
reply finished without a sentinel
retrieval: download returned 0 file(s)
no files retrievable: download returned 0, no manifest found, no archive extracted (0 path(s) exist)
```

There are no approval-card events in this retry, so this is not a recurrence of the prior card-detection loop. The remote reply says agents were not spawned; this is distinct from the locally accepted eight-agent configuration. No sandbox files were independently inspected.

Inspect the existing preserved record without starting a replacement:

```sh
musebird jobs show job_20261122_172637_45f539
musebird jobs show job_20261122_172637_45f539 --events
```

The record supplies the existing side-chat URL. Do not reproduce private conversation content in a public issue.

### Verified mechanism and lost recovery path

- `src/musebird/drivers/browser/driver.py:513-531` parses sentinels after a reply settles. If neither sentinel exists, it emits `completed`, irrespective of whether the reply is asking a question.
- `src/musebird/prompts/__init__.py:33-54` only recognizes explicit sentinel text, not an ordinary-language confirmation request.
- `src/musebird/jobs/worker.py:182-184` interprets `completed` as the signal to land output. With no files or manifest, the no-output guard then fails the job. Keep that guard; an empty result must not become success.
- `src/musebird/mcp_server.py:400-426` rejects steering messages to terminal jobs, but can resume a `NEEDS_INPUT` job on its saved thread. Because this question was misclassified as terminal, the existing approved answer cannot be relayed through the supported message tool.
- The task prompt explicitly stated that the user had approved eight agents and requested this retry. The remote model nevertheless sought confirmation and did not follow the blocked-question sentinel contract. The reason for that behavior is unknown.
- The reply refers to earlier pasted contracts despite the intended side-chat isolation. That reference alone does not establish context leakage; verify the actual context source before claiming an isolation defect.

### Requested repair-agent work

1. Investigate the startup handshake and plain-text-question fallback so a real unanswered confirmation is represented as an actionable `needs_input`, not a retrieval failure.
2. Preserve the genuine question and the saved thread, allowing an explicit user answer to resume that same job through the supported API without creating a fresh fan-out.
3. Do not automatically approve arbitrary text or guess user intent. A broad question-mark heuristic is insufficient: completed deliverables can contain questions and quoted instructions. Use a verified structured signal or a conservative ambiguous/needs-input state, and keep explicit completion + manifest retrieval semantics intact.
4. Make existing approved agent count, quota consent, scope, and sandbox limits clear at startup. Do not ask again solely because the contract came from MuseBird, but continue to ask for genuinely new decisions.
5. Distinguish no-output-after-completion from stopped-for-confirmation. Terminal diagnostic text should not say Muse reported completion when its substantive reply says it has not started.
6. Add regression coverage for this exact two-chunk confirmation reply without a sentinel, an explicit needs-input sentinel, a legitimate completed reply with an attachment/manifest but no sentinel, and completed text containing quoted questions.
7. Verify that a real answer resumes the original side chat with at most the approved eight agents, no duplicate task, no unauthorized upload/deploy, and no automatic extra quota-consuming retry.

### Scope of this report

No repair, terminal-state rewrite, manual web-message workaround, or additional job was attempted. The user approved this one retry only. The failed game deliverable was not implemented locally. The original project files remain unchanged.

## Resolution — 0.1.5 (2026-11-22)

The retry failure is fixed. A plain-text question is no longer completion.

### Classification (deliberately conservative, as requested)

Evidence of asking — any of:
- explicit confirmation phrases: `do you confirm`, `should I proceed`, `let me know`,
  `I need your`, `which would you prefer`
- a trailing question **plus** waiting language: `I haven't started`, `until you
  confirm`, `before I continue`, `stopping to check`
- a short reply that is only a question

Explicitly **not** evidence: a bare `?`; a question inside a fenced code block; a
leading block quote; a reply reporting produced artefacts (`MANIFEST.json`,
`out.tar.gz`, "attached"). Anything unproven is `ambiguous`, never a silent completion.
The existing no-output guard is unchanged: `completed` with no files still fails with
`no_output`.

### Recovery

`muse_job_message` now accepts a `FAILED` job when it has a saved thread and the
failure was `no_output` or `upstream_error`. It moves the job back to `running` on
**that same thread**, without re-sending the task and without changing the agent
count. Guard failures (`guard_violation`, `manifest_error`, …) stay terminal, because
repeating them only fails again. No automatic retry: resuming always requires an
explicit user message.

### Startup scope

Muse was stopping to confirm agent count, quota spend and sandbox limits the user had
already approved. `build_prompt` now receives an `approval_note` and the prompt states
those facts with "ALREADY APPROVED — do not ask again", while genuinely new decisions
are still asked.

### Verification

Replaying the two text events from `job_20261122_172637_45f539` through the new
classifier yields `needs_input` with the real question. 250 tests pass, including the
live reply, an explicit sentinel, a deliverable containing a question, a question in a
code fence, a quoted instruction, a plain finished reply, and both resume paths.

### Still open (unchanged by this fix)

- The remote model referenced earlier pasted contracts. Whether that is context
  leakage or its own phrasing is **not** established; no isolation defect is claimed.
- The failed job was not retried. Retrying costs quota and is the user's decision.

## Scope and exclusions

- No repair was implemented and no replacement job was started by this report.
- Claude Code's earlier Auto Mode classifier errors and MCP disconnections happened outside MuseBird's job logic; they are not established MuseBird defects.
- The separate Playwright MCP's missing Google Chrome binary prevented an independent UI inspection. It is not evidence of a Muse browser-driver failure: Muse's own Chromium health checks passed.
- Do not include account details, cookies, tokens, or the full user task payload in public issues. The job ID, versions, sanitized events, and repository-relative source locations above are sufficient starting evidence.
