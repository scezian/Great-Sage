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
    Qt, QTimer, QSize, pyqtSignal, QMimeData,
)
from PyQt6.QtGui import (
    QColor, QFont, QTextCursor, QTextCharFormat, QTextBlockFormat,
    QTextListFormat, QKeySequence, QShortcut, QAction, QTextDocument, QPainter,
    QLinearGradient, QBrush, QPen, QIcon,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QLabel, QPushButton, QFrame, QFileDialog, QMessageBox,
    QComboBox, QSizePolicy, QScrollArea, QStatusBar, QFontComboBox,
    QSpinBox, QColorDialog, QSplitter, QListWidget, QListWidgetItem,
    QDialog, QDialogButtonBox, QLineEdit, QMenu, QToolButton,
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
        for w in (self._btn_find, self._btn_export, self._btn_save_main, self._wc_lbl):
            h2.addWidget(w)

        main.addWidget(row2)

    def _connect_editor(self):
        self._editor.currentCharFormatChanged.connect(self._sync_format)
        self._editor.cursorPositionChanged.connect(self._sync_alignment)
        self._editor.textChanged.connect(self._update_wc)
        self._signals_connected = True

    def _sync_format(self, fmt: QTextCharFormat):
        self._btn_bold.setChecked(fmt.fontWeight() >= QFont.Weight.Bold)
        self._btn_italic.setChecked(fmt.fontItalic())
        self._btn_under.setChecked(fmt.fontUnderline())
        self._btn_strike.setChecked(fmt.fontStrikeOut())
        vt = fmt.verticalAlignment()
        self._btn_sub.setChecked(vt == QTextCharFormat.VerticalAlignment.AlignSubScript)
        self._btn_sup.setChecked(vt == QTextCharFormat.VerticalAlignment.AlignSuperScript)
        # Update color underline indicator
        col = fmt.foreground().color()
        if col.isValid():
            # Single-rule only — multi-rule QPushButton stylesheets crash Qt on Mesa
            self._btn_color.setStyleSheet(
                f"border-bottom: 2px solid {col.name()};"
            )
        # Sync font combo and size
        self._font_combo.blockSignals(True)
        if fmt.fontFamily():
            self._font_combo.setCurrentFont(QFont(fmt.fontFamily()))
        self._font_combo.blockSignals(False)
        self._size_spin.blockSignals(True)
        if fmt.fontPointSize() > 0:
            self._size_spin.setValue(int(fmt.fontPointSize()))
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
        doc   = self._editor.document()
        text  = doc.toPlainText()
        count = text.count(term)
        # Use document's find/replace
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        while True:
            cursor = doc.find(term, cursor)
            if cursor.isNull():
                break
            cursor.insertText(repl)
        cursor.endEditBlock()
        self._status.setText(f"Replaced {count} occurrence{'s' if count != 1 else ''}.")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN EDITOR CANVAS
# ══════════════════════════════════════════════════════════════════════════════

class WriterCanvas(QTextEdit):
    """
    The actual typing area. Styled to match the Artemis reading aesthetic —
    Palatino body text, generous padding, warm off-white on near-black.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.setWordWrapMode(
            __import__("PyQt6.QtGui", fromlist=["QTextOption"]).QTextOption.WrapMode.WordWrap
        )
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
        QTimer.singleShot(100, self._restore_last_open)

    # ── Construction ──────────────────────────────────────────────────────────

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar
        self._sidebar = DocumentSidebar()
        self._sidebar.new_doc.connect(self.new_document)
        self._sidebar.open_file.connect(self._load_file)
        root.addWidget(self._sidebar)

        # ── Right: dark workspace
        right = QWidget()
        right.setStyleSheet("background: #0C0C10;")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        # Canvas created first so toolbar can ref it
        self._canvas = WriterCanvas()
        self._canvas.textChanged.connect(self._on_text_changed)

        # Toolbar (thin, at top)
        self._toolbar = EditorToolbar(self._canvas)
        self._toolbar.sig_new.connect(self.new_document)
        self._toolbar.sig_open.connect(self.open_document)
        self._toolbar.sig_save.connect(self.save_document)
        self._toolbar.sig_save_as.connect(self.save_as_document)
        self._toolbar.sig_export.connect(self.export_txt)
        self._toolbar.sig_print.connect(self.print_document)
        self._toolbar.sig_find.connect(self.show_find)
        rv.addWidget(self._toolbar)

        # Canvas centred with breathing room
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
            self._toolbar._connect_editor()
            self._current_path = path
            self._modified = False
            self._update_status()
            self._sidebar.set_active(path)
            self._save_recent(path)
        except Exception as e:
            QMessageBox.warning(self, "Open Failed", f"Could not open file:\n{e}")

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
        text = self._canvas.toPlainText()
        self._status_chars.setText(f"{len(text):,} chars")
        self._status_modified.setText("● unsaved")

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

