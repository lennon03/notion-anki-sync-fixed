# -*- coding: utf-8 -*-
"""Sync orchestration.

Phase 1 (network, background thread):
    walk the configured Notion pages -> build PageData structures
    (tables -> rows -> rendered HTML with media placeholders),
    download all referenced media into a temp dir.
    Rows whose Notion ``last_edited_time`` has not changed since the last
    sync are not re-downloaded (see ``is_unchanged``). The same goes for the
    list of tables on an unchanged page, and each table's data source id is
    remembered, so a sync with no edits only lists pages and table rows.

Phase 2 (collection, background QueryOp):
    ensure note type / decks exist, import media, add or update notes,
    remove obsolete ones, persist the row cache in the collection config.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from .htmlify import MediaCollector, render_blocks
from .notion_api import (
    BlockTree,
    NotionClient,
    NotionError,
    data_source_of,
    page_title,
)

MODEL_NAME = "Notion Sync Card"

# Cache kept in the collection config:
#   rows:  row id -> (last_edited_time, unix time its content was fetched)
#   pages: page id -> [last_edited_time, fetched at, [[database id, title]...]]
#   dbs:   database id -> data source id (never changes for a database) Bump RENDER_VERSION whenever the
# HTML produced for a row changes so every row is re-rendered once.
STATE_KEY = "notionSync.rowState"
RENDER_VERSION = 1
# Notion's last_edited_time has minute precision; a row only counts as
# unchanged if that whole minute had passed when its content was fetched.
EDIT_PRECISION_SECONDS = 60

RowState = Tuple[str, float]
Progress = Callable[[str], None]


def _no_progress(_message: str) -> None:
    pass


# --------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------- #


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


class SyncResult:
    def __init__(self):
        self.pages: List[PageData] = []
        self.config: dict = {}
        self.media_dir: str = ""
        self.media_files: List[Tuple[str, str]] = []  # (abs path, fname)
        self.warnings: List[str] = []
        # row cache before this run / after it
        self.known: Dict[str, RowState] = {}
        self.fetched: Dict[str, RowState] = {}
        # page structure and data source ids: before this run / after it
        self.page_cache: Dict[str, list] = {}
        self.db_cache: Dict[str, str] = {}
        self.pages_out: Dict[str, list] = {}
        self.dbs_out: Dict[str, str] = {}
        # set when part of Notion could not be read: note removal is then
        # skipped so a network error can never delete cards
        self.incomplete = False
        self.progress: Progress = _no_progress

    def cleanup(self) -> None:
        if self.media_dir:
            shutil.rmtree(self.media_dir, ignore_errors=True)


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
# Row cache (last_edited_time)
# --------------------------------------------------------------------- #


def _parse_notion_time(value: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def is_unchanged(cached: Optional[RowState], edited: str) -> bool:
    """True when the cached content was fetched after the row's last edit."""
    if not cached or not edited or cached[0] != edited:
        return False
    edited_ts = _parse_notion_time(edited)
    return edited_ts is not None and cached[1] >= edited_ts + EDIT_PRECISION_SECONDS


def _source_field_index(model: dict) -> Optional[int]:
    for index, fld in enumerate(model.get("flds", [])):
        if fld.get("name") == "Source":
            return index
    return None


def _notes_by_source(col) -> Dict[str, int]:
    """Source id -> note id for every synced note (one SQL query)."""
    model = col.models.by_name(MODEL_NAME)
    if model is None:
        return {}
    index = _source_field_index(model)
    if index is None:
        return {}
    out: Dict[str, int] = {}
    for nid, flds in col.db.all("select id, flds from notes where mid = ?", model["id"]):
        parts = flds.split("\x1f")
        if index < len(parts) and parts[index].strip():
            out[parts[index].strip()] = nid
    return out


