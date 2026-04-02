"""
gs_theme.py — Great Sage
========================
Design tokens, colour palette, runtime constants, and global QSS stylesheet.
Import everything with:  from gs_theme import *
"""

# ── Runtime constants ───────────────────────────────────────────────────────────
MPV_SOCKET_PATH = "/tmp/mpvsocket_gs"  # Unix socket for mpv IPC

# ── Design tokens — Psychic Purple Cyberpunk ───────────────────────────────
# Deep purple palette: near-black backgrounds, purple neon accents,
# mystical violet highlights, sharp contrast hierarchy.

BG      = "#0a0015"   # near-black, purple undertone
BG2     = "#1a0a2e"   # cards / elevated surfaces
BG3     = "#2a1a3e"   # inputs / list rows
PANEL   = "#0f0a1a"   # panels
BORDER  = "#6a1b9a"   # subtle borders
BORDER2 = "#9c27b0"   # active borders

ACCENT  = "#ba68c8"   # violet purple — primary CTA
ACCENT2 = "#e1bee7"   # light purple — secondary highlights
NEON    = "#f3e5f5"   # bright lavender
RED     = "#e91e63"
BLUE    = "#ba68c8"   # repurposed as purple
PURPLE  = "#4a148c"

TEXT    = "#ffffff"   # pure white — primary text
TEXT2   = "#e1bee7"   # light purple — secondary text
MUTED   = "#ce93d8"   # muted purple — hints, labels

FONT_BODY    = "Palatino Linotype, Palatino, Book Antiqua, Georgia, serif"
FONT_UI      = "JetBrains Mono, Fira Code, Consolas, monospace"
FONT_DISPLAY = "Palatino Linotype, Palatino, Book Antiqua, serif"

# ── Global stylesheet ──────────────────────────────────────────────────────────
QSS = f"""
* {{ font-family:{FONT_UI}; font-size:14px; color:{TEXT}; }}
QMainWindow, QWidget, QDialog {{ background:{BG}; }}
QToolTip {{
    background:{PANEL}; border:1px solid {BORDER2}; color:{TEXT};
    padding:4px 8px; font-size:11px;
}}

/* ── Buttons ── */
QPushButton {{
    background: transparent;
    color: {TEXT2};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 8px 16px;
    letter-spacing: 1px;
    text-transform: uppercase;
}}
QPushButton:hover {{
    background: {BG3};
    border-color: {ACCENT2};
    color: {ACCENT2};
    box-shadow: 0 0 10px rgba(186, 104, 200, 0.3);
}}
QPushButton:pressed {{ background: {PANEL}; }}
QPushButton#accent {{
    background: linear-gradient(135deg, {ACCENT2}, {NEON});
    color: {BG};
    border: none;
    font-weight: bold;
    letter-spacing: 2px;
    text-transform: uppercase;
}}
QPushButton#accent:hover {{ background: linear-gradient(135deg, {NEON}, {ACCENT2}); }}
QPushButton#danger {{
    background: transparent;
    color: {RED};
    border: 1px solid {RED};
}}
QPushButton#danger:hover {{ background: rgba(233, 30, 99, 0.1); border-color: {RED}; }}

/* ── Nav rail buttons ── */
QPushButton#nav {{
    background: transparent;
    border: none;
    border-left: 2px solid transparent;
    border-radius: 0px;
    color: {MUTED};
    text-align: left;
    padding: 11px 20px 11px 18px;
    font-size: 11px;
    letter-spacing: 1.5px;
}}
QPushButton#nav:hover {{
    background: {BG2};
    color: {TEXT2};
}}
QPushButton#nav[active=true] {{
    background: {BG2};
    color: {ACCENT};
    border-left: 2px solid {ACCENT};
}}

/* ── Inputs ── */
QLineEdit {{
    background: {BG3};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 10px 16px;
    color: {TEXT};
    selection-background-color: {ACCENT2};
}}
QLineEdit:focus {{ 
    border-color: {ACCENT2};
    box-shadow: 0 0 10px rgba(186, 104, 200, 0.3);
}}

QTextEdit {{
    background: {BG};
    border: none;
    padding: 20px 28px;
    font-family: {FONT_BODY};
    font-size: 18px;
    color: {TEXT};
    selection-background-color: #2A3020;
}}

/* ── Scrollbars ── */
QScrollBar:vertical {{
    background: {BG};
    width: 3px;
    border: none;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 2px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: {BG};
    height: 3px;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER};
    border-radius: 2px;
}}

/* ── Lists ── */
QListWidget {{
    background: transparent;
    border: none;
    outline: none;
}}
QListWidget::item {{
    background: {BG3};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 12px 16px;
    margin: 4px;
    color: {TEXT2};
}}
QListWidget::item:hover {{
    background: {PANEL};
    border-color: {ACCENT2};
    color: {ACCENT2};
}}
QListWidget::item:selected {{
    background: {PANEL};
    border-color: {ACCENT2};
    color: {ACCENT2};
    border-left: 3px solid {ACCENT2};
}}

/* ── Tabs ── */
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 12px 24px;
    font-size: 11px;
    letter-spacing: 2px;
    text-transform: uppercase;
}}
QTabBar::tab:selected {{ color: {ACCENT2}; border-bottom: 2px solid {ACCENT2}; }}
QTabBar::tab:hover {{ color: {TEXT2}; }}

/* ── Combo ── */
QComboBox {{
    background: {BG3};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 6px 12px;
    color: {TEXT};
}}
QComboBox QAbstractItemView {{
    background: {PANEL};
    border: 1px solid {BORDER2};
    color: {TEXT};
    selection-background-color: {BG3};
}}
QComboBox::drop-down {{ border: none; width: 20px; }}

/* ── Progress ── */
QProgressBar {{
    background: {BG3};
    border: none;
    border-radius: 1px;
    height: 2px;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 1px;
}}

/* ── GroupBox ── */
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 4px;
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
    font-size: 10px;
    letter-spacing: 1.5px;
    color: {MUTED};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {ACCENT};
    font-size: 9px;
    letter-spacing: 2px;
}}

/* ── Menu ── */
QMenu {{
    background: {PANEL};
    border: 1px solid {BORDER2};
    padding: 4px;
    border-radius: 4px;
}}
QMenu::item {{ padding: 8px 20px; border-radius: 2px; color: {TEXT2}; }}
QMenu::item:selected {{ background: {BG3}; color: {ACCENT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 3px 0; }}

/* ── Status bar ── */
QStatusBar {{
    background: {BG2};
    color: {MUTED};
    font-size: 10px;
    border-top: 1px solid {BORDER};
}}
"""


# ── Shared painting utilities ──────────────────────────────────────────────────
def paint_topbar(widget, event):
    """
    Paint a horizontal gradient bar with a 1px bottom border.
    Attach to any QWidget via:
        import types
        widget.paintEvent = types.MethodType(paint_topbar, widget)
    """
    from PyQt6.QtGui import QPainter, QLinearGradient, QColor, QBrush, QPen

    p = QPainter(widget)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    W, H = widget.width(), widget.height()
    grad = QLinearGradient(0, 0, W, 0)
    grad.setColorAt(0,   QColor(BG2).lighter(112))
    grad.setColorAt(0.5, QColor(BG2))
    grad.setColorAt(1.0, QColor(BG2).lighter(105))
    p.fillRect(0, 0, W, H, QBrush(grad))
    p.setPen(QPen(QColor(BORDER), 1))
    p.drawLine(0, H - 1, W, H - 1)
    p.end()
