"""AES-256-GCM for stored secrets, password hashing and signed tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = "v1"
_NONCE_BYTES = 12
SESSION_SECONDS = 7 * 24 * 3600
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_LEN = 32
_SCRYPT_MEM = 64 * 1024 * 1024


class DecryptError(Exception):
    """A stored value can't be read with the current key."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def derive_key(master: bytes, purpose: str) -> bytes:
    """Return a key for ``purpose``, independent of the keys for others."""
    return hmac.new(master, purpose.encode("utf-8"), hashlib.sha256).digest()


class Vault:
    """Encrypts values; each is bound to the name it is stored under."""

    def __init__(self, key: bytes) -> None:
        self._aead = AESGCM(key)

    def encrypt(self, name: str, plain: str) -> str:
        """Encrypt ``plain`` for storage under ``name``."""
        nonce = os.urandom(_NONCE_BYTES)
        body = self._aead.encrypt(
            nonce, plain.encode("utf-8"), name.encode("utf-8")
        )
        return f"{_VERSION}:{_b64(nonce)}:{_b64(body)}"

    def decrypt(self, name: str, stored: str) -> str:
        """Decrypt a value stored under ``name``."""
        try:
            version, nonce, body = stored.split(":")
            if version != _VERSION:
                raise ValueError(version)
            plain = self._aead.decrypt(
                _unb64(nonce), _unb64(body), name.encode("utf-8")
            )
        except (ValueError, InvalidTag) as e:
            raise DecryptError(f"cannot decrypt {name}") from e
        return plain.decode("utf-8")


def hash_password(password: str) -> str:
    """Return an scrypt hash for ``OWNER_PASSWORD_HASH``."""
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MEM,
        dklen=_SCRYPT_LEN,
    )
    params = f"{_SCRYPT_N}:{_SCRYPT_R}:{_SCRYPT_P}"
    return f"scrypt:{params}:{_b64(salt)}:{_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Check ``password`` against a ``hash_password`` value."""
    try:
        scheme, n, r, p, salt, digest = stored.split(":")
        if scheme != "scrypt":
            return False
        expected = _unb64(digest)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_unb64(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_SCRYPT_MEM,
            dklen=len(expected),
        )
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


class TokenSigner:
    """Short signed tokens with an expiry; no state on the server."""

    def __init__(self, key: bytes) -> None:
        self._key = key

    def _mac(self, body: str) -> str:
        return _b64(
            hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest()
        )

    def issue(self, subject: str, ttl: int = SESSION_SECONDS) -> str:
        """Return a token for ``subject`` that lasts ``ttl`` seconds."""
        claims = {"sub": subject, "exp": int(time.time()) + ttl}
        body = _b64(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
        return f"{body}.{self._mac(body)}"

    def verify(self, token: str) -> Optional[str]:
        """Return the token's subject, or None if invalid or expired."""
        try:
            body, mac = token.split(".")
            if not hmac.compare_digest(mac, self._mac(body)):
                return None
            claims = json.loads(_unb64(body))
        except (ValueError, TypeError):
            return None
        if not isinstance(claims, dict) or claims.get("exp", 0) < time.time():
            return None
        subject = claims.get("sub")
        return subject if isinstance(subject, str) else None

    def sign_path(self, path: str, expires: int) -> str:
        """Sign a path and an absolute expiry time."""
        return self._mac(_b64(f"{path}\n{expires}".encode("utf-8")))

    def check_path(self, path: str, expires: int, signature: str) -> bool:
        """Check ``sign_path``'s result and that it has not expired."""
        if expires < time.time():
            return False
        return hmac.compare_digest(
            signature.encode("utf-8"),
            self.sign_path(path, expires).encode("utf-8"),
        )
