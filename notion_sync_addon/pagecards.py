# -*- coding: utf-8 -*-
r"""Find flashcards in the plain content of a Notion page.

A page that is not a table can hold cards written as text:

* ``### Question`` or ``#### Question``: the blocks under the heading, up
  to the next heading or divider, are the answer. A numbered list as the
  answer makes an overlapping cloze (recite it line by line).
* ``Q: question`` followed by ``A: answer`` (the answer goes on until the
  next ``Q:``, heading, divider or empty line).
* ``front :: back`` on one line.
* a paragraph, list item, quote or callout holding ``{{c1::cloze}}``.

Toggles are not read. This module only finds the cards; fetch.py renders
them. It is standalone (no Anki imports) so it can be unit tested.
"""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from .htmlify import _BLOCK_BODY_TYPES, _split_rich_lines

HEADINGS = ("heading_1", "heading_2", "heading_3", "heading_4")
QUESTION_HEADINGS = ("heading_3", "heading_4")
CLOZE_BLOCKS = (
    "paragraph",
    "bulleted_list_item",
    "numbered_list_item",
    "to_do",
    "quote",
    "callout",
)

_CLOZE = re.compile(r"\{\{c\d+::")
_Q = re.compile(r"^\s*(?:q|question)\s*:\s*(?P<rest>.*)$", re.I | re.S)
_A = re.compile(r"^\s*(?:a|answer)\s*:\s*(?P<rest>.*)$", re.I | re.S)
_PAIR = re.compile(r"^(?P<front>.+?)\s::\s(?P<back>.+)$", re.S)
_SPEC = re.compile(r"\s*\[(?P<spec>[^\[\]]*)\]\s*$")
_SPEC_BODY = re.compile(
    r"(?:overlap(?:ping)?)?\s*(?:\d+\s*/\s*\d+)?\s*(?:x\s*\d+)?", re.I
)


@dataclass
class PageCard:
    """A card found in a page (not yet rendered)."""

    source_id: str  # a 32 hex id: the question's block id
    origin: str  # "heading", "qa", "pair" or "cloze"
    title: List[dict] = field(default_factory=list)  # question rich text
    blocks: List[dict] = field(default_factory=list)  # answer blocks
    spec: Optional[str] = None  # "[2/1 x2]" option text, if written


def rt_text(rts: Any) -> str:
    """Return the plain text of a rich text list."""
    out = []
    for rt in rts or []:
        if not isinstance(rt, dict):
            continue
        text = rt.get("text")
        if rt.get("type", "text") == "text" and isinstance(text, dict):
            out.append(str(text.get("content", "")))
        else:
            out.append(str(rt.get("plain_text", "")))
    return "".join(out)


def has_cloze(text: str) -> bool:
    """Tell whether ``text`` holds an Anki cloze like ``{{c1::x}}``."""
    return bool(_CLOZE.search(text))


def _body(block: dict) -> dict:
    body = block.get(block.get("type") or "")
    return body if isinstance(body, dict) else {}


def _block_text(block: dict) -> str:
    return rt_text(_body(block).get("rich_text"))


def _is_empty(block: dict) -> bool:
    return (
        block.get("type") == "paragraph"
        and not block.get("has_children")
        and not _block_text(block).strip()
    )


def _renderable(block: dict) -> bool:
    return block.get("type") in _BLOCK_BODY_TYPES and not _is_empty(block)


def _is_hint(block: dict) -> bool:
    return (
        block.get("type") == "heading_4"
        and _block_text(block).strip().lower() == "hint"
    )


def _sid(block: dict) -> str:
    return str(block.get("id", "")).replace("-", "").lower()


def _virtual_id(block_id: str, index: int) -> str:
    if index == 0:
        return str(block_id).replace("-", "").lower()
    seed = f"{block_id}:{index}".encode("utf-8")
    return hashlib.md5(seed).hexdigest()  # noqa: S324 - an id, not security


def _expand(blocks: List[dict]) -> List[dict]:
    """Make every line of a plain paragraph its own paragraph.

    ``Q: x`` and ``A: y`` typed with a soft line break (shift+enter) then
    read like two paragraphs. Each piece gets an id derived from the
    block's, so one block can hold several cards.
    """
    out: List[dict] = []
    for block in blocks:
        if block.get("type") != "paragraph" or block.get("has_children"):
            out.append(block)
            continue
        lines = _split_rich_lines(_body(block).get("rich_text"))
        if len(lines) < 2:
            out.append(block)
            continue
        index = 0
        for line in lines:
            if not rt_text(line).strip():
                continue
            piece = copy.deepcopy(block)
            piece["id"] = _virtual_id(block.get("id", ""), index)
            piece["paragraph"]["rich_text"] = line
            out.append(piece)
            index += 1
    return out


def _drop_chars(rts: List[dict], count: int) -> List[dict]:
    """Return ``rts`` without its first ``count`` characters."""
    out: List[dict] = []
    for rt in rts:
        if count <= 0:
            out.append(rt)
            continue
        text = rt_text([rt])
        if len(text) <= count:
            count -= len(text)
            continue
        if rt.get("type", "text") == "text":
            piece = copy.deepcopy(rt)
            piece["text"]["content"] = text[count:]
            piece["plain_text"] = text[count:]
            out.append(piece)
        count = 0
    return out


