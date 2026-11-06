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

import hashlib
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
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .htmlify import MediaCollector, render_blocks
from .notion_api import (
    BlockTree,
    NotionClient,
    NotionError,
    data_source_of,
    page_title,
)

MODEL_NAME = "Notion Sync Card"
UNDO_NAME = "Notion Sync"

# The cache lives in a JSON file per profile in the add-on's user_files
# folder (kept across add-on upgrades, never sent to AnkiWeb):
#   rows:  row id -> RowState (see RowState.to_json)
#   pages: page id -> [last_edited_time, fetched at, [[database id, title]...]]
#   dbs:   database id -> data source id (never changes for a database)
# Bump RENDER_VERSION whenever the HTML produced for a row changes so every
# row is re-rendered once.
RENDER_VERSION = 2
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
    volatile: bool = False  # content can change without bumping `edited`

    def to_json(self) -> list:
        return [self.edited, round(self.fetched_at, 1), self.fingerprint,
                self.page_id, self.db_id, int(self.volatile)]

    @classmethod
    def from_json(cls, value: Any) -> Optional["RowState"]:
        if not isinstance(value, list) or len(value) != 6:
            return None
        try:
            return cls(str(value[0]), float(value[1]), str(value[2]),
                       str(value[3]), str(value[4]), bool(value[5]))
        except (TypeError, ValueError):
            return None


@dataclass
class Cache:
    rows: Dict[str, RowState] = field(default_factory=dict)
    pages: Dict[str, list] = field(default_factory=dict)
    dbs: Dict[str, str] = field(default_factory=dict)
    raw: str = ""  # file content as loaded: an identical cache isn't rewritten


@dataclass
class RowCard:
    source_id: str
    front: str
    back_html: str  # may contain @@NSMEDIA:*@@ placeholders
    url: str
    unchanged: bool = False  # content not fetched: note is already current


@dataclass
class TableData:
    title: str
    rows: List[RowCard] = field(default_factory=list)
    db_id: str = ""
    raw_title: str = ""  # Notion title before deck_safe()


@dataclass
class PageData:
    title: str
    deck_name: str
    tables: List[TableData] = field(default_factory=list)


@dataclass
class Prepared:
    """Output of ``prepare_sync``: the cache, checked against the collection."""

    known: Dict[str, RowState]
    structure: Dict[str, dict]
    previous: Cache


class SyncResult:
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
        self.page_ids: List[str] = []  # configured pages, canonical ids
        # set when part of Notion could not be read. Notes are then only
        # removed if the page and table they last came from were read fine,
        # so a network error can never delete cards.
        self.incomplete = False
        self.failures = 0
        self.failed_pages: Set[str] = set()
        self.failed_dbs: Set[str] = set()
        self.unscoped_failure = False  # a failure no page/table can be blamed for
        self.protected: Set[str] = set()  # rows listed in Notion but not readable
        self.changes: Any = None  # OpChanges of the undo step, None = no changes
        self.progress: Progress = _no_progress

    def cleanup(self) -> None:
        if self.media_dir:
            shutil.rmtree(self.media_dir, ignore_errors=True)


def _fail(result: SyncResult, message: str, page_id: str = "", db_id: str = "") -> None:
    result.incomplete = True
    result.failures += 1
    result.warnings.append(message)
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
        from aqt import mw  # type: ignore  # noqa: F401

        addon_dir = os.path.dirname(os.path.abspath(__file__))
        bases = (os.path.join(addon_dir, "user_files"), addon_dir,
                 os.getcwd(), os.path.expanduser("~"))
    except Exception:
        bases = (os.getcwd(), os.path.expanduser("~"))
    for base in bases:
        value = _dotenv_value(_read_dotenv(os.path.join(base, ".env")), "NOTION_API_TOKEN")
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
        return bare
    match = re.search(
        r"(?i)([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9a-f]{32})",
        raw,
    )
    if match:
        return match.group(1).replace("-", "").lower()
    # URL: take the slug after the last /, then the id is the trailing
    # 32-hex segment after the title words (e.g. .../p/Philo-Histoire-<32hex>).
    slug = raw.rstrip("/").split("/")[-1].split("?")[0]
    parts = slug.split("-")
    for take in range(1, min(4, len(parts)) + 1):
        candidate = "".join(parts[-take:]).replace("-", "")
        if re.fullmatch(r"[0-9a-fA-F]{32}", candidate):
            return candidate
    # fallback: last 32 hex characters of the whole string
    hex_only = re.sub(r"[^0-9a-fA-F]", "", raw)
    if len(hex_only) >= 32:
        return hex_only[-32:]
    raise NotionError(f"Cannot parse page id: {raw}")


