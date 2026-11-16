# -*- coding: utf-8 -*-
"""Writing cards in a page: long fronts, short clozes, hidden clozes."""

from notion_sync_addon.htmlify import has_short_cloze, number_short_clozes
from notion_sync_addon.pagecards import parse_page, split_front
from tests.helpers import DB1, _para, _rid
from tests.test_cloze import _kind, _page, _title
from tests.test_page_cards import _b, _h, _li, _p, _rts, _titles, _write

ROW = _rid(0)
CLOZE = "Notion Sync Cloze"


def _text(blocks):
    return [
        "".join(r["text"]["content"] for r in b[b["type"]]["rich_text"])
        for b in blocks
    ]


def _image(n):
    return {
        "id": _b(n),
        "type": "image",
        "has_children": False,
        "image": {
            "type": "external",
            "external": {"url": "https://example.com/a.png"},
            "caption": [],
        },
    }


def _code(text):
    rt = _rts(text)
    rt[0]["annotations"] = {"code": True}
    return rt


# --------------------------------------------------------------------- #
# the front of a card
# --------------------------------------------------------------------- #


def test_back_line_ends_the_front():
    blocks = [_p("a long setup", 1), _p("Back: the answer", 2), _p("more", 3)]
    front, back = split_front(blocks)
    assert _text(front) == ["a long setup"]
    assert _text(back) == ["the answer", "more"]


def test_back_line_alone_is_dropped_and_other_words_work():
    for marker in ("Back:", "Answer :", "Réponse:", "back:"):
        front, back = split_front([_p("x", 1), _p(marker, 2), _p("y", 3)])
        assert (_text(front), _text(back)) == (["x"], ["y"])


def test_a_bare_a_line_or_no_line_leaves_the_front_empty():
    blocks = [_p("A: the set of integers", 1), _p("more", 2)]
    assert split_front(blocks) == ([], blocks)


def test_blocks_between_q_and_a_are_the_front():
    cards = parse_page(
        [
            _p("Q: What does this show?", 1),
            _image(2),
            _p("Look at the axes.", 3),
            _p("A: A parabola.", 4),
            _p("It opens upwards.", 5),
        ]
    )
    assert _titles(cards) == [("qa", "What does this show?")]
    assert [b["type"] for b in cards[0].front or []] == ["image", "paragraph"]
    assert _text(cards[0].blocks) == ["A parabola.", "It opens upwards."]


def test_q_alone_on_its_line_takes_the_next_paragraph_as_question():
    cards = parse_page([_p("Q:", 1), _p("Why?", 2), _p("A: Because.", 3)])
    assert _titles(cards) == [("qa", "Why?")]
    assert cards[0].front == []


def test_a_heading_followed_by_cards_is_a_section_not_a_card():
    cards = parse_page(
        [
            _h(3, "Vocabulary", 1),
            _p("chat :: cat", 2),
            _p("Q: chien?", 3),
            _p("A: dog", 4),
        ]
    )
    assert _titles(cards) == [("pair", "chat"), ("qa", "chien?")]


def test_a_heading_answer_stops_where_another_card_starts():
    cards = parse_page([_h(3, "Why?", 1), _p("Because.", 2), _p("x :: y", 3)])
    assert _titles(cards) == [("heading", "Why?"), ("pair", "x")]
    assert _text(cards[0].blocks) == ["Because."]


def test_a_long_front_under_a_heading_reaches_the_note(env):
    _write(
        env,
        _h(3, "Read this", 1),
        _p("A long text to study.", 2),
        _p("Back: Its summary.", 3),
        _p("And a detail.", 4),
    )
    env.sync()
    front, back, _source = env.note(_b(1)).fields
    assert front.startswith("Read this")
    assert '<div class="ns-front"><p>A long text to study.</p></div>' in front
    assert "Its summary." in back and "And a detail." in back
    assert "A long text" not in back and "Back:" not in back


def test_a_question_with_a_picture_on_the_front(env):
    _write(
        env,
        _p("Q: Name it", 1),
        _p("the thing below", 2),
        _p("A: A thing", 3),
    )
    env.sync()
    front, back, _source = env.note(_b(1)).fields
    assert "Name it" in front and "the thing below" in front
    assert back == "<p>A thing</p>"


def test_a_back_line_in_a_table_row_moves_the_text_above_it(env):
    _page(env, ROW, _para("Long statement."), _para("Back:"), _para("Proof."))
    env.sync()
    front, back, _source = env.note(ROW).fields
    assert '<div class="ns-front"><p>Long statement.</p></div>' in front
    assert back == "<p>Proof.</p>"


def test_a_row_without_a_back_line_keeps_its_short_front(env):
    _page(env, ROW, _para("Just the answer."))
    env.sync()
    front, back, _source = env.note(ROW).fields
    assert "ns-front" not in front
    assert back == "<p>Just the answer.</p>"


# --------------------------------------------------------------------- #
# short clozes: {{x}}
# --------------------------------------------------------------------- #


