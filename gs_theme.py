"""
gs_theme.py — Great Sage
========================
Design tokens, colour palette, runtime constants, and global QSS stylesheet.
Import everything with:  from gs_theme import *
"""

# ── Runtime constants ───────────────────────────────────────────────────────────
import tempfile
import os as _os
MPV_SOCKET_PATH = _os.path.join(tempfile.gettempdir(), "mpvsocket_gs")  # Unix socket for mpv IPC

# ── Design tokens — Ink & Amber ────────────────────────────────────────────────
# Deep editorial palette: near-black backgrounds, warm amber accents,
# cool slate text, sharp contrast hierarchy.

BG      = "#0C0C0E"   # near-black, warm undertone
BG2     = "#111116"   # cards / elevated surfaces
BG3     = "#17171D"   # inputs / list rows
PANEL   = "#1C1C24"   # panels
BORDER  = "#252530"   # subtle borders
BORDER2 = "#32324A"   # active borders

ACCENT  = "#C9A84C"   # amber gold — primary CTA
ACCENT2 = "#4EC9A4"   # seafoam — secondary highlights
NEON    = "#5DDFCC"   # bright teal
RED     = "#E05A6A"
GREEN   = "#4EC97A"
BLUE    = "#4A90D9"
PURPLE  = "#8B6FD4"

TEXT    = "#E8E4DC"   # warm off-white — primary text
TEXT2   = "#A0A0B4"   # cool slate — secondary text
MUTED   = "#606070"   # muted — hints, labels

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
    border-radius: 3px;
    padding: 6px 14px;
    letter-spacing: 0.5px;
}}
QPushButton:hover {{
    background: {BG3};
    border-color: {ACCENT};
    color: {ACCENT};
}}
QPushButton:pressed {{ background: {PANEL}; }}
/* accent/danger/nav button styles applied directly on widgets */

/* ── Inputs ── */
QLineEdit {{
    background: {BG3};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 7px 12px;
    color: {TEXT};
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}

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
    padding: 9px 14px;
    border-bottom: 1px solid {BG3};
    color: {TEXT2};
}}
QListWidget::item:hover {{
    background: {BG2};
    color: {TEXT};
}}
QListWidget::item:selected {{
    background: {BG3};
    color: {ACCENT};
    border-left: 2px solid {ACCENT};
}}

/* ── Tabs ── */
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    border: none;
    border-bottom: 1px solid transparent;
    padding: 8px 20px;
    font-size: 11px;
    letter-spacing: 1.5px;
    font-weight: bold;
}}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 1px solid {ACCENT}; }}
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



# ── Per-widget style helpers (replaces global ID selectors) ───────────────────
def accent_btn_style():
    return (
        f"QPushButton {{ background:{ACCENT}; color:{BG}; border:none; "
        f"font-weight:bold; letter-spacing:1px; border-radius:3px; padding:6px 14px; }}"
        f"QPushButton:hover {{ background:#D4B460; }}"
        f"QPushButton:pressed {{ background:#B8923E; }}"
    )

def danger_btn_style():
    return (
        f"QPushButton {{ background:transparent; color:{RED}; border:1px solid #3D1A20; "
        f"border-radius:3px; padding:6px 14px; }}"
        f"QPushButton:hover {{ background:#2A0E14; border-color:{RED}; }}"
    )

def nav_btn_style(active=False):
    if active:
        return (
            f"QPushButton {{ background:{BG2}; color:{ACCENT}; border:none; "
            f"border-left:2px solid {ACCENT}; border-radius:0px; text-align:left; "
            f"padding:11px 20px 11px 18px; font-size:11px; letter-spacing:1.5px; }}"
            f"QPushButton:hover {{ background:{BG2}; color:{ACCENT}; }}"
        )
    return (
        f"QPushButton {{ background:transparent; color:{MUTED}; border:none; "
        f"border-left:2px solid transparent; border-radius:0px; text-align:left; "
        f"padding:11px 20px 11px 18px; font-size:11px; letter-spacing:1.5px; }}"
        f"QPushButton:hover {{ background:{BG2}; color:{TEXT2}; }}"
    )

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
