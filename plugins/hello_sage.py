"""
hello_sage.py — Example Great Sage Plugin
==========================================
This is a minimal example showing how to build a plugin.
Drop any .py file into the plugins folder to add new features.
"""

PLUGIN_NAME        = "Hello Sage"
PLUGIN_ICON        = "👋"
PLUGIN_DESCRIPTION = "Example plugin — shows your reading & watching stats"
PLUGIN_VERSION     = "1.0"
PLUGIN_AUTHOR      = "Great Sage"
PLUGIN_COLOR       = "#4FC4A0"

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame
from PyQt6.QtCore    import Qt


def build_page(parent, api):
    """Build and return the plugin's page widget."""
    colours = api.colours

    w = QWidget(parent)
    w.setStyleSheet(f"background:{colours['BG']};")
    v = QVBoxLayout(w); v.setContentsMargins(40, 32, 40, 32); v.setSpacing(20)

    title = api.make_label("👋  HELLO SAGE", colours["ACCENT"], 18, bold=True)
    v.addWidget(title)

    sub = api.make_label(
        "This is an example plugin. Replace this file to build your own feature.",
        colours["TEXT2"], 13)
    sub.setWordWrap(True)
    v.addWidget(sub)

    # Show some live data from the app
    ld = api.legion_data()
    md = api.matrix_data()

    books    = ld.get("books", {})
    watching = md.get("watching", {})

    stats_frame = QFrame()
    stats_frame.setStyleSheet(
        f"background:{colours['BG2']};border:1px solid {colours['BORDER']};"
        f"border-radius:8px;padding:16px;")
    sf = QVBoxLayout(stats_frame); sf.setSpacing(10)

    sf.addWidget(api.make_label("YOUR STATS", colours["MUTED"], 9, bold=True))

    total_words = sum(b.get("words_read", 0) for b in books.values())
    total_ch    = sum(b.get("chapters_read", 0) for b in books.values())
    sf.addWidget(api.make_label(
        f"📚  {len(books)} book(s) in library  ·  {total_ch} chapters read  ·  {total_words:,} words",
        colours["TEXT"], 13))
    sf.addWidget(api.make_label(
        f"📺  {len(watching)} show(s) in continue watching",
        colours["TEXT"], 13))

    v.addWidget(stats_frame)
    v.addStretch()

    hint = api.make_label(
        f"Plugin file: plugins/hello_sage.py  —  edit it to build your own feature.",
        colours["MUTED"], 10)
    hint.setWordWrap(True)
    v.addWidget(hint)

    return w


def refresh(page):
    """Called when user navigates to this plugin — rebuild live data if needed."""
    pass  # For a stateless display like this, nothing to do
