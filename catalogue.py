"""
catalogue.py — Chapter-anchored notes for Great Sage
=====================================================
Stores notes per book, per chapter, in:
  ~/Documents/Great Sage/Catalogue/<Book Title>/notes.json

Exposes:
  CataloguePanel(parent)  — QFrame sidebar, mirrors the Sage panel pattern
  catalogue_data(book)    — load notes for a book
  save_note(book, ch, text, tags) — save a note
"""

from __future__ import annotations
import json, os, re
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore    import Qt, pyqtSignal, QObject
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QScrollArea, QWidget, QCheckBox, QSizePolicy,
    QMessageBox
)
from PyQt6.QtGui import QTextCharFormat, QFont, QColor

# ── Colours (mirror great_sage_gui.py constants) ──────────────────────────────
BG      = "#080B0F"; BG2 = "#0D1117"; BG3 = "#111820"; PANEL = "#141D28"
BORDER  = "#1E2D3D"; ACCENT = "#E8C97A"; ACCENT2 = "#4FC4A0"; NEON = "#00E5CC"
MUTED   = "#4A6070"; TEXT = "#D4E4EE"; TEXT2 = "#7A9BB0"; RED = "#E05C6A"
PURPLE  = "#9B72CF"
FONT_UI = "JetBrains Mono, Fira Code, Consolas, monospace"
FONT_BODY = "Palatino Linotype, Palatino, Book Antiqua, Georgia, serif"

# ── Tag definitions ────────────────────────────────────────────────────────────
TAGS = [
    ("Character",   "#4FC4A0"),
    ("Plot",        "#E8C97A"),
    ("Power-up",    "#9B72CF"),
    ("World",       "#00E5CC"),
    ("Reaction",    "#E05C6A"),
    ("Quote",       "#7A9BB0"),
]

# ── Paths ──────────────────────────────────────────────────────────────────────
CATALOGUE_ROOT = Path(__file__).resolve().parent / "Catalogue"



