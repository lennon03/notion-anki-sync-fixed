# -*- coding: utf-8 -*-
"""Notion Sync addon - official Notion API -> Anki.

Structure created in your collection:
  target deck (config) -> a deck per configured Notion page
    -> a subdeck per table in that page
      -> one card per row (Front = title column, Back = row page content)
"""
from __future__ import annotations

import threading
import traceback
from typing import Any, Dict

from . import sync

try:
    from aqt import gui_hooks, mw
    from aqt.qt import QAction, QMenu, QTimer
    from aqt.utils import showInfo, showWarning

    _IN_ANKI = True
except ImportError:  # imported outside Anki (tests / tooling) - register nothing
    _IN_ANKI = False

MENU_TITLE = "Notion Sync"

_state: Dict[str, Any] = {"busy": False, "timer": None}


def _config() -> dict:
    try:
        return mw.addonManager.getConfig(__name__) or {}
    except Exception:
        return {}


# --------------------------------------------------------------------- #
# two-phase sync (network thread -> main thread collection writes)
# --------------------------------------------------------------------- #


def run_sync(remove_obsolete: bool = False, preview_only: bool = False) -> None:
    if _state["busy"]:
        showInfo("Notion Sync is already running.")
        return
    if mw.col is None:
        showWarning("Notion Sync: no collection is open (no profile loaded).")
        return

    cfg = _config()
    if not cfg.get("notion_pages"):
        showWarning(
            "Notion Sync: no pages configured.\n"
            "Open Tools > Add-ons > 'Notion Sync (official API)' > Config and add "
            "at least one page_id."
        )
        return
    if not sync.resolve_token(cfg):
        showWarning(
            "Notion Sync: no API token found.\n\n"
            "Set 'notion_token' in the add-on config, or the NOTION_API_TOKEN "
            "environment variable, or a .env file containing NOTION_API_TOKEN=... ."
        )
        return

    _state["busy"] = True
    mw.progress.start(label="Notion Sync: fetching from Notion...", immediate=True)

    holder: Dict[str, Any] = {}
    thread = threading.Thread(target=lambda: _fetch_worker(cfg, holder), daemon=True)
    thread.start()
    _poll_until_fetched(holder, then=lambda: _after_fetch(holder, remove_obsolete, preview_only))


def _fetch_worker(cfg: dict, holder: Dict[str, Any]) -> None:
    try:
        holder["result"] = sync.fetch_pages(cfg)
    except Exception as e:  # noqa: BLE001
        holder["error"] = f"{e}\n{traceback.format_exc(limit=6)}"
    holder["done"] = True


def _poll_until_fetched(holder: Dict[str, Any], then) -> None:
    """Light main-thread polling: continues as soon as the fetch thread ends."""
    timer = QTimer(mw)
    timer.setInterval(150)
    timer.setSingleShot(False)

    def tick():
        if holder.get("done"):
            timer.stop()
            timer.deleteLater()
            then()

    timer.timeout.connect(tick)
    timer.start()


def _after_fetch(holder: Dict[str, Any], remove_obsolete: bool, preview_only: bool) -> None:
    _state["busy"] = False
    mw.progress.finish()
    if "error" in holder:
        showWarning(f"Notion Sync failed:\n{holder['error']}")
        return
    result = holder["result"]

    for w in result.warnings:
        showWarning(f"Notion Sync warning: {w}")

    if preview_only:
        lines = []
        n_rows = 0
        for p in result.pages:
            n_rows += sum(len(t.rows) for t in p.tables)
            lines.append(f"{p.deck_name}")
            for t in p.tables:
                lines.append(f"&nbsp;&nbsp;&nbsp;&bull; {t.title} ({len(t.rows)} rows)")
        showInfo(
            "<b>Notion Sync preview</b><br>"
            + "<br>".join(lines)
            + f"<br><br>Total rows: {n_rows}, media files: {len(result.media_files)}"
        )
        result.cleanup()
        return

    try:
        mw.progress.start(label="Notion Sync: importing cards...", immediate=True)
        stats = sync.apply_sync(mw.col, result, remove_obsolete)
    finally:
        mw.progress.finish()
        result.cleanup()

    n_tables = sum(len(p.tables) for p in result.pages)
    summary = (
        f"<b>Notion Sync finished.</b><br>"
        f"{len(result.pages)} page(s) / {n_tables} table(s)<br>"
        f"Added {stats['added']} &ndash; updated {stats['updated']} "
        f"&ndash; removed {stats['removed']} &ndash; media {stats['media']}"
    )
    mw.reset()
    showInfo(summary)


# --------------------------------------------------------------------- #
# auto sync timer
# --------------------------------------------------------------------- #


def _schedule_autosync() -> None:
    stop_autosync()
    cfg = _config()
    interval_minutes = int(cfg.get("sync_every_minutes") or 0)
    if interval_minutes <= 0:
        return

    def on_tick() -> None:
        if mw.col is not None and not _state["busy"]:
            run_sync(remove_obsolete=False)

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


# --------------------------------------------------------------------- #
# menu
# --------------------------------------------------------------------- #


def _build_menu() -> None:
    menu = QMenu(MENU_TITLE, mw)
    mw.form.menuTools.addMenu(menu)

    act_sync = QAction("Sync Now", mw)
    act_sync.triggered.connect(lambda: run_sync(remove_obsolete=False))
    menu.addAction(act_sync)

    act_sync_rm = QAction("Sync && Remove Obsolete", mw)
    act_sync_rm.triggered.connect(lambda: run_sync(remove_obsolete=True))
    menu.addAction(act_sync_rm)

    act_preview = QAction("Preview Structure", mw)
    act_preview.triggered.connect(lambda: run_sync(preview_only=True))
    menu.addAction(act_preview)


if _IN_ANKI:
    gui_hooks.profile_did_open.append(_schedule_autosync)
    gui_hooks.profile_will_close.append(stop_autosync)
    _build_menu()
