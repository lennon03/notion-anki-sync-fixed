# -*- coding: utf-8 -*-
"""Notion Sync addon - official Notion API -> Anki.

Structure created in your collection:
  a deck per configured Notion page -> a subdeck per table
    -> one card per row
"""
from __future__ import annotations

import threading
import traceback
from typing import Any, Dict

from . import sync

try:
    from aqt import gui_hooks, mw
    from aqt.qt import (
        QAction, QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
        QLineEdit, QListWidget, QMessageBox, QPushButton, QSpinBox, QTimer,
        QVBoxLayout,
    )
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


def run_sync() -> None:
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
    _poll_until_fetched(holder, then=lambda: _after_fetch(holder))


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


def _after_fetch(holder: Dict[str, Any]) -> None:
    _state["busy"] = False
    mw.progress.finish()
    if "error" in holder:
        showWarning(f"Notion Sync failed:\n{holder['error']}")
        return
    result = holder["result"]

    for w in result.warnings:
        showWarning(f"Notion Sync warning: {w}")

    try:
        mw.progress.start(label="Notion Sync: importing cards...", immediate=True)
        stats = sync.apply_sync(mw.col, result)
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
    interval_minutes = int(cfg.get("sync_every_minutes") or 0) if cfg.get("autosync") else 0
    if interval_minutes <= 0:
        return

    def on_tick() -> None:
        if mw.col is not None and not _state["busy"]:
            run_sync()

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
# settings dialog and menu
# --------------------------------------------------------------------- #


def _open_settings() -> None:
    cfg = _config()
    dialog = QDialog(mw)
    dialog.setWindowTitle("Notion Sync Settings")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()

    token = QLineEdit(str(cfg.get("notion_token") or ""))
    token.setEchoMode(QLineEdit.EchoMode.Password)
    token.setPlaceholderText("secret_...")
    form.addRow("Notion token:", token)

    autosync = QCheckBox("Enable automatic sync")
    autosync.setChecked(bool(cfg.get("autosync", False)))
    form.addRow("", autosync)

    interval = QSpinBox()
    interval.setRange(1, 10080)
    interval.setSuffix(" minutes")
    interval.setValue(max(1, int(cfg.get("sync_every_minutes") or 60)))
    interval.setEnabled(autosync.isChecked())
    autosync.toggled.connect(interval.setEnabled)
    form.addRow("Sync interval:", interval)
    layout.addLayout(form)

    layout.addWidget(QLabel("Notion pages"))
    pages = QListWidget()
    for entry in cfg.get("notion_pages", []):
        value = entry if isinstance(entry, str) else entry.get("page_id", "")
        if value:
            pages.addItem(str(value))
    layout.addWidget(pages)

    add_row = QHBoxLayout()
    page_input = QLineEdit()
    page_input.setPlaceholderText("Notion page URL or 32-character ID")
    add_button = QPushButton("Add")
    add_row.addWidget(page_input)
    add_row.addWidget(add_button)
    layout.addLayout(add_row)

    def add_page() -> None:
        raw = page_input.text().strip()
        if not raw:
            return
        try:
            sync.canonical_page_id(raw)
        except sync.NotionError as exc:
            QMessageBox.warning(dialog, "Invalid page", str(exc))
            return
        pages.addItem(raw)
        page_input.clear()

    add_button.clicked.connect(add_page)

    buttons = QHBoxLayout()
    save = QPushButton("Save")
    cancel = QPushButton("Cancel")
    buttons.addStretch(1)
    buttons.addWidget(cancel)
    buttons.addWidget(save)
    layout.addLayout(buttons)
    cancel.clicked.connect(dialog.reject)

    def save_config() -> None:
        values = [pages.item(i).text().strip() for i in range(pages.count())]
        if not values:
            QMessageBox.warning(dialog, "Missing pages", "Add at least one Notion page.")
            return
        mw.addonManager.writeConfig(
            __name__,
            {
                "notion_token": token.text().strip(),
                "notion_pages": values,
                "autosync": autosync.isChecked(),
                "sync_every_minutes": interval.value() if autosync.isChecked() else 0,
            },
        )
        _schedule_autosync()
        dialog.accept()

    save.clicked.connect(save_config)
    dialog.exec()


def _build_menu() -> None:
    menu = QMenu(MENU_TITLE, mw)
    mw.form.menuTools.addMenu(menu)

    act_sync = QAction("Sync Now", mw)
    act_sync.triggered.connect(run_sync)
    menu.addAction(act_sync)

    act_settings = QAction("Settings", mw)
    act_settings.triggered.connect(_open_settings)
    menu.addAction(act_settings)


if _IN_ANKI:
    gui_hooks.profile_did_open.append(_schedule_autosync)
    gui_hooks.profile_will_close.append(stop_autosync)
    _build_menu()
