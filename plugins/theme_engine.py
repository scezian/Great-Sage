"""
theme_engine.py — Great Sage Plugin
Reskin the sidebar nav and page headers with personality.

Themes:
  Default        — original flat dark
  Glassmorphism  — frosted panels, translucent layers, shimmer
  Neon/Cyberpunk — glowing accents, sharp neon edges
  Minimal Premium — subtle gradients, refined spacing
  Anime/Dark     — dramatic, moody, high contrast
"""

PLUGIN_NAME        = "Theme Engine"
PLUGIN_ICON        = "◈"
PLUGIN_DESCRIPTION = "Reskin the sidebar and headers — glass, neon, premium & more"
PLUGIN_VERSION     = "1.0"
PLUGIN_AUTHOR      = "Great Sage"
PLUGIN_COLOR       = "#8B6FD4"

import types
from PyQt6.QtCore    import Qt, QRectF, QPointF, QTimer
from PyQt6.QtGui     import (QColor, QPainter, QLinearGradient, QRadialGradient,
                              QBrush, QPen, QPainterPath, QFont)
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QPushButton, QScrollArea, QGridLayout,
                              QApplication, QFrame)

# ── Theme definitions ─────────────────────────────────────────────────────────
THEMES = {
    "Default": {
        "nav_bg":          "#111116",
        "nav_border":      "#252530",
        "nav_active_bg":   "#17171D",
        "nav_active_bar":  "#C9A84C",
        "nav_ico_active":  "#C9A84C",
        "nav_ico_muted":   "#606070",
        "nav_hover_bg":    "#17171D",
        "header_bg":       "#111116",
        "header_border":   "#252530",
        "mode": "flat",
        "desc": "Original flat dark",
    },
    "Glassmorphism": {
        "nav_bg":          "#0E0E1A",
        "nav_border":      "#3A3A5A",
        "nav_active_bg":   "#1E1E30",
        "nav_active_bar":  "#7B6FF5",
        "nav_ico_active":  "#A090FF",
        "nav_ico_muted":   "#4A4A6A",
        "nav_hover_bg":    "#1A1A28",
        "header_bg":       "#0E0E1A",
        "header_border":   "#2A2A4A",
        "mode": "glass",
        "desc": "Frosted panels, translucent layers",
    },
    "Neon / Cyberpunk": {
        "nav_bg":          "#07070F",
        "nav_border":      "#00FFB320",
        "nav_active_bg":   "#0A0A18",
        "nav_active_bar":  "#00FFB3",
        "nav_ico_active":  "#00FFB3",
        "nav_ico_muted":   "#1A3A2A",
        "nav_hover_bg":    "#0D0D1E",
        "header_bg":       "#07070F",
        "header_border":   "#00FFB330",
        "mode": "neon",
        "desc": "Glowing accents, sharp neon edges",
    },
    "Minimal Premium": {
        "nav_bg":          "#0C0C12",
        "nav_border":      "#1E1E2A",
        "nav_active_bg":   "#161620",
        "nav_active_bar":  "#C9A84C",
        "nav_ico_active":  "#E8D89A",
        "nav_ico_muted":   "#383848",
        "nav_hover_bg":    "#131320",
        "header_bg":       "#0C0C12",
        "header_border":   "#1A1A26",
        "mode": "premium",
        "desc": "Subtle gradients, refined spacing",
    },
    "Anime / Dark": {
        "nav_bg":          "#0A0610",
        "nav_border":      "#2A1A3A",
        "nav_active_bg":   "#150D20",
        "nav_active_bar":  "#E05A6A",
        "nav_ico_active":  "#FF7A8A",
        "nav_ico_muted":   "#3A2040",
        "nav_hover_bg":    "#120A1C",
        "header_bg":       "#0A0610",
        "header_border":   "#2A1A3A",
        "mode": "anime",
        "desc": "Dramatic, moody, high contrast",
    },
}

THEME_NAMES = list(THEMES.keys())

# ── Find MainWindow + NavRail ────────────────────────────────────────────────
def _find_main():
    for w in QApplication.topLevelWidgets():
        if hasattr(w, "_navigate"):
            return w
    return None

def _find_nav(main):
    if not main: return None
    for child in main.findChildren(QWidget):
        if child.objectName() == "NavRail":
            return child
    return None

