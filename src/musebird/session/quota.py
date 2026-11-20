"""Quota text parsing, ported in spirit from muse2api's parse_usage_text.

The dialog text is localized, may use fullwidth percent signs, and reports both a
weekly percentage and an additional-token balance. Only the weekly percentage is
required; everything else is best-effort.
"""

from __future__ import annotations

import re

_PCT = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*[%％]")
_RESET = re.compile(
    r"(?i)(?:resets?(?:\s+on)?|reset\s+date|renews?(?:\s+on)?|重置|更新)\s*[:：]?\s*([^\n]{3,40})"
)
_PLAN = re.compile(r"(?i)\b(free|power|maximum|pro)\b\s*(?:plan)?")
_EXTRA = re.compile(r"(?i)([\d.,]+\s*[BMK]?)\s*(?:tokens?)?\s*(?:left|remaining|剩余)", re.UNICODE)
_FULLWIDTH = str.maketrans("０１２３４５６７８９．％", "0123456789.%")


def parse_usage_text(text: str) -> dict:
    clean = text.translate(_FULLWIDTH)
    result: dict = {"found": False, "weekly_used_pct": None, "plan": None,
                    "resets_at": None, "extra_left": None}
    plan = _PLAN.search(clean)
    if plan:
        result["plan"] = plan.group(1).title()
    reset = _RESET.search(clean)
    if reset:
        result["resets_at"] = reset.group(1).strip()
    extra = _EXTRA.search(clean)
    if extra:
        result["extra_left"] = extra.group(1).strip()
    percentages = [float(p.replace(",", ".")) for p in _PCT.findall(clean)]
    if percentages:
        # The weekly usage line appears before the additional-token line.
        result["weekly_used_pct"] = max(0.0, min(100.0, percentages[0]))
        result["found"] = True
    return result
