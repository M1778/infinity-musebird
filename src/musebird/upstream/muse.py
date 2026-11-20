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
# New work always starts in a *side chat*, never the account's main chat. The
# product's side-chat control is a UI action, so the driver opens a fresh thread
# and then uses this control; the label list is localized like the settings menu.
NEW_THREAD_URL = f"{SITE}/thread/new"
THREAD_URL = f"{SITE}/thread/{{thread_id}}"
LOGIN_URL = SITE

# Side chat. Verified against a live account on 2026-11-21: a thread opened at
# /thread/new *is* a side chat (the header offers "Back to main chat"), so no extra
# control has to be clicked. The invariant is checked, not assumed: a thread counts
# as isolated only while that control is present.
SIDE_CHAT_MARKERS = (
    'button[aria-label*="Back to main chat" i]',
    '[data-testid="hatch-side-chats-panel-shell"]',
    '[data-testid*="side-chat"]',
)
SIDE_CHAT_LABELS = ("Back to main chat", "メインチャットに戻る", "返回主聊天")
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
    # Verified 2026-11-21: Muse's composer has no send button. Enter submits, the
    # composer clears, and the URL becomes /thread/<uuid>. Clicking a button with a
    # "Send" label finds nothing, which is why the driver presses Enter first.
    "send_button": 'button[aria-label*="Send" i], button[data-testid*="send" i]',
    "stop_button": 'button[aria-label*="Stop" i]',
    "approval_card": '[data-testid="hatch-inline-approval-card"]',
    "approval_deny": 'button[aria-label*="Deny" i], button[aria-label*="Decline" i]',
    "settings_dock": '[data-testid="hatch-dock-more"]',
    "menu_item": "[role=menuitem]",
    "dialog": '[role="dialog"], [aria-modal="true"]',
    "file_input": 'input[type="file"]',
    # Any of these present means the thread is isolated from the main conversation.
    "side_chat_marker": (
        'button[aria-label*="Back to main chat" i], '
        '[data-testid="hatch-side-chats-panel-shell"], '
        '[data-testid*="side-chat"]'
    ),
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
