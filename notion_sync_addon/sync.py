# -*- coding: utf-8 -*-
"""Sync orchestration.

Phase 0 (collection, background QueryOp): ``prepare_sync``
    load the row cache file and check it against the collection: a row
    whose note is missing or no longer holds exactly what the add-on wrote
    (edited in Anki, undone, changed on another device) is dropped from the
    cache, so it is downloaded again and overwritten with Notion's version.

Phase 1 (network, background thread, no collection): ``fetch_pages``
    walk the configured Notion pages -> build PageData structures
    (tables -> rows -> rendered HTML with media placeholders),
    download all referenced media into a temp dir.
    Rows whose Notion ``last_edited_time`` has not changed since the last
    sync are not re-downloaded (see ``is_unchanged``). The same goes for the
    list of tables on an unchanged page, and each table's data source id is
    remembered, so a sync with no edits only lists pages and table rows.

Phase 2 (collection, background QueryOp): ``apply_sync``
    ensure note type / decks exist, import media, add or update notes,
    remove obsolete ones, write the row cache file.
"""

from __future__ import annotations

import filecmp
import hashlib
import html
import json
import os
import re
import shutil
import tempfile
import time
import unicodedata
import zlib
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Set,
    Tuple,
)

from .htmlify import (
    MediaCollector,
    StrikeTags,
    render_blocks,
    render_lines,
    rich_text_to_html,
)
from .notion_api import (
    BlockSource,
    BlockTree,
    NotionClient,
    NotionError,
    data_source_of,
    page_title,
    plain_text,
    title_rich_text,
)
from .pagecards import SOURCE as SOURCE_ID  # a Notion id: 32 hex digits
from .pagecards import PageCard, has_cloze, parse_page, rt_text

MODEL_NAME = "Notion Sync Card"
UNDO_NAME = "Notion Sync"
# The add-on's own note types are found by id, kept in the collection config
# (synced via AnkiWeb): role ("basic", ...) -> note type id. The name is only
# the fallback, so renaming a note type in Anki changes nothing.
NOTE_TYPES_KEY = "notionSync.noteTypes"
# Two more entries of that dict, not roles: table (database id) -> id of the
# note type its new rows use, and the ids of every chosen type ever used
# (their notes stay synced after the choice changes).
TABLES_KEY = "tables"
USED_KEY = "used"
RESERVED_KEYS = (TABLES_KEY, USED_KEY)
# Fixed field positions of the basic type: names can be renamed in Anki.
FRONT, BACK, SOURCE = 0, 1, 2
# The add-on owns the tags under this prefix (Anki tags are case-insensitive)
# and never touches any other tag.
TAG_PREFIX = "Notion::"
# Marks the notes whose cards were suspended because the row was unchecked.
UNCHECKED_TAG = "notion-sync::unchecked"

# The cache lives in a JSON file per profile in the add-on's user_files
# folder (kept across add-on upgrades, never sent to AnkiWeb):
#   rows:  row id -> RowState (see RowState.to_json)
#   pages: page id -> [last_edited_time, fetched at, [[database id, title]...]]
#   dbs:   database id -> data source id (looked up again if it stops working)
#   problems: what the last sync could not read, shown in the settings dialog
# Bump RENDER_VERSION whenever the HTML produced for a row changes so every
# row is re-rendered once (row origins and the page structure are kept).
RENDER_VERSION = 6
# Older versions kept the cache in the collection config under this key.
LEGACY_STATE_KEY = "notionSync.rowState"
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
# When part of Notion can't be read, notes whose row origin is unknown are
# looked up one by one (GET /pages/<row>); at most this many per sync.
MAX_ORIGIN_CHECKS = 25
# A row that was asked about is asked again after this long, so rows that
# keep failing don't starve the ones not yet asked.
ORIGIN_RECHECK_SECONDS = 24 * 3600
# The Source of a synced note is a Notion row id (SOURCE_ID). Notes of the
# add-on's note type with any other Source were made by hand.

Progress = Callable[[str], None]


def _no_progress(_message: str) -> None:
    pass


# --------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------- #


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


@dataclass
class RowCard:
    """One Notion row, to become one note."""

    source_id: str
    front: str
    back_html: str  # may contain @@NSMEDIA:*@@ placeholders
    url: str
    unchanged: bool = False  # content not fetched: note is already current
    tags: List[str] = field(default_factory=list)  # Notion::... tags
    unchecked: bool = False  # sync checkbox off: no card, content not read
    columns: Optional["RowColumns"] = None  # the row's parsed properties
    context_html: str = ""  # the page's "Hint" section, same placeholders
    # "basic", "cloze" or "overlapping" ("" = content not read)
    kind: str = ""
    lines: List[str] = field(default_factory=list)  # overlapping: its lines
    overlap: Optional["OverlapSpec"] = None  # overlapping: Card type options


@dataclass
class TableData:
    """One Notion table (a subdeck) and its rows."""

    title: str
    rows: List[RowCard] = field(default_factory=list)
    db_id: str = ""
    raw_title: str = ""  # Notion title before deck_safe()


@dataclass
class PageData:
    """One configured Notion page (a deck) and its tables."""

    title: str
    deck_name: str
    tables: List[TableData] = field(default_factory=list)


@dataclass
class Prepared:
    """Output of ``prepare_sync``: the cache checked against the notes."""

    known: Dict[str, RowState]
    structure: Dict[str, dict]
    previous: Cache
    sources: Set[str] = field(default_factory=set)  # Sources of synced notes
    media_folder: str = ""  # the collection's media folder ("" if unknown)


class SyncResult:
    """Everything read from Notion, plus what apply_sync needs to know."""

    def __init__(self):
        self.pages: List[PageData] = []
        self.config: dict = {}
        self.media_dir: str = ""
        self.media_files: List[Tuple[str, str]] = []  # (abs path, fname)
        self.failed_media: Set[str] = set()
        self.warnings: List[str] = []
        # trusted row cache before this run / cache entries after it
        self.known: Dict[str, RowState] = {}
        self.fetched: Dict[str, RowState] = {}
        # the whole cache file before this run (row origins, structure)
        self.previous: Cache = Cache()
        self.cache_path: str = ""
        # page structure and data source ids: before this run / after it
        self.page_cache: Dict[str, list] = {}
        self.db_cache: Dict[str, str] = {}
        self.pages_out: Dict[str, list] = {}
        self.dbs_out: Dict[str, str] = {}
        # table -> deck name, for the tables read cleanly (see _move_renamed)
        self.decks_out: Dict[str, str] = {}
        self.page_ids: List[str] = []  # configured pages, canonical ids
        # set when part of Notion could not be read. Notes are then only
        # removed if the page and table they last came from were read fine,
        # so a network error can never delete cards.
        self.incomplete = False
        self.failures = 0
        self.failed_pages: Set[str] = set()
        self.failed_dbs: Set[str] = set()
        self.unscoped_failure = (
            False  # a failure no page/table can be blamed for
        )
        self.protected: Set[str] = (
            set()
        )  # rows that exist in Notion but weren't read
        # config entries that aren't a Notion page id or link
        self.invalid_entries: List[str] = []
        # short descriptions of what could not be read (kept in the cache
        # file so the settings dialog can show them)
        self.problems: List[str] = []
        # unlisted rows Notion confirmed are in the trash: safe to remove
        self.confirmed_gone: Set[str] = set()
        # row origins learned by looking unlisted rows up (see _check_unlisted)
        self.recovered: Dict[str, RowState] = {}
        # unlisted rows asked about in this run: row id -> time
        self.checked: Dict[str, float] = {}
        # Sources of the synced notes before / after this sync
        self.sources: Set[str] = set()
        self.sources_after: Optional[Set[str]] = None
        self.dbs_drop: Set[str] = (
            set()
        )  # cached data source ids that stopped working
        self.db_pages: Dict[str, str] = (
            {}
        )  # every table met -> its configured page
        self.changes: Any = (
            None  # OpChanges of the undo step, None = no changes
        )
        self.progress: Progress = _no_progress

    def cleanup(self) -> None:
        """Delete the downloaded media files."""
        if self.media_dir:
            shutil.rmtree(self.media_dir, ignore_errors=True)


def _fail(
    result: SyncResult,
    message: str,
    page_id: str = "",
    db_id: str = "",
    problem: str = "",
) -> None:
    result.incomplete = True
    result.failures += 1
    result.warnings.append(message)
    result.problems.append(problem or message)
    if db_id:
        result.failed_dbs.add(db_id)
    elif page_id:
        result.failed_pages.add(page_id)
    else:
        result.unscoped_failure = True


# --------------------------------------------------------------------- #
# Config / token helpers
# --------------------------------------------------------------------- #


def resolve_token(cfg: Optional[dict]) -> str:
    """Order: config value -> NOTION_API_TOKEN env var -> .env file."""
    if cfg and str(cfg.get("notion_token") or "").strip():
        return str(cfg["notion_token"]).strip()
    val = os.environ.get("NOTION_API_TOKEN", "").strip()
    if val:
        return val
    try:
        from aqt import mw  # noqa: F401

        addon_dir = os.path.dirname(os.path.abspath(__file__))
        bases: Tuple[str, ...] = (
            os.path.join(addon_dir, "user_files"),
            addon_dir,
            os.getcwd(),
            os.path.expanduser("~"),
        )
    except Exception:
        bases = (os.getcwd(), os.path.expanduser("~"))
    for base in bases:
        value = _dotenv_value(
            _read_dotenv(os.path.join(base, ".env")), "NOTION_API_TOKEN"
        )
        if value:
            return value
    return ""


