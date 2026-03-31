#!/usr/bin/env python3
"""
great_sage_gui.py — Great Sage
================================
Entry point and shell. Imports all page modules and wires MainWindow.
Run:  python3 great_sage_gui.py
"""

import sys, threading, time
from pathlib import Path

# ── Logging ───────────────────────────────────────────────────────────────────
try:
    from gs_logger import log
except Exception as _log_err:
    class _NoopLog:
        def __getattr__(self, name): return _NoopLog()
        def __call__(self, *a, **kw): return None
    log = _NoopLog()

# ── Theme + shared widgets ────────────────────────────────────────────────────
from gs_theme import *  # noqa: F403  — includes paint_topbar
from gs_widgets import (
    lbl, btn, hline, vline, tag,
    NavRail, EyeBreakToast, SyncToast,
    WatchfaceWindow, MemoryPalaceWindow,
)

# ── Page modules ──────────────────────────────────────────────────────────────
from gs_legion_ui import LegionPage
from gs_matrix_ui import MatrixPage
from gs_sage_ui   import SagePage, SettingsPage

# ── Core ─────────────────────────────────────────────────────────────────────
from great_sage_core import (
    LEGION_PROGRESS, save_json, legion_data, matrix_data,
    AutoSyncWorker, start_mobile_server,
)

# ── Qt ────────────────────────────────────────────────────────────────────────
from PyQt6.QtCore  import Qt, QTimer
from PyQt6.QtGui   import QColor, QPalette, QShortcut, QPainter, QLinearGradient, QBrush, QPen, QKeySequence
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QLabel, QPushButton, QScrollArea, QFrame, QMenu,
)

