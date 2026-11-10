# -*- coding: utf-8 -*-
"""The row cache file, and when its content is trusted.

The cache remembers, per Notion row, when its content was downloaded and
what the add-on wrote for it, so an unchanged row is not downloaded again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

# The cache lives in a JSON file per profile in the add-on's user_files
# folder (kept across add-on upgrades, never sent to AnkiWeb):
#   rows:  row id -> RowState (see RowState.to_json)
#   pages: page id -> [last_edited_time, fetched at, [[database id, title]...]]
#   dbs:   database id -> data source id (looked up again if it stops working)
#   problems: what the last sync could not read, shown in the settings dialog
# Bump RENDER_VERSION whenever the HTML produced for a row changes so every
# row is re-rendered once (row origins and the page structure are kept).
RENDER_VERSION = 6
# Notion's last_edited_time has minute precision; a row only counts as
# unchanged if that whole minute had passed when its content was fetched.
EDIT_PRECISION_SECONDS = 60
# fetched_at is this computer's clock, last_edited_time is Notion's: allow
# for a clock that runs this much ahead of Notion's.
CLOCK_SKEW_SECONDS = 5 * 60
# Cached content is trusted for 7 to 14 days (spread per row so the
# re-downloads don't all land on the same day), so any drift that can't be
# detected heals by itself.
MAX_AGE_SECONDS = 7 * 24 * 3600
AGE_SPREAD_SECONDS = 7 * 24 * 3600
# Rows showing content that can change without bumping their last-edited
# time (page mentions, synced block copies) are checked again this often.
VOLATILE_MAX_AGE_SECONDS = 3600
# RowState.volatile level -> how long the cached content is trusted
VOLATILE_AGES = {1: VOLATILE_MAX_AGE_SECONDS}


@dataclass
class RowState:
    """What the cache knows about one Notion row."""

    edited: str  # Notion last_edited_time; "" = never trust the cache
    fetched_at: float  # local unix time the content was downloaded
    fingerprint: str = ""  # hash of the Front/Back the add-on wrote
    page_id: str = ""  # configured page and table the row was last seen in
    db_id: str = ""
    # content can change without bumping `edited`: 0 = no, 1 = checked
    # hourly (mentions, synced copies)
    volatile: int = 0

    def to_json(self) -> list:
        """Return the compact list stored in the cache file."""
        return [
            self.edited,
            round(self.fetched_at, 1),
            self.fingerprint,
            self.page_id,
            self.db_id,
            int(self.volatile),
        ]

    @classmethod
    def from_json(cls, value: Any) -> Optional["RowState"]:
        """Parse ``to_json``'s output; None if it isn't valid."""
        if not isinstance(value, list) or len(value) != 6:
            return None
        try:
            return cls(
                str(value[0]),
                float(value[1]),
                str(value[2]),
                str(value[3]),
                str(value[4]),
                max(0, min(1, int(value[5]))),  # older caches: false/true
            )
        except (TypeError, ValueError):
            return None


@dataclass
class Cache:
    """The cache file's content (see the comment on RENDER_VERSION)."""

    rows: Dict[str, RowState] = field(default_factory=dict)
    pages: Dict[str, list] = field(default_factory=dict)
    dbs: Dict[str, str] = field(default_factory=dict)
    problems: List[str] = field(default_factory=list)
    # unlisted row id -> when _check_unlisted last asked Notion about it
    checked: Dict[str, float] = field(default_factory=dict)
    # database id -> full name of the deck its cards were put in
    decks: Dict[str, str] = field(default_factory=dict)
    raw: str = ""  # file content as loaded: an identical cache isn't rewritten


