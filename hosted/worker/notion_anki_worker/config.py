"""Settings read from the environment. Secrets stay out of ``repr``."""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass, field
from pathlib import Path

KEY_BYTES = 32


class ConfigError(Exception):
    """A required environment variable is missing or malformed."""


def _required(env: dict, name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set")
    return value


def _key(env: dict) -> bytes:
    try:
        key = base64.b64decode(_required(env, "ENCRYPTION_KEY"), validate=True)
    except binascii.Error as e:
        raise ConfigError("ENCRYPTION_KEY is not valid base64") from e
    if len(key) != KEY_BYTES:
        raise ConfigError(f"ENCRYPTION_KEY must decode to {KEY_BYTES} bytes")
    return key


@dataclass(frozen=True)
class Settings:
    """Everything the worker needs to start."""

    data_dir: Path
    api_secret: str = field(repr=False)
    encryption_key: bytes = field(repr=False)
    owner_password_hash: str = field(repr=False)
    port: int = 8000
    # Sign in with Notion: optional, pasting a token keeps working without
    notion_client_id: str = ""
    notion_client_secret: str = field(default="", repr=False)
    notion_redirect_uri: str = ""

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        """Read the settings; raises ``ConfigError`` when one is unusable."""
        env = dict(os.environ if env is None else env)
        return cls(
            data_dir=Path(env.get("DATA_DIR", "/data")),
            api_secret=_required(env, "WORKER_API_SECRET"),
            encryption_key=_key(env),
            owner_password_hash=_required(env, "OWNER_PASSWORD_HASH"),
            port=int(env.get("PORT", "8000")),
            notion_client_id=env.get("NOTION_CLIENT_ID", "").strip(),
            notion_client_secret=env.get("NOTION_CLIENT_SECRET", "").strip(),
            notion_redirect_uri=env.get("NOTION_REDIRECT_URI", "").strip(),
        )
