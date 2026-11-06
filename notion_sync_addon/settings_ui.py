# -*- coding: utf-8 -*-
"""Settings dialog styled after the T3 Code "Ocean" theme (light and dark).

Every primitive here (buttons, inputs, switch, number field, settings cards)
copies the geometry and colors of the matching T3 Code component:
apps/web/src/components/ui/{button,input,switch,number-field}.tsx and the
settings panels in the captured Ocean mockups.

The module only needs Qt, so it can be rendered outside Anki for previews.
"""

from __future__ import annotations

import os
import threading
from typing import Callable, List, Optional, cast

try:
    from aqt.qt import (
        QAbstractButton,
        QColor,
        QDesktopServices,
        QDialog,
        QFont,
        QFontDatabase,
        QFrame,
        QHBoxLayout,
        QIcon,
        QLabel,
        QLineEdit,
        QObject,
        QPainter,
        QPalette,
        QPixmap,
        QPushButton,
        QRectF,
        QScrollArea,
        QSize,
        QSpinBox,
        Qt,
        QToolButton,
        QUrl,
        QVariantAnimation,
        QVBoxLayout,
        QWidget,
        pyqtSignal,
    )
except ImportError:  # standalone preview
    from PyQt6.QtCore import (  # type: ignore[no-redef,unused-ignore]
        QObject,
        QRectF,
        QSize,
        Qt,
        QUrl,
        QVariantAnimation,
        pyqtSignal,
    )
    from PyQt6.QtGui import (  # type: ignore[no-redef,unused-ignore]
        QColor,
        QDesktopServices,
        QFont,
        QFontDatabase,
        QIcon,
        QPainter,
        QPalette,
        QPixmap,
    )
    from PyQt6.QtWidgets import (  # type: ignore[no-redef,unused-ignore]
        QAbstractButton,
        QDialog,
        QFrame,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QScrollArea,
        QSpinBox,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )

# --------------------------------------------------------------------- #
# Ocean palettes
#
# Source: T3 Code packages/shared/src/themePalettes.ts (OCEAN_THEME and its
# variants.dark), mapped to CSS variables the way apps/web does
# (--primary = messageAction, --accent = accentSurface, --ring = focus,
# --popover = surfaceOverlay ...). Light values were also checked against
# the captured mockups/ocean pages. Mixed values follow the component
# classes: dark inputs/outline buttons are input/32, hover input/64, card
# borders border/60, primary hover primary/90.
# --------------------------------------------------------------------- #

LIGHT = {
    "BACKGROUND": "#f5f7f8",
    "FOREGROUND": "#241523",
    "CARD": "#f7f9fa",
    "BORDER": "#cdd4dc",
    "CARD_BORDER": "#dce1e6",
    "PRIMARY": "#0a6f75",
    "PRIMARY_HOVER": "#227d82",
    "PRIMARY_PRESSED": "#09646a",
    "PRIMARY_FOREGROUND": "#fffaff",
    "MUTED_FOREGROUND": "#6f6873",
    "PLACEHOLDER": "#6c6570",
    "INPUT": "#c0c9d4",
    "RING": "#2672af",
    "ACCENT": "#d8e4ee",
    "POPOVER": "#e8e9eb",
    "OUTLINE_HOVER": "#e0e6ec",
    "FIELD": "#f5f7f8",
    "DESTRUCTIVE_FOREGROUND": "#c10007",
    "SWITCH_THUMB_OFF": "#f5f7f8",
    "INVALID_BORDER": "#f19aa0",
    "DISABLED_TEXT": "#a39fa6",
    "DISABLED_BORDER": "#d6dce3",
    "DISABLED_BG": "#eeeff1",
    "SCROLL_HOVER": "#a9b4c1",
}

DARK = {
    "BACKGROUND": "#17212b",
    "FOREGROUND": "#fffaff",
    "CARD": "#1e2832",
    "BORDER": "#405567",
    "CARD_BORDER": "#30404f",
    "PRIMARY": "#5bd0d6",
    "PRIMARY_HOVER": "#6fd6db",
    "PRIMARY_PRESSED": "#54bfc5",
    "PRIMARY_FOREGROUND": "#241523",
    "MUTED_FOREGROUND": "#969ca6",
    "PLACEHOLDER": "#a4a4ac",
    "INPUT": "#4f677b",
    "RING": "#70b9ee",
    "ACCENT": "#324e66",
    "POPOVER": "#293745",
    "OUTLINE_HOVER": "#3b4e5e",
    "FIELD": "#293745",
    "DESTRUCTIVE_FOREGROUND": "#ff6467",
    "SWITCH_THUMB_OFF": "#969ca6",
    "INVALID_BORDER": "#ab4c51",
    "DISABLED_TEXT": "#7f838a",
    "DISABLED_BORDER": "#334453",
    "DISABLED_BG": "#202c38",
    "SCROLL_HOVER": "#7b8c9c",
}

