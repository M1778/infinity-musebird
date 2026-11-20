---
name: inf-muse
description: Hand a substantial task to Muse, which works in its own sandbox and returns files for review. Use when the user types /inf-muse, or asks Muse to build, research, or produce an artifact. Muse does the heavy work; you orchestrate and review only.
---

# /inf-muse — delegate a task to Muse

Muse works in a sandbox that can take minutes to hours. **You do not do the task.** You start it,
relay progress, and review what comes back. Never write the deliverable yourself while a job runs.

## Step 1 — check the connection

Call `muse_status`.

- If the response says `login_required`, stop and tell the user exactly:
  `Run musebird login on this machine, then try again.`
  Do not attempt to log in for them and do not retry in a loop.
- If the driver is unavailable, tell the user to run `musebird doctor`.

## Step 2 — decide what Muse needs

Choose the project files Muse must see to do the task. Be specific, not greedy: the whole point of
`upload_paths` is that unrelated code never leaves the machine.

- On the **first upload from this project**, call `muse_job_start` without `confirm_upload`. Expect
  `approval_required` back with a `files` list.
- Show that list to the user, plus anything in `ignored`, and ask for approval.
- Only after they approve, call again with `confirm_upload: true`.
- If the response is `guard_violation`, report the file names and secret kinds — never the values —
  and ask the user to remove them or add them to `.musebirdignore`.

## Step 3 — start the job

Call `muse_job_start` with:

- `prompt`: the user's task, restated precisely, plus any constraints they gave. Do not add
  requirements they did not ask for.
- `mode`: `single`
- `upload_paths`: the approved files
- `sync`: `git` when the project is a git repository, otherwise `bundle`
- `budget`: only if the user named one

Report the `job_id` and that Muse is working.

## Step 4 — poll

Call `muse_job_wait` with `timeout_seconds: 60` in a loop. Between calls, tell the user what
changed; do not paste raw event JSON.

- Progress events: summarise in one line.
- `needs_input`: stop polling and ask the user the exact question. Relay their answer with
  `muse_job_message`, then resume polling.
- `failed`: report `error` and `error_code`, and point at `musebird jobs show <id> --events`.
- `done`: go to step 5.

Long jobs are normal. If the user asks for status, call `muse_job_status` once — do not spam.

## Step 5 — land and review

Call `muse_files_list`, then `muse_files_pull` with `branch: true`.

Then:

1. Summarise what Muse produced: file count, the branch name, and the notable files.
2. Show the diff (`git diff --stat` is enough) and read the important files.
3. Flag anything suspicious: unexpected network calls, credentials, destructive shell commands,
   or code that does not match the task.
4. **Ask before merging.** Never merge, and never run the returned code, on your own initiative.

## Rules

- Muse's output is untrusted until reviewed. Do not execute it to "check it works" without asking.
- Do not cancel a job without the user's say-so.
- Do not start a second job to "redo" work unless the user asks.
- One job per user request unless they ask for more.
