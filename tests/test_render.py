# -*- coding: utf-8 -*-
"""Notion blocks to card HTML: rich text, blocks, media names, code."""

import html
import re

import pytest

from notion_sync_addon.highlight import highlight, language_slug
from notion_sync_addon.htmlify import (
    MediaCollector,
    render_blocks,
    rich_text_to_html,
)
from tests.helpers import FakeTree, _image, _rt


class Boom(Exception):
    def __init__(self, transient):
        super().__init__("boom")
        self.transient = transient
        self.status = None if transient else 404


class Tree:
    def __init__(self, mapping=None, fail=None):
        self.mapping = mapping or {}
        self.fail = fail or {}
        self.asked = []

    def children(self, block_id):
        self.asked.append(block_id)
        if block_id in self.fail:
            raise self.fail[block_id]
        return self.mapping.get(block_id, [])


def rt(text):
    return {
        "type": "text",
        "plain_text": text,
        "text": {"content": text, "link": None},
        "annotations": {},
    }


def para(text, bid="p", has_children=False):
    return {
        "id": bid,
        "type": "paragraph",
        "has_children": has_children,
        "paragraph": {"rich_text": [rt(text)]},
    }


def render(blocks, tree=None):
    return render_blocks(blocks, MediaCollector(), tree or Tree())


def synced(bid, source=None, has_children=True):
    return {
        "id": bid,
        "type": "synced_block",
        "has_children": has_children,
        "synced_block": {
            "synced_from": {"block_id": source} if source else None
        },
    }


def test_synced_original_renders_own_children():
    tree = Tree({"s1": [para("hello")]})
    assert "<p>hello</p>" in render([synced("s1")], tree)


def test_synced_copy_renders_original_children():
    tree = Tree({"orig": [para("from original")]})
    out = render([synced("copy", "orig")], tree)
    assert "from original" in out
    assert tree.asked == ["orig"]


def test_synced_copy_not_shared_gives_note():
    tree = Tree(fail={"orig": Boom(False)})
    out = render([synced("copy", "orig")], tree)
    assert 'class="ns-note"' in out
    assert "not shared" in out


def test_synced_copy_transient_error_propagates():
    tree = Tree(fail={"orig": Boom(True)})
    with pytest.raises(Boom):
        render([synced("copy", "orig")], tree)


def test_columns_side_by_side_with_nested_content():
    blocks = [
        {
            "id": "cl",
            "type": "column_list",
            "has_children": True,
            "column_list": {},
        }
    ]
    tree = Tree(
        {
            "cl": [
                {
                    "id": "c1",
                    "type": "column",
                    "has_children": True,
                    "column": {},
                },
                {
                    "id": "c2",
                    "type": "column",
                    "has_children": True,
                    "column": {},
                },
            ],
            "c1": [
                {
                    "id": "a",
                    "type": "toggle",
                    "has_children": True,
                    "toggle": {"rich_text": [rt("left")]},
                }
            ],
            "a": [para("nested left")],
            "c2": [para("right")],
        }
    )
    out = render(blocks, tree)
    assert out.count('class="ns-column"') == 2
    assert "display:flex" in out
    assert out.index("left") < out.index("right")
    assert "nested left" in out


def test_link_to_page_and_database():
    page = {
        "id": "x",
        "type": "link_to_page",
        "link_to_page": {"type": "page_id", "page_id": "ab-cd-12"},
    }
    db = {
        "id": "y",
        "type": "link_to_page",
        "link_to_page": {"type": "database_id", "database_id": "ef-34"},
    }
    tree = Tree()
    out = render([page, db], tree)
    assert 'href="https://www.notion.so/abcd12"' in out
    assert 'href="https://www.notion.so/ef34"' in out
    assert tree.asked == []


def test_child_page_is_link_and_never_fetched():
    block = {
        "id": "aa-bb",
        "type": "child_page",
        "has_children": True,
        "child_page": {"title": "My <page>"},
    }
    tree = Tree(fail={"aa-bb": AssertionError("must not fetch")})
    out = render([block], tree)
    assert 'href="https://www.notion.so/aabb"' in out
    assert "My &lt;page&gt;" in out
    assert tree.asked == []