def _raw_state(col) -> dict:
    try:
        state = col.get_config(STATE_KEY, None)
    except Exception:
        return {}
    if not isinstance(state, dict) or state.get("v") != RENDER_VERSION:
        return {}
    return state


def _load_state(col) -> Dict[str, RowState]:
    rows = _raw_state(col).get("rows") or {}
    return {k: (str(v[0]), float(v[1])) for k, v in rows.items()
            if isinstance(v, (list, tuple)) and len(v) == 2}


def load_structure(col) -> Dict[str, dict]:
    """Cached page structure and data source ids (see STATE_KEY)."""
    state = _raw_state(col)
    pages = {k: v for k, v in (state.get("pages") or {}).items()
             if isinstance(v, list) and len(v) == 3}
    dbs = {k: str(v) for k, v in (state.get("dbs") or {}).items() if v}
    return {"pages": pages, "dbs": dbs}


def load_known_rows(col) -> Dict[str, RowState]:
    """Cached rows that still have a note in this collection.

    A row whose note was deleted in Anki is dropped here, so the next sync
    downloads it again and recreates the card.
    """
    existing = _notes_by_source(col)
    return {k: v for k, v in _load_state(col).items() if k in existing}


def _save_state(col, result: SyncResult) -> None:
    rows: Dict[str, RowState] = {}
    pages: Dict[str, list] = {}
    dbs: Dict[str, str] = {}
    if result.incomplete:  # keep entries for parts we could not read
        rows.update(_load_state(col))
        old = load_structure(col)
        pages.update(old["pages"])
        dbs.update(old["dbs"])
    rows.update(result.fetched)
    pages.update(result.pages_out)
    dbs.update(result.dbs_out)
    # undoable: undoing a sync must also forget the rows it cached,
    # or reverted notes would look up to date and never be re-fetched
    col.set_config(STATE_KEY, {"v": RENDER_VERSION,
                               "rows": {k: list(v) for k, v in rows.items()},
                               "pages": pages, "dbs": dbs},
                   undoable=True)


# --------------------------------------------------------------------- #
# Phase 1: fetch from Notion
# --------------------------------------------------------------------- #


def fetch_pages(
    cfg: dict,
    known: Optional[Dict[str, RowState]] = None,
    progress: Optional[Progress] = None,
    structure: Optional[Dict[str, dict]] = None,
) -> SyncResult:
    """Read all configured pages. Rows in ``known`` that were not edited
    since they were fetched are returned as ``unchanged`` placeholders.
    ``structure`` (from ``load_structure``) skips re-reading unchanged
    pages and re-resolving data source ids."""
    client = NotionClient(resolve_token(cfg))
    tree = BlockTree(client)
    media = MediaCollector()
    result = SyncResult()
    result.config = cfg
    result.known = dict(known or {})
    if structure:
        result.page_cache = dict(structure.get("pages") or {})
        result.db_cache = dict(structure.get("dbs") or {})
    if progress is not None:
        result.progress = progress
    result.media_dir = tempfile.mkdtemp(prefix="notion-anki-sync-")
    for entry in cfg.get("notion_pages", []):
        raw = entry if isinstance(entry, str) else entry.get("page_id", "")
        raw = str(raw).strip()
        if not raw:
            continue
        try:
            page_id = canonical_page_id(raw)
            _fetch_page(page_id, client, tree, media, result, None)
        except NotionError as e:
            result.incomplete = True
            result.warnings.append(str(e))

    result.media_files = _download_media(client, media, result.media_dir, result.progress)
    return result


