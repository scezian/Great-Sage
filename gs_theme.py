"""
gs_theme.py — Great Sage
========================
Design tokens, colour palette, runtime constants, and global QSS stylesheet.
Import everything with:  from gs_theme import *
"""

# ── Runtime constants ───────────────────────────────────────────────────────────
MPV_SOCKET_PATH = "/tmp/mpvsocket_gs"  # Unix socket for mpv IPC

# ── Design tokens — Aurora Borealis ───────────────────────────────────
# Deep blue palette: near-black backgrounds, cyan accents,
# northern lights effects, cool ethereal hierarchy.

BG      = "#000511"   # deep midnight blue
BG2     = "#001d3d"   # dark ocean blue
BG3     = "#003459"   # deep sea blue
PANEL   = "#002855"   # navy panel
BORDER  = "#0077b6"   # bright cyan border
BORDER2 = "#00a8cc"   # light cyan border

ACCENT  = "#00d4ff"   # electric blue — primary CTA
ACCENT2 = "#5599ff"   # sky blue — secondary highlights
NEON    = "#99ccff"   # ice blue
RED     = "#ff6b6b"
BLUE    = "#00d4ff"   # electric blue
PURPLE  = "#9b59b6"

TEXT    = "#ffffff"   # pure white — primary text
TEXT2   = "#ccd6f6"   # light cyan — secondary text
MUTED   = "#64748b"   # muted blue — hints, labels

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
    border-radius: 6px;
    padding: 8px 16px;
    letter-spacing: 1px;
}}
QPushButton:hover {{
    background: {BG3};
    border-color: {ACCENT};
    color: {ACCENT};
    box-shadow: 0 0 20px rgba(0, 212, 255, 0.4);
}}
QPushButton:pressed {{ background: {PANEL}; }}
QPushButton#accent {{
    background: {ACCENT};
    color: {BG};
    border: none;
    font-weight: 500;
    letter-spacing: 1px;
}}
QPushButton#accent:hover {{ background: {ACCENT2}; }}
QPushButton#danger {{
    background: transparent;
    color: {RED};
    border: 1px solid {RED};
}}
QPushButton#danger:hover {{ background: rgba(220, 53, 69, 0.1); border-color: {RED}; }}

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
    border-radius: 8px;
    padding: 10px 16px;
    color: {TEXT};
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{ 
    border-color: {ACCENT};
    box-shadow: 0 0 0 3px rgba(0, 212, 255, 0.2);
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
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 12px 16px;
    margin-bottom: 6px;
    color: {TEXT2};
}}
QListWidget::item:hover {{
    background: {PANEL};
    border-color: {ACCENT};
    transform: translateX(4px);
    box-shadow: 0 4px 16px rgba(0, 212, 255, 0.2);
}}
QListWidget::item:selected {{
    background: {PANEL};
    border-color: {ACCENT};
    color: {ACCENT};
    box-shadow: 0 0 20px rgba(0, 212, 255, 0.3);
}}

/* ── Tabs ── */
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 16px 32px;
    font-size: 11px;
    font-weight: 400;
}}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; background: rgba(0, 212, 255, 0.02); }}
QTabBar::tab:hover {{ color: {TEXT}; background: rgba(0, 212, 255, 0.05); }}

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
