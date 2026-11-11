# -*- coding: utf-8 -*-
"""``Overlapping`` Card type options: ``Overlapping 2/1 x2``."""

import re

import pytest

from notion_sync_addon.sync import OverlapSpec, _row_fields, parse_overlap
from tests.helpers import _para
from tests.test_cloze import ROW, _kind, _page
from tests.test_columns import _assert_noop, _edit

WORDS = ("one", "two", "three", "four", "five", "six")


def _setup(env, card_type, words=WORDS):
    _page(env, ROW, *[_para(w) for w in words])
    env.notion.props[ROW] = _kind(card_type)
    env.sync()
    return env.note(ROW)


def _shown_words(note, ord_, before, after):
    """Apply the window rules by hand to a card's question HTML."""
    question = note.cards()[ord_].question()
    lines = re.findall(r'<div class="ns-line">(.*?)</div>', question)
    active = next(i for i, ln in enumerate(lines) if 'class="cloze"' in ln)
    keep = range(
        max(active - before, 0), min(active + after, len(lines) - 1) + 1
    )
    return [i for i in range(len(lines)) if i in keep], active


# ---------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    "text, expected",
    [
        ("overlapping", OverlapSpec()),
        ("overlapping 2/1", OverlapSpec(2, 1, 1)),
        ("overlapping 0/0", OverlapSpec(0, 0, 1)),
        ("overlapping 2 / 1", OverlapSpec(2, 1, 1)),
        ("overlapping x2", OverlapSpec(None, 0, 2)),
        ("overlapping 2/1 x3", OverlapSpec(2, 1, 3)),
        ("overlapping 2/1 x 3", OverlapSpec(2, 1, 3)),
        ("overlapping 9/9 x20", OverlapSpec(9, 9, 20)),
        ("overlapping 2", None),
        ("overlapping 2/", None),
        ("overlapping x0", None),
        ("overlapping x21", None),
        ("overlapping 10/1", None),
        ("overlapping 1/10", None),
        ("overlapping 2/1 x2 x2", None),
        ("overlapping x2 2/1", None),
        ("overlapping hello", None),
        ("overlapping 2-1", None),
    ],
)
def test_parse_overlap(text, expected):
    assert parse_overlap(text) == expected


# -------------------------------------------------- unchanged plain output


def test_plain_overlapping_output_is_unchanged(env):
    note = _setup(env, "Overlapping", WORDS[:3])
    text = note.fields[0]
    assert text == (
        '<div class="ns-title">h0</div><div class="ns-overlap">'
        '<div class="ns-line">{{c1::one}}</div>'
        '<div class="ns-line">{{c2::two}}</div>'
        '<div class="ns-line">{{c3::three}}</div></div>'
    )
    assert "<style>" not in text
    assert not env.result.warnings


def test_x1_and_bare_spec_equal_plain(env):
    plain = _setup(env, "Overlapping", WORDS[:3]).fields[0]
    env.notion.props[ROW] = _kind("Overlapping x1")
    _page(env, ROW, *[_para(w) for w in WORDS[:3]])
    _edit(env, ROW)
    env.sync()
    assert env.note(ROW).fields[0] == plain


# ---------------------------------------------------------------- windows


def test_window_marks_wrapper_and_style(env):
    note = _setup(env, "Overlapping 2/1")
    text = note.fields[0]
    assert text.startswith("<style>")
    assert '<div class="ns-window">' in text
    assert "ns-overlap" not in text  # the old template rule must not apply
    assert len(note.cards()) == 6
    style = text[: text.index("</style>")]
    assert ".ns-q .ns-window .ns-line { display: none; }" in style
    assert ".ns-line:has(.cloze) { display: block; }" in style
    # 1 and 2 lines before, 1 after
    assert ":has(+ .ns-line:has(.cloze))" in style
    assert ":has(+ .ns-line + .ns-line:has(.cloze))" in style
    assert ":has(.cloze) + .ns-line {" in style
    assert ":has(.cloze) + .ns-line + .ns-line {" not in style
    assert not env.result.warnings


def test_window_cards_render_the_active_line_as_a_cloze(env):
    note = _setup(env, "Overlapping 2/1")
    for ord_, card in enumerate(note.cards()):
        question = card.question()
        answer = card.answer()
        lines = re.findall(r'<div class="ns-line">(.*?)</div>', question)
        assert len(lines) == 6
        # exactly the asked line is a cloze on the question; the others are
        # plain (the CSS hides the ones outside the window)
        clozes = [i for i, ln in enumerate(lines) if "[...]" in ln]
        assert clozes == [ord_]
        for i, ln in enumerate(lines):
            if i != ord_:
                assert WORDS[i] in ln
        assert WORDS[ord_] in answer and "[...]" not in answer
        assert "ns-window" in question and "ns-window" in answer
        # the back is not styled: the style rules only target .ns-q
        assert ".ns-q .ns-window" in question
        assert '<div class="ns-a">' in answer