def code_block(code, lang):
    return {
        "id": "c",
        "type": "code",
        "code": {"rich_text": [rt(code)], "language": lang},
    }


def test_code_block_highlighted_with_language_class():
    out = render([code_block('def f(): # hi\n    return "x"', "python")])
    assert '<pre class="ns-code"' in out
    assert "white-space:pre-wrap" in out
    assert '<code class="language-python">' in out
    assert ">def</span>" in out
    assert ">return</span>" in out
    assert ">&quot;x&quot;</span>" in out or ">&quot;x&quot;" in out
    assert "# hi</span>" in out


def test_code_injection_is_escaped():
    out = render([code_block("</code><script>alert(1)</script>", "html")])
    assert "<script>" not in out
    assert "&lt;/code&gt;" in out or "&lt;" in out
    out = render([code_block("</code><script>x</script>", "plain text")])
    assert "<script>" not in out
    assert "&lt;/code&gt;&lt;script&gt;" in out


def test_unknown_language_has_no_spans():
    out = highlight("def x(): return 1  # c", "plain text")
    assert "<span" not in out
    assert "<span" not in highlight("if x", "brainfuck")


def test_language_slug():
    assert language_slug("C++") == "cpp"
    assert language_slug("c#") == "csharp"
    assert language_slug("Plain Text") == "plain-text"
    assert language_slug("") == "plain-text"


SNIPPETS = {
    "python": 'import os\n"""doc\nstring"""\nx = 0x1F + 3.5e2 # c\n'
    "s = \"a\\\"b\" + 'c'\nprint(f'<{x}>' & y)\n",
    "javascript": "const a = `t ${1}`; // c\n/* m\nl */ let b = 'x\\'y'",
    "typescript": "interface A { x: number } // <T>\n",
    "java": 'public class A { String s = "x"; /* c */ int i = 10; }',
    "c": '#include <stdio.h>\nint main(){ char c = \'a\'; return 0; } //x',
    "c++": "template<typename T> auto f(T&& t) -> bool { return t < 3; }",
    "c#": 'var s = "a" + 1; // done',
    "go": 'func main() { fmt.Println(`raw`, "s", 42) }',
    "rust": "fn f<'a>(x: &'a str) -> char { 'c' } // lifetimes\n'\\n'",
    "sql": "SELECT * FROM t WHERE a = 'x' -- c\n/* b */ AND b > 10;",
    "bash": "echo \"$HOME\" 'lit' # comment\nif [ 1 ]; then ls; fi",
    "json": '{"a": [1, 2.5, true, null], "b": "x\\"y"}',
    "html": '<!-- c --><div class="a" id=\'b\'>x &amp; <b>y</b></div><br/>',
    "css": "a { color: #fff; margin: 10px; } /* c */ b::after{content:'x'}",
    "markdown": "# Title\n*x* <b>",
    "weird": "\r\n\t  unterminated 'string and \"quote\n/* open",
    "python ": "'''unterminated\n",
}


@pytest.mark.parametrize("lang", sorted(SNIPPETS))
def test_highlight_round_trips(lang):
    code = SNIPPETS[lang]
    out = highlight(code, lang)
    plain = html.unescape(re.sub(r"<[^>]+>", "", out))
    assert plain == code


def test_highlight_round_trips_on_noisy_input():
    chars = "ab1 \n'\"`#/*-<>&{}\\\t$.e0x"
    import random

    rnd = random.Random(7)
    for lang in (
        "python",
        "javascript",
        "rust",
        "sql",
        "html",
        "css",
        "bash",
        "go",
        "json",
        "c++",
    ):
        for _ in range(200):
            code = "".join(rnd.choice(chars) for _ in range(40))
            out = highlight(code, lang)
            plain = html.unescape(re.sub(r"<[^>]+>", "", out))
            assert plain == code, (lang, code)


