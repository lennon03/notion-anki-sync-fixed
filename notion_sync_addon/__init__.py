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
        QAction, QCheckBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout,
        QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
        QSpinBox, QTimer, QVBoxLayout, QLineEdit,
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
    interval_minutes = int(cfg.get("sync_every_minutes") or 0)
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


def _open_settings_legacy() -> None:
    cfg = _config()
    dialog = QDialog(mw)
    dialog.setWindowTitle("Notion Sync Settings")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()

    token = QLineEdit(str(cfg.get("notion_token") or ""))
    token.setEchoMode(QLineEdit.EchoMode.Password)
    token.setPlaceholderText("secret_...")
    form.addRow("Notion token:", token)

    interval = QSpinBox()
    interval.setRange(0, 10080)
    interval.setSuffix(" minutes")
    interval.setSpecialValueText("Off")
    interval.setValue(max(0, int(cfg.get("sync_every_minutes") or 0)))
    form.addRow("Auto-sync interval:", interval)
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
                "sync_every_minutes": interval.value(),
            },
        )
        _schedule_autosync()
        dialog.accept()

    save.clicked.connect(save_config)
    dialog.exec()


def _open_settings() -> None:
    cfg = _config()
    dialog = QDialog(mw)
    dialog.setWindowTitle("Notion Sync Settings")
    dialog.setMinimumWidth(560)
    dialog.setStyleSheet(
        """
        QDialog { background: #f7f8fb; color: #242733; }
        QLabel { color: #242733; }
        QGroupBox {
            background: #ffffff; border: 1px solid #e1e4ec;
            border-radius: 12px; margin-top: 12px; padding: 14px;
            font-weight: 600;
        }
        QGroupBox::title {
            subcontrol-origin: margin; left: 14px; padding: 0 6px;
            color: #596174;
        }
        QLineEdit, QListWidget, QSpinBox {
            background: #ffffff; border: 1px solid #d6dae5;
            border-radius: 8px; padding: 8px; color: #242733;
        }
        QLineEdit:focus, QListWidget:focus, QSpinBox:focus {
            border: 1px solid #6d63d8;
        }
        QListWidget::item { padding: 8px; border-radius: 6px; }
        QListWidget::item:selected { background: #ebe9ff; color: #37317d; }
        QCheckBox { spacing: 9px; color: #242733; padding: 5px 0; }
        QCheckBox::indicator {
            width: 18px; height: 18px; border-radius: 5px;
            border: 1px solid #b8bdca; background: #ffffff;
        }
        QCheckBox::indicator:checked {
            background: #665bd3; border-color: #665bd3;
        }
        QPushButton {
            background: #ffffff; border: 1px solid #d6dae5;
            border-radius: 8px; padding: 8px 15px; color: #34394a;
        }
        QPushButton:hover { background: #f0efff; border-color: #9c96e8; }
        QPushButton:pressed { background: #e4e1ff; }
        QPushButton#primary {
            background: #665bd3; border-color: #665bd3;
            color: #ffffff; font-weight: 600;
        }
        QPushButton#primary:hover { background: #574cc5; }
        """
    )
    layout = QVBoxLayout(dialog)
    layout.setSpacing(12)

    intro = QLabel(
        "<h2>Notion Sync</h2>"
        "<p>Choose the pages to sync and how often Anki should check Notion.</p>"
    )
    intro.setWordWrap(True)
    layout.addWidget(intro)

    connection = QGroupBox("Connection")
    connection_form = QFormLayout(connection)
    token = QLineEdit(str(cfg.get("notion_token") or ""))
    token.setEchoMode(QLineEdit.EchoMode.Password)
    token.setPlaceholderText("Paste your Notion integration token")
    token.setClearButtonEnabled(True)
    connection_form.addRow("Notion token", token)
    layout.addWidget(connection)

    behavior = QGroupBox("Sync behavior")
    behavior_form = QFormLayout(behavior)
    interval = QSpinBox()
    interval.setRange(0, 10080)
    interval.setSuffix(" min")
    interval.setSpecialValueText("Off")
    interval.setValue(max(0, int(cfg.get("sync_every_minutes") or 0)))
    autosync = QCheckBox("Enable automatic sync")
    autosync.setChecked(interval.value() > 0)
    behavior_form.addRow("", autosync)
    behavior_form.addRow("Auto-sync interval", interval)
    last_interval = {"value": interval.value() or 60}

    def toggle_autosync(enabled: bool) -> None:
        if enabled:
            interval.setValue(last_interval["value"])
        else:
            if interval.value() > 0:
                last_interval["value"] = interval.value()
            interval.setValue(0)
        interval.setEnabled(enabled)

    def remember_interval(value: int) -> None:
        if value > 0:
            last_interval["value"] = value

    interval.valueChanged.connect(remember_interval)
    autosync.toggled.connect(toggle_autosync)
    interval.setEnabled(autosync.isChecked())
    empty_cards = QCheckBox("Add rows without a title as empty cards")
    empty_cards.setChecked(bool(cfg.get("include_empty_cards", True)))
    empty_cards.setToolTip(
        "When enabled, rows with no title still become cards with an empty front."
    )
    behavior_form.addRow("", empty_cards)
    layout.addWidget(behavior)

    pages_group = QGroupBox("Notion pages")
    pages_layout = QVBoxLayout(pages_group)
    hint = QLabel("Add a page URL or its 32-character ID.")
    hint.setStyleSheet("color: palette(mid);")
    pages_layout.addWidget(hint)
    pages = QListWidget()
    pages.setMinimumHeight(130)
    pages.setAlternatingRowColors(True)
    pages.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
    for entry in cfg.get("notion_pages", []):
        value = entry if isinstance(entry, str) else entry.get("page_id", "")
        if value:
            pages.addItem(QListWidgetItem(str(value)))
    pages_layout.addWidget(pages)

    add_row = QHBoxLayout()
    page_input = QLineEdit()
    page_input.setPlaceholderText("https://app.notion.com/p/... or page ID")
    add_button = QPushButton("Add page")
    add_button.setDefault(True)
    add_row.addWidget(page_input)
    add_row.addWidget(add_button)
    pages_layout.addLayout(add_row)

    page_actions = QHBoxLayout()
    remove_button = QPushButton("Remove selected")
    remove_button.setText("Remove selected")
    remove_button.setEnabled(False)
    page_actions.addWidget(remove_button)
    page_actions.addStretch(1)
    page_count = QLabel()
    page_actions.addWidget(page_count)
    pages_layout.addLayout(page_actions)
    layout.addWidget(pages_group)

    def update_page_state() -> None:
        page_count.setText(f"{pages.count()} page(s)")
        remove_button.setEnabled(pages.currentItem() is not None)

    pages.itemSelectionChanged.connect(update_page_state)

    def remove_page() -> None:
        row = pages.currentRow()
        if row >= 0:
            pages.takeItem(row)
            update_page_state()

    remove_button.clicked.connect(remove_page)

    def add_page() -> None:
        raw = page_input.text().strip()
        if not raw:
            return
        try:
            page_id = sync.canonical_page_id(raw)
        except sync.NotionError as exc:
            QMessageBox.warning(dialog, "Invalid page", str(exc))
            return
        existing_ids = {
            sync.canonical_page_id(pages.item(i).text())
            for i in range(pages.count())
        }
        if page_id in existing_ids:
            QMessageBox.information(dialog, "Already added", "That page is already in the list.")
            return
        pages.addItem(QListWidgetItem(raw))
        page_input.clear()
        update_page_state()

    add_button.clicked.connect(add_page)
    page_input.returnPressed.connect(add_page)
    update_page_state()

    buttons = QHBoxLayout()
    save = QPushButton("Save settings")
    save.setObjectName("primary")
    cancel = QPushButton("Cancel")
    save.setDefault(True)
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
                "include_empty_cards": empty_cards.isChecked(),
                "sync_every_minutes": interval.value(),
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
