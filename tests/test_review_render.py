# -*- coding: utf-8 -*-
"""Rendering fixes from docs/review.md (C1 to C4)."""

from notion_sync_addon.htmlify import rich_text_to_html
from tests.test_columns import ROW, _page
from tests.test_htmlify_and_sync import DB1, _para
from tests.test_render import Tree, para, render


def test_equations_are_escaped():
    inline = rich_text_to_html(
        [{"type": "equation", "equation": {"expression": "a<b & c>d"}}]
    )
    assert inline == "\\(a&lt;b &amp; c&gt;d\\)"
    block = render(
        [{"id": "e", "type": "equation", "equation": {"expression": "x<y"}}]
    )
    assert "\\[x&lt;y\\]" in block


def test_blocks_indented_under_a_paragraph_are_kept():
    tree = Tree({"p": [para("child", "c")]})
    out = render([para("parent", "p", has_children=True)], tree)
    assert out.index("<p>parent</p>") < out.index("<p>child</p>")
    assert "ns-indent" in out


def test_toggle_heading_keeps_its_content():
    heading = {
        "id": "h",
        "type": "heading_2",
        "has_children": True,
        "heading_2": {"rich_text": [], "is_toggleable": True},
    }
    out = render([heading], Tree({"h": [para("inside", "c")]}))
    assert out == "<h2></h2><p>inside</p>"


def test_table_with_only_a_header_row_is_balanced():
    table = {
        "id": "t",
        "type": "table",
        "has_children": True,
        "table": {"has_column_header": True},
    }
    row = {"type": "table_row", "table_row": {"cells": [[]]}}
    out = render([table], Tree({"t": [row]}))
    assert "</tbody>" not in out
    two = render([table], Tree({"t": [row, row]}))
    assert two.count("<tbody>") == two.count("</tbody>") == 1


def test_row_title_is_escaped_in_the_front_field(env):
    env.notion.tables[DB1]["rows"][ROW][0] = "What is <div> & a < b?"
    _page(env, [_para("answer")])
    env.sync()
    front = env.note(ROW).fields[0]
    assert front == "What is &lt;div&gt; &amp; a &lt; b?"