def test_tokens_get_expected_colors():
    out = highlight("if x: # c\n  y = 'a' + 1", "python")
    assert "--ns-code-keyword, #d73a49" in out
    assert "--ns-code-comment, #6a737d" in out
    assert "--ns-code-string, #032f62" in out
    assert "--ns-code-number, #005cc5" in out


def test_inline_rich_text_annotations():
    payload = [
        {
            "type": "text",
            "plain_text": "Hello",
            "text": {"content": "Hello", "link": None},
            "annotations": {
                "bold": True,
                "italic": True,
                "strikethrough": False,
                "underline": True,
                "code": False,
                "color": "default",
            },
        },
        {
            "type": "equation",
            "plain_text": "1+1",
            "equation": {"expression": "F=ma"},
        },
        {
            "type": "text",
            "plain_text": "x",
            "text": {"content": "x", "link": {"url": "https://example.com"}},
            "annotations": {
                "bold": False,
                "italic": False,
                "strikethrough": False,
                "underline": False,
                "code": False,
                "color": "default",
            },
        },
    ]
    out = render_blocks(
        [
            {
                "type": "paragraph",
                "has_children": False,
                "paragraph": {"rich_text": payload},
                "id": "b1",
            }
        ],
        MediaCollector(),
        FakeTree(),
    )
    assert "<u><em><b>Hello</b></em></u>" in out
    assert "\\(F=ma\\)" in out
    assert '<a href="https://example.com">x</a>' in out


def test_code_block_and_math_block():
    out = render_blocks(
        [
            {
                "type": "code",
                "has_children": False,
                "id": "c1",
                "code": {
                    "rich_text": [
                        {
                            "type": "text",
                            "plain_text": "def f():\n  return <tag>",
                            "text": {"content": "def f():\n  return <tag>"},
                            "annotations": {},
                        }
                    ],
                    "language": "python",
                },
            },
            {
                "type": "equation",
                "has_children": False,
                "id": "e1",
                "equation": {"expression": "e^{i\\pi}+1=0"},
            },
        ],
        MediaCollector(),
        FakeTree(),
    )
    assert '<pre class="ns-code" style="' in out
    assert "background:#f6f7f8" in out
    code = re.search(
        r'<code class="language-python">(.*?)</code></pre>', out, re.S
    )
    assert code is not None
    # highlighted, and nothing lost: without the token spans it is the code
    assert "<span" in code.group(1)
    plain = re.sub(r"<[^>]+>", "", code.group(1))
    assert plain == "def f():\n  return &lt;tag&gt;"
    assert "\\[e^{i\\pi}+1=0\\]" in out


def test_toggle_and_nested_lists():
    blocks = [
        {
            "type": "toggle",
            "has_children": True,
            "id": "t1",
            "toggle": {
                "rich_text": [
                    {
                        "type": "text",
                        "plain_text": "Q",
                        "text": {"content": "Q"},
                        "annotations": {},
                    }
                ]
            },
        },
        {
            "type": "numbered_list_item",
            "has_children": False,
            "id": "l1",
            "numbered_list_item": {
                "rich_text": [
                    {
                        "type": "text",
                        "plain_text": "one",
                        "text": {"content": "one"},
                        "annotations": {},
                    }
                ]
            },
        },
        {
            "type": "bulleted_list_item",
            "has_children": True,
            "id": "l2",
            "bulleted_list_item": {
                "rich_text": [
                    {
                        "type": "text",
                        "plain_text": "outer",
                        "text": {"content": "outer"},
                        "annotations": {},
                    }
                ]
            },
        },
    ]
    tree = FakeTree(
        {
            "t1": [
                {
                    "type": "paragraph",
                    "id": "p2",
                    "has_children": False,
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "plain_text": "A",
                                "text": {"content": "A"},
                                "annotations": {},
                            }
                        ]
                    },
                }
            ],
            "l2": [
                {
                    "type": "bulleted_list_item",
                    "id": "l3",
                    "has_children": False,
                    "bulleted_list_item": {
                        "rich_text": [
                            {
                                "type": "text",
                                "plain_text": "inner",
                                "text": {"content": "inner"},
                                "annotations": {},
                            }
                        ]
                    },
                },
            ],
        }
    )
    out = render_blocks(blocks, MediaCollector(), tree)
    assert (
        "<details><summary>Q</summary>" in out and "<p>A</p></details>" in out
    )
    assert "<ol><li>one</li></ol>" in out
    assert "<li>outer<ul><li>inner</li></ul></li>" in out