def _keep_chars(rts: List[dict], count: int) -> List[dict]:
    """Return the first ``count`` characters of ``rts``."""
    out: List[dict] = []
    for rt in rts:
        if count <= 0:
            break
        text = rt_text([rt])
        if len(text) <= count or rt.get("type", "text") != "text":
            out.append(rt)
            count -= len(text)
            continue
        piece = copy.deepcopy(rt)
        piece["text"]["content"] = text[:count]
        piece["plain_text"] = text[:count]
        out.append(piece)
        count = 0
    return out


def _strip_prefix(block: dict, rest_start: int) -> dict:
    """Copy a text block without the first ``rest_start`` characters."""
    block = copy.deepcopy(block)
    body = _body(block)
    body["rich_text"] = _drop_chars(body.get("rich_text") or [], rest_start)
    return block


def split_spec(rts: List[dict]) -> Tuple[List[dict], Optional[str]]:
    """Cut a trailing ``[2/1 x2]`` option off a question's rich text."""
    text = rt_text(rts)
    m = _SPEC.search(text)
    if m is None:
        return rts, None
    body = m.group("spec").strip()
    if not body or _SPEC_BODY.fullmatch(body) is None:
        return rts, None
    return _keep_chars(rts, m.start()), body


def _plain_question(block: dict) -> bool:
    return block.get("type") in (
        "paragraph",
        "bulleted_list_item",
        "numbered_list_item",
        "quote",
    )


def parse_page(blocks: List[dict]) -> List[PageCard]:
    """Find the cards in the top-level blocks of a page."""
    blocks = _expand(blocks)
    cards: List[PageCard] = []
    n = len(blocks)
    i = 0
    while i < n:
        block = blocks[i]
        btype = block.get("type")
        text = _block_text(block)
        if btype in QUESTION_HEADINGS:
            if btype == "heading_4" and _is_hint(block):
                i += 1
                continue
            i = _heading_card(blocks, i, cards)
            continue
        if btype == "paragraph" and _Q.match(text):
            i = _qa_card(blocks, i, cards)
            continue
        if btype == "paragraph" and not has_cloze(text):
            pair = _PAIR.match(text)
            if pair is not None:
                title = _keep_chars(
                    _body(block).get("rich_text") or [], len(pair["front"])
                )
                answer = _strip_prefix(block, pair.start("back"))
                cards.append(
                    PageCard(
                        _sid(block),
                        "pair",
                        title,
                        [answer],
                    )
                )
                i += 1
                continue
        if btype in CLOZE_BLOCKS and has_cloze(text):
            cards.append(
                PageCard(
                    _sid(block),
                    "cloze",
                    _body(block).get("rich_text") or [],
                )
            )
        i += 1
    return [c for c in cards if SOURCE.fullmatch(c.source_id)]


SOURCE = re.compile(r"[0-9a-f]{32}")


def _heading_card(blocks: List[dict], i: int, cards: List[PageCard]) -> int:
    """Read the card under the heading at ``i``; return the next index."""
    heading = blocks[i]
    n = len(blocks)
    j = i + 1
    answer: List[dict] = []
    in_hint = False
    while j < n:
        block = blocks[j]
        btype = block.get("type")
        if _is_hint(block):
            in_hint = True
        elif btype in HEADINGS:
            break
        elif btype == "divider":
            if not in_hint:
                break
            in_hint = False
        answer.append(block)
        j += 1
    rts = _body(heading).get("rich_text") or []
    title, spec = split_spec(rts)
    if any(_renderable(b) for b in answer):
        cards.append(
            PageCard(
                _sid(heading),
                "heading",
                title,
                answer,
                spec,
            )
        )
    elif has_cloze(rt_text(title)):
        cards.append(
            PageCard(
                _sid(heading),
                "cloze",
                title,
            )
        )
    return j


def _qa_card(blocks: List[dict], i: int, cards: List[PageCard]) -> int:
    """Read ``Q:`` / ``A:`` at ``i``; return the next index."""
    n = len(blocks)
    first = blocks[i]
    match = _Q.match(_block_text(first))
    assert match is not None
    question = [_strip_prefix(first, match.start("rest"))]
    j = i + 1
    while (
        j < n
        and not _A.match(_block_text(blocks[j]))
        and blocks[j].get("type") == "paragraph"
        and not _is_empty(blocks[j])
        and not _Q.match(_block_text(blocks[j]))
    ):
        question.append(blocks[j])
        j += 1
    if j >= n or not (
        blocks[j].get("type") == "paragraph"
        and _A.match(_block_text(blocks[j]))
    ):
        return i + 1  # no answer: not a card
    amatch = _A.match(_block_text(blocks[j]))
    assert amatch is not None
    answer = [_strip_prefix(blocks[j], amatch.start("rest"))]
    j += 1
    while j < n:
        block = blocks[j]
        if (
            block.get("type") in HEADINGS
            or block.get("type") == "divider"
            or _is_empty(block)
            or (
                block.get("type") == "paragraph"
                and _Q.match(_block_text(block))
            )
        ):
            break
        answer.append(block)
        j += 1
    answer = [b for b in answer if not _is_empty(b)]
    title: List[dict] = []
    for index, piece in enumerate(question):
        if index:
            title.append({"type": "text", "text": {"content": " "}})
        title += _body(piece).get("rich_text") or []
    title, spec = split_spec(title)
    if any(_renderable(b) for b in answer):
        cards.append(
            PageCard(
                _sid(first),
                "qa",
                title,
                answer,
                spec,
            )
        )
    return j