def _find_headers(main):
    """Find all page header QWidgets (fixed height ~52, have border-bottom style)."""
    if not main: return []
    headers = []
    for child in main.findChildren(QWidget):
        ss = child.styleSheet()
        if ("border-bottom" in ss and
                (child.minimumHeight() == 52 or child.maximumHeight() == 52)):
            headers.append(child)
    return headers


# ── Nav paintEvent painters ───────────────────────────────────────────────────
def _make_nav_paint(theme):
    mode = theme["mode"]
    nav_bg  = QColor(theme["nav_bg"])
    acc_hex = theme["nav_active_bar"]

    def _paint_glass(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        # Base
        base = QColor(nav_bg)
        base.setAlpha(230)
        p.fillRect(0, 0, W, H, QBrush(base))
        # Subtle vertical shimmer
        sh = QLinearGradient(0, 0, W, 0)
        sh.setColorAt(0,   QColor(255, 255, 255, 0))
        sh.setColorAt(0.5, QColor(255, 255, 255, 8))
        sh.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(0, 0, W, H, QBrush(sh))
        # Right edge accent line
        p.setPen(QPen(QColor(theme["nav_border"]), 1))
        p.drawLine(W - 1, 0, W - 1, H)
        p.end()

    def _paint_neon(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QBrush(QColor(theme["nav_bg"])))
        # Scanlines
        p.setPen(QPen(QColor(0, 255, 179, 6)))
        for y in range(0, H, 4):
            p.drawLine(0, y, W, y)
        # Right neon border
        glow = QLinearGradient(0, 0, 0, H)
        glow.setColorAt(0,   QColor(acc_hex).darker(120))
        glow.setColorAt(0.5, QColor(acc_hex))
        glow.setColorAt(1.0, QColor(acc_hex).darker(120))
        p.setPen(QPen(QBrush(glow), 1))
        p.drawLine(W - 1, 0, W - 1, H)
        p.end()

    def _paint_premium(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        grad = QLinearGradient(0, 0, 0, H)
        grad.setColorAt(0,   QColor(theme["nav_bg"]).lighter(110))
        grad.setColorAt(1.0, QColor(theme["nav_bg"]))
        p.fillRect(0, 0, W, H, QBrush(grad))
        # Subtle right separator
        p.setPen(QPen(QColor(theme["nav_border"]), 1))
        p.drawLine(W - 1, 0, W - 1, H)
        p.end()

    def _paint_anime(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        # Dark base with dramatic top-to-bottom gradient
        grad = QLinearGradient(0, 0, 0, H)
        grad.setColorAt(0,   QColor(theme["nav_bg"]).lighter(140))
        grad.setColorAt(0.3, QColor(theme["nav_bg"]))
        grad.setColorAt(1.0, QColor(5, 3, 8))
        p.fillRect(0, 0, W, H, QBrush(grad))
        # Right accent line (red glow)
        glow = QLinearGradient(0, 0, 0, H)
        glow.setColorAt(0,   QColor(acc_hex).darker(150))
        glow.setColorAt(0.4, QColor(acc_hex))
        glow.setColorAt(1.0, QColor(acc_hex).darker(200))
        p.setPen(QPen(QBrush(glow), 1))
        p.drawLine(W - 1, 0, W - 1, H)
        p.end()

    return {
        "glass":   _paint_glass,
        "neon":    _paint_neon,
        "premium": _paint_premium,
        "anime":   _paint_anime,
    }.get(mode)


# ── Apply theme to button active state ───────────────────────────────────────
def _nav_btn_active_paint(btn_widget, theme):
    mode    = theme["mode"]
    acc     = theme["nav_active_bar"]
    bg      = theme["nav_active_bg"]
    ico_col = theme["nav_ico_active"]

    def _paint_btn_glass(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        base = QColor(bg); base.setAlpha(200)
        p.fillRect(0, 0, W, H, QBrush(base))
        # Left accent bar with glow
        for i, alpha in [(4, 30), (3, 60), (2, 120), (1, 200), (0, 255)]:
            c = QColor(acc); c.setAlpha(alpha)
            p.setPen(QPen(c, 1))
            p.drawLine(i, 0, i, H)
        p.end()

    def _paint_btn_neon(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QBrush(QColor(bg)))
        # Neon left bar
        for i, alpha in [(3, 40), (2, 100), (1, 200), (0, 255)]:
            c = QColor(acc); c.setAlpha(alpha)
            p.setPen(QPen(c, 1))
            p.drawLine(i, 4, i, H - 4)
        # Subtle neon background glow
        glow = QRadialGradient(QPointF(0, H / 2), W * 1.2)
        c = QColor(acc); c.setAlpha(25)
        glow.setColorAt(0, c)
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(0, 0, W, H, QBrush(glow))
        p.end()

    def _paint_btn_premium(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        grad = QLinearGradient(0, 0, W, 0)
        c1 = QColor(acc); c1.setAlpha(30)
        grad.setColorAt(0, c1)
        grad.setColorAt(1, QColor(bg))
        p.fillRect(0, 0, W, H, QBrush(grad))
        # Gold left accent
        for i, alpha in [(2, 80), (1, 180), (0, 255)]:
            c = QColor(acc); c.setAlpha(alpha)
            p.setPen(QPen(c, 1))
            p.drawLine(i, 6, i, H - 6)
        p.end()

    def _paint_btn_anime(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QBrush(QColor(bg)))
        # Red glow from left
        glow = QRadialGradient(QPointF(0, H / 2), W)
        c = QColor(acc); c.setAlpha(40)
        glow.setColorAt(0, c)
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(0, 0, W, H, QBrush(glow))
        # Sharp left bar
        for i, alpha in [(2, 60), (1, 180), (0, 255)]:
            c = QColor(acc); c.setAlpha(alpha)
            p.setPen(QPen(c, 1))
            p.drawLine(i, 0, i, H)
        p.end()

    paint_fn = {
        "glass":   _paint_btn_glass,
        "neon":    _paint_btn_neon,
        "premium": _paint_btn_premium,
        "anime":   _paint_btn_anime,
    }.get(mode)

    if paint_fn:
        btn_widget.paintEvent = types.MethodType(paint_fn, btn_widget)
    else:
        btn_widget.setStyleSheet(
            f"QWidget{{background:{bg};border-left:2px solid {acc};}}")

    if hasattr(btn_widget, "_ico"):
        btn_widget._ico.setStyleSheet(
            f"background:transparent;font-size:17px;color:{ico_col};")
        btn_widget._txt.setStyleSheet(
            f"background:transparent;font-size:7px;letter-spacing:0.5px;color:{ico_col};")
    btn_widget.update()


# ── Apply theme to header widget ─────────────────────────────────────────────
def _apply_header(header, theme):
    mode = theme["mode"]
    bg   = theme["header_bg"]
    bdr  = theme["header_border"]
    acc  = theme["nav_active_bar"]

    def _paint_header_glass(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        base = QColor(bg); base.setAlpha(220)
        p.fillRect(0, 0, W, H, QBrush(base))
        sh = QLinearGradient(0, 0, 0, H)
        sh.setColorAt(0, QColor(255, 255, 255, 12))
        sh.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(0, 0, W, H, QBrush(sh))
        p.setPen(QPen(QColor(bdr), 1))
        p.drawLine(0, H - 1, W, H - 1)
        p.end()

    def _paint_header_neon(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QBrush(QColor(bg)))
        # Scanlines
        p.setPen(QPen(QColor(0, 255, 179, 5)))
        for y in range(0, H, 4):
            p.drawLine(0, y, W, y)
        # Neon bottom border
        glow = QLinearGradient(0, 0, W, 0)
        glow.setColorAt(0,   QColor(acc).darker(120))
        glow.setColorAt(0.3, QColor(acc))
        glow.setColorAt(0.7, QColor(acc))
        glow.setColorAt(1.0, QColor(acc).darker(120))
        p.setPen(QPen(QBrush(glow), 1))
        p.drawLine(0, H - 1, W, H - 1)
        # Second faint line
        p.setPen(QPen(QColor(acc).darker(200), 1))
        p.drawLine(0, H - 2, W, H - 2)
        p.end()

    def _paint_header_premium(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        grad = QLinearGradient(0, 0, W, 0)
        grad.setColorAt(0,   QColor(bg).lighter(115))
        grad.setColorAt(0.5, QColor(bg))
        grad.setColorAt(1.0, QColor(bg).lighter(108))
        p.fillRect(0, 0, W, H, QBrush(grad))
        # Subtle gold separator
        sep = QLinearGradient(0, 0, W, 0)
        sep.setColorAt(0,   QColor(acc).darker(300))
        sep.setColorAt(0.2, QColor(acc).darker(150))
        sep.setColorAt(0.8, QColor(acc).darker(150))
        sep.setColorAt(1.0, QColor(acc).darker(300))
        p.setPen(QPen(QBrush(sep), 1))
        p.drawLine(0, H - 1, W, H - 1)
        p.end()

    def _paint_header_anime(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        grad = QLinearGradient(0, 0, W, 0)
        grad.setColorAt(0,   QColor(bg).lighter(160))
        grad.setColorAt(0.4, QColor(bg))
        grad.setColorAt(1.0, QColor(4, 2, 6))
        p.fillRect(0, 0, W, H, QBrush(grad))
        # Red dramatic separator
        sep = QLinearGradient(0, 0, W, 0)
        sep.setColorAt(0,   QColor(acc))
        sep.setColorAt(0.5, QColor(acc).lighter(130))
        sep.setColorAt(1.0, QColor(acc).darker(150))
        p.setPen(QPen(QBrush(sep), 1))
        p.drawLine(0, H - 1, W, H - 1)
        p.end()

    paint_fn = {
        "glass":   _paint_header_glass,
        "neon":    _paint_header_neon,
        "premium": _paint_header_premium,
        "anime":   _paint_header_anime,
    }.get(mode)

    if paint_fn:
        header.paintEvent = types.MethodType(paint_fn, header)
        header.setStyleSheet("")  # clear stylesheet so paintEvent takes over
    else:
        header.setStyleSheet(
            f"background:{bg};border-bottom:1px solid {bdr};")
    header.update()


# ── Main apply function ───────────────────────────────────────────────────────
def apply_theme(theme_name, api=None):
    theme = THEMES.get(theme_name, THEMES["Default"])
    mode  = theme["mode"]
    main  = _find_main()
    if not main:
        return

    nav = _find_nav(main)
    if nav:
        # Nav rail background
        paint_fn = _make_nav_paint(theme)
        if paint_fn:
            nav.paintEvent = types.MethodType(paint_fn, nav)
            nav.setStyleSheet("")
        else:
            nav.setStyleSheet(
                f"QFrame#NavRail{{background:{theme['nav_bg']};"
                f"border-right:1px solid {theme['nav_border']};}}")

        # Patch _apply_style on nav rail to use theme colors
        orig_apply = nav._apply_style.__func__ if hasattr(nav._apply_style, '__func__') else None

        def _themed_apply(self, widget, active):
            th = theme
            if active:
                _nav_btn_active_paint(widget, th)
            else:
                # Remove custom paintEvent
                try: del widget.paintEvent
                except AttributeError: pass
                hover = th["nav_hover_bg"]
                bdr   = th["nav_border"]
                widget.setStyleSheet(
                    f"QWidget{{background:transparent;border-left:2px solid transparent;}}"
                    f"QWidget:hover{{background:{hover};}}")
                if hasattr(widget, "_ico"):
                    muted = th["nav_ico_muted"]
                    widget._ico.setStyleSheet(
                        f"background:transparent;font-size:17px;color:{muted};")
                    widget._txt.setStyleSheet(
                        f"background:transparent;font-size:9px;letter-spacing:0.5px;color:{muted};")
            widget.update()

        nav._apply_style = types.MethodType(_themed_apply, nav)

        # Re-apply active state for current page
        active_key = nav._active
        for key, btn_w in nav._btns.items():
            nav._apply_style(btn_w, key == active_key)

        nav.update()

    # Apply to all page headers
    headers = _find_headers(main)
    for h in headers:
        _apply_header(h, theme)

    if api:
        api.save_plugin_data("theme", theme_name)


# ── Tile widget ───────────────────────────────────────────────────────────────
class _ThemeTile(QWidget):
    def __init__(self, name, selected, on_select, parent=None):
        super().__init__(parent)
        self._name     = name
        self._selected = selected
        self._on_select = on_select
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(80)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(4)
        self._name_lbl = QLabel(self._name)
        self._desc_lbl = QLabel(THEMES[self._name]["desc"])
        self._desc_lbl.setStyleSheet(
            "background:transparent;border:none;font-size:9px;letter-spacing:0.5px;")
        lay.addWidget(self._name_lbl)
        lay.addWidget(self._desc_lbl)
        self._refresh()

    def _refresh(self):
        t = THEMES[self._name]
        border = t["nav_active_bar"] if self._selected else "#252530"
        bg     = t["nav_active_bg"]  if self._selected else "#111116"
        self.setObjectName(f"tile_{self._name.replace(' ','_').replace('/','_')}")
        obj = self.objectName()
        self.setStyleSheet(f"""
            QWidget#{obj} {{
                background: {bg};
                border: 2px solid {border};
                border-radius: 8px;
            }}
        """)
        text_c = t["nav_ico_active"] if self._selected else "#A0A0B0"
        sub_c  = t["nav_active_bar"] if self._selected else "#454555"
        self._name_lbl.setStyleSheet(
            f"background:transparent;border:none;color:{text_c};"
            f"font-size:12px;font-weight:bold;letter-spacing:0.5px;")
        self._desc_lbl.setStyleSheet(
            f"background:transparent;border:none;color:{sub_c};"
            f"font-size:9px;letter-spacing:0.5px;")

    def set_selected(self, v):
        if self._selected != v:
            self._selected = v
            self._refresh()

    def mousePressEvent(self, e):
        self._on_select(self._name)
        super().mousePressEvent(e)

    def paintEvent(self, event):
        # Paint a small colour swatch of the theme's accent
        super().paintEvent(event)
        t = THEMES[self._name]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Small dot in top-right showing accent colour
        c = QColor(t["nav_active_bar"])
        p.setBrush(QBrush(c))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(self.width() - 18, 10, 8, 8)
        p.end()


# ── Plugin entry points ───────────────────────────────────────────────────────
def build_page(parent, api):
    colours     = api.colours
    saved_theme = api.load_plugin_data("theme") or "Default"
    if saved_theme not in THEMES:
        saved_theme = "Default"

    page = QWidget(parent)
    page.setStyleSheet(f"background:{colours['BG']};")
    root = QVBoxLayout(page)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)

    # Header
    hdr = QWidget()
    hdr.setStyleSheet(
        f"background:{colours['BG2']};border-bottom:1px solid {colours['BORDER']};")
    hdr.setFixedHeight(52)
    hh = QHBoxLayout(hdr); hh.setContentsMargins(28, 0, 28, 0)
    tl = QLabel("◈  THEME ENGINE")
    tl.setStyleSheet(
        f"color:{colours['ACCENT']};font-size:13px;font-weight:bold;letter-spacing:3px;")
    hh.addWidget(tl); hh.addStretch()
    root.addWidget(hdr)

    # Scrollable body
    scroll = QScrollArea(); scroll.setWidgetResizable(True)
    scroll.setStyleSheet(
        "QScrollArea{border:none;background:transparent;}"
        f"QScrollBar:vertical{{background:{colours['BG']};width:4px;border:none;}}"
        f"QScrollBar::handle:vertical{{background:{colours['BORDER']};border-radius:2px;}}")
    body = QWidget(); body.setStyleSheet("background:transparent;")
    bv = QVBoxLayout(body); bv.setContentsMargins(32, 28, 32, 28); bv.setSpacing(20)
    scroll.setWidget(body)
    root.addWidget(scroll, 1)

    def _sec(text):
        l = QLabel(text)
        l.setStyleSheet(
            f"color:{colours['MUTED']};font-size:9px;letter-spacing:3px;background:transparent;")
        return l

    bv.addWidget(_sec("CHOOSE A THEME"))

    # Grid of tiles
    grid_w = QWidget(); grid_w.setStyleSheet("background:transparent;")
    grid   = QGridLayout(grid_w)
    grid.setSpacing(10)
    grid.setContentsMargins(0, 0, 0, 0)

    tiles = {}

    def _on_select(name):
        for n, tile in tiles.items():
            tile.set_selected(n == name)
        apply_theme(name, api)

    for i, name in enumerate(THEME_NAMES):
        tile = _ThemeTile(name, name == saved_theme, _on_select)
        tiles[name] = tile
        grid.addWidget(tile, i // 2, i % 2)

    bv.addWidget(grid_w)

    note = QLabel(
        "Themes restyle the sidebar and page headers.\n"
        "Your choice is saved and applied automatically on next launch.")
    note.setStyleSheet(
        f"background:{colours['BG2']};color:{colours['TEXT2']};"
        f"font-size:12px;padding:14px;border-radius:6px;"
        f"border:1px solid {colours['BORDER']};")
    note.setWordWrap(True)
    bv.addWidget(note)
    bv.addStretch()

    # Apply saved theme immediately
    QTimer.singleShot(300, lambda: apply_theme(saved_theme, api))

    return page


def refresh(page):
    pass
