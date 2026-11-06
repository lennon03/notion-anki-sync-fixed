# -*- coding: utf-8 -*-
import pytest


def test_close_guard_keeps_only_the_first_callback(monkeypatch):
    addon = pytest.importorskip("notion_sync_addon")
    monkeypatch.setattr(addon, "tooltip", lambda *a, **k: None, raising=False)
    guard = addon._CloseGuard()
    calls = []
    guard.closeWithCallback(lambda: calls.append(1))
    guard.closeWithCallback(lambda: calls.append(2))
    assert len(guard.callbacks) == 1
    guard.callbacks[0]()
    assert calls == [1]
