# -*- coding: utf-8 -*-
r"""Convert Notion block trees to Anki-friendly HTML.

Handles: paragraphs, headings (incl. h4), nested lists (bulleted,
numbered, to-do), toggles (<details>), quotes, callouts, dividers,
code blocks (<pre><code>), math blocks & inline math (Anki-native
MathJax \[...] / \(...\) delimiters), inline images, simple tables,
mentions and links.

Media (images/audio/files) are represented by placeholders so the
collection writer can download & import them, see MediaCollector.

This module is standalone (no Anki imports) so it can be unit tested.
"""

from __future__ import annotations

import copy
import html
import re
from typing import Any, Dict, List, Optional

from .highlight import highlight, language_slug


class MediaCollector:
    """Collects downloadable file URLs found while rendering blocks."""

    def __init__(self) -> None:
        # fname -> url ; fname is a safe, unique Anki media filename
        self.files: Dict[str, str] = {}
        self.order: List[str] = []
        self._counter = 0

    def add(self, url: str, fname: str) -> str:
        """Collect ``url`` as ``fname``; return its placeholder."""
        fname = _file_safe(fname)
        if fname in self.files:
            return self._placeholder(fname)
        self.files[fname] = url
        self.order.append(fname)
        return self._placeholder(fname)

    @staticmethod
    def _placeholder(fname: str) -> str:
        return f"@@NSMEDIA:{fname}@@"

    @staticmethod
    def resolve(html_text: str, fname_to_imported: Dict[str, str]) -> str:
        """Replace placeholders with the final exported media filenames."""

        def repl(m: "re.Match[str]") -> str:
            fname = m.group(1)
            dest = fname_to_imported.get(fname)
            return dest if dest is not None else f"[missing media: {fname}]"

        return re.sub(r"@@NSMEDIA:([A-Za-z0-9_.\-]+)@@", repl, html_text)


def _file_safe(name: str) -> str:
    """Make a media file name Anki stores unchanged on every platform.

    Lowercase only: on Windows Anki stores the first copy of a file under a
    lowercased name but returns the name as given when the same file is
    added again, so a mixed-case name would flip between syncs (and not
    match the stored file on case-sensitive AnkiDroid/iOS).
    """
    return re.sub(r"[^a-z0-9_.\-]+", "_", name.lower())[:120] or "media"


def _attr(text: str) -> str:
    return html.escape(text, quote=True)


def _esc(text: str) -> str:
    return html.escape(text, quote=False).replace("\n", "<br>")


def _plain(rts: List[dict]) -> str:
    return "".join(rt.get("plain_text", "") for rt in rts).strip()


# --------------------------------------------------------------------- #
# Rich text
# --------------------------------------------------------------------- #

_COLOR_CSS = {
    "red": "#c0392b",
    "blue": "#2980b9",
    "green": "#27ae60",
    "yellow": "#b7950b",
    "purple": "#8e44ad",
    "pink": "#c0397b",
    "orange": "#ca6f1e",
    "brown": "#873600",
    "gray": "#7f8c8d",
    "grey": "#7f8c8d",
}


def rich_text_to_html(rts: Optional[List[dict]]) -> str:
    """Render Notion rich text (annotations, links, math, mentions)."""
    out: List[str] = []
    for rt in rts or []:
        t = rt.get("type", "text")
        if t == "text":
            text = rt.get("text", {})
            chunk = _esc(text.get("content", ""))
            link = text.get("link")
            ann = rt.get("annotations", {})
            if ann.get("code"):
                chunk = f"<code>{chunk}</code>"
            if ann.get("bold"):
                chunk = f"<b>{chunk}</b>"
            if ann.get("italic"):
                chunk = f"<em>{chunk}</em>"
            if ann.get("strikethrough"):
                chunk = f"<s>{chunk}</s>"
            if ann.get("underline"):
                chunk = f"<u>{chunk}</u>"
            color = ann.get("color") or ""
            if color and color != "default":
                style = ""
                if color.endswith("_background"):
                    base = _COLOR_CSS.get(color[: -len("_background")])
                    if base:
                        bg = _tint(base)
                        style = (
                            f"background:{bg};border-radius:3px;padding:0 2px"
                        )
                else:
                    fg = _COLOR_CSS.get(color)
                    if fg:
                        style = f"color:{fg}"
                if style:
                    chunk = f'<span style="{style}">{chunk}</span>'
            if link and link.get("url"):
                chunk = f'<a href="{_attr(link["url"])}">{chunk}</a>'
            out.append(chunk)
        elif t == "equation":
            # Anki-native MathJax inline delimiters
            out.append(
                "\\(" + rt.get("equation", {}).get("expression", "") + "\\)"
            )
        elif t == "mention":
            mention = rt.get("mention", {})
            label = rt.get("plain_text", "")
            if mention.get("type") == "page":
                url = (mention.get("page") or {}).get("url", "")
                if url:
                    out.append(f'<a href="{_attr(url)}">{_esc(label)}</a>')
                else:
                    out.append(_esc(label))
            else:
                out.append(_esc(label))
    return "".join(out)