# the palette in use; widgets read it when they paint or are (re)styled
P = dict(LIGHT)


def use_theme(dark: bool) -> None:
    """Switch the palette ``P`` to the dark or light Ocean colors."""
    P.clear()
    P.update(DARK if dark else LIGHT)


FONT_FAMILIES = [
    "Inter",
    "Segoe UI Variable Text",
    "Segoe UI",
    ".AppleSystemUIFont",
    "SF Pro Text",
    "Helvetica Neue",
    "Ubuntu",
    "sans-serif",
]
FONT_STACK = ", ".join(f'"{f}"' for f in FONT_FAMILIES)
MONO_STACK = (
    '"JetBrains Mono", "SF Mono", "Cascadia Mono", Consolas, monospace'
)


def build_stylesheet(p: dict) -> str:
    """Return the dialog's Qt stylesheet for palette ``p``."""
    return f"""
QDialog#notionSyncSettings {{ background: {p['BACKGROUND']}; }}
* {{ font-family: {FONT_STACK}; }}
QLabel, QLineEdit, QSpinBox, QPushButton, QToolButton {{
    color: {p['FOREGROUND']};
}}
QLabel {{ font-size: 14px; }}
QWidget#body, QScrollArea, QScrollArea > QWidget > QWidget {{
    background: {p['BACKGROUND']};
    border: 0;
}}

QFrame#header {{
    background: {p['BACKGROUND']};
    border-bottom: 1px solid {p['BORDER']};
}}
QFrame#footer {{
    background: {p['BACKGROUND']};
    border-top: 1px solid {p['BORDER']};
}}
QLabel#crumbMuted {{ color: {p['MUTED_FOREGROUND']}; }}
QLabel#crumbSep {{ color: {p['MUTED_FOREGROUND']}; padding: 0 2px; }}
QLabel#crumb {{ color: {p['FOREGROUND']}; font-weight: 500; }}

QLabel#sectionTitle {{ font-size: 16px; font-weight: 600; }}
QLabel#sectionCount {{ font-size: 12px; color: {p['MUTED_FOREGROUND']}; }}
QLabel#rowTitle {{ font-size: 14px; font-weight: 500; }}
QLabel#rowDesc, QLabel#footerStatus {{
    font-size: 12px;
    color: {p['MUTED_FOREGROUND']};
}}
QLabel#pageId {{
    font-family: {MONO_STACK};
    font-size: 11px;
    color: {p['MUTED_FOREGROUND']};
}}
QLabel#pageName {{ font-size: 14px; font-weight: 500; }}
QLabel#pageName[pending="true"] {{
    color: {p['MUTED_FOREGROUND']};
    font-weight: 400;
}}
QLabel#errorText {{ font-size: 12px; color: {p['DESTRUCTIVE_FOREGROUND']}; }}
QLabel#problemItem {{ font-size: 12px; color: {p['FOREGROUND']}; }}
QLabel#emptyText {{ font-size: 13px; color: {p['MUTED_FOREGROUND']}; }}

QFrame#card {{
    background: {p['CARD']};
    border: 1px solid {p['CARD_BORDER']};
    border-radius: 12px;
}}
QFrame#warnCard {{
    background: {p['CARD']};
    border: 1px solid {p['INVALID_BORDER']};
    border-radius: 12px;
}}
QFrame#row {{ background: transparent; border: 0; }}
QFrame#row[divider="true"] {{
    border-top: 1px solid {p['CARD_BORDER']};
    border-radius: 0;
}}

/* ---- buttons (button.tsx) ---- */
QPushButton, QToolButton#ghostIcon {{
    font-size: 14px; font-weight: 500; outline: none;
    border-radius: 8px; min-height: 30px; padding: 0 11px;
}}
QPushButton {{
    background: {p['POPOVER']};
    border: 1px solid {p['INPUT']};
    color: {p['FOREGROUND']};
}}
QPushButton:hover {{ background: {p['OUTLINE_HOVER']}; }}
QPushButton:pressed {{ background: {p['ACCENT']}; }}
QPushButton:focus {{ border-color: {p['RING']}; }}
QPushButton:disabled {{
    color: {p['DISABLED_TEXT']};
    border-color: {p['DISABLED_BORDER']};
    background: {p['DISABLED_BG']};
}}

QPushButton#primary {{
    background: {p['PRIMARY']};
    border: 1px solid {p['PRIMARY']};
    color: {p['PRIMARY_FOREGROUND']};
}}
QPushButton#primary:hover {{
    background: {p['PRIMARY_HOVER']};
    border-color: {p['PRIMARY_HOVER']};
}}
QPushButton#primary:pressed {{ background: {p['PRIMARY_PRESSED']}; }}
QPushButton#primary:focus {{ border-color: {p['RING']}; }}

QPushButton#ghost {{ background: transparent; border: 1px solid transparent; }}
QPushButton#ghost:hover, QPushButton#ghost:pressed {{
    background: {p['ACCENT']};
}}
QPushButton#ghost:focus {{ border-color: {p['RING']}; }}

QPushButton#ghostXs {{
    background: transparent; border: 1px solid transparent;
    font-size: 12px; min-height: 22px; padding: 0 7px;
}}
QPushButton#ghostXs:hover {{ background: {p['ACCENT']}; }}

QToolButton#ghostIcon {{
    background: transparent; border: 1px solid transparent;
    min-height: 26px;
    min-width: 26px;
    max-height: 26px;
    max-width: 26px;
    padding: 0;
}}
QToolButton#ghostIcon:hover {{ background: {p['ACCENT']}; }}
QToolButton#ghostIcon:checked {{ background: {p['ACCENT']}; }}

/* ---- inputs (input.tsx / number-field.tsx) ---- */
QLineEdit#input {{
    background: {p['FIELD']};
    border: 1px solid {p['INPUT']};
    border-radius: 8px;
    min-height: 30px; padding: 0 11px; font-size: 14px;
    selection-background-color: {p['PRIMARY']};
    selection-color: {p['PRIMARY_FOREGROUND']};
}}
QLineEdit#input:focus {{ border-color: {p['RING']}; }}
QLineEdit#input[invalid="true"] {{ border-color: {p['INVALID_BORDER']}; }}

QFrame#numberField {{
    background: {p['FIELD']};
    border: 1px solid {p['INPUT']};
    border-radius: 8px;
}}
QFrame#numberField[focused="true"] {{ border-color: {p['RING']}; }}
QFrame#numberField QSpinBox {{
    background: transparent; border: 0; padding: 0; min-height: 30px;
    font-size: 14px; selection-background-color: {p['PRIMARY']};
}}
QFrame#numberField QSpinBox::up-button,
QFrame#numberField QSpinBox::down-button {{
    width: 0; height: 0; border: 0; subcontrol-origin: border;
}}
QToolButton#stepperLeft {{
    background: transparent; border: 0; padding: 0 10px; min-height: 30px;
    border-top-left-radius: 7px; border-bottom-left-radius: 7px;
}}
QToolButton#stepperLeft:hover {{ background: {p['ACCENT']}; }}
QToolButton#stepperRight {{
    background: transparent; border: 0; padding: 0 10px; min-height: 30px;
    border-top-right-radius: 7px; border-bottom-right-radius: 7px;
}}
QToolButton#stepperRight:hover {{ background: {p['ACCENT']}; }}

/* ---- scrollbars ---- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {p['INPUT']};
    border-radius: 3px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {p['SCROLL_HOVER']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{
    background: {p['POPOVER']};
    color: {p['FOREGROUND']};
    border: 1px solid {p['BORDER']};
    padding: 4px 8px; font-size: 12px;
}}
"""


