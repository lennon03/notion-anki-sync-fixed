# -*- coding: utf-8 -*-
"""The settings dialog (Qt, offscreen)."""

from notion_sync_addon.sync import canonical_page_id
from tests.helpers import P1


def _dialog(problems=None):
    from notion_sync_addon.settings_ui import SettingsDialog

    cfg = {"notion_token": "t", "notion_pages": [P1, "garbage"]}
    return SettingsDialog(
        cfg,
        canonical_page_id,
        lambda pid, token: "Hist",
        lambda c: None,
        lambda: None,
        problems=problems,
    )


def test_settings_dialog_shows_the_last_sync_problems(qapp):
    from PyQt6.QtWidgets import QLabel

    dialog = _dialog(["Table 'Defs' on 'Hist': network error"])
    texts = [w.text() for w in dialog.problems_card.findChildren(QLabel)]
    assert any("Defs" in t for t in texts)
    assert not hasattr(_dialog(), "problems_card")


def test_settings_dialog_shows_problems_as_plain_text(qapp):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLabel

    dialog = _dialog(["Row '<b>x</b>' failed"])
    items = [
        w
        for w in dialog.problems_card.findChildren(QLabel)
        if w.objectName() == "problemItem"
    ]
    assert items and all(
        w.textFormat() == Qt.TextFormat.PlainText for w in items
    )
    assert "<b>x</b>" in items[0].text()


def test_page_names_are_shown_as_plain_text(qapp):
    from PyQt6.QtCore import Qt

    dialog = _dialog()
    title = dialog._page_rows[P1][1]
    dialog._apply_name(P1, "<b>Hist</b>")
    assert title.textFormat() == Qt.TextFormat.PlainText
    assert title.text() == "<b>Hist</b>"


def test_dialog_opens_with_a_sync_interval_that_is_not_a_number(qapp):
    from notion_sync_addon.settings_ui import SettingsDialog

    cfg = {"notion_pages": [], "sync_every_minutes": "often"}
    dialog = SettingsDialog(
        cfg, canonical_page_id, lambda p, t: "", lambda c: None, lambda: None
    )
    assert dialog.config()["sync_every_minutes"] == 0


def test_page_name_arriving_after_the_dialog_is_gone_is_safe(qapp):
    from notion_sync_addon import settings_ui

    dialog = _dialog()
    signals = settings_ui._name_signals()
    assert dialog._signals is signals  # one emitter, owned by the module
    dialog.deleteLater()
    del dialog
    qapp.processEvents()
    import gc

    gc.collect()
    signals.resolved.emit(P1, "late name")  # no receiver left: nothing happens
    qapp.processEvents()


def test_settings_dialog_keeps_config_keys_it_does_not_show(qapp):
    from notion_sync_addon.settings_ui import SettingsDialog

    cfg = {
        "notion_token": "t",
        "notion_pages": [P1],
        "card_type_column": "Kind",
        "something_new": [1, 2],
    }
    dialog = SettingsDialog(
        cfg,
        canonical_page_id=lambda raw: raw,
        lookup_name=lambda page_id, token: "Page",
        on_save=lambda new_cfg: None,
        on_sync=lambda: None,
    )
    try:
        out = dialog.config()
    finally:
        dialog.deleteLater()
    assert out["card_type_column"] == "Kind"
    assert out["something_new"] == [1, 2]
    assert out["notion_pages"] == [P1]
