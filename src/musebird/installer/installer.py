"""`musebird install` / `update` / `uninstall` and the doctor checklist.

All logic is here (not in install.sh) so it is testable and identical on every OS.
Nothing in this module requires network access except the optional login step,
which the CLI runs separately.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field

from musebird import __version__
from musebird.config import Paths, paths
from musebird.installer.adapters import adapter_for, packaged_skills
from musebird.installer.detect import Detected, selected

MCP_SERVER_NAME = "musebird"


@dataclass
class InstallReport:
    dry_run: bool = False
    harnesses: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": __version__,
            "dry_run": self.dry_run,
            "harnesses": self.harnesses,
            "notes": self.notes,
            "warnings": self.warnings,
            "manual": self.manual,
        }


def _state(paths_: Paths) -> dict:
    try:
        return json.loads(paths_.install_state.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(paths_: Paths, state: dict) -> None:
    paths_.install_state.parent.mkdir(parents=True, exist_ok=True)
    paths_.install_state.write_text(json.dumps(state, indent=2))


def install(
    *,
    harnesses: str | None = None,
    dry_run: bool = False,
    paths_: Paths | None = None,
) -> InstallReport:
    p = paths_ or paths()
    report = InstallReport(dry_run=dry_run)
    found = selected(harnesses)
    if not found:
        report.warnings.append(
            "No supported harness found. Install one of: Claude Code, Codex, OpenCode."
        )
    state = _state(p)
    installed_names = set(state.get("harnesses", []))
    installed_skills = set(state.get("skills", []))

    for h in found:
        entry: dict = {"harness": h.name, "installed": h.installed, "changes": [], "ok": True}
        if not h.installed and not harnesses:
            continue
        adapter = adapter_for(h, p)
        try:
            changes = adapter.install_mcp(dry_run=dry_run)
            skills = adapter.install_skills(dry_run=dry_run)
            entry["changes"] = [str(c) for c in changes + skills]
            if not dry_run:
                for c in skills:
                    if c.action in ("skill install", "skill update"):
                        installed_skills.add(str(c.target))
                installed_names.add(h.name)
            entry["verified"] = adapter.verify()
        except Exception as exc:  # one broken harness must not kill the install
            entry["ok"] = False
            entry["error"] = str(exc)
            report.warnings.append(f"{h.name}: {exc}")
            report.manual.append(
                f"{h.name}: register manually with `{_manual_command(h)}`, then re-run `musebird doctor`."
            )
        report.harnesses.append(entry)

    _ensure_browser(dry_run=dry_run, report=report)

    if not dry_run:
        state["harnesses"] = sorted(installed_names)
        state["skills"] = sorted(installed_skills)
        state["version"] = __version__
        _save_state(p, state)
        report.notes.append(f"State saved to {p.install_state}")
    report.notes.append(
        "Restart the harness if the MCP server does not appear; skills are picked up live in "
        "Claude Code and OpenCode."
    )
    return report


def uninstall(*, harnesses: str | None = None, dry_run: bool = False,
              paths_: Paths | None = None) -> InstallReport:
    p = paths_ or paths()
    report = InstallReport(dry_run=dry_run)
    for h in selected(harnesses) if harnesses else _all():
        if not h.installed:
            continue
        adapter = adapter_for(h, p)
        entry: dict = {"harness": h.name, "changes": [], "ok": True}
        try:
            entry["changes"] = [str(c) for c in adapter.uninstall_mcp(dry_run) + adapter.uninstall_skills(dry_run)]
        except Exception as exc:
            entry["ok"] = False
            entry["error"] = str(exc)
            report.warnings.append(f"{h.name}: {exc}")
        report.harnesses.append(entry)
    if not dry_run:
        state = _state(p)
        state["harnesses"] = []
        _save_state(p, state)
    report.notes.append("Backups were kept in " + str(p.backups))
    return report


def _all() -> list[Detected]:
    from musebird.installer.detect import all_harnesses

    return all_harnesses()


def _manual_command(h: Detected) -> str:
    if h.name == "claude":
        return "claude mcp add --transport stdio --scope user musebird -- musebird mcp"
    if h.name == "codex":
        return "codex mcp add musebird -- musebird mcp"
    return "opencode mcp add musebird --global -- musebird mcp"


# ---------------------------------------------------------------- doctor
@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""

    def render(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        line = f"[{mark}] {self.name}"
        if self.detail:
            line += f" — {self.detail}"
        if not self.ok and self.fix:
            line += f"\n       fix: {self.fix}"
        return line

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail, "fix": self.fix}


def doctor(*, selftest: bool = False, paths_: Paths | None = None, live: bool = False) -> list[Check]:
    p = paths_ or paths()
    checks: list[Check] = []

    exe = shutil.which("musebird")
    if not exe:
        # Running from a venv or a checkout: the module is importable but not installed
        # as a tool. That works for `uv run` and tests, so report it as a soft pass.
        exe = sys.executable if _module_importable() else None
    checks.append(
        Check(
            "musebird on PATH",
            bool(exe),
            exe or "not found",
            "Re-run the installer: `uv tool install infinity-musebird`.",
        )
    )

    try:
        packaged_skills()
        checks.append(Check("bundled skills present", True))
    except FileNotFoundError as exc:
        checks.append(Check("bundled skills present", False, str(exc), "Reinstall the package."))

    problems = []
    for h in _all():
        if not h.installed:
            continue
        try:
            adapter = adapter_for(h, p)
            if not adapter.mcp_installed():
                problems.append(h.name)
        except Exception:
            problems.append(h.name)
    checks.append(
        Check(
            "MCP registered in detected harnesses",
            not problems,
            "missing in: " + ", ".join(problems) if problems else "all detected",
            "Run `musebird install --yes`.",
        )
    )

    state = _state(p)
    install_ok = bool(state.get("harnesses"))
    checks.append(
        Check("install state recorded", install_ok,
              ", ".join(state.get("harnesses", [])) or "none",
              "Run `musebird install --yes`.")
    )

    from musebird.session.vault import Vault

    vault = Vault(p)
    stored = vault.load()
    checks.append(
        Check(
            "Muse session stored",
            bool(stored),
            f"backend: {vault.backend}" if stored else "no session",
            "Run `musebird login`.",
        )
    )

    browser = _browser_check()
    checks.append(
        Check("browser available", browser[0], browser[1],
              "Install Chrome/Edge, or run: `uv tool install 'infinity-musebird[browser]' && playwright install chromium`.")
    )

    checks.append(
        Check("quota cache", True, "run `musebird status` to refresh" if not p.quota_cache.exists() else "cached")
    )

    stale = _stale_jobs(p)
    checks.append(
        Check("no stuck jobs", not stale, ", ".join(stale) if stale else "none",
              "Run `musebird jobs list`, then `musebird jobs cancel <id>`.")
    )

    if live:
        checks.extend(_live_checks())
    if selftest:
        checks.extend(_selftest_checks())
    return checks


def _selftest_checks() -> list[Check]:
    """Probe Muse for real: session, VM wake, quota, and every selector.

    Returns a single explanatory failure (not a misleading PASS) when there is no
    session, because selectors cannot be checked without one.
    """
    import asyncio

    from musebird.drivers.browser.driver import BrowserDriver
    from musebird.session.vault import Vault

    if not Vault(paths()).load():
        return [
            Check(
                "live selftest",
                False,
                "no stored session",
                "Run `musebird login`, then `musebird doctor --selftest`.",
            )
        ]

    async def probe() -> list[Check]:
        driver = BrowserDriver("doctor-selftest")
        out: list[Check] = []
        try:
            state = await driver.ensure_session()
            out.append(
                Check("live session", state.valid, state.detail,
                      "Run `musebird login`.")
            )
            if not state.valid:
                return out
            try:
                vm = await driver.wake_vm()
                out.append(
                    Check("sandbox VM awake", vm == "running", vm,
                          "Open an app.muse thread once, then retry.")
                )
            except Exception as exc:
                out.append(Check("sandbox VM awake", False, type(exc).__name__,
                                 "Open an app.muse thread once, then retry."))
            try:
                badge = await driver.read_quota()
                detail = f"{badge.used}% of {badge.limit}" if badge.used is not None else "unparsed"
                out.append(
                    Check("quota readable", badge.used is not None, detail,
                          "Muse's Usage dialog changed; update parse_usage_text.")
                )
            except Exception as exc:
                out.append(Check("quota readable", False, type(exc).__name__,
                                 "Update the usage selectors in upstream/muse.py."))
            for item in await driver.selftest():
                out.append(
                    Check(f"selector {item['check']}", bool(item["ok"]), str(item["detail"]),
                          "Update the selector in src/musebird/upstream/muse.py.")
                )
        finally:
            await driver.close()
        return out

    try:
        return asyncio.run(probe())
    except Exception as exc:
        # Show the message, not just the class: "Error" told the user nothing.
        detail = getattr(exc, "message", None) or str(exc) or type(exc).__name__
        return [
            Check(
                "live selftest",
                False,
                f"{type(exc).__name__}: {detail}"[:300],
                "Run `MUSEBIRD_LIVE=1 .venv/bin/python -m pytest tests/test_live.py -s -v`.",
            )
        ]


def _module_importable() -> bool:
    import importlib.util

    return importlib.util.find_spec("musebird") is not None


def _ensure_browser(*, dry_run: bool, report: InstallReport) -> bool:
    """One command must produce a working browser: download Chromium if needed.

    Preference is a system Chrome/Edge (no download). Only when none exists do we
    fetch Playwright's Chromium, which the spec allows as the fallback.
    """
    from musebird.browsers import (
        describe,
        find_system_browser,
        playwright_bundle_present,
    )

    channel, executable = find_system_browser()
    if channel or executable:
        report.notes.append(f"browser: {describe()} (no download needed)")
        return True
    if playwright_bundle_present():
        report.notes.append("browser: playwright bundled chromium")
        return True
    if dry_run:
        report.notes.append("browser: would download Playwright Chromium (none found)")
        return False
    report.notes.append("browser: downloading Playwright Chromium (no system Chrome/Edge found)")
    import shutil
    import subprocess

    playwright = shutil.which("playwright")
    cmd = [playwright, "install", "chromium"] if playwright else None
    if cmd is None:
        return False
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        report.warnings.append(
            f"could not download Chromium ({exc}); install Chrome/Edge and re-run `musebird doctor`"
        )
        return False
    if proc.returncode != 0:
        report.warnings.append(
            "Playwright Chromium download failed; install Chrome/Edge, or run "
            "`playwright install chromium` manually."
        )
        return False
    report.notes.append("browser: chromium downloaded")
    return True


def _browser_check() -> tuple[bool, str]:
    from musebird.browsers import describe, find_system_browser, playwright_bundle_present

    channel, executable = find_system_browser()
    if channel or executable:
        return True, describe()
    if playwright_bundle_present():
        return True, "playwright bundled chromium"
    return False, "no Chrome/Edge/Chromium found"


def _stale_jobs(paths_: Paths) -> list[str]:
    import time

    from musebird.jobs import state as st
    from musebird.jobs.registry import JobRegistry

    try:
        registry = JobRegistry(paths_)
    except Exception:
        return []
    now = time.time()
    return [
        j.id for j in registry.find_running() if now - j.updated_at > 3600 and j.state != st.NEEDS_INPUT
    ]


def _live_checks() -> list[Check]:
    return [
        Check("live session", False, "not checked", "Run `musebird status --selftest`."),
    ]


def render(report: InstallReport) -> str:
    lines = []
    title = "Planned changes" if report.dry_run else "Done"
    lines.append(f"== musebird {__version__} — {title} ==")
    for entry in report.harnesses:
        status = "ok" if entry.get("ok", True) else "FAILED"
        lines.append(f"\n{entry['harness']}: {status}")
        for change in entry.get("changes", []):
            lines.append(f"  {change}")
        if entry.get("error"):
            lines.append(f"  error: {entry['error']}")
    for note in report.notes:
        lines.append(f"\n{note}")
    for warning in report.warnings:
        lines.append(f"warning: {warning}")
    for manual in report.manual:
        lines.append(f"manual: {manual}")
    return "\n".join(lines)