# --------------------------------------------------------------------- #
# lucide icons (stroke 2, 24x24 viewBox)
# --------------------------------------------------------------------- #

_ICONS = {
    "eye": '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 '
    '0 '
    '1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/><circle cx="12" cy="12" '
    'r="3"/>',
    "eye-off": '<path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 '
    '0 1 0 .696 '
    '10.747 10.747 0 0 1-1.444 2.49"/><path d="M14.084 14.158a3 3 0 0 '
    '1-4.242-4.242"/>'
    '<path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 '
    '10.75 10.75 0 0 1 4.446-5.143"/><path d="m2 2 20 20"/>',
    "trash": '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 '
    '0-2-1-2-2V6"/>'
    '<path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "minus": '<path d="M5 12h14"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>'
    '<path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 '
    '1-6.74-2.74L3 16"/>'
    '<path d="M8 16H3v5"/>',
    "file": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 '
    '2-2V7Z"/>'
    '<path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 '
    '13H8"/>'
    '<path d="M16 17H8"/>',
    "external": '<path d="M15 3h6v6"/><path d="M10 14 21 3"/>'
    '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 '
    '21h16a2 2 0 0 0 '
    '1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
}


_FONT_LOADED = False


def _load_inter() -> None:
    """Register bundled Inter (T3 Code's UI font) once.

    These are Inter's hinted static TTFs: the variable/web build has no
    TrueType hints and looks soft at 1x on Windows, where Anki renders.
    """
    global _FONT_LOADED
    if _FONT_LOADED:
        return
    _FONT_LOADED = True
    folder = os.path.join(os.path.dirname(__file__), "fonts")
    for weight in ("Regular", "Medium", "SemiBold"):
        path = os.path.join(folder, f"Inter-{weight}.ttf")
        if os.path.exists(path):
            QFontDatabase.addApplicationFont(path)


