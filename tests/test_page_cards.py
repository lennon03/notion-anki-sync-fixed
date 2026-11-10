# -*- coding: utf-8 -*-
"""Cards written as text in a normal page (headings, Q:/A:, ::, cloze)."""

from notion_sync_addon.pagecards import parse_page, split_spec
from tests.test_htmlify_and_sync import P1

HEX = "0123456789abcdef"


def _rts(text):
    return [
        {
            "type": "text",
            "plain_text": text,
            "text": {"content": text},
            "annotations": {},
        }
    ]


def _block(kind, text, n):
    return {
        "id": f"b{n:03d}".ljust(32, "0"),
        "type": kind,
        "has_children": False,
        kind: {"rich_text": _rts(text)},
    }


def _p(text, n=0):
    return _block("paragraph", text, n)


def _h(level, text, n=0):
    return _block(f"heading_{level}", text, n)


def _li(text, n=0):
    return _block("numbered_list_item", text, n)


DIVIDER = {"id": "d" * 32, "type": "divider", "has_children": False}


def _titles(cards):
    return [
        (c.origin, "".join(r["plain_text"] for r in c.title)) for c in cards
    ]


# --------------------------------------------------------------------- #
# parse_page
# --------------------------------------------------------------------- #


def test_heading_3_and_4_are_questions_and_end_at_the_next_heading():
    cards = parse_page(
        [
            _h(1, "Chapter", 1),
            _h(3, "Why?", 2),
            _p("Because.", 3),
            _p("Really.", 4),
            _h(4, "How?", 5),
            _p("Like so.", 6),
            _h(2, "Other", 7),
            _p("not a card", 8),
        ]
    )
    assert _titles(cards) == [("heading", "Why?"), ("heading", "How?")]
    assert [len(c.blocks) for c in cards] == [2, 1]


def test_heading_without_an_answer_is_just_a_title():
    cards = parse_page([_h(3, "Section", 1), _h(4, "Question?", 2)])
    assert cards == []  # no answer under either
    cards = parse_page(
        [_h(3, "Section", 1), _h(4, "Question?", 2), _p("a", 3)]
    )
    assert _titles(cards) == [("heading", "Question?")]


def test_a_divider_ends_a_heading_answer_but_not_a_hint():
    cards = parse_page([_h(3, "Q", 1), _p("a", 2), DIVIDER, _p("loose", 3)])
    assert len(cards) == 1 and len(cards[0].blocks) == 1
    cards = parse_page(
        [
            _h(3, "Q", 1),
            _p("a", 2),
            _h(4, "Hint", 3),
            _p("h", 4),
            DIVIDER,
            _p("more", 5),
            _h(3, "Next", 6),
            _p("n", 7),
        ]
    )
    assert _titles(cards) == [("heading", "Q"), ("heading", "Next")]
    assert len(cards[0].blocks) == 5


def test_question_and_answer_paragraphs():
    cards = parse_page(
        [
            _p("Q: Capital of France?", 1),
            _p("A: Paris", 2),
            _p("and more", 3),
            _p("", 4),
            _p("question: Two?", 5),
            _p("ANSWER: 2", 6),
        ]
    )
    assert _titles(cards) == [("qa", "Capital of France?"), ("qa", "Two?")]
    first = cards[0].blocks
    assert [b["paragraph"]["rich_text"][0]["plain_text"] for b in first] == [
        "Paris",
        "and more",
    ]


def test_question_and_answer_in_one_paragraph_with_line_breaks():
    block = _p("Q: One?\nA: 1\nQ: Two?\nA: 2", 1)
    cards = parse_page([block])
    assert _titles(cards) == [("qa", "One?"), ("qa", "Two?")]
    assert len({c.source_id for c in cards}) == 2
    assert cards[0].source_id == block["id"]


def test_question_without_answer_is_not_a_card():
    assert parse_page([_p("Q: lonely?", 1), _p("text", 2)]) == []


def test_double_colon_pair_but_not_a_cloze():
    cards = parse_page(
        [
            _p("front :: back", 1),
            _p("The sky is {{c1::blue}}", 2),
            _p("no spaces::here", 3),
        ]
    )
    assert _titles(cards) == [
        ("pair", "front"),
        ("cloze", "The sky is {{c1::blue}}"),
    ]
    assert cards[0].blocks[0]["paragraph"]["rich_text"][0]["plain_text"] == (
        "back"
    )


