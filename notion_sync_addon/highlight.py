# -*- coding: utf-8 -*-
"""Tiny regex-based syntax highlighter for Notion code blocks.

``highlight(code, language)`` returns HTML-escaped code in which comments,
strings, numbers and keywords are wrapped in colored ``<span>`` tags. The
colors are CSS variables (``--ns-code-<kind>``) with a light-background
fallback, so a card template can restyle them (e.g. for night mode).

Every input character ends up in the output exactly once and in order, so
stripping the tags and unescaping gives the original code back.

This module is standalone (no Anki imports) so it can be unit tested.
"""

from __future__ import annotations

import html
import re
from typing import Dict, FrozenSet, List, Pattern

_FALLBACK = {
    "comment": "#6a737d",
    "string": "#032f62",
    "number": "#005cc5",
    "keyword": "#d73a49",
    "builtin": "#6f42c1",
}


def _span(kind: str, text: str) -> str:
    return (
        f'<span style="color:var(--ns-code-{kind}, {_FALLBACK[kind]})">'
        f"{html.escape(text)}</span>"
    )


def _kw(words: str) -> FrozenSet[str]:
    return frozenset(words.split())


_C_KW = (
    "if else for while do switch case default break continue return goto "
    "struct union enum typedef static const volatile extern sizeof void "
    "int long short char float double unsigned signed auto register inline"
)
_KEYWORDS: Dict[str, FrozenSet[str]] = {
    "python": _kw(
        "False None True and as assert async await break class continue "
        "def del elif else except finally for from global if import in is "
        "lambda nonlocal not or pass raise return try while with yield "
        "match case"
    ),
    "javascript": _kw(
        "async await break case catch class const continue debugger "
        "default delete do else export extends finally for function if "
        "import in instanceof let new of return static super switch this "
        "throw try typeof var void while with yield null undefined true "
        "false interface type enum implements public private protected "
        "readonly abstract as declare namespace"
    ),
    "java": _kw(
        "abstract assert boolean break byte case catch char class const "
        "continue default do double else enum extends final finally float "
        "for if implements import instanceof int interface long native new "
        "package private protected public return short static super switch "
        "synchronized this throw throws try void volatile while null true "
        "false var record"
    ),
    "c": _kw(_C_KW),
    "cpp": _kw(
        _C_KW + " class namespace template typename public private "
        "protected virtual override new delete this using try catch throw "
        "bool true false nullptr constexpr operator"
    ),
    "csharp": _kw(
        "abstract as base bool break byte case catch char checked class "
        "const continue decimal default delegate do double else enum event "
        "explicit extern false finally fixed float for foreach if in int "
        "interface internal is lock long namespace new null object "
        "operator out override params private protected public readonly "
        "ref return sealed short static string struct switch this throw "
        "true try typeof uint ulong using var virtual void while async "
        "await"
    ),
    "go": _kw(
        "break case chan const continue default defer else fallthrough for "
        "func go goto if import interface map package range return select "
        "struct switch type var nil true false"
    ),
    "rust": _kw(
        "as async await break const continue crate dyn else enum extern "
        "false fn for if impl in let loop match mod move mut pub ref "
        "return self Self static struct super trait true type unsafe use "
        "where while"
    ),
    "sql": _kw(
        "select from where and or not null insert into values update set "
        "delete create table drop alter add join inner left right outer on "
        "group by order having limit offset as distinct union all is in "
        "like between case when then else end primary key foreign "
        "references index view exists asc desc"
    ),
    "bash": _kw(
        "if then else elif fi for while until do done case esac in "
        "function select return exit break continue local export readonly "
        "echo cd set unset source alias"
    ),
    "json": _kw("true false null"),
    "css": _kw("important inherit initial unset none auto"),
    "html": frozenset(),
}

_ALIASES = {
    "js": "javascript",
    "typescript": "javascript",
    "ts": "javascript",
    "jsx": "javascript",
    "tsx": "javascript",
    "c++": "cpp",
    "c#": "csharp",
    "cs": "csharp",
    "golang": "go",
    "shell": "bash",
    "sh": "bash",
    "zsh": "bash",
    "powershell": "bash",
    "py": "python",
    "xml": "html",
    "scss": "css",
    "less": "css",
}

