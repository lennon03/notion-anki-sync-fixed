# -*- coding: utf-8 -*-
"""Notion Sync addon - official Notion API -> Anki.

Structure created in your collection:
  a deck per configured Notion page -> a subdeck per table
    -> one card per row

A sync never blocks the UI and never opens a progress window: the row
cache is checked against the collection in a background QueryOp, Notion is
read in a QueryOp that does not hold the collection, then the cards are
written in a third QueryOp that records a single "Notion Sync" undo step
(only when something changed). Progress shows on the "Notion Sync" link
next to Anki's Sync in the top toolbar.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Callable, Dict, Optional

from . import sync
from .notion_api import NotionClient, page_title

try:
    from aqt import gui_hooks, mw
    from aqt.operations import QueryOp
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
ADDON_DIR = os.path.dirname(os.path.abspath(__file__))

_state: Dict[str, Any] = {
    "busy": False,
    # bumped by every sync: late progress callbacks of an older one are ignored
    "generation": 0,
    "timer": None,
    "dialog": None,
    "closing": False,
    # True only while we call Anki's sync ourselves (synchronous call)
    "followup_pending": False,
    # True from that sync's sync_will_start to its sync_did_finish
    "followup_running": False,
    # set by Anki's sync error handler during the current AnkiWeb sync
    "ankiweb_failed": False,
    # warnings of the last automatic sync: only new ones raise a tooltip
    "last_warnings": None,
    # False when aqt.sync.handle_sync_error is missing: failed AnkiWeb syncs
    # can't be told apart from successful ones
    "probe": False,
    # the _CloseGuard registered while a sync op uses the collection
    "guard": None,
}

# name under which the close guard is registered with Anki's dialog manager
CLOSE_GUARD_NAME = "NotionSyncWritingCards"


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
# sync: prepare (collection) -> fetch (Notion, no collection) -> apply
# --------------------------------------------------------------------- #


def _no_changes() -> Any:
    from anki.collection import OpChanges

    return OpChanges()


def _op_finished(changes: Any) -> None:
    """What CollectionOp does after its op: refresh undo menu and screens."""
    if mw.col is None:
        return
    try:
        from aqt.operations import on_op_finished
    except ImportError:  # pragma: no cover - older/newer aqt
        mw.update_undo_actions()
        gui_hooks.operation_did_execute(changes, None)
        if mw.col.op_made_changes(changes):
            gui_hooks.state_did_reset()
        return
    on_op_finished(mw, changes, None)


class _CloseGuard:
    """Makes Anki wait for a running sync op before it closes the collection.

    Anki closes every window registered with its dialog manager before it
    unloads the collection (closing the profile or Anki, a full sync) and
    waits until each one has called back. While one of our ops uses the
    collection, this stand-in is registered there: it calls back only when
    the op has finished, so the collection is never closed under it.
    """

    def __init__(self) -> None:
        self.callbacks: list = []

    def closeWithCallback(self, callback: Callable[[], None]) -> None:  # noqa: N802
        self.callbacks.append(callback)
        tooltip("Notion Sync: finishing the import before closing...", period=3000)


def _dialog_manager() -> Any:
    try:
        import aqt

        return aqt.dialogs
    except Exception:  # noqa: BLE001 - outside Anki
        return None


def _hold_collection() -> None:
    """Keep Anki from closing the collection until _release_collection."""
    if _state["guard"] is not None:
        return
    dialogs = _dialog_manager()
    if dialogs is None or not hasattr(dialogs, "register_dialog"):
        return  # older/newer aqt: an op cut short by closing just fails quietly
    guard = _CloseGuard()
    try:
        dialogs.register_dialog(CLOSE_GUARD_NAME, _CloseGuard, guard)
    except Exception:  # noqa: BLE001
        return
    _state["guard"] = guard


def _release_collection() -> None:
    guard = _state["guard"]
    if guard is None:
        return
    _state["guard"] = None
    dialogs = _dialog_manager()
    try:
        dialogs.markClosed(CLOSE_GUARD_NAME)
    except Exception:  # noqa: BLE001
        pass
    for callback in guard.callbacks:  # Anki was waiting to close: go on
        mw.taskman.run_on_main(callback)


def run_sync(manual: bool = True, full: bool = False, then_ankiweb: bool = False) -> None:
    """Start a sync.

    ``full`` ignores every cache. ``then_ankiweb`` uploads the result with a
    second AnkiWeb sync when anything changed.
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

    _state["generation"] += 1
    generation = _state["generation"]
    _state["busy"] = True
    _set_status("starting...")
    if cfg.get("sync_with_ankiweb"):  # no toolbar link to show progress on
        tooltip("Notion Sync: reading Notion...", period=2500)
    profile = mw.pm.name
    path = sync.cache_path(ADDON_DIR, profile)

    def current() -> bool:
        return _state["generation"] == generation and _state["busy"]

    def aborted() -> bool:
        """Profile closed or switched since the sync started."""
        return _state["closing"] or mw.col is None or mw.pm.name != profile

    def failed(exc: Exception) -> None:
        _release_collection()
        _finish(generation)
        if _state["closing"]:
            print(f"Notion Sync failed while closing: {exc}", file=sys.stderr)
        elif manual:
            showWarning(f"Notion Sync failed:\n{exc}")
        else:
            tooltip(f"Notion Sync failed: {exc}", period=6000)

    def guarded(step: Callable[[Any], None]) -> Callable[[Any], None]:
        """A success callback that can't leave the sync stuck as busy."""
        def run(value: Any) -> None:
            try:
                step(value)
            except Exception as exc:  # noqa: BLE001
                if isinstance(value, sync.SyncResult):
                    value.cleanup()
                failed(exc)
        return run

    def progress(message: str) -> None:  # called on the background thread
        mw.taskman.run_on_main(lambda: current() and _set_status(message))

    def prepare(col) -> "sync.Prepared":
        return sync.prepare_sync(col, path, full)

    def prepared(prep: "sync.Prepared") -> None:
        _release_collection()
        if aborted():
            _finish(generation)
            return

        def fetch(_col) -> "sync.SyncResult":
            result = sync.fetch_pages(cfg, prep.known, progress, prep.structure,
                                      prep.previous, prep.sources)
            result.cache_path = path
            return result

        QueryOp(parent=mw, op=fetch, success=guarded(fetched)).failure(failed) \
            .without_collection().run_in_background()

    def fetched(result: "sync.SyncResult") -> None:
        if aborted():
            result.cleanup()
            _finish(generation)
            return
        _set_status("importing cards...")
        stats: Dict[str, int] = {}

        def apply(col) -> None:
            stats.update(sync.apply_sync(col, result))

        def applied(_none: None) -> None:
            try:
                _finish(generation)
                if result.changes is not None:
                    _op_finished(result.changes)
                _report(result, stats, manual)
                changed = (stats.get("added", 0) + stats.get("updated", 0)
                           + stats.get("removed", 0))
                if then_ankiweb and changed:
                    _upload_to_ankiweb()
            finally:
                _release_collection()

        def apply_failed(exc: Exception) -> None:
            try:
                result.cleanup()
                if result.changes is not None:  # partial changes still form one undo step
                    _op_finished(result.changes)
                _finish(generation)
                if _state["closing"] or mw.col is None:
                    # the next sync rewrites whatever was left half done
                    print(f"Notion Sync import interrupted: {exc}", file=sys.stderr)
                else:
                    showWarning(f"Notion Sync failed while importing:\n{exc}")
            finally:
                _release_collection()

        # a QueryOp, not a CollectionOp: CollectionOp always shows Anki's
        # modal "Processing..." window; the undo step is made by apply_sync
        _hold_collection()
        QueryOp(parent=mw, op=apply, success=guarded(applied)).failure(apply_failed) \
            .run_in_background()

    try:
        _hold_collection()
        QueryOp(parent=mw, op=prepare, success=guarded(prepared)).failure(failed) \
            .run_in_background()
    except Exception as exc:  # noqa: BLE001
        failed(exc)


