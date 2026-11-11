"""Keeps secrets out of logs and of anything stored or shown."""

from __future__ import annotations

import logging
import re
import threading
from typing import Set

MARK = "[redacted]"
# internal integration tokens, whether or not this process has seen them
_NOTION_TOKEN = re.compile(r"\b(?:secret|ntn)_[A-Za-z0-9]{20,}")
_MIN_LENGTH = 6  # shorter strings would blank out ordinary words

_lock = threading.Lock()
_secrets: Set[str] = set()


def register(secret: str) -> None:
    """Have every later ``scrub`` remove this value."""
    if secret and len(secret) >= _MIN_LENGTH:
        with _lock:
            _secrets.add(secret)


def scrub(text: str) -> str:
    """Return ``text`` without any known secret."""
    with _lock:
        known = sorted(_secrets, key=len, reverse=True)
    for secret in known:
        text = text.replace(secret, MARK)
    return _NOTION_TOKEN.sub(MARK, text)


class ScrubbingFormatter(logging.Formatter):
    """Scrub the whole formatted record, tracebacks included."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the record, then remove secrets from the result."""
        return scrub(super().format(record))


def configure_logging(level: int = logging.INFO) -> None:
    """Send every logger through a single scrubbing handler."""
    handler = logging.StreamHandler()
    handler.setFormatter(
        ScrubbingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
