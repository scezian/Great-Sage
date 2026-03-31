"""
clock_widget.py — Great Sage Plugin
Live clock and date on the dashboard, with a quote pulled from your current book.
Slot: dashboard_below_cards
"""

PLUGIN_NAME        = "Clock"
PLUGIN_ICON        = "◷"
PLUGIN_DESCRIPTION = "Live clock, date, and a line from your current book on the dashboard"
PLUGIN_VERSION     = "1.0"
PLUGIN_AUTHOR      = "Great Sage"
PLUGIN_COLOR       = "#5DDFCC"

import datetime, os, re, random
from PyQt6.QtCore    import Qt, QTimer
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QSizePolicy)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _pick_quote(api) -> str:
    """Pull a random sentence from the current book's downloaded .txt file."""
    try:
        import json
        path = os.path.expanduser("~/.great_sage_legion.json")
        if not os.path.exists(path):
            return ""
        with open(path) as f:
            data = json.load(f)
        books = data.get("books", {})
        if not books:
            return ""
        # Find the most recently read book
        candidates = [(n, b) for n, b in books.items()
                      if b.get("current_chapter", 0) > 0]
        if not candidates:
            candidates = list(books.items())
        name, book = candidates[-1]

        # Find the .txt file
        script_dir = os.path.dirname(os.path.abspath(__file__))
        fname = re.sub(r'[^\w\-_\. ]', '_', name) + ".txt"
        txt_path = os.path.join(script_dir, fname)
        if not os.path.exists(txt_path):
            return ""

        with open(txt_path, encoding="utf-8", errors="replace") as f:
            content = f.read()

        # Split into sentences, pick a nice one (50–160 chars, no chapter headers)
        sentences = re.split(r'(?<=[.!?])\s+', content)
        good = [s.strip() for s in sentences
                if 50 < len(s.strip()) < 160
                and not re.match(r'[A-Z\s\d=]{8,}', s)
                and '"' not in s[:3]]
        if good:
            return random.choice(good[:300])  # pick from first 300 good sentences
    except Exception:
        pass
    return ""


# ── Dashboard widget ───────────────────────────────────────────────────────────

class ClockWidget(QWidget):
    def __init__(self, api, parent=None):
        super().__init__(parent)
        self._api   = api
        self._quote = ""
        self._build()
        self._tick()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        # Refresh quote every 10 minutes
        self._q_timer = QTimer(self)
        self._q_timer.timeout.connect(self._refresh_quote)
        self._q_timer.start(600_000)
        QTimer.singleShot(800, self._refresh_quote)

    def _build(self):
        self.setStyleSheet("background:transparent;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        root = QHBoxLayout(self)
        root.setContentsMargins(48, 20, 48, 16)
        root.setSpacing(0)

        # Left: time + date
        left = QVBoxLayout()
        left.setSpacing(2)

        self._time_lbl = QLabel()
        self._time_lbl.setStyleSheet(
            "background:transparent; color:#E8E4DC; "
            "font-size:42px; font-weight:bold; letter-spacing:2px;")

        self._date_lbl = QLabel()
        self._date_lbl.setStyleSheet(
            "background:transparent; color:#606070; "
            "font-size:12px; letter-spacing:3px;")

        left.addWidget(self._time_lbl)
        left.addWidget(self._date_lbl)
        root.addLayout(left)
        root.addStretch(1)

        # Right: quote
        right = QVBoxLayout()
        right.setSpacing(4)
        right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._quote_lbl = QLabel()
        self._quote_lbl.setStyleSheet(
            "background:transparent; color:#454565; "
            "font-size:12px; font-style:italic; font-family:Palatino Linotype,Georgia,serif;")
        self._quote_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._quote_lbl.setWordWrap(True)
        self._quote_lbl.setMaximumWidth(480)

        self._src_lbl = QLabel()
        self._src_lbl.setStyleSheet(
            "background:transparent; color:#353550; "
            "font-size:10px; letter-spacing:1px;")
        self._src_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

        right.addWidget(self._quote_lbl)
        right.addWidget(self._src_lbl)
        root.addLayout(right)

    def _tick(self):
        now = datetime.datetime.now()
        self._time_lbl.setText(now.strftime("%H:%M:%S"))
        self._date_lbl.setText(now.strftime("%A, %d %B %Y").upper())

    def _refresh_quote(self):
        import threading
        def _fetch():
            q = _pick_quote(self._api)
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._set_quote(q))
        threading.Thread(target=_fetch, daemon=True).start()

    def _set_quote(self, q):
        if q:
            self._quote_lbl.setText(f'"{q}"')
            # Find book name for attribution
            try:
                import json
                with open(os.path.expanduser("~/.great_sage_legion.json")) as f:
                    d = json.load(f)
                books = d.get("books", {})
                candidates = [(n, b) for n, b in books.items()
                              if b.get("current_chapter", 0) > 0]
                if candidates:
                    name = candidates[-1][0]
                    self._src_lbl.setText(f"— {name[:40]}")
            except Exception:
                pass
        else:
            self._quote_lbl.setText("")
            self._src_lbl.setText("")


# ── Plugin entry points ────────────────────────────────────────────────────────

_widget = None

def build_page(parent, api):
    global _widget
    colours = api.colours

    page = QWidget()
    page.setStyleSheet(f"background:{colours['BG']};")
    v = QVBoxLayout(page)
    v.setContentsMargins(32, 28, 32, 28)
    v.setSpacing(16)

    title = QLabel("◷  CLOCK")
    title.setStyleSheet(f"color:{colours['ACCENT']};font-size:14px;"
                        f"font-weight:bold;letter-spacing:3px;")
    v.addWidget(title)

    desc = QLabel(
        "Displays a live clock and date on the dashboard.\n"
        "A random line from your current book appears as a quote on the right.")
    desc.setStyleSheet(f"color:{colours['TEXT2']};font-size:13px;")
    desc.setWordWrap(True)
    v.addWidget(desc)

    # Register dashboard slot (create once; reuse as preview so only one timer runs)
    if _widget is None:
        _widget = ClockWidget(api)
    api.register_slot("dashboard_below_cards", _widget)

    preview = _widget
    preview.setStyleSheet(
        f"background:{colours['BG2']};border:1px solid {colours['BORDER']};"
        f"border-radius:6px;")
    v.addWidget(preview)
    v.addStretch()

    return page


def refresh(page):
    pass
