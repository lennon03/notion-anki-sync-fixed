# -*- coding: utf-8 -*-
"""Phase 2 of a sync: write the collection (background QueryOp).

``apply_sync`` makes sure the note types and decks exist, imports media,
adds, updates and removes notes in one undo step, and writes the cache.
"""

from __future__ import annotations

import html
import time
import unicodedata
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from .cache import Cache, save_cache
from .htmlify import MediaCollector
from .media import _import_media
from .note_types import (
    BACK,
    FRONT,
    SOURCE,
    SyncedNote,
    _get_or_create_cloze_model,
    _get_or_create_model,
    _no_change,
    _remember_model,
    _source_index,
    card_css,
    find_model,
    fingerprint,
    own_model_ids,
    owned_key,
    owned_tags,
    synced_notes,
    table_note_types,
)
from .notion_api import bare_id
from .pagecards import has_cloze
from .rows import (
    PAGE_CARDS,
    OverlapSpec,
    PageData,
    RowCard,
    SyncResult,
    TableData,
    _forget,
    _origin,
)

UNDO_NAME = "Notion Sync"
# Marks the notes whose cards were suspended because the row was unchecked.
UNCHECKED_TAG = "notion-sync::unchecked"
# Older versions kept the cache in the collection config under this key.
LEGACY_STATE_KEY = "notionSync.rowState"


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
