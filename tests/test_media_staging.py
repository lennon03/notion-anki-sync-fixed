"""Media files are copied into Anki's media folder by the fetch step."""

import os

from tests.test_htmlify_and_sync import DB1, _image, _rid


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
