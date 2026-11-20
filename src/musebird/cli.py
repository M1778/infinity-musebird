"""Command line entry point.

`musebird` is the only executable; `musebird mcp` is what harnesses launch. Every
command prints plain text for humans except `--json` outputs used by tests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from musebird import __version__
from musebird.config import paths
from musebird.errors import MuseBirdError


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="musebird", description="Use Muse as a subagent via MCP.")
    p.add_argument("--version", action="version", version=f"musebird {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    inst = sub.add_parser("install", help="install/refresh MCP server and skills in detected harnesses")
    inst.add_argument("--yes", action="store_true", help="no prompts")
    inst.add_argument("--dry-run", action="store_true", help="print planned changes, write nothing")
    inst.add_argument("--harness", default=None, help="comma list: claude,codex,opencode")
    inst.add_argument("--skip-login", action="store_true", help="do not start the login flow")
    inst.add_argument("--json", action="store_true")

    un = sub.add_parser("uninstall", help="remove MCP entries and skills we installed")
    un.add_argument("--dry-run", action="store_true")
    un.add_argument("--harness", default=None)
    un.add_argument("--purge", action="store_true", help="also delete the stored session and jobs")

    sub.add_parser("update", help="re-run install logic and refresh skills").add_argument(
        "--dry-run", action="store_true"
    )

    lg = sub.add_parser("login", help="sign in to Muse once in a browser")
    lg.add_argument("--timeout", type=int, default=600)

    lo = sub.add_parser("logout", help="delete the stored session and browser profile")
    lo.add_argument("--keep-profile", action="store_true", help="keep the browser profile")

    st = sub.add_parser("status", help="show session, quota and recent jobs")
    st.add_argument("--json", action="store_true")

    doc = sub.add_parser("doctor", help="plain pass/fail checklist for each component")
    doc.add_argument("--selftest", action="store_true", help="probe live selectors (needs login)")
    doc.add_argument("--json", action="store_true")
    doc.add_argument(
        "--fix", action="store_true",
        help="clear a browser profile lock left by a crashed process",
    )

    jobs = sub.add_parser("jobs", help="inspect and cancel jobs")
    jobs_sub = jobs.add_subparsers(dest="jobs_command", required=True)
    jobs_sub.add_parser("list")
    show = jobs_sub.add_parser("show")
    show.add_argument("job_id")
    show.add_argument("--events", action="store_true")
    cancel = jobs_sub.add_parser("cancel")
    cancel.add_argument("job_id")

    run = sub.add_parser("job-run", help="internal: worker for one job")
    run.add_argument("job_id")

    sub.add_parser("version", help="print the installed version")
    sub.add_parser("mcp", help="run the MCP stdio server (harnesses launch this)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except MuseBirdError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"fix: {exc.hint}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "install":
        return _install(args)
    if args.command == "update":
        # Same logic as install, but never prompts and never starts a login flow.
        return _install(
            argparse.Namespace(dry_run=args.dry_run, skip_login=True, json=False, harness=None)
            if hasattr(args, "dry_run")
            else argparse.Namespace(dry_run=False, skip_login=True, json=False, harness=None)
        )
    if args.command == "uninstall":
        return _uninstall(args)
    if args.command == "login":
        return _login(args)
    if args.command == "logout":
        from musebird.session.login import logout

        removed = asyncio.run(logout(keep_profile=args.keep_profile))
        print("Session and browser profile deleted." if removed else "No stored session.")
        return 0
    if args.command == "status":
        return _status(args)
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "jobs":
        return _jobs(args)
    if args.command == "job-run":
        from musebird.jobs.worker import main as worker_main

        return worker_main(args.job_id)
    if args.command == "version":
        print(f"musebird {__version__}")
        return 0
    if args.command == "mcp":
        from musebird.mcp_server import main as mcp_main

        mcp_main()
        return 0
    return 1


def _install(args: argparse.Namespace) -> int:
    from musebird.installer.installer import install, render

    report = install(
        harnesses=getattr(args, "harness", None), dry_run=getattr(args, "dry_run", False)
    )
    if getattr(args, "json", False):
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(render(report))
    if args.dry_run:
        print("\nDry run: nothing was written.")
        return 0
    if not getattr(args, "skip_login", False):
        from musebird.session.vault import Vault

        if not Vault().load():
            print("\nNext: sign in to Muse (one time).")
            try:
                asyncio.run(_login_flow(args.timeout if hasattr(args, "timeout") else 600))
            except MuseBirdError as exc:
                print(f"login skipped: {exc.message}")
    print("\nCheck everything with: musebird doctor")
    print("Then try in your harness: /inf-muse say hello from the sandbox")
    return 0


async def _login_flow(timeout: int) -> None:
    from musebird.session.login import login

    result = await login(timeout=timeout)
    print(f"Logged in as {result.account or 'Muse user'}: {result.detail}")


def _login(args: argparse.Namespace) -> int:
    if sys.stdin.isatty() is False and sys.platform != "win32":
        # Still attempt: in CI this fails with a clear browser error rather than a prompt.
        pass
    asyncio.run(_login_flow(args.timeout))
    return 0


def _uninstall(args: argparse.Namespace) -> int:
    from musebird.installer.installer import render, uninstall

    report = uninstall(harnesses=args.harness, dry_run=args.dry_run)
    print(render(report))
    if args.purge and not args.dry_run:
        import shutil

        root = paths().root
        shutil.rmtree(root, ignore_errors=True)
        print(f"Deleted {root} (session, jobs, logs, backups).")
    return 0


def _status(args: argparse.Namespace) -> int:
    import asyncio as _asyncio

    from musebird.jobs.registry import JobRegistry
    from musebird.session.keepalive import QuotaCache, session_summary

    summary = session_summary()
    quota = QuotaCache().get()
    jobs = [
        {"id": j.id, "state": j.state, "mode": j.mode, "prompt_sha": j.prompt[:0] or ""}
        for j in JobRegistry().list(limit=10)
    ]
    data = {"session": summary, "quota": quota, "jobs": jobs, "driver_default": "browser"}
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"session: {'stored' if summary['stored'] else 'not stored'} "
          f"({'valid' if summary.get('valid') else 'needs login'}, {summary['backend']})")
    if summary.get("account"):
        print(f"account: {summary['account']}")
    if quota:
        used = quota.get("used")
        limit = quota.get("limit")
        resets = quota.get("resets_at") or "unknown"
        pct = f"{used}/{limit}" if used is not None and limit else "unknown"
        print(f"quota: {pct} used, resets {resets}")
    else:
        print("quota: unknown (run a job or `musebird status --json` after login)")
    if jobs:
        print("\nrecent jobs:")
        for j in jobs:
            print(f"  {j['id']:<28} {j['state']:<12} {j['mode']}")
    else:
        print("\nno jobs yet")
    del _asyncio
    return 0


def _doctor(args: argparse.Namespace) -> int:
    from musebird.installer.installer import doctor

    if getattr(args, "fix", False):
        from musebird.browser_lock import force_unlock
        from musebird.config import paths as _paths

        try:
            cleared = force_unlock(_paths().browser_profile)
            print("cleared: " + (", ".join(cleared) if cleared else "nothing to clear"))
        except MuseBirdError as exc:
            print(f"error: {exc.message}", file=sys.stderr)
            if exc.hint:
                print(f"fix: {exc.hint}", file=sys.stderr)
            return 2

    checks = doctor(selftest=args.selftest)
    if args.json:
        print(json.dumps([c.to_dict() for c in checks], indent=2))
        return 0 if all(c.ok for c in checks) else 1
    for check in checks:
        print(check.render())
    ok = all(c.ok for c in checks)
    print("\nAll checks passed." if ok else "\nSome checks failed; each has a one-line fix above.")
    return 0 if ok else 1


def _jobs(args: argparse.Namespace) -> int:
    from musebird.jobs.registry import JobRegistry

    registry = JobRegistry()
    if args.jobs_command == "list":
        rows = registry.list()
        if not rows:
            print("no jobs")
            return 0
        for j in rows:
            print(f"{j.id:<30} {j.state:<12} {j.mode:<7} {j.prompt[:50]}")
        return 0
    if args.jobs_command == "show":
        job = registry.get(args.job_id)
        print(json.dumps(job.to_dict(), indent=2))
        if args.events:
            events, _ = registry.events_since(args.job_id, 0)
            for e in events:
                print(f"[{e.get('kind')}] {e.get('text')}")
        return 0
    if args.jobs_command == "cancel":
        job = registry.request_cancel(args.job_id)
        print(f"cancel requested for {job.id} ({job.state})")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
