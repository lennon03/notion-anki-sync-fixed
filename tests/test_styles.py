# -*- coding: utf-8 -*-
"""Card style presets: CSS, apply_preset and the settings dialog row."""

import pytest

from notion_sync_addon import styles, sync

CODE_VARS = (
    "--ns-code-comment",
    "--ns-code-string",
    "--ns-code-number",
    "--ns-code-keyword",
)


def test_classic_is_todays_model_css():
    assert styles.PRESETS["classic"] == sync.MODEL_CSS
    assert styles.DEFAULT_PRESET == "classic"


@pytest.mark.parametrize("key", ["t3-ocean", "minimal"])
def test_presets_style_cards_code_and_dark_mode(key):
    css = styles.PRESETS[key]
    assert ".card" in css and css.count("{") == css.count("}")
    for var in CODE_VARS:
        assert var in css
    for sel in (".ns-code", ".ns-callout", ".ns-table", ".nightMode"):
        assert sel in css
    assert ".night_mode" in css


def test_labels_and_css_for():
    assert list(styles.PRESET_LABELS) == [
        "classic",
        "t3-ocean",
        "minimal",
        "custom",
    ]
    assert styles.PRESET_LABELS["custom"] == "Custom (keep my CSS)"
    assert styles.css_for("custom") is None
    assert styles.css_for("nope") is None
    assert styles.css_for("minimal") == styles.PRESETS["minimal"]
    for sample in ("ns-code", "ns-callout", "<table", "<details", "<ul"):
        assert sample in styles.SAMPLE_CARD_HTML


def test_apply_preset_is_one_undo_step(tmp_path):
    collection = pytest.importorskip("anki.collection")
    col = collection.Collection(str(tmp_path / "col.anki2"))
    try:
        model = sync._get_or_create_model(col)
        ids = [model["id"]]
        assert styles.apply_preset(col, "custom", ids) is False
        assert col.models.get(ids[0])["css"] == sync.MODEL_CSS

        assert styles.apply_preset(col, "t3-ocean", ids) is True
        assert col.models.get(ids[0])["css"] == styles.PRESETS["t3-ocean"]
        assert col.undo_status().undo == "Notion Sync: Card Style"

        col.undo()  # a single step brings the old CSS back
        assert col.models.get(ids[0])["css"] == sync.MODEL_CSS

        styles.apply_preset(col, "minimal", ids)
        before = col.undo_status().undo
        assert styles.apply_preset(col, "minimal", ids) is False
        assert col.undo_status().undo == before
    finally:
        col.close()


def _dialog(cfg=None, preview=None):
    from notion_sync_addon.settings_ui import SettingsDialog

    return SettingsDialog(
        dict({"notion_token": "t", "notion_pages": []}, **(cfg or {})),
        lambda s: s,
        lambda pid, token: "x",
        lambda c: None,
        lambda: None,
        preview=preview,
    )


def test_dialog_combo_and_config(qapp):
    dialog = _dialog({"card_style": "minimal", "other": 1})
    assert dialog.selected_style() == "minimal"
    assert dialog.card_style.currentText() == "Minimal"
    assert dialog.config()["other"] == 1
    dialog.card_style.setCurrentIndex(dialog.card_style.findData("custom"))
    assert dialog.config()["card_style"] == "custom"
    assert _dialog().config()["card_style"] == "classic"
    assert _dialog({"card_style": "bogus"}).selected_style() == "classic"


def test_dialog_preview_receives_css_and_sample(qapp):
    seen = []
    dialog = _dialog(
        {"card_style": "t3-ocean"}, preview=lambda *a: seen.append(a)
    )
    dialog.preview_button.click()
    parent, html, dark = seen[0]
    assert parent is dialog and dark is False
    assert styles.PRESETS["t3-ocean"] in html
    assert styles.SAMPLE_CARD_HTML in html
    dialog.apply_theme(True)
    dialog.preview_button.click()
    assert seen[1][2] is True and "nightMode" in seen[1][1]


def test_default_preview_does_not_crash(qapp):
    dialog = _dialog()
    dialog.preview_button.click()
    qapp.processEvents()


def test_style_picked_during_a_sync_waits_for_it(addon, env, monkeypatch):
    """No style op runs next to a sync; it holds the close guard too."""
    from tests.test_htmlify_and_sync import _Dialogs

    env.sync()
    mod = addon.mod
    dialogs = _Dialogs()
    monkeypatch.setattr(mod, "_dialog_manager", lambda: dialogs)
    held = []
    real_hold = mod._hold_collection

    def hold():
        real_hold()
        held.append(mod._state["guard"] is not None)

    mod._hold_collection = hold
    try:
        mod._state["busy"] = True  # a sync is running
        mod._apply_card_style("minimal")
        model = env.col.models.by_name("Notion Sync Card")
        assert model["css"] == sync.MODEL_CSS  # not yet
        addon.mw.taskman.defer = True
        mod._finish()
        assert mod._state["pending_style"] is None and addon.queue
        for fn in addon.queue:
            fn()
    finally:
        mod._hold_collection = real_hold
    model = env.col.models.by_name("Notion Sync Card")
    assert model["css"] == styles.PRESETS["minimal"]
    assert env.col.undo_status().undo == "Notion Sync: Card Style"
    assert mod._state["busy"] is False and mod._state["guard"] is None
    assert held == [True]