def icon(name: str, color: str = "", size: int = 16, scale: int = 3) -> QIcon:
    """Render a lucide icon as a crisp QIcon (supersampled for HiDPI)."""
    color = color or P["MUTED_FOREGROUND"]
    px = size * scale
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{px}" height="{px}" '
        f'viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round">{_ICONS[name]}</svg>'
    )
    pixmap = QPixmap()
    pixmap.loadFromData(svg.encode("utf-8"), "SVG")
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


def set_icon(
    widget: QWidget, name: str, role: str = "MUTED_FOREGROUND", size: int = 16
) -> None:
    """Give a button or label a lucide icon in a palette color.

    The spec is remembered on the widget so ``refresh_icons`` can redraw it
    in the new palette when Anki switches between light and dark.
    """
    setattr(widget, "_ns_icon", (name, role, size))
    rendered = icon(name, P[role], size)
    if isinstance(widget, QLabel):
        widget.setPixmap(rendered.pixmap(QSize(size, size)))
    else:  # a QPushButton or QToolButton
        btn = cast(QAbstractButton, widget)
        btn.setIcon(rendered)
        btn.setIconSize(QSize(size, size))


def refresh_icons(root: QWidget) -> None:
    """Redraw every icon under ``root`` in the current palette."""
    for widget in root.findChildren(QWidget):
        spec = getattr(widget, "_ns_icon", None)
        if spec:
            set_icon(widget, *spec)


def _repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# --------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------- #