def test_cloze_in_list_items_quotes_and_headings():
    cards = parse_page(
        [
            _block("bulleted_list_item", "a {{c1::b}}", 1),
            _block("quote", "{{c1::q}}", 2),
            _h(3, "Heading {{c1::cloze}}", 3),
            _p("plain", 4),
            _block("toggle", "toggle {{c1::skipped}}", 5),
        ]
    )
    assert [c.origin for c in cards] == ["cloze", "cloze", "heading"]


def test_a_cloze_heading_without_answer_is_a_cloze_card():
    cards = parse_page([_h(4, "Only {{c1::this}}", 1)])
    assert _titles(cards) == [("cloze", "Only {{c1::this}}")]


def test_split_spec():
    rts = _rts("Steps [2/1 x2]")
    title, spec = split_spec(rts)
    assert spec == "2/1 x2" and title[0]["text"]["content"] == "Steps"
    assert split_spec(_rts("Steps [see page]"))[1] is None
    assert split_spec(_rts("Steps []"))[1] is None
    assert split_spec(_rts("Steps [x3]"))[1] == "x3"
    assert split_spec(_rts("Steps [overlap]"))[1] == "overlap"


# --------------------------------------------------------------------- #
# in a sync
# --------------------------------------------------------------------- #


def _write(env, *blocks):
    env.notion.page_blocks[P1] = list(blocks)
    env.notion.pages[P1]["edited"] = "2026-01-02T00:00:00.000Z"


def _cards(env):
    """Notes of the page's own cards: front -> note."""
    out = {}
    for nid in env.col.find_notes("deck:Hist"):
        note = env.col.get_note(nid)
        out[note.fields[0]] = note
    return out


def _deck(env, note):
    return env.col.decks.name(note.cards()[0].did)


def test_page_cards_become_notes_in_the_pages_deck(env):
    _write(
        env,
        _p("Q: What?", 3),
        _p("A: That.", 4),
        _p("", 7),
        _p("x :: y", 5),
        _p("A {{c1::cloze}} here", 6),
        _h(3, "Why?", 1),
        _p("Because.", 2),
    )
    stats = env.sync()
    assert stats["added"] == 8 + 4
    notes = [
        env.col.get_note(n)
        for n in env.col.find_notes("")
        if env.col.get_note(n).fields[2] in {_b(1), _b(3), _b(5), _b(6)}
    ]
    by_front = {n.fields[0]: n for n in notes}
    assert set(by_front) == {
        "Why?",
        "What?",
        "x",
        "A {{c1::cloze}} here",
    }
    why = by_front["Why?"]
    assert "Because." in why.fields[1]
    assert _deck(env, why) == "Hist"
    cloze = by_front["A {{c1::cloze}} here"]
    assert cloze.note_type()["name"] == "Notion Sync Cloze"


def _b(n):
    return f"b{n:03d}".ljust(32, "0")


def test_numbered_list_answer_is_an_overlapping_cloze(env):
    _write(
        env,
        _h(3, "Steps of mitosis", 1),
        _li("prophase", 2),
        _li("metaphase", 3),
        _li("anaphase", 4),
    )
    env.sync()
    note = next(
        env.col.get_note(n)
        for n in env.col.find_notes("")
        if env.col.get_note(n).fields[2] == _b(1)
    )
    assert note.note_type()["name"] == "Notion Sync Cloze"
    assert "{{c1::prophase}}" in note.fields[0]
    assert "{{c3::anaphase}}" in note.fields[0]
    assert "Steps of mitosis" in note.fields[0]


def test_spec_after_the_question_sets_the_overlap_options(env):
    _write(
        env,
        _h(4, "Lines [1/1]", 1),
        _p("one", 2),
        _p("two", 3),
        _p("three", 4),
    )
    env.sync()
    note = next(
        env.col.get_note(n)
        for n in env.col.find_notes("")
        if env.col.get_note(n).fields[2] == _b(1)
    )
    assert "ns-window" in note.fields[0]
    assert "[1/1]" not in note.fields[0]