def deck_safe(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip()
    name = name.replace("::", "/")          # protect path separator first
    name = re.sub(r"[\x00:\"]", " ", name)  # drop any other illegal chars
    return name or "Untitled"


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


def _fresh(cached_edited: str, fetched_at: float, edited: str, key: str,
           now: Optional[float]) -> bool:
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


def is_unchanged(cached: Optional[RowState], edited: str, key: str = "",
                 now: Optional[float] = None) -> bool:
    """True when the cached content is still what Notion has for this row."""
    if cached is None or cached.volatile:
        return False
    return _fresh(cached.edited, cached.fetched_at, edited, key, now)


# --------------------------------------------------------------------- #
# Cache file
# --------------------------------------------------------------------- #


def cache_path(addon_dir: str, profile: str) -> str:
    """One cache file per profile in the add-on's user_files folder."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", profile).strip("._")[:40] or "profile"
    digest = hashlib.sha1(profile.encode("utf-8")).hexdigest()[:8]
    return os.path.join(addon_dir, "user_files", f"cache-{safe}-{digest}.json")


def load_cache(path: str) -> Cache:
    """Read the cache file; a missing, corrupt or outdated file is no cache."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        state = json.loads(raw)
    except (OSError, ValueError):
        return Cache()
    if not isinstance(state, dict) or state.get("v") != RENDER_VERSION:
        return Cache()
    rows: Dict[str, RowState] = {}
    for key, value in (state.get("rows") or {}).items():
        row = RowState.from_json(value)
        if row is not None:
            rows[str(key)] = row
    pages = {str(k): v for k, v in (state.get("pages") or {}).items()
             if _valid_page_entry(v)}
    dbs = {str(k): str(v) for k, v in (state.get("dbs") or {}).items() if v}
    return Cache(rows=rows, pages=pages, dbs=dbs, raw=raw)


def _valid_page_entry(value: Any) -> bool:
    return (isinstance(value, list) and len(value) == 3
            and isinstance(value[0], str) and isinstance(value[1], (int, float))
            and isinstance(value[2], list)
            and all(isinstance(t, list) and len(t) == 2 for t in value[2]))


def _cache_text(cache: Cache) -> str:
    return json.dumps({"v": RENDER_VERSION,
                       "rows": {k: v.to_json() for k, v in sorted(cache.rows.items())},
                       "pages": dict(sorted(cache.pages.items())),
                       "dbs": dict(sorted(cache.dbs.items()))},
                      separators=(",", ":"), ensure_ascii=False)


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


def fingerprint(front: str, back: str) -> str:
    return hashlib.sha1(f"{front}\x1f{back}".encode("utf-8")).hexdigest()[:16]


def _field_indexes(model: dict) -> Dict[str, int]:
    return {fld.get("name"): index for index, fld in enumerate(model.get("flds", []))}


def synced_notes(col) -> Dict[str, List[Tuple[int, str]]]:
    """Source id -> [(note id, fingerprint of its Front/Back)], oldest first.

    One SQL query; notes without a Source (added by hand) are left out.
    """
    model = col.models.by_name(MODEL_NAME)
    if model is None:
        return {}
    index = _field_indexes(model)
    src, front, back = index.get("Source"), index.get("Front"), index.get("Back")
    if src is None or front is None or back is None:
        return {}
    out: Dict[str, List[Tuple[int, str]]] = {}
    sql = "select id, flds from notes where mid = ? order by id"
    for nid, flds in col.db.all(sql, model["id"]):
        parts = flds.split("\x1f")
        if max(src, front, back) >= len(parts) or not parts[src].strip():
            continue
        out.setdefault(parts[src].strip(), []).append(
            (nid, fingerprint(parts[front], parts[back])))
    return out


def trusted_rows(cache: Cache, notes: Dict[str, List[Tuple[int, str]]]) -> Dict[str, RowState]:
    """Cached rows whose note still holds exactly what the add-on wrote.

    Anything else (note deleted, edited in Anki, undone, changed by another
    device) is downloaded again and overwritten with Notion's content.
    """
    out: Dict[str, RowState] = {}
    for key, state in cache.rows.items():
        entries = notes.get(key)
        if entries and state.fingerprint and entries[0][1] == state.fingerprint:
            out[key] = state
    return out


def prepare_sync(col, path: str, full: bool = False) -> Prepared:
    """Load the cache and check it against the collection (background op)."""
    previous = load_cache(path)
    if full:  # Full Resync: trust nothing, but keep row origins for removal
        return Prepared(known={}, structure={}, previous=previous)
    known = trusted_rows(previous, synced_notes(col))
    structure = {"pages": dict(previous.pages), "dbs": dict(previous.dbs)}
    return Prepared(known=known, structure=structure, previous=previous)


def _drop_legacy_state(col, before_change: Callable[[], None]) -> None:
    """Older versions kept the cache in the collection config, which is
    sent to AnkiWeb on every sync: remove it once. Removing config is always
    undoable in Anki, so it joins the sync's own undo step."""
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
) -> SyncResult:
    """Read all configured pages. Rows in ``known`` that were not edited
    since they were fetched are returned as ``unchanged`` placeholders.
    ``structure`` (pages/dbs of the cache) skips re-reading unchanged
    pages and re-resolving data source ids. ``previous`` is the whole cache,
    used to scope note removal when part of Notion can't be read."""
    client = NotionClient(resolve_token(cfg))
    tree = BlockTree(client)
    media = MediaCollector()
    result = SyncResult()
    result.config = cfg
    result.known = dict(known or {})
    result.previous = previous or Cache()
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
            except NotionError as e:
                _fail(result, str(e))
                continue
            result.page_ids.append(page_id)
            try:
                _fetch_page(page_id, client, tree, media, result)
            except NotionError as e:
                _fail(result, str(e), page_id=page_id)

        result.media_files, result.failed_media = _download_media(
            client, media, result.media_dir, result.progress)
        _forget_rows_with_failed_media(result)
    except BaseException:
        result.cleanup()
        raise
    return result