class Switch(QAbstractButton):
    """switch.tsx: 30x18 track, 14px thumb; primary when on, input when off."""

    TRACK_W, TRACK_H, THUMB = 30, 18, 14

    def __init__(
        self, checked: bool = False, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self.TRACK_W, self.TRACK_H)
        self._pos = 1.0 if checked else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._animate)

    def _on_anim(self, value) -> None:
        self._pos = float(value)
        self.update()

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def sizeHint(self) -> QSize:
        """Return the fixed track size."""
        return QSize(self.TRACK_W, self.TRACK_H)

    def paintEvent(self, _event) -> None:
        """Paint the track, focus ring and thumb."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off, on = QColor(P["INPUT"]), QColor(P["PRIMARY"])
        t = self._pos
        track = QColor(
            int(off.red() + (on.red() - off.red()) * t),
            int(off.green() + (on.green() - off.green()) * t),
            int(off.blue() + (on.blue() - off.blue()) * t),
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, self.TRACK_W, self.TRACK_H), 9, 9)
        if self.hasFocus():
            p.setPen(QColor(P["RING"]))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(
                QRectF(0.5, 0.5, self.TRACK_W - 1, self.TRACK_H - 1), 8.5, 8.5
            )
            p.setPen(Qt.PenStyle.NoPen)
        x = 2 + (self.TRACK_W - 4 - self.THUMB) * t
        # shadow-sm/5
        p.setBrush(QColor(0, 0, 0, 18))
        p.drawEllipse(QRectF(x, 2.6, self.THUMB, self.THUMB))
        # dark mode: muted-foreground thumb while off (switch.tsx)
        thumb_off, thumb_on = QColor(P["SWITCH_THUMB_OFF"]), QColor(
            P["BACKGROUND"]
        )
        p.setBrush(thumb_on if t > 0.5 else thumb_off)
        p.drawEllipse(QRectF(x, 2, self.THUMB, self.THUMB))
        p.end()


class NumberField(QFrame):
    """number-field.tsx: [ - | value | + ] inside one bordered control."""

    def __init__(
        self,
        minimum: int,
        maximum: int,
        value: int,
        step: int = 5,
        suffix: str = "",
        special: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("numberField")
        self.spin = QSpinBox()
        self.spin.setRange(minimum, maximum)
        self.spin.setSingleStep(step)
        self.spin.setValue(value)
        self.spin.setSuffix(suffix)
        if special:
            self.spin.setSpecialValueText(special)
        self.spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spin.setFrame(False)
        self.spin.installEventFilter(self)

        dec = QToolButton()
        dec.setObjectName("stepperLeft")
        set_icon(dec, "minus", "FOREGROUND")
        dec.setAutoRepeat(True)
        dec.clicked.connect(self.spin.stepDown)
        inc = QToolButton()
        inc.setObjectName("stepperRight")
        set_icon(inc, "plus", "FOREGROUND")
        inc.setAutoRepeat(True)
        inc.clicked.connect(self.spin.stepUp)
        for b in (dec, inc):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(dec)
        lay.addWidget(self.spin, 1)
        lay.addWidget(inc)
        self.setFixedWidth(150)

    def eventFilter(self, obj, event) -> bool:
        """Show the focus ring while the inner spin box has focus."""
        if obj is self.spin and event.type() in (
            event.Type.FocusIn,
            event.Type.FocusOut,
        ):
            self.setProperty("focused", event.type() == event.Type.FocusIn)
            _repolish(self)
        return False

    def value(self) -> int:
        """Return the current value."""
        return self.spin.value()


def text_input(placeholder: str = "", text: str = "") -> QLineEdit:
    """Create an input.tsx text field."""
    edit = QLineEdit(text)
    edit.setObjectName("input")
    edit.setPlaceholderText(placeholder)
    style_placeholder(edit)
    return edit


def style_placeholder(edit: QLineEdit) -> None:
    """Color the placeholder text from the current palette."""
    pal = edit.palette()
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(P["PLACEHOLDER"]))
    edit.setPalette(pal)


def button(
    text: str, variant: str = "outline", icon_name: str = ""
) -> QPushButton:
    """Create a button.tsx button: outline, primary, ghost or ghostXs."""
    btn = QPushButton(text)
    if variant != "outline":
        btn.setObjectName(variant)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:
        role = (
            "PRIMARY_FOREGROUND"
            if variant == "primary"
            else "MUTED_FOREGROUND"
        )
        set_icon(btn, icon_name, role, 14 if variant == "ghostXs" else 16)
    return btn


def icon_button(icon_name: str, tooltip: str) -> QToolButton:
    """Create a ghost icon-only button with a tooltip."""
    btn = QToolButton()
    btn.setObjectName("ghostIcon")
    set_icon(btn, icon_name)
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def label(text: str, name: str) -> QLabel:
    """Create a label styled by its object name."""
    lbl = QLabel(text)
    lbl.setObjectName(name)
    return lbl


class Card(QFrame):
    """Settings card: rounded-xl border, rows separated by a 1px divider."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)

    def rows(self) -> List[QFrame]:
        """Return the card's rows, top to bottom."""
        return [self._lay.itemAt(i).widget() for i in range(self._lay.count())]

    def add_row(self, row: QFrame, index: int = -1) -> None:
        """Insert ``row`` (at the end by default)."""
        if index < 0:
            self._lay.addWidget(row)
        else:
            self._lay.insertWidget(index, row)
        self.refresh_dividers()

    def refresh_dividers(self) -> None:
        """Draw a divider above every row but the first."""
        for i, row in enumerate(self.rows()):
            row.setProperty("divider", i > 0)
            _repolish(row)


def setting_row(
    title: str,
    description: str = "",
    control: Optional[QWidget] = None,
    below: Optional[QWidget] = None,
) -> QFrame:
    """Title + muted description on the left, control on the right."""
    row = QFrame()
    row.setObjectName("row")
    outer = QVBoxLayout(row)
    outer.setContentsMargins(16, 13, 16, 14)
    outer.setSpacing(10)
    top = QHBoxLayout()
    top.setSpacing(24)
    text = QVBoxLayout()
    text.setSpacing(3)
    text.addWidget(label(title, "rowTitle"))
    if description:
        desc = label(description, "rowDesc")
        desc.setWordWrap(True)
        text.addWidget(desc)
    top.addLayout(text, 1)
    if control is not None:
        top.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    outer.addLayout(top)
    if below is not None:
        outer.addWidget(below)
    return row


# --------------------------------------------------------------------- #
# dialog
# --------------------------------------------------------------------- #


class _NameSignals(QObject):
    resolved = pyqtSignal(str, str)


# One long-lived emitter, created on the main thread and never freed: a
# lookup thread that outlives its dialog only holds a reference to it, so
# the QObject can't be destroyed on that thread. Each dialog connects to
# it; Qt drops the connection when the dialog is deleted, so a late name
# goes nowhere.
_NAME_SIGNALS: List[_NameSignals] = []