def _finish(generation: Optional[int] = None) -> None:
    if generation is not None and generation != _state["generation"]:
        return
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
        summary += " (cards of the parts that could not be read were kept)"
    tooltip(f"<b>Notion Sync</b>: {summary}", period=5000)

    if not result.warnings:
        _state["last_warnings"] = None
        return
    shown = result.warnings[:15]
    more = len(result.warnings) - len(shown)
    text = "\n".join(f"- {w}" for w in shown)
    if more > 0:
        text += f"\n... and {more} more"
    if manual:
        showWarning(f"Notion Sync finished with warnings:\n\n{text}")
    elif result.warnings != _state["last_warnings"]:
        # automatic syncs: a table that keeps failing only warns once
        tooltip(f"Notion Sync: {len(result.warnings)} warning(s). "
                "Run Tools > Notion Sync > Sync Now to see them.", period=6000)
    _state["last_warnings"] = list(result.warnings)


# --------------------------------------------------------------------- #
# AnkiWeb: pull Notion after Anki syncs, upload the result right away
# --------------------------------------------------------------------- #


def _install_sync_error_probe() -> None:
    """sync_did_finish fires even when the AnkiWeb sync failed; Anki routes
    every sync error through aqt.sync.handle_sync_error, so note it there."""
    try:
        import aqt.sync as aqt_sync
    except ImportError:
        aqt_sync = None
    original = getattr(aqt_sync, "handle_sync_error", None)
    if original is None:
        # Notion is still pulled after every AnkiWeb sync; the upload that
        # follows only happens once Anki's status check succeeds and asks
        # for a normal sync (see _upload_to_ankiweb)
        _state["probe"] = False
        print("Notion Sync: aqt.sync.handle_sync_error not found, failed AnkiWeb "
              "syncs can't be detected", file=sys.stderr)
        return
    _state["probe"] = True
    if getattr(original, "_notion_sync_probe", False):
        return

    def probe(*args: Any, **kwargs: Any) -> Any:
        _state["ankiweb_failed"] = True
        return original(*args, **kwargs)

    probe._notion_sync_probe = True  # type: ignore[attr-defined]
    aqt_sync.handle_sync_error = probe