# ── Flow layout — wraps tag chips to next line when panel is narrow ───────────
class _FlowLayout(QVBoxLayout):
    """
    Wraps child widgets into multiple rows.
    Used for the tag chip rows so they never overflow the 320px panel.
    """
    def __init__(self, parent=None, h_spacing=4, v_spacing=4):
        super().__init__(parent)
        self._h = h_spacing
        self._v = v_spacing
        self._rows: list[QHBoxLayout] = []
        self._cur_row: QHBoxLayout | None = None
        self.setSpacing(v_spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def add_chip(self, widget: QWidget, max_width: int = 290):
        """Add a chip widget, starting a new row if it would overflow max_width."""
        if self._cur_row is None:
            self._new_row()
        # Measure current row width
        used = sum(
            (w.sizeHint().width() + self._h)
            for i in range(self._cur_row.count())
            for w in [self._cur_row.itemAt(i).widget()]
            if w
        )
        chip_w = widget.sizeHint().width() + self._h
        if used + chip_w > max_width and used > 0:
            self._new_row()
        self._cur_row.addWidget(widget)

    def _new_row(self):
        row = QHBoxLayout()
        row.setSpacing(self._h)
        row.setContentsMargins(0, 0, 0, 0)
        self._rows.append(row)
        self._cur_row = row
        self.addLayout(row)

    def finish(self):
        """Call after adding all chips to add trailing stretch to the last row."""
        if self._cur_row:
            self._cur_row.addStretch()

def _book_dir(book: str) -> Path:
    """Return (and create if needed) the folder for a book's notes."""
    safe = re.sub(r'[<>:"/\\|?*]', "_", book).strip()
    d = CATALOGUE_ROOT / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


def _notes_path(book: str) -> Path:
    return _book_dir(book) / "notes.json"


def catalogue_data(book: str) -> list[dict]:
    """Load all notes for *book*. Returns a list sorted by chapter then timestamp."""
    p = _notes_path(book)
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return sorted(data, key=lambda n: (n.get("chapter", 0), n.get("timestamp", "")))
    except Exception:
        return []


def save_note(book: str, chapter: int, html: str, tags: list[str]) -> dict:
    """Append a note and return it."""
    note = {
        "id":        datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "chapter":   chapter,
        "html":      html,
        "tags":      tags,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    notes = catalogue_data(book)
    notes.append(note)
    p = _notes_path(book)
    tmp = str(p) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return note


def delete_note(book: str, note_id: str) -> None:
    """Remove a note by id."""
    notes = [n for n in catalogue_data(book) if n.get("id") != note_id]
    p = _notes_path(book)
    tmp = str(p) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


# ── Small helpers ──────────────────────────────────────────────────────────────
def _lbl(text, color=TEXT, size=12, bold=False):
    w = QLabel(text)
    weight = "bold" if bold else "normal"
    w.setStyleSheet(f"color:{color};font-size:{size}px;font-weight:{weight};"
                    f"font-family:{FONT_UI};background:transparent;")
    return w


def _tag_chip(tag: str, color: str, checked: bool = False) -> QPushButton:
    b = QPushButton(tag)
    b.setCheckable(True)
    b.setChecked(checked)
    b.setFixedHeight(22)
    b.setStyleSheet(f"""
        QPushButton {{
            background:transparent; border:1px solid {color};
            color:{color}; font-size:10px; font-family:{FONT_UI};
            letter-spacing:1px; padding:0 8px; border-radius:11px;
        }}
        QPushButton:checked {{
            background:{color}; color:{BG}; font-weight:bold;
        }}
        QPushButton:hover {{ background:{color}22; }}
    """)
    return b


# ── Note card widget ───────────────────────────────────────────────────────────
class _NoteCard(QFrame):
    deleted = pyqtSignal(str)   # emits note id

    def __init__(self, note: dict, book: str, parent=None):
        super().__init__(parent)
        self._note = note
        self._book = book
        self.setStyleSheet(f"""
            QFrame {{
                background:{BG3}; border:1px solid {BORDER};
                border-radius:6px;
            }}
            QFrame:hover {{ border-color:{ACCENT}44; }}
        """)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # Header row: chapter + timestamp + delete
        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        ch = self._note.get("chapter", 0)
        ts = self._note.get("timestamp", "")[:16].replace("T", "  ")
        hdr.addWidget(_lbl(f"Ch.{ch}", ACCENT, 11, True))
        hdr.addWidget(_lbl(ts, MUTED, 10))
        hdr.addStretch()

        del_btn = QPushButton("✕")
        del_btn.setFixedSize(18, 18)
        del_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; border:none;
                color:{MUTED}; font-size:10px;
            }}
            QPushButton:hover {{ color:{RED}; }}
        """)
        del_btn.clicked.connect(self._confirm_delete)
        hdr.addWidget(del_btn)
        root.addLayout(hdr)

        # Note body (rendered HTML)
        body = QTextEdit()
        body.setReadOnly(True)
        body.setHtml(self._note.get("html", ""))
        body.setStyleSheet(f"""
            QTextEdit {{
                background:transparent; border:none;
                color:{TEXT}; font-family:{FONT_BODY}; font-size:13px;
            }}
        """)
        body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        root.addWidget(body)

        # Height must be set after Qt has laid out the document; defer by one event loop tick
        def _fix_height():
            h = int(body.document().size().height()) + 10
            body.setFixedHeight(max(h, 40))
        from PyQt6.QtCore import QTimer as _QTimer
        _QTimer.singleShot(0, _fix_height)

        # Tags row — flow layout so chips wrap on narrow panels
        tags = self._note.get("tags", [])
        if tags:
            tag_map = dict(TAGS)
            tag_flow = _FlowLayout(h_spacing=4, v_spacing=3)
            for tag in tags:
                color = tag_map.get(tag, MUTED)
                chip = _tag_chip(tag, color, checked=False)
                chip.setEnabled(False)
                tag_flow.add_chip(chip)
            tag_flow.finish()
            root.addLayout(tag_flow)

    def _confirm_delete(self):
        mb = QMessageBox(self)
        mb.setWindowTitle("Delete note?")
        mb.setText("This note will be permanently deleted.")
        mb.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        mb.setStyleSheet(f"background:{BG2}; color:{TEXT};")
        if mb.exec() == QMessageBox.StandardButton.Yes:
            delete_note(self._book, self._note["id"])
            self.deleted.emit(self._note["id"])


# ── Main panel ─────────────────────────────────────────────────────────────────
class CataloguePanel(QFrame):
    """
    Drop-in sidebar panel — mirrors the Sage panel.
    Usage in LegionPage:
        self._catalogue_panel = CataloguePanel(reader_body_widget)
        reader_body.addWidget(self._catalogue_panel)
    Then call:
        self._catalogue_panel.set_context(book_name, chapter_num)
    to update context when chapter changes.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._book    = ""
        self._chapter = 0
        self._show_all = False

        self.setStyleSheet(f"background:{BG2}; border-left:1px solid {BORDER};")
        self.setFixedWidth(320)
        self.setVisible(False)
        self._build()

    # ── Public API ─────────────────────────────────────────────────────────────
    def set_context(self, book: str, chapter: int):
        """Call whenever the reader navigates to a new chapter."""
        changed = (book != self._book or chapter != self._chapter)
        self._book    = book
        self._chapter = chapter
        self._ctx_label.setText(
            f"<span style='color:{ACCENT};font-weight:bold;'>{book}</span>"
            f"<span style='color:{MUTED};'>  ·  Ch.{chapter}</span>"
        )
        if changed:
            self._show_all = False
            self._toggle_all_btn.setText("Show all notes")
            self._refresh_notes()

    def toggle(self):
        self.setVisible(not self.isVisible())

    # ── Build ──────────────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Header
        hdr = QHBoxLayout()
        hdr.addWidget(_lbl("✎  NOTES", ACCENT, 13, True))
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(f"background:transparent;border:none;color:{MUTED};font-size:11px;")
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        root.addLayout(hdr)

        # Context label
        self._ctx_label = QLabel("—")
        self._ctx_label.setWordWrap(True)
        self._ctx_label.setStyleSheet(f"font-size:11px;font-family:{FONT_UI};background:transparent;")
        root.addWidget(self._ctx_label)

        # ── Editor ────────────────────────────────────────────────────────────
        self._editor = QTextEdit()
        self._editor.setPlaceholderText("Write a note for this chapter…")
        self._editor.setFixedHeight(120)
        self._editor.setStyleSheet(f"""
            QTextEdit {{
                background:{BG3}; border:1px solid {BORDER};
                border-radius:4px; color:{TEXT};
                font-family:{FONT_BODY}; font-size:13px;
                padding:6px;
            }}
            QTextEdit:focus {{ border-color:{ACCENT}; }}
        """)
        root.addWidget(self._editor)

        # Formatting toolbar
        fmt_row2 = QHBoxLayout()
        fmt_row2.setSpacing(4)
        for symbol, tip, handler in [
            ("B",  "Bold",       self._fmt_bold),
            ("I",  "Italic",     self._fmt_italic),
            ("U",  "Underline",  self._fmt_underline),
            ("•",  "Bullet",     self._fmt_bullet),
        ]:
            fb2 = QPushButton(symbol)
            fb2.setFixedSize(26, 22)
            fb2.setToolTip(tip)
            if symbol == "B":
                fb2.setStyleSheet(f"QPushButton{{background:{BG3};border:1px solid {BORDER};color:{TEXT};font-weight:bold;font-size:12px;border-radius:3px;}}QPushButton:hover{{border-color:{ACCENT};color:{ACCENT};}}")
            elif symbol == "I":
                fb2.setStyleSheet(f"QPushButton{{background:{BG3};border:1px solid {BORDER};color:{TEXT};font-style:italic;font-size:12px;border-radius:3px;}}QPushButton:hover{{border-color:{ACCENT};color:{ACCENT};}}")
            elif symbol == "U":
                fb2.setStyleSheet(f"QPushButton{{background:{BG3};border:1px solid {BORDER};color:{TEXT};text-decoration:underline;font-size:12px;border-radius:3px;}}QPushButton:hover{{border-color:{ACCENT};color:{ACCENT};}}")
            else:
                fb2.setStyleSheet(f"QPushButton{{background:{BG3};border:1px solid {BORDER};color:{ACCENT};font-size:14px;border-radius:3px;}}QPushButton:hover{{border-color:{ACCENT};background:{PANEL};}}")
            fb2.clicked.connect(handler)
            fmt_row2.addWidget(fb2)
        fmt_row2.addStretch()
        root.addLayout(fmt_row2)

        # Tag row — uses flow layout so chips wrap instead of truncating
        self._tag_btns: dict[str, QPushButton] = {}
        tag_flow = _FlowLayout(h_spacing=4, v_spacing=4)
        tag_flow.setContentsMargins(0, 2, 0, 2)
        for tag, color in TAGS:
            chip = _tag_chip(tag, color)
            self._tag_btns[tag] = chip
            tag_flow.add_chip(chip)
        tag_flow.finish()
        root.addLayout(tag_flow)

        # Save button
        save_btn = QPushButton("✎  Save Note")
        save_btn.setObjectName("accent")
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background:{ACCENT}; color:{BG}; border:none;
                font-weight:bold; font-size:12px; font-family:{FONT_UI};
                letter-spacing:1px; padding:7px; border-radius:4px;
            }}
            QPushButton:hover {{ background:#F0D98A; }}
            QPushButton:pressed {{ background:{PANEL}; color:{ACCENT}; }}
            QPushButton:disabled {{ background:{BG3}; color:{MUTED}; }}
        """)
        save_btn.clicked.connect(self._save_note)
        root.addWidget(save_btn)

        # Divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet(f"color:{BORDER};")
        root.addWidget(div)

        # Toggle all / chapter notes
        self._toggle_all_btn = QPushButton("Show all notes")
        self._toggle_all_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; border:1px solid {BORDER};
                color:{MUTED}; font-size:10px; font-family:{FONT_UI};
                letter-spacing:1px; padding:4px; border-radius:3px;
            }}
            QPushButton:hover {{ border-color:{ACCENT2}; color:{ACCENT2}; }}
        """)
        self._toggle_all_btn.clicked.connect(self._toggle_all)
        root.addWidget(self._toggle_all_btn)

        # Notes scroll area
        self._notes_scroll = QScrollArea()
        self._notes_scroll.setWidgetResizable(True)
        self._notes_scroll.setStyleSheet(f"""
            QScrollArea {{ border:none; background:transparent; }}
            QScrollBar:vertical {{ background:{BG}; width:4px; border:none; }}
            QScrollBar::handle:vertical {{ background:{BORDER}; border-radius:2px; }}
            QScrollBar::handle:vertical:hover {{ background:{ACCENT}; }}
        """)
        self._notes_container = QWidget()
        self._notes_container.setStyleSheet("background:transparent;")
        self._notes_layout = QVBoxLayout(self._notes_container)
        self._notes_layout.setContentsMargins(0, 0, 0, 0)
        self._notes_layout.setSpacing(8)
        self._notes_layout.addStretch()
        self._notes_scroll.setWidget(self._notes_container)
        root.addWidget(self._notes_scroll, 1)

        # Empty state label — created once, shown/hidden by _refresh_notes
        self._empty_lbl = _lbl("No notes for this chapter yet.", MUTED, 11)
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setWordWrap(True)
        self._empty_lbl.hide()
        self._notes_layout.insertWidget(0, self._empty_lbl)

    # ── Formatting actions ─────────────────────────────────────────────────────
    def _fmt_bold(self):
        fmt = QTextCharFormat()
        cur = self._editor.textCursor()
        current_weight = cur.charFormat().fontWeight()
        fmt.setFontWeight(QFont.Weight.Normal if current_weight == QFont.Weight.Bold else QFont.Weight.Bold)
        cur.mergeCharFormat(fmt)

    def _fmt_italic(self):
        fmt = QTextCharFormat()
        cur = self._editor.textCursor()
        fmt.setFontItalic(not cur.charFormat().fontItalic())
        cur.mergeCharFormat(fmt)

    def _fmt_underline(self):
        fmt = QTextCharFormat()
        cur = self._editor.textCursor()
        fmt.setFontUnderline(not cur.charFormat().fontUnderline())
        cur.mergeCharFormat(fmt)

    def _fmt_bullet(self):
        cur = self._editor.textCursor()
        cur.insertText("\n• ")
        self._editor.setTextCursor(cur)

    # ── Save ───────────────────────────────────────────────────────────────────
    def _save_note(self):
        if not self._book:
            return
        html = self._editor.toHtml()
        plain = self._editor.toPlainText().strip()
        if not plain:
            return

        selected_tags = [tag for tag, btn in self._tag_btns.items() if btn.isChecked()]
        save_note(self._book, self._chapter, html, selected_tags)

        # Reset editor + tags
        self._editor.clear()
        for btn in self._tag_btns.values():
            btn.setChecked(False)

        self._refresh_notes()

    # ── Notes list ─────────────────────────────────────────────────────────────
    def _toggle_all(self):
        self._show_all = not self._show_all
        self._toggle_all_btn.setText(
            "Show chapter notes only" if self._show_all else "Show all notes"
        )
        self._refresh_notes()

    def _refresh_notes(self):
        # Remove all cards (stretch is always last; empty label is always index 0 if present)
        # We keep the stretch sentinel at the end and the _empty_lbl instance from _build.
        # Remove everything except the trailing stretch.
        while self._notes_layout.count() > 1:
            item = self._notes_layout.takeAt(0)
            if item and item.widget():
                item.widget().setParent(None)

        all_notes = catalogue_data(self._book)
        notes = all_notes if self._show_all else [
            n for n in all_notes if n.get("chapter") == self._chapter
        ]

        if not notes:
            msg = "No notes yet." if self._show_all else "No notes for this chapter yet."
            self._empty_lbl.setText(msg)
            self._notes_layout.insertWidget(0, self._empty_lbl)
            self._empty_lbl.show()
            return

        self._empty_lbl.hide()
        for i, note in enumerate(reversed(notes)):
            card = _NoteCard(note, self._book)
            card.deleted.connect(lambda _: self._refresh_notes())
            self._notes_layout.insertWidget(i, card)
