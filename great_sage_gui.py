#!/usr/bin/env python3
"""
great_sage_gui.py — Great Sage
================================
Entry point and shell. Pure native PyQt6 — no HTML pages.
Run:  python3 great_sage_gui.py
"""

import os, sys, json, time, re, subprocess, threading
from pathlib import Path

# Software rendering — avoids Mesa/EGL seg faults on stylesheet parse
os.environ["QT_XCB_GL_INTEGRATION"] = "none"
os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"

# ── Logging ───────────────────────────────────────────────────────────────────
try:
    from gs_logger import log
except Exception:
    class _NoopLog:
        def __getattr__(self, name): return _NoopLog()
        def __call__(self, *a, **kw): return None
    log = _NoopLog()

# ── Theme + shared widgets ────────────────────────────────────────────────────
from gs_theme import *  # noqa: F403
from gs_widgets import (
    lbl, btn, hline, vline, tag,
    NavRail, EyeBreakToast, SyncToast,
    WatchfaceWindow, MemoryPalaceWindow,
)

# ── Page modules ──────────────────────────────────────────────────────────────
from gs_legion_ui import LegionPage, HighlightsDialog, CalendarDialog, WrappedDialog
from gs_matrix_ui import MatrixPage
from gs_sage_ui   import SagePage, SettingsPage
try:
    from artemis import EditorPage
except ImportError:
    from PyQt6.QtWidgets import QWidget as EditorPage
    import warnings
    warnings.warn("artemis module not found, using placeholder")

# ── Core ─────────────────────────────────────────────────────────────────────
from great_sage_core import (
    SCRIPT_DIR, LEGION_PROGRESS, MATRIX_PROGRESS, LEGION_BOOKMARKS, SAGE_MEMORY_PATH,
    save_json, get_legion_data, get_matrix_data, get_bookmarks_data,
    legion_data, matrix_data, bookmarks_data,
    sage_memory_load,
    SageWorker, AutoSyncWorker, start_mobile_server,
)

# ── Qt ────────────────────────────────────────────────────────────────────────
from PyQt6.QtCore    import Qt, QTimer, QSize, QCoreApplication
from PyQt6.QtGui     import (QColor, QPalette, QShortcut, QPainter,
                              QLinearGradient, QBrush, QPen, QKeySequence)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QLabel, QPushButton, QScrollArea, QFrame, QMenu,
    QSizePolicy, QGraphicsOpacityEffect,
)


# ── Layered background helper ─────────────────────────────────────────────────