class DashboardPage(QWidget):
    def __init__(self):
        super().__init__()
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Plugin slot: dashboard_top ─────────────────────────────────────────
        try:
            from plugin_manager import SlotHost as _SH
            self._slot_top = _SH("dashboard_top")
        except Exception:
            self._slot_top = QWidget()
            self._slot_top.setVisible(False)
        root.addWidget(self._slot_top)

        # ── Content area ────────────────────────────────────────────────────────
        content_area = QWidget()
        content_area.setStyleSheet(f"background:{BG};")
        ca_layout = QVBoxLayout(content_area)
        ca_layout.setContentsMargins(0, 0, 0, 0)
        ca_layout.setSpacing(0)

        # Background slot — sits behind scroll, filled via resizeEvent
        try:
            from plugin_manager import SlotHost as _SH
            self._slot_bg = _SH("dashboard_background")
            self._slot_bg.setParent(content_area)
            self._slot_bg.setGeometry(0, 0, content_area.width(), content_area.height())
            self._slot_bg.lower()
            self._content_area = content_area  # save ref for resizeEvent
        except Exception:
            self._slot_bg = None
            self._content_area = content_area

        # ── Scrollable body ────────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        body = QWidget()
        body.setStyleSheet("background:transparent;")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(48, 40, 48, 40)
        bv.setSpacing(0)
        scroll.setWidget(body)
        ca_layout.addWidget(scroll, 1)
        root.addWidget(content_area, 1)

        # ── Section heading ────────────────────────────────────────────────────
        heading = QLabel("WHERE TO?")
        heading.setStyleSheet(
            f"color:{MUTED}; font-size:11px; letter-spacing:3px; font-family:{FONT_UI};")
        bv.addWidget(heading)
        bv.addSpacing(16)

        # ── Cards grid ────────────────────────────────────────────────────────
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        cards_row.setContentsMargins(0, 0, 0, 0)

        card_data = [
            ("legion",  "◎", "Legion",   "Novel Reader",   ACCENT,  "Read & download web novels"),
            ("matrix",  "▣", "Matrix",   "Media Manager",  BLUE,    "Track shows, anime & movies"),
            ("sage",    "✦", "Sage",     "AI Companion",   PURPLE,  "Recommendations & analysis"),
            ("plugins", "⬡", "Plugins",  "Extensions",     ACCENT2, "Add features via plugins"),
        ]

        self._launch_cards = {}
        self._card_widgets  = {}   # key → card QWidget (used by card styler plugin)

        for key, icon, name, tagline, color, desc in card_data:
            card = QWidget()
            card.setStyleSheet(f"""
                QWidget {{
                    background: {BG2};
                    border: 1px solid {BORDER};
                    border-radius: 4px;
                }}
                QWidget:hover {{
                    border-color: {color};
                    background: {BG3};
                }}
            """)
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.mousePressEvent = lambda e, k=key: self._launch(k)

            cv = QVBoxLayout(card)
            cv.setContentsMargins(22, 20, 22, 20)
            cv.setSpacing(0)

            # Top row: icon + accent bar
            top_row = QHBoxLayout()
            top_row.setSpacing(0)
            ico_lbl = QLabel(icon)
            ico_lbl.setStyleSheet(f"color:{color}; font-size:18px; background:transparent;")
            accent_bar = QFrame()
            accent_bar.setFixedHeight(2)
            accent_bar.setStyleSheet(f"background:{color}; border:none;")
            top_row.addWidget(ico_lbl)
            top_row.addStretch()
            cv.addLayout(top_row)
            cv.addSpacing(2)
            cv.addWidget(accent_bar)
            cv.addSpacing(16)

            name_lbl = QLabel(name.upper())
            name_lbl.setStyleSheet(
                f"color:{TEXT}; font-size:18px; font-weight:bold;"
                f"font-family:{FONT_DISPLAY}; letter-spacing:2px; background:transparent;")
            cv.addWidget(name_lbl)
            cv.addSpacing(4)

            tag_lbl = QLabel(tagline.upper())
            tag_lbl.setStyleSheet(
                f"color:{MUTED}; font-size:10px; letter-spacing:1.5px; background:transparent;")
            cv.addWidget(tag_lbl)
            cv.addSpacing(12)

            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet(
                f"color:{TEXT2}; font-size:13px; background:transparent;")
            desc_lbl.setWordWrap(True)
            cv.addWidget(desc_lbl)
            cv.addSpacing(12)

            status_lbl = QLabel("")
            status_lbl.setStyleSheet(
                f"color:{color}; font-size:11px; background:transparent;")
            status_lbl.setWordWrap(True)
            cv.addWidget(status_lbl)

            cv.addSpacing(16)

            open_btn = QPushButton(f"Open {name}  →")
            open_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: 1px solid {BORDER};
                    color: {MUTED};
                    font-size: 9px;
                    letter-spacing: 1.5px;
                    padding: 7px 14px;
                    border-radius: 2px;
                    text-align: left;
                }}
                QPushButton:hover {{
                    border-color: {color};
                    color: {color};
                }}
            """)
            open_btn.clicked.connect(lambda _, k=key: self._launch(k))
            cv.addWidget(open_btn)

            cards_row.addWidget(card, 1)
            self._launch_cards[key] = status_lbl
            self._card_widgets[key] = card

        bv.addLayout(cards_row)
        bv.addSpacing(32)

        # ── Plugin slot: dashboard_below_cards ─────────────────────────────────
        try:
            from plugin_manager import SlotHost as _SH
            self._slot_bottom = _SH("dashboard_below_cards")
            self._slot_bottom.setMaximumHeight(260)  # cap so lyrics/widgets don't take over
        except Exception:
            self._slot_bottom = QWidget()
            self._slot_bottom.setVisible(False)
        bv.addWidget(self._slot_bottom)
        bv.addStretch()

        # ── Menu button (top right of topbar) ─────────────────────────────────
        self._menu_btn = None   # referenced by _open_menu below; set in topbar

    def _check_network(self): pass  # handled by NavRail now

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Keep background slot filling the content area
        if hasattr(self, "_slot_bg") and self._slot_bg:
            parent = self._slot_bg.parent()
            if parent:
                self._slot_bg.setGeometry(0, 0, parent.width(), parent.height())
                self._slot_bg.lower()
                self._slot_bg._resize_children()

    def _launch(self, key):
        mw = self.window()
        if hasattr(mw, "_navigate"):
            mw._navigate(key)

    def _open_menu(self):
        menu = QMenu(self)
        menu.addAction("✦  Highlights").triggered.connect(self._show_highlights)
        menu.addSeparator()
        menu.addAction("📅  Calendar — What's Airing").triggered.connect(self._show_calendar)
        menu.addSeparator()
        menu.addAction("🏆  Wrapped — This Year").triggered.connect(lambda: self._show_wrapped("year"))
        menu.addAction("📊  Wrapped — All Time").triggered.connect(lambda: self._show_wrapped("alltime"))
        menu.addSeparator()
        menu.addAction("⊙  Settings").triggered.connect(lambda: self._launch("settings"))
        # Prefer rail menu button; fall back to old topbar button or cursor pos
        rail = self._nav_rail
        if hasattr(rail, "_rail_menu_btn") and rail._rail_menu_btn:
            btn = rail._rail_menu_btn
            menu.exec(btn.mapToGlobal(btn.rect().bottomRight()))
        elif self._menu_btn:
            menu.exec(self._menu_btn.mapToGlobal(self._menu_btn.rect().bottomLeft()))
        else:
            menu.exec()

    def _show_highlights(self):
        dlg = HighlightsDialog(self); dlg.exec()

    def _show_calendar(self):
        dlg = CalendarDialog(self); dlg.exec()

    def _show_wrapped(self, period):
        dlg = WrappedDialog(period, self); dlg.exec()

    def refresh(self):
        ld = legion_data()
        md = matrix_data()
        books    = ld.get("books", {})
        watching = md.get("watching", {})
        wl       = md.get("watchlist", {})

        reading = [(n, b.get("current_chapter", 0))
                   for n, b in books.items()
                   if b.get("chapters_read", 0) > 0 or b.get("current_chapter", 0) > 0]

        # Card status labels
        if reading:
            n, ch = reading[-1]
            self._launch_cards["legion"].setText(
                f"{n[:28]}{'…' if len(n)>28 else ''}  ·  Ch.{ch}")
        else:
            self._launch_cards["legion"].setText(f"{len(books)} book(s) in library")

        if watching:
            sk   = list(watching.keys())[-1]
            info = watching[sk]
            t    = info.get("title", sk) if isinstance(info, dict) else sk
            ep   = info.get("current_episode", 0) if isinstance(info, dict) else 0
            self._launch_cards["matrix"].setText(
                f"{t[:28]}{'…' if len(t)>28 else ''}  ·  Ep.{ep}")
        else:
            n_plan = sum(len(wl.get(k, [])) for k in ("planning",))
            self._launch_cards["matrix"].setText(f"{n_plan} show(s) on watchlist")

        self._launch_cards["sage"].setText("Ask for recommendations or analysis")

        try:
            from plugin_manager import PluginEngine as _PE
            n = len(_PE().enabled_plugins())
            self._launch_cards["plugins"].setText(
                f"{n} plugin{'s' if n != 1 else ''} enabled" if n else "No plugins installed yet")
        except Exception:
            self._launch_cards["plugins"].setText("Drop .py files in plugins/ folder")


# ── Remaining pages, dialogs, overlays kept intact below ──────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Great Sage")
        self.setMinimumSize(1080, 700)
        self.resize(1380, 860)
        self._build()
        self._navigate("dashboard")
        # Refresh dashboard every 30s
        t = QTimer(self)
        t.timeout.connect(self._page_objs["dashboard"].refresh)
        t.start(30000)
        # Mobile companion
        threading.Thread(target=start_mobile_server, daemon=True).start()
        # Auto-sync
        self._auto_sync_worker = None
        QTimer.singleShot(500,  self._reset_stale_downloads)
        QTimer.singleShot(4000, self._run_auto_sync)
        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._run_auto_sync)
        self._sync_timer.start(6 * 60 * 60 * 1000)
        # Auto-activate all enabled plugins on startup so slot registrations
        # (e.g. dashboard_top visualizer) happen without the user needing to
        # manually open each plugin first.
        QTimer.singleShot(800, self._activate_plugins)

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Nav rail ──────────────────────────────────────────────────────────
        self._nav_rail = NavRail()
        self._nav_rail.navigate.connect(self._navigate)
        self._nav_rail.setVisible(False)  # hidden on dashboard; shown when a module is active
        root.addWidget(self._nav_rail)

        # ── Page stack + topbar ───────────────────────────────────────────────
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        # Topbar
        topbar = QWidget()
        self._topbar = topbar
        topbar.setFixedHeight(0)
        topbar.setVisible(False)

        import types as _types
        topbar.paintEvent = _types.MethodType(paint_topbar, topbar)

        tv = QHBoxLayout(topbar)
        tv.setContentsMargins(20, 0, 16, 0)
        tv.setSpacing(0)

        app_ico = QLabel("◈")
        app_ico.setStyleSheet(f"color:{ACCENT}; font-size:14px; background:transparent;")
        tv.addWidget(app_ico)
        tv.addSpacing(8)

        self._page_title_lbl = QLabel("GREAT SAGE")
        self._page_title_lbl.setStyleSheet(
            f"color:{TEXT2}; font-size:10px; letter-spacing:3px; background:transparent;")
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
        self._menu_btn = menu_btn

        rv.addWidget(topbar)

        # Page stack
        self._pages    = QStackedWidget()
        self._page_objs: dict = {}

        pages = [
            ("dashboard", DashboardPage()),
            ("legion",    LegionPage()),
            ("matrix",    MatrixPage()),
            ("sage",      SagePage()),
            ("settings",  SettingsPage()),
        ]
        for key, page in pages:
            self._pages.addWidget(page)
            self._page_objs[key] = page

        # Wire dashboard menu button
        if hasattr(self._page_objs["dashboard"], "_menu_btn"):
            self._page_objs["dashboard"]._menu_btn = menu_btn

        # Plugins page
        try:
            from plugin_manager import create_plugins_page as _cpp
            self._plugin_engine, _plugins_page = _cpp(
                status_cb=None)
            self._pages.addWidget(_plugins_page)
            self._page_objs["plugins"] = _plugins_page
            _plugins_page.navigate_home.connect(lambda: self._navigate("dashboard"))
        except Exception as e:
            log.warning("Plugin system unavailable", error=str(e))
            _ph = QWidget()
            _ph.setStyleSheet(f"background:{BG};")
            _phv = QVBoxLayout(_ph)
            _phv.addWidget(lbl("Plugin system unavailable — check plugin_manager.py", MUTED, 12))
            _ph.refresh = lambda: None
            self._pages.addWidget(_ph)
            self._page_objs["plugins"] = _ph

        rv.addWidget(self._pages, 1)
        root.addWidget(right, 1)

        # Keyboard shortcuts
        for i, key in enumerate(("dashboard", "legion", "matrix", "sage", "plugins"), 1):
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
        self._topbar.setVisible(False)  # topbar removed; menu lives in NavRail
        self._page_title_lbl.setText(key.upper())

    def _refresh_current(self):
        self._pages.currentWidget().refresh()

    def _open_menu(self):
        dash = self._page_objs.get("dashboard")
        if dash and hasattr(dash, "_open_menu"):
            dash._open_menu()

    def _open_watchface(self):
        if self._watchface and self._watchface.isVisible():
            self._watchface.close()
            self._watchface = None
            return
        self._watchface = WatchfaceWindow()
        self._watchface.showFullScreen()

    def _open_memory_palace(self):
        if self._memory_palace and self._memory_palace.isVisible():
            self._memory_palace.close()
            self._memory_palace = None
            return
        self._memory_palace = MemoryPalaceWindow()
        self._memory_palace.show()

    def _activate_plugins(self):
        """
        Auto-activate all enabled plugins on startup.
        Calls build_page() for every enabled plugin so their slot registrations
        (e.g. injecting a visualizer into dashboard_top) fire immediately,
        without the user needing to open each plugin manually first.
        """
        plugins_page = self._page_objs.get("plugins")
        if plugins_page is None:
            return
        try:
            engine = getattr(self, "_plugin_engine", None)
            if engine is None:
                return
            for rec in engine.enabled_plugins():
                try:
                    # _open_plugin builds the page and registers slots
                    # but doesn't navigate away from the current page
                    if rec.filename not in plugins_page._plugin_page_indices:
                        api  = plugins_page._api(rec)
                        page = rec.build_page(plugins_page, api)
                        if page is not None:
                            idx = plugins_page._page_stack.count()
                            plugins_page._page_stack.addWidget(page)
                            plugins_page._plugin_page_indices[rec.filename] = idx
                            log.info("Plugin auto-activated", plugin=rec.name)
                except Exception as e:
                    log.warning("Plugin auto-activation failed", plugin=rec.name, error=str(e))
        except Exception as e:
            log.warning("_activate_plugins failed", error=str(e))
        # Force dashboard to re-check slots after all plugins have registered
        QTimer.singleShot(200, self._post_activate_refresh)

    def _post_activate_refresh(self):
        """Called after plugins activate — refreshes dashboard slot hosts."""
        try:
            dashboard = self._page_objs.get("dashboard")
            if dashboard:
                # Trigger SlotHost rebuilds by notifying the registry
                from plugin_manager import SlotRegistry
                for slot in ("dashboard_top", "dashboard_background"):
                    reg = SlotRegistry.instance()
                    reg._notify(slot)
                dashboard.refresh()
        except Exception as e:
            log.warning("_post_activate_refresh failed", error=str(e))

    def _reset_stale_downloads(self):
        ld      = legion_data()
        changed = False
        for name, book in ld.get("books", {}).items():
            status = book.get("download_state", {}).get("status", "idle")
            if status in ("downloading", "queued"):
                book["download_state"]["status"]          = "idle"
                book["download_state"]["pause_requested"] = False
                changed = True
        if changed:
            save_json(LEGION_PROGRESS, ld)

    def _run_auto_sync(self):
        if self._auto_sync_worker and self._auto_sync_worker.isRunning():
            return
        self._auto_sync_worker = AutoSyncWorker()
        self._auto_sync_worker.status_update.connect(
            lambda msg: None)
        self._auto_sync_worker.sync_done.connect(self._on_sync_done)
        self._auto_sync_worker.sync_clear.connect(
            lambda: None)
        self._auto_sync_worker.start()

    def _on_sync_done(self, msg: str):
        pass  # status bar removed
        legion_page = self._page_objs.get("legion")
        if legion_page and hasattr(legion_page, "_show_toast"):
            legion_page._show_toast(msg)


# ── Entry point ────────────────────────────────────────────────────────────────

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
