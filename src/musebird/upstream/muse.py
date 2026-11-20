"""Every Muse-specific fact lives here.

Verified against https://github.com/www222fff/muse2api at commit 3a20b5e (2026-11-01)
and muse.ai itself on 2026-11-20. See `docs/HARNESS_NOTES.md` for the source list.

Muse is Meta's Muse (https://muse.ai), launched 2026-09. Threads live at
`/thread/<id>`; app routes are not `/app/*`. Unauthenticated app/API routes answer
401 JSON. The "hatch" prefix is Muse's internal client codename, which is why the
cookie names look unusual.

When Muse ships a UI change, this file changes and nothing else should need to.
The browser driver degrades to text scanning and the worker falls back to
`MANIFEST.json` discovery, so a selector drift is recoverable, not fatal.
"""

from __future__ import annotations

SITE = "https://muse.ai"
NEW_THREAD_URL = f"{SITE}/thread/new"
THREAD_URL = f"{SITE}/thread/{{thread_id}}"
LOGIN_URL = SITE
# Authenticated JSON probe: 200 with status == "assigned" means a live session.
SESSION_API = f"{SITE}/api/session"
VM_WAKE_API = f"{SITE}/api/hatch/vm/wake"

# Required as of 2026-10. hatch_gw / hatch_vml are optional: Muse no longer sets
# them for every logged-in session, so they are captured when present but never
# required. `hatch_sess` may be `__Host-` prefixed; see set_cookies() in the driver.
SESSION_COOKIES = ("hatch_sess", "hatch_native_auth_device")
OPTIONAL_SESSION_COOKIES = ("hatch_gw", "hatch_vml")

SESSION_ASSIGNED = "assigned"
VM_RUNNING = "RUNNING"
VM_DISABLED = "DISABLED"

# Completion contract printed by the remote agent (see prompts/contract.md).
DONE_SENTINEL = "INF-MUSEBIRD-DONE"
NEEDS_INPUT_SENTINEL = "INF-MUSEBIRD-NEEDS-INPUT"

# Paths the remote agent is told to use, relative to the sandbox project root.
OUT_DIR = "out"
MANIFEST_NAME = "MANIFEST.json"
BUNDLE_NAME = "out.tar.zst"

# DOM selectors lifted from muse2api's dom.py (the file its authors call the single
# point of breakage). Ranked by fragility; the driver treats every one as a hint
# and never as a hard requirement except the composer.
SELECTORS = {
    # Highest blast radius: CSS-module hash in the class name.
    "agent_bubble": 'div[class*="hatch-agent-bubble-bg"]',
    "hydration": "[data-hatch-shell-hydration-state]",
    "hydration_ready": "hydrated",
    "attachment": '[data-testid^="hatch-chat-attachment-presentation-"]',
    "composer": "textarea",
    "send_button": 'button[aria-label*="Send" i], button[data-testid*="send" i]',
    "stop_button": 'button[aria-label*="Stop" i]',
    "approval_card": '[data-testid="hatch-inline-approval-card"]',
    "approval_deny": 'button[aria-label*="Deny" i], button[aria-label*="Decline" i]',
    "settings_dock": '[data-testid="hatch-dock-more"]',
    "menu_item": "[role=menuitem]",
    "dialog": '[role="dialog"], [aria-modal="true"]',
    "file_input": 'input[type="file"]',
}

# Menu labels used to reach Settings -> Usage (quota). Localized on purpose: the
# account UI language is not under our control.
SETTINGS_LABELS = ("Settings", "設定", "设置")
LOGIN_HINTS = ("log in", "sign in", "create an account", "use another account")

# Plausible values Muse reports; used only to normalise, never to hard-code.
PLAN_NAMES = ("Free", "Power", "Maximum")

POLL_INTERVAL_S = 0.15
STABLE_POLLS_DONE = 4
STALL_GIVE_UP_S = 45
QUOTA_CACHE_S = 1800