class _LayeredWidget(QWidget):
    """Two-layer container: background behind foreground, both fill the rect."""
    def __init__(self, *, background: QWidget, foreground: QWidget,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._bg = background
        self._fg = foreground
        self._bg.setParent(self)
        self._fg.setParent(self)
        self._bg.lower()

    def resizeEvent(self, e):
        self._bg.setGeometry(0, 0, self.width(), self.height())
        self._fg.setGeometry(0, 0, self.width(), self.height())
        super().resizeEvent(e)


# ── Dashboard ─────────────────────────────────────────────────────────────────

class DashboardPage(QWidget):
    """
    Fully native PyQt6 dashboard.

    Layout:
        _LayeredWidget (root):
            background: SlotHost("dashboard_background") — full screen ambient plugin
            foreground: QVBoxLayout:
                SlotHost("dashboard_top") — visualizer
                scroll area:
                    cards row
                    quick actions
                SlotHost("dashboard_below_cards") — clock widget
    """

    def __init__(self):
        super().__init__()
        self._build()
        self._last_refresh_time = 0
        self._cached_legion_data = None
        self._cached_matrix_data = None

    def _build(self):
        from plugin_manager import SlotHost

        # 1. Background Slot
        self._slot_bg = SlotHost("dashboard_background")

        # 2. Main content container
        self._main_content = QWidget()
        self._main_content.setStyleSheet("background: transparent;")
        root = QVBoxLayout(self._main_content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Slot top (visualizer)
        self._slot_top = SlotHost("dashboard_top")
        root.addWidget(self._slot_top)

        # Scroll area for cards and actions
        self._scroll_content = QWidget()
        self._scroll_content.setStyleSheet("background: transparent;")
        cv = QVBoxLayout(self._scroll_content)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        # Hero section spacer (since text removed)
        cv.addSpacing(32)

        # Cards row
        cards_wrap = QWidget()
        cards_wrap.setStyleSheet("background: transparent;")
        cards_v = QVBoxLayout(cards_wrap)
        cards_v.setContentsMargins(48, 20, 48, 0)
        cards_v.setSpacing(0)
        cards_h = QHBoxLayout()
        cards_h.setSpacing(12)
        
        # Create cards and store in _card_widgets for card_styler plugin
        self._card_legion  = self._make_card("◎", "LEGION ○ READING",  "legion")
        self._card_matrix  = self._make_card("▣", "MATRIX ○ WATCHING", "matrix")
        self._card_sage    = self._make_card("✦", "SAGE ○ AI",          "sage")
        self._card_editor  = self._make_card("✎", "ARTEMIS ○ WRITE",    "editor")
        self._card_plugins = self._make_card("⬡", "PLUGINS",            "plugins")
        
        self._card_widgets = {
            "legion":  self._card_legion,
            "matrix":  self._card_matrix,
            "sage":    self._card_sage,
            "editor":  self._card_editor,
            "plugins": self._card_plugins
        }
        
        for c in self._card_widgets.values():
            cards_h.addWidget(c)
        cards_v.addLayout(cards_h)
        cv.addWidget(cards_wrap)

        # Quick actions
        qa_wrap = QWidget()
        qa_wrap.setStyleSheet("background: transparent;")
        qa_v = QVBoxLayout(qa_wrap)
        qa_v.setContentsMargins(48, 20, 48, 20)
        qa_v.setSpacing(8)
        qa_label = QLabel("QUICK ACTIONS")
        qa_label.setStyleSheet(
            f"color:{MUTED}; font-size:9px; letter-spacing:3px; background:transparent;")
        qa_v.addWidget(qa_label)
        qa_row = QHBoxLayout()
        qa_row.setSpacing(10)
        for text, cb in [
            ("✦  HIGHLIGHTS",          self._show_highlights),
            ("📅  CALENDAR",            self._show_calendar),
            ("📊  WRAPPED — ALL TIME",  lambda: self._show_wrapped("alltime")),
            ("⊙  SETTINGS",            lambda: self._launch("settings")),
        ]:
            b = QPushButton(text)
            b.setStyleSheet(
                f"QPushButton{{background:transparent;border:1px solid {BORDER};"
                f"border-radius:3px;color:{TEXT2};font-size:10px;letter-spacing:1px;"
                f"padding:8px 14px;}}"
                f"QPushButton:hover{{border-color:{ACCENT};color:{ACCENT};}}")
            b.clicked.connect(cb)
            qa_row.addWidget(b)
        qa_row.addStretch()
        qa_v.addLayout(qa_row)
        cv.addWidget(qa_wrap)
        cv.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._scroll_content)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(scroll, 1)

        # Slot below cards (clock widget)
        self._slot_bottom = SlotHost("dashboard_below_cards")
        self._slot_bottom.setMaximumHeight(260)
        root.addWidget(self._slot_bottom)

        # 3. Final Layered root
        self._layout_root = QVBoxLayout(self)
        self._layout_root.setContentsMargins(0, 0, 0, 0)
        self._layered = _LayeredWidget(background=self._slot_bg, foreground=self._main_content)
        self._layout_root.addWidget(self._layered)

        QTimer.singleShot(0, self.refresh)

    def _make_stat_pill(self, value: str, label: str) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 6, 0, 6)
        h.setSpacing(0)
        val_lbl = QLabel(value)
        val_lbl.setStyleSheet(
            f"color:{ACCENT};font-size:13px;font-weight:bold;"
            f"background:transparent;border-left:2px solid {BORDER2};padding-left:16px;")
        unit_lbl = QLabel(f" {label}")
        unit_lbl.setStyleSheet(f"color:{TEXT2};font-size:11px;background:transparent;")
        h.addWidget(val_lbl)
        h.addWidget(unit_lbl)
        h.addSpacing(24)
        w._val_lbl = val_lbl
        return w

    def _make_card(self, icon: str, title: str, nav_key: str) -> QWidget:
        card = QFrame()
        # Initial style; card_styler plugin will override this if enabled
        card.setStyleSheet(
            f"QFrame{{background:{BG2};border:1px solid {BORDER};border-radius:8px;}}"
            f"QFrame:hover{{background:{BG3};border-color:{ACCENT};}}")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(18, 16, 18, 16)
        cv.setSpacing(6)
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet(
            f"font-size:18px;color:{ACCENT};background:transparent;border:none;")
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            f"font-size:9px;letter-spacing:2px;color:{MUTED};"
            f"background:transparent;border:none;")
        val_lbl = QLabel("—")
        val_lbl.setStyleSheet(
            f"font-size:14px;color:{TEXT};font-weight:bold;"
            f"background:transparent;border:none;")
        val_lbl.setWordWrap(True)
        sub_lbl = QLabel("")
        sub_lbl.setStyleSheet(
            f"font-size:10px;color:{TEXT2};background:transparent;border:none;")
        cv.addWidget(icon_lbl)
        cv.addWidget(title_lbl)
        cv.addWidget(val_lbl)
        cv.addWidget(sub_lbl)
        card._val_lbl = val_lbl
        card._sub_lbl = sub_lbl
        card._nav_key = nav_key
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.mousePressEvent = lambda e, k=nav_key: self._launch(k)
        return card

    def _launch(self, key: str):
        mw = self.window()
        if hasattr(mw, "_navigate"):
            mw._navigate(key)

    def _show_highlights(self):
        HighlightsDialog(self.window()).exec()

    def _show_calendar(self):
        CalendarDialog(self.window()).exec()

    def _show_wrapped(self, period: str):
        WrappedDialog(period, self.window()).exec()

    def _open_menu(self):
        menu = QMenu(self)
        menu.addAction("✦  Highlights").triggered.connect(self._show_highlights)
        menu.addSeparator()
        menu.addAction("📅  Calendar — What's Airing").triggered.connect(self._show_calendar)
        menu.addSeparator()
        menu.addAction("📊  Wrapped — All Time").triggered.connect(
            lambda: self._show_wrapped("alltime"))
        menu.addSeparator()
        menu.addAction("⊙  Settings").triggered.connect(lambda: self._launch("settings"))
        menu.exec()

    def refresh(self):
        current_time = time.time()
        if current_time - self._last_refresh_time < 5:
            return
        try:
            self._cached_legion_data = get_legion_data()
            self._cached_matrix_data = get_matrix_data()
            legion_data = self._cached_legion_data
            matrix_data = self._cached_matrix_data
            self._last_refresh_time = current_time
            books       = legion_data.get("books", {})
            watching    = matrix_data.get("watching", {})
            watchlist   = matrix_data.get("watchlist", {})

            # Try to get stat values
            n_books = len(books)
            n_watching = len(watching)
            try:
                from plugin_manager import PluginEngine
                n_enabled = len(PluginEngine().enabled_plugins())
            except Exception:
                n_enabled = 0

            reading = [
                (name, book.get("current_chapter") or 0)
                for name, book in books.items()
                if (book.get("chapters_read") or 0) > 0 or (book.get("current_chapter") or 0) > 0
            ]
            if reading:
                name, ch = reading[-1]
                self._card_legion._val_lbl.setText(name[:30] + ("…" if len(name) > 30 else ""))
                self._card_legion._sub_lbl.setText(f"Ch.{ch}")
            else:
                self._card_legion._val_lbl.setText(f"{len(books)} book(s) in library")
                self._card_legion._sub_lbl.setText("")

            if watching:
                last_key = list(watching.keys())[-1]
                info     = watching[last_key]
                title    = info.get("title", last_key) if isinstance(info, dict) else last_key
                episode  = (info.get("current_episode") or 0) if isinstance(info, dict) else 0
                self._card_matrix._val_lbl.setText(title[:30] + ("…" if len(title) > 30 else ""))
                self._card_matrix._sub_lbl.setText(f"Ep.{episode}" if episode else "")
            else:
                n_plan = len((watchlist.get("planning") or []) if isinstance(watchlist, dict) else [])
                self._card_matrix._val_lbl.setText(f"{n_plan} show(s) on watchlist")
                self._card_matrix._sub_lbl.setText("")

            self._card_sage._val_lbl.setText("Ready")
            self._card_sage._sub_lbl.setText("Chat ● Analyse ● Discover")
            
            # Editor stats
            from pathlib import Path
            docs_dir = Path.home() / "Documents" / "artemis"
            if docs_dir.exists():
                art_files = list(docs_dir.glob("*.art"))
                self._card_editor._val_lbl.setText(f"{len(art_files)} document{'s' if len(art_files) != 1 else ''}")
                if art_files:
                    latest = max(art_files, key=lambda p: p.stat().st_mtime)
                    self._card_editor._sub_lbl.setText(f"Latest: {latest.stem[:20]}{'…' if len(latest.stem) > 20 else ''}")
                else:
                    self._card_editor._sub_lbl.setText("Click to write")
            else:
                self._card_editor._val_lbl.setText("Ready")
                self._card_editor._sub_lbl.setText("Click to write")
            
            self._card_plugins._val_lbl.setText(
                f"{n_enabled} plugin{'s' if n_enabled != 1 else ''} enabled"
                if n_enabled else "No plugins installed yet")
            self._card_plugins._sub_lbl.setText("")

        except Exception as e:
            log.warning("DashboardPage.refresh error", error=str(e))


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Great Sage")
        self.setMinimumSize(1080, 700)
        self.resize(1380, 860)
        self._build()
        self._navigate("dashboard")
        t = QTimer(self)
        t.timeout.connect(self._page_objs["dashboard"].refresh)
        t.start(30_000)
        threading.Thread(target=start_mobile_server, daemon=True).start()
        self._auto_sync_worker = None
        QTimer.singleShot(500,  self._reset_stale_downloads)
        QTimer.singleShot(4000, self._run_auto_sync)
        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._run_auto_sync)
        sync_hours = 6
        try:
            settings = get_matrix_data().get("settings", {})
            sync_hours = settings.get("sync_interval_hours", 6)
        except:
            pass
        self._sync_timer.start(sync_hours * 60 * 60 * 1000)
        QTimer.singleShot(800, self._activate_plugins)

    def closeEvent(self, event):
        if hasattr(self, '_watchface') and self._watchface:
            self._watchface.close()
        if hasattr(self, '_memory_palace') and self._memory_palace:
            self._memory_palace.close()
        if hasattr(self, '_auto_sync_worker') and self._auto_sync_worker:
            self._auto_sync_worker.quit()
            self._auto_sync_worker.wait(1000)
        event.accept()

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._nav_rail = NavRail()
        self._nav_rail.navigate.connect(self._navigate)
        self._nav_rail.setVisible(False)
        root.addWidget(self._nav_rail)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        import types as _types
        topbar = QWidget()
        self._topbar = topbar
        topbar.setFixedHeight(0)
        topbar.setVisible(False)
        def paint_topbar(self, event):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            gradient = QLinearGradient(0, 0, self.width(), 0)
            gradient.setColorAt(0.0, QColor(BG))
            gradient.setColorAt(0.5, QColor(BG2))
            gradient.setColorAt(1.0, QColor(BG))
            painter.fillRect(self.rect(), gradient)
            pen = QPen(QColor(BORDER))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawLine(0, self.height()-1, self.width(), self.height()-1)
        topbar.paintEvent = _types.MethodType(paint_topbar, topbar)
        tv = QHBoxLayout(topbar)
        tv.setContentsMargins(20, 0, 16, 0)
        tv.setSpacing(0)
        self._page_title_lbl = QLabel("GREAT SAGE")
        self._page_title_lbl.setStyleSheet(
            f"color:{TEXT2};font-size:10px;letter-spacing:3px;background:transparent;")
        tv.addWidget(self._page_title_lbl)
        tv.addStretch()
        menu_btn = QPushButton("⋯")
        menu_btn.setFixedSize(36, 30)
        menu_btn.setStyleSheet(
            f"QPushButton{{background:{BG3};border:1px solid {BORDER2};"
            f"color:{TEXT2};font-size:16px;border-radius:6px;}}"
            f"QPushButton:hover{{background:{PANEL};border-color:{ACCENT};color:{ACCENT};}}"
            f"QPushButton:pressed{{background:{BG};}}")
        menu_btn.clicked.connect(self._open_menu)
        tv.addWidget(menu_btn)
        rv.addWidget(topbar)

        self._pages     = QStackedWidget()
        self._page_objs: dict[str, QWidget] = {}

        pages = [
            ("dashboard", DashboardPage()),
            ("legion",    LegionPage()),
            ("matrix",    MatrixPage()),
            ("sage",      SagePage()),
            ("editor",    EditorPage()),
            ("settings",  SettingsPage()),
        ]
        for key, page in pages:
            self._pages.addWidget(page)
            self._page_objs[key] = page

        try:
            from plugin_manager import create_plugins_page as _cpp
            self._plugin_engine, _plugins_page = _cpp(status_cb=None)
            self._pages.addWidget(_plugins_page)
            self._page_objs["plugins"] = _plugins_page
            _plugins_page.navigate_home.connect(lambda: self._navigate("dashboard"))
        except Exception as e:
            log.warning("Plugin system unavailable", error=str(e))
            _ph = QWidget()
            _ph.setStyleSheet(f"background:{BG};")
            _phv = QVBoxLayout(_ph)
            _phv.addWidget(lbl("Plugin system unavailable", MUTED, 12))
            _ph.refresh = lambda: None
            self._pages.addWidget(_ph)
            self._page_objs["plugins"] = _ph

        rv.addWidget(self._pages, 1)
        root.addWidget(right, 1)

        for i, key in enumerate(
                ("dashboard", "legion", "matrix", "sage", "editor", "plugins"), 1):
            QShortcut(QKeySequence(f"Ctrl+{i}"), self).activated.connect(
                lambda k=key: self._navigate(k))
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self._refresh_current)

        sc_w = QShortcut(QKeySequence("Ctrl+W"), self)
        sc_w.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc_w.activated.connect(self._open_watchface)
        self._watchface = None

        sc_m = QShortcut(QKeySequence("Ctrl+M"), self)
        sc_m.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc_m.activated.connect(self._open_memory_palace)
        self._memory_palace = None

    def _navigate(self, key: str):
        log.ui.debug("Navigate", page=key)
        if key not in self._page_objs:
            return
        idx = list(self._page_objs.keys()).index(key)
        self._pages.setCurrentIndex(idx)
        self._page_objs[key].refresh()
        on_dash = key == "dashboard"
        self._nav_rail.set_active(key)
        self._nav_rail.setVisible(not on_dash)
        self._topbar.setVisible(False)
        self._page_title_lbl.setText(key.upper())

    def _refresh_current(self):
        self._pages.currentWidget().refresh()

    def _open_menu(self):
        dash = self._page_objs.get("dashboard")
        if dash and hasattr(dash, "_open_menu"):
            dash._open_menu()

    def _open_watchface(self):
        if hasattr(self, '_watchface') and self._watchface and self._watchface.isVisible():
            self._watchface.close()
            self._watchface.deleteLater()
            self._watchface = None
            return
        if hasattr(self, '_watchface') and self._watchface:
            self._watchface.deleteLater()
        self._watchface = WatchfaceWindow()
        self._watchface.showFullScreen()

    def _open_memory_palace(self):
        if hasattr(self, '_memory_palace') and self._memory_palace and self._memory_palace.isVisible():
            self._memory_palace.close()
            self._memory_palace.deleteLater()
            self._memory_palace = None
            return
        if hasattr(self, '_memory_palace') and self._memory_palace:
            self._memory_palace.deleteLater()
        self._memory_palace = MemoryPalaceWindow()
        self._memory_palace.show()

    def _activate_plugins(self):
        plugins_page = self._page_objs.get("plugins")
        if plugins_page is None:
            return
        try:
            engine = getattr(self, "_plugin_engine", None)
            if engine is None:
                return
            for rec in engine.enabled_plugins():
                page = None
                try:
                    if rec.filename not in plugins_page._plugin_page_indices:
                        api  = plugins_page._api(rec)
                        page = rec.build_page(plugins_page, api)
                        if page is not None:
                            idx = plugins_page._page_stack.count()
                            plugins_page._page_stack.addWidget(page)
                            plugins_page._plugin_page_indices[rec.filename] = idx
                            log.info("Plugin auto-activated", plugin=rec.name)
                except Exception as e:
                    log.warning("Plugin auto-activation failed",
                                plugin=rec.name, error=str(e))
                finally:
                    if page is None:
                        log.error("Plugin activation failed completely", plugin=rec.name)
                        continue
        except Exception as e:
            log.warning("_activate_plugins failed", error=str(e))
        QTimer.singleShot(200, self._post_activate_refresh)

    def _post_activate_refresh(self):
        try:
            from plugin_manager import SlotRegistry
            for slot in ("dashboard_top", "dashboard_background", "dashboard_below_cards"):
                SlotRegistry.instance()._notify(slot)
            dash = self._page_objs.get("dashboard")
            if dash:
                dash.refresh()
        except Exception as e:
            log.warning("_post_activate_refresh failed", error=str(e))

    def _reset_stale_downloads(self):
        legion_data = get_legion_data()
        changed = False
        for name, book in legion_data.get("books", {}).items():
            status = book.get("download_state", {}).get("status", "idle")
            if status in ("downloading", "queued"):
                book["download_state"]["status"]          = "idle"
                book["download_state"]["pause_requested"] = False
                changed = True
        if changed:
            save_json(LEGION_PROGRESS, legion_data)

    def _run_auto_sync(self):
        if hasattr(self, '_auto_sync_worker') and self._auto_sync_worker and self._auto_sync_worker.isRunning():
            return
        self._auto_sync_worker = AutoSyncWorker()
        self._auto_sync_worker.status_update.connect(lambda msg: None)
        self._auto_sync_worker.sync_done.connect(self._on_sync_done)
        self._auto_sync_worker.sync_clear.connect(lambda: None)
        self._auto_sync_worker.start()

    def _on_sync_done(self, msg: str):
        legion_page = self._page_objs.get("legion")
        if legion_page and hasattr(legion_page, "_show_toast"):
            legion_page._show_toast(msg)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Great Sage")
    app.setStyleSheet(QSS)
    pal = QPalette()
    for role, col in [
        (QPalette.ColorRole.Window,          BG),
        (QPalette.ColorRole.WindowText,      TEXT),
        (QPalette.ColorRole.Base,            BG3),
        (QPalette.ColorRole.AlternateBase,   BG2),
        (QPalette.ColorRole.Text,            TEXT),
        (QPalette.ColorRole.Button,          BG3),
        (QPalette.ColorRole.ButtonText,      TEXT),
        (QPalette.ColorRole.Highlight,       ACCENT),
        (QPalette.ColorRole.HighlightedText, BG),
    ]:
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    log.info("MainWindow creating")
    win = MainWindow()
    win.show()
    log.info("App event loop starting")
    code = app.exec()
    log.info("App exiting", exit_code=code)
    sys.exit(code)


if __name__ == "__main__":
    main()
