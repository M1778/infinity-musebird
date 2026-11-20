"""Session keepalive and quota caching.

Both are cheap disk-backed caches so `muse_status` can answer instantly without
touching Muse. Keepalive is deliberately passive: it never logs in automatically
and never retries in a loop (the spec forbids login loops).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from musebird.config import Paths, paths

from .vault import Vault


@dataclass
class Cached:
    at: float
    data: dict

    @property
    def age(self) -> float:
        return time.time() - self.at


class QuotaCache:
    def __init__(self, paths_: Paths | None = None, ttl: float = 1800) -> None:
        self.paths = paths_ or paths()
        self.ttl = ttl

    def get(self) -> dict | None:
        path = self.paths.quota_cache
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return data

    def fresh(self) -> dict | None:
        """The cache, only if it is recent *and* actually carries a percentage."""
        data = self.get()
        if not data:
            return None
        if data.get("used") is None:
            return None
        if time.time() - float(data.get("_cached_at", 0)) > self.ttl:
            return None
        return data

    def put(self, quota: dict) -> None:
        payload = {**quota, "_cached_at": time.time()}
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.paths.quota_cache.write_text(json.dumps(payload, indent=2))


def session_summary(paths_: Paths | None = None) -> dict:
    p = paths_ or paths()
    vault = Vault(p)
    data = vault.load()
    if not data:
        return {"stored": False, "backend": vault.backend, "valid": False}
    meta = data.get("meta") or {}
    expires = meta.get("expires_at")
    valid = True
    if expires:
        try:
            valid = float(expires) > time.time()
        except (TypeError, ValueError):
            valid = True
    return {
        "stored": True,
        "backend": vault.backend,
        "valid": valid,
        "account": meta.get("account"),
        "expires_at": expires,
        "saved_at": meta.get("saved_at"),
    }


def keepalive_touch(paths_: Paths | None = None) -> dict:
    """Update the saved session's last-used time; does not contact Muse."""
    p = paths_ or paths()
    vault = Vault(p)
    data = vault.load()
    if not data:
        return {"ok": False, "reason": "no session"}
    meta = data.setdefault("meta", {})
    meta["last_used_at"] = time.time()
    vault.save(data.get("cookies") or {}, meta)
    return {"ok": True, "backend": vault.backend}
