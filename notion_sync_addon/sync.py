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

Each part lives in its own module: ``cache`` (the cache file and when its
content is trusted), ``note_types`` (the add-on's note types and the notes
it made), ``rows`` (what a sync carries), ``fetch`` (phase 1), ``media``
(downloading and importing files) and ``apply`` (phase 2). This module runs
phase 0 and keeps every name the add-on, the worker and the tests use.
"""

from __future__ import annotations

from typing import Dict, List

from .apply import UNCHECKED_TAG, _row_fields, apply_sync
from .cache import (
    AGE_SPREAD_SECONDS,
    MAX_AGE_SECONDS,
    RENDER_VERSION,
    VOLATILE_MAX_AGE_SECONDS,
    Cache,
    RowState,
    _parse_notion_time,
    cache_path,
    is_unchanged,
    known_tables,
    load_cache,
    load_problems,
    max_age,
    save_cache,
)
from .fetch import (
    MAX_ORIGIN_CHECKS,
    _check_unlisted,
    _collect_page_children,
    _fetch_table,
    _has_live_mention,
    _is_volatile,
    deck_safe,
    fetch_pages,
    resolve_token,
    split_hint,
)
from .media import _download_media
from .note_types import (
    MODEL_CSS,
    TAG_PREFIX,
    SyncedNote,
    _get_or_create_model,
    eligible_note_types,
    fingerprint,
    own_model_ids,
    set_table_note_types,
    synced_notes,
    table_note_types,
)
from .notion_api import NotionClient, bare_id, canonical_page_id
from .rows import (
    OverlapSpec,
    PageData,
    Prepared,
    RowCard,
    RowColumns,
    SyncResult,
    _origin,
    clean_tag,
    parse_overlap,
    row_columns,
)

__all__ = [
    "AGE_SPREAD_SECONDS",
    "Cache",
    "MAX_AGE_SECONDS",
    "MAX_ORIGIN_CHECKS",
    "MODEL_CSS",
    "NotionClient",
    "OverlapSpec",
    "PageData",
    "Prepared",
    "RENDER_VERSION",
    "RowCard",
    "RowColumns",
    "RowState",
    "SyncResult",
    "TAG_PREFIX",
    "UNCHECKED_TAG",
    "VOLATILE_MAX_AGE_SECONDS",
    "_check_unlisted",
    "_collect_page_children",
    "_download_media",
    "_fetch_table",
    "_get_or_create_model",
    "_has_live_mention",
    "_is_volatile",
    "_origin",
    "_parse_notion_time",
    "_row_fields",
    "apply_sync",
    "bare_id",
    "cache_path",
    "canonical_page_id",
    "clean_tag",
    "deck_safe",
    "eligible_note_types",
    "fetch_pages",
    "fingerprint",
    "is_unchanged",
    "known_tables",
    "load_cache",
    "load_problems",
    "max_age",
    "own_model_ids",
    "parse_overlap",
    "prepare_sync",
    "resolve_token",
    "row_columns",
    "save_cache",
    "set_table_note_types",
    "split_hint",
    "synced_notes",
    "table_note_types",
]


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