def test_window_visible_lines_by_rule(env):
    note = _setup(env, "Overlapping 2/1")
    shown, active = _shown_words(note, 3, 2, 1)
    assert shown == [1, 2, 3, 4] and active == 3
    shown, active = _shown_words(note, 0, 2, 1)
    assert shown == [0, 1]


def test_zero_window_shows_only_the_asked_line(env):
    note = _setup(env, "Overlapping 0/0")
    style = note.fields[0][: note.fields[0].index("</style>")]
    assert style.count("display: block") == 1
    assert len(note.cards()) == 6


# --------------------------------------------------------------- grouping


def test_groups_make_one_cloze_per_group(env):
    note = _setup(env, "Overlapping 2/1 x2")
    text = note.fields[0]
    assert text.count('<div class="ns-line">') == 3
    assert "{{c1::one<br>two}}" in text
    assert "{{c2::three<br>four}}" in text
    assert "{{c3::five<br>six}}" in text
    assert len(note.cards()) == 3
    question = note.cards()[1].question()
    assert "[...]" in question and "one" in question


def test_last_group_may_be_smaller(env):
    note = _setup(env, "Overlapping x4")
    text = note.fields[0]
    assert "{{c1::one<br>two<br>three<br>four}}" in text
    assert "{{c2::five<br>six}}" in text
    assert "<style>" not in text  # no window: the template rule is used
    assert 'class="ns-overlap"' in text
    assert len(note.cards()) == 2


# ----------------------------------------------------- invalid -> warning


@pytest.mark.parametrize(
    "bad",
    ["Overlapping 2", "Overlapping x0", "Overlapping 12/1", "Overlapping foo"],
)
def test_invalid_spec_falls_back_to_plain_with_a_warning(env, bad):
    plain = _setup(env, "Overlapping", WORDS[:3]).fields[0]
    # a fresh row with the invalid spec
    env.col.remove_notes(env.col.find_notes(f"Source:{ROW}"))
    env.notion.props[ROW] = _kind(bad)
    _edit(env, ROW)
    env.sync()
    assert env.note(ROW).fields[0] == plain
    assert any(
        "not understood" in w and bad.lower() in w for w in env.result.warnings
    )


# ------------------------------------------------ cache / change handling


def test_changing_the_spec_updates_the_note_and_noop_after(env):
    note = _setup(env, "Overlapping 1/1")
    assert "ns-window" in note.fields[0]
    env.notion.props[ROW] = _kind("Overlapping")
    _edit(env, ROW)
    env.sync()
    assert 'class="ns-overlap"' in env.note(ROW).fields[0]
    assert len(env.col.find_notes(f"Source:{ROW}")) == 1
    env.notion.downloads.clear()
    _assert_noop(env, env.sync(), 8)


def test_row_fields_media_and_guarding():
    from notion_sync_addon.sync import RowCard

    row = RowCard(
        source_id="s",
        front="t",
        back_html="",
        url="",
        kind="overlapping",
        lines=["a}}", "b::c"],
        overlap=OverlapSpec(1, 0, 2),
    )
    text = _row_fields(row, {})[0]
    assert "{{c1::a}&#125;<br>b:&#58;c}}" in text


# ------------------------------------------- multi-id clozes, investigated


def _ids_fields(n, window):
    """What a ``{{c1,2::...}}`` encoding of the same hiding would be."""
    before, after = window
    lines = []
    for j in range(n):
        ids = [j + 1] + [
            k + 1 for k in range(n) if not (k - before <= j <= k + after)
        ]
        lines.append(
            '<div class="ns-line">{{c%s::%s}}</div>'
            % (",".join(str(i) for i in sorted(set(ids))), WORDS[j])
        )
    return "".join(lines)


def test_multi_id_clozes_cannot_replace_the_css(env):
    """{{c1,2::...}} hides a line on a card only as "[...]": the hidden lines
    stay on the page (as many "[...]") and the active line cannot be told
    from them, and on the back every hidden line is highlighted too. The
    CSS keeps the behaviour; this documents why multi-id is not used."""
    col = env.col
    _setup(env, "Overlapping", WORDS[:2])
    model = env.col.models.by_name("Notion Sync Cloze")
    note = col.new_note(model)
    note["Text"] = _ids_fields(6, (1, 1))
    note["Source"] = "multi-id-experiment"
    col.add_note(note, env.note(ROW).cards()[0].did)
    cards = note.cards()
    assert len(cards) == 6
    q = cards[3].question()
    # lines 0, 1 and 5 are outside the window of card 3 -> also "[...]"
    assert q.count("[...]") == 4
    # the hidden lines are still on the page, and each cloze span even
    # carries its text in a data attribute
    assert q.count('<div class="ns-line">') == 6
    assert 'data-cloze="one"' in q
    a = cards[3].answer()
    assert a.count('class="cloze"') == 4  # the back highlights all four
