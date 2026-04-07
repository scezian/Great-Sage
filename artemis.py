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
    QKeySequence, QShortcut, QAction, QTextDocument, QPainter,
    QLinearGradient, QBrush, QPen, QIcon,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QLabel, QPushButton, QFrame, QFileDialog, QMessageBox,
    QComboBox, QSizePolicy, QScrollArea, QStatusBar, QFontComboBox,
    QSpinBox, QColorDialog, QSplitter, QListWidget, QListWidgetItem,
    QDialog, QDialogButtonBox, QLineEdit, QMenu, QToolButton,
)
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog

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
        self.setFixedWidth(220)
        self.setStyleSheet(f"background:{BG2}; border-right:1px solid {BORDER};")
        self._build()
        self.refresh()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Header
        hdr = QWidget()
        hdr.setFixedHeight(48)
        hdr.setStyleSheet(f"background:{BG2}; border-bottom:1px solid {BORDER};")
        hh = QHBoxLayout(hdr)
        hh.setContentsMargins(14, 0, 10, 0)
        title = QLabel("DOCUMENTS")
        title.setStyleSheet(
            f"color:{MUTED}; font-family:{FONT_UI}; font-size:9px; letter-spacing:2px;")
        new_btn = _ToolBtn("＋", "New document")
        new_btn.setFixedWidth(28)
        new_btn.clicked.connect(self.new_doc.emit)
        hh.addWidget(title)
        hh.addStretch()
        hh.addWidget(new_btn)
        v.addWidget(hdr)

        # List
        self._list = QListWidget()
        self._list.setStyleSheet(f"""
            QListWidget {{
                background: transparent;
                border: none;
                outline: none;
                font-family: {FONT_UI};
            }}
            QListWidget::item {{
                padding: 10px 14px;
                border-bottom: 1px solid {BG3};
                color: {TEXT2};
                font-size: 12px;
            }}
            QListWidget::item:hover {{
                background: {BG3};
                color: {TEXT};
            }}
            QListWidget::item:selected {{
                background: {BG3};
                color: {ACCENT};
                border-left: 2px solid {ACCENT};
            }}
        """)
        self._list.itemDoubleClicked.connect(self._on_open)
        v.addWidget(self._list, 1)

        # Bottom hint
        hint = QLabel("Double-click to open")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(
            f"color:{MUTED}; font-size:9px; font-family:{FONT_UI}; "
            f"padding:8px; border-top:1px solid {BORDER};")
        v.addWidget(hint)

    def refresh(self):
        self._list.clear()
        files = sorted(DOCS_DIR.glob("*.art"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            item = QListWidgetItem("No documents yet")
            item.setForeground(QColor(MUTED))
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
        self.setFixedHeight(44)
        self.setStyleSheet(f"""
            QWidget {{
                background: #111116;
                border-bottom: 1px solid #1c1c24;
            }}
            QPushButton {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                color: #a0a0b4;
                font-family: {FONT_UI};
                font-size: 13px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{
                background: #1c1c24;
                color: #e8e4dc;
            }}
            QPushButton:pressed {{
                background: #252530;
            }}
            QPushButton#primary {{
                background: #c9a84c;
                border-color: #c9a84c;
                color: #0c0c0e;
                font-weight: 500;
            }}
            QPushButton#primary:hover {{
                background: #d4b460;
            }}
            QPushButton#icon {{
                padding: 6px 8px;
                font-size: 14px;
            }}
            QPushButton#checkable:checked {{
                background: #c9a84c22;
                color: #c9a84c;
                border-color: #c9a84c55;
            }}
            QFrame#divider {{
                background: #252530;
            }}
        """)
        self._build()
        self._connect_editor()

    def _build(self):
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(4)

        # File ops (icon buttons)
        self._btn_new = QPushButton("+")
        self._btn_new.setObjectName("icon")
        self._btn_new.setToolTip("New (Ctrl+N)")
        
        self._btn_open = QPushButton("↑")
        self._btn_open.setObjectName("icon")
        self._btn_open.setToolTip("Open (Ctrl+O)")
        
        self._btn_save = QPushButton("↓")
        self._btn_save.setObjectName("icon")
        self._btn_save.setToolTip("Save (Ctrl+S)")
        
        for btn in (self._btn_new, self._btn_open, self._btn_save):
            h.addWidget(btn)
        
        self._btn_new.clicked.connect(self.sig_new.emit)
        self._btn_open.clicked.connect(self.sig_open.emit)
        self._btn_save.clicked.connect(self.sig_save.emit)
        
        # Divider
        div1 = QFrame()
        div1.setObjectName("divider")
        div1.setFixedWidth(1)
        div1.setFixedHeight(20)
        h.addSpacing(8)
        h.addWidget(div1)
        h.addSpacing(8)
        
        # Formatting
        self._btn_bold = QPushButton("B")
        self._btn_bold.setObjectName("checkable")
        self._btn_bold.setCheckable(True)
        self._btn_bold.setToolTip("Bold (Ctrl+B)")
        self._btn_bold.setFont(QFont("Palatino Linotype", 12, QFont.Weight.Bold))
        
        self._btn_italic = QPushButton("I")
        self._btn_italic.setObjectName("checkable")
        self._btn_italic.setCheckable(True)
        self._btn_italic.setToolTip("Italic (Ctrl+I)")
        
        self._btn_under = QPushButton("U")
        self._btn_under.setObjectName("checkable")
        self._btn_under.setCheckable(True)
        self._btn_under.setToolTip("Underline (Ctrl+U)")
        
        for btn in (self._btn_bold, self._btn_italic, self._btn_under):
            h.addWidget(btn)
        
        self._btn_bold.clicked.connect(self._toggle_bold)
        self._btn_italic.clicked.connect(self._toggle_italic)
        self._btn_under.clicked.connect(self._toggle_underline)
        
        # Divider
        div2 = QFrame()
        div2.setObjectName("divider")
        div2.setFixedWidth(1)
        div2.setFixedHeight(20)
        h.addSpacing(8)
        h.addWidget(div2)
        h.addSpacing(8)
        
        # Headings & Lists
        self._btn_h1 = QPushButton("H1")
        self._btn_h1.setToolTip("Heading 1")
        self._btn_h1.clicked.connect(lambda: self._apply_heading(1))
        
        self._btn_h2 = QPushButton("H2")
        self._btn_h2.setToolTip("Heading 2")
        self._btn_h2.clicked.connect(lambda: self._apply_heading(2))
        
        self._btn_list = QPushButton("☰")
        self._btn_list.setObjectName("icon")
        self._btn_list.setToolTip("Bullet List")
        self._btn_list.clicked.connect(self._insert_bullet)
        
        h.addWidget(self._btn_h1)
        h.addWidget(self._btn_h2)
        h.addWidget(self._btn_list)
        
        # Spacer
        h.addStretch()
        
        # Right side: Export + Save
        self._btn_export = QPushButton("Export")
        self._btn_export.setToolTip("Export as text")
        
        self._btn_save_main = QPushButton("Save")
        self._btn_save_main.setObjectName("primary")
        self._btn_save_main.setToolTip("Save document")
        
        h.addWidget(self._btn_export)
        h.addWidget(self._btn_save_main)
        
        self._btn_export.clicked.connect(self.sig_export.emit)
        self._btn_save_main.clicked.connect(self.sig_save.emit)
        
        # Word count label
        self._wc_lbl = QLabel("0 words")
        self._wc_lbl.setStyleSheet("color: #606070; font-size: 12px; margin-left: 12px;")
        h.addWidget(self._wc_lbl)

    def _connect_editor(self):
        self._editor.currentCharFormatChanged.connect(self._sync_format)
        self._editor.textChanged.connect(self._update_wc)

    def _sync_format(self, fmt: QTextCharFormat):
        self._btn_bold.setChecked(fmt.fontWeight() >= QFont.Weight.Bold)
        self._btn_italic.setChecked(fmt.fontItalic())
        self._btn_under.setChecked(fmt.fontUnderline())

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

    def _apply_heading(self, level: int):
        sizes = {1: 28, 2: 22}
        fmt = QTextCharFormat()
        fmt.setFontPointSize(sizes[level])
        fmt.setFontWeight(QFont.Weight.Bold)
        self._merge_format(fmt)

    def _insert_bullet(self):
        cursor = self._editor.textCursor()
        cursor.insertList(QTextCursor.BlockInsertionMode.ListSquare)

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
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {BG};
                border: none;
                color: {TEXT};
                font-family: {FONT_BODY};
                font-size: 18px;
                line-height: 1.8;
                selection-background-color: #2A3020;
                padding: 0;
            }}
            QScrollBar:vertical {{
                background: {BG};
                width: 4px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {BORDER};
                border-radius: 2px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
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

    # ── Construction ──────────────────────────────────────────────────────────

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar
        self._sidebar = DocumentSidebar()
        self._sidebar.new_doc.connect(self.new_document)
        self._sidebar.open_file.connect(self._load_file)
        root.addWidget(self._sidebar)

        # Right pane: toolbar + canvas + status
        right = QWidget()
        right.setStyleSheet(f"background:{BG};")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        # Canvas (created first so toolbar can reference it)
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

        rv.addWidget(self._toolbar)

        # Page area — centred column with margins
        page_area = QWidget()
        page_area.setStyleSheet(f"background:{BG};")
        pa = QHBoxLayout(page_area)
        pa.setContentsMargins(0, 0, 0, 0)
        pa.setSpacing(0)

        left_margin  = self._margin_widget()
        right_margin = self._margin_widget()

        pa.addWidget(left_margin, 1)
        pa.addWidget(self._canvas, 5)
        pa.addWidget(right_margin, 1)

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
        bar.setFixedHeight(28)
        bar.setStyleSheet(
            f"background:{BG2}; border-top:1px solid {BORDER};")
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 0, 14, 0)
        h.setSpacing(20)
        self._status_path = QLabel("Untitled")
        self._status_path.setStyleSheet(
            f"color:{MUTED}; font-size:10px; font-family:{FONT_UI};")
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
            if path.endswith(".html") or path.endswith(".art"):
                self._canvas.setHtml(content)
            else:
                self._canvas.setPlainText(content)
            self._current_path = path
            self._modified = False
            self._update_status()
            self._sidebar.set_active(path)
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

