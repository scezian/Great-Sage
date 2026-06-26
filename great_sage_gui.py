#!/usr/bin/env python3
"""
great_sage_gui.py — Great Sage
================================
Entry point and shell. Pure native PyQt6 — no HTML pages.
Run:  python3 great_sage_gui.py
"""

import os, sys, time, threading, urllib.request
from pathlib import Path

# Software rendering — avoids Mesa/EGL seg faults on stylesheet parse
os.environ["QT_XCB_GL_INTEGRATION"] = "none"
os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"

# Qt WebEngine ICU data fix — must be set BEFORE any WebEngine imports
# Try to find PyQt6 resources in various locations
def _find_webengine_resources():
    # First: ALWAYS check user site-packages for PyQt6 (handles split installations)
    user_site = Path.home() / ".local/lib"
    for pydir in user_site.glob("python*/site-packages"):
        for subpath in [pydir / "PyQt6/Qt6", pydir / "PyQt6_WebEngine_Qt6/Qt6"]:
            if (subpath / "resources/qtwebengine_resources.pak").exists():
                os.environ["QTWEBENGINE_RESOURCES_PATH"] = str(subpath / "resources")
                locales_path = subpath / "translations/qtwebengine_locales"
                if locales_path.exists():
                    os.environ["QTWEBENGINE_LOCALES_PATH"] = str(locales_path)
                elif (subpath / "translations").exists():
                    os.environ["QTWEBENGINE_LOCALES_PATH"] = str(subpath / "translations")
                return
    # Second: try PyQt6 package location (system or same-env install)
    try:
        import PyQt6
        qt6_pkg = Path(PyQt6.__file__).parent
        for sub in ["Qt6/resources", "resources"]:
            res_path = qt6_pkg / sub
            if (res_path / "qtwebengine_resources.pak").exists():
                os.environ["QTWEBENGINE_RESOURCES_PATH"] = str(res_path)
                if sub == "Qt6/resources":
                    trans_path = qt6_pkg / "Qt6/translations/qtwebengine_locales"
                    if not trans_path.exists():
                        trans_path = qt6_pkg / "Qt6/translations"
                else:
                    trans_path = qt6_pkg / "translations/qtwebengine_locales"
                    if not trans_path.exists():
                        trans_path = qt6_pkg / "translations"
                if trans_path.exists():
                    os.environ["QTWEBENGINE_LOCALES_PATH"] = str(trans_path)
                return
    except Exception:
        pass
    # Third: try PyQt6-WebEngine-Qt6 package
    try:
        import PyQt6_WebEngine_Qt6
        webeng_path = Path(PyQt6_WebEngine_Qt6.__file__).parent
        for sub in ["Qt6/resources", "resources"]:
            res_path = webeng_path / sub
            if (res_path / "qtwebengine_resources.pak").exists():
                os.environ["QTWEBENGINE_RESOURCES_PATH"] = str(res_path)
                if sub == "Qt6/resources":
                    trans_path = webeng_path / "Qt6/translations/qtwebengine_locales"
                    if not trans_path.exists():
                        trans_path = webeng_path / "Qt6/translations"
                else:
                    trans_path = webeng_path / "translations/qtwebengine_locales"
                    if not trans_path.exists():
                        trans_path = webeng_path / "translations"
                if trans_path.exists():
                    os.environ["QTWEBENGINE_LOCALES_PATH"] = str(trans_path)
                return
    except Exception:
        pass
    # System paths fallback
    for prefix in ["/usr/share/qt6", "/usr/share/qt", "/usr/lib/qt6", "/usr/lib/qt", "/usr/lib/x86_64-linux-gnu/qt6"]:
        p = Path(prefix)
        if (p / "resources" / "qtwebengine_resources.pak").exists():
            os.environ.setdefault("QTWEBENGINE_RESOURCES_PATH", str(p / "resources"))
        if (p / "translations" / "qtwebengine_locales").exists():
            os.environ.setdefault("QTWEBENGINE_LOCALES_PATH", str(p / "translations" / "qtwebengine_locales"))
        elif (p / "translations").exists():
            os.environ.setdefault("QTWEBENGINE_LOCALES_PATH", str(p / "translations"))