def _tint(hex_color: str) -> str:
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return f"rgba({r}, {g}, {b}, 0.2)"


# --------------------------------------------------------------------- #
# Tags: struck-through text
# --------------------------------------------------------------------- #


class StrikeTags:
    """A block source whose struck-through text is taken out as tags.

    Wraps anything with ``children(block_id)``. Every struck-through run of
    text in the blocks it returns is removed from them and its words are
    collected in ``tags`` (split at commas, not cleaned). Code and equation
    blocks are left alone.
    """

    def __init__(self, tree: Any) -> None:
        self._tree = tree
        self.tags: List[str] = []

    def children(self, block_id: str) -> List[dict]:
        """Return the child blocks without their struck-through text."""
        return [self._strip(b) for b in self._tree.children(block_id)]

    def strip_blocks(self, blocks: List[dict]) -> List[dict]:
        """Return ``blocks`` without their struck-through text."""
        return [self._strip(b) for b in blocks]

    def strip_rich(self, rts: List[dict]) -> List[dict]:
        """Return rich text without its struck-through text."""
        return self._clean(rts)

    def _strip(self, block: dict) -> dict:
        btype = block.get("type")
        body = block.get(btype) if isinstance(btype, str) else None
        if not isinstance(body, dict) or btype in ("code", "equation"):
            return block
        block = copy.deepcopy(block)
        body = block[btype]
        if isinstance(body.get("rich_text"), list):
            body["rich_text"] = self._clean(body["rich_text"])
        if isinstance(body.get("cells"), list):  # table row
            body["cells"] = [
                self._clean(c) if isinstance(c, list) else c
                for c in body["cells"]
            ]
        return block

    def _clean(self, rts: List[dict]) -> List[dict]:
        kept: List[dict] = []
        run: List[str] = []
        for rt in rts:
            struck = rt.get("type", "text") == "text" and (
                rt.get("annotations") or {}
            ).get("strikethrough")
            if struck:
                run.append((rt.get("text") or {}).get("content", ""))
                continue
            self._flush(run)
            kept.append(rt)
        self._flush(run)
        return kept

    def _flush(self, run: List[str]) -> None:
        if run:
            self.tags += "".join(run).split(",")
            run.clear()


# --------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------- #

_BLOCK_BODY_TYPES = (
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "heading_4",
    "bulleted_list_item",
    "numbered_list_item",
    "to_do",
    "toggle",
    "quote",
    "callout",
    "code",
    "equation",
    "table",
    "image",
    "video",
    "file",
    "audio",
    "bookmark",
    "embed",
    "link_preview",
    "divider",
    "synced_block",
    "column_list",
    "column",
    "link_to_page",
    "child_page",
)

_NOT_SHARED_NOTE = (
    '<p class="ns-note">[synced block not shared with the integration]</p>'
)

_CODE_BLOCK_STYLE = (
    "display:block; overflow-x:auto; padding:12px; margin:8px 0; "
    "background:#f6f7f8; border:1px solid #dfe3e6; border-radius:6px; "
    "text-align:left; white-space:pre-wrap;"
)


def render_blocks(blocks: List[dict], media: MediaCollector, tree: Any) -> str:
    """Render a flat list of block dicts (linked children via `tree`).

    `tree` must expose children(block_id) -> List[dict] to fetch nested
    blocks lazily.
    """
    out: List[str] = []
    i = 0
    n = len(blocks)
    while i < n:
        block = blocks[i]
        btype = block.get("type")
        i += 1
        if btype not in _BLOCK_BODY_TYPES:
            continue

        # group consecutive list items
        if btype in ("bulleted_list_item", "numbered_list_item", "to_do"):
            tag = "ol" if btype == "numbered_list_item" else "ul"
            items: List[dict] = [block]
            while i < n and blocks[i].get("type") == btype:
                items.append(blocks[i])
                i += 1
            out.append(
                f"<{tag}>"
                f"{_render_list_items(items, btype, media, tree)}"
                f"</{tag}>"
            )
            continue

        out.append(_render_block(block, media, tree))
    return "\n".join(x for x in out if x)