def test_struck_text_in_a_page_card_is_a_tag_and_hint_works(env):
    struck = _p("", 3)
    struck["paragraph"]["rich_text"] = [
        {
            "type": "text",
            "plain_text": "geo",
            "text": {"content": "geo"},
            "annotations": {"strikethrough": True},
        }
    ]
    _write(
        env,
        _h(3, "Capital?", 1),
        _p("Paris", 2),
        struck,
        _h(4, "Hint", 4),
        _p("Eiffel", 5),
    )
    env.sync()
    note = next(
        env.col.get_note(n)
        for n in env.col.find_notes("")
        if env.col.get_note(n).fields[2] == _b(1)
    )
    assert note.tags == ["Notion::geo"]
    assert "Paris" in note.fields[1] and "geo" not in note.fields[1]
    assert "<summary>Hint</summary>" in note.fields[1]


def test_unchanged_page_makes_no_download_and_edits_update_one_card(env):
    _write(env, _h(3, "A?", 1), _p("a", 2), _h(3, "B?", 3), _p("b", 4))
    env.sync()
    env.col.add_custom_undo_entry("Previous action")
    env.notion.calls.clear()
    env.sync()
    assert ("blocks", P1) not in env.notion.calls
    assert env.result.changes is None
    assert env.col.undo_status().undo == "Previous action"

    env.notion.page_blocks[P1][1]["paragraph"]["rich_text"] = _rts("a2")
    env.notion.pages[P1]["edited"] = "2026-01-03T00:00:00.000Z"
    stats = env.sync()
    assert stats["updated"] == 1 and stats["added"] == 0
    assert (
        "a2"
        in next(
            n for n in _cards(env).values() if n.fields[2] == _b(1)
        ).fields[1]
    )


def test_a_page_mention_in_a_card_is_checked_again_within_the_hour(env):
    mention = {
        "type": "mention",
        "plain_text": "Other page",
        "mention": {"type": "page", "page": {"id": "f" * 32}},
    }
    answer = _p("see ", 2)
    answer["paragraph"]["rich_text"].append(mention)
    _write(env, _h(3, "A?", 1), answer, _h(3, "B?", 3), _p("b", 4))
    env.sync()
    states = env.result.fetched
    assert states[_b(1)].volatile == 1 and states[_b(3)].volatile == 0


def test_deleting_a_block_removes_its_card_and_edits_keep_its_note(env):
    _write(env, _h(3, "A?", 1), _p("a", 2), _h(3, "B?", 3), _p("b", 4))
    env.sync()
    before = env.count()
    env.notion.page_blocks[P1] = [_h(3, "A?", 1), _p("a", 2)]
    env.notion.pages[P1]["edited"] = "2026-01-03T00:00:00.000Z"
    stats = env.sync()
    assert stats["removed"] == 1 and env.count() == before - 1


def test_page_cards_can_be_turned_off(env):
    _write(env, _h(3, "A?", 1), _p("a", 2))
    env.cfg["page_cards"] = False
    stats = env.sync()
    assert stats["added"] == 8  # the tables' rows only


def test_a_failed_page_read_keeps_existing_page_cards(env):
    _write(env, _h(3, "A?", 1), _p("a", 2))
    env.sync()
    before = env.count()
    env.notion.fail = {("page", P1)}
    env.sync()
    assert env.count() == before


def test_page_cards_of_a_failed_page_cost_no_row_lookup(env):
    """C11: a page card's Source is a block id, so GET /pages can't help."""
    import os

    _write(env, _h(3, "A?", 1), _p("a", 2), _h(3, "B?", 3), _p("b", 4))
    env.sync()
    env.notion.fail = {("page", P1)}
    env.notion.calls.clear()
    env.sync()
    # the cache knows these are the page's cards: nothing is asked
    assert ("page", _b(1)) not in env.notion.calls

    # without the cache (a new device) each is asked once, then not for a day
    os.remove(env.path)
    env.notion.calls.clear()
    env.sync()
    assert ("page", _b(1)) in env.notion.calls
    env.notion.calls.clear()
    env.sync()
    assert ("page", _b(1)) not in env.notion.calls