def _fetch_page(
    page_id: str,
    client: NotionClient,
    tree: BlockTree,
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
            block = {"id": db_id, "type": "child_database",
                     "child_database": {"title": db_title}}
            table = _fetch_table(client, tree, block, media, data.deck_name, result, page_id)
            if table is not None:
                data.tables.append(table)
        if result.failures == failures:
            result.pages_out[page_id] = cached
        return

    fetched_at = time.time()
    _collect_page_children(page_id, client, tree, media, data, result,
                           blocks=tree.children(page_id))
    # a page with any failure keeps its previous cache entry: a table that
    # could not be read must not vanish from the cached table list
    if edited and result.failures == failures:
        result.pages_out[page_id] = [edited, fetched_at,
                                     [[t.db_id, t.raw_title] for t in data.tables]]


def _collect_page_children(
    page_id: str,
    client: NotionClient,
    tree: BlockTree,
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
                databases += [inner for inner in tree.children(col["id"])
                              if inner.get("type") == "child_database"]
    for block in databases:
        table = _fetch_table(client, tree, block, media, data.deck_name, result, page_id)
        if table is not None:
            data.tables.append(table)


class _RecordingTree:
    """Passes children() through and notes content that can change in
    Notion without bumping the row's last_edited_time."""

    def __init__(self, tree: Any) -> None:
        self.tree = tree
        self.volatile = False

    def children(self, block_id: str) -> List[dict]:
        blocks = self.tree.children(block_id)
        if not self.volatile:
            self.volatile = any(_is_volatile(b) for b in blocks)
        return blocks


def _is_volatile(block: dict) -> bool:
    btype = block.get("type") or ""
    body = block.get(btype) or {}
    if not isinstance(body, dict):
        return False
    # a copy of a synced block: the original lives (and is edited) elsewhere
    if btype == "synced_block" and body.get("synced_from"):
        return True
    # a page mention shows the other page's current title
    for rt in body.get("rich_text") or []:
        if rt.get("type") == "mention" and (rt.get("mention") or {}).get("type") == "page":
            return True
    return False


def _fetch_table(
    client: NotionClient,
    tree: BlockTree,
    db_block: dict,
    media: MediaCollector,
    parent_deck: str,
    result: SyncResult,
    page_id: str = "",
) -> Optional[TableData]:
    db_id = db_block.get("id", "")
    block_title = str((db_block.get("child_database") or {}).get("title") or "").strip()
    ds_id = result.db_cache.get(db_id, "")
    if ds_id and block_title:
        # data source ids never change; the page block already has the title
        title = block_title
    else:
        try:
            db = client.get_database(db_id)
        except NotionError as e:
            _fail(result, f"Table {block_title or db_id}: {e}", page_id, db_id)
            return None
        ds = data_source_of(db) or {}
        ds_id = ds.get("id") or db_id
        title = (
            "".join(t.get("plain_text", "") for t in db.get("title", [])).strip()
            or ds.get("name", "").strip()
            or block_title
            or "Table"
        )
    table = TableData(title=deck_safe(title), db_id=db_id, raw_title=title)
    result.progress(title)
    try:
        # one cheap metadata query per 100 rows; every row carries its
        # built-in last_edited_time, no table property is needed
        rows = client.query_data_source(ds_id)
    except NotionError as e:
        _fail(result, f"Table '{title}': {e}", page_id, db_id)
        return table
    result.dbs_out[db_id] = ds_id

    now = time.time()
    for index, row in enumerate(rows, 1):
        front = ""
        for prop in row.get("properties", {}).values():
            if prop.get("type") == "title":
                front = "".join(x.get("plain_text", "") for x in prop.get("title", [])).strip()
                break
        if not front and not bool(result.config.get("include_empty_cards", True)):
            result.warnings.append(f"Suspended empty row in table '{title}' (no title): skipped")
            continue
        row_id = row["id"].replace("-", "")
        edited = str(row.get("last_edited_time") or "")
        cached = result.known.get(row_id)
        if is_unchanged(cached, edited, row_id, now):
            assert cached is not None
            result.fetched[row_id] = replace(cached, page_id=page_id, db_id=db_id)
            table.rows.append(RowCard(source_id=row_id, front=front, back_html="",
                                      url=row.get("url", ""), unchanged=True))
            continue
        result.progress(f"{title} {index}/{len(rows)}")
        fetched_at = time.time()
        recorder = _RecordingTree(tree)
        try:
            back = render_blocks(recorder.children(row["id"]), media, recorder)
        except NotionError as e:
            # still listed in Notion: keep its note, retry next sync
            result.protected.add(row_id)
            _fail(result, f"Row '{front}' in '{title}': {e}", page_id, db_id)
            continue
        result.fetched[row_id] = RowState(edited, fetched_at, "", page_id, db_id,
                                          recorder.volatile)
        table.rows.append(
            RowCard(source_id=row_id, front=front, back_html=back, url=row.get("url", ""))
        )
    return table


def _download_media(
    client: NotionClient,
    media: MediaCollector,
    media_dir: str,
    progress: Progress = _no_progress,
) -> Tuple[List[Tuple[str, str]], Set[str]]:
    """Download every collected file: (downloaded (path, fname), failed fnames)."""
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
            ext = _sniff_ext(blob) or ".bin"
            path += ext
        with open(path, "wb") as f:
            f.write(blob)
        out.append((path, fname + (ext if not os.path.splitext(fname)[1] else "")))
    return out, failed


_PLACEHOLDER = re.compile(r"@@NSMEDIA:([A-Za-z0-9_.\-]+)@@")


def _forget_rows_with_failed_media(result: SyncResult) -> None:
    """A row whose media could not be downloaded is still written (its text
    is current), but not cached, so the next sync downloads it again.

    This does not make the sync incomplete: which rows exist in Notion is
    known, so removing deleted rows stays safe."""
    if not result.failed_media:
        return
    names = sorted(result.failed_media)
    for page in result.pages:
        for table in page.tables:
            for row in table.rows:
                if row.unchanged or not set(_PLACEHOLDER.findall(row.back_html)) & result.failed_media:
                    continue
                _forget(result, row.source_id)
    shown = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
    result.warnings.append(f"Could not download {len(names)} media file(s), "
                           f"will retry next sync: {shown}")


def _forget(result: SyncResult, source_id: str) -> None:
    """Keep only where the row came from: its content is fetched next sync."""
    state = result.fetched.get(source_id)
    if state is not None:
        result.fetched[source_id] = RowState("", 0.0, "", state.page_id, state.db_id)


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


def _get_or_create_model(col, before_change: Callable[[], None] = _no_change) -> dict:
    """Find or build the 'Notion Sync Card' note type."""
    model = col.models.by_name(MODEL_NAME)
    if model is not None:
        if model.get("css") != MODEL_CSS:
            before_change()
            model["css"] = MODEL_CSS
            save = getattr(col.models, "save", None)
            if save is not None:
                save(model)
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
    model["css"] = MODEL_CSS
    col.models.add_dict(model)
    refreshed = col.models.by_name(MODEL_NAME)
    return refreshed if refreshed is not None else model


class _UndoStep:
    """One "Notion Sync" undo entry, created only once something changes.

    Anki keeps about 30 undo steps and every note change is one, so changes
    are folded into the entry as they go: otherwise a sync with many changes
    pushes the entry out of the queue and it can no longer be merged."""

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


def _import_media(col, result: SyncResult) -> Dict[str, str]:
    """Import downloaded files: Notion file name -> name in the collection."""
    imported_names: Dict[str, str] = {}
    for path, fname in result.media_files:
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


def apply_sync(col, result: SyncResult, undo_name: Optional[str] = UNDO_NAME) -> Dict[str, int]:
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
        save_cache(result.cache_path, _next_cache(result, synced_notes(col)))
    return stats


def _apply(col, result: SyncResult, undo: _UndoStep) -> Dict[str, int]:
    from anki.notes import Note  # local import: keep module import cheap at startup

    stats = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0,
             "media": len(result.media_files)}
    _drop_legacy_state(col, undo.begin)
    model = _get_or_create_model(col, undo.begin)
    imported_names = _import_media(col, result)

    notes = synced_notes(col)
    seen = set(result.protected)
    for page in result.pages:
        for table in page.tables:
            deck_id: Optional[int] = None
            for row in table.rows:
                seen.add(row.source_id)
                entries = notes.get(row.source_id)
                nid, current = entries[0] if entries else (None, "")
                state = result.fetched.get(row.source_id)
                if row.unchanged:
                    # checked again here: the note may have been edited,
                    # undone or deleted while Notion was being read
                    if nid is not None and state is not None and current == state.fingerprint:
                        stats["unchanged"] += 1
                    else:
                        _forget(result, row.source_id)
                    continue
                front = unicodedata.normalize("NFC", row.front)
                back = unicodedata.normalize(
                    "NFC", MediaCollector.resolve(row.back_html, imported_names))
                if nid is None:
                    undo.begin()
                    if deck_id is None:
                        deck_id = get_or_create_deck(col, f"{page.deck_name}::{table.title}")
                    note = Note(col, model)
                    note["Front"] = front
                    note["Back"] = back
                    note["Source"] = row.source_id
                    col.add_note(note, deck_id)
                    nid = note.id
                    stats["added"] += 1
                elif current != fingerprint(front, back):
                    undo.begin()
                    note = col.get_note(nid)
                    note["Front"] = front
                    note["Back"] = back
                    col.update_note(note)
                    stats["updated"] += 1
                else:
                    if state is not None:
                        state.fingerprint = current
                    continue
                if state is not None:  # fingerprint of what was actually stored
                    stored = col.get_note(nid)
                    state.fingerprint = fingerprint(stored["Front"], stored["Back"])
    stats["removed"] = _remove_obsolete(col, notes, seen, result, undo.begin)
    return stats


def get_or_create_deck(col, name: str) -> int:
    return col.decks.id(name, create=True)


def _removal_blocked(source_id: str, result: SyncResult) -> bool:
    """Keep a note whose row may still exist in a part of Notion that could
    not be read in this sync."""
    if not result.incomplete:
        return False
    if result.unscoped_failure:
        return True
    origin = result.previous.rows.get(source_id)
    if origin is None or not origin.page_id:
        return True  # don't know where it came from
    return origin.page_id in result.failed_pages or origin.db_id in result.failed_dbs


def _remove_obsolete(
    col,
    notes: Dict[str, List[Tuple[int, str]]],
    seen: Set[str],
    result: SyncResult,
    before_change: Callable[[], None] = _no_change,
) -> int:
    """Remove stale synced notes and duplicates; notes without a Source
    (added by hand) never appear in ``notes`` and are never touched."""
    to_remove: List[int] = []
    for source_id, entries in notes.items():
        if source_id not in seen and not _removal_blocked(source_id, result):
            to_remove += [nid for nid, _ in entries]
        else:
            # the same row added twice (e.g. on two devices before they
            # synced): keep the oldest note
            to_remove += [nid for nid, _ in entries[1:]]
    if to_remove:
        before_change()
        col.remove_notes(to_remove)
    return len(to_remove)


def _next_cache(result: SyncResult, notes: Dict[str, List[Tuple[int, str]]]) -> Cache:
    """The cache after this sync. Rows kept although Notion didn't list them
    (their page/table could not be read) keep their old entry."""
    rows = dict(result.fetched)
    for key, state in result.previous.rows.items():
        if key not in rows and key in notes:
            rows[key] = state
    pages = {k: v for k, v in result.previous.pages.items() if k in result.page_ids}
    pages.update(result.pages_out)
    dbs = dict(result.previous.dbs)
    dbs.update(result.dbs_out)
    return Cache(rows=rows, pages=pages, dbs=dbs, raw=result.previous.raw)