_find_webengine_resources()
# Suppress GPU/blacklist errors
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox --disable-features=IsolateOrigins,site-per-process")

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
    NotificationBell,
)

# ── Page modules ──────────────────────────────────────────────────────────────
from gs_legion_ui import LegionPage
from gs_matrix_ui import HighlightsDialog, CalendarDialog, WrappedDialog
from gs_matrix_ui import MatrixPage
from gs_sage_ui   import SagePage, SettingsPage
from gs_bugreport_ui import BugReportPage
try:
    from artemis import EditorPage
except ImportError as _artemis_err:
    import warnings as _warnings
    _warnings.warn(f"artemis module not found — using placeholder EditorPage ({_artemis_err})")

    from PyQt6.QtWidgets import QVBoxLayout, QLabel as _QLabel

    class EditorPage(QWidget):           # type: ignore[no-redef]
        """
        Placeholder shown when artemis.py fails to import.
        Implements the same surface as all page objects so MainWindow.
        _navigate() never crashes (refresh, resizeEvent, etc.).
        """
        def __init__(self, parent=None):
            super().__init__(parent)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(40, 40, 40, 40)
            msg = _QLabel(
                "✦ Artemis editor unavailable\n\n"
                f"Reason: {_artemis_err}\n\n"
                "Check that artemis.py is present in the same directory as great_sage_gui.py.",
            )
            msg.setStyleSheet(
                f"color: {MUTED}; font-size: 13px; qproperty-alignment: AlignCenter;"
            )
            msg.setWordWrap(True)
            layout.addStretch()
            layout.addWidget(msg)
            layout.addStretch()

        def refresh(self):
            pass   # nothing to refresh in the placeholder

# ── Core ─────────────────────────────────────────────────────────────────────
from great_sage_core import (
    SCRIPT_DIR, LEGION_PROGRESS, MATRIX_PROGRESS, LEGION_BOOKMARKS, SAGE_MEMORY_PATH,
    save_json, get_legion_data, get_matrix_data, get_bookmarks_data,
    legion_data, matrix_data, bookmarks_data,
    sage_memory_load,
    SageWorker, AutoSyncWorker, start_mobile_server,
    get_notification_store,
)

