# -*- coding: utf-8 -*-
r"""Find flashcards in the plain content of a Notion page.

A page that is not a table can hold cards written as text:

* ``### Question`` or ``#### Question``: the blocks under the heading, up
  to the next heading, divider, ``Q:`` line or ``front :: back`` line, are
  the answer. A numbered list as the answer makes an overlapping cloze
  (recite it line by line).
* ``Q: question`` followed by ``A: answer`` (the answer goes on until the
  next ``Q:``, heading, divider or empty line). Blocks between the two
  lines belong to the question.
* ``front :: back`` on one line.
* a paragraph, list item, quote or callout holding a cloze: ``{{c1::x}}``
  or the short ``{{x}}``, which is numbered for you.

A line starting with ``Back:`` (or ``Answer:``) under a heading puts what
is above it on the front of the card: see ``split_front``.

Toggles are not read. This module only finds the cards; fetch.py renders
them. It is standalone (no Anki imports) so it can be unit tested.
"""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from .htmlify import _BLOCK_BODY_TYPES, _split_rich_lines, has_short_cloze

HEADINGS = ("heading_1", "heading_2", "heading_3", "heading_4")
QUESTION_HEADINGS = ("heading_3", "heading_4")
# a sub-page or a table: read on its own, never part of a card
STRUCTURE = ("child_page", "child_database")
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
_A = re.compile(
    r"^\s*(?:a|answer|back|r[eé]ponse)\s*:\s*(?P<rest>.*)$", re.I | re.S
)
# where the front of a card ends. Not the bare "A:": outside a "Q:" card it
# is too easily ordinary text ("A: the set of ...")
_BACK = re.compile(
    r"^\s*(?:answer|back|r[eé]ponse)\s*:\s*(?P<rest>.*)$", re.I | re.S
)
_PAIR = re.compile(r"^(?P<front>.+?)\s::\s(?P<back>.+)$", re.S)
_SPEC = re.compile(r"\s*\[(?P<spec>[^\[\]]*)\]\s*$")
_SPEC_BODY = re.compile(
    r"basic|(?:cloze\s+)?hide(?:\s+others)?"
    r"|(?:overlap(?:ping)?)?\s*(?:\d+\s*/\s*\d+)?\s*(?:x\s*\d+)?",
    re.I,
)


@dataclass
class PageCard:
    """A card found in a page (not yet rendered)."""

    source_id: str  # a 32 hex id: the question's block id
    origin: str  # "heading", "qa", "pair" or "cloze"
    title: List[dict] = field(default_factory=list)  # question rich text
    blocks: List[dict] = field(default_factory=list)  # answer blocks
    spec: Optional[str] = None  # "[2/1 x2]" option text, if written
    # blocks shown on the front under the question. None: not looked for
    # yet, a "Back:" line among ``blocks`` says where the front ends
    front: Optional[List[dict]] = None


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


def _cloze_rich(rts: Any) -> bool:
    """Tell whether rich text holds a cloze, numbered or short."""
    return has_cloze(rt_text(rts)) or has_short_cloze(rts)


def _body(block: dict) -> dict:
    body = block.get(block.get("type") or "")
    return body if isinstance(body, dict) else {}


def _block_text(block: dict) -> str:
    return rt_text(_body(block).get("rich_text"))


def _block_cloze(block: dict) -> bool:
    return _cloze_rich(_body(block).get("rich_text"))


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


def split_front(blocks: List[dict]) -> Tuple[List[dict], List[dict]]:
    """Split a card's blocks into (front blocks, back blocks).

    A paragraph starting with ``Back:`` (or ``Answer:``, ``Réponse:``)
    ends the front: what is above it is shown with the question, the rest
    of that line and what follows is the answer. Without such a line the
    front is empty and every block is the answer.
    """
    for index, block in enumerate(blocks):
        if block.get("type") != "paragraph":
            continue
        match = _BACK.match(_block_text(block))
        if match is None:
            continue
        back = blocks[index + 1 :]
        if match["rest"].strip() or block.get("has_children"):
            back = [_strip_prefix(block, match.start("rest"))] + back
        return blocks[:index], back
    return [], blocks


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
        if btype == "paragraph" and not _block_cloze(block):
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
                        front=[],
                    )
                )
                i += 1
                continue
        if btype in CLOZE_BLOCKS and _block_cloze(block):
            title, spec = split_spec(_body(block).get("rich_text") or [])
            cards.append(PageCard(_sid(block), "cloze", title, spec=spec))
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
        elif btype in HEADINGS or btype in STRUCTURE:
            break
        elif btype == "divider":
            if not in_hint:
                break
            in_hint = False
        elif btype == "paragraph" and not in_hint and _starts_card(block):
            break  # a card of its own: the heading above it is a section
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
    elif _cloze_rich(title):
        cards.append(PageCard(_sid(heading), "cloze", title, spec=spec))
    return j


def _starts_card(block: dict) -> bool:
    """Tell whether a paragraph is a ``Q:`` or a ``front :: back`` line."""
    text = _block_text(block)
    if _Q.match(text):
        return True
    return _PAIR.match(text) is not None and not _block_cloze(block)


def _is_question(block: dict) -> bool:
    return block.get("type") == "paragraph" and bool(
        _Q.match(_block_text(block))
    )


def _is_answer(block: dict) -> bool:
    return block.get("type") == "paragraph" and bool(
        _A.match(_block_text(block))
    )


def _ends_card(block: dict) -> bool:
    """Tell whether a block can't be part of a ``Q:`` / ``A:`` card."""
    return (
        block.get("type") in HEADINGS
        or block.get("type") in STRUCTURE
        or block.get("type") == "divider"
        or _is_empty(block)
        or _is_question(block)
    )


def _qa_card(blocks: List[dict], i: int, cards: List[PageCard]) -> int:
    """Read ``Q:`` / ``A:`` at ``i``; return the next index.

    The blocks between the two lines (a picture, a list, more text) are
    shown on the front under the question.
    """
    n = len(blocks)
    first = blocks[i]
    match = _Q.match(_block_text(first))
    assert match is not None
    question = _strip_prefix(first, match.start("rest"))
    title = _body(question).get("rich_text") or []
    front: List[dict] = []
    j = i + 1
    while j < n and not _is_answer(blocks[j]) and not _ends_card(blocks[j]):
        front.append(blocks[j])
        j += 1
    if j >= n or not _is_answer(blocks[j]):
        return i + 1  # no answer: not a card
    amatch = _A.match(_block_text(blocks[j]))
    assert amatch is not None
    answer = [_strip_prefix(blocks[j], amatch.start("rest"))]
    j += 1
    while j < n and not _ends_card(blocks[j]):
        answer.append(blocks[j])
        j += 1
    answer = [b for b in answer if not _is_empty(b)]
    if not rt_text(title).strip() and front:
        # "Q:" alone on its line: the next paragraph is the question
        if front[0].get("type") == "paragraph":
            title = _body(front.pop(0)).get("rich_text") or []
    title, spec = split_spec(title)
    if any(_renderable(b) for b in answer):
        cards.append(PageCard(_sid(first), "qa", title, answer, spec, front))
    return j
