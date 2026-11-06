# -*- coding: utf-8 -*-
"""Notion Sync addon - official Notion API -> Anki.

Structure created in your collection:
  a deck per configured Notion page -> a subdeck per table
    -> one card per row

A sync never blocks the UI: Notion is read in a background QueryOp that
does not hold the collection, then the cards are written in a CollectionOp
that Anki records as a single "Notion Sync" undo step. Progress shows on
the "Notion Sync" link next to Anki's Sync in the top toolbar.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from . import sync
from .notion_api import NotionClient, page_title

try:
    from aqt import gui_hooks, mw
    from aqt.operations import CollectionOp, QueryOp
    from aqt.qt import QAction, QKeySequence, QMenu, QTimer
    from aqt.theme import theme_manager
    from aqt.utils import showWarning, tooltip

    _IN_ANKI = True
except ImportError:  # imported outside Anki (tests / tooling) - register nothing
    _IN_ANKI = False

MENU_TITLE = "Notion Sync"
TOOLBAR_IDLE_TEXT = "Notion Sync"
TOOLBAR_LINK_ID = "notionsync"
SHORTCUT = "Ctrl+Shift+Y"

_state: Dict[str, Any] = {
    "busy": False,
    "timer": None,
    "dialog": None,
    "closing": False,
    # set while we trigger the follow-up AnkiWeb sync ourselves
    "followup_sync": False,
}


def _config() -> dict:
    try:
        return mw.addonManager.getConfig(__name__) or {}
    except Exception:
        return {}


# --------------------------------------------------------------------- #
# status in the menu bar and top toolbar
# --------------------------------------------------------------------- #


def _toolbar_label(text: Optional[str]) -> str:
    """Short label for the toolbar link: keeps the pill from jumping around."""
    if text is None:
        return TOOLBAR_IDLE_TEXT
    count = re.search(r"\d+/\d+", text)
    return f"Notion Sync {count.group(0)}" if count else "Notion Sync..."


def _set_status(text: Optional[str] = None) -> None:
    """Show progress on the toolbar link; ``None`` restores the idle state."""
    label = _toolbar_label(text)
    try:
        mw.toolbar.web.eval(
            f"(function(){{var e=document.getElementById({json.dumps(TOOLBAR_LINK_ID)});"
            f"if(e)e.textContent={json.dumps(label)};}})();"
        )
    except Exception:  # toolbar not ready or replaced by another add-on
        pass


# --------------------------------------------------------------------- #
# sync: QueryOp (Notion, no collection) -> CollectionOp (cards, one undo)
# --------------------------------------------------------------------- #


def run_sync(manual: bool = True, full: bool = False, then_ankiweb: bool = False) -> None:
    """Start a sync.

    ``full`` ignores the last_edited cache. ``then_ankiweb`` uploads the
    result with a second AnkiWeb sync when anything changed.
    """
    if _state["busy"]:
        if manual:
            tooltip("Notion Sync is already running.")
        return
    if mw.col is None:
        if manual:
            showWarning("Notion Sync: no collection is open (no profile loaded).")
        return

    cfg = _config()
    if not cfg.get("notion_pages"):
        if manual:
            showWarning("Notion Sync: no pages configured.\n"
                        "Open Tools > Notion Sync > Settings and add a page.")
        return
    if not sync.resolve_token(cfg):
        if manual:
            showWarning(
                "Notion Sync: no API token found.\n\n"
                "Set it in Tools > Notion Sync > Settings, or use the NOTION_API_TOKEN "
                "environment variable, or a .env file containing NOTION_API_TOKEN=... ."
            )
        return

    _state["busy"] = True
    _set_status("starting...")
    known: dict = {}
    structure: dict = {}
    if not full:
        try:
            known = sync.load_known_rows(mw.col)
            structure = sync.load_structure(mw.col)
        except Exception:  # noqa: BLE001 - a bad cache just means a full sync
            known, structure = {}, {}
    profile = mw.pm.name

    def progress(message: str) -> None:  # called on the background thread
        mw.taskman.run_on_main(lambda: _state["busy"] and _set_status(message))

    def fetch(_col) -> "sync.SyncResult":
        return sync.fetch_pages(cfg, known, progress, structure)

    def fetched(result: "sync.SyncResult") -> None:
        if mw.col is None or mw.pm.name != profile:  # profile closed/switched meanwhile
            result.cleanup()
            _finish()
            return
        _import(result, manual, then_ankiweb)

    def failed(exc: Exception) -> None:
        _finish()
        if manual:
            showWarning(f"Notion Sync failed:\n{exc}")
        else:
            tooltip(f"Notion Sync failed: {exc}", period=6000)

    QueryOp(parent=mw, op=fetch, success=fetched).failure(failed) \
        .without_collection().run_in_background()


def _import(result: "sync.SyncResult", manual: bool, then_ankiweb: bool) -> None:
    _set_status("importing cards...")
    stats: Dict[str, int] = {}

    def apply(col):
        undo_start = col.add_custom_undo_entry("Notion Sync")
        stats.update(sync.apply_sync(col, result))
        return col.merge_undo_entries(undo_start)

    def done(_changes) -> None:
        _finish()
        _report(result, stats, manual)
        changed = stats.get("added", 0) + stats.get("updated", 0) + stats.get("removed", 0)
        if then_ankiweb and changed:
            _upload_to_ankiweb()

    def failed(exc: Exception) -> None:
        result.cleanup()
        _finish()
        showWarning(f"Notion Sync failed while importing:\n{exc}")

    CollectionOp(parent=mw, op=apply).success(done).failure(failed).run_in_background()


def _finish() -> None:
    _state["busy"] = False
    _set_status(None)


def _report(result: "sync.SyncResult", stats: Dict[str, int], manual: bool) -> None:
    parts = []
    for key in ("added", "updated", "removed"):
        if stats.get(key):
            parts.append(f"{stats[key]} {key}")
    if stats.get("unchanged"):
        parts.append(f"{stats['unchanged']} unchanged")
    summary = ", ".join(parts) or "nothing to sync"
    if result.incomplete:
        summary += " (removal skipped: some of Notion could not be read)"
    tooltip(f"<b>Notion Sync</b>: {summary}", period=5000)

    if result.warnings:
        shown = result.warnings[:15]
        more = len(result.warnings) - len(shown)
        text = "\n".join(f"- {w}" for w in shown)
        if more > 0:
            text += f"\n... and {more} more"
        if manual:
            showWarning(f"Notion Sync finished with warnings:\n\n{text}")
        else:
            tooltip(f"Notion Sync: {len(result.warnings)} warning(s). "
                    "Run Sync Notion manually to see them.", period=6000)


# --------------------------------------------------------------------- #
# AnkiWeb: pull Notion after Anki syncs, upload the result right away
# --------------------------------------------------------------------- #


def _on_ankiweb_sync_finished(*_args: Any) -> None:
    if _state["followup_sync"]:  # our own upload sync: don't loop
        _state["followup_sync"] = False
        return
    if _state["closing"] or not _config().get("sync_with_ankiweb"):
        return
    run_sync(manual=False, then_ankiweb=True)


def _upload_to_ankiweb() -> None:
    if _state["closing"] or mw.col is None:
        return
    if mw.media_syncer.is_syncing():  # the button would only open the media log
        tooltip("Notion Sync: changes will be uploaded on the next AnkiWeb sync.")
        return
    _state["followup_sync"] = True
    mw.on_sync_button_clicked()


# --------------------------------------------------------------------- #
# auto sync timer and profile lifecycle
# --------------------------------------------------------------------- #


def _schedule_autosync() -> None:
    stop_autosync()
    cfg = _config()
    interval_minutes = int(cfg.get("sync_every_minutes") or 0)
    if interval_minutes <= 0:
        return

    def on_tick() -> None:
        if mw.col is not None and not _state["busy"]:
            run_sync(manual=False)

    timer = QTimer(mw)
    timer.setInterval(interval_minutes * 60 * 1000)
    timer.timeout.connect(on_tick)
    timer.start()
    _state["timer"] = timer


def stop_autosync() -> None:
    timer = _state.get("timer")
    if timer is not None:
        timer.stop()
        timer.deleteLater()
        _state["timer"] = None


def _on_profile_open() -> None:
    _state["closing"] = False
    _state["followup_sync"] = False
    _schedule_autosync()


def _on_profile_close() -> None:
    # fires before Anki's close-time AnkiWeb sync: don't start Notion then
    _state["closing"] = True
    stop_autosync()


# --------------------------------------------------------------------- #
# settings dialog, menus and toolbar
# --------------------------------------------------------------------- #


def _open_settings() -> None:
    from .settings_ui import SettingsDialog

    def lookup_name(page_id: str, token: str) -> str:
        client = NotionClient(sync.resolve_token({"notion_token": token}))
        return page_title(client.get_page(page_id))

    def on_save(new_cfg: dict) -> None:
        mw.addonManager.writeConfig(__name__, new_cfg)
        _schedule_autosync()
        _redraw_toolbar()  # show/hide the toolbar link

    dialog = SettingsDialog(
        _config(),
        canonical_page_id=sync.canonical_page_id,
        lookup_name=lookup_name,
        on_save=on_save,
        on_sync=run_sync,
        parent=mw,
        dark=theme_manager.night_mode,
    )
    _state["dialog"] = dialog
    try:
        dialog.exec()
    finally:
        _state["dialog"] = None


def _on_theme_changed() -> None:
    dialog = _state.get("dialog")
    if dialog is not None:
        dialog.apply_theme(theme_manager.night_mode)


def _fill_menu(menu: QMenu) -> None:
    """Tools > Notion Sync."""
    act_sync = QAction("Sync Now", menu)
    act_sync.setShortcut(QKeySequence(SHORTCUT))
    act_sync.triggered.connect(lambda: run_sync())
    menu.addAction(act_sync)

    act_full = QAction("Full Resync (re-download every row)", menu)
    act_full.triggered.connect(lambda: run_sync(full=True))
    menu.addAction(act_full)

    menu.addSeparator()
    act_settings = QAction("Settings", menu)
    act_settings.triggered.connect(_open_settings)
    menu.addAction(act_settings)


def _on_toolbar_links(links: list, toolbar: Any) -> None:
    """One-click "Notion Sync" right after Anki's Sync link.

    Hidden when Anki's own Sync button already syncs Notion.
    """
    if _config().get("sync_with_ankiweb"):
        return
    label = _toolbar_label("" if _state["busy"] else None)
    links.append(toolbar.create_link(
        "notionsync", label, lambda: run_sync(),
        tip=f"Sync cards from Notion ({SHORTCUT})", id=TOOLBAR_LINK_ID,
    ))


def _redraw_toolbar() -> None:
    try:
        mw.toolbar.draw()
    except Exception:  # toolbar not ready or replaced by another add-on
        pass


def _build_menu() -> None:
    menu = QMenu(MENU_TITLE, mw)
    _fill_menu(menu)
    mw.form.menuTools.addMenu(menu)


if _IN_ANKI:
    gui_hooks.profile_did_open.append(_on_profile_open)
    gui_hooks.profile_will_close.append(_on_profile_close)
    gui_hooks.sync_did_finish.append(_on_ankiweb_sync_finished)
    gui_hooks.theme_did_change.append(_on_theme_changed)
    gui_hooks.top_toolbar_did_init_links.append(_on_toolbar_links)
    # Tools > Add-ons > Config opens our dialog instead of the JSON editor
    mw.addonManager.setConfigAction(__name__, _open_settings)
    _build_menu()