def _fetch_page(
    page_id: str,
    client: NotionClient,
    tree: BlockTree,
    media: MediaCollector,
    result: SyncResult,
    parent_deck: Optional[str],
    recursive: bool = False,
) -> None:
    result.progress("reading pages")
    page = client.get_page(page_id)
    title = page_title(page) or page_id[:8]
    deck_name = deck_safe(title) if parent_deck is None else f"{parent_deck}::{deck_safe(title)}"
    data = PageData(title=title, deck_name=deck_name)
    result.pages.append(data)

    edited = str(page.get("last_edited_time") or "")
    cached = result.page_cache.get(page_id)
    if not recursive and cached and is_unchanged((cached[0], cached[1]), edited):
        # page untouched: same tables as last time, skip walking its blocks
        result.pages_out[page_id] = cached
        for db_id, db_title in cached[2]:
            block = {"id": db_id, "type": "child_database",
                     "child_database": {"title": db_title}}
            table = _fetch_table(client, tree, block, media, data.deck_name, result)
            if table is not None:
                data.tables.append(table)
        return

    fetched_at = time.time()
    was_incomplete = result.incomplete
    blocks = tree.children(page_id)
    _collect_page_children(
        page_id, client, tree, media, data, result, recursive, blocks=blocks
    )
    if not recursive and edited and not (result.incomplete and not was_incomplete):
        result.pages_out[page_id] = [edited, fetched_at,
                                     [[t.db_id, t.raw_title] for t in data.tables]]


def _recurse_into_subpage(
    block: dict,
    client: NotionClient,
    tree: BlockTree,
    media: MediaCollector,
    data: PageData,
    result: SyncResult,
    recursive: bool,
) -> None:
    if not recursive or not block.get("has_children"):
        return
    try:
        sub_id = canonical_page_id(block["id"])
        _fetch_page(sub_id, client, tree, media, result,
                    parent_deck=data.deck_name, recursive=True)
    except NotionError as e:
        result.incomplete = True
        result.warnings.append(str(e))


def _collect_page_children(
    page_id: str,
    client: NotionClient,
    tree: BlockTree,
    media: MediaCollector,
    data: PageData,
    result: SyncResult,
    recursive: bool = False,
    blocks: Optional[list] = None,
) -> None:
    if blocks is None:
        blocks = tree.children(page_id)
    for block in blocks:
        if block.get("type") == "child_database":
            table = _fetch_table(client, tree, block, media, data.deck_name, result)
            if table is not None:
                data.tables.append(table)
        elif block.get("type") == "child_page":
            _recurse_into_subpage(block, client, tree, media, data, result, recursive)
        elif block.get("type") == "column_list" and block.get("has_children"):
            for col in tree.children(block["id"]):
                if col.get("type") != "column" or not col.get("has_children"):
                    continue
                for inner in tree.children(col["id"]):
                    if inner.get("type") == "child_database":
                        table = _fetch_table(client, tree, inner, media, data.deck_name, result)
                        if table is not None:
                            data.tables.append(table)
                    elif inner.get("type") == "child_page":
                        _recurse_into_subpage(inner, client, tree, media, data, result, recursive)