# ── Qt ────────────────────────────────────────────────────────────────────────
from PyQt6.QtCore    import Qt, QTimer, QSize, QCoreApplication, pyqtSlot
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

        # Bell — first slot, replaces Highlights
        self._notif_bell = NotificationBell()
        qa_row.addWidget(self._notif_bell)

        for text, cb in [
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

            # Refresh notification bell badge
            if hasattr(self, "_notif_bell"):
                self._notif_bell.refresh_badge()

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
        QTimer.singleShot(3000, self._check_for_updates)

    def closeEvent(self, event):
        if hasattr(self, '_watchface') and self._watchface:
            self._watchface.close()
        if hasattr(self, '_memory_palace') and self._memory_palace:
            self._memory_palace.close()
        if hasattr(self, '_auto_sync_worker') and self._auto_sync_worker:
            self._auto_sync_worker.quit()
            self._auto_sync_worker.wait(1000)
        # Wait for play thread to finish its final save before exiting
        matrix_page = self._page_objs.get("matrix") if hasattr(self, '_page_objs') else None
        if matrix_page is not None:
            play_thread = getattr(matrix_page, '_play_thread', None)
            if play_thread is not None and play_thread.is_alive():
                play_thread.join(timeout=4)
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
        bug_btn = QPushButton("⚑ BUG")
        bug_btn.setFixedHeight(30)
        bug_btn.setStyleSheet(
            f"QPushButton{{background:{BG3};border:1px solid {BORDER2};"
            f"color:{MUTED};font-size:8px;letter-spacing:1.5px;"
            f"border-radius:6px;padding:0 10px;margin-right:6px;}}"
            f"QPushButton:hover{{background:{PANEL};border-color:#e05555;color:#e05555;}}"
            f"QPushButton:pressed{{background:{BG};}}")
        bug_btn.clicked.connect(self._open_bug_report)
        tv.addWidget(bug_btn)
        tv.addWidget(menu_btn)
        rv.addWidget(topbar)

        # ── Update banner (hidden by default) ────────────────────────────────
        self._update_banner = QWidget()
        self._update_banner.setVisible(False)
        self._update_banner.setFixedHeight(36)
        self._update_banner.setStyleSheet(
            f"background:#1a1a2e;border-bottom:1px solid {ACCENT};")
        ub_layout = QHBoxLayout(self._update_banner)
        ub_layout.setContentsMargins(20, 0, 12, 0)
        ub_layout.setSpacing(10)
        self._update_banner_lbl = QLabel("✦ A new version of Great Sage is available — run  git pull  to update.")
        self._update_banner_lbl.setStyleSheet(
            f"color:{ACCENT};font-size:11px;letter-spacing:0.5px;background:transparent;")
        ub_dismiss = QPushButton("✕")
        ub_dismiss.setFixedSize(20, 20)
        ub_dismiss.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;color:{MUTED};font-size:11px;}}"
            f"QPushButton:hover{{color:{ACCENT};}}")
        ub_dismiss.clicked.connect(lambda: self._update_banner.setVisible(False))
        ub_layout.addWidget(self._update_banner_lbl, 1)
        ub_layout.addWidget(ub_dismiss)
        rv.addWidget(self._update_banner)

        self._pages     = QStackedWidget()
        self._page_objs: dict[str, QWidget] = {}

        pages = [
            ("dashboard", DashboardPage()),
            ("legion",    LegionPage()),
            ("matrix",    MatrixPage()),
            ("sage",      SagePage()),
            ("editor",    EditorPage()),
            ("settings",  SettingsPage()),
            ("bugreport", BugReportPage()),
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

    def _open_bug_report(self):
        import urllib.parse, subprocess, platform
        from pathlib import Path

        # Collect debug info
        version = "unknown"
        try:
            vf = Path(__file__).parent / "VERSION"
            if vf.exists():
                version = vf.read_text().strip()
        except Exception:
            pass

        os_info = platform.platform()

        # Last 30 lines of the most recent log file
        log_snippet = "No logs found."
        try:
            log_dir = Path(__file__).parent / "logs"
            if log_dir.exists():
                logs = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
                if logs:
                    lines = logs[0].read_text(errors="replace").splitlines()
                    log_snippet = "\n".join(lines[-30:])
        except Exception as e:
            log_snippet = f"Could not read logs: {e}"

        current_module = getattr(self, "_current_page", "unknown")

        body = (
            f"## Bug Report\n\n"
            f"**Great Sage version:** {version}\n"
            f"**OS:** {os_info}\n"
            f"**Module:** {current_module}\n\n"
            f"## What happened?\n"
            f"_Describe what you did and what went wrong._\n\n"
            f"## Expected behaviour\n"
            f"_What did you expect to happen?_\n\n"
            f"## Log snippet (last 30 lines)\n"
            f"```\n{log_snippet}\n```\n"
        )

        url = (
            "https://github.com/scezian/Great-Sage/issues/new"
            f"?title={urllib.parse.quote('[Bug] ')}"
            f"&body={urllib.parse.quote(body)}"
            f"&labels={urllib.parse.quote('bug')}"
        )

        try:
            subprocess.Popen(["xdg-open", url])
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Bug Report", f"Open this URL to report:\n{url}")

    def _check_for_updates(self):
        def _fetch():
            try:
                version_file = Path(SCRIPT_DIR) / "VERSION"
                local_version = version_file.read_text().strip() if version_file.exists() else None

                # ── Fetch remote VERSION ──────────────────────────────────────
                url_ver = "https://raw.githubusercontent.com/scezian/Great-Sage/main/VERSION"
                with urllib.request.urlopen(url_ver, timeout=5) as resp:
                    remote_version = resp.read().decode().strip()

                if not (local_version and remote_version and
                        local_version != remote_version):
                    return   # already up to date

                # ── Deduplicate — don't re-add the same update notification ──
                store = get_notification_store()
                notif_id = f"update-{remote_version}"
                existing = {n["id"] for n in store.all_items()}
                if notif_id in existing:
                    # Still show the banner if it was previously dismissed
                    from PyQt6.QtCore import QMetaObject, Qt as _Qt
                    QMetaObject.invokeMethod(
                        self, "_show_update_banner",
                        _Qt.ConnectionType.QueuedConnection,
                    )
                    return

                # ── Fetch commits between local tag and remote tag ────────────
                commits = []
                try:
                    import json as _json
                    local_tag  = f"v{local_version}"
                    remote_tag = f"v{remote_version}"

                    # Compare API: commits between local tag and remote tag
                    compare_url = (
                        f"https://api.github.com/repos/scezian/Great-Sage"
                        f"/compare/{local_tag}...{remote_tag}"
                    )
                    req = urllib.request.Request(
                        compare_url,
                        headers={"Accept": "application/vnd.github+json",
                                 "User-Agent": "GreatSage-App"},
                    )
                    with urllib.request.urlopen(req, timeout=8) as r:
                        cmp_data = _json.loads(r.read().decode())

                    for c in cmp_data.get("commits", []):
                        msg = c.get("commit", {}).get("message", "").strip()
                        sha = c.get("sha", "")
                        if msg:
                            commits.append({"sha": sha, "message": msg})

                except Exception as _ce:
                    log.warning("Update check: commit compare failed", error=str(_ce))
                    # Fallback — last 8 commits from main
                    try:
                        import json as _json
                        fallback_url = (
                            "https://api.github.com/repos/scezian/Great-Sage"
                            "/commits?per_page=8"
                        )
                        req2 = urllib.request.Request(
                            fallback_url,
                            headers={"Accept": "application/vnd.github+json",
                                     "User-Agent": "GreatSage-App"},
                        )
                        with urllib.request.urlopen(req2, timeout=8) as r2:
                            raw = _json.loads(r2.read().decode())
                        for c in raw:
                            msg = c.get("commit", {}).get("message", "").strip()
                            sha = c.get("sha", "")
                            if msg:
                                commits.append({"sha": sha, "message": msg})
                    except Exception:
                        pass

                # ── Push notification to store ────────────────────────────────
                store.add(
                    notif_type="update",
                    title=f"Great Sage v{remote_version} available",
                    data={"version": remote_version, "commits": commits},
                    notif_id=notif_id,
                )

                # Also show the banner and refresh the bell
                from PyQt6.QtCore import QMetaObject, Qt as _Qt
                QMetaObject.invokeMethod(
                    self, "_show_update_banner",
                    _Qt.ConnectionType.QueuedConnection,
                )
                QMetaObject.invokeMethod(
                    self, "_refresh_notif_bell",
                    _Qt.ConnectionType.QueuedConnection,
                )

            except Exception as e:
                log.warning("Update check failed", error=str(e))

        threading.Thread(target=_fetch, daemon=True).start()

    @pyqtSlot()
    def _refresh_notif_bell(self):
        """Refresh the notification bell badge from the main thread."""
        dash = self._page_objs.get("dashboard")
        if dash and hasattr(dash, "_notif_bell"):
            dash._notif_bell.refresh_badge()

    @pyqtSlot()
    def _show_update_banner(self):
        self._update_banner.setVisible(True)

    def _on_sync_done(self, msg: str):
        legion_page = self._page_objs.get("legion")
        if legion_page and hasattr(legion_page, "_show_toast"):
            legion_page._show_toast(msg)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    # WebEngine requires shared OpenGL contexts
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
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