_LINE_COMMENT = {
    "python": "#",
    "bash": "#",
    "javascript": "//",
    "java": "//",
    "c": "//",
    "cpp": "//",
    "csharp": "//",
    "go": "//",
    "rust": "//",
    "sql": "--",
}
_C_BLOCK = ("/*", "*/")
_BLOCK_COMMENT = {
    "javascript": _C_BLOCK,
    "java": _C_BLOCK,
    "c": _C_BLOCK,
    "cpp": _C_BLOCK,
    "csharp": _C_BLOCK,
    "go": _C_BLOCK,
    "rust": _C_BLOCK,
    "sql": _C_BLOCK,
    "css": _C_BLOCK,
    "html": ("<!--", "-->"),
}

_DQ = r'"(?:\\.|[^"\\\n])*"'
_SQ = r"'(?:\\.|[^'\\\n])*'"
_BT = r"`(?:\\[\s\S]|[^`\\])*`"
# Rust: a char literal is one character, so lifetimes ('a) don't match
_RUST_CHAR = r"'(?:\\[0-9a-fA-Fx{}u]+|\\.|[^'\\\n])'"
_STRINGS = {
    "python": r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|' + _DQ + "|" + _SQ,
    "javascript": _DQ + "|" + _SQ + "|" + _BT,
    "go": _DQ + "|" + _BT + "|" + _SQ,
    "rust": _DQ + "|" + _RUST_CHAR,
    "bash": _DQ + "|'[^'\\n]*'",
}
_NUMBER = (
    r"(?<![\w.])(?:0[xX][0-9a-fA-F_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)"
)
_CSS_NUMBER = r"#[0-9a-fA-F]{3,8}\b|" + _NUMBER
_WORD = r"[A-Za-z_$][A-Za-z0-9_$]*"

_cache: Dict[str, Pattern[str]] = {}


def _pattern(lang: str) -> Pattern[str]:
    """Build (and cache) the token regex of a known language."""
    pat = _cache.get(lang)
    if pat is not None:
        return pat
    parts: List[str] = []
    block = _BLOCK_COMMENT.get(lang)
    if block:
        o, c = (re.escape(x) for x in block)
        parts.append(f"(?P<comment>{o}[\\s\\S]*?(?:{c}|\\Z))")
    if lang == "html":
        parts.append(r"(?P<tag></?[A-Za-z][^<>]*>?)")
    else:
        line = _LINE_COMMENT.get(lang)
        if line:
            parts.append(f"(?P<linecomment>{re.escape(line)}[^\\n]*)")
        strings = _STRINGS.get(lang, _DQ + "|" + _SQ)
        parts.append(f"(?P<string>{strings})")
        num = _CSS_NUMBER if lang == "css" else _NUMBER
        parts.append(f"(?P<number>{num})")
        parts.append(f"(?P<word>{_WORD})")
    pat = re.compile("|".join(parts))
    _cache[lang] = pat
    return pat


_TAG_PART = re.compile(
    r"(?P<tagname>(?<=<)/?[\w:-]+)"
    r"|(?P<attr>[\w:@.-]+)(?==)"
    r"|(?P<string>\"[^\"]*\"|'[^']*')"
)


def _html_tag(text: str) -> str:
    out: List[str] = []
    pos = 0
    for m in _TAG_PART.finditer(text):
        out.append(html.escape(text[pos : m.start()]))
        kind = {"tagname": "keyword", "attr": "builtin"}.get(
            m.lastgroup or "", "string"
        )
        out.append(_span(kind, m.group()))
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


def highlight(code: str, language: str) -> str:
    """Return ``code`` as escaped HTML with colored token spans.

    Unknown languages (and "plain text") come back escaped but without any
    markup.
    """
    name = (language or "").strip().lower()
    lang = _ALIASES.get(name, name)
    if lang not in _KEYWORDS:
        return html.escape(code)
    keywords = _KEYWORDS[lang]
    fold = lang == "sql"
    out: List[str] = []
    pos = 0
    for m in _pattern(lang).finditer(code):
        out.append(html.escape(code[pos : m.start()]))
        pos = m.end()
        text = m.group()
        group = m.lastgroup or ""
        if group == "tag":
            out.append(_html_tag(text))
        elif group in ("comment", "linecomment"):
            out.append(_span("comment", text))
        elif group == "word":
            word = text.lower() if fold else text
            if word in keywords:
                out.append(_span("keyword", text))
            else:
                out.append(html.escape(text))
        else:
            out.append(_span(group, text))
    out.append(html.escape(code[pos:]))
    return "".join(out)


def language_slug(language: str) -> str:
    """CSS-class-safe slug for a Notion language name."""
    name = (language or "").strip().lower()
    special = {"c++": "cpp", "c#": "csharp", "f#": "fsharp"}
    if name in special:
        return special[name]
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-") or "plain-text"
