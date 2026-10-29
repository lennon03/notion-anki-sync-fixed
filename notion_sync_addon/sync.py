# -*- coding: utf-8 -*-
"""Sync orchestration.

Phase 1 (network, may run on a background thread):
    walk the configured Notion pages -> build PageData structures
    (tables -> rows -> rendered HTML with media placeholders),
    download all referenced media into a temp dir.

Phase 2 (collection, must run on the main thread with mw.col open):
    ensure note type / decks exist, import media, add or update notes,
    optionally remove obsolete ones.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .htmlify import MediaCollector, render_blocks
from .notion_api import (
    BlockTree,
    NotionClient,
    NotionError,
    data_source_of,
    page_title,
)

MODEL_NAME = "Notion Sync Card"
# --------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------- #


@dataclass
class RowCard:
    source_id: str
    front: str
    back_html: str  # may contain @@NSMEDIA:*@@ placeholders
    url: str


@dataclass
class TableData:
    title: str
    rows: List[RowCard] = field(default_factory=list)


@dataclass
class PageData:
    title: str
    deck_name: str
    tables: List[TableData] = field(default_factory=list)


class SyncResult:
    def __init__(self):
        self.pages: List[PageData] = []
        self.media_dir: str = ""
        self.media_files: List[Tuple[str, str]] = []  # (abs path, fname)
        self.warnings: List[str] = []

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
# Phase 1: fetch from Notion
# --------------------------------------------------------------------- #


def fetch_pages(cfg: dict) -> SyncResult:
    client = NotionClient(resolve_token(cfg))
    tree = BlockTree(client)
    media = MediaCollector()
    result = SyncResult()
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
            result.warnings.append(str(e))

    result.media_files = _download_media(client, media, result.media_dir)
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
    page = client.get_page(page_id)
    title = page_title(page) or page_id[:8]
    deck_name = deck_safe(title) if parent_deck is None else f"{parent_deck}::{deck_safe(title)}"
    data = PageData(title=title, deck_name=deck_name)
    result.pages.append(data)

    blocks = tree.children(page_id)
    for block in blocks:
        if (
            recursive
            and block.get("type") == "child_page"
            and block.get("has_children")
        ):
            _recurse_into_subpage(block, client, tree, media, data, result, recursive)

    _collect_page_children(page_id, client, tree, media, data, result, recursive)


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
    sub_title = (block.get("child_page") or {}).get("title") or "sub page"
    try:
        sub_id = canonical_page_id(block["id"])
        _fetch_page(sub_id, client, tree, media, result,
                    parent_deck=data.deck_name, recursive=True)
    except NotionError as e:
        result.warnings.append(str(e))


def _collect_page_children(
    page_id: str,
    client: NotionClient,
    tree: BlockTree,
    media: MediaCollector,
    data: PageData,
    result: SyncResult,
    recursive: bool = False,
) -> None:
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
    try:
        db = client.get_database(db_id)
    except NotionError as e:
        result.warnings.append(f"Table {db_id}: {e}")
        return None
    ds = data_source_of(db) or {}
    ds_id = ds.get("id") or db_id
    title = (
        "".join(t.get("plain_text", "") for t in db.get("title", [])).strip()
        or ds.get("name", "").strip()
        or "Table"
    )
    table = TableData(title=deck_safe(title))
    try:
        rows = client.query_data_source(ds_id)
    except NotionError as e:
        result.warnings.append(f"Table '{title}': {e}")
        return table

    for row in rows:
        front = ""
        for prop in row.get("properties", {}).values():
            if prop.get("type") == "title":
                front = "".join(x.get("plain_text", "") for x in prop.get("title", [])).strip()
                break
        if not front:
            result.warnings.append(f"Suspended empty row in table '{title}' (no title): skipped")
            continue
        row_id = row["id"].replace("-", "")
        try:
            back = render_blocks(tree.children(row["id"]), media, tree)
        except NotionError as e:
            result.warnings.append(f"Row '{front}' in '{title}': {e}")
            continue
        table.rows.append(
            RowCard(source_id=row_id, front=front, back_html=back, url=row.get("url", ""))
        )
    return table


def _download_media(
    client: NotionClient, media: MediaCollector, media_dir: str
) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for fname, url in media.files.items():
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

MODEL_CSS = ".card { text-align: left; }"


def _get_or_create_model(col) -> dict:
    """Find or build the 'Notion Sync Card' note type."""
    model = col.models.by_name(MODEL_NAME)
    if model is not None:
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
    """Write decks/media/notes to the collection. MUST run on the main
    thread with mw.col open (col writes are not thread safe)."""
    from anki.notes import Note  # local import: keep module import cheap at startup

    stats = {"added": 0, "updated": 0, "removed": 0, "media": len(result.media_files)}
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

    seen_ids = set()
    for page in result.pages:
        for table in page.tables:
            for row in table.rows:
                seen_ids.add(row.source_id)
                deck_id = get_or_create_deck(col, f"{page.deck_name}::{table.title}")
                back = MediaCollector.resolve(row.back_html, fname_to_imported)
                existing = _find_note_by_source(col, row.source_id)
                if existing is None:
                    note = Note(col, model)
                    note["Front"] = row.front
                    note["Back"] = back
                    note["Source"] = row.source_id
                    col.add_note(note, deck_id)
                    stats["added"] += 1
                else:
                    if existing["Back"] != back or existing["Front"] != row.front:
                        existing["Back"] = back
                        existing["Front"] = row.front
                        col.update_note(existing)
                        stats["updated"] += 1
    stats["removed"] = _remove_obsolete(col, seen_ids)

    result.cleanup()
    return stats


def get_or_create_deck(col, name: str) -> int:
    return col.decks.id(name, create=True)


def _find_note_by_source(col, source_id: str):
    try:
        note_ids = col.find_notes(f'Source:{source_id}')
    except Exception:
        return None
    for nid in note_ids:
        try:
            note = col.get_note(nid)
        except Exception:
            continue
        if str(note["Source"]) == source_id:
            return note
    return None


def _remove_obsolete(col, seen_ids: set) -> int:
    """Remove notes of our model whose Source was not seen during this sync."""
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
        if str(note["Source"]) not in seen_ids:
            to_remove.append(nid)
    if to_remove:
        col.remove_notes(to_remove)
    return len(to_remove)