_LIST_TYPES = ("bulleted_list_item", "numbered_list_item", "to_do")


def _split_rich_lines(rts: Optional[List[dict]]) -> List[List[dict]]:
    """Cut rich text at its line breaks (formatting stays balanced)."""
    lines: List[List[dict]] = [[]]
    for rt in rts or []:
        if rt.get("type", "text") != "text":
            lines[-1].append(rt)
            continue
        text = rt.get("text") or {}
        for index, piece in enumerate(
            str(text.get("content", "")).split("\n")
        ):
            if index:
                lines.append([])
            if piece:
                lines[-1].append({**rt, "text": {**text, "content": piece}})
    return lines


def render_lines(
    blocks: List[dict], media: MediaCollector, tree: Any
) -> List[str]:
    """Render top-level blocks as separate lines of HTML.

    Every block is a line, every list item too (as its inner HTML) and a
    paragraph is cut at its line breaks. Empty lines and dividers are
    dropped.
    """
    out: List[str] = []
    for block in blocks:
        btype = block.get("type")
        if btype not in _BLOCK_BODY_TYPES or btype == "divider":
            continue
        if btype in _LIST_TYPES:
            item = _render_list_items([block], btype, media, tree)
            out.append(re.sub(r"(?s)^<li>(.*)</li>$", r"\1", item))
        elif btype == "paragraph" and not block.get("has_children"):
            rich = (block.get("paragraph") or {}).get("rich_text")
            out += [
                rich_text_to_html(line) for line in _split_rich_lines(rich)
            ]
        else:
            out.append(_render_block(block, media, tree))
    return [line for line in out if line.strip()]


def _render_list_items(
    items: List[dict], btype: str, media: MediaCollector, tree: Any
) -> str:
    parts: List[str] = []
    for item in items:
        body = item.get(btype, {})
        rts = rich_text_to_html(body.get("rich_text"))
        children_html = ""
        if item.get("has_children"):
            kids = tree.children(item.get("id"))
            if kids:
                children_html = render_blocks(kids, media, tree)
        if btype == "to_do":
            checked = "checked" if body.get("checked") else ""
            parts.append(
                f'<li><input type="checkbox" disabled {checked}>'
                f"{rts}{children_html}</li>"
            )
        else:
            parts.append(f"<li>{rts}{children_html}</li>")
    return "\n".join(parts)


def _render_block(block: dict, media: MediaCollector, tree: Any) -> str:
    btype = block["type"]
    body = block.get(btype, {})
    text = rich_text_to_html(body.get("rich_text"))
    children_html = ""
    if btype == "synced_block":
        return _render_synced(block, media, tree)
    if btype in ("child_page", "link_to_page"):
        pass  # never inline another page's content
    elif block.get("has_children") and btype != "table":
        kids = tree.children(block.get("id"))
        if kids:
            children_html = render_blocks(kids, media, tree)

    if btype == "paragraph":
        return f"<p>{text}</p>" if text else (children_html or "")
    if btype.startswith("heading_"):
        level = btype.split("_")[1]
        return f"<h{level}>{text}</h{level}>"
    if btype == "quote":
        return f"<blockquote>{text}{children_html}</blockquote>"
    if btype == "callout":
        color = body.get("color", "gray_background")
        icon_emoji = (body.get("icon") or {}).get("emoji", "")
        icon_html = _esc(icon_emoji) + " " if icon_emoji else ""
        return (
            f'<div class="ns-callout ns-bg-{html.escape(color)}">'
            f'{icon_html}{text or ""}{children_html}</div>'
        )
    if btype == "toggle":
        return f"<details><summary>{text}</summary>{children_html}</details>"
    if btype == "code":
        content = _plain(body.get("rich_text") or [])
        lang = body.get("language") or ""
        return (
            f'<pre class="ns-code" style="{_CODE_BLOCK_STYLE}">'
            f'<code class="language-{language_slug(lang)}">'
            f"{highlight(content, lang)}</code></pre>"
        )
    if btype == "equation":
        return (
            "<div class='ns-eq'>\\[" + body.get("expression", "") + "\\]</div>"
        )
    if btype == "divider":
        return "<hr>"
    if btype in ("image", "video", "audio", "file"):
        return _render_file(block, media)
    if btype in ("bookmark", "embed", "link_preview"):
        url = body.get("url", "")
        caption = rich_text_to_html(body.get("caption"))
        return f'<p><a href="{_attr(url)}">{_esc(url)}</a>{caption}</p>'
    if btype == "column_list":
        return (
            '<div class="ns-columns" '
            'style="display:flex;flex-wrap:wrap;gap:12px">'
            f"{children_html}</div>"
        )
    if btype == "column":
        return (
            '<div class="ns-column" style="flex:1 1 200px;min-width:0">'
            f"{children_html}</div>"
        )
    if btype == "link_to_page":
        target = body.get("page_id") or body.get("database_id") or ""
        return f'<p><a href="{_notion_url(target)}">→ linked page</a></p>'
    if btype == "child_page":
        return (
            f'<p><a href="{_notion_url(block.get("id", ""))}">'
            f'{_esc(body.get("title") or "child page")}</a></p>'
        )
    if btype == "child_database":
        href = _attr(block.get("url", ""))
        name = _esc(body.get("title") or "table")
        return f'<p><a href="{href}">→ {name}</a></p>'
    if btype == "table":
        return _render_table(block, media, tree)
    return children_html


