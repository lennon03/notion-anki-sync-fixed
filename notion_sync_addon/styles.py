# -*- coding: utf-8 -*-
"""Card style presets for the add-on's note types.

The CSS is written to a note type only when the user picks a preset in the
settings (and when the add-on creates a note type), never on every sync, so
a style edited by hand in Anki's card editor is left alone.

No Anki or Qt import at module level.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

DEFAULT_PRESET = "classic"

# exactly the CSS the add-on has always written: existing note types are
# already "classic"
_CLASSIC = (
    ".card { font-family: arial; font-size: 20px; line-height: 1.5; "
    "text-align: center; color: black; background-color: white; }"
)

# Shared by the presets below: code blocks, callouts and tables. Token
# colors come from the --ns-code-* variables defined per preset.
_BLOCKS = """
.ns-code { font-family: "JetBrains Mono", "Cascadia Code", Consolas, Menlo,
  monospace; font-size: 0.85em; line-height: 1.5; text-align: left;
  background: var(--ns-code-bg); color: var(--ns-fg);
  border: 1px solid var(--ns-border); border-radius: 8px;
  padding: 10px 12px; margin: 0.6em 0; overflow-x: auto;
  white-space: pre; }
code { font-family: "JetBrains Mono", "Cascadia Code", Consolas, Menlo,
  monospace; font-size: 0.88em; background: var(--ns-code-bg);
  border: 1px solid var(--ns-border); border-radius: 4px;
  padding: 0.05em 0.35em; }
pre.ns-code code { background: none; border: 0; padding: 0; }
.ns-callout { text-align: left; background: var(--ns-card);
  border: 1px solid var(--ns-border); border-left: 4px solid var(--ns-accent);
  border-radius: 8px; padding: 10px 14px; margin: 0.6em 0; }
.ns-table { border-collapse: collapse; margin: 0.6em auto; }
.ns-table th, .ns-table td { border: 1px solid var(--ns-border);
  padding: 6px 10px; text-align: left; }
.ns-table th { background: var(--ns-card); font-weight: 600; }
details { text-align: left; margin: 0.4em 0; }
summary { cursor: pointer; font-weight: 600; }
hr#answer { border: 0; border-top: 1px solid var(--ns-border); }
img { max-width: 100%; }
a { color: var(--ns-accent); }
"""

# colors: settings_ui LIGHT / DARK palettes
_T3_OCEAN = ("""
:root { --ns-bg: #f5f7f8; --ns-fg: #241523; --ns-card: #f7f9fa;
  --ns-border: #cdd4dc; --ns-accent: #0a6f75; --ns-code-bg: #e8edf1;
  --ns-code-comment: #6f6873; --ns-code-string: #0a6f75;
  --ns-code-number: #2672af; --ns-code-keyword: #c10007; }
.nightMode, .night_mode { --ns-bg: #17212b; --ns-fg: #fffaff;
  --ns-card: #1e2832; --ns-border: #405567; --ns-accent: #5bd0d6;
  --ns-code-bg: #293745; --ns-code-comment: #969ca6;
  --ns-code-string: #5bd0d6; --ns-code-number: #70b9ee;
  --ns-code-keyword: #ff6467; }
.card { font-family: Inter, "Segoe UI", system-ui, -apple-system,
  "Helvetica Neue", Arial, sans-serif; font-size: 20px; line-height: 1.55;
  text-align: center; color: var(--ns-fg); background-color: var(--ns-bg); }
""" + _BLOCKS).strip()

_MINIMAL = ("""
:root { --ns-bg: #ffffff; --ns-fg: #1f2328; --ns-card: #f6f8fa;
  --ns-border: #d0d7de; --ns-accent: #0969da; --ns-code-bg: #f6f8fa;
  --ns-code-comment: #6e7781; --ns-code-string: #0a3069;
  --ns-code-number: #0550ae; --ns-code-keyword: #cf222e; }
.nightMode, .night_mode { --ns-bg: #1e1e1e; --ns-fg: #e6e6e6;
  --ns-card: #2a2a2a; --ns-border: #444444; --ns-accent: #58a6ff;
  --ns-code-bg: #2a2a2a; --ns-code-comment: #8b949e;
  --ns-code-string: #a5d6ff; --ns-code-number: #79c0ff;
  --ns-code-keyword: #ff7b72; }
.card { font-family: system-ui, -apple-system, "Segoe UI", Roboto,
  "Helvetica Neue", Arial, sans-serif; font-size: 18px; line-height: 1.6;
  text-align: left; max-width: 42em; margin: 0 auto; padding: 0 12px;
  color: var(--ns-fg); background-color: var(--ns-bg); }
""" + _BLOCKS).strip()

PRESETS: Dict[str, str] = {
    "classic": _CLASSIC,
    "t3-ocean": _T3_OCEAN,
    "minimal": _MINIMAL,
}

PRESET_LABELS: Dict[str, str] = {
    "classic": "Classic",
    "t3-ocean": "T3 Ocean",
    "minimal": "Minimal",
    "custom": "Custom (keep my CSS)",
}

SAMPLE_CARD_HTML = (
    '<div class="card">'
    "<p>What does <b>Python's</b> <code>enumerate</code> return?</p>"
    "<hr id=answer>"
    "<p>An iterator of <i>(index, item)</i> pairs.</p>"
    "<ul><li>starts at 0 by default</li><li>takes a <code>start</code> "
    "argument</li></ul>"
    '<pre class="ns-code"><code>for i, name in enumerate(names):\n'
    '    # first item is 0\n    print(i, "name", 42)</code></pre>'
    '<table class="ns-table"><tr><th>Call</th><th>Result</th></tr>'
    "<tr><td><code>enumerate('ab')</code></td><td>(0,'a'), (1,'b')</td></tr>"
    "</table>"
    '<div class="ns-callout">Tip: prefer <code>enumerate</code> over '
    "<code>range(len(x))</code>.</div>"
    "<details><summary>Toggle</summary>Hidden details live here.</details>"
    "</div>"
)


def css_for(preset: str) -> Optional[str]:
    """Return the preset's CSS; ``None`` for "custom" and unknown names."""
    return PRESETS.get(preset)


def preview_document(preset: str, dark: bool) -> str:
    """Return a full HTML document showing the sample card in ``preset``."""
    css = css_for(preset) or PRESETS[DEFAULT_PRESET]
    cls = "nightMode night_mode" if dark else ""
    return (
        "<!doctype html><html><head><meta charset=utf-8>"
        f"<style>{css}</style></head>"
        f'<body class="{cls}">{SAMPLE_CARD_HTML}</body></html>'
    )


def apply_preset(
    col: Any,
    preset: str,
    model_ids: Iterable[int],
    undo_name: str = "Notion Sync: Card Style",
) -> bool:
    """Write the preset's CSS to the given note types in one undo step.

    The undo entry is created only if a note type changes. Returns whether
    anything changed; "custom" changes nothing.
    """
    css = css_for(preset)
    if css is None:
        return False
    start: Optional[int] = None
    for model_id in model_ids:
        model = col.models.get(model_id)
        if model is None or model.get("css") == css:
            continue
        if start is None:
            start = col.add_custom_undo_entry(undo_name)
        model["css"] = css
        col.models.save(model)
    if start is None:
        return False
    col.merge_undo_entries(start)
    return True