def _fetch_table(
    client: NotionClient,
    tree: BlockTree,
    db_block: dict,
    media: MediaCollector,
    parent_deck: str,
    result: SyncResult,
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
            result.incomplete = True
            result.warnings.append(f"Table {db_id}: {e}")
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
        result.incomplete = True
        result.warnings.append(f"Table '{title}': {e}")
        return table
    result.dbs_out[db_id] = ds_id

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
        if cached is not None and is_unchanged(cached, edited):
            result.fetched[row_id] = cached
            table.rows.append(RowCard(source_id=row_id, front=front, back_html="",
                                      url=row.get("url", ""), unchanged=True))
            continue
        result.progress(f"{title} {index}/{len(rows)}")
        fetched_at = time.time()
        try:
            back = render_blocks(tree.children(row["id"]), media, tree)
        except NotionError as e:
            result.incomplete = True
            result.warnings.append(f"Row '{front}' in '{title}': {e}")
            continue
        if edited:
            result.fetched[row_id] = (edited, fetched_at)
        table.rows.append(
            RowCard(source_id=row_id, front=front, back_html=back, url=row.get("url", ""))
        )
    return table


def _download_media(
    client: NotionClient,
    media: MediaCollector,
    media_dir: str,
    progress: Progress = _no_progress,
) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    total = len(media.files)
    for index, (fname, url) in enumerate(media.files.items(), 1):
        progress(f"media {index}/{total}")
        ext = os.path.splitext(fname)[1].lower()
        path = os.path.join(media_dir, fname)
        try:
            blob = client.download_file(url)
        except Exception:  # noqa: BLE001 - media is best-effort only
            continue
        if not ext:
            ext = _sniff_ext(blob) or ".bin"
            path += ext
        with open(path, "wb") as f:
            f.write(blob)
        out.append((path, fname + (ext if not os.path.splitext(fname)[1] else "")))
    return out


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


def _get_or_create_model(col) -> dict:
    """Find or build the 'Notion Sync Card' note type."""
    model = col.models.by_name(MODEL_NAME)
    if model is not None:
        if model.get("css") != MODEL_CSS:
            model["css"] = MODEL_CSS
            save = getattr(col.models, "save", None)
            if save is not None:
                save(model)
        return model

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


def apply_sync(col, result: SyncResult) -> Dict[str, int]:
    """Write decks/media/notes to the collection.

    Runs inside a QueryOp (background thread); the Anki backend serialises
    collection access, so this does not block the UI.
    """
    from anki.notes import Note  # local import: keep module import cheap at startup

    stats = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0,
             "media": len(result.media_files)}
    model = _get_or_create_model(col)

    # 1) media: file -> imported filename inside the collection
    fname_to_imported: Dict[str, str] = {}
    for path, fname in result.media_files:
        try:
            imported = col.media.add_file(path, preferred_fname=fname)
        except TypeError:  # older anki without preferred_fname
            imported = col.media.add_file(path)
        fname_to_imported[os.path.split(fname)[-1]] = imported
        fname_to_imported.setdefault(os.path.splitext(fname)[0], imported)

    by_source = _notes_by_source(col)
    seen_ids = set()
    for page in result.pages:
        for table in page.tables:
            deck_id: Optional[int] = None
            for row in table.rows:
                seen_ids.add(row.source_id)
                nid = by_source.get(row.source_id)
                if row.unchanged and nid is not None:
                    stats["unchanged"] += 1
                    continue
                if row.unchanged:  # note vanished since the cache was read
                    result.fetched.pop(row.source_id, None)
                    continue
                back = MediaCollector.resolve(row.back_html, fname_to_imported)
                if nid is None:
                    if deck_id is None:
                        deck_id = get_or_create_deck(col, f"{page.deck_name}::{table.title}")
                    note = Note(col, model)
                    note["Front"] = row.front
                    note["Back"] = back
                    note["Source"] = row.source_id
                    col.add_note(note, deck_id)
                    by_source[row.source_id] = note.id
                    stats["added"] += 1
                else:
                    existing = col.get_note(nid)
                    if existing["Back"] != back or existing["Front"] != row.front:
                        existing["Back"] = back
                        existing["Front"] = row.front
                        col.update_note(existing)
                        stats["updated"] += 1
    if not result.incomplete:
        stats["removed"] = _remove_obsolete(col, seen_ids)
    _save_state(col, result)

    result.cleanup()
    return stats


def get_or_create_deck(col, name: str) -> int:
    return col.decks.id(name, create=True)


def _remove_obsolete(col, seen_ids: set) -> int:
    """Remove stale synced cards without touching manually created cards."""
    to_remove = []
    try:
        note_ids = list(col.find_notes(f'note:"{MODEL_NAME}"'))
    except Exception:
        return 0
    for nid in note_ids:
        try:
            note = col.get_note(nid)
        except Exception:
            continue
        source_id = str(note["Source"]).strip()
        if source_id and source_id not in seen_ids:
            to_remove.append(nid)
    if to_remove:
        col.remove_notes(to_remove)
    return len(to_remove)
