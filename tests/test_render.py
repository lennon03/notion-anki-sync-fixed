# -*- coding: utf-8 -*-
"""Tests for synced blocks, columns, page links and code highlighting."""

import html
import re

import pytest

from notion_sync_addon.highlight import highlight, language_slug
from notion_sync_addon.htmlify import MediaCollector, render_blocks


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