def _parse_notion_time(value: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def max_age(key: str) -> float:
    """How long cached content for ``key`` is trusted (7 to 14 days)."""
    spread = (zlib.crc32(key.encode("utf-8")) % 1000) / 1000
    return MAX_AGE_SECONDS + spread * AGE_SPREAD_SECONDS


def _fresh(
    cached_edited: str,
    fetched_at: float,
    edited: str,
    key: str,
    now: Optional[float],
) -> bool:
    if not edited or cached_edited != edited:
        return False
    edited_ts = _parse_notion_time(edited)
    if edited_ts is None:
        return False
    if fetched_at < edited_ts + EDIT_PRECISION_SECONDS + CLOCK_SKEW_SECONDS:
        return False
    now = time.time() if now is None else now
    if fetched_at > now + CLOCK_SKEW_SECONDS:  # clock was wrong when fetched
        return False
    return now - fetched_at < max_age(key)


def is_unchanged(
    cached: Optional[RowState],
    edited: str,
    key: str = "",
    now: Optional[float] = None,
) -> bool:
    """Tell whether the cached content is still what Notion has.

    A volatile row can change without its last_edited_time moving: with page
    mentions or synced block copies it is only trusted for an hour.
    """
    if cached is None:
        return False
    limit = VOLATILE_AGES.get(cached.volatile)
    if limit:
        now = time.time() if now is None else now
        if now - cached.fetched_at >= limit:
            return False
    return _fresh(cached.edited, cached.fetched_at, edited, key, now)


def cache_path(addon_dir: str, profile: str) -> str:
    """One cache file per profile in the add-on's user_files folder."""
    safe = (
        re.sub(r"[^A-Za-z0-9_.-]+", "_", profile).strip("._")[:40] or "profile"
    )
    digest = hashlib.sha1(profile.encode("utf-8")).hexdigest()[:8]
    return os.path.join(addon_dir, "user_files", f"cache-{safe}-{digest}.json")


def load_cache(path: str) -> Cache:
    """Read the cache file; a missing or corrupt file is no cache.

    A file from another RENDER_VERSION keeps only what doesn't depend on
    the rendered HTML: where each row came from (so removal stays scoped)
    and the page structure. Every row is then downloaded once.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        state = json.loads(raw)
    except (OSError, ValueError):
        return Cache()
    if not isinstance(state, dict) or not isinstance(state.get("v"), int):
        return Cache()
    current = state["v"] == RENDER_VERSION
    rows: Dict[str, RowState] = {}
    for key, value in _dict(state.get("rows")).items():
        row = RowState.from_json(value)
        if row is not None:
            rows[str(key)] = (
                row
                if current
                else RowState("", 0.0, "", row.page_id, row.db_id)
            )
    pages = {
        str(k): v
        for k, v in _dict(state.get("pages")).items()
        if _valid_page_entry(v)
    }
    dbs = {str(k): str(v) for k, v in _dict(state.get("dbs")).items() if v}
    problems = state.get("problems")
    if not isinstance(problems, list):
        problems = []
    checked = {
        str(k): float(v)
        for k, v in _dict(state.get("checked")).items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    decks = {
        str(k): v
        for k, v in _dict(state.get("decks")).items()
        if isinstance(v, str) and v
    }
    return Cache(
        rows=rows,
        pages=pages,
        dbs=dbs,
        problems=[str(p) for p in problems],
        checked=checked,
        decks=decks,
        raw=raw if current else "",
    )


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _valid_page_entry(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) in (3, 4)  # 4th: page cards were read
        and isinstance(value[0], str)
        and isinstance(value[1], (int, float))
        and isinstance(value[2], list)
        and all(isinstance(t, list) and len(t) == 2 for t in value[2])
    )


def _cache_text(cache: Cache) -> str:
    state: Dict[str, Any] = {
        "v": RENDER_VERSION,
        "rows": {k: v.to_json() for k, v in sorted(cache.rows.items())},
        "pages": dict(sorted(cache.pages.items())),
        "dbs": dict(sorted(cache.dbs.items())),
        "problems": list(cache.problems),
    }
    if cache.checked:  # absent when empty: older caches stay identical
        state["checked"] = dict(sorted(cache.checked.items()))
    if cache.decks:
        state["decks"] = dict(sorted(cache.decks.items()))
    return json.dumps(
        state,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def save_cache(path: str, cache: Cache) -> bool:
    """Write the cache atomically, only when it changed. True if written."""
    text = _cache_text(cache)
    if text == cache.raw:
        return False
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".cache-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    cache.raw = text
    return True


def known_tables(
    path: str, page_ids: Iterable[str]
) -> List[Tuple[str, List[Tuple[str, str]]]]:
    """(page id, [(database id, title)...]) the last sync saw, for the
    configured pages that have tables (for the settings dialog).
    """
    pages = load_cache(path).pages
    out: List[Tuple[str, List[Tuple[str, str]]]] = []
    for page_id in page_ids:
        entry = pages.get(page_id)
        if entry and entry[2]:
            out.append((page_id, [(str(d), str(t)) for d, t in entry[2]]))
    return out


def load_problems(path: str) -> List[str]:
    """Return what the last sync could not read (for the dialog)."""
    return list(load_cache(path).problems)
