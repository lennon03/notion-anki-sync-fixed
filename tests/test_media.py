"""Media: downloads, staging in Anki's media folder, retries."""

import os
from typing import cast

from notion_sync_addon.htmlify import MediaCollector
from notion_sync_addon.notion_api import NotionClient
from tests.helpers import DB1, OLD, _image, _rid


def _with_images(env, count=2):
    for i in range(count):
        image = _image(f"{i + 1:x}" + "0" * 31, f"pic{i}.png")
        env.notion.tables[DB1]["rows"][_rid(i)][2] = [image]


def _fetch(env, **kwargs):
    prep = env.sync_mod.prepare_sync(env.col, env.path)
    assert prep.media_folder == env.col.media.dir()
    result = env.sync_mod.fetch_pages(
        env.cfg,
        prep.known,
        None,
        prep.structure,
        prep.previous,
        prep.sources,
        media_folder=prep.media_folder,
        **kwargs,
    )
    result.cache_path = env.path
    return result


def _media_files(env):
    return sorted(
        n for n in os.listdir(env.col.media.dir()) if n.endswith(".png")
    )


def _spy_add_file(env, monkeypatch):
    calls = []
    real = env.col.media.add_file

    def spy(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(env.col.media, "add_file", spy)
    return calls


def test_fetch_stages_new_files_and_apply_skips_them(env, monkeypatch):
    _with_images(env)
    calls = _spy_add_file(env, monkeypatch)
    result = _fetch(env)
    staged = _media_files(env)
    assert len(staged) == 2
    assert not [n for n in os.listdir(env.col.media.dir()) if "tmp" in n]
    env.sync_mod.apply_sync(env.col, result)
    assert calls == []
    for i in range(2):
        back = env.note(_rid(i))["Back"]
        name = back.split('src="')[1].split('"')[0]
        assert name in staged


def test_should_stop_prevents_staging_but_sync_completes(env, monkeypatch):
    _with_images(env)
    calls = _spy_add_file(env, monkeypatch)
    result = _fetch(env, should_stop=lambda: True)
    assert _media_files(env) == []
    env.sync_mod.apply_sync(env.col, result)
    assert len(calls) == 2
    assert len(_media_files(env)) == 2
    assert env.count() == 8


def test_same_name_other_bytes_is_not_overwritten(env, monkeypatch):
    _with_images(env, 1)
    env.sync()
    name = env.note(_rid(0))["Back"].split('src="')[1].split('"')[0]
    path = os.path.join(env.col.media.dir(), name)
    with open(path, "wb") as f:
        f.write(b"different")
    # the row changes so its media is fetched again
    env.notion.tables[DB1]["rows"][_rid(0)][1] = "2026-01-05T00:00:00.000Z"
    calls = _spy_add_file(env, monkeypatch)
    result = _fetch(env)
    with open(path, "rb") as f:
        assert f.read() == b"different"
    env.sync_mod.apply_sync(env.col, result)
    assert len(calls) == 1
    new = env.note(_rid(0))["Back"].split('src="')[1].split('"')[0]
    assert new != name
    assert os.path.exists(os.path.join(env.col.media.dir(), new))


def test_unwritable_media_folder_does_not_fail_the_sync(env, tmp_path):
    _with_images(env)
    missing = str(tmp_path / "nowhere" / "collection.media")
    prep = env.sync_mod.prepare_sync(env.col, env.path)
    result = env.sync_mod.fetch_pages(
        env.cfg,
        prep.known,
        None,
        prep.structure,
        prep.previous,
        prep.sources,
        media_folder=missing,
    )
    result.cache_path = env.path
    env.sync_mod.apply_sync(env.col, result)
    assert not os.path.exists(missing)
    assert env.count() == 8 and len(_media_files(env)) == 2


def test_addon_end_to_end_stages_media(addon, env, monkeypatch):
    _with_images(env)
    calls = _spy_add_file(env, monkeypatch)
    addon.mod.run_sync(manual=True)
    assert env.count() == 8
    assert calls == []
    assert len(_media_files(env)) == 2


def test_a_media_file_that_fails_midway_fails_alone(tmp_path):
    """A download or write that fails part way fails that one file: its
    partial file is removed and the other files still arrive."""
    from notion_sync_addon.sync import _download_media

    class Client:
        def download_file(self, url, path):
            with open(path, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n" + url.encode())
                if url == "u-bad":
                    raise OSError("No space left on device")

    media = MediaCollector()
    media.add("u-bad", "bad.png")
    media.add("u-good", "good")
    out, failed = _download_media(
        cast(NotionClient, Client()), media, str(tmp_path)
    )
    assert failed == {"bad.png"}
    assert out == [(str(tmp_path / "good.png"), "good.png")]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["good.png"]


def test_failed_media_is_written_but_retried(env):
    env.sync()
    image = {
        "id": "1a2b" + "0" * 28,
        "type": "image",
        "has_children": False,
        "image": {
            "type": "file",
            "file": {"url": "https://s3/pic.png?x=1"},
            "caption": [],
        },
    }
    env.notion.tables[DB1]["rows"][_rid(0)] = [
        "h0",
        "2026-01-05T00:00:00.000Z",
        [image],
    ]
    env.notion.media_fail = True
    stats = env.sync()
    assert stats["updated"] == 1 and not env.result.incomplete
    assert "missing media" in env.note(_rid(0))["Back"]
    assert any("media" in w for w in env.result.warnings)
    env.notion.media_fail = False
    env.notion.downloads.clear()
    stats = env.sync()
    assert env.notion.downloads == [_rid(0)] and stats["updated"] == 1
    assert "missing media" not in env.note(_rid(0))["Back"]
    env.notion.downloads.clear()
    env.sync()
    assert env.notion.downloads == []


def test_media_from_an_earlier_sync_is_reused_when_download_fails(env):
    image = {
        "id": "1a2b" + "0" * 28,
        "type": "image",
        "has_children": False,
        "image": {
            "type": "file",
            "file": {"url": "https://s3/pic.png?x=1"},
            "caption": [],
        },
    }
    env.notion.tables[DB1]["rows"][_rid(0)] = ["h0", OLD, [image]]
    env.sync()
    back = env.note(_rid(0))["Back"]
    assert "missing media" not in back
    env.notion.tables[DB1]["rows"][_rid(0)][1] = "2026-01-05T00:00:00.000Z"
    env.notion.media_fail = True
    env.sync()
    assert env.note(_rid(0))["Back"] == back


def test_changed_media_under_the_same_name_is_imported(env):
    """N6: identical files skip Anki's import; a changed file still goes in."""
    image = _image("1a2b" + "0" * 28, "pic.png")
    env.notion.tables[DB1]["rows"][_rid(0)][2] = [image]
    env.sync()
    first = env.note(_rid(0))["Back"]
    image["image"]["file"]["url"] = image["image"]["file"]["url"].replace(
        "sig=1", "sig=2"
    )
    env.notion.tables[DB1]["rows"][_rid(0)][1] = "2026-01-05T00:00:00.000Z"
    assert env.sync()["updated"] == 1
    second = env.note(_rid(0))["Back"]
    assert second != first
    for back in (first, second):
        name = back.split('src="')[1].split('"')[0]
        assert os.path.exists(os.path.join(env.col.media.dir(), name))
