#!/usr/bin/env python3
"""
artemis.py — Artemis
==================================
Standalone rich-text editor built with PyQt6.
Matches the Artemis Ink & Amber design system exactly.

Run standalone:
    python3 artemis.py

Merge into main app later:
    from artemis import EditorPage
    # add ("editor", EditorPage()) to the pages list in great_sage_gui.py

Dependencies (already in setup.sh):
    pip install PyQt6
"""

import sys
import os
import json
from pathlib import Path
from datetime import datetime

# ── Attempt to import Artemis theme; fall back to inline tokens ─────────────
try:
    from gs_theme import (
        BG, BG2, BG3, PANEL, BORDER, BORDER2,
        ACCENT, ACCENT2, RED, BLUE, PURPLE,
        TEXT, TEXT2, MUTED,
        FONT_BODY, FONT_UI, FONT_DISPLAY,
        QSS,
    )
    _HAS_THEME = True
except ImportError:
    # Inline fallback so the file runs completely standalone
    BG      = "#0C0C0E"
    BG2     = "#111116"
    BG3     = "#17171D"
    PANEL   = "#1C1C24"
    BORDER  = "#252530"
    BORDER2 = "#32324A"
    ACCENT  = "#C9A84C"
    ACCENT2 = "#4EC9A4"
    RED     = "#E05A6A"
    BLUE    = "#4A90D9"
    PURPLE  = "#8B6FD4"
    TEXT    = "#E8E4DC"
    TEXT2   = "#A0A0B4"
    MUTED   = "#606070"
    FONT_BODY    = "Palatino Linotype, Palatino, Book Antiqua, Georgia, serif"
    FONT_UI      = "JetBrains Mono, Fira Code, Consolas, monospace"
    FONT_DISPLAY = "Palatino Linotype, Palatino, Book Antiqua, serif"
    QSS = ""
    _HAS_THEME = False

from PyQt6.QtCore import (
    Qt, QTimer, QSize, pyqtSignal, QMimeData, QObject, QThread,
)
from PyQt6.QtGui import (
    QColor, QFont, QTextCursor, QTextCharFormat, QTextBlockFormat,
    QTextListFormat, QKeySequence, QShortcut, QAction, QTextDocument, QPainter,
    QLinearGradient, QBrush, QPen, QIcon, QTextOption,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QLabel, QPushButton, QFrame, QFileDialog, QMessageBox,
    QComboBox, QSizePolicy, QScrollArea, QScrollBar, QStatusBar, QFontComboBox,
    QSpinBox, QColorDialog, QSplitter, QListWidget, QListWidgetItem,
    QDialog, QDialogButtonBox, QLineEdit, QMenu, QToolButton, QStackedWidget,
)
try:
    from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
    _HAS_PRINT = True
except ImportError:
    _HAS_PRINT = False

# ── Paths ──────────────────────────────────────────────────────────────────────
DOCS_DIR = Path.home() / "Documents" / "artemis"
DOCS_DIR.mkdir(parents=True, exist_ok=True)

RECENT_FILE = DOCS_DIR / ".recent.json"

# ── Colours used only in this module ──────────────────────────────────────────
TOOLBAR_BG  = "#0E0E12"
TOOLBAR_SEP = "#1E1E28"

# ══════════════════════════════════════════════════════════════════════════════
# HELPER WIDGETS
# ══════════════════════════════════════════════════════════════════════════════

def _lbl(text, color=TEXT2, size=12, bold=False):
    w = QLabel(text)
    s = f"color:{color}; font-size:{size}px; font-family:{FONT_UI};"
    if bold:
        s += "font-weight:bold;"
    w.setStyleSheet(s)
    return w


def _sep():
    """Thin vertical separator for the toolbar."""
    f = QFrame()
    f.setFrameShape(QFrame.Shape.VLine)
    f.setFixedWidth(1)
    f.setStyleSheet(f"background:{TOOLBAR_SEP}; border:none; margin:6px 4px;")
    return f


def _hsep():
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet(f"background:{BORDER}; border:none; max-height:1px;")
    return f