def _read_dotenv(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except OSError:
        return ""


def _dotenv_value(content: str, key: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith(key + "="):
            val = line.split("=", 1)[1].strip().strip("'\"")
            if val and val != "your_token_here":
                return val
    return ""


def canonical_page_id(raw: str) -> str:
    """Turn a Notion URL or dashed/undashed id into the plain 32-hex id."""
    raw = raw.strip()
    bare = raw.replace("-", "")
    if re.fullmatch(r"[0-9a-fA-F]{32}", bare):
        return bare.lower()
    # a link: the id follows the title words (.../Philo-Histoire-<32hex>)
    match = re.search(
        r"(?i)([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9a-f]{32})",
        raw,
    )
    if match:
        return match.group(1).replace("-", "").lower()
    raise NotionError(f"Cannot parse page id: {raw}")


def deck_safe(name: str) -> str:
    """Turn a Notion title into a valid Anki deck name component."""
    name = re.sub(r"\s+", " ", name).strip()
    name = name.replace("::", "/")  # protect path separator first
    name = re.sub(r"[\x00:\"]", " ", name)  # drop any other illegal chars
    return name or "Untitled"


# --------------------------------------------------------------------- #
# Row properties (columns)
# --------------------------------------------------------------------- #

COLUMN_DEFAULTS = {
    "sync_column": "Anki",
    "card_type_column": "Card type",
}


@dataclass
class RowColumns:
    """What a row's properties say, read by ``row_columns``."""

    sync: Optional[bool] = None  # None = the table has no sync column
    card_type: str = ""  # lowercased select name
    props: Dict[str, str] = field(default_factory=dict)  # name -> text


def _date_text(date: Any) -> str:
    if not isinstance(date, dict) or not date.get("start"):
        return ""
    text = str(date["start"])
    if date.get("end"):
        text += f" \u2192 {date['end']}"
    return text


def _names(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    return [
        str(i.get("name") or "")
        for i in items
        if isinstance(i, dict) and i.get("name")
    ]


def _value(prop: dict) -> Any:
    kind = prop.get("type")
    return prop.get(kind) if isinstance(kind, str) else None


def _prop_text(prop: dict) -> str:
    """Plain text of one property value (see ``row_columns``)."""
    kind = prop.get("type")
    value = _value(prop)
    if kind in ("title", "rich_text"):
        return plain_text(value)
    if kind in ("select", "status"):
        return str((value or {}).get("name") or "")
    if kind in ("multi_select", "people"):
        return ", ".join(_names(value))
    if kind == "number":
        return "" if value is None else str(value)
    if kind == "checkbox":
        return "Yes" if value else ""
    if kind == "date":
        return _date_text(value)
    if kind in ("url", "email", "phone_number"):
        return str(value or "")
    if kind == "formula" and isinstance(value, dict):
        inner = value.get(value.get("type") or "")
        if value.get("type") == "date":
            return _date_text(inner)
        if value.get("type") == "boolean":
            return "Yes" if inner else ""
        return "" if inner is None else str(inner)
    return ""


def _find_prop(props: dict, name: str) -> Optional[dict]:
    """Find the property called ``name`` (ignoring case and space)."""
    wanted = name.strip().lower()
    if not wanted:
        return None
    for key, prop in props.items():
        if str(key).strip().lower() == wanted and isinstance(prop, dict):
            return prop
    return None


def clean_tag(raw: str) -> str:
    """Make a Notion value a valid Anki tag ("" if nothing is left)."""
    text = re.sub(r"\s+", "_", raw.strip())
    text = re.sub(r'["\x00-\x1f\x7f]', "", text)
    parts = [p.strip(":") for p in text.split("::")]
    return "::".join(p for p in parts if p)


def clean_tags(values: Iterable[str]) -> List[str]:
    """Clean tags, dropping empties and case-insensitive duplicates."""
    out: Dict[str, str] = {}
    for value in values:
        tag = clean_tag(value)
        if tag:
            out.setdefault(tag.lower(), tag)
    return list(out.values())


def row_columns(row: dict, cfg: dict) -> RowColumns:
    """Read the special columns of a row listed by a data source query.

    Column names come from the config (``sync_column`` ...), matched
    ignoring case and surrounding space; an empty name disables the column.
    """

    def name(key: str) -> str:
        value = cfg.get(key, COLUMN_DEFAULTS[key])
        return "" if value is None else str(value)

    props = row.get("properties")
    props = props if isinstance(props, dict) else {}
    out = RowColumns()
    out.props = {
        str(key): _prop_text(prop)
        for key, prop in props.items()
        if isinstance(prop, dict)
    }
    sync = _find_prop(props, name("sync_column"))
    if sync is not None:
        if sync.get("type") == "checkbox":
            out.sync = bool(sync.get("checkbox"))
        elif sync.get("type") == "formula":
            formula = sync.get("formula") or {}
            if formula.get("type") == "boolean":
                out.sync = bool(formula.get("boolean"))
    card_type = _find_prop(props, name("card_type_column"))
    if card_type is not None and card_type.get("type") in (
        "select",
        "status",
    ):
        out.card_type = _prop_text(card_type).strip().lower()
    return out


# --------------------------------------------------------------------- #
# Cache freshness
# --------------------------------------------------------------------- #


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


# --------------------------------------------------------------------- #
# Cache file
# --------------------------------------------------------------------- #


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


# --------------------------------------------------------------------- #
# Collection side of the cache
# --------------------------------------------------------------------- #


def owned_tags(tags: Iterable[str]) -> List[str]:
    """Pick the tags the add-on owns (under ``Notion::``)."""
    return [t for t in tags if t.lower().startswith(TAG_PREFIX.lower())]


def owned_key(tags: Iterable[str]) -> str:
    """Owned tags as a sorted, lowercase string for ``fingerprint``."""
    return " ".join(sorted(t.lower() for t in owned_tags(tags)))


def fingerprint(*content: str, tags: str = "") -> str:
    """Return a short hash of a note's content fields and owned tags.

    ``tags`` is ``owned_key`` of the note's tags; without owned tags the hash
    is the one older versions computed, so existing caches stay valid.
    """
    data = "\x1f".join(content)
    if tags:
        data += "\x1e" + tags
    return hashlib.sha1(data.encode("utf-8")).hexdigest()[:16]


# role -> name used when the stored id is missing (see NOTE_TYPES_KEY)
CLOZE_MODEL_NAME = "Notion Sync Cloze"
MODEL_NAMES = {"basic": MODEL_NAME, "cloze": CLOZE_MODEL_NAME}


def _registry(col) -> Dict[str, Any]:
    """Read the stored note type ids ({} if none or unreadable)."""
    try:
        value = col.get_config(NOTE_TYPES_KEY, None)
    except Exception:  # noqa: BLE001 - no config, no registry
        return {}
    return dict(value) if isinstance(value, dict) else {}


def find_model(col, role: str = "basic") -> Optional[dict]:
    """Find the add-on's note type for ``role``: by id, then by name."""
    mid = _registry(col).get(role)
    if isinstance(mid, int) and not isinstance(mid, bool):
        model = col.models.get(mid)
        if model is not None:
            return model
    name = MODEL_NAMES.get(role)
    return col.models.by_name(name) if name else None


def own_model_ids(col) -> Dict[str, int]:
    """Role -> note type id of the add-on's own types that exist."""
    out: Dict[str, int] = {}
    roles = (set(_registry(col)) | set(MODEL_NAMES)) - set(RESERVED_KEYS)
    for role in sorted(roles):
        model = find_model(col, role)
        if model is not None and model.get("id"):
            out[role] = int(model["id"])
    return out


def _int_id(value: Any) -> Optional[int]:
    if isinstance(value, int) and not isinstance(value, bool) and value:
        return value
    return None


def table_note_types(col) -> Dict[str, int]:
    """Table (database id) -> id of the note type chosen for its new rows.

    Entries whose note type no longer exists are left out.
    """
    stored = _registry(col).get(TABLES_KEY)
    out: Dict[str, int] = {}
    if isinstance(stored, dict):
        for db_id, mid in stored.items():
            mid = _int_id(mid)
            if mid is not None and col.models.get(mid) is not None:
                out[bare_id(str(db_id))] = mid
    return out


def _used_model_ids(col) -> List[int]:
    """Ids of every chosen note type the add-on has ever used (and that
    still exist): their notes stay synced after the choice changes.
    """
    registry = _registry(col)
    ids = set(table_note_types(col).values())
    stored = registry.get(USED_KEY)
    if isinstance(stored, list):
        for mid in stored:
            mid = _int_id(mid)
            if mid is not None and col.models.get(mid) is not None:
                ids.add(mid)
    return sorted(ids - set(own_model_ids(col).values()))


def _source_index(model: dict) -> Optional[int]:
    """Position of the field named Source (None unless there is exactly
    one, after the title and page fields).
    """
    found = [
        i
        for i, fld in enumerate(model.get("flds") or [])
        if str(fld.get("name", "")).strip().casefold() == "source"
    ]
    return found[0] if len(found) == 1 and found[0] >= 2 else None


def eligible_note_types(col) -> List[Tuple[int, str]]:
    """(id, name) of the note types a table's rows can be exported to.

    Standard (not cloze) types with at least three fields, one of them
    named Source, and no note already holding a Source value, so the user's
    own notes can never be mistaken for synced ones. A type the add-on
    already uses is always eligible. The add-on's own types are left out
    (they are the default and the cloze type).
    """
    own = set(own_model_ids(col).values())
    used = set(_used_model_ids(col))
    out: List[Tuple[int, str]] = []
    for model in col.models.all():
        mid = int(model["id"])
        index = _source_index(model)
        if (
            mid in own
            or model.get("type") != 0
            or len(model.get("flds") or []) < 3
            or index is None
        ):
            continue
        if mid not in used and _has_source(col, mid, index):
            continue
        out.append((mid, str(model["name"])))
    return sorted(out, key=lambda item: item[1].casefold())


def _has_source(col, mid: int, index: int) -> bool:
    for (flds,) in col.db.all("select flds from notes where mid = ?", mid):
        parts = flds.split("\x1f")
        if len(parts) > index and parts[index].strip():
            return True
    return False


def set_table_note_types(col, changes: Dict[str, int]) -> bool:
    """Choose the note type of tables: database id -> note type id, 0 for
    the default. Tables not named stay as they are. Ids that are not
    eligible are ignored. One undo step, only when something changed.
    """
    registry = _registry(col)
    stored = registry.get(TABLES_KEY)
    tables: Dict[str, int] = {}
    if isinstance(stored, dict):
        tables = {bare_id(str(k)): v for k, v in stored.items() if _int_id(v)}
    eligible = {mid for mid, _name in eligible_note_types(col)}
    for db_id, mid in changes.items():
        key = bare_id(db_id)
        if not mid:
            tables.pop(key, None)
        elif mid in eligible:
            tables[key] = mid
    used = registry.get(USED_KEY)
    used_ids = (
        [m for m in used if _int_id(m)] if isinstance(used, list) else []
    )
    for mid in tables.values():
        if mid not in used_ids:
            used_ids.append(mid)
    if tables == (stored or {}) and used_ids == (used or []):
        return False
    if tables:
        registry[TABLES_KEY] = tables
    else:
        registry.pop(TABLES_KEY, None)
    if used_ids:
        registry[USED_KEY] = used_ids
    col.set_config(NOTE_TYPES_KEY, registry, undoable=True)
    return True


def _remember_model(
    col, role: str, model: dict, before_change: Callable[[], None]
) -> None:
    """Store the note type id, only when it differs (unknown roles stay)."""
    registry = _registry(col)
    if registry.get(role) == model["id"]:
        return
    before_change()
    registry[role] = model["id"]
    col.set_config(NOTE_TYPES_KEY, registry, undoable=True)


class SyncedNote(NamedTuple):
    """One note made by the add-on."""

    nid: int
    fp: str  # fingerprint of its content fields and owned tags
    mid: int  # note type id
    tags: str  # Anki's tag string of the note


def synced_notes(col) -> Dict[str, List[SyncedNote]]:
    """Source id -> synced notes, oldest first.

    One SQL query over the add-on's own note types and the types chosen for
    tables. Only notes whose Source is a Notion row id count: notes without
    one, or with anything else in it (added by hand), are left out and never
    touched. Fields of the own types are read by position, so renaming them
    in Anki changes nothing; a chosen type is found by its Source field.
    """
    # note type id -> position of its Source field
    positions: Dict[int, int] = {
        mid: SOURCE for mid in set(own_model_ids(col).values())
    }
    for mid in _used_model_ids(col):
        index = _source_index(col.models.get(mid) or {})
        if index is not None:
            positions[mid] = index
    mids = sorted(positions)
    if not mids:
        return {}
    marks = ",".join("?" * len(mids))
    sql = (
        "select id, mid, flds, tags from notes "
        f"where mid in ({marks}) order by id"
    )
    out: Dict[str, List[SyncedNote]] = {}
    for nid, mid, flds, tags in col.db.all(sql, *mids):
        parts = flds.split("\x1f")
        if len(parts) <= positions[mid]:
            continue
        source = parts[positions[mid]].strip()
        if not SOURCE_ID.fullmatch(source):
            continue
        fp = fingerprint(
            parts[FRONT], parts[BACK], tags=owned_key(tags.split())
        )
        out.setdefault(source, []).append(SyncedNote(nid, fp, mid, tags))
    return out


def trusted_rows(
    cache: Cache, notes: Dict[str, List[SyncedNote]]
) -> Dict[str, RowState]:
    """Return the cached rows whose note holds what the add-on wrote.

    Anything else (note deleted, edited in Anki, undone, changed by another
    device) is downloaded again and overwritten with Notion's content.
    """
    out: Dict[str, RowState] = {}
    for key, state in cache.rows.items():
        entries = notes.get(key)
        if (
            entries
            and state.fingerprint
            and entries[0].fp == state.fingerprint
        ):
            out[key] = state
    return out


def prepare_sync(col, path: str, full: bool = False) -> Prepared:
    """Load the cache and check it against the collection (background op)."""
    previous = load_cache(path)
    notes = synced_notes(col)
    try:
        media_folder = str(col.media.dir())
    except Exception:  # noqa: BLE001 - staging media is only an optimisation
        media_folder = ""
    if full:  # Full Resync: trust nothing, but keep row origins for removal
        return Prepared(
            known={},
            structure={},
            previous=previous,
            sources=set(notes),
            media_folder=media_folder,
        )
    known = trusted_rows(previous, notes)
    structure: Dict[str, dict] = {
        "pages": dict(previous.pages),
        "dbs": dict(previous.dbs),
    }
    return Prepared(
        known=known,
        structure=structure,
        previous=previous,
        sources=set(notes),
        media_folder=media_folder,
    )


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


def _drop_legacy_state(col, before_change: Callable[[], None]) -> None:
    """Older versions kept the cache in the collection config, which is
    sent to AnkiWeb on every sync: remove it once. Removing config is always
    undoable in Anki, so it joins the sync's own undo step.
    """
    try:
        if col.get_config(LEGACY_STATE_KEY, None) is None:
            return
        before_change()
        col.remove_config(LEGACY_STATE_KEY)
    except Exception:  # noqa: BLE001 - cleanup only
        pass


# --------------------------------------------------------------------- #
# Phase 1: fetch from Notion
# --------------------------------------------------------------------- #


def fetch_pages(
    cfg: dict,
    known: Optional[Dict[str, RowState]] = None,
    progress: Optional[Progress] = None,
    structure: Optional[Dict[str, dict]] = None,
    previous: Optional[Cache] = None,
    sources: Optional[Set[str]] = None,
    media_folder: str = "",
    should_stop: Optional[Callable[[], bool]] = None,
) -> SyncResult:
    """Read all configured pages. Rows in ``known`` that were not edited
    since they were fetched are returned as ``unchanged`` placeholders.
    ``structure`` (pages/dbs of the cache) skips re-reading unchanged
    pages and re-resolving data source ids. ``previous`` is the whole cache,
    used to scope note removal when part of Notion can't be read.
    ``sources`` are the Sources of the synced notes in the collection: when
    part of Notion can't be read, unlisted ones of unknown origin are
    looked up so they can be removed or kept for certain.
    New media files are copied into ``media_folder`` here, without the
    collection, until ``should_stop()`` says to give up.
    """
    client = NotionClient(resolve_token(cfg))
    tree = BlockTree(client)
    media = MediaCollector()
    result = SyncResult()
    result.config = cfg
    result.known = dict(known or {})
    result.previous = previous or Cache()
    result.sources = set(sources or ())
    if structure:
        result.page_cache = dict(structure.get("pages") or {})
        result.db_cache = dict(structure.get("dbs") or {})
    if progress is not None:
        result.progress = progress
    result.media_dir = tempfile.mkdtemp(prefix="notion-anki-sync-")
    try:
        for entry in cfg.get("notion_pages", []):
            raw = entry if isinstance(entry, str) else entry.get("page_id", "")
            raw = str(raw).strip()
            if not raw:
                continue
            try:
                page_id = canonical_page_id(raw)
            except NotionError:
                # a config mistake, not a Notion failure: no card ever came
                # from this entry, so it doesn't hold up removal elsewhere
                # (see _removal_blocked for an entry that used to be valid)
                result.invalid_entries.append(raw)
                message = (
                    f"Ignored page '{raw}' in the settings: "
                    "not a Notion page link or id"
                )
                result.warnings.append(message)
                result.problems.append(message)
                continue
            if page_id in result.page_ids:  # listed twice: read it once
                continue
            result.page_ids.append(page_id)
            try:
                _fetch_page(page_id, client, tree, media, result)
            except NotionError as e:
                _fail(
                    result,
                    str(e),
                    page_id=page_id,
                    problem=f"Page {page_id[:8]}...: {e.reason()}",
                )

        if result.sources and result.incomplete:
            _check_unlisted(client, result)
        result.media_files, result.failed_media = _download_media(
            client, media, result.media_dir, result.progress
        )
        _stage_media(result, media_folder, should_stop)
        _forget_rows_with_failed_media(result)
    except BaseException:
        result.cleanup()
        raise
    return result


def _fetch_page(
    page_id: str,
    client: NotionClient,
    tree: BlockSource,
    media: MediaCollector,
    result: SyncResult,
) -> None:
    result.progress("reading pages")
    page = client.get_page(page_id)
    title = page_title(page) or page_id[:8]
    data = PageData(title=title, deck_name=deck_safe(title))
    result.pages.append(data)
    failures = result.failures

    edited = str(page.get("last_edited_time") or "")
    cached = result.page_cache.get(page_id)
    if cached and _fresh(cached[0], float(cached[1]), edited, page_id, None):
        # page untouched: same tables as last time, skip walking its blocks
        for db_id, db_title in cached[2]:
            block = {
                "id": db_id,
                "type": "child_database",
                "child_database": {"title": db_title},
            }
            table = _fetch_table(
                client, tree, block, media, data.deck_name, result, page_id
            )
            if table is not None:
                data.tables.append(table)
        _page_cards(
            page_id,
            edited,
            None,
            client,
            tree,
            media,
            data,
            result,
            read_before=len(cached) > 3 and bool(cached[3]),
        )
        if result.failures == failures:
            result.pages_out[page_id] = cached
        return

    fetched_at = time.time()
    blocks = tree.children(page_id)
    _collect_page_children(
        page_id, client, tree, media, data, result, blocks=blocks
    )
    _page_cards(page_id, edited, blocks, client, tree, media, data, result)
    # a page with any failure keeps its previous cache entry: a table that
    # could not be read must not vanish from the cached table list
    if edited and result.failures == failures:
        entry: list = [
            edited,
            fetched_at,
            [
                [t.db_id, t.raw_title]
                for t in data.tables
                if not t.db_id.startswith(PAGE_CARDS)
            ],
        ]
        if result.config.get("page_cards", True):
            entry.append(1)  # the page's own cards were read
        result.pages_out[page_id] = entry


PAGE_CARDS = "pagecards:"  # db_id prefix of a page's own cards


def _numbered(blocks: List[dict]) -> bool:
    """Tell whether the blocks are a numbered list of two items or more."""
    items = [b for b in blocks if b.get("type") != "divider"]
    return len(items) >= 2 and all(
        b.get("type") == "numbered_list_item" for b in items
    )


def _page_card_type(card: PageCard, answer: List[dict]) -> str:
    """Return the "Card type" a found card is rendered as ("" = by
    content): overlapping when options are written after the question or
    the answer is a numbered list.
    """
    if card.spec is not None:
        rest = re.sub(r"(?i)^overlap(?:ping)?", "", card.spec).strip().lower()
        rest = re.sub(r"(\d)\s*x", r"\1 x", rest)
        return f"overlapping {rest}".strip()
    if card.origin in ("heading", "qa") and _numbered(split_hint(answer)[0]):
        return "overlapping"
    return ""


def _page_cards(
    page_id: str,
    edited: str,
    blocks: Optional[List[dict]],
    client: NotionClient,
    tree: BlockSource,
    media: MediaCollector,
    data: PageData,
    result: SyncResult,
    read_before: bool = False,
) -> None:
    """Read the cards written as text in a page (see pagecards.py).

    ``blocks`` is None when the page itself is unchanged: its cards are
    then reused, unless one is missing from the cache or has to be checked
    again, in which case the page's blocks are read.
    """
    if not result.config.get("page_cards", True):
        return
    marker = PAGE_CARDS + page_id
    result.db_pages[marker] = page_id
    table = TableData(title="", db_id=marker, raw_title="")
    url = "https://www.notion.so/" + page_id
    if blocks is None:
        now = time.time()
        listed = [
            rid
            for rid, state in result.previous.rows.items()
            if state.db_id == marker and state.page_id == page_id
        ]
        if listed and all(
            rid in result.known
            and is_unchanged(result.known[rid], edited, rid, now)
            for rid in listed
        ):
            for rid in listed:
                result.fetched[rid] = replace(result.known[rid])
                table.rows.append(
                    RowCard(rid, "", "", url=url, unchanged=True)
                )
            data.tables.append(table)
            return
        if not listed and read_before:
            return  # read before, and it held no card
        try:
            blocks = tree.children(page_id)
        except NotionError as e:
            _fail(
                result,
                f"Page {data.title}: {e}",
                page_id=page_id,
                problem=f"Page '{data.title}': {e.reason()}",
            )
            return
    seen: Set[str] = set()
    for card in parse_page(blocks):
        if card.source_id in seen:
            continue
        seen.add(card.source_id)
        recorder = _RecordingTree(tree)
        strike = StrikeTags(recorder)
        fetched_at = time.time()
        try:
            row = _render_page_card(card, url, media, strike, result)
        except NotionError as e:
            result.protected.add(card.source_id)
            _fail(
                result,
                f"Card in '{data.title}': {e}",
                page_id=page_id,
                problem=f"Card in '{data.title}': {e.reason()}",
            )
            continue
        if row is None:
            continue
        row.tags = [TAG_PREFIX + t for t in clean_tags(strike.tags)]
        result.fetched[card.source_id] = RowState(
            edited,
            fetched_at,
            "",
            page_id,
            marker,
            1 if recorder.volatile else 0,
        )
        table.rows.append(row)
    if table.rows:
        data.tables.append(table)


def _render_page_card(
    card: PageCard,
    url: str,
    media: MediaCollector,
    tree: StrikeTags,
    result: SyncResult,
) -> Optional[RowCard]:
    """Render a found card; None if nothing is left of it."""
    title_rts = tree.strip_rich(card.title)
    title = rt_text(title_rts).strip()
    if card.origin == "cloze":
        front = rich_text_to_html(title_rts).strip()
        if not has_cloze(title):
            return None
        return RowCard(card.source_id, front, "", url=url, kind="cloze")
    if not title:
        return None
    answer = tree.strip_blocks(card.blocks)
    kind, back, hint, lines, overlap = _render_blocks(
        answer, title, _page_card_type(card, answer), media, tree, result
    )
    if kind == "cloze" and has_cloze(title):
        front = rich_text_to_html(title_rts).strip()
    elif kind == "basic":
        front = html.escape(title, quote=False)
    else:
        front = title
    return RowCard(
        card.source_id,
        front,
        back,
        url=url,
        context_html=hint,
        kind=kind,
        lines=lines,
        overlap=overlap,
    )


def _collect_page_children(
    page_id: str,
    client: NotionClient,
    tree: BlockSource,
    media: MediaCollector,
    data: PageData,
    result: SyncResult,
    blocks: Optional[list] = None,
) -> None:
    if blocks is None:
        blocks = tree.children(page_id)
    databases = []
    for block in blocks:
        if block.get("type") == "child_database":
            databases.append(block)
        elif block.get("type") == "column_list" and block.get("has_children"):
            for col in tree.children(block["id"]):
                if col.get("type") != "column" or not col.get("has_children"):
                    continue
                databases += [
                    inner
                    for inner in tree.children(col["id"])
                    if inner.get("type") == "child_database"
                ]
    for block in databases:
        table = _fetch_table(
            client, tree, block, media, data.deck_name, result, page_id
        )
        if table is not None:
            data.tables.append(table)


class _RecordingTree:
    """Passes children() through and notes content that can change in
    Notion without bumping the row's last_edited_time.
    """

    def __init__(self, tree: Any) -> None:
        self.tree = tree
        self.volatile = False

    def children(self, block_id: str) -> List[dict]:
        blocks = self.tree.children(block_id)
        if not self.volatile:
            self.volatile = any(_is_volatile(b) for b in blocks)
        return blocks


# mentions whose text is another object's current name
_VOLATILE_MENTIONS = ("page", "database", "user")


def _has_live_mention(rich_text: Any) -> bool:
    if not isinstance(rich_text, list):
        return False
    for rt in rich_text:
        if isinstance(rt, dict) and rt.get("type") == "mention":
            if (rt.get("mention") or {}).get("type") in _VOLATILE_MENTIONS:
                return True
    return False


def _is_volatile(block: dict) -> bool:
    btype = block.get("type") or ""
    body = block.get(btype) or {}
    if not isinstance(body, dict):
        return False
    # a copy of a synced block: the original lives (and is edited) elsewhere
    if btype == "synced_block" and body.get("synced_from"):
        return True
    # a page mention shows the other page's current title; rich text sits
    # in the block's text, its caption (media, bookmarks) and table cells
    if _has_live_mention(body.get("rich_text")) or _has_live_mention(
        body.get("caption")
    ):
        return True
    return any(_has_live_mention(cell) for cell in body.get("cells") or [])


def _resolve_table(
    client: NotionClient, db_id: str, block_title: str
) -> Tuple[str, str]:
    """Look up a database's data source id and title."""
    db = client.get_database(db_id)
    ds = data_source_of(db) or {}
    title = (
        plain_text(db.get("title")).strip()
        or ds.get("name", "").strip()
        or block_title
        or "Table"
    )
    return ds.get("id") or db_id, title


def _fetch_table(
    client: NotionClient,
    tree: BlockSource,
    db_block: dict,
    media: MediaCollector,
    parent_deck: str,
    result: SyncResult,
    page_id: str = "",
) -> Optional[TableData]:
    db_id = db_block.get("id", "")
    block_title = str(
        (db_block.get("child_database") or {}).get("title") or ""
    ).strip()
    if page_id:
        result.db_pages[db_id] = page_id
    cached_ds = result.db_cache.get(db_id, "")
    where = f" on '{parent_deck}'" if parent_deck else ""
    if cached_ds and block_title:
        # the page block already has the title: no database lookup needed
        ds_id, title = cached_ds, block_title
    else:
        cached_ds = ""
        try:
            ds_id, title = _resolve_table(client, db_id, block_title)
        except NotionError as e:
            _fail(
                result,
                f"Table {block_title or db_id}: {e}",
                page_id,
                db_id,
                f"Table '{block_title or db_id}'{where}: {e.reason()}",
            )
            return None
    table = TableData(title=deck_safe(title), db_id=db_id, raw_title=title)
    result.progress(title)
    try:
        # one cheap metadata query per 100 rows; every row carries its
        # built-in last_edited_time, no table property is needed
        rows = client.query_data_source(ds_id)
    except NotionError as e:
        error = e
        retried: Optional[List[dict]] = None
        if cached_ds and not getattr(e, "transient", False):
            # Notion rejected the remembered data source (deleted or
            # replaced): look the database up again, once
            result.dbs_drop.add(db_id)
            try:
                fresh_ds, title = _resolve_table(client, db_id, block_title)
                table.title, table.raw_title = deck_safe(title), title
                if fresh_ds != ds_id:
                    ds_id = fresh_ds
                    retried = client.query_data_source(ds_id)
            except NotionError as again:
                error = again
        if retried is None:
            _fail(
                result,
                f"Table '{title}': {error}",
                page_id,
                db_id,
                f"Table '{title}'{where}: {error.reason()}",
            )
            return table
        rows = retried
    result.dbs_out[db_id] = ds_id

    now = time.time()
    for index, row in enumerate(rows, 1):
        title_rich = title_rich_text(row)
        front = plain_text(title_rich).strip()
        live_title = _has_live_mention(title_rich)
        if not front and not bool(
            result.config.get("include_empty_cards", True)
        ):
            result.warnings.append(
                f"Suspended empty row in table '{title}' (no title): skipped"
            )
            continue
        row_id = row["id"].replace("-", "")
        edited = str(row.get("last_edited_time") or "")
        columns = row_columns(row, result.config)
        if columns.sync is False:
            # unchecked: no card and no download. Only where the row came
            # from is kept; its content is read when it is checked again
            result.fetched[row_id] = RowState("", 0.0, "", page_id, db_id)
            table.rows.append(
                RowCard(
                    source_id=row_id,
                    front=front,
                    back_html="",
                    url=row.get("url", ""),
                    unchecked=True,
                    columns=columns,
                )
            )
            continue
        cached = result.known.get(row_id)
        if is_unchanged(cached, edited, row_id, now):
            assert cached is not None
            result.fetched[row_id] = replace(
                cached, page_id=page_id, db_id=db_id
            )
            table.rows.append(
                RowCard(
                    source_id=row_id,
                    front=front,
                    back_html="",
                    url=row.get("url", ""),
                    unchanged=True,
                    columns=columns,
                )
            )
            continue
        result.progress(f"{title} {index}/{len(rows)}")
        fetched_at = time.time()
        recorder = _RecordingTree(tree)
        struck = StrikeTags(recorder)
        try:
            kind, back, context, lines, overlap = _render_row(
                row["id"], front, columns.card_type, media, struck, result
            )
        except NotionError as e:
            # still listed in Notion: keep its note, retry next sync
            result.protected.add(row_id)
            _fail(
                result,
                f"Row '{front}' in '{title}': {e}",
                page_id,
                db_id,
                f"Row '{front}' in '{title}': {e.reason()}",
            )
            continue
        result.fetched[row_id] = RowState(
            edited,
            fetched_at,
            "",
            page_id,
            db_id,
            1 if recorder.volatile or live_title else 0,
        )
        if kind == "basic" or (kind == "cloze" and has_cloze(front)):
            # the title is the field itself: Anki reads fields as HTML
            front = html.escape(front, quote=False)
        table.rows.append(
            RowCard(
                source_id=row_id,
                front=front,
                back_html=back,
                context_html=context,
                url=row.get("url", ""),
                tags=[TAG_PREFIX + t for t in clean_tags(struck.tags)],
                columns=columns,
                kind=kind,
                lines=lines,
                overlap=overlap,
            )
        )
    return table


@dataclass(frozen=True)
class OverlapSpec:
    """Options of an ``Overlapping`` Card type, e.g. ``Overlapping 2/1 x2``.

    ``before``/``after`` are how many previous/following clozes are shown as
    cues (``before`` None = every previous one, none after: the plain
    behaviour); ``group`` is how many lines make one cloze.
    """

    before: Optional[int] = None
    after: int = 0
    group: int = 1

    @property
    def window(self) -> bool:
        """Tell whether only a window of lines is shown."""
        return self.before is not None


OVERLAP_MAX = 9  # largest before/after count
OVERLAP_MAX_GROUP = 20  # largest lines per cloze
OVERLAP_SYNTAX = (
    "Overlapping, Overlapping 2/1, Overlapping x2 or Overlapping 2/1 x2"
)
_OVERLAP_SPEC = re.compile(
    r"overlapping(?:\s+(\d+)\s*/\s*(\d+))?(?:\s+x\s*(\d+))?"
)


def parse_overlap(card_type: str) -> Optional[OverlapSpec]:
    """Parse a lowercased Card type that starts with ``overlapping``.

    ``overlapping``: the plain spec. ``overlapping B/A``: B previous and A
    following clozes shown. ``xN`` (after B/A or alone): N lines per cloze.
    None if the options are invalid or out of range.
    """
    m = _OVERLAP_SPEC.fullmatch(card_type.strip())
    if m is None:
        return None
    before = int(m.group(1)) if m.group(1) is not None else None
    after = int(m.group(2)) if m.group(2) is not None else 0
    group = int(m.group(3)) if m.group(3) is not None else 1
    if (before or 0) > OVERLAP_MAX or after > OVERLAP_MAX:
        return None
    if not 1 <= group <= OVERLAP_MAX_GROUP:
        return None
    return OverlapSpec(before, after, group)


HINT_HEADING = "hint"


def split_hint(blocks: List[dict]) -> Tuple[List[dict], List[dict]]:
    """Split a page into (card blocks, hint blocks).

    A ``#### Hint`` heading starts the hint; it runs up to the next divider
    (``---``), which is dropped, or to the end of the page. What comes
    before the heading and after the divider stays on the card.
    """
    for start, block in enumerate(blocks):
        if block.get("type") != "heading_4":
            continue
        text = plain_text((block.get("heading_4") or {}).get("rich_text"))
        if text.strip().lower() != HINT_HEADING:
            continue
        rest = blocks[start + 1 :]
        end = next(
            (i for i, b in enumerate(rest) if b.get("type") == "divider"),
            None,
        )
        if end is None:
            return blocks[:start], rest
        return blocks[:start] + rest[end + 1 :], rest[:end]
    return blocks, []


def _render_row(
    row_id: str,
    title: str,
    card_type: str,
    media: MediaCollector,
    tree: Any,
    result: SyncResult,
) -> Tuple[str, str, str, List[str], Optional[OverlapSpec]]:
    """Download and render a row: (kind, page HTML, hint HTML, lines of an
    overlapping cloze, its options). The page is read once; the "Card type"
    column decides the kind, otherwise a cloze marker in the title or page
    makes it a cloze.
    """
    return _render_blocks(
        tree.children(row_id), title, card_type, media, tree, result
    )


def _render_blocks(
    all_blocks: List[dict],
    title: str,
    card_type: str,
    media: MediaCollector,
    tree: Any,
    result: SyncResult,
) -> Tuple[str, str, str, List[str], Optional[OverlapSpec]]:
    """Render the blocks of a row or page card (see ``_render_row``)."""
    blocks, hint_blocks = split_hint(all_blocks)
    hint = ""
    if hint_blocks:
        body = render_blocks(hint_blocks, media, tree)
        if body:
            hint = (
                '<details class="ns-context"><summary>Hint</summary>'
                f"{body}</details>"
            )
    if card_type.split(None, 1)[:1] == ["overlapping"]:
        spec = parse_overlap(card_type)
        if spec is None:
            result.warnings.append(
                f"Row '{title}': Card type '{card_type}' is not understood "
                f"(use {OVERLAP_SYNTAX}); used plain Overlapping"
            )
            spec = OverlapSpec()
        lines = render_lines(blocks, media, tree)
        if lines:
            options = None if spec == OverlapSpec() else spec
            return "overlapping", "\n".join(lines), hint, lines, options
        result.warnings.append(
            f"Row '{title}': Card type is Overlapping but the page has no "
            "line; made a basic card"
        )
        return "basic", "", hint, [], None
    back = render_blocks(blocks, media, tree)
    if card_type == "basic":
        return "basic", back, hint, [], None
    if has_cloze(title) or has_cloze(back):
        return "cloze", back, hint, [], None
    if card_type == "cloze":
        result.warnings.append(
            f"Row '{title}': Card type is Cloze but no {{{{c1::...}}}} "
            "was found; made a basic card"
        )
    return "basic", back, hint, [], None


def bare_id(notion_id: str) -> str:
    """Return a Notion id without dashes, in lowercase."""
    return str(notion_id or "").replace("-", "").lower()


def _check_unlisted(client: NotionClient, result: SyncResult) -> None:
    """Look up synced notes that no table listed and whose origin is unknown.

    Only runs when part of Notion could not be read: such a note may belong
    to the part that failed, so it would be kept. Notion answers for the
    row itself: a row in the trash is gone for certain (its note can go);
    a row that exists keeps its note
    and, when its table belongs to a configured page, the origin is
    remembered so the next sync doesn't need to ask again. Any error (the
    row may just not be shared) keeps the note.
    """
    listed = {
        row.source_id
        for page in result.pages
        for table in page.tables
        for row in table.rows
    } | result.protected
    pending = sorted(
        s for s in result.sources - listed if _origin(s, result) is None
    )
    if not pending:
        return
    # database id -> (configured page, database id as the sync records it)
    tables: Dict[str, Tuple[str, str]] = {}
    for page_id, entry in result.previous.pages.items():
        if page_id in result.page_ids:
            for db_id, _title in entry[2]:
                tables[bare_id(db_id)] = (page_id, db_id)
    for db_id, page_id in result.db_pages.items():
        tables[bare_id(db_id)] = (page_id, db_id)
    now = time.time()
    asked = {**result.previous.checked, **result.checked}
    # never asked first, then the longest ago; rows asked within the last
    # day wait, so the same few failing rows can't use up every sync
    due = sorted(
        (
            s
            for s in pending
            if now - asked.get(s, 0.0) >= ORIGIN_RECHECK_SECONDS
        ),
        key=lambda s: (asked.get(s, 0.0), s),
    )
    batch = due[:MAX_ORIGIN_CHECKS]
    for source_id in batch:
        result.progress("checking unlisted rows")
        result.checked[source_id] = now  # whatever the answer: asked today
        try:
            row = client.get_page(source_id)
        except NotionError:
            continue  # not shared, or no answer: keep the note
        if row.get("in_trash") or row.get("archived"):
            result.confirmed_gone.add(source_id)
            continue
        result.protected.add(source_id)  # it exists: keep its note
        parent = row.get("parent") or {}
        known = tables.get(bare_id(parent.get("database_id") or ""))
        if known is not None:
            result.recovered[source_id] = RowState(
                "", 0.0, "", known[0], known[1]
            )
    if len(pending) > len(batch):
        result.warnings.append(
            f"{len(pending) - len(batch)} card(s) of unknown origin "
            "were kept without being checked this time; "
            "they are checked on the next syncs"
        )


def _origin(source_id: str, result: SyncResult) -> Optional[RowState]:
    """Where a note's row was last seen, if known."""
    origin = result.previous.rows.get(source_id) or result.recovered.get(
        source_id
    )
    if origin is None or not origin.page_id:
        return None
    return origin


def _download_media(
    client: NotionClient,
    media: MediaCollector,
    media_dir: str,
    progress: Progress = _no_progress,
) -> Tuple[List[Tuple[str, str]], Set[str]]:
    """Download every collected file.

    Returns (downloaded (path, fname) pairs, names that failed).
    """
    out: List[Tuple[str, str]] = []
    failed: Set[str] = set()
    total = len(media.files)
    for index, (fname, url) in enumerate(media.files.items(), 1):
        progress(f"media {index}/{total}")
        ext = os.path.splitext(fname)[1].lower()
        path = os.path.join(media_dir, fname)
        try:
            blob = client.download_file(url)
        except Exception:  # noqa: BLE001 - media is best-effort only
            failed.add(fname)
            continue
        if not ext:
            ext = _sniff_ext(blob)
            path += ext
        with open(path, "wb") as f:
            f.write(blob)
        out.append(
            (path, fname + (ext if not os.path.splitext(fname)[1] else ""))
        )
    return out, failed


_PLACEHOLDER = re.compile(r"@@NSMEDIA:([A-Za-z0-9_.\-]+)@@")


def _forget_rows_with_failed_media(result: SyncResult) -> None:
    """Write rows whose media failed, but don't cache them (their text
    is current), but not cached, so the next sync downloads it again.

    This does not make the sync incomplete: which rows exist in Notion is
    known, so removing deleted rows stays safe.
    """
    if not result.failed_media:
        return
    names = sorted(result.failed_media)
    for page in result.pages:
        for table in page.tables:
            for row in table.rows:
                if (
                    row.unchanged
                    or not set(
                        _PLACEHOLDER.findall(row.back_html + row.context_html)
                    )
                    & result.failed_media
                ):
                    continue
                _forget(result, row.source_id)
    shown = ", ".join(names[:5]) + (
        f" and {len(names) - 5} more" if len(names) > 5 else ""
    )
    result.warnings.append(
        f"Could not download {len(names)} media file(s), "
        f"will retry next sync: {shown}"
    )


def _forget(result: SyncResult, source_id: str) -> None:
    """Keep only where the row came from: its content is fetched next sync."""
    state = result.fetched.get(source_id)
    if state is not None:
        result.fetched[source_id] = RowState(
            "", 0.0, "", state.page_id, state.db_id
        )


def _sniff_ext(blob: bytes) -> str:
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if blob[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return ".webp"
    if blob[:4] == b"OggS":
        return ".ogg"
    if blob[:3] == b"ID3" or blob[:2] == b"\xff\xfb":
        return ".mp3"
    if blob[:5] == b"%PDF-":
        return ".pdf"
    if blob[:2] == b"PK":
        return ".zip"
    return ".bin"


# --------------------------------------------------------------------- #
# Phase 2: write to Anki collection
# --------------------------------------------------------------------- #

MODEL_CSS = (
    ".card { font-family: arial; font-size: 20px; line-height: 1.5; "
    "text-align: center; color: black; background-color: white; }"
)


def _no_change() -> None:
    pass


def card_css(cfg: Optional[dict]) -> str:
    """CSS for a note type the add-on creates: the chosen style preset.

    "Custom" (or an unknown name) creates it with the classic style.
    """
    from .styles import DEFAULT_PRESET, css_for

    preset = str((cfg or {}).get("card_style") or DEFAULT_PRESET)
    return css_for(preset) or MODEL_CSS


def _get_or_create_model(
    col,
    before_change: Callable[[], None] = _no_change,
    css: str = MODEL_CSS,
) -> dict:
    """Find or build the 'Notion Sync Card' note type.

    An existing note type is left as it is: its CSS is only written when
    the user picks a card style in the settings (see styles.apply_preset).
    """
    model = find_model(col)
    if model is not None:
        return model

    before_change()
    model = col.models.new(MODEL_NAME)
    fieldnames = ("Front", "Back", "Source")
    existing = {f["name"] for f in model["flds"]}
    for fname in fieldnames:
        if fname not in existing:
            col.models.add_field(model, col.models.new_field(fname))
    template = {
        "name": "Notion Sync Card",
        "ord": 0,
        "qfmt": "{{Front}}",
        "afmt": "{{Front}}<hr id=answer>{{Back}}",
    }
    col.models.add_template(model, template)
    model["css"] = css
    col.models.add_dict(model)
    refreshed = col.models.by_name(MODEL_NAME)
    return refreshed if refreshed is not None else model


CLOZE_FRONT_STYLE = (
    "<style>"
    ".ns-q .ns-overlap .ns-line:has(.cloze) ~ .ns-line "
    "{ display: none; }"
    "</style>"
)


def _get_or_create_cloze_model(
    col,
    before_change: Callable[[], None] = _no_change,
    css: str = MODEL_CSS,
) -> dict:
    """Find or build the 'Notion Sync Cloze' note type (Text, Back Extra,
    Source). Like the basic type, an existing one is left as it is.
    """
    model = find_model(col, "cloze")
    if model is not None:
        return model

    before_change()
    model = col.models.new(CLOZE_MODEL_NAME)
    model["type"] = 1  # anki.models.MODEL_CLOZE
    existing = {f["name"] for f in model["flds"]}
    for fname in ("Text", "Back Extra", "Source"):
        if fname not in existing:
            col.models.add_field(model, col.models.new_field(fname))
    col.models.add_template(
        model,
        {
            "name": "Cloze",
            "ord": 0,
            "qfmt": '<div class="ns-q">{{cloze:Text}}</div>'
            + CLOZE_FRONT_STYLE,
            "afmt": '<div class="ns-a">{{cloze:Text}}</div>'
            "{{#Back Extra}}<hr id=answer>{{Back Extra}}{{/Back Extra}}",
        },
    )
    model["css"] = css
    col.models.add_dict(model)
    refreshed = col.models.by_name(CLOZE_MODEL_NAME)
    return refreshed if refreshed is not None else model


def _guard_cloze(line: str) -> str:
    """Keep ``}}`` and ``::`` of a line from ending or splitting its cloze.

    A line holding a cloze marker of its own is left as written: its
    markers are meant (nested clozes), so the line is wrapped as it is.
    """
    if has_cloze(line):
        return line
    line = line.replace("}}", "}&#125;").replace("::", ":&#58;")
    return line[:-1] + "&#125;" if line.endswith("}") else line


def _window_style(spec: OverlapSpec) -> str:
    """Build the question-side CSS of a windowed overlapping cloze.

    Every line is
    hidden except the asked one, ``before`` previous and ``after`` following
    ones. It lives in the note's own field because an existing note type
    keeps its old template.
    """
    base = ".ns-q .ns-window .ns-line"
    active = base + ":has(.cloze)"
    show = " { display: block; }"
    rules = [base + " { display: none; }", active + show]
    for d in range(1, (spec.before or 0) + 1):
        between = " + .ns-line" * (d - 1)
        rules.append(f"{base}:has(+ .ns-line{between}:has(.cloze)){show}")
    for d in range(1, spec.after + 1):
        rules.append(f"{active}{' + .ns-line' * d}{show}")
    return "<style>" + "".join(rules) + "</style>"


def _row_fields(row: RowCard, imported_names: Dict[str, str]) -> List[str]:
    """Return a row's [Front/Text, Back/Back Extra], by its kind."""
    title = row.front
    back = MediaCollector.resolve(row.back_html, imported_names)
    shown = f'<div class="ns-title">{html.escape(title, quote=False)}</div>'
    if row.kind == "overlapping":
        spec = row.overlap or OverlapSpec()
        guarded = [
            _guard_cloze(MediaCollector.resolve(line, imported_names))
            for line in row.lines
        ]
        size = spec.group
        groups = [
            "<br>".join(guarded[i : i + size])
            for i in range(0, len(guarded), size)
        ]
        lines = [
            '<div class="ns-line">{{c%d::%s}}</div>' % (i, group)
            for i, group in enumerate(groups, 1)
        ]
        wrap = "ns-window" if spec.window else "ns-overlap"
        style = _window_style(spec) if spec.window else ""
        fields = [
            f'{style}{shown}<div class="{wrap}">{"".join(lines)}</div>',
            "",
        ]
    elif row.kind == "cloze" and has_cloze(title):
        fields = [title, back]
    elif row.kind == "cloze":
        fields = [shown + back, ""]
    else:
        fields = [title, back]
    fields[1] += _resolved(row.context_html, imported_names)
    return [unicodedata.normalize("NFC", f) for f in fields]


def _field_plan(
    model: dict, own_ids: Set[int], row: RowCard
) -> Optional[Dict[int, str]]:
    """Fields of a note of ``model`` besides the first two: position ->
    text. The Source gets the row id; in a chosen note type any other field
    named like a Notion column gets that column's text. None if the type
    has no usable Source field.
    """
    if int(model["id"]) in own_ids:
        return {SOURCE: row.source_id}
    index = _source_index(model)
    if index is None:
        return None
    plan = {index: row.source_id}
    props = {
        k.strip().casefold(): v
        for k, v in (row.columns.props if row.columns else {}).items()
    }
    for position, fld in enumerate(model["flds"]):
        name = str(fld.get("name", "")).strip().casefold()
        if position > BACK and position != index and name in props:
            plan[position] = unicodedata.normalize(
                "NFC", html.escape(props[name], quote=False)
            )
    return plan


def _plan_stale(note, plan: Dict[int, str]) -> bool:
    """Tell whether a note holds something else than the plan says."""
    return any(
        i < len(note.fields) and note.fields[i] != value
        for i, value in plan.items()
    )


def _set_fields(note, front: str, back: str, plan: Dict[int, str]) -> None:
    note.fields[FRONT] = front
    note.fields[BACK] = back
    for i, value in plan.items():
        note.fields[i] = value


class _UndoStep:
    """One "Notion Sync" undo entry, created only once something changes.

    Anki keeps about 30 undo steps and every note change is one, so changes
    are folded into the entry as they go: otherwise a sync with many changes
    pushes the entry out of the queue and it can no longer be merged.
    """

    MERGE_EVERY = 10  # begin() calls; each covers at most 2-3 backend ops

    def __init__(self, col, name: Optional[str]) -> None:
        self.col = col
        self.name = name
        self.start: Optional[int] = None
        self.pending = 0

    def begin(self) -> None:
        """Call before every change."""
        if not self.name:
            return
        if self.start is None:
            self.start = self.col.add_custom_undo_entry(self.name)
            return
        self.pending += 1
        if self.pending >= self.MERGE_EVERY:
            self.col.merge_undo_entries(self.start)
            self.pending = 0

    def finish(self) -> Any:
        if self.start is None:
            return None
        return self.col.merge_undo_entries(self.start)


def _media_present(col) -> Tuple[str, Set[str]]:
    """Return the media folder and the exact names of its files."""
    try:
        folder = col.media.dir()
        return folder, set(os.listdir(folder))
    except (OSError, AttributeError):
        return "", set()


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.getsize(a) == os.path.getsize(b) and filecmp.cmp(
            a, b, shallow=False
        )
    except OSError:
        return False


def _stage_media(
    result: SyncResult,
    media_folder: str,
    should_stop: Optional[Callable[[], bool]] = None,
) -> None:
    """Copy downloaded files that are new into the media folder.

    Done by the fetch step, which does not hold the collection; the write
    step then finds each file under its name with the same bytes and skips
    it. A name that is already taken is left to Anki's ``add_file``, and a
    file that can't be copied is simply imported by the write step.
    """
    if not media_folder or not result.media_files:
        return
    try:
        taken = {n.casefold() for n in os.listdir(media_folder)}
    except OSError:
        return
    for path, fname in result.media_files:
        if should_stop is not None and should_stop():
            return
        name = os.path.split(fname)[-1]
        target = os.path.join(media_folder, name)
        if name.casefold() in taken or os.path.exists(target):
            continue
        tmp = ""
        try:
            fd, tmp = tempfile.mkstemp(
                prefix=".ns-stage-", suffix=".tmp", dir=media_folder
            )
            with os.fdopen(fd, "wb") as out, open(path, "rb") as src:
                shutil.copyfileobj(src, out)
            os.replace(tmp, target)
            tmp = ""
            taken.add(name.casefold())
        except OSError:
            pass
        finally:
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def _import_media(col, result: SyncResult) -> Dict[str, str]:
    """Import downloaded files: Notion file name -> name in the collection.

    A re-downloaded file already in the media folder under exactly its name
    and with the same bytes is not handed to Anki again: that is what Anki
    would conclude too, after hashing and rewriting it while the
    collection is held.
    """
    imported_names: Dict[str, str] = {}
    folder, present = (
        _media_present(col) if result.media_files else ("", set())
    )
    for path, fname in result.media_files:
        name = os.path.split(fname)[-1]
        if name in present and _same_file(path, os.path.join(folder, name)):
            imported = name
        else:
            imported = col.media.add_file(path)
        imported_names[os.path.split(fname)[-1]] = imported
        imported_names.setdefault(os.path.splitext(fname)[0], imported)
    # a file that failed to download now may still be there from a past sync
    for fname in result.failed_media:
        try:
            if fname not in imported_names and col.media.have(fname):
                imported_names[fname] = fname
        except Exception:  # noqa: BLE001
            pass
    return imported_names


def apply_sync(
    col, result: SyncResult, undo_name: Optional[str] = UNDO_NAME
) -> Dict[str, int]:
    """Write decks/media/notes to the collection and save the row cache.

    Runs inside a QueryOp (background thread); the Anki backend serialises
    collection access, so this does not block the UI. All note changes form
    one undo step named ``undo_name``, created only if something changes;
    its OpChanges end up in ``result.changes``.
    """
    undo = _UndoStep(col, undo_name)
    try:
        stats = _apply(col, result, undo)
    finally:
        result.changes = undo.finish()
        result.cleanup()
    if result.cache_path:
        _save_cache_quietly(result)
    return stats


def _save_cache_quietly(result: SyncResult) -> None:
    """Save the cache; failing is a warning, the notes are written.

    A cache file that can't be
    written (antivirus or OneDrive holding it, disk full) only costs extra
    downloads next time, so it is a warning, not a failed sync.
    """
    cache = _next_cache(result)
    for attempt in range(2):
        try:
            save_cache(result.cache_path, cache)
            return
        except OSError as e:
            if attempt:
                result.warnings.append(
                    f"Could not save the sync cache ({e}); "
                    "the cards are up to date, "
                    "the next sync just downloads more rows"
                )
            else:
                time.sleep(0.5)


def _apply(col, result: SyncResult, undo: _UndoStep) -> Dict[str, int]:
    # local import: keep the module cheap to import at startup
    from anki.notes import Note

    stats = {
        "added": 0,
        "updated": 0,
        "removed": 0,
        "unchanged": 0,
        "media": len(result.media_files),
    }
    _drop_legacy_state(col, undo.begin)
    model = _get_or_create_model(col, undo.begin, card_css(result.config))
    _remember_model(col, "basic", model, undo.begin)
    own: Dict[str, dict] = {}  # the cloze type, once a cloze row needs it
    own_ids = set(own_model_ids(col).values())
    cloze_now = find_model(col, "cloze")
    cloze_id = int(cloze_now["id"]) if cloze_now else None
    chosen_types = table_note_types(col)
    imported_names = _import_media(col, result)

    notes = synced_notes(col)
    seen = set(result.protected)
    handled: Set[str] = set()
    added: Set[str] = set()
    for page in result.pages:
        for table in page.tables:
            deck_id: Optional[int] = None
            chosen = _chosen_model(col, chosen_types, table, result)
            for row in table.rows:
                if row.source_id in handled:
                    continue  # the same row twice in one sync: written once
                handled.add(row.source_id)
                seen.add(row.source_id)
                entries = notes.get(row.source_id)
                entry = entries[0] if entries else None
                nid = entry.nid if entry else None
                current = entry.fp if entry else ""
                state = result.fetched.get(row.source_id)
                if row.unchecked:
                    if entry is not None:
                        stats[
                            (
                                "updated"
                                if _hold(col, entry, undo)
                                else "unchanged"
                            )
                        ] += 1
                    continue
                restored = False
                if entry is not None and _marked(entry.tags):
                    _release(col, entry.nid, undo)
                    stats["updated"] += 1
                    restored = True
                bump = 0 if restored else 1  # one change per note
                if row.unchanged:
                    # checked again here: the note may have been edited,
                    # undone or deleted while Notion was being read
                    if (
                        entry is not None
                        and state is not None
                        and current == state.fingerprint
                    ):
                        # tags come from the page: nothing to compare
                        stats["unchanged"] += bump
                    else:
                        _forget(result, row.source_id)
                    continue
                front, back = _row_fields(row, imported_names)
                written = fingerprint(front, back, tags=owned_key(row.tags))
                is_cloze = row.kind in ("cloze", "overlapping")
                target = chosen or model
                if is_cloze:
                    target = _cloze_model(col, own, undo, result)
                    own_ids.add(int(target["id"]))
                # A basic row keeps its note whatever (non-cloze) type it is
                # in: a changed choice only affects rows without a note.
                replacing = entry is not None and (
                    entry.mid != target["id"]
                    if is_cloze
                    else entry.mid == cloze_id
                )
                soft = is_cloze or target["id"] not in own_ids
                plan: Optional[Dict[int, str]] = None
                if nid is None or replacing:
                    plan = _field_plan(target, own_ids, row)
                elif entry is not None:
                    plan = _field_plan(
                        col.models.get(entry.mid) or model, own_ids, row
                    )
                if plan is None:  # unusable Source field: stay safe
                    result.warnings.append(
                        f"Row '{row.front}': its note type has no usable "
                        "Source field; skipped"
                    )
                    continue
                if nid is None or replacing:
                    undo.begin()
                    home = (
                        _deck_of(col, entry.nid, None)
                        if entry is not None
                        else None
                    )
                    if home is None and deck_id is None:
                        deck_id = get_or_create_deck(
                            col, _deck_name(page, table)
                        )
                    note = Note(col, target)
                    _set_fields(note, front, back, plan)
                    note.tags = list(row.tags)
                    if replacing:
                        assert entry is not None
                        old = col.get_note(entry.nid)
                        note.tags = _with_tags(old.tags, row.tags)
                    try:
                        col.add_note(note, home or deck_id)
                    except Exception as e:  # noqa: BLE001 - e.g. no cloze
                        if not soft:
                            raise
                        result.warnings.append(
                            f"Row '{row.front}': Anki refused the note "
                            f"({e}); the row is tried again next sync"
                        )
                        continue
                    if replacing:
                        assert entry is not None
                        undo.begin()
                        col.remove_notes([entry.nid])
                        notes[row.source_id] = [
                            SyncedNote(note.id, written, target["id"], "")
                        ] + (entries or [])[1:]
                        stats["updated"] += bump
                    else:
                        added.add(row.source_id)
                        stats["added"] += 1
                    nid = note.id
                elif current != written or (
                    len(plan) > 1 and _plan_stale(col.get_note(nid), plan)
                ):
                    undo.begin()
                    note = col.get_note(nid)
                    _set_fields(note, front, back, plan)
                    note.tags = _with_tags(note.tags, row.tags)
                    try:
                        col.update_note(note)
                    except Exception as e:  # noqa: BLE001 - e.g. no cloze
                        if not (
                            is_cloze
                            or (entry is not None and entry.mid not in own_ids)
                        ):
                            raise
                        result.warnings.append(
                            f"Row '{row.front}': Anki refused the note "
                            f"({e}); the row is tried again next sync"
                        )
                        continue
                    stats["updated"] += bump
                else:
                    if state is not None:
                        state.fingerprint = current
                    continue
                if state is not None:
                    # fingerprint of what Anki actually stored (normally
                    # what was written; read back in case it differs)
                    state.fingerprint = _stored_fingerprint(col.get_note(nid))
    stats["updated"] += _move_renamed(col, result, notes, undo)
    removed, gone = _remove_obsolete(col, notes, seen, result, undo.begin)
    stats["removed"] = removed
    result.sources_after = (set(notes) - gone) | added
    return stats


def _deck_name(page: "PageData", table: "TableData") -> str:
    """Full deck name of a table (the page's own cards: the page's deck)."""
    if table.db_id.startswith(PAGE_CARDS):
        return page.deck_name
    return f"{page.deck_name}::{table.title}"


def _chosen_model(
    col, chosen: Dict[str, int], table: TableData, result: SyncResult
) -> Optional[dict]:
    """Return the note type chosen for a table's new rows (None = default)."""
    mid = chosen.get(bare_id(table.db_id))
    if not mid:
        return None
    model = col.models.get(mid)
    if (
        model is None
        or model.get("type") != 0
        or len(model.get("flds") or []) < 3
        or _source_index(model) is None
    ):
        result.warnings.append(
            f"Table '{table.title}': the chosen note type can no longer be "
            "used (it needs 3 fields, one named Source); "
            "new rows use Notion Sync Card"
        )
        return None
    return model


def _resolved(html: str, imported_names: Dict[str, str]) -> str:
    """Fill in the media names of a rendered part (NFC, like Anki)."""
    return unicodedata.normalize(
        "NFC", MediaCollector.resolve(html, imported_names)
    )


def _cloze_model(
    col, own: Dict[str, dict], undo: _UndoStep, result: SyncResult
) -> dict:
    """Return the cloze note type, created (and remembered) on first use."""
    if "cloze" not in own:
        model = _get_or_create_cloze_model(
            col, undo.begin, card_css(result.config)
        )
        _remember_model(col, "cloze", model, undo.begin)
        own["cloze"] = model
    return own["cloze"]


def _deck_of(col, nid: int, fallback: Optional[int]) -> Optional[int]:
    """Return the deck of a note's first card (home deck if filtered)."""
    did = col.db.scalar(
        "select case when odid then odid else did end from cards "
        "where nid=? order by ord limit 1",
        nid,
    )
    return int(did) if did else fallback


def _stored_fingerprint(note) -> str:
    return fingerprint(
        note.fields[FRONT], note.fields[BACK], tags=owned_key(note.tags)
    )


def _with_tags(existing: Iterable[str], owned: Iterable[str]) -> List[str]:
    """Replace the owned tags among a note's tags; others stay."""
    drop = set(owned_tags(existing))
    return [t for t in existing if t not in drop] + list(owned)


def _marked(tags: str) -> bool:
    return UNCHECKED_TAG in tags.lower().split()


def _hold(col, entry: SyncedNote, undo: _UndoStep) -> bool:
    """Suspend an unchecked row's cards and mark the note, once.

    A note already marked is left alone, so cards the user unsuspended by
    hand stay unsuspended. True if something changed.
    """
    if _marked(entry.tags):
        return False
    undo.begin()
    col.sched.suspend_cards(col.card_ids_of_note(entry.nid))
    note = col.get_note(entry.nid)
    note.tags = list(note.tags) + [UNCHECKED_TAG]
    col.update_note(note)
    return True


def _release(col, nid: int, undo: _UndoStep) -> None:
    """Undo ``_hold`` for a row that is checked again (or lost the column).

    Only notes carrying the marker get here: cards suspended by hand never.
    """
    undo.begin()
    col.sched.unsuspend_cards(col.card_ids_of_note(nid))
    note = col.get_note(nid)
    note.tags = [t for t in note.tags if t.lower() != UNCHECKED_TAG]
    col.update_note(note)


def get_or_create_deck(col, name: str) -> int:
    """Return the id of deck ``name``, creating it if needed."""
    return col.decks.id(name, create=True)


def _move_renamed(
    col,
    result: SyncResult,
    notes: Dict[str, List[SyncedNote]],
    undo: _UndoStep,
) -> int:
    """Move the cards of a renamed (or moved) table to its new deck.

    The cache remembers the deck each table's cards were put in. For a
    table read cleanly whose deck name changed, the cards of its synced
    notes that still sit in the old deck follow; cards the user moved
    elsewhere, notes made by hand and the old deck itself stay as they are.
    Records the deck names in ``result.decks_out``; returns the number of
    notes moved.
    """
    old_names = result.previous.decks
    moved = 0
    for page in result.pages:
        for table in page.tables:
            db_id = table.db_id
            page_id = result.db_pages.get(db_id, "")
            if (
                not db_id
                or db_id in result.failed_dbs
                or (page_id and page_id in result.failed_pages)
            ):
                continue
            name = _deck_name(page, table)
            result.decks_out[db_id] = name
            old = old_names.get(db_id)
            if not old or old == name:
                continue
            old_id = col.decks.id_for_name(old)
            if old_id is None:
                continue
            nids = sorted(
                {
                    entries[0].nid
                    for row in table.rows
                    for entries in [notes.get(row.source_id)]
                    if entries
                }
            )
            by_note: List[List[int]] = []
            for nid in nids:
                cids = col.db.list(
                    "select id from cards where nid = ? and did = ?",
                    nid,
                    old_id,
                )
                if cids:
                    by_note.append(cids)
            if not by_note:
                continue
            undo.begin()
            new_id = get_or_create_deck(col, name)
            col.set_deck([c for cids in by_note for c in cids], new_id)
            moved += len(by_note)
    return moved


def _removal_blocked(source_id: str, result: SyncResult) -> bool:
    """Keep a note whose row may still exist in a part of Notion that could
    not be read in this sync.
    """
    if source_id in result.confirmed_gone:  # Notion says it's in the trash
        return False
    origin = _origin(source_id, result)
    if result.invalid_entries and (
        origin is None or origin.page_id not in result.page_ids
    ):
        # an invalid page entry may be a page that used to be configured
        # (e.g. its link got mangled): keep the cards that may be its own
        return True
    if not result.incomplete:
        return False
    if result.unscoped_failure:
        return True
    if origin is None:
        return True  # don't know where it came from
    return (
        origin.page_id in result.failed_pages
        or origin.db_id in result.failed_dbs
    )


def _remove_obsolete(
    col,
    notes: Dict[str, List[SyncedNote]],
    seen: Set[str],
    result: SyncResult,
    before_change: Callable[[], None] = _no_change,
) -> Tuple[int, Set[str]]:
    """Remove stale synced notes and duplicates: (notes removed, Sources
    left with no note). Notes not made by the add-on never appear in
    ``notes`` and are never touched.
    """
    to_remove: List[int] = []
    gone: Set[str] = set()
    for source_id, entries in notes.items():
        if source_id not in seen and not _removal_blocked(source_id, result):
            to_remove += [e.nid for e in entries]
            gone.add(source_id)
        else:
            # the same row added twice (e.g. on two devices before they
            # synced): keep the oldest note
            to_remove += [e.nid for e in entries[1:]]
    if to_remove:
        before_change()
        col.remove_notes(to_remove)
    return len(to_remove), gone


def _next_cache(result: SyncResult) -> Cache:
    """Build the cache after this sync.

    Rows kept although Notion didn't list them
    (their page/table could not be read) keep their old entry.
    """
    rows = dict(result.fetched)
    remaining = (
        result.sources_after if result.sources_after is not None else set(rows)
    )
    for key, state in list(result.previous.rows.items()) + list(
        result.recovered.items()
    ):
        if key not in rows and key in remaining:
            rows[key] = state
    pages = {
        k: v for k, v in result.previous.pages.items() if k in result.page_ids
    }
    pages.update(result.pages_out)
    dbs = {
        k: v
        for k, v in result.previous.dbs.items()
        if k not in result.dbs_drop
    }
    dbs.update(result.dbs_out)
    checked = dict(result.previous.checked)
    checked.update(result.checked)
    # remembered decks of tables not read now stay while their page does
    listed = {db for entry in pages.values() for db, _title in entry[2]}
    listed |= {
        db for db, pid in result.db_pages.items() if pid in result.page_ids
    }
    decks = {k: v for k, v in result.previous.decks.items() if k in listed}
    decks.update(result.decks_out)
    return Cache(
        rows=rows,
        pages=pages,
        dbs=dbs,
        problems=list(result.problems),
        checked={k: t for k, t in checked.items() if k in remaining},
        decks=decks,
        raw=result.previous.raw,
    )
