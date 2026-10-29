# -*- coding: utf-8 -*-
"""Convert Notion block trees to Anki-friendly HTML.

Handles: paragraphs, headings (incl. h4), nested lists (bulleted,
numbered, to-do), toggles (<details>), quotes, callouts, dividers,
code blocks (<pre><code>), math blocks & inline math (Anki-native
MathJax \\[...] / \\(...\\) delimiters), inline images, simple tables,
mentions and links.

Media (images/audio/files) are represented by placeholders so the
collection writer can download & import them, see MediaCollector.

This module is standalone (no Anki imports) so it can be unit tested.
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional


class MediaCollector:
    """Collects downloadable file URLs found while rendering blocks."""

    def __init__(self) -> None:
        # fname -> url ; fname is a safe, unique Anki media filename
        self.files: Dict[str, str] = {}
        self.order: List[str] = []
        self._counter = 0

    def add(self, url: str, fname: str) -> str:
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
    return re.sub(r"[^A-Za-z0-9_.\-]+", "_", name)[:120] or "media"


def _esc(text: str) -> str:
    return html.escape(text, quote=False).replace("\n", "<br>")


def _plain(rts: List[dict]) -> str:
    return "".join(rt.get("plain_text", "") for rt in rts).strip()


# --------------------------------------------------------------------- #
# Rich text
# --------------------------------------------------------------------- #

_COLOR_CSS = {
    "red": "#c0392b", "blue": "#2980b9", "green": "#27ae60", "yellow": "#b7950b",
    "purple": "#8e44ad", "pink": "#c0397b", "orange": "#ca6f1e", "brown": "#873600",
    "gray": "#7f8c8d", "grey": "#7f8c8d",
}


def rich_text_to_html(rts: Optional[List[dict]]) -> str:
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
                        style = f"background:{bg};border-radius:3px;padding:0 2px"
                else:
                    fg = _COLOR_CSS.get(color)
                    if fg:
                        style = f"color:{fg}"
                if style:
                    chunk = f'<span style="{style}">{chunk}</span>'
            if link and link.get("url"):
                chunk = f'<a href="{html.escape(link["url"], quote=True)}">{chunk}</a>'
            out.append(chunk)
        elif t == "equation":
            # Anki-native MathJax inline delimiters
            out.append("\\(" + rt.get("equation", {}).get("expression", "") + "\\)")
        elif t == "mention":
            mention = rt.get("mention", {})
            label = rt.get("plain_text", "")
            if mention.get("type") == "page":
                url = (mention.get("page") or {}).get("url", "")
                if url:
                    out.append(f'<a href="{html.escape(url, quote=True)}">{_esc(label)}</a>')
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
# Blocks
# --------------------------------------------------------------------- #

_BLOCK_BODY_TYPES = (
    "paragraph", "heading_1", "heading_2", "heading_3", "heading_4",
    "bulleted_list_item", "numbered_list_item", "to_do", "toggle",
    "quote", "callout", "code", "equation", "table", "image", "video",
    "file", "audio", "bookmark", "embed", "link_preview", "divider",
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
            out.append(f"<{tag}>{_render_list_items(items, btype, media, tree)}</{tag}>")
            continue

        out.append(_render_block(block, media, tree))
    return "\n".join(x for x in out if x)


def _render_list_items(items: List[dict], btype: str, media: MediaCollector, tree: Any) -> str:
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
                f'<li><input type="checkbox" disabled {checked}>{rts}{children_html}</li>'
            )
        else:
            parts.append(f"<li>{rts}{children_html}</li>")
    return "\n".join(parts)


def _render_block(block: dict, media: MediaCollector, tree: Any) -> str:
    btype = block["type"]
    body = block.get(btype, {})
    text = rich_text_to_html(body.get("rich_text"))
    children_html = ""
    if block.get("has_children") and btype != "table":
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
        return f'<div class="ns-callout ns-bg-{html.escape(color)}">{icon_html}{text or ""}{children_html}</div>'
    if btype == "toggle":
        return f"<details><summary>{text}</summary>{children_html}</details>"
    if btype == "code":
        content = _plain(body.get("rich_text") or [])
        lang = html.escape(body.get("language", "plain text"), quote=True)
        return (
            f'<pre class="ns-code" style="{_CODE_BLOCK_STYLE}">'
            f'<code>{html.escape(content)}</code></pre>'
        )
    if btype == "equation":
        return "<div class='ns-eq'>\\[" + body.get("expression", "") + "\\]</div>"
    if btype == "divider":
        return "<hr>"
    if btype in ("image", "video", "audio", "file"):
        return _render_file(block, media)
    if btype in ("bookmark", "embed", "link_preview"):
        url = body.get("url", "")
        caption = rich_text_to_html(body.get("caption"))
        return f'<p><a href="{html.escape(url, quote=True)}">{_esc(url)}</a>{caption}</p>'
    if btype == "link_to_page":
        lr = body.get("link_to_page", {})
        return f'<p><a href="{html.escape(lr.get("page_url", "") or lr.get("url", ""), quote=True)}">→ page</a></p>'
    if btype == "child_page":
        return (
            f'<p><a href="{html.escape(body.get("url", ""), quote=True)}">'
            f'{_esc(body.get("title") or "child page")}</a></p>'
        )
    if btype == "child_database":
        return f'<p><a href="{html.escape(block.get("url", ""), quote=True)}">→ {_esc(body.get("title") or "table")}</a></p>'
    if btype == "table":
        return _render_table(block, media, tree)
    if btype == "synced_block":
        return children_html
    return children_html


def _render_table(block: dict, media: MediaCollector, tree: Any) -> str:
    body = block.get("table", {})
    rows: List[dict] = []
    if block.get("has_children"):
        rows = tree.children(block.get("id"))
    lines: List[str] = ["<table class='ns-table'>"]
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
            f"<{t}>{rich_text_to_html(cell)}</{t}>"
            for t, cell in zip(tags, cells)
        )
        open_thead = '<thead>' if idx == 0 and col_header else ''
        close_thead = '</thead><tbody>' if idx == 0 and col_header and len(rows) > 1 else ('</thead>' if idx == 0 and col_header else '')
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
            return f'<img src="{html.escape(url, quote=True)}">{caption}'
        return f'<p><a href="{html.escape(url, quote=True)}">{_esc(url)}</a>{caption}</p>'

    # notion-hosted file -> placeholder, downloaded at import time
    base = url.split("/")[-1].split("?")[0] or f"{btype}.bin"
    fname = f"ns-{block.get('id','').replace('-','')[:12]}-{base}"
    ph = media.add(url, fname)
    if btype == "image":
        return f'<img src="{ph}">{caption}'
    if btype == "audio":
        return f'<audio controls src="{ph}"></audio>{caption}'
    if btype == "video":
        return f'<video controls src="{ph}"></video>{caption}'
    # file: try to render as attachment link
    return f'<p><a href="{ph}">{_esc(base)}</a>{caption}</p>'