def _name_signals() -> _NameSignals:
    """Return the shared emitter; call on the main thread."""
    if _NAME_SIGNALS:
        try:
            _NAME_SIGNALS[0].objectName()
            return _NAME_SIGNALS[0]
        except RuntimeError:  # its QApplication is gone (tests, previews)
            _NAME_SIGNALS.clear()
    _NAME_SIGNALS.append(_NameSignals())
    return _NAME_SIGNALS[0]


class SettingsDialog(QDialog):
    """Notion Sync settings, laid out like a T3 Code settings panel.

    The constructor takes:
        cfg: the add-on config dict.
        canonical_page_id: parses a URL/ID, raising on invalid input.
        lookup_name: (page_id, token) -> page title; called off the UI thread.
        on_save: receives the new config dict.
        on_sync: called after a successful "Sync now" save.
        problems: what the last sync could not read, shown above the pages.
    """

    def __init__(
        self,
        cfg: dict,
        canonical_page_id: Callable[[str], str],
        lookup_name: Callable[[str, str], str],
        on_save: Callable[[dict], None],
        on_sync: Callable[[], None],
        parent: Optional[QWidget] = None,
        dark: bool = False,
        problems: Optional[List[str]] = None,
    ) -> None:
        super().__init__(parent)
        use_theme(dark)
        self.setObjectName("notionSyncSettings")
        self.setWindowTitle("Notion Sync Settings")
        _load_inter()
        font = QFont()
        font.setFamilies(FONT_FAMILIES)
        font.setPixelSize(14)
        self.setFont(font)
        self.setStyleSheet(build_stylesheet(P))
        self.resize(680, 830)
        self.setMinimumWidth(560)

        self._canonical = canonical_page_id
        self._lookup = lookup_name
        self._on_save = on_save
        self._on_sync = on_sync
        self._signals = _name_signals()
        self._signals.resolved.connect(self._apply_name)
        self._problems = list(problems or [])
        self._page_rows: dict = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        body = QWidget()
        body.setObjectName("body")
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(28, 22, 28, 28)
        self._body.setSpacing(0)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self._build_connection(cfg)
        self._build_sync(cfg)
        self._build_pages(cfg)
        self._body.addStretch(1)

        root.addWidget(self._build_footer())
        self._update_status()

    def apply_theme(self, dark: bool) -> None:
        """Restyle in place when Anki switches between light and dark."""
        use_theme(dark)
        self.setStyleSheet(build_stylesheet(P))
        refresh_icons(self)
        for edit in self.findChildren(QLineEdit):
            style_placeholder(edit)
        for switch in self.findChildren(Switch):
            switch.update()

    # ---- layout pieces ------------------------------------------------ #

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(48)
        lay = QHBoxLayout(header)
        lay.setContentsMargins(20, 0, 14, 0)
        lay.setSpacing(6)
        lay.addWidget(label("Settings", "crumbMuted"))
        lay.addWidget(label("/", "crumbSep"))
        lay.addWidget(label("Notion Sync", "crumb"))
        lay.addStretch(1)
        integrations = button("Notion integrations", "ghostXs", "external")
        integrations.setToolTip(
            "Open notion.so/my-integrations to create or copy a token"
        )
        integrations.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://www.notion.so/my-integrations")
            )
        )
        lay.addWidget(integrations)
        return header

    def _section(self, title: str, first: bool = False) -> QLabel:
        head = QHBoxLayout()
        head.setContentsMargins(16, 0 if first else 26, 16, 10)
        head.setSpacing(8)
        head.addWidget(label(title, "sectionTitle"))
        count = label("", "sectionCount")
        head.addWidget(count, 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        self._body.addLayout(head)
        return count

    def _build_connection(self, cfg: dict) -> None:
        self._section("Connection", first=True)
        card = Card()
        self.token = text_input(
            "Paste your Notion integration token",
            str(cfg.get("notion_token") or ""),
        )
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        reveal = icon_button("eye", "Show token")
        reveal.setCheckable(True)

        def toggle(visible: bool) -> None:
            self.token.setEchoMode(
                QLineEdit.EchoMode.Normal
                if visible
                else QLineEdit.EchoMode.Password
            )
            set_icon(reveal, "eye-off" if visible else "eye")
            reveal.setToolTip("Hide token" if visible else "Show token")

        reveal.toggled.connect(toggle)
        token_row = QWidget()
        tl = QHBoxLayout(token_row)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(6)
        tl.addWidget(self.token, 1)
        tl.addWidget(reveal)
        card.add_row(
            setting_row(
                "Notion token",
                "Internal integration secret. Share each page with the "
                "integration in Notion.",
                below=token_row,
            )
        )
        self._body.addWidget(card)

    def _build_sync(self, cfg: dict) -> None:
        self._section("Sync")
        card = Card()
        self.interval = NumberField(
            0,
            10080,
            max(0, int(cfg.get("sync_every_minutes") or 0)),
            step=5,
            suffix=" min",
            special="Off",
        )
        self.interval.spin.valueChanged.connect(
            lambda _v: self._update_status()
        )
        card.add_row(
            setting_row(
                "Auto-sync",
                "Check Notion on a timer while Anki is open. Set to Off to "
                "sync manually.",
                control=self.interval,
            )
        )
        self.with_ankiweb = Switch(bool(cfg.get("sync_with_ankiweb", False)))
        card.add_row(
            setting_row(
                "Anki's Sync button also syncs Notion",
                "Pressing Sync in Anki pulls Notion too, so the separate "
                "Notion Sync button is hidden. New cards are uploaded with a "
                "second AnkiWeb sync.",
                control=self.with_ankiweb,
            )
        )
        self.empty_cards = Switch(bool(cfg.get("include_empty_cards", True)))
        card.add_row(
            setting_row(
                "Untitled rows",
                "Turn rows without a title into cards with an empty front.",
                control=self.empty_cards,
            )
        )
        self._body.addWidget(card)

    def _build_problems(self) -> None:
        """Warning card: what the last sync couldn't read."""
        card = Card()
        card.setObjectName("warnCard")
        row = QFrame()
        row.setObjectName("row")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(16, 13, 16, 14)
        lay.setSpacing(12)
        glyph = QLabel()
        set_icon(glyph, "alert", "DESTRUCTIVE_FOREGROUND")
        glyph.setContentsMargins(0, 2, 0, 0)
        lay.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(3)
        text.addWidget(
            label("The last sync couldn't read everything", "rowTitle")
        )
        desc = label(
            "Cards from these parts are kept as they are. Cards deleted "
            "there in Notion are removed once a sync can read them again.",
            "rowDesc",
        )
        desc.setWordWrap(True)
        text.addWidget(desc)
        text.addSpacing(4)
        shown = self._problems[:6]
        for problem in shown:
            item = label(f"\u2022  {problem}", "problemItem")
            item.setTextFormat(Qt.TextFormat.PlainText)  # user data
            item.setWordWrap(True)
            item.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            text.addWidget(item)
        if len(self._problems) > len(shown):
            text.addWidget(
                label(
                    f"... and {len(self._problems) - len(shown)} more",
                    "rowDesc",
                )
            )
        lay.addLayout(text, 1)
        card.add_row(row)
        self._body.addWidget(card)
        self._body.addSpacing(10)
        self.problems_card = card

    def _build_pages(self, cfg: dict) -> None:
        self._page_count = self._section("Notion pages")
        if self._problems:
            self._build_problems()
        self.pages_card = Card()

        self._empty_row = setting_row(
            "No pages yet", "Add a page below. Each page becomes a deck."
        )
        self._empty_row.findChild(QLabel, "rowTitle").setObjectName(
            "emptyText"
        )
        self.pages_card.add_row(self._empty_row)

        self.page_input = text_input(
            "https://www.notion.so/... or 32-character page ID"
        )
        self.page_input.returnPressed.connect(self._add_page)
        self.page_input.textChanged.connect(self._clear_error)
        add = button("Add page", "outline", "plus")
        add.clicked.connect(self._add_page)
        add_box = QWidget()
        al = QVBoxLayout(add_box)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(6)
        line = QHBoxLayout()
        line.setSpacing(8)
        line.addWidget(self.page_input, 1)
        line.addWidget(add)
        al.addLayout(line)
        self.page_error = label("", "errorText")
        self.page_error.hide()
        al.addWidget(self.page_error)
        self._add_row = QFrame()
        self._add_row.setObjectName("row")
        arl = QVBoxLayout(self._add_row)
        arl.setContentsMargins(16, 12, 16, 12)
        arl.addWidget(add_box)
        self.pages_card.add_row(self._add_row)
        self._body.addWidget(self.pages_card)

        for entry in cfg.get("notion_pages", []):
            value = (
                entry if isinstance(entry, str) else entry.get("page_id", "")
            )
            if not value:
                continue
            try:
                self._insert_page(self._canonical(str(value)))
            except Exception:
                self._insert_page(
                    str(value), name="Invalid page ID", resolve=False
                )

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("footer")
        lay = QHBoxLayout(footer)
        lay.setContentsMargins(20, 12, 20, 12)
        lay.setSpacing(8)
        self.status = label("", "footerStatus")
        lay.addWidget(self.status)
        lay.addStretch(1)
        cancel = button("Cancel", "ghost")
        apply = button("Apply")
        sync_now = button("Sync now", "outline", "refresh")
        save = button("Save", "primary")
        save.setDefault(True)
        for b in (cancel, apply, sync_now, save):
            lay.addWidget(b)
        cancel.clicked.connect(self.reject)
        apply.clicked.connect(self.save)
        save.clicked.connect(lambda: self.save() and self.accept())
        sync_now.clicked.connect(
            lambda: self.save() and (self.accept(), self._on_sync())
        )
        return footer

    # ---- pages -------------------------------------------------------- #

    def page_ids(self) -> List[str]:
        """Return the listed page ids, in order."""
        return list(self._page_rows)

    def _insert_page(
        self, page_id: str, name: str = "", resolve: bool = True
    ) -> None:
        row = QFrame()
        row.setObjectName("row")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(16, 10, 10, 10)
        lay.setSpacing(12)
        glyph = QLabel()
        set_icon(glyph, "file")
        glyph.setContentsMargins(0, 3, 0, 0)
        lay.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        title = label(name or "Loading page name...", "pageName")
        title.setProperty("pending", not name)
        text.addWidget(title)
        text.addWidget(label(page_id, "pageId"))
        lay.addLayout(text, 1)
        remove = icon_button("trash", "Remove page")
        remove.clicked.connect(lambda: self._remove_page(page_id))
        lay.addWidget(remove, 0, Qt.AlignmentFlag.AlignVCenter)
        # pages go above the empty-state and add rows
        self.pages_card.add_row(row, len(self._page_rows))
        self._page_rows[page_id] = (row, title)
        self._update_status()
        if resolve:
            token = self.token.text()
            # the thread only holds these two, never the dialog itself
            lookup, signals = self._lookup, self._signals

            def work() -> None:
                try:
                    result = lookup(page_id, token) or "Untitled page"
                except Exception:
                    result = "Name unavailable"
                try:
                    signals.resolved.emit(page_id, result)
                except RuntimeError:
                    pass

            threading.Thread(target=work, daemon=True).start()

    def _apply_name(self, page_id: str, name: str) -> None:
        entry = self._page_rows.get(page_id)
        if not entry:
            return
        try:
            entry[1].setText(name)
            entry[1].setProperty("pending", False)
            _repolish(entry[1])
        except RuntimeError:  # row or dialog already deleted
            pass

    def _remove_page(self, page_id: str) -> None:
        row, _ = self._page_rows.pop(page_id)
        row.setParent(None)
        row.deleteLater()
        self.pages_card.refresh_dividers()
        self._update_status()

    def _show_error(self, message: str) -> None:
        self.page_error.setText(message)
        self.page_error.show()
        self.page_input.setProperty("invalid", True)
        _repolish(self.page_input)

    def _clear_error(self, *_args) -> None:
        if self.page_error.isVisible():
            self.page_error.hide()
            self.page_input.setProperty("invalid", False)
            _repolish(self.page_input)

    def _add_page(self) -> None:
        raw = self.page_input.text().strip()
        if not raw:
            return
        try:
            page_id = self._canonical(raw)
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
            return
        if page_id in self._page_rows:
            self._show_error("That page is already in the list.")
            return
        self._insert_page(page_id)
        self.page_input.clear()

    # ---- state -------------------------------------------------------- #

    def _update_status(self) -> None:
        n = len(self._page_rows)
        if hasattr(self, "_page_count"):
            self._page_count.setText(str(n))
            self._empty_row.setVisible(n == 0)
        if hasattr(self, "status"):
            minutes = self.interval.value()
            auto = (
                f"auto-sync every {minutes} min"
                if minutes
                else "auto-sync off"
            )
            self.status.setText(f"{n} page{'s' if n != 1 else ''} · {auto}")

    def config(self) -> dict:
        """Return the config as currently entered."""
        return {
            "notion_token": self.token.text().strip(),
            "notion_pages": self.page_ids(),
            "include_empty_cards": self.empty_cards.isChecked(),
            "sync_with_ankiweb": self.with_ankiweb.isChecked(),
            "sync_every_minutes": self.interval.value(),
        }

    def save(self) -> bool:
        """Pass the config to ``on_save``; False if it isn't complete."""
        if not self._page_rows:
            self._show_error("Add at least one Notion page before saving.")
            self.page_input.setFocus()
            return False
        self._on_save(self.config())
        return True