def test_short_clozes_are_numbered_in_order():
    out, used = number_short_clozes(_rts("{{Paris}} is in {{France}}"), 0)
    assert out[0]["text"]["content"] == "{{c1::Paris}} is in {{c2::France}}"
    assert used == 2


def test_numbered_clozes_and_hints_are_kept():
    out, used = number_short_clozes(_rts("{{c3::a}} {{b::hint}}"), 4)
    assert out[0]["text"]["content"] == "{{c3::a}} {{c5::b::hint}}"
    assert used == 5


def test_a_short_cloze_can_span_formatting_but_not_start_in_code():
    bold = _rts("Par")
    bold[0]["annotations"] = {"bold": True}
    split = _rts("{{") + bold + _rts("is}} rocks")
    out, _used = number_short_clozes(split, 0)
    assert "".join(r["text"]["content"] for r in out) == "{{c1::Paris}} rocks"
    assert out[1]["annotations"] == {"bold": True}
    assert not has_short_cloze(_code("{{ user.name }}"))
    assert not has_short_cloze(_rts("{{ }} and {}"))


def test_a_short_cloze_paragraph_is_a_cloze_card(env):
    _write(env, _p("{{Canberra}} is the capital of {{Australia}}", 1))
    env.sync()
    note = env.note(_b(1))
    assert note.note_type()["name"] == CLOZE
    assert note.fields[0] == (
        "{{c1::Canberra}} is the capital of {{c2::Australia}}"
    )
    assert len(note.cards()) == 2


def test_short_clozes_count_through_a_heading_card(env):
    _write(
        env,
        _h(3, "Capitals of {{Europe}}", 1),
        _p("{{Paris}} for France", 2),
        _p("{{Rome}} for Italy", 3),
    )
    env.sync()
    text, extra, _source = env.note(_b(1)).fields
    assert text == "Capitals of {{c1::Europe}}"
    assert "{{c2::Paris}}" in extra and "{{c3::Rome}}" in extra


def test_a_short_cloze_in_a_row_title_and_page(env):
    _title(env, ROW, "The {{cat}} sat")
    _page(env, ROW, _para("on the {{mat}}"))
    env.sync()
    note = env.note(ROW)
    assert note.note_type()["name"] == CLOZE
    assert note.fields[0] == "The {{c1::cat}} sat"
    assert "{{c2::mat}}" in note.fields[1]


def test_code_blocks_keep_their_double_braces(env):
    code = {
        "id": "c0de",
        "type": "code",
        "has_children": False,
        "code": {"rich_text": _rts("{{ value }}"), "language": "jinja"},
    }
    _page(env, ROW, code)
    env.sync()
    note = env.note(ROW)
    assert note.note_type()["name"] == "Notion Sync Card"
    assert "c1::" not in note.fields[1]


# --------------------------------------------------------------------- #
# hiding the other clozes, and plain cards from a list
# --------------------------------------------------------------------- #


def test_hide_keeps_the_other_clozes_hidden(env):
    _write(
        env,
        _p("{{Paris}} is in {{France}} [hide]", 1),
        _p("{{Rome}} is in {{Italy}}", 2),
    )
    env.sync()
    hidden, shown = env.note(_b(1)).fields[0], env.note(_b(2)).fields[0]
    assert '<div class="ns-hide">' in hidden and "[hide]" not in hidden
    assert ".cloze-inactive" in hidden
    assert "{{c1::Paris}} is in {{c2::France}}" in hidden
    assert shown == "{{c1::Rome}} is in {{c2::Italy}}"


def test_hide_under_a_heading_and_in_the_card_type_column(env):
    _write(env, _h(3, "Capitals [hide]", 1), _p("{{Paris}}, {{Rome}}", 2))
    env.notion.props[ROW] = _kind("Cloze hide")
    _page(env, ROW, _para("{{a}} then {{b}}"))
    env.sync()
    assert "ns-hide" in env.note(_b(1)).fields[0]
    assert "ns-hide" in env.note(ROW).fields[0]


def test_hide_on_a_card_without_cloze_warns_and_is_ignored(env):
    _write(env, _h(3, "Plain [hide]", 1), _p("no cloze here", 2))
    env.sync()
    note = env.note(_b(1))
    assert note.note_type()["name"] == "Notion Sync Card"
    assert "ns-hide" not in note.fields[0]
    assert any("'hide' is for cloze" in w for w in env.result.warnings)


def test_basic_option_keeps_a_numbered_list_as_one_card(env):
    _write(env, _h(3, "Steps [basic]", 1), _li("one", 2), _li("two", 3))
    env.sync()
    note = env.note(_b(1))
    assert note.note_type()["name"] == "Notion Sync Card"
    assert note.fields[0] == "Steps"
    assert "<ol>" in note.fields[1]


def test_table_rows_are_still_read(env):
    assert DB1  # tables stay supported next to page cards
    stats = env.sync()
    assert stats["added"] == 8
