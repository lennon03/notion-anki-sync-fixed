# -*- coding: utf-8 -*-
"""The add-on's note types and the notes it made.

Note types are found by the ids stored in the collection config, so renaming
them in Anki changes nothing. A table can use a note type the user chose.
``anki`` is only used through the collection passed in.
"""

from __future__ import annotations

import hashlib
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Tuple,
)

from .notion_api import bare_id
from .pagecards import SOURCE as SOURCE_ID  # a Notion id: 32 hex digits

MODEL_NAME = "Notion Sync Card"
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
# The Source of a synced note is a Notion row id (SOURCE_ID). Notes of the
# add-on's note type with any other Source were made by hand.
# The add-on owns the tags under this prefix (Anki tags are case-insensitive)
# and never touches any other tag.
TAG_PREFIX = "Notion::"


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


def _create_model(
    col, name: str, fields: Tuple[str, ...], template: dict, css: str
) -> dict:
    """Add a note type with these fields and one card template."""
    model = col.models.new(name)
    if "{{cloze:" in template["qfmt"]:
        model["type"] = 1  # anki.models.MODEL_CLOZE
    for fname in fields:
        col.models.add_field(model, col.models.new_field(fname))
    col.models.add_template(model, {"ord": 0, **template})
    model["css"] = css
    col.models.add_dict(model)
    refreshed = col.models.by_name(name)
    return refreshed if refreshed is not None else model


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
    return _create_model(
        col,
        MODEL_NAME,
        ("Front", "Back", "Source"),
        {
            "name": "Notion Sync Card",
            "qfmt": "{{Front}}",
            "afmt": "{{Front}}<hr id=answer>{{Back}}",
        },
        css,
    )


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
    return _create_model(
        col,
        CLOZE_MODEL_NAME,
        ("Text", "Back Extra", "Source"),
        {
            "name": "Cloze",
            "qfmt": '<div class="ns-q">{{cloze:Text}}</div>'
            + CLOZE_FRONT_STYLE,
            "afmt": '<div class="ns-a">{{cloze:Text}}</div>'
            "{{#Back Extra}}<hr id=answer>{{Back Extra}}{{/Back Extra}}",
        },
        css,
    )
