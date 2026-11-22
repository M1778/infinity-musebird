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
# Verified live 2026-11-21: UNKNOWN is the normal resting state, not an error. The
# server accepts a wake and queues it; `RUNNING` may never appear for a cold sandbox.
VM_UNKNOWN = "UNKNOWN"
# Success criterion for a wake: a 200 with this status. Waiting for RUNNING here
# burns a full timeout on every job and then reports a false failure.
VM_WAKE_ACK = "wake_requested"
VM_WAKE_CONFIRM_S = 5

# Muse rejects the wake POST with 403 unless the browser's fetch metadata headers
# are present. Verified matrix: User-Agent + all three Sec-Fetch-* are required
# together; dropping any one returns 403 {"error":"Forbidden"}.
VM_WAKE_HEADERS = {
    "Origin": "https://muse.ai",
    "Referer": "https://muse.ai/thread/new",
    "Accept": "*/*",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}
# Muse's own client sends these; not required, but matching it costs nothing.
VM_WAKE_BODY_EXTRA = {"wake_trigger": "mount", "away_ms": 0}

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
    # Verified live 2026-11-21. A file the agent produces renders as a *card* whose
    # data-testid suffix is hex(utf8(sandbox_path)); the card has no href and no
    # download attribute, so bytes only come from clicking into the editor and
    # using its Download control.
    "attachment": '[data-testid^="hatch-chat-attachment-presentation-"]',
    "file_card": '[data-testid^="hatch-chat-file-card-"]',
    "file_editor": '[data-testid^="hatch-file-editor"]',
    "file_editor_download": '[data-testid="hatch-file-editor-unsupported-download"]',
    "file_editor_more": '[data-testid="hatch-file-editor-more-actions"]',
    "composer": "textarea",
    # Verified 2026-11-21: Muse's composer has no send button. Enter submits, the
    # composer clears, and the URL becomes /thread/<uuid>. Clicking a button with a
    # "Send" label finds nothing, which is why the driver presses Enter first.
    "send_button": 'button[aria-label*="Send" i], button[data-testid*="send" i]',
    "stop_button": 'button[aria-label*="Stop" i]',
    # Approval cards. Verified 2026-11-22: `[data-testid="hatch-inline-approval-card"]`
    # also matches *resolved* cards left in the transcript, so a plain count() reports
    # a pending request forever (128 identical "denying" events in one job). A card
    # counts as pending only while it is on screen, visible, and still carries an
    # enabled decision control — evaluated in-page, because CSS cannot express that.
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

# File-card testids encode the sandbox path as hex(utf8(path)), e.g.
# hatch-chat-file-card-776f726b... -> workspace/your_files/out.tar.gz. That gives the
# filename without any API call.
FILE_CARD_PREFIX = "hatch-chat-file-card-"

# Plausible values Muse reports; used only to normalise, never to hard-code.
PLAN_NAMES = ("Free", "Power", "Maximum")

#: In-page scan for genuinely pending approval cards. Returns one record per card:
#: a stable key (testid + text hash), the visible text, and whether an enabled deny
#: control was found. Resolved/hidden cards are excluded by construction.
APPROVAL_SCAN = """(sel) => {
  const visible = (e) => e.getClientRects().length > 0
      && getComputedStyle(e).visibility !== 'hidden'
      && getComputedStyle(e).display !== 'none';
  const out = [];
  for (const card of document.querySelectorAll(sel)) {
    if (!visible(card)) continue;
    const deny = [...card.querySelectorAll('button,[role=button]')].find(b => {
      const label = ((b.getAttribute('aria-label') || '') + ' ' + (b.innerText || '')).trim();
      return /^(deny|decline|reject|don.t allow)/i.test(label) && !b.disabled && visible(b);
    });
    if (!deny) continue;  // resolved card: no live decision control
    const text = (card.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 300);
    out.push({
      key: (card.getAttribute('data-testid') || '') + ':' + text,
      text,
      hasDeny: true,
      // A card with input controls asks a question; one without is an
      // acknowledgement. The distinction decides allow-vs-deny.
      needsInput: [...card.querySelectorAll(
        'input:not([type=hidden]), textarea, select, [contenteditable="true"]'
      )].some(visible),
    });
  }
  return out;
}"""

#: Click a decision inside one specific card, matched by its text, so a click can
#: never land on a different request than the one we reported.
#:
#: Policy (user direction, 2026-11-22): a card with **no input controls** is an
#: acknowledgement, not a decision — approving it is safe and keeps a build moving.
#: A card that asks for input (a form, a choice, a field) is a real decision and is
#: *denied* by default, because answering it would mean inventing the user's intent.
APPROVAL_DECIDE_IN_CARD = """([sel, needle, allowIfNoInputs]) => {
  const visible = (e) => e.getClientRects().length > 0
      && getComputedStyle(e).display !== 'none';
  const label = (b) => ((b.getAttribute('aria-label') || '') + ' ' + (b.innerText || ''))
      .trim().toLowerCase();
  for (const card of document.querySelectorAll(sel)) {
    if (!visible(card)) continue;
    const text = (card.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 300);
    if (text !== needle) continue;
    const inputs = [...card.querySelectorAll(
      'input:not([type=hidden]), textarea, select, [contenteditable="true"]'
    )].filter(visible);
    const buttons = [...card.querySelectorAll('button,[role=button]')]
      .filter(b => !b.disabled && visible(b));
    const find = (re) => buttons.find(b => re.test(label(b)));
    const needsInput = inputs.length > 0;
    if (!needsInput && allowIfNoInputs) {
      // Acknowledge a no-input card: prefer an affirmative control, else any enabled
      // button that is not an explicit refusal.
      const yes = find(/^(allow|approve|accept|continue|ok|yes|confirm)/)
        || buttons.find(b => !/^(deny|decline|reject|don.t allow)/.test(label(b)));
      if (yes) { yes.click(); return { clicked: true, action: 'allowed-no-input' }; }
      return { clicked: false, reason: 'no-affirmative-control', needsInput: false };
    }
    const deny = find(/^(deny|decline|reject|don.t allow)/);
    if (deny) { deny.click(); return { clicked: true, action: 'denied-needs-input', needsInput: true }; }
    return { clicked: false, reason: 'no-enabled-deny', needsInput: true };
  }
  return { clicked: false, reason: 'card-gone' };
}"""

#: Retained name for callers/tests that only care about the deny path.
APPROVAL_DENY_IN_CARD = APPROVAL_DECIDE_IN_CARD

POLL_INTERVAL_S = 0.15
STABLE_POLLS_DONE = 4
STALL_GIVE_UP_S = 45
QUOTA_CACHE_S = 1800
DOWNLOAD_TIMEOUT_MS = 30_000