def _notion_url(block_id: str) -> str:
    return _attr("https://www.notion.so/" + str(block_id).replace("-", ""))


def _render_synced(block: dict, media: MediaCollector, tree: Any) -> str:
    """Render a synced block; copies show the original's children."""
    body = block.get("synced_block") or {}
    source = (body.get("synced_from") or {}).get("block_id")
    if not source:
        if not block.get("has_children"):
            return ""
        return render_blocks(tree.children(block.get("id")), media, tree)
    try:
        kids = tree.children(source)
    except Exception as e:
        # 403/404: the original isn't shared. Transient errors propagate so
        # the row is retried instead of caching a wrong render.
        if getattr(e, "transient", False) or not getattr(e, "status", 0):
            raise
        return _NOT_SHARED_NOTE
    return render_blocks(kids, media, tree) if kids else ""


def _render_table(block: dict, media: MediaCollector, tree: Any) -> str:
    body = block.get("table", {})
    rows: List[dict] = []
    if block.get("has_children"):
        rows = tree.children(block.get("id"))
    lines: List[str] = [
        "<table class='ns-table' style='border-collapse:collapse; "
        "border:1px solid #b8bec5; width:100%; text-align:left;'>"
    ]
    col_header = bool(body.get("has_column_header"))
    row_header = bool(body.get("has_row_header"))
    for idx, row in enumerate(rows):
        cells = row.get("table_row", {}).get("cells", [])
        tags: List[str] = []
        for cidx, cell in enumerate(cells):
            if idx == 0 and col_header or (cidx == 0 and row_header):
                tags.append("th")
            else:
                tags.append("td")
        cells_html = "".join(
            f"<{t} style='border:1px solid #b8bec5; padding:6px 8px; "
            f"vertical-align:top;'>{rich_text_to_html(cell)}</{t}>"
            for t, cell in zip(tags, cells)
        )
        open_thead = '<thead>' if idx == 0 and col_header else ''
        close_thead = (
            '</thead><tbody>'
            if idx == 0 and col_header and len(rows) > 1
            else ('</thead>' if idx == 0 and col_header else '')
        )
        lines.append(f"{open_thead}<tr>{cells_html}</tr>{close_thead}")
    if col_header:
        lines.append("</tbody>")
    lines.append("</table>")
    return "".join(lines)


def _render_file(block: dict, media: MediaCollector) -> str:
    btype = block["type"]
    info = block.get(btype, {})
    src_type = info.get("type")
    src = (info.get(src_type) or {}) if src_type else {}
    url = src.get("url", "")
    caption = rich_text_to_html(info.get("caption"))
    if not url:
        return ""
    if src_type == "external":
        # cannot be stored reliably -> hot link
        tag = "img" if btype == "image" else "a"
        if tag == "img":
            return f'<img src="{_attr(url)}">{caption}'
        return f'<p><a href="{_attr(url)}">{_esc(url)}</a>{caption}</p>'

    # notion-hosted file -> placeholder, downloaded at import time
    base = url.split("/")[-1].split("?")[0] or f"{btype}.bin"
    fname = f"ns-{block.get('id', '').replace('-', '')[:12]}-{base}"
    ph = media.add(url, fname)
    if btype == "image":
        return f'<img src="{ph}">{caption}'
    if btype == "audio":
        return f'<audio controls src="{ph}"></audio>{caption}'
    if btype == "video":
        return f'<video controls src="{ph}"></video>{caption}'
    # file: try to render as attachment link
    return f'<p><a href="{ph}">{_esc(base)}</a>{caption}</p>'