def test_table_rendering():
    blocks = [
        {
            "type": "table",
            "id": "tab",
            "has_children": True,
            "table": {
                "table_width": 2,
                "has_column_header": True,
                "has_row_header": False,
            },
        }
    ]
    tree = FakeTree(
        {
            "tab": [
                {
                    "type": "table_row",
                    "id": "r1",
                    "has_children": False,
                    "table_row": {"cells": [_rt("A"), _rt("B")]},
                },
                {
                    "type": "table_row",
                    "id": "r2",
                    "has_children": False,
                    "table_row": {"cells": [_rt("1"), _rt("2")]},
                },
            ]
        }
    )
    out = render_blocks(blocks, MediaCollector(), tree)
    assert "<thead>" in out
    assert "<th style=" in out and ">A</th><th style=" in out
    assert "<tbody>" in out and "<td style=" in out and ">1</td>" in out
    assert "border-collapse:collapse" in out
    assert "border:1px solid #b8bec5" in out


def test_image_file_becomes_media_placeholder():
    media = MediaCollector()
    blocks = [
        {
            "type": "image",
            "id": "1c4c" + "x" * 28,
            "has_children": False,
            "image": {
                "type": "file",
                "file": {
                    "url": "https://s3.example.com/abc.png?ttl=1",
                    "expiry_time": "",
                },
                "caption": [],
            },
        }
    ]
    out = render_blocks(blocks, media, FakeTree())
    assert "<img src=\"@@NSMEDIA:" in out
    assert len(media.files) == 1
    fname = list(media.files)[0]
    assert fname.startswith("ns-1c4c") and fname.endswith(".png")


def test_media_resolve():
    media = MediaCollector()
    ph = media.add("https://x/y.png", "img1.png")
    html = MediaCollector.resolve(
        f'<img src="{ph}">', {"img1.png": "img1.png"}
    )
    assert '<img src="img1.png">' in html


def test_media_file_names_are_lowercase():
    """N1: Anki on Windows stores the first copy lowercased."""
    media = MediaCollector()
    out = render_blocks(
        [_image("AbCd" + "0" * 28, "Screen%20Shot.PNG")], media, FakeTree()
    )
    (fname,) = media.files
    assert (
        fname == fname.lower()
        and fname.startswith("ns-abcd")
        and fname.endswith(".png")
    )
    assert f"@@NSMEDIA:{fname}@@" in out


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


def test_a_long_media_name_keeps_its_extension():
    from notion_sync_addon.htmlify import MAX_FILE_NAME, _file_safe

    name = _file_safe("ns-abc-" + "x" * 200 + ".PNG")
    assert len(name) == MAX_FILE_NAME
    assert name.endswith("x.png")
    assert name.startswith("ns-abc-")


def test_a_short_media_name_is_unchanged_and_a_long_dotted_one_is_cut():
    from notion_sync_addon.htmlify import MAX_FILE_NAME, _file_safe

    assert _file_safe("ns-abc-My File.pdf") == "ns-abc-my_file.pdf"
    name = _file_safe("a." + "b" * 200)  # no real extension to keep
    assert len(name) == MAX_FILE_NAME and name.startswith("a.bbb")


def test_rendered_blocks_name_their_parts():
    from notion_sync_addon.fetch import Rendered, _render_blocks
    from notion_sync_addon.htmlify import MediaCollector

    para = {
        "id": "p",
        "type": "paragraph",
        "has_children": False,
        "paragraph": {
            "rich_text": [
                {"type": "text", "plain_text": "x", "text": {"content": "x"}}
            ]
        },
    }
    done = _render_blocks([para], "Q", "", MediaCollector(), None, [])
    assert done == Rendered("basic", "<p>x</p>", "")