class _ToolBtn(QPushButton):
    """Small icon-text toolbar button."""
    def __init__(self, text, tooltip="", checkable=False, parent=None):
        super().__init__(text, parent)
        self.setCheckable(checkable)
        self.setToolTip(tooltip)
        self.setFixedHeight(28)
        self.setMinimumWidth(28)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_style(False)
        if checkable:
            self.toggled.connect(self._refresh_style)

    def _refresh_style(self, checked=False):
        if checked:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {ACCENT}22;
                    color: {ACCENT};
                    border: 1px solid {ACCENT}55;
                    border-radius: 3px;
                    padding: 0 8px;
                    font-family: {FONT_UI};
                    font-size: 12px;
                    font-weight: bold;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {TEXT2};
                    border: 1px solid transparent;
                    border-radius: 3px;
                    padding: 0 8px;
                    font-family: {FONT_UI};
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background: {BG3};
                    border-color: {BORDER};
                    color: {TEXT};
                }}
                QPushButton:pressed {{
                    background: {PANEL};
                }}
            """)


# ══════════════════════════════════════════════════════════════════════════════
# DOCUMENT LIST SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

class DocumentSidebar(QWidget):
    """Left panel listing saved documents. Emits open_file(path) on click."""
    open_file = pyqtSignal(str)
    new_doc   = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(240)
        self.setStyleSheet(
            "background: #0A0A0D;"
            "border-right: 1px solid #1A1A24;"
        )
        self._build()
        self.refresh()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── Brand bar at very top
        brand = QWidget()
        brand.setFixedHeight(56)
        brand.setStyleSheet("background: #0A0A0D; border-bottom: 1px solid #1A1A24;")
        bh = QHBoxLayout(brand)
        bh.setContentsMargins(16, 0, 12, 0)

        logo = QLabel("✦")
        logo.setStyleSheet(f"color:{ACCENT}; font-size:18px; font-family:serif;")

        app_name = QLabel("Artemis")
        app_name.setStyleSheet(
            f"color:{TEXT}; font-size:14px; font-weight:bold; font-family:{FONT_UI}; letter-spacing:1px;"
        )

        self.back_btn = QPushButton("⌂")
        self.back_btn.setFixedSize(28, 28)
        self.back_btn.setToolTip("Back to Home")
        self.back_btn.setStyleSheet(
            f"background:transparent; border:none; color:{MUTED}; font-size:16px;"
            f"border-radius:6px;"
        )
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.clicked.connect(self._on_home)

        bh.addWidget(logo)
        bh.addSpacing(8)
        bh.addWidget(app_name)
        bh.addStretch()
        bh.addWidget(self.back_btn)
        v.addWidget(brand)

        # ── Section label + new button
        sec = QWidget()
        sec.setFixedHeight(36)
        sec.setStyleSheet("background: #0A0A0D;")
        sh = QHBoxLayout(sec)
        sh.setContentsMargins(16, 0, 8, 0)

        sec_lbl = QLabel("DOCUMENTS")
        sec_lbl.setStyleSheet(
            f"color:#3A3A4A; font-family:{FONT_UI}; font-size:9px; letter-spacing:2.5px;"
        )

        new_btn = QPushButton("+")
        new_btn.setFixedSize(24, 24)
        new_btn.setToolTip("New document")
        new_btn.setStyleSheet(
            f"background:#1A1A24; border:1px solid #252530; border-radius:6px;"
            f"color:{TEXT2}; font-size:14px;"
        )
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.clicked.connect(self.new_doc.emit)

        sh.addWidget(sec_lbl)
        sh.addStretch()
        sh.addWidget(new_btn)
        v.addWidget(sec)

        # ── Document list
        self._list = QListWidget()
        self._list.setStyleSheet("""
            QListWidget {
                background: transparent;
                border: none;
                outline: none;
            }
            QListWidget::item {
                padding: 10px 16px 10px 16px;
                margin: 1px 0px;
                border-radius: 0px;
                background: transparent;
                color: #7070A0;
                font-size: 13px;
            }
            QListWidget::item:hover {
                background: #111118;
                color: #C8C4BC;
            }
            QListWidget::item:selected {
                background: #131320;
                color: #C9A84C;
                border-left: 2px solid #C9A84C;
            }
        """)
        self._list.itemDoubleClicked.connect(self._on_open)
        v.addWidget(self._list, 1)

    def refresh(self):
        self._list.clear()
        files = sorted(DOCS_DIR.glob("*.art"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            item = QListWidgetItem("  No documents yet")
            item.setForeground(QColor("#3A3A4A"))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(item)
            return
        for f in files:
            ts = datetime.fromtimestamp(f.stat().st_mtime).strftime("%d %b %Y")
            item = QListWidgetItem(f"  {f.stem}\n  {ts}")
            item.setData(Qt.ItemDataRole.UserRole, str(f))
            self._list.addItem(item)

    def _on_open(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.open_file.emit(path)

    def set_active(self, path: str):
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == path:
                self._list.setCurrentItem(item)
                return

    def _on_home(self):
        mw = self.window()
        if hasattr(mw, "_navigate"):
            mw._navigate("dashboard")

# ══════════════════════════════════════════════════════════════════════════════
# SIMPLE TOOLBAR (Single Row)
# ══════════════════════════════════════════════════════════════════════════════

class EditorToolbar(QWidget):
    """
    Clean single-row toolbar with essential formatting only.
    File ops | Formatting | Headings | Lists | Save/Export
    """

    sig_new       = pyqtSignal()
    sig_open      = pyqtSignal()
    sig_save      = pyqtSignal()
    sig_save_as   = pyqtSignal()
    sig_export    = pyqtSignal()
    sig_print     = pyqtSignal()
    sig_find      = pyqtSignal()
    sig_toggle_ai = pyqtSignal()

    def __init__(self, editor: QTextEdit, parent=None):
        super().__init__(parent)
        self._editor = editor
        self.setFixedHeight(80)
        self.setStyleSheet(
            "QWidget { background: #0D0D10; border-bottom: 1px solid #1A1A22; }"
            "QPushButton { background: transparent; border: none; border-radius: 4px;"
            "  color: #50506A; font-size: 12px; padding: 4px 10px; margin: 1px; }"
            "QPushButton:hover { background: #16161E; color: #C8C4BC; }"
            "QPushButton:pressed { background: #1C1C28; color: #E8E4DC; }"
            "QLabel { color: #3A3A4A; font-size: 11px; }"
        )
        self._build()
        self._connect_editor()

    def _build(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        # ── ROW 1: Undo/Redo | Font | Size | B I U S sub sup | Color | Alignment
        row1 = QWidget()
        row1.setStyleSheet("background: #0D0D10; border-bottom: 1px solid #141420;")
        h1 = QHBoxLayout(row1)
        h1.setContentsMargins(12, 0, 12, 0)
        h1.setSpacing(2)

        def div():
            f = QFrame(); f.setObjectName("divider")
            f.setFixedWidth(1); f.setFixedHeight(18)
            return f

        # Undo / Redo
        self._btn_undo = QPushButton("↶")
        self._btn_undo.setToolTip("Undo (Ctrl+Z)")
        self._btn_undo.clicked.connect(self._editor.undo)
        self._btn_redo = QPushButton("↷")
        self._btn_redo.setToolTip("Redo (Ctrl+Y)")
        self._btn_redo.clicked.connect(self._editor.redo)
        h1.addWidget(self._btn_undo)
        h1.addWidget(self._btn_redo)
        h1.addSpacing(4); h1.addWidget(div()); h1.addSpacing(4)

        # Font family
        self._font_combo = QFontComboBox()
        self._font_combo.setFixedWidth(150)
        self._font_combo.setToolTip("Font Family")
        self._font_combo.setStyleSheet(
            "QFontComboBox { background:#141420; border:1px solid #1A1A2A; "
            "border-radius:4px; color:#C8C4BC; padding:0 6px; font-size:12px; }"
            "QFontComboBox::drop-down { border:none; width:16px; }"
        )
        self._font_combo.currentFontChanged.connect(self._on_font_changed)
        h1.addWidget(self._font_combo)
        h1.addSpacing(4)

        # Font size
        self._size_spin = QSpinBox()
        self._size_spin.setRange(6, 96)
        self._size_spin.setValue(18)
        self._size_spin.setFixedWidth(52)
        self._size_spin.setToolTip("Font Size")
        self._size_spin.setStyleSheet(
            "QSpinBox { background:#141420; border:1px solid #1A1A2A; "
            "border-radius:4px; color:#C8C4BC; padding:0 4px; font-size:12px; }"
            "QSpinBox::up-button, QSpinBox::down-button { width:14px; }"
        )
        self._size_spin.valueChanged.connect(self._on_size_changed)
        h1.addWidget(self._size_spin)
        h1.addSpacing(4); h1.addWidget(div()); h1.addSpacing(4)

        # B I U S sub sup
        self._btn_bold = QPushButton("B")
        self._btn_bold.setCheckable(True)
        self._btn_bold.setToolTip("Bold (Ctrl+B)")
        self._btn_bold.setFont(QFont("Georgia", 12, QFont.Weight.Bold))
        self._btn_italic = QPushButton("I")
        self._btn_italic.setCheckable(True)
        self._btn_italic.setToolTip("Italic (Ctrl+I)")
        self._btn_under = QPushButton("U")
        self._btn_under.setCheckable(True)
        self._btn_under.setToolTip("Underline (Ctrl+U)")
        self._btn_strike = QPushButton("S̶")
        self._btn_strike.setCheckable(True)
        self._btn_strike.setToolTip("Strikethrough")
        self._btn_sub = QPushButton("x₂")
        self._btn_sub.setCheckable(True)
        self._btn_sub.setToolTip("Subscript")
        self._btn_sup = QPushButton("x²")
        self._btn_sup.setCheckable(True)
        self._btn_sup.setToolTip("Superscript")
        for b in (self._btn_bold, self._btn_italic, self._btn_under,
                  self._btn_strike, self._btn_sub, self._btn_sup):
            h1.addWidget(b)
        self._btn_bold.clicked.connect(self._toggle_bold)
        self._btn_italic.clicked.connect(self._toggle_italic)
        self._btn_under.clicked.connect(self._toggle_underline)
        self._btn_strike.clicked.connect(self._toggle_strikethrough)
        self._btn_sub.clicked.connect(self._toggle_subscript)
        self._btn_sup.clicked.connect(self._toggle_superscript)
        h1.addSpacing(4); h1.addWidget(div()); h1.addSpacing(4)

        # Text color + Highlight
        self._btn_color = QPushButton("A")
        self._btn_color.setToolTip("Text Color")
        self._btn_color.clicked.connect(self._set_text_color)
        self._btn_highlight = QPushButton("█")
        self._btn_highlight.setToolTip("Highlight Color")
        self._btn_highlight.clicked.connect(self._set_highlight_color)
        h1.addWidget(self._btn_color)
        h1.addWidget(self._btn_highlight)
        h1.addSpacing(4); h1.addWidget(div()); h1.addSpacing(4)

        # Alignment
        self._btn_al = QPushButton("≡") ;
        self._btn_al.setCheckable(True)
        self._btn_al.setToolTip("Align Left")
        self._btn_ac = QPushButton("≣") ;
        self._btn_ac.setCheckable(True)
        self._btn_ac.setToolTip("Align Center")
        self._btn_ar = QPushButton("≡") ;
        self._btn_ar.setCheckable(True)
        self._btn_ar.setToolTip("Align Right")
        self._btn_aj = QPushButton("☰") ;
        self._btn_aj.setCheckable(True)
        self._btn_aj.setToolTip("Justify")
        self._align_btns = [self._btn_al, self._btn_ac, self._btn_ar, self._btn_aj]
        self._btn_al.setChecked(True)
        for b in self._align_btns:
            h1.addWidget(b)
        self._btn_al.clicked.connect(lambda: self._set_align(Qt.AlignmentFlag.AlignLeft))
        self._btn_ac.clicked.connect(lambda: self._set_align(Qt.AlignmentFlag.AlignHCenter))
        self._btn_ar.clicked.connect(lambda: self._set_align(Qt.AlignmentFlag.AlignRight))
        self._btn_aj.clicked.connect(lambda: self._set_align(Qt.AlignmentFlag.AlignJustify))

        h1.addStretch()
        main.addWidget(row1)

        # ── ROW 2: File | H1 H2 H3 | Lists | Indent | HR | Image | stretch | Find Export Save
        row2 = QWidget()
        row2.setStyleSheet("background: #0D0D10;")
        h2 = QHBoxLayout(row2)
        h2.setContentsMargins(12, 0, 12, 0)
        h2.setSpacing(2)

        # File ops
        self._btn_new = QPushButton("+") ;
        self._btn_new.setToolTip("New (Ctrl+N)")
        self._btn_open = QPushButton("↑") ;
        self._btn_open.setToolTip("Open (Ctrl+O)")
        self._btn_save = QPushButton("↓") ;
        self._btn_save.setToolTip("Save (Ctrl+S)")
        self._btn_new.clicked.connect(self.sig_new.emit)
        self._btn_open.clicked.connect(self.sig_open.emit)
        self._btn_save.clicked.connect(self.sig_save.emit)
        for b in (self._btn_new, self._btn_open, self._btn_save):
            h2.addWidget(b)
        h2.addSpacing(4); h2.addWidget(div()); h2.addSpacing(4)

        # Headings
        self._btn_h1 = QPushButton("H1")
        self._btn_h1.setToolTip("Heading 1")
        self._btn_h1.clicked.connect(lambda: self._apply_heading(1))
        self._btn_h2 = QPushButton("H2")
        self._btn_h2.setToolTip("Heading 2")
        self._btn_h2.clicked.connect(lambda: self._apply_heading(2))
        self._btn_h3 = QPushButton("H3")
        self._btn_h3.setToolTip("Heading 3")
        self._btn_h3.clicked.connect(lambda: self._apply_heading(3))
        for b in (self._btn_h1, self._btn_h2, self._btn_h3):
            h2.addWidget(b)
        h2.addSpacing(4); h2.addWidget(div()); h2.addSpacing(4)

        # Lists
        self._btn_list = QPushButton("•") ;
        self._btn_list.setToolTip("Bullet List")
        self._btn_list.clicked.connect(self._insert_bullet)
        self._btn_num_list = QPushButton("1.") ;
        self._btn_num_list.setToolTip("Numbered List")
        self._btn_num_list.clicked.connect(self._insert_num_list)
        h2.addWidget(self._btn_list)
        h2.addWidget(self._btn_num_list)
        h2.addSpacing(4); h2.addWidget(div()); h2.addSpacing(4)

        # Indent
        self._btn_indent_out = QPushButton("⇤") ;
        self._btn_indent_out.setToolTip("Decrease Indent")
        self._btn_indent_out.clicked.connect(self._indent_decrease)
        self._btn_indent_in = QPushButton("⇥") ;
        self._btn_indent_in.setToolTip("Increase Indent")
        self._btn_indent_in.clicked.connect(self._indent_increase)
        h2.addWidget(self._btn_indent_out)
        h2.addWidget(self._btn_indent_in)
        h2.addSpacing(4); h2.addWidget(div()); h2.addSpacing(4)

        # HR + Image
        self._btn_hr = QPushButton("─") ;
        self._btn_hr.setToolTip("Insert Horizontal Rule")
        self._btn_hr.clicked.connect(self._insert_hr)
        self._btn_img = QPushButton("🖼") ;
        self._btn_img.setToolTip("Insert Image")
        self._btn_img.clicked.connect(self._insert_image)
        h2.addWidget(self._btn_hr)
        h2.addWidget(self._btn_img)

        h2.addStretch()

        # Right: Find, Export, Save
        self._btn_find = QPushButton("🔍") ;
        self._btn_find.setToolTip("Find & Replace (Ctrl+F)")
        self._btn_find.clicked.connect(self.sig_find.emit)
        self._btn_export = QPushButton("Export")
        self._btn_export.setToolTip("Export as text")
        self._btn_export.clicked.connect(self.sig_export.emit)
        self._btn_save_main = QPushButton("Save")
        self._btn_save_main.setStyleSheet("QPushButton { background: #C9A84C; border-radius: 6px; color: #0A0A0D; font-weight: bold; padding: 5px 16px; margin: 4px 8px; border: none; } QPushButton:hover { background: #DDB85A; }")
        self._btn_save_main.setToolTip("Save document")
        self._btn_save_main.clicked.connect(self.sig_save.emit)
        self._wc_lbl = QLabel("0 words")
        self._wc_lbl.setStyleSheet("color:#3A3A4A; font-size:11px; margin:0 8px;")
        # ── SAGE AI toggle button
        self._btn_sage = QPushButton("✦ SAGE")
        self._btn_sage.setCheckable(True)
        self._btn_sage.setToolTip("Toggle AI Sidebar (Ctrl+Shift+A)")
        self._btn_sage.setStyleSheet(f"""
            QPushButton {{
                background: rgba(201,168,76,.12);
                color: {ACCENT};
                border: 1px solid rgba(201,168,76,.25);
                border-radius: 5px;
                font-family: {FONT_UI};
                font-size: 11px;
                font-weight: bold;
                padding: 4px 12px;
                margin: 3px 6px 3px 4px;
            }}
            QPushButton:hover {{
                background: rgba(201,168,76,.22);
            }}
            QPushButton:checked {{
                background: rgba(201,168,76,.28);
                border-color: rgba(201,168,76,.7);
            }}
        """)
        self._btn_sage.clicked.connect(self.sig_toggle_ai.emit)
        for w in (self._btn_find, self._btn_export, self._btn_save_main,
                  self._wc_lbl, self._btn_sage):
            h2.addWidget(w)

        main.addWidget(row2)

    def _connect_editor(self):
        try:
            self._editor.currentCharFormatChanged.disconnect(self._sync_format)
        except Exception:
            pass
        try:
            self._editor.cursorPositionChanged.disconnect(self._sync_alignment)
        except Exception:
            pass
        try:
            self._editor.textChanged.disconnect(self._update_wc)
        except Exception:
            pass
        self._editor.currentCharFormatChanged.connect(self._sync_format)
        self._editor.cursorPositionChanged.connect(self._sync_alignment)
        self._editor.textChanged.connect(self._update_wc)
        self._signals_connected = True

    def _sync_format(self, fmt: QTextCharFormat):
        # Do NOT call any Qt widget methods here. This slot is called from
        # inside Qt's C++ edit pipeline (e.g. deletePreviousChar) and any
        # re-entry into Qt widgets causes a segfault on free-threaded Python 3.14.
        # Instead, copy raw data out and defer all widget updates via singleShot(0).
        try:
            self._pending_fmt = {
                "bold":       fmt.fontWeight() >= QFont.Weight.Bold,
                "italic":     fmt.fontItalic(),
                "under":      fmt.fontUnderline(),
                "strike":     fmt.fontStrikeOut(),
                "vert":       fmt.verticalAlignment(),
                "color":      fmt.foreground().color().name()
                              if fmt.foreground().color().isValid() else None,
                "family":     fmt.fontFamily(),
                "size":       fmt.fontPointSize(),
            }
        except Exception:
            return
        QTimer.singleShot(0, self._apply_pending_fmt)

    def _apply_pending_fmt(self):
        d = getattr(self, "_pending_fmt", None)
        if not d:
            return
        self._btn_bold.setChecked(d["bold"])
        self._btn_italic.setChecked(d["italic"])
        self._btn_under.setChecked(d["under"])
        self._btn_strike.setChecked(d["strike"])
        vt = d["vert"]
        self._btn_sub.setChecked(
            vt == QTextCharFormat.VerticalAlignment.AlignSubScript)
        self._btn_sup.setChecked(
            vt == QTextCharFormat.VerticalAlignment.AlignSuperScript)
        if d["color"]:
            self._btn_color.setStyleSheet(
                f"border-bottom: 2px solid {d['color']};")
        self._font_combo.blockSignals(True)
        if d["family"]:
            self._font_combo.setCurrentFont(QFont(d["family"]))
        self._font_combo.blockSignals(False)
        self._size_spin.blockSignals(True)
        if d["size"] and d["size"] > 0:
            self._size_spin.setValue(int(d["size"]))
        self._size_spin.blockSignals(False)

    def _sync_alignment(self):
        align = self._editor.alignment()
        self._btn_al.setChecked(align == Qt.AlignmentFlag.AlignLeft)
        self._btn_ac.setChecked(align == Qt.AlignmentFlag.AlignHCenter)
        self._btn_ar.setChecked(align == Qt.AlignmentFlag.AlignRight)
        self._btn_aj.setChecked(align == Qt.AlignmentFlag.AlignJustify)

    def _update_wc(self):
        text = self._editor.toPlainText().strip()
        words = len(text.split()) if text else 0
        self._wc_lbl.setText(f"{words:,} words")

    def _toggle_bold(self):
        fmt = QTextCharFormat()
        w = QFont.Weight.Bold if self._btn_bold.isChecked() else QFont.Weight.Normal
        fmt.setFontWeight(w)
        self._merge_format(fmt)

    def _toggle_italic(self):
        fmt = QTextCharFormat()
        fmt.setFontItalic(self._btn_italic.isChecked())
        self._merge_format(fmt)

    def _toggle_underline(self):
        fmt = QTextCharFormat()
        fmt.setFontUnderline(self._btn_under.isChecked())
        self._merge_format(fmt)

    def _toggle_strikethrough(self):
        fmt = QTextCharFormat()
        fmt.setFontStrikeOut(self._btn_strike.isChecked())
        self._merge_format(fmt)

    def _toggle_subscript(self):
        fmt = QTextCharFormat()
        if self._btn_sub.isChecked():
            self._btn_sup.setChecked(False)
            fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSubScript)
        else:
            fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignNormal)
        self._merge_format(fmt)

    def _toggle_superscript(self):
        fmt = QTextCharFormat()
        if self._btn_sup.isChecked():
            self._btn_sub.setChecked(False)
            fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSuperScript)
        else:
            fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignNormal)
        self._merge_format(fmt)

    def _set_align(self, alignment):
        self._editor.setAlignment(alignment)
        self._sync_alignment()

    def _indent_increase(self):
        cursor = self._editor.textCursor()
        blk_fmt = cursor.blockFormat()
        blk_fmt.setIndent(blk_fmt.indent() + 1)
        cursor.setBlockFormat(blk_fmt)

    def _indent_decrease(self):
        cursor = self._editor.textCursor()
        blk_fmt = cursor.blockFormat()
        blk_fmt.setIndent(max(0, blk_fmt.indent() - 1))
        cursor.setBlockFormat(blk_fmt)

    def _insert_hr(self):
        cursor = self._editor.textCursor()
        cursor.insertHtml("<hr style='border:1px solid #2A2A36;'><p></p>")

    def _insert_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Insert Image", str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp)"
        )
        if path:
            cursor = self._editor.textCursor()
            cursor.insertHtml(f'<img src="{path}" style="max-width:100%;">')

    def _set_text_color(self):
        color = QColorDialog.getColor(parent=self)
        if color.isValid():
            fmt = QTextCharFormat()
            fmt.setForeground(QBrush(color))
            self._merge_format(fmt)
            self._btn_color.setStyleSheet(
                f"border-bottom: 2px solid {color.name()};"
            )

    def _set_highlight_color(self):
        color = QColorDialog.getColor(parent=self)
        if color.isValid():
            fmt = QTextCharFormat()
            fmt.setBackground(QBrush(color))
            self._merge_format(fmt)
            self._btn_highlight.setStyleSheet(
                f"color: {color.name()};"
            )

    def _on_font_changed(self, font: QFont):
        fmt = QTextCharFormat()
        fmt.setFontFamily(font.family())
        self._merge_format(fmt)

    def _on_size_changed(self, size: int):
        fmt = QTextCharFormat()
        fmt.setFontPointSize(size)
        self._merge_format(fmt)

    def _apply_heading(self, level: int):
        sizes = {1: 28, 2: 22, 3: 17}
        fmt = QTextCharFormat()
        fmt.setFontPointSize(sizes[level])
        fmt.setFontWeight(QFont.Weight.Bold)
        self._merge_format(fmt)

    def _insert_bullet(self):
        cursor = self._editor.textCursor()
        fmt = QTextListFormat()
        fmt.setStyle(QTextListFormat.Style.ListDisc)
        cursor.insertList(fmt)

    def _insert_num_list(self):
        cursor = self._editor.textCursor()
        fmt = QTextListFormat()
        fmt.setStyle(QTextListFormat.Style.ListDecimal)
        cursor.insertList(fmt)

    def _merge_format(self, fmt: QTextCharFormat):
        cursor = self._editor.textCursor()
        cursor.mergeCharFormat(fmt)
        self._editor.mergeCurrentCharFormat(fmt)

    def update_word_count(self):
        self._update_wc()


# ══════════════════════════════════════════════════════════════════════════════
# FIND & REPLACE DIALOG
# ══════════════════════════════════════════════════════════════════════════════

class FindReplaceDialog(QDialog):
    def __init__(self, editor: QTextEdit, parent=None):
        super().__init__(parent)
        self._editor = editor
        self.setWindowTitle("Find & Replace")
        self.setModal(False)
        self.setFixedWidth(420)
        self.setStyleSheet(f"""
            QDialog {{ background:{BG2}; border:1px solid {BORDER2}; }}
            QLabel  {{ color:{TEXT2}; font-family:{FONT_UI}; font-size:12px; }}
            QLineEdit {{
                background:{BG3}; border:1px solid {BORDER}; border-radius:3px;
                color:{TEXT}; font-family:{FONT_UI}; font-size:13px; padding:6px 10px;
            }}
            QLineEdit:focus {{ border-color:{ACCENT}; }}
            QPushButton {{
                background:transparent; color:{TEXT2}; border:1px solid {BORDER};
                border-radius:3px; padding:6px 14px; font-family:{FONT_UI}; font-size:12px;
            }}
            QPushButton:hover {{ background:{BG3}; border-color:{ACCENT}; color:{ACCENT}; }}
            QPushButton#accent {{
                background:{ACCENT}; color:{BG}; border:none; font-weight:bold;
            }}
            QPushButton#accent:hover {{ background:#D4B460; }}
        """)
        self._build()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 16)
        v.setSpacing(12)

        v.addWidget(QLabel("FIND & REPLACE"))

        v.addWidget(QLabel("Find"))
        self._find_edit = QLineEdit()
        self._find_edit.setPlaceholderText("Search text…")
        v.addWidget(self._find_edit)

        v.addWidget(QLabel("Replace with"))
        self._repl_edit = QLineEdit()
        self._repl_edit.setPlaceholderText("Replacement text…")
        v.addWidget(self._repl_edit)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self._status = QLabel("")
        self._status.setStyleSheet(f"color:{MUTED}; font-size:11px;")

        find_btn    = QPushButton("Find Next")
        repl_btn    = QPushButton("Replace")
        repl_all    = QPushButton("Replace All")
        repl_all.setObjectName("accent")

        find_btn.clicked.connect(self._find_next)
        repl_btn.clicked.connect(self._replace_one)
        repl_all.clicked.connect(self._replace_all)

        btns.addWidget(find_btn)
        btns.addWidget(repl_btn)
        btns.addWidget(repl_all)
        btns.addStretch()
        v.addLayout(btns)
        v.addWidget(self._status)

        self._find_edit.returnPressed.connect(self._find_next)

    def _find_next(self):
        term = self._find_edit.text()
        if not term:
            return
        found = self._editor.find(term)
        if not found:
            # Wrap around
            cursor = self._editor.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self._editor.setTextCursor(cursor)
            found = self._editor.find(term)
        self._status.setText("Not found." if not found else "")

    def _replace_one(self):
        cursor = self._editor.textCursor()
        if cursor.hasSelection():
            cursor.insertText(self._repl_edit.text())
        self._find_next()

    def _replace_all(self):
        term = self._find_edit.text()
        repl = self._repl_edit.text()
        if not term:
            return
        doc = self._editor.document()
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        count = 0
        while True:
            found = doc.find(term, cursor)
            if found.isNull():
                break
            found.insertText(repl)
            cursor = found
            count += 1
        cursor.endEditBlock()
        self._status.setText(f"Replaced {count} occurrence{'s' if count != 1 else ''}.")


# ══════════════════════════════════════════════════════════════════════════════
# AI WORKER
# ══════════════════════════════════════════════════════════════════════════════

class _ArtemisAIWorker(QThread):
    """
    Thin QThread wrapper around sage.py's groq_stream_chat / groq_chat.
    Standalone-safe: if sage.py is unavailable it emits an error signal
    instead of crashing.  When running inside Great Sage it picks up the
    API key from Matrix settings automatically (same pattern as SageWorker).
    """
    chunk_ready = pyqtSignal(str)
    finished    = pyqtSignal()
    error       = pyqtSignal(str)

    def __init__(self, prompt: str, system: str = "", history: list = None,
                 parent=None):
        super().__init__(parent)
        self.prompt  = prompt
        self.system  = system
        self.history = history or []
        self._stop   = False

    def stop(self):
        self._stop = True

    def run(self):
        # ── resolve sage module ────────────────────────────────────────────
        mod = None
        try:
            import importlib.util as _ilu
            _script_dir = Path(__file__).parent
            _path = _script_dir / "sage.py"
            if _path.exists():
                spec = _ilu.spec_from_file_location(
                    "sage", str(_path),
                    submodule_search_locations=[str(_script_dir)]
                )
                mod = _ilu.module_from_spec(spec)
                spec.loader.exec_module(mod)
        except Exception as e:
            self.error.emit(f"Could not load sage.py: {e}")
            return

        if mod is None or not (hasattr(mod, "groq_stream_chat") or
                                hasattr(mod, "groq_chat")):
            self.error.emit(
                "sage.py not found or missing groq_chat.\n"
                "Make sure sage.py is in the same directory as artemis.py."
            )
            return

        # ── apply API key / model overrides from Matrix settings ──────────
        try:
            from great_sage_core import get_matrix_data
            _s = get_matrix_data().get("settings", {})
            if _s.get("groq_api_key") and hasattr(mod, "GROQ_API_KEY"):
                mod.GROQ_API_KEY = _s["groq_api_key"]
            if _s.get("groq_model") and hasattr(mod, "GROQ_MODEL"):
                mod.GROQ_MODEL = _s["groq_model"]
        except Exception:
            pass  # Running standalone — use whatever key is hardcoded in sage.py

        # ── stream ────────────────────────────────────────────────────────
        try:
            if hasattr(mod, "groq_stream_chat"):
                history_arg = self.history if self.history else None
                for chunk, err in mod.groq_stream_chat(
                        self.prompt,
                        system=self.system or None,
                        history=history_arg):
                    if self._stop:
                        return
                    if err:
                        self.error.emit(err)
                        return
                    if chunk:
                        self.chunk_ready.emit(chunk)
            else:
                resp, err = mod.groq_chat(self.prompt, system=self.system or None)
                if err:
                    self.error.emit(err)
                    return
                if resp:
                    self.chunk_ready.emit(resp)
        except Exception as e:
            self.error.emit(str(e))
            return

        self.finished.emit()


# ══════════════════════════════════════════════════════════════════════════════
# AI SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

class AISidebar(QWidget):
    """
    Right-hand AI panel for Artemis.

    Four tabs
    ─────────
    CHAT     — free chat with full document context.
    CONTINUE — generate a continuation; preview → accept/discard.
    REWRITE  — rewrite selected text; side-by-side diff → accept/discard.
    PROOF    — proofread the document; clickable issue cards with AI Fix.

    Signals
    ───────
    insert_text(str)        — insert accepted continuation at cursor
    replace_selection(str)  — replace current selection with accepted rewrite
    request_doc_text()      — emitted when sidebar needs the canvas plain text
    request_selection()     — emitted when sidebar needs the current selection
    jump_to_phrase(str)     — emitted when user clicks a proof issue card
    apply_fix(str, str)     — (original_phrase, fixed_text) for AI Fix button
    """

    insert_text       = pyqtSignal(str)
    replace_selection = pyqtSignal(str)
    request_doc_text  = pyqtSignal()
    request_selection = pyqtSignal()
    jump_to_phrase    = pyqtSignal(str)
    apply_fix         = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(310)
        self._doc_text        = ""
        self._selection       = ""
        self._worker: _ArtemisAIWorker | None = None
        self._chat_history: list = []
        self._continue_buf    = ""
        self._rewrite_buf     = ""
        self._proof_raw       = ""
        self._proof_fix_phrase = ""
        self._proof_fix_buf   = ""
        self._build()

    # ── public API ────────────────────────────────────────────────────────────

    def set_doc_text(self, text: str):
        """Called by EditorPage to push current document content."""
        self._doc_text = text

    def set_selection(self, text: str):
        """Called by EditorPage to push current selection."""
        self._selection = text
        self._update_selection_indicator()

    # ── construction ──────────────────────────────────────────────────────────

    def _build(self):
        self.setStyleSheet("background:#0E0E14; border-left:1px solid #1A1A24;")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── header ────────────────────────────────────────────────────────
        header = QWidget()
        header.setFixedHeight(52)
        header.setStyleSheet(
            "background:#0E0E14; border-bottom:1px solid #1A1A24;")
        hh = QHBoxLayout(header)
        hh.setContentsMargins(14, 0, 10, 0)
        hh.setSpacing(8)

        icon_lbl = QLabel("✦")
        icon_lbl.setStyleSheet(
            f"color:{ACCENT}; font-size:15px; background:transparent;")
        title_lbl = QLabel("SAGE")
        title_lbl.setStyleSheet(
            f"color:{TEXT}; font-size:12px; font-weight:bold; "
            f"letter-spacing:2px; background:transparent;")

        hh.addWidget(icon_lbl)
        hh.addWidget(title_lbl)
        hh.addStretch()
        root.addWidget(header)

        # ── tab bar ───────────────────────────────────────────────────────
        tab_bar = QWidget()
        tab_bar.setFixedHeight(36)
        tab_bar.setStyleSheet(
            "background:#0E0E14; border-bottom:1px solid #1A1A24;")
        th = QHBoxLayout(tab_bar)
        th.setContentsMargins(0, 0, 0, 0)
        th.setSpacing(0)

        self._tab_btns: list[QPushButton] = []
        self._stack = QStackedWidget()

        for label in ("CHAT", "CONTINUE", "REWRITE", "PROOF"):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: none;
                    border-bottom: 2px solid transparent;
                    color: {MUTED};
                    font-family: {FONT_UI};
                    font-size: 9px;
                    letter-spacing: 1.5px;
                    padding: 0 4px;
                }}
                QPushButton:hover {{ color: {TEXT2}; }}
                QPushButton:checked {{
                    color: {ACCENT};
                    border-bottom: 2px solid {ACCENT};
                }}
            """)
            b.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            b.clicked.connect(lambda _, lb=label: self._switch_tab(lb))
            th.addWidget(b)
            self._tab_btns.append(b)

        root.addWidget(tab_bar)

        # ── panels ────────────────────────────────────────────────────────
        self._panel_chat     = self._build_chat_panel()
        self._panel_continue = self._build_continue_panel()
        self._panel_rewrite  = self._build_rewrite_panel()
        self._panel_proof    = self._build_proof_panel()

        for panel in (self._panel_chat, self._panel_continue,
                      self._panel_rewrite, self._panel_proof):
            self._stack.addWidget(panel)

        root.addWidget(self._stack, 1)

        # Activate CHAT tab by default
        self._switch_tab("CHAT")

    # ── tab switching ─────────────────────────────────────────────────────────

    def _switch_tab(self, label: str):
        idx_map = {"CHAT": 0, "CONTINUE": 1, "REWRITE": 2, "PROOF": 3}
        idx = idx_map.get(label, 0)
        self._stack.setCurrentIndex(idx)
        for i, b in enumerate(self._tab_btns):
            b.setChecked(i == idx)
        if label == "REWRITE":
            self.request_selection.emit()

    # ── CHAT panel ────────────────────────────────────────────────────────────

    def _build_chat_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet("background:transparent;")
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Message scroll area
        self._chat_scroll = QScrollArea()
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{background:transparent;width:4px;border:none;margin:8px 0;}"
            "QScrollBar::handle:vertical{background:#2A2A36;border-radius:2px;min-height:20px;}"
            "QScrollBar::handle:vertical:hover{background:#C9A84C;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
        )
        self._chat_content = QWidget()
        self._chat_content.setStyleSheet("background:transparent;")
        self._chat_layout = QVBoxLayout(self._chat_content)
        self._chat_layout.setContentsMargins(10, 10, 10, 10)
        self._chat_layout.setSpacing(10)
        self._chat_layout.addStretch()
        self._chat_scroll.setWidget(self._chat_content)
        v.addWidget(self._chat_scroll, 1)

        self._add_chat_message("SAGE",
            "Document loaded. Ask me anything — themes, characters, "
            "structure, or ideas.", is_ai=True)

        # Input area
        input_area = QWidget()
        input_area.setStyleSheet(
            "background:#0E0E14; border-top:1px solid #1A1A24;")
        iv = QVBoxLayout(input_area)
        iv.setContentsMargins(10, 8, 10, 10)
        iv.setSpacing(6)

        self._chat_input = QTextEdit()
        self._chat_input.setFixedHeight(62)
        self._chat_input.setPlaceholderText("Ask about the document…")
        self._chat_input.setStyleSheet(f"""
            QTextEdit {{
                background:#13131A; border:1px solid {BORDER};
                border-radius:5px; color:{TEXT};
                font-family:{FONT_UI}; font-size:12px; padding:6px 8px;
            }}
            QTextEdit:focus {{ border-color:rgba(201,168,76,.4); }}
        """)

        self._chat_send_btn = QPushButton("Send")
        self._chat_send_btn.setFixedHeight(30)
        self._chat_send_btn.setStyleSheet(f"""
            QPushButton {{
                background:{ACCENT}; color:#0A0A0D; border:none;
                border-radius:5px; font-weight:bold;
                font-family:{FONT_UI}; font-size:11px;
            }}
            QPushButton:hover {{ background:#D4B460; }}
            QPushButton:disabled {{ background:#3A3028; color:{MUTED}; }}
        """)
        self._chat_send_btn.clicked.connect(self._send_chat)

        send_sc = QShortcut(QKeySequence("Ctrl+Return"), self._chat_input)
        send_sc.activated.connect(self._send_chat)

        iv.addWidget(self._chat_input)
        iv.addWidget(self._chat_send_btn)
        v.addWidget(input_area)
        return panel

    def _add_chat_message(self, role: str, text: str,
                          is_ai: bool = False) -> QLabel:
        """Append a message bubble to the chat layout. Returns the bubble label."""
        msg_w = QWidget()
        msg_w.setStyleSheet("background:transparent;")
        mv = QVBoxLayout(msg_w)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(3)

        role_lbl = QLabel(role)
        role_lbl.setStyleSheet(
            f"color:{'#C9A84C' if is_ai else MUTED}; "
            f"font-size:9px; letter-spacing:1.5px; background:transparent;")

        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        bubble.setStyleSheet(f"""
            QLabel {{
                background:{'#0F0F18' if is_ai else '#13131A'};
                border:1px solid {'#252535' if is_ai else BORDER};
                border-radius:5px; color:{TEXT2};
                font-family:{FONT_UI}; font-size:12px;
                padding:7px 9px; line-height:1.6;
            }}
        """)
        bubble._stream_buf = ""

        mv.addWidget(role_lbl)
        mv.addWidget(bubble)

        count = self._chat_layout.count()
        self._chat_layout.insertWidget(count - 1, msg_w)

        QTimer.singleShot(50, lambda: self._chat_scroll.verticalScrollBar().setValue(
            self._chat_scroll.verticalScrollBar().maximum()))

        return bubble

    def _send_chat(self):
        text = self._chat_input.toPlainText().strip()
        if not text:
            return
        self._chat_input.clear()
        self._chat_send_btn.setEnabled(False)
        self._add_chat_message("YOU", text, is_ai=False)

        doc_ctx = self._doc_text[:8000] if self._doc_text else ""
        if doc_ctx:
            system = (
                "You are Sage, an AI writing assistant embedded in the Artemis "
                "editor. You have the user's current document below. "
                "Be concise, insightful, and focused on the writing. "
                "Answer questions about the text, offer literary analysis, "
                "help with structure, characters, themes, or anything "
                "writing-related.\n\n"
                f"CURRENT DOCUMENT:\n{doc_ctx}"
            )
        else:
            system = (
                "You are Sage, an AI writing assistant. "
                "Help the user with their writing."
            )

        self._chat_history.append({"role": "user", "content": text})
        ai_bubble = self._add_chat_message("SAGE", "", is_ai=True)
        ai_bubble.setText("▌")

        self._stop_worker()
        self._worker = _ArtemisAIWorker(
            prompt=text, system=system,
            history=self._chat_history[:-1])
        self._worker.chunk_ready.connect(
            lambda c, b=ai_bubble: self._stream_into_label(b, c))
        self._worker.finished.connect(
            lambda b=ai_bubble: self._on_chat_finished(b))
        self._worker.error.connect(
            lambda e, b=ai_bubble: self._on_chat_error(b, e))
        self._worker.start()

    def _on_chat_finished(self, bubble: QLabel):
        full_text = getattr(bubble, "_stream_buf", "")
        if full_text:
            bubble.setText(full_text)
            self._chat_history.append(
                {"role": "assistant", "content": full_text})
        self._chat_send_btn.setEnabled(True)

    def _on_chat_error(self, bubble: QLabel, err: str):
        bubble.setText(f"Error: {err}")
        self._chat_send_btn.setEnabled(True)

    # ── CONTINUE panel ────────────────────────────────────────────────────────

    def _build_continue_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet("background:transparent;")
        v = QVBoxLayout(panel)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        hint = QLabel(
            "Sage reads the last ~500 words and continues "
            "from where you stopped.\nReview the preview before accepting.")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color:{MUTED}; font-size:10px; "
            f"font-family:{FONT_UI}; background:transparent;")
        v.addWidget(hint)

        self._continue_btn = QPushButton("▶  Generate continuation")
        self._continue_btn.setStyleSheet(self._action_btn_style())
        self._continue_btn.clicked.connect(self._run_continue)
        v.addWidget(self._continue_btn)

        self._continue_preview = QWidget()
        self._continue_preview.setVisible(False)
        pv = QVBoxLayout(self._continue_preview)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(6)

        plbl = QLabel("PREVIEW")
        plbl.setStyleSheet(
            f"color:{MUTED}; font-size:9px; "
            f"letter-spacing:1.5px; background:transparent;")

        self._continue_text = QLabel("")
        self._continue_text.setWordWrap(True)
        self._continue_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._continue_text.setStyleSheet(f"""
            QLabel {{
                background:#0A0A0D; border:1px solid {BORDER2};
                border-radius:5px; color:{TEXT2};
                font-family:'Palatino Linotype', Georgia, serif;
                font-size:13px; padding:9px 10px; line-height:1.6;
                min-height:60px;
            }}
        """)

        accept_row = QHBoxLayout()
        accept_row.setSpacing(6)
        acc_btn = QPushButton("✓  Accept")
        acc_btn.setStyleSheet(self._accept_btn_style())
        acc_btn.clicked.connect(self._accept_continue)
        dis_btn = QPushButton("✕  Discard")
        dis_btn.setStyleSheet(self._discard_btn_style())
        dis_btn.clicked.connect(
            lambda: self._continue_preview.setVisible(False))
        accept_row.addWidget(acc_btn)
        accept_row.addWidget(dis_btn)

        pv.addWidget(plbl)
        pv.addWidget(self._continue_text)
        pv.addLayout(accept_row)
        v.addWidget(self._continue_preview)
        v.addStretch()
        return panel

    def _run_continue(self):
        self.request_doc_text.emit()
        QTimer.singleShot(60, self._do_run_continue)

    def _do_run_continue(self):
        tail = self._doc_text[-2500:] if self._doc_text else ""
        if not tail.strip():
            self._continue_text.setText("The document appears to be empty.")
            self._continue_preview.setVisible(True)
            return

        self._continue_buf = ""
        self._continue_btn.setEnabled(False)
        self._continue_text.setText("▌")
        self._continue_preview.setVisible(True)

        system = (
            "You are a skilled fiction and non-fiction writing assistant. "
            "Continue the provided text naturally, matching the author's "
            "voice, style, tone, and pacing exactly. "
            "Write one to three paragraphs. "
            "Output ONLY the continuation — no titles, commentary, or "
            "explanations."
        )
        prompt = f"Continue this text naturally from where it ends:\n\n{tail}"

        self._stop_worker()
        self._worker = _ArtemisAIWorker(prompt=prompt, system=system)
        self._worker.chunk_ready.connect(self._on_continue_chunk)
        self._worker.finished.connect(self._on_continue_done)
        self._worker.error.connect(self._on_continue_error)
        self._worker.start()

    def _on_continue_chunk(self, chunk: str):
        self._continue_buf += chunk
        self._continue_text.setText(self._continue_buf + " ▌")

    def _on_continue_done(self):
        self._continue_text.setText(self._continue_buf)
        self._continue_btn.setEnabled(True)

    def _on_continue_error(self, err: str):
        self._continue_text.setText(f"Error: {err}")
        self._continue_btn.setEnabled(True)

    def _accept_continue(self):
        if self._continue_buf:
            self.insert_text.emit("\n\n" + self._continue_buf)
        self._continue_preview.setVisible(False)
        self._continue_buf = ""

    # ── REWRITE panel ─────────────────────────────────────────────────────────

    def _build_rewrite_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet("background:transparent;")
        v = QVBoxLayout(panel)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        hint = QLabel(
            "Select text in the canvas, then rewrite it.\n"
            "Original and rewrite shown side by side — accept to replace.")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color:{MUTED}; font-size:10px; "
            f"font-family:{FONT_UI}; background:transparent;")
        v.addWidget(hint)

        self._sel_indicator = QLabel("No text selected.")
        self._sel_indicator.setWordWrap(True)
        self._sel_indicator.setStyleSheet(f"""
            QLabel {{
                color:{MUTED}; font-size:10px; font-family:{FONT_UI};
                background:transparent; border:1px solid {BORDER};
                border-radius:4px; padding:6px 8px;
            }}
        """)
        v.addWidget(self._sel_indicator)

        self._rewrite_btn = QPushButton("▶  Rewrite selection")
        self._rewrite_btn.setStyleSheet(self._action_btn_style())
        self._rewrite_btn.clicked.connect(self._run_rewrite)
        v.addWidget(self._rewrite_btn)

        self._rewrite_preview = QWidget()
        self._rewrite_preview.setVisible(False)
        pv = QVBoxLayout(self._rewrite_preview)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(6)

        orig_lbl = QLabel("ORIGINAL")
        orig_lbl.setStyleSheet(
            f"color:{MUTED}; font-size:9px; "
            f"letter-spacing:1.5px; background:transparent;")
        self._rewrite_original = QLabel("")
        self._rewrite_original.setWordWrap(True)
        self._rewrite_original.setStyleSheet(f"""
            QLabel {{
                background:#0A0A0D; border:1px solid {BORDER};
                border-radius:5px; color:{MUTED};
                font-family:'Palatino Linotype', Georgia, serif;
                font-size:12px; padding:8px 9px; line-height:1.6;
            }}
        """)

        new_lbl = QLabel("REWRITE")
        new_lbl.setStyleSheet(
            f"color:{MUTED}; font-size:9px; "
            f"letter-spacing:1.5px; background:transparent;")
        self._rewrite_text = QLabel("")
        self._rewrite_text.setWordWrap(True)
        self._rewrite_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._rewrite_text.setStyleSheet(f"""
            QLabel {{
                background:#0A0A0D;
                border:1px solid rgba(201,164,76,.3);
                border-radius:5px; color:{TEXT2};
                font-family:'Palatino Linotype', Georgia, serif;
                font-size:12px; padding:8px 9px; line-height:1.6;
                min-height:40px;
            }}
        """)

        accept_row = QHBoxLayout()
        accept_row.setSpacing(6)
        acc_btn = QPushButton("✓  Accept")
        acc_btn.setStyleSheet(self._accept_btn_style())
        acc_btn.clicked.connect(self._accept_rewrite)
        dis_btn = QPushButton("✕  Discard")
        dis_btn.setStyleSheet(self._discard_btn_style())
        dis_btn.clicked.connect(
            lambda: self._rewrite_preview.setVisible(False))
        accept_row.addWidget(acc_btn)
        accept_row.addWidget(dis_btn)

        pv.addWidget(orig_lbl)
        pv.addWidget(self._rewrite_original)
        pv.addWidget(new_lbl)
        pv.addWidget(self._rewrite_text)
        pv.addLayout(accept_row)
        v.addWidget(self._rewrite_preview)
        v.addStretch()
        return panel

    def _update_selection_indicator(self):
        if self._selection.strip():
            preview = self._selection[:120]
            if len(self._selection) > 120:
                preview += "…"
            self._sel_indicator.setText(f'"{preview}"')
            self._sel_indicator.setStyleSheet(f"""
                QLabel {{
                    color:{ACCENT}; font-size:10px; font-family:{FONT_UI};
                    background:rgba(201,168,76,.06);
                    border:1px solid rgba(201,168,76,.2);
                    border-radius:4px; padding:6px 8px;
                }}
            """)
        else:
            self._sel_indicator.setText("No text selected.")
            self._sel_indicator.setStyleSheet(f"""
                QLabel {{
                    color:{MUTED}; font-size:10px; font-family:{FONT_UI};
                    background:transparent; border:1px solid {BORDER};
                    border-radius:4px; padding:6px 8px;
                }}
            """)

    def _run_rewrite(self):
        self.request_selection.emit()
        QTimer.singleShot(60, self._do_run_rewrite)

    def _do_run_rewrite(self):
        sel = self._selection.strip()
        if not sel:
            self._sel_indicator.setText(
                "Select some text in the canvas first.")
            return

        self._rewrite_buf = ""
        self._rewrite_btn.setEnabled(False)
        self._rewrite_original.setText(sel)
        self._rewrite_text.setText("▌")
        self._rewrite_preview.setVisible(True)

        system = (
            "You are a skilled editor and writing assistant. "
            "Rewrite the provided passage — improve clarity, rhythm, and "
            "style while preserving the author's voice and intent. "
            "Output ONLY the rewritten text, nothing else. "
            "No commentary, no quotes, no explanation."
        )
        prompt = f"Rewrite this passage:\n\n{sel}"

        self._stop_worker()
        self._worker = _ArtemisAIWorker(prompt=prompt, system=system)
        self._worker.chunk_ready.connect(self._on_rewrite_chunk)
        self._worker.finished.connect(self._on_rewrite_done)
        self._worker.error.connect(self._on_rewrite_error)
        self._worker.start()

    def _on_rewrite_chunk(self, chunk: str):
        self._rewrite_buf += chunk
        self._rewrite_text.setText(self._rewrite_buf + " ▌")

    def _on_rewrite_done(self):
        self._rewrite_text.setText(self._rewrite_buf)
        self._rewrite_btn.setEnabled(True)

    def _on_rewrite_error(self, err: str):
        self._rewrite_text.setText(f"Error: {err}")
        self._rewrite_btn.setEnabled(True)

    def _accept_rewrite(self):
        if self._rewrite_buf:
            self.replace_selection.emit(self._rewrite_buf)
        self._rewrite_preview.setVisible(False)
        self._rewrite_buf = ""

    # ── PROOF panel ───────────────────────────────────────────────────────────

    def _build_proof_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet("background:transparent;")
        v = QVBoxLayout(panel)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        hint = QLabel(
            "Sage scans the full document and lists issues.\n"
            "Click an issue card to jump to it in the canvas.")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color:{MUTED}; font-size:10px; "
            f"font-family:{FONT_UI}; background:transparent;")
        v.addWidget(hint)

        self._proof_btn = QPushButton("▶  Proofread document")
        self._proof_btn.setStyleSheet(self._action_btn_style())
        self._proof_btn.clicked.connect(self._run_proof)
        v.addWidget(self._proof_btn)

        self._proof_scroll = QScrollArea()
        self._proof_scroll.setWidgetResizable(True)
        self._proof_scroll.setVisible(False)
        self._proof_scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{background:transparent;width:4px;border:none;}"
            "QScrollBar::handle:vertical{background:#2A2A36;border-radius:2px;min-height:20px;}"
            "QScrollBar::handle:vertical:hover{background:#C9A84C;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
        )
        self._proof_list_widget = QWidget()
        self._proof_list_widget.setStyleSheet("background:transparent;")
        self._proof_list_layout = QVBoxLayout(self._proof_list_widget)
        self._proof_list_layout.setContentsMargins(0, 0, 0, 0)
        self._proof_list_layout.setSpacing(7)
        self._proof_list_layout.addStretch()
        self._proof_scroll.setWidget(self._proof_list_widget)
        v.addWidget(self._proof_scroll, 1)

        self._proof_status = QLabel("")
        self._proof_status.setWordWrap(True)
        self._proof_status.setStyleSheet(
            f"color:{MUTED}; font-size:10px; "
            f"font-family:{FONT_UI}; background:transparent;")
        v.addWidget(self._proof_status)

        return panel

    def _run_proof(self):
        self.request_doc_text.emit()
        QTimer.singleShot(60, self._do_run_proof)

    def _do_run_proof(self):
        doc = self._doc_text.strip()
        if not doc:
            self._proof_status.setText("The document is empty.")
            return

        # Clear previous results
        while self._proof_list_layout.count() > 1:
            item = self._proof_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._proof_btn.setEnabled(False)
        self._proof_status.setText("Scanning…")
        self._proof_scroll.setVisible(True)
        self._proof_raw = ""

        system = (
            "You are a precise copy-editor. Analyse the provided text for:\n"
            "- Grammar and punctuation errors\n"
            "- Awkward or unclear phrasing\n"
            "- Repetitive words or sentence structures\n"
            "- Structural issues (abrupt transitions, isolated sentences)\n\n"
            "Return ONLY a numbered list. Each item must follow this exact format:\n"
            "N. PHRASE: \"exact problematic phrase from the text\" | "
            "SUGGESTION: your suggestion\n\n"
            "Maximum 10 issues. Be specific and actionable. "
            "No commentary outside the list."
        )
        prompt = f"Proofread this document:\n\n{doc[:12000]}"

        self._stop_worker()
        self._worker = _ArtemisAIWorker(prompt=prompt, system=system)
        self._worker.chunk_ready.connect(
            lambda c: setattr(self, "_proof_raw", self._proof_raw + c))
        self._worker.finished.connect(self._on_proof_done)
        self._worker.error.connect(self._on_proof_error)
        self._worker.start()

    def _on_proof_done(self):
        self._proof_btn.setEnabled(True)
        self._proof_status.setText("")
        self._parse_and_render_proof(self._proof_raw)

    def _on_proof_error(self, err: str):
        self._proof_btn.setEnabled(True)
        self._proof_status.setText(f"Error: {err}")

    def _parse_and_render_proof(self, raw: str):
        import re as _re
        issues = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            m = _re.match(
                r'\d+\.\s*PHRASE:\s*"(.+?)"\s*\|\s*SUGGESTION:\s*(.+)',
                line, _re.IGNORECASE)
            if m:
                issues.append((m.group(1).strip(), m.group(2).strip()))

        if not issues:
            lbl = QLabel("No significant issues found.")
            lbl.setStyleSheet(
                f"color:{ACCENT2}; font-size:11px; "
                f"font-family:{FONT_UI}; background:transparent;")
            self._proof_list_layout.insertWidget(
                self._proof_list_layout.count() - 1, lbl)
            return

        for phrase, suggestion in issues:
            card = self._make_issue_card(phrase, suggestion)
            self._proof_list_layout.insertWidget(
                self._proof_list_layout.count() - 1, card)

        self._proof_status.setText(
            f"{len(issues)} issue{'s' if len(issues) != 1 else ''} found.")

    def _make_issue_card(self, phrase: str, suggestion: str) -> QWidget:
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background:#0A0A0D; border:1px solid {BORDER};
                border-radius:5px;
            }}
            QFrame:hover {{ border-color:rgba(201,168,76,.35); }}
        """)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(9, 8, 9, 8)
        cv.setSpacing(4)

        phrase_lbl = QLabel(f'"{phrase}"')
        phrase_lbl.setWordWrap(True)
        phrase_lbl.setStyleSheet(
            f"color:{RED}; font-size:11px; "
            f"font-family:'Palatino Linotype',Georgia,serif; "
            f"background:transparent;")

        sug_lbl = QLabel(suggestion)
        sug_lbl.setWordWrap(True)
        sug_lbl.setStyleSheet(
            f"color:{TEXT2}; font-size:11px; font-family:{FONT_UI}; "
            f"background:transparent; line-height:1.5;")

        fix_btn = QPushButton("AI Fix")
        fix_btn.setFixedHeight(24)
        fix_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; border:1px solid {BORDER2};
                border-radius:4px; color:{ACCENT};
                font-size:10px; font-family:{FONT_UI}; padding:0 8px;
            }}
            QPushButton:hover {{ background:rgba(201,168,76,.1); }}
        """)
        fix_btn.clicked.connect(
            lambda _, p=phrase, s=suggestion: self._run_ai_fix(p, s))

        cv.addWidget(phrase_lbl)
        cv.addWidget(sug_lbl)
        cv.addWidget(fix_btn)

        card.mousePressEvent = lambda e, p=phrase: self.jump_to_phrase.emit(p)
        return card

    def _run_ai_fix(self, phrase: str, suggestion: str):
        system = (
            "You are a copy-editor. Given a problematic phrase and a "
            "suggestion, return ONLY the corrected replacement text — "
            "nothing else, no quotes, no commentary."
        )
        prompt = (
            f'Problematic phrase: "{phrase}"\n'
            f"Suggestion: {suggestion}\n\n"
            f"Provide the corrected replacement:"
        )
        self._proof_fix_phrase = phrase
        self._proof_fix_buf    = ""
        self._proof_status.setText("Generating fix…")

        self._stop_worker()
        self._worker = _ArtemisAIWorker(prompt=prompt, system=system)
        self._worker.chunk_ready.connect(
            lambda c: setattr(self, "_proof_fix_buf",
                               self._proof_fix_buf + c))
        self._worker.finished.connect(self._on_fix_done)
        self._worker.error.connect(
            lambda e: self._proof_status.setText(f"Fix error: {e}"))
        self._worker.start()

    def _on_fix_done(self):
        fixed = self._proof_fix_buf.strip().strip('"\'')
        if fixed:
            self.apply_fix.emit(self._proof_fix_phrase, fixed)
        self._proof_status.setText(
            "Fix applied." if fixed else "No fix generated.")

    # ── shared helpers ────────────────────────────────────────────────────────

    def _stop_worker(self):
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            try:
                self._worker.chunk_ready.disconnect()
            except Exception:
                pass
            try:
                self._worker.finished.disconnect()
            except Exception:
                pass
            try:
                self._worker.error.disconnect()
            except Exception:
                pass
            self._worker.wait(2000)
        self._worker = None

    @staticmethod
    def _stream_into_label(label: QLabel, chunk: str):
        buf = getattr(label, "_stream_buf", "")
        buf += chunk
        label._stream_buf = buf
        label.setText(buf + " ▌")

    @staticmethod
    def _action_btn_style() -> str:
        return (
            f"QPushButton{{background:transparent;border:1px solid {BORDER};"
            f"border-radius:5px;color:{TEXT2};font-size:11px;"
            f"font-family:{FONT_UI};padding:8px 12px;text-align:left;}}"
            f"QPushButton:hover{{border-color:rgba(201,168,76,.4);"
            f"color:{ACCENT};}}"
            f"QPushButton:disabled{{color:{MUTED};border-color:{BORDER};}}"
        )

    @staticmethod
    def _accept_btn_style() -> str:
        return (
            f"QPushButton{{background:{ACCENT2};color:#0A0A0D;border:none;"
            f"border-radius:5px;font-weight:bold;font-family:{FONT_UI};"
            f"font-size:11px;padding:6px;}}"
            f"QPushButton:hover{{background:#5ED4B0;}}"
        )

    @staticmethod
    def _discard_btn_style() -> str:
        return (
            f"QPushButton{{background:#1A1A24;color:{MUTED};"
            f"border:1px solid {BORDER};border-radius:5px;"
            f"font-family:{FONT_UI};font-size:11px;padding:6px;}}"
            f"QPushButton:hover{{color:{TEXT2};}}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# MAIN EDITOR CANVAS
# ══════════════════════════════════════════════════════════════════════════════

class _TouchScrollFilter(QObject):
    """Intercepts touch events on QTextEdit and converts them to smooth scroll,
    preventing raw touch input from crashing Qt on touchpad/touchscreen devices."""
    def __init__(self, scroll_widget, parent=None):
        super().__init__(parent)
        self._widget  = scroll_widget
        self._start_y = None

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        t = event.type()
        if t == QEvent.Type.TouchBegin:
            pts = event.points()
            if pts:
                self._start_y = pts[0].position().y()
            return True
        elif t == QEvent.Type.TouchUpdate:
            pts = event.points()
            if pts and self._start_y is not None:
                dy = self._start_y - pts[0].position().y()
                self._widget.verticalScrollBar().setValue(
                    self._widget.verticalScrollBar().value() + int(dy))
                self._start_y = pts[0].position().y()
            return True
        elif t == QEvent.Type.TouchEnd:
            self._start_y = None
            return True
        return False


class WriterCanvas(QTextEdit):
    """
    The actual typing area. Styled to match the Artemis reading aesthetic —
    Palatino body text, generous padding, warm off-white on near-black.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        self.setStyleSheet("""
            QTextEdit {
                background: #13131A;
                border: 1px solid #1E1E2A;
                border-radius: 12px;
                color: #E8E4DC;
                font-family: 'Palatino Linotype', Palatino, Georgia, serif;
                font-size: 18px;
                selection-background-color: rgba(201,168,76,0.2);
                padding: 48px 64px;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 4px;
                border: none;
                margin: 12px 0;
            }
            QScrollBar::handle:vertical {
                background: #2A2A36;
                border-radius: 2px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: #C9A84C; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        # Set default block format (line spacing)
        fmt = QTextBlockFormat()
        fmt.setLineHeight(160, 1)  # 160% line height
        cursor = self.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.mergeBlockFormat(fmt)
        self.setTextCursor(cursor)

        # Default char format
        char_fmt = QTextCharFormat()
        char_fmt.setFontFamily("Palatino Linotype")
        char_fmt.setFontPointSize(18)
        char_fmt.setForeground(QColor(TEXT))
        self.mergeCurrentCharFormat(char_fmt)

        # Touch filter — prevents crash from raw touchpad/touchscreen events
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self._touch_filter = _TouchScrollFilter(self, self)
        self.installEventFilter(self._touch_filter)

    def paintEvent(self, event):
        super().paintEvent(event)
        # Draw subtle page margin guides
        if self.document().isEmpty():
            self._draw_placeholder()

    def _draw_placeholder(self):
        p = QPainter(self.viewport())
        p.setPen(QPen(QColor(MUTED), 1))
        p.setFont(QFont(FONT_BODY.split(",")[0].strip(), 18))
        rect = self.viewport().rect().adjusted(60, 60, -60, -60)
        p.drawText(rect, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                   "Begin writing…")
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
# EDITOR PAGE  (embeddable QWidget — drop into main app as-is)
# ══════════════════════════════════════════════════════════════════════════════

class EditorPage(QWidget):
    """
    Self-contained editor page.

    Standalone: wrap in a QMainWindow (see bottom of file).
    Embedded:   add directly to the Artemis QStackedWidget.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path: str | None = None
        self._modified = False
        self._find_dialog: FindReplaceDialog | None = None
        self._build()
        self._wire_shortcuts()
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.timeout.connect(self._auto_save)
        self._auto_save_timer.start(30_000)  # auto-save every 30 s
        self._text_change_timer = QTimer(self)
        self._text_change_timer.setSingleShot(True)
        self._text_change_timer.timeout.connect(self._on_text_changed_debounced)
        QTimer.singleShot(100, self._restore_last_open)

    # ── Construction ──────────────────────────────────────────────────────────

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Document sidebar (left)
        self._sidebar = DocumentSidebar()
        self._sidebar.new_doc.connect(self.new_document)
        self._sidebar.open_file.connect(self._load_file)
        root.addWidget(self._sidebar)

        # ── Centre: dark workspace
        right = QWidget()
        right.setStyleSheet("background: #0C0C10;")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        # Canvas created first so toolbar can reference it
        self._canvas = WriterCanvas()
        self._canvas.textChanged.connect(self._on_text_changed)

        # Toolbar
        self._toolbar = EditorToolbar(self._canvas)
        self._toolbar.sig_new.connect(self.new_document)
        self._toolbar.sig_open.connect(self.open_document)
        self._toolbar.sig_save.connect(self.save_document)
        self._toolbar.sig_save_as.connect(self.save_as_document)
        self._toolbar.sig_export.connect(self.export_txt)
        self._toolbar.sig_print.connect(self.print_document)
        self._toolbar.sig_find.connect(self.show_find)
        self._toolbar.sig_toggle_ai.connect(self._toggle_ai_sidebar)
        rv.addWidget(self._toolbar)

        # Canvas with breathing room
        page_area = QWidget()
        page_area.setStyleSheet("background: #0C0C10;")
        pa = QHBoxLayout(page_area)
        pa.setContentsMargins(32, 24, 32, 24)
        pa.setSpacing(0)
        pa.addWidget(self._canvas, 1)
        rv.addWidget(page_area, 1)

        # Status bar
        rv.addWidget(self._build_status())

        root.addWidget(right, 1)

        # ── AI Sidebar (right) — hidden by default
        self._ai_sidebar = AISidebar()
        self._ai_sidebar.setVisible(False)

        # Signals: sidebar → canvas
        self._ai_sidebar.insert_text.connect(self._ai_insert_text)
        self._ai_sidebar.replace_selection.connect(self._ai_replace_selection)
        self._ai_sidebar.jump_to_phrase.connect(self._ai_jump_to_phrase)
        self._ai_sidebar.apply_fix.connect(self._ai_apply_fix)

        # Signals: sidebar requesting data from canvas
        self._ai_sidebar.request_doc_text.connect(self._push_doc_text)
        self._ai_sidebar.request_selection.connect(self._push_selection)

        root.addWidget(self._ai_sidebar)

    def _margin_widget(self):
        """Decorative side margin with a faint rule."""
        w = QWidget()
        w.setStyleSheet(f"background:{BG};")
        return w

    def _build_status(self):
        bar = QWidget()
        bar.setFixedHeight(26)
        bar.setStyleSheet(
            "background:#0A0A0D; border-top:1px solid #1A1A22;")
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 0, 20, 0)
        h.setSpacing(20)
        self._status_path = QLabel("Untitled")
        self._status_path.setStyleSheet(
            f"color:#3A3A4A; font-size:10px; font-family:{FONT_UI};")
        self._status_modified = QLabel("")
        self._status_modified.setStyleSheet(
            f"color:{ACCENT}; font-size:10px; font-family:{FONT_UI};")
        self._status_chars = QLabel("0 chars")
        self._status_chars.setStyleSheet(
            f"color:{MUTED}; font-size:10px; font-family:{FONT_UI};")
        h.addWidget(self._status_path)
        h.addWidget(self._status_modified)
        h.addStretch()
        h.addWidget(self._status_chars)
        return bar

    # ── Keyboard shortcuts ─────────────────────────────────────────────────────

    def _wire_shortcuts(self):
        def sc(seq, fn):
            s = QShortcut(QKeySequence(seq), self)
            s.activated.connect(fn)
        sc("Ctrl+N",       self.new_document)
        sc("Ctrl+O",       self.open_document)
        sc("Ctrl+S",       self.save_document)
        sc("Ctrl+Shift+S", self.save_as_document)
        sc("Ctrl+F",       self.show_find)
        sc("Ctrl+P",       self.print_document)
        sc("Ctrl+Shift+A", self._toggle_ai_sidebar)

    # ── AI sidebar ────────────────────────────────────────────────────────────

    def _toggle_ai_sidebar(self):
        """Show/hide the AI sidebar and collapse/restore the doc sidebar."""
        visible = not self._ai_sidebar.isVisible()
        self._ai_sidebar.setVisible(visible)
        # Sync the toolbar toggle button state
        self._toolbar._btn_sage.setChecked(visible)
        # Collapse doc sidebar when AI is open to give maximum space
        self._sidebar.setVisible(not visible)
        if visible:
            # Push current doc text so sidebar is ready immediately
            self._push_doc_text()

    def _push_doc_text(self):
        """Send current plain-text document content to the AI sidebar."""
        self._ai_sidebar.set_doc_text(self._canvas.toPlainText())

    def _push_selection(self):
        """Send current canvas selection to the AI sidebar."""
        cursor = self._canvas.textCursor()
        self._ai_sidebar.set_selection(cursor.selectedText())

    def _ai_insert_text(self, text: str):
        """Insert AI continuation text at the current cursor position."""
        cursor = self._canvas.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._canvas.setTextCursor(cursor)
        self._canvas.insertPlainText(text)
        self._canvas.setFocus()

    def _ai_replace_selection(self, text: str):
        """Replace the current selection with the AI rewrite."""
        cursor = self._canvas.textCursor()
        if cursor.hasSelection():
            cursor.insertText(text)
        else:
            # Fallback: insert at cursor if selection was lost
            cursor.insertText(text)
        self._canvas.setTextCursor(cursor)
        self._canvas.setFocus()

    def _ai_jump_to_phrase(self, phrase: str):
        """Find phrase in canvas and select it so user can see it."""
        # Reset to top then search
        cursor = self._canvas.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self._canvas.setTextCursor(cursor)
        found = self._canvas.find(phrase)
        if not found:
            # Phrase not found verbatim — try first 40 chars
            self._canvas.find(phrase[:40])
        self._canvas.setFocus()

    def _ai_apply_fix(self, original_phrase: str, fixed_text: str):
        """Find original_phrase in canvas and replace it with fixed_text."""
        import re as _re
        doc   = self._canvas.document()
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        found_cursor = doc.find(original_phrase, cursor)
        if not found_cursor.isNull():
            found_cursor.insertText(fixed_text)
        cursor.endEditBlock()
        self._canvas.setFocus()

    # ── Document operations ───────────────────────────────────────────────────

    def new_document(self):
        if self._modified and not self._confirm_discard():
            return
        self._canvas.clear()
        self._current_path = None
        self._modified = False
        self._update_status()

    def _save_recent(self, path: str):
        try:
            with open(RECENT_FILE, "w") as f:
                json.dump({"last_open": path}, f)
        except Exception:
            pass

    def _restore_last_open(self):
        try:
            if RECENT_FILE.exists():
                with open(RECENT_FILE, "r") as f:
                    data = json.load(f)
                last = data.get("last_open")
                if last and Path(last).exists():
                    self._load_file(last)
        except Exception:
            pass

    def open_document(self):
        if self._modified and not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Document", str(DOCS_DIR),
            "Artemis (*.art);;HTML Files (*.html);;All Files (*.*)"
        )
        if path:
            self._load_file(path)

    def _load_file(self, path: str):
        self._auto_save_timer.stop()
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            # Disconnect signals during load to prevent crash in currentCharFormatChanged
            # when setHtml() fires the signal before the toolbar widgets are ready
            # Disconnect format signals during load — setHtml fires currentCharFormatChanged
            # which crashes Qt on Mesa when it tries to call setStyleSheet mid-load
            try:
                self._canvas.currentCharFormatChanged.disconnect()
                self._canvas.cursorPositionChanged.disconnect()
            except Exception:
                pass
            if path.endswith(".html") or path.endswith(".art"):
                self._canvas.setHtml(content)
            else:
                self._canvas.setPlainText(content)
            # Reconnect after load is complete
            self._toolbar._signals_connected = False
            self._toolbar._connect_editor()
            self._current_path = path
            self._modified = False
            self._update_status()
            self._sidebar.set_active(path)
            self._save_recent(path)
        except Exception as e:
            QMessageBox.warning(self, "Open Failed", f"Could not open file:\n{e}")
        finally:
            self._auto_save_timer.start(30_000)

    def save_document(self):
        if self._current_path is None:
            self.save_as_document()
        else:
            self._write_file(self._current_path)

    def save_as_document(self):
        suggested = DOCS_DIR / "Untitled.art"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Document", str(suggested),
            "Artemis (*.art);;HTML Files (*.html)"
        )
        if path:
            if not path.endswith(".art") and not path.endswith(".html"):
                path += ".art"
            self._write_file(path)
            self._sidebar.refresh()
            self._sidebar.set_active(path)

    def _write_file(self, path: str):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._canvas.toHtml())
            self._current_path = path
            self._modified = False
            self._update_status()
            self._save_recent(path)
        except Exception as e:
            QMessageBox.warning(self, "Save Failed", f"Could not save file:\n{e}")

    def export_txt(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export as Text", str(DOCS_DIR),
            "Text Files (*.txt);;All Files (*.*)"
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self._canvas.toPlainText())
            except Exception as e:
                QMessageBox.warning(self, "Export Failed", str(e))

    def print_document(self):
        if not _HAS_PRINT:
            QMessageBox.information(self, "Print", "Print support is not available.\nInstall it with: pip install PyQt6[full]")
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dlg = QPrintDialog(printer, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._canvas.print(printer)

    def show_find(self):
        if self._find_dialog is None:
            self._find_dialog = FindReplaceDialog(self._canvas, self)
        self._find_dialog.show()
        self._find_dialog.raise_()
        self._find_dialog.activateWindow()

    def _auto_save(self):
        if self._modified and self._current_path:
            self._write_file(self._current_path)

    # ── State tracking ─────────────────────────────────────────────────────────

    def _on_text_changed(self):
        self._modified = True
        self._status_modified.setText("● unsaved")
        self._text_change_timer.start(300)

    def _on_text_changed_debounced(self):
        text = self._canvas.toPlainText()
        self._status_chars.setText(f"{len(text):,} chars")

    def _update_status(self):
        name = Path(self._current_path).stem if self._current_path else "Untitled"
        self._status_path.setText(name)
        self._status_modified.setText("" if not self._modified else "● unsaved")

    def refresh(self):
        """Called by Great Sage when navigating to this page."""
        self._sidebar.refresh()

    def _confirm_discard(self) -> bool:
        reply = QMessageBox.question(
            self, "Unsaved Changes",
            "You have unsaved changes. Discard them?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        return reply == QMessageBox.StandardButton.Discard


# ══════════════════════════════════════════════════════════════════════════════
# STANDALONE WINDOW (removed when merging into main app)
# ══════════════════════════════════════════════════════════════════════════════

class WriterWindow(QMainWindow):
    """
    Thin QMainWindow shell — only used when running artemis.py standalone.
    When merging into Artemis, add EditorPage() directly to the nav stack.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("◈ Artemis")
        self.resize(1200, 800)
        self.setStyleSheet(f"QMainWindow {{ background:{BG}; }}")

        self._page = EditorPage()
        self.setCentralWidget(self._page)

        # Apply the full Great Sage QSS if available
        if QSS:
            QApplication.instance().setStyleSheet(QSS)

    def closeEvent(self, e):
        if self._page._modified and not self._page._confirm_discard():
            e.ignore()
        else:
            e.accept()


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Artemis")

    # Global stylesheet baseline
    app.setStyleSheet(f"""
        * {{ font-family:{FONT_UI}; font-size:13px; color:{TEXT}; }}
        QMainWindow, QWidget, QDialog {{ background:{BG}; }}
        QToolTip {{
            background:{PANEL}; border:1px solid {BORDER2}; color:{TEXT};
            padding:4px 8px; font-size:11px;
        }}
        QMessageBox {{ background:{BG2}; }}
        QMessageBox QLabel {{ color:{TEXT}; }}
        QMessageBox QPushButton {{
            background:{BG3}; border:1px solid {BORDER}; border-radius:3px;
            color:{TEXT2}; padding:6px 18px; min-width:80px;
        }}
        QMessageBox QPushButton:hover {{ border-color:{ACCENT}; color:{ACCENT}; }}
        QFileDialog {{ background:{BG2}; color:{TEXT}; }}
        QColorDialog {{ background:{BG2}; }}
    """)

    win = WriterWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

