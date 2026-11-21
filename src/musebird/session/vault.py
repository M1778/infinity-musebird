"""Secret storage: OS keychain when available, encrypted file otherwise.

File format: magic header + scrypt-derived key + AES-GCM. If `cryptography` is not
installed we fall back to a 0600 JSON file and say so in `doctor`, rather than
pretending the session is encrypted. Cookie values never appear in errors.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
from typing import Protocol

from musebird.config import Paths, paths, secure_write

SERVICE = "infinity-musebird"
ACCOUNT = "muse-session"
MAGIC = b"MUSEBIRD1"


#: Set this to skip keyring entirely. Tests set it so no run can ever touch the host
#: keychain: importing keyring on Windows probes the Credential Manager, which has
#: deadlocked CI, and a test has no business reading the developer's real secrets.
ENV_DISABLE_KEYRING = "MUSEBIRD_DISABLE_KEYRING"


def _usable_keyring() -> KeyringLike | None:
    """Return the keyring module only if it can actually store a secret.

    Importing keyring is not enough: headless Linux and containers get the "fail"
    backend, which passes isinstance checks and raises on use.
    """
    import os

    if os.environ.get(ENV_DISABLE_KEYRING) == "1":
        return None
    try:
        import keyring  # type: ignore[import-not-found]
        from keyring.backends.fail import Keyring as FailKeyring

        if isinstance(keyring.get_keyring(), FailKeyring):
            return None
        return keyring  # type: ignore[return-value]
    except Exception:
        return None


#: Pass as `keyring_module` to force the encrypted-file backend (tests, or a user
#: who does not want the OS keychain involved). `None` means "use the default".
FILE_ONLY = object()


class KeyringLike(Protocol):
    """The three calls the vault makes on a keychain backend."""

    def set_password(self, service: str, account: str, value: str) -> None: ...

    def get_password(self, service: str, account: str) -> str | None: ...

    def delete_password(self, service: str, account: str) -> None: ...


class Vault:
    def __init__(
        self,
        paths_: Paths | None = None,
        *,
        keyring_module: KeyringLike | object | None = FILE_ONLY,
    ) -> None:
        self.paths = paths_ or paths()
        if keyring_module is FILE_ONLY:
            # Explicitly file-only: never touch a system keychain.
            self._keyring: KeyringLike | None = None
        elif keyring_module is None:
            self._keyring = _usable_keyring()
        else:
            self._keyring = keyring_module  # type: ignore[assignment]

    @property
    def backend(self) -> str:
        return "keyring" if self._keyring else "file"

    def save(self, cookies: dict[str, str], meta: dict | None = None) -> None:
        payload = json.dumps({"cookies": cookies, "meta": meta or {}}).encode()
        if self._keyring:
            try:
                self._keyring.set_password(SERVICE, ACCOUNT, base64.b64encode(payload).decode())
                return
            except Exception:  # keychain locked or unavailable: fall through
                pass
        secure_write(self.paths.session_file, _seal(payload))

    def load(self) -> dict | None:
        if self._keyring:
            try:
                raw = self._keyring.get_password(SERVICE, ACCOUNT)
            except Exception:
                raw = None
            if raw:
                try:
                    return json.loads(base64.b64decode(raw))
                except (ValueError, json.JSONDecodeError):
                    return None
        path = self.paths.session_file
        if not path.exists():
            return None
        try:
            return json.loads(_unseal(path.read_bytes()))
        except Exception:
            return None

    def delete(self) -> bool:
        removed = False
        if self._keyring:
            with contextlib.suppress(Exception):
                self._keyring.delete_password(SERVICE, ACCOUNT)
                removed = True
        if self.paths.session_file.exists():
            self.paths.session_file.unlink()
            removed = True
        return removed

    def cookies(self) -> dict[str, str]:
        data = self.load() or {}
        return data.get("cookies") or {}


def _seal(payload: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        # No crypto available: keep the session local, owner-only, and be honest about it.
        return MAGIC + b"\x00" + payload
    secret = _local_secret()
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive(secret, salt)
    ct = AESGCM(key).encrypt(nonce, payload, MAGIC)
    return MAGIC + b"\x01" + salt + nonce + ct


def _unseal(data: bytes) -> bytes:
    if not data.startswith(MAGIC):
        raise ValueError("not a musebird session file")
    mode = data[len(MAGIC) : len(MAGIC) + 1]
    body = data[len(MAGIC) + 1 :]
    if mode == b"\x00":
        return body
    if mode != b"\x01":
        raise ValueError("unknown session format")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, nonce, ct = body[:16], body[16:28], body[28:]
    return AESGCM(_derive(_local_secret(), salt)).decrypt(nonce, ct, MAGIC)


def _local_secret() -> bytes:
    """A per-machine secret stored beside the vault with 0600.

    This protects against accidental sharing of the session file, not against an
    attacker who can already read the user's whole home directory.
    """
    path = paths().root / "vault.key"
    if path.exists():
        return path.read_bytes()
    secret = os.urandom(32)
    secure_write(path, secret)
    return secret


def _derive(secret: bytes, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(secret)


def redact(value: str) -> str:
    if not value:
        return value
    if len(value) <= 8:
        return "***"
    return value[:3] + "***" + value[-2:]
