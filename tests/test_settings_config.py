# -*- coding: utf-8 -*-
from tests.test_htmlify_and_sync import P1


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