def _on_ankiweb_sync_will_start() -> None:
    _state["ankiweb_failed"] = False
    # a stale flag (a follow-up that never finished) must not swallow this sync
    _state["followup_running"] = _state["followup_pending"]


def _on_ankiweb_sync_finished(*_args: Any) -> None:
    if _state["followup_running"]:  # our own upload sync: don't loop
        _state["followup_running"] = False
        return
    if _state["ankiweb_failed"] or _state["closing"] or not _config().get("sync_with_ankiweb"):
        return
    run_sync(manual=False, then_ankiweb=True)


def _can_upload() -> bool:
    # without saved AnkiWeb credentials Anki would open its login dialog
    return not _state["closing"] and mw.col is not None and bool(mw.pm.sync_auth())


def _upload_to_ankiweb() -> None:
    if not _can_upload():
        return
    try:
        from aqt.sync import get_sync_status
    except ImportError:  # pragma: no cover
        if _state["probe"]:
            _start_followup_sync()
        return

    def on_status(status: Any) -> None:
        # Anki calls back only when the status check worked (it swallows
        # errors), so a failing AnkiWeb never gets a follow-up sync
        if status.required == status.FULL_SYNC:  # would ask upload/download
            tooltip("Notion Sync: AnkiWeb needs a full sync; press Sync to upload.",
                    period=6000)
            return
        if status.required != getattr(status, "NORMAL_SYNC", 1):
            return  # nothing to upload
        _start_followup_sync()

    get_sync_status(mw, on_status)


def _start_followup_sync() -> None:
    if not _can_upload():
        return
    if mw.media_syncer.is_syncing():  # the button would only open the media log
        tooltip("Notion Sync: changes will be uploaded on the next AnkiWeb sync.")
        return
    _state["followup_pending"] = True
    try:
        mw.on_sync_button_clicked()  # fires sync_will_start synchronously
    finally:
        _state["followup_pending"] = False


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
    _state["followup_pending"] = False
    _state["followup_running"] = False
    _state["last_warnings"] = None
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

    try:
        problems = sync.load_problems(sync.cache_path(ADDON_DIR, mw.pm.name))
    except Exception:  # noqa: BLE001 - the dialog must open regardless
        problems = []
    dialog = SettingsDialog(
        _config(),
        canonical_page_id=sync.canonical_page_id,
        lookup_name=lookup_name,
        on_save=on_save,
        on_sync=run_sync,
        parent=mw,
        dark=theme_manager.night_mode,
        problems=problems,
    )
    _state["dialog"] = dialog
    try:
        dialog.exec()
    finally:
        _state["dialog"] = None
        dialog.deleteLater()  # parented to mw: would otherwise live until exit


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
    gui_hooks.sync_will_start.append(_on_ankiweb_sync_will_start)
    gui_hooks.sync_did_finish.append(_on_ankiweb_sync_finished)
    gui_hooks.theme_did_change.append(_on_theme_changed)
    gui_hooks.top_toolbar_did_init_links.append(_on_toolbar_links)
    _install_sync_error_probe()
    # Tools > Add-ons > Config opens our dialog instead of the JSON editor
    mw.addonManager.setConfigAction(__name__, _open_settings)
    _build_menu()
