"""
gs_matrix_ui.py — Great Sage
==============================
Matrix module UI: media tracker page and related dialogs.
"""
import os, re, subprocess, sys, threading, time, datetime as dt
from pathlib import Path

try:
    from gs_logger import log
except Exception as _log_err:
    class _NoopLog:
        def __getattr__(self, name): return _NoopLog()
        def __call__(self, *a, **kw): return None
    log = _NoopLog()

from gs_theme import *
from gs_widgets import lbl, btn, hline, vline, tag, NavRail, EyeBreakToast, SyncToast

from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QSize, QRectF, QRect, QUrl, QPoint, QObject, QEvent
)
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
    WEBENGINE_OK = True
except ImportError:
    WEBENGINE_OK = False
from PyQt6.QtGui import (
    QColor, QFont, QPalette, QTextCursor, QTextOption, QKeySequence, QShortcut,
    QPixmap, QPainter, QLinearGradient, QRadialGradient, QBrush, QPen, QPainterPath
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QLabel, QPushButton, QLineEdit, QTextEdit, QSlider,
    QFrame, QListWidget, QListWidgetItem, QTabWidget, QComboBox,
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QMessageBox, QAbstractItemView,
    QProgressBar, QGroupBox, QFormLayout, QStatusBar, QMenu, QSplitter, QScrollArea,
    QGraphicsOpacityEffect, QRadioButton,
)
from great_sage_core import (
    SCRIPT_DIR, LEGION_PROGRESS, MATRIX_PROGRESS, LEGION_BOOKMARKS, SAGE_MEMORY_PATH,
    load_json, save_json,
    legion_data, matrix_data, bookmarks_data,
    legion_mod, matrix_mod, sage_mod,
    _catalogue_panel_class, _clean_media_title, _strip_markdown, _detect_genre,
    _detect_show_genre,
    _grep_book_for_term,
    sage_memory_load, sage_memory_append, sage_memory_extract,
    behaviour_data, behaviour_summary, track_event, stream_watch_context,
    FetchChapterWorker, SageWorker, MetadataWorker, AutoSyncWorker,
    _SageCompanionWorker, _NewChaptersWorker, _MetaRefreshWorker,
    start_mobile_server, _wl_now,
)

# MPV_SOCKET_PATH is defined in gs_theme.py (imported via 'from gs_theme import *' above).
# Do NOT redefine it here — both gs_matrix_ui and any external IPC caller must
# use the same socket path or IPC commands will be sent to the wrong socket.
# Current value: MPV_SOCKET_PATH = "/tmp/mpvsocket_gs"  (see gs_theme.py)

# Sync hook — registered by SettingsPage after it constructs GreatSageSync.
# Calling _sync_item_added(title, media_type, status, ...) fires an immediate
# push_single to the cloud without any coupling between MatrixPage and SettingsPage.
def _sync_item_added(title: str, media_type: str, status: str = "Planning", **kw) -> None:
    pass  # replaced at runtime by SettingsPage.__init__

# Sync hook — registered by SettingsPage after it constructs GreatSageSync.
# Calling _sync_item_removed(title) fires an immediate delete_item on the cloud
# without any coupling between MatrixPage and SettingsPage.
def _sync_item_removed(title: str) -> None:
    pass  # replaced at runtime by SettingsPage.__init__


class TrailerPickerDialog(QDialog):
    """Shown when TMDB returns multiple matches for a title."""
    def __init__(self, title, candidates, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Title")
        self.setFixedSize(480, 340)
        self.setStyleSheet(f"background:{BG}; border:1px solid {BORDER};")
        self.chosen = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0,0,0,0)
        root.setSpacing(0)

        # Header
        hdr = QWidget()
        hdr.setStyleSheet(f"background:{BG2}; border-bottom:1px solid {BORDER};")
        hdr.setFixedHeight(52)
        hv = QHBoxLayout(hdr)
        hv.setContentsMargins(24,0,24,0)
        hl = QLabel("Multiple matches found")
        hl.setStyleSheet(
            f"font-family:{FONT_DISPLAY}; font-size:14px; font-weight:bold; color:{TEXT};")
        sub = QLabel(f"  for '{title}'")
        sub.setStyleSheet(f"color:{MUTED}; font-size:12px;")
        hv.addWidget(hl); hv.addWidget(sub); hv.addStretch()
        root.addWidget(hdr)

        # Candidate list
        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(20,16,20,16); bv.setSpacing(6)

        hint = QLabel("Which one did you mean?")
        hint.setStyleSheet(f"color:{MUTED}; font-size:11px; letter-spacing:.5px;")
        bv.addWidget(hint)
        bv.addSpacing(8)

        self._buttons = []
        self._bg = None  # button group placeholder — we use manual radio logic

        for c in candidates:
            row = QWidget()
            row.setStyleSheet(
                f"background:{BG2}; border:1px solid {BORDER}; border-radius:4px;")
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            rv = QHBoxLayout(row)
            rv.setContentsMargins(14,12,14,12)
            rv.setSpacing(12)

            radio = QRadioButton()
            radio.setStyleSheet(f"color:{TEXT};")

            type_badge = QLabel("TV" if c["type"] == "tv" else "FILM")
            type_badge.setFixedWidth(38)
            type_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            color = BLUE if c["type"] == "tv" else PURPLE
            type_badge.setStyleSheet(
                f"background:transparent; border:1px solid {color}; color:{color};"
                f"font-size:9px; letter-spacing:1px; padding:2px 4px; border-radius:3px;")

            title_lbl = QLabel(c["title"])
            title_lbl.setStyleSheet(f"font-size:13px; color:{TEXT};")

            year_lbl = QLabel(c["year"] or "—")
            year_lbl.setStyleSheet(f"font-size:12px; color:{MUTED};")
            year_lbl.setFixedWidth(40)

            rv.addWidget(radio); rv.addWidget(type_badge)
            rv.addWidget(title_lbl, 1); rv.addWidget(year_lbl)
            bv.addWidget(row)

            # Clicking anywhere on row selects the radio
            radio.candidate = c
            self._buttons.append(radio)
            row.mousePressEvent = lambda e, rb=radio: rb.setChecked(True)

        if self._buttons:
            self._buttons[0].setChecked(True)

        bv.addStretch()
        root.addWidget(body, 1)

        # Footer
        foot = QWidget()
        foot.setStyleSheet(f"background:{BG2}; border-top:1px solid {BORDER};")
        foot.setFixedHeight(52)
        fv = QHBoxLayout(foot)
        fv.setContentsMargins(20,0,20,0)
        fv.setSpacing(10)
        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setStyleSheet(
            f"background:transparent; border:1px solid {BORDER}; color:{MUTED};"
            f"font-size:9px; letter-spacing:1.5px; padding:8px 20px; border-radius:3px;")
        cancel_btn.clicked.connect(self.reject)
        watch_btn = QPushButton("▶  WATCH TRAILER")
        watch_btn.setStyleSheet(
            f"background:{ACCENT}; color:{BG}; border:none; font-size:10px; font-weight:700;"
            f"letter-spacing:1.5px; padding:9px 22px; border-radius:3px;")
        watch_btn.clicked.connect(self._confirm)
        fv.addStretch(); fv.addWidget(cancel_btn); fv.addWidget(watch_btn)
        root.addWidget(foot)

    def _confirm(self):
        for rb in self._buttons:
            if rb.isChecked():
                self.chosen = rb.candidate
                self.accept()
                return
        self.reject()


class TrailerDialog(QDialog):
    """Embedded YouTube trailer search."""

    if WEBENGINE_OK:
        from PyQt6.QtWebEngineWidgets import QWebEngineView as _WEV
        from PyQt6.QtWebEngineCore import QWebEnginePage as _WEP

        class _QuietPage(_WEP):
            """Suppress noisy JS console messages from YouTube headers."""
            def javaScriptConsoleMessage(self, level, message, line, source):
                pass  # silence ch-ua-form-factors / Document-Policy noise

    def __init__(self, title, vid_id=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Trailer — {title}")
        self.setModal(True)
        self.setMinimumSize(900, 560)
        self.resize(1020, 620)
        self.setStyleSheet(f"background:{BG};")

        # Center on parent
        if parent:
            pg = parent.window().geometry()
            self.move(
                pg.x() + (pg.width()  - 1020) // 2,
                pg.y() + (pg.height() -  620) // 2,
            )

        root = QVBoxLayout(self)
        root.setContentsMargins(0,0,0,0)
        root.setSpacing(0)

        # Header
        hdr = QWidget()
        hdr.setStyleSheet(f"background:{BG2}; border-bottom:1px solid {BORDER};")
        hdr.setFixedHeight(48)
        hv = QHBoxLayout(hdr)
        hv.setContentsMargins(20,0,12,0)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            f"font-family:{FONT_DISPLAY}; font-size:15px; color:{TEXT}; letter-spacing:1px;")
        badge = QLabel("▶ YouTube")
        badge.setStyleSheet(
            f"background:#FF0000; color:white; font-size:9px; font-weight:700;"
            f"letter-spacing:1px; padding:3px 8px; border-radius:3px;")
        close_btn = QPushButton("✕  CLOSE")
        close_btn.setStyleSheet(
            f"background:transparent; border:1px solid {BORDER}; color:{MUTED};"
            f"font-size:9px; letter-spacing:1px; padding:6px 14px; border-radius:3px;")
        close_btn.clicked.connect(self._close_and_stop)
        hv.addWidget(title_lbl); hv.addSpacing(12); hv.addWidget(badge)
        hv.addStretch(); hv.addWidget(close_btn)
        root.addWidget(hdr)

        # Embedded browser with noise-suppressed page
        self._view = QWebEngineView()
        if WEBENGINE_OK:
            self._view.setPage(self._QuietPage(self._view))
        import urllib.parse
        url = ("https://www.youtube.com/watch?v=" + vid_id) if vid_id else \
              ("https://www.youtube.com/results?search_query=" +
               urllib.parse.quote(f"{title} official trailer"))
        self._view.load(QUrl(url))
        root.addWidget(self._view, 1)

        # Footer hint
        bot = QWidget()
        bot.setStyleSheet(f"background:{BG2}; border-top:1px solid {BORDER};")
        bot.setFixedHeight(32)
        bv = QHBoxLayout(bot)
        bv.setContentsMargins(20,0,20,0)
        hint = QLabel("Click a result to play  ·  Esc to close")
        hint.setStyleSheet(f"color:{MUTED}; font-size:10px;")
        bv.addWidget(hint)
        root.addWidget(bot)

    def _close_and_stop(self):
        self._view.load(QUrl("about:blank"))
        self.accept()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._close_and_stop()
        else:
            super().keyPressEvent(e)


# ── Ad blocker — see gs_adblock.py ───────────────────────────────────────────
from gs_adblock import AdBlockPage, get_manager as _get_adblock_manager


# ══════════════════════════════════════════════════════════════════════════════
# ANIMEKAI WATCH HISTORY SYNC WORKER
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# WATCHLIST DETAIL POSTER WORKER
# ══════════════════════════════════════════════════════════════════════════════

class _PosterWorker(QThread):
    """Downloads a poster/cover image from a URL in the background and
    emits the raw bytes. Kept separate from MetadataWorker so a slow image
    host never blocks the synopsis/title from showing up."""
    done  = pyqtSignal(bytes)
    error = pyqtSignal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
        try:
            import requests as _req
            r = _req.get(self.url, timeout=15)
            r.raise_for_status()
            self.done.emit(r.content)
        except Exception as e:
            self.error.emit(str(e))


class MatrixPage(QWidget):
    _request_cloud_push = pyqtSignal()   # thread-safe push trigger from play loop
    _request_toast      = pyqtSignal(str, str)  # (message, kind) — "info" or "warn"

    def __init__(self):
        super().__init__()
        self._meta_worker  = None
        self._mpv_process  = None
        self._play_thread  = None
        self._request_cloud_push.connect(self._cloud_push)
        self._request_toast.connect(self._show_cloud_toast)
        self._build()
        self.refresh()
        # Instantiate cloud-sync toast (parented to self so it overlays MatrixPage)
        self._cloud_toast = SyncToast(self)
        self._cloud_toast.hide()
        # Pull from cloud on launch (delayed so token refresh has time to complete)
        QTimer.singleShot(3000, self._cloud_pull_on_launch)

    def _cloud_pull_on_launch(self):
        """
        Pull latest watchlist from Supabase on launch, then keep polling every
        5 minutes so TrackFlix additions appear in Great Sage without a restart.
        """
        POLL_INTERVAL = 300  # seconds between watchlist pulls

        def _do():
            try:
                from gs_sync import GreatSageSync
                s = GreatSageSync()
                if not s.is_logged_in():
                    log.sync.warning("[cloud] Not logged in — skipping pull")
                    self._request_toast.emit(
                        "☁  Cloud sync: not logged in — sign in in Settings", "warn"
                    )
                    return

                # ── First pull immediately on launch ─────────────────────────
                try:
                    s.restore_to_disk()
                    log.sync.info("[cloud] Pull on launch complete")
                    QTimer.singleShot(0, self.refresh)
                except Exception as e:
                    log.sync.warning("[cloud] Pull on launch failed", error=str(e))

                # ── Keep polling every POLL_INTERVAL seconds ──────────────────
                while True:
                    import time as _time
                    _time.sleep(POLL_INTERVAL)
                    try:
                        ok = s.restore_to_disk()
                        if ok:
                            log.sync.info("[cloud] Periodic pull complete")
                            QTimer.singleShot(0, self.refresh)
                        else:
                            log.sync.warning("[cloud] Periodic pull returned False")
                    except Exception as e:
                        log.sync.warning("[cloud] Periodic pull failed", error=str(e))

            except Exception as e:
                log.sync.warning("[cloud] Pull thread crashed", error=str(e))

        threading.Thread(target=_do, daemon=True, name="gs_cloud_pull").start()

    def _cloud_push(self):
        """Push current progress to Supabase in the background."""
        def _do():
            try:
                from gs_sync import GreatSageSync
                s = GreatSageSync()
                if s.is_logged_in():
                    s.push()
                    log.sync.info("[cloud] Push complete")
            except Exception as e:
                log.sync.warning("[cloud] Push failed", error=str(e))
        threading.Thread(target=_do, daemon=True).start()

    def _show_cloud_toast(self, message: str, kind: str = "info"):
        """Show a SyncToast on the main thread. Safe to call via signal from any thread."""
        self._cloud_toast.show_toast(message)

    def _build(self):
        import types as _mt
        from PyQt6.QtGui import (QPainter as _mP, QLinearGradient as _mG,
                                  QColor as _mC, QBrush as _mB, QPen as _mPen)
        root = QVBoxLayout(self)
        root.setContentsMargins(0,0,0,0)
        root.setSpacing(0)

        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: none; background: {BG}; }}
            QTabBar {{ background: {BG2}; }}
            QTabBar::tab {{ background: transparent; color: {MUTED}; border: none;
                border-bottom: 1px solid transparent; padding: 14px 28px;
                font-size: 9px; letter-spacing: 2.5px; font-weight: bold; }}
            QTabBar::tab:selected {{ color: {BLUE}; border-bottom: 1px solid {BLUE}; }}
            QTabBar::tab:hover {{ color: {TEXT2}; background: {BG3}; }}
        """)
        tabs.addTab(self._build_watchlist(),  "WATCHLIST")
        tabs.addTab(self._build_browser(),    "BROWSE")
        tabs.addTab(self._build_continue(),   "CONTINUE")

        # STREAM tab is expensive to build (QWebEngineView + ad-blocker
        # filter compilation), so we defer it until the user actually opens
        # it instead of paying that cost at app launch for every session.
        self._stream_built = False
        self._stream_tab_index = 3
        self._stream_ready_callbacks = []
        self._stream_tab = self._build_stream_placeholder()
        tabs.addTab(self._stream_tab,         "STREAM")
        tabs.currentChanged.connect(self._on_matrix_tab_changed)

        self._tabs = tabs

        try:
            from plugin_manager import SlotHost as _SH
            _slot_mh = _SH("matrix_header")
            root.addWidget(_slot_mh)
        except Exception as e:
            log.warning("Operation failed", error=str(e), location="_build")
        root.addWidget(tabs, 1)

    def _build_watchlist(self):
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(0,0,0,0)
        root.setSpacing(0)

        # ── Add bar (full width at top) ───────────────────────────────────────
        add_bar = QWidget()
        add_bar.setStyleSheet(
            f"background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {BG2},stop:0.5 {BG3},stop:1 {BG2});"
            f"border-bottom:1px solid {BORDER2};")
        add_bar.setFixedHeight(56)
        add_row = QHBoxLayout(add_bar)
        add_row.setContentsMargins(16,0,16,0)
        add_row.setSpacing(10)
        self.wl_input = QLineEdit()
        self.wl_input.setPlaceholderText("Add a title to your list...")
        self.wl_input.setStyleSheet(
            f"QLineEdit{{background:{BG};border:1px solid {BORDER2};"
            f"border-radius:4px;color:{TEXT};font-size:12px;padding:6px 12px;}}"
            f"QLineEdit:focus{{border-color:{BLUE};}}")
        self.wl_input.returnPressed.connect(self._wl_add)
        self.wl_target = QComboBox()
        self.wl_target.setStyleSheet(
            f"QComboBox{{background:{BG};border:1px solid {BORDER2};"
            f"color:{TEXT2};font-size:11px;padding:5px 10px;border-radius:4px;}}"
            f"QComboBox::drop-down{{border:none;}}")
        for n in ("planning","watching","dropped","completed"): self.wl_target.addItem(n.capitalize(),n)
        self.wl_anime = QCheckBox("Anime")
        self.wl_anime.setStyleSheet(f"color:{TEXT2};font-size:11px;background:transparent;")
        add_row.addWidget(self.wl_input, 1)
        add_row.addWidget(self.wl_target)
        add_row.addWidget(self.wl_anime)
        add_row.addWidget(btn("+ Add","accent",self._wl_add))
        root.addWidget(add_bar)

        # ── Splitter: list | detail ───────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("QSplitter::handle{background:#1E2D3D;width:1px;}")

        # Left: tabs + lists
        left = QWidget()
        left.setStyleSheet(f"background:{BG2};")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0,0,0,0)
        lv.setSpacing(0)
        self.wl_tabs = QTabWidget()
        self.wl_tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border:none; background:{BG}; }}
            QTabBar {{ background:{BG}; border-bottom:1px solid {BORDER}; }}
            QTabBar::tab {{ background:transparent; color:{MUTED};
                border:none; border-bottom:2px solid transparent;
                padding:10px 20px; font-size:9px; letter-spacing:1.5px; }}
            QTabBar::tab:selected {{ color:{BLUE}; border-bottom:2px solid {BLUE}; }}
            QTabBar::tab:hover {{ color:{TEXT}; background:{BG3}; }}
        """)
        self.wl_lists = {}
        for n in ("planning","watching","dropped","completed"):
            lw = QListWidget()
            lw.setStyleSheet(
                f"QListWidget{{background:transparent;border:none;padding:6px;}}"
                f"QListWidget::item{{color:{TEXT2};padding:10px 16px;"
                f"border-bottom:1px solid {BORDER};font-size:13px;}}"
                f"QListWidget::item:hover{{color:{TEXT};background:{BG2};}}"
                f"QListWidget::item:selected{{color:{BLUE};background:{BG3};"
                f"border-left:3px solid {BLUE};}}"
                f"QScrollBar:vertical{{background:transparent;width:8px;border:none;margin:0;}}"
                f"QScrollBar::handle:vertical{{background:{BORDER2};border-radius:4px;min-height:30px;}}"
                f"QScrollBar::handle:vertical:hover{{background:{ACCENT};}}"
                f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}")
            lw.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            lw.customContextMenuRequested.connect(lambda pos, lw=lw, n=n: self._wl_ctx(pos,lw,n))
            lw.itemDoubleClicked.connect(lambda item, n=n: self._wl_meta(item,n))
            lw.itemClicked.connect(lambda item, n=n: self._wl_meta(item,n))
            self.wl_lists[n] = lw
            self.wl_tabs.addTab(lw, n.capitalize())
        lv.addWidget(self.wl_tabs, 1)
        splitter.addWidget(left)

        # Right: detail panel
        right = QWidget()
        right.setStyleSheet(f"background:{BG};")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(32,28,32,28)
        rv.setSpacing(0)

        self.wl_placeholder = QLabel("Click any title to see details")
        self.wl_placeholder.setStyleSheet(f"color:{MUTED};font-size:13px;letter-spacing:.5px;")
        self.wl_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rv.addWidget(self.wl_placeholder, 1)

        self.wl_detail_w = QWidget()
        self.wl_detail_w.hide()
        outer_dv = QHBoxLayout(self.wl_detail_w)
        outer_dv.setContentsMargins(0,0,0,0)
        outer_dv.setSpacing(24)

        # Poster — fixed width, left side. Hidden until an image loads so
        # layout doesn't reserve empty space for titles with no artwork.
        self.wl_d_poster = QLabel("")
        self.wl_d_poster.setFixedSize(180, 260)
        self.wl_d_poster.setScaledContents(False)
        self.wl_d_poster.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.wl_d_poster.setStyleSheet(
            f"background:{BG2};border:1px solid {BORDER};border-radius:6px;")
        self.wl_d_poster.hide()
        outer_dv.addWidget(self.wl_d_poster, 0, Qt.AlignmentFlag.AlignTop)
        self._poster_worker = None

        text_col = QWidget()
        dv = QVBoxLayout(text_col)
        dv.setContentsMargins(0,0,0,0)
        dv.setSpacing(0)

        self.wl_d_title = QLabel("")
        self.wl_d_title.setStyleSheet(
            f"font-family:{FONT_DISPLAY};font-size:26px;font-weight:bold;color:{TEXT};letter-spacing:1px;")
        self.wl_d_title.setWordWrap(True)
        dv.addWidget(self.wl_d_title); dv.addSpacing(6)

        self.wl_d_meta = QLabel("")
        self.wl_d_meta.setStyleSheet(f"color:{MUTED};font-size:12px;letter-spacing:.5px;")
        dv.addWidget(self.wl_d_meta); dv.addSpacing(16)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet(f"color:{BORDER};"); dv.addWidget(div); dv.addSpacing(16)

        self.wl_d_synopsis = QLabel("")
        self.wl_d_synopsis.setStyleSheet(
            f"font-family:{FONT_DISPLAY};font-size:15px;color:{TEXT2};line-height:1.8;")
        self.wl_d_synopsis.setWordWrap(True)
        self.wl_d_synopsis.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        dv.addWidget(self.wl_d_synopsis, 1); dv.addSpacing(20)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.wl_d_trailer_btn = QPushButton("▶  WATCH TRAILER")
        self.wl_d_trailer_btn.setStyleSheet(
            f"background:{BLUE};color:{BG};border:none;font-size:10px;font-weight:700;"
            f"letter-spacing:1.5px;padding:10px 20px;border-radius:3px;")
        self.wl_d_trailer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.wl_d_trailer_btn.clicked.connect(self._trailer_btn_clicked)
        self.wl_d_trailer_btn.hide()
        self.wl_d_src_lbl = QLabel("")
        self.wl_d_src_lbl.setStyleSheet(f"color:{MUTED};font-size:10px;letter-spacing:.5px;")
        btn_row.addWidget(self.wl_d_trailer_btn); btn_row.addStretch(); btn_row.addWidget(self.wl_d_src_lbl)
        dv.addLayout(btn_row)

        outer_dv.addWidget(text_col, 1)

        rv.addWidget(self.wl_detail_w, 1)
        splitter.addWidget(right)

        # 40% list, 60% detail
        splitter.setSizes([420, 630])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        root.addWidget(splitter, 1)

        self.wl_info = QLabel(""); self.wl_info.hide()
        self._wl_current_title = ""
        return w


    def _wl_add(self):
        title = self.wl_input.text().strip()
        if not title: return
        lst = self.wl_target.currentData()
        if not lst:
            self.wl_info.setText("⚠ Select a list first (Planning / Watching / etc.)")
            return
        anime = self.wl_anime.isChecked()
        try:
            md = matrix_data(); wl = md.setdefault("watchlist",{})
            for k in ("planning","watching","dropped","completed"): wl.setdefault(k,[])
            # Remove from all lists first (dedup)
            for k in wl:
                wl[k] = [e for e in wl[k]
                    if (e.get("title","") if isinstance(e,dict) else str(e)).lower() != title.lower()]
            wl[lst].append({"title":title,"is_anime":anime,"added":time.time(),
                            "watched":False,"notes":"Added via GUI","updated_at":_wl_now()})
            save_json(MATRIX_PROGRESS, md)
            _sync_item_added(title, "Anime" if anime else "Novel", lst.capitalize())
            self.wl_input.clear()
            self.refresh()
            # Switch to the tab we just added to
            tab_idx = {"planning":0,"watching":1,"dropped":2,"completed":3}.get(lst, 0)
            self.wl_tabs.setCurrentIndex(tab_idx)
            self.wl_info.setText(f"✓ Added '{title}' to {lst.capitalize()}")
        except Exception as e:
            self.wl_info.setText(f"⚠ Could not save: {e}")

    def _wl_ctx(self, pos, lw, lst_name):
        item = lw.itemAt(pos)
        if not item: return
        e = item.data(Qt.ItemDataRole.UserRole)
        title = e.get("title","") if isinstance(e,dict) else str(e)
        menu = QMenu(self)
        for target in ("planning","watching","dropped","completed"):
            if target != lst_name:
                act = menu.addAction(f"Move -> {target.capitalize()}")
                act.triggered.connect(lambda _, t=target, ti=title, en=e: self._wl_move(ti,en,lst_name,t))
        menu.addSeparator()
        menu.addAction("Remove").triggered.connect(lambda: self._wl_remove(title,lst_name))
        menu.exec(lw.mapToGlobal(pos))

    def _wl_move(self, title, entry, from_list, to_list):
        md = matrix_data(); wl = md.setdefault("watchlist", {})
        for k in ("planning", "watching", "dropped", "completed"): wl.setdefault(k, [])
        for k in wl:
            wl[k] = [e for e in wl[k]
                if (e.get("title", "") if isinstance(e, dict) else str(e)).lower() != title.lower()]
        e = entry if isinstance(entry, dict) else {"title": title, "watched": False, "added": time.time()}
        e["updated_at"] = _wl_now()  # stamp on every status change

        # ── Scoring prompt when moving to Completed ──────────────────────────
        if to_list == "completed":
            score_dlg = QDialog(self)
            score_dlg.setWindowTitle("Rate It")
            score_dlg.setModal(True)
            score_dlg.setFixedWidth(340)
            score_dlg.setStyleSheet(f"background:{BG}; color:{TEXT};")
            if self.window():
                pg = self.window().geometry()
                score_dlg.move(pg.x() + (pg.width() - 340) // 2,
                               pg.y() + (pg.height() - 160) // 2)
            sv = QVBoxLayout(score_dlg)
            sv.setContentsMargins(20, 20, 20, 20)
            sv.setSpacing(12)
            sv.addWidget(lbl(f"How would you rate  {title[:36]}?", ACCENT, 13, True))
            sv.addWidget(lbl("0 = Skip  ·  1–10 = Your score", MUTED, 10))
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 10); slider.setValue(0); slider.setTickInterval(1)
            slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            score_lbl = QLabel("Skip (no rating)")
            score_lbl.setStyleSheet(f"color:{TEXT2}; font-size:11px;")
            score_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            def _upd(v): score_lbl.setText("Skip (no rating)" if v == 0 else f"{'★' * v}{'☆' * (10-v)}  {v}/10")
            slider.valueChanged.connect(_upd)
            sv.addWidget(slider); sv.addWidget(score_lbl)
            ok_btn = QPushButton("Save & Complete")
            ok_btn.setStyleSheet(
                f"background:{ACCENT}; color:{BG}; border:none; font-size:10px;"
                f"letter-spacing:1px; padding:8px 16px; border-radius:3px;")
            ok_btn.clicked.connect(score_dlg.accept)
            sv.addWidget(ok_btn)
            score_dlg.exec()
            score = slider.value()
            if score > 0:
                e["score"] = score
                e["scored_at"] = time.time()

        wl[to_list].append(e); save_json(MATRIX_PROGRESS, md); self.refresh()
        _sync_item_added(
            title,
            "Anime" if e.get("is_anime", True) else "Novel",
            to_list.capitalize(),
            rating=e.get("score", 0),
        )

    def _wl_remove(self, title, lst_name):
        md = matrix_data(); wl = md.get("watchlist",{})
        wl[lst_name] = [e for e in wl.get(lst_name,[])
            if (e.get("title","") if isinstance(e,dict) else str(e)).lower() != title.lower()]
        save_json(MATRIX_PROGRESS, md)
        _sync_item_removed(title)
        self.refresh()

    def _wl_meta(self, item, lst_name):
        e = item.data(Qt.ItemDataRole.UserRole)
        title = e.get("title","?") if isinstance(e,dict) else str(e)
        title = _strip_markdown(_clean_media_title(title))
        is_anime = isinstance(e,dict) and e.get("is_anime",False)
        self.wl_placeholder.setText(f"Fetching info for '{title}'...")
        self.wl_placeholder.show(); self.wl_detail_w.hide()
        if self._meta_worker and self._meta_worker.isRunning(): self._meta_worker.terminate()
        self._meta_worker = MetadataWorker(title, is_anime)
        self._meta_worker.done.connect(lambda info, t=title: self._show_meta(info,t))
        self._meta_worker.error.connect(lambda e: (
            self.wl_placeholder.setText(f"Error: {e}"),
            self.wl_placeholder.show() or self.wl_detail_w.hide()
        ))
        self._meta_worker.start()

    def _play_trailer(self, title):
        self.wl_info.setText(f"Finding trailer for '{title}'...")
        def _fetch():
            import shutil, urllib.parse
            trailer_url = None; err = ""
            try:
                if shutil.which("yt-dlp"):
                    import subprocess as sp
                    r = sp.run(["yt-dlp", f"ytsearch1:{title} official trailer",
                                "--get-url", "--no-playlist", "-f", "best[height<=720]/best"],
                               capture_output=True, text=True, timeout=15)
                    if r.returncode == 0 and r.stdout.strip():
                        trailer_url = r.stdout.strip().splitlines()[0]
                    else: err = "yt-dlp could not find a trailer"
                else: err = "yt-dlp not installed — run: pip install yt-dlp"
            except Exception as e: err = str(e)[:100]
            def _launch():
                if trailer_url:
                    self.wl_info.setText(f"Playing trailer: {title}")
                    try: subprocess.Popen(["mpv","--really-quiet",
                                           f"--title=Trailer — {title}", trailer_url])
                    except Exception as ex: self.wl_info.setText(f"mpv error: {ex}")
                else:
                    import webbrowser
                    self.wl_info.setText(f"Opening YouTube — {err}")
                    webbrowser.open("https://www.youtube.com/results?search_query=" +
                                    urllib.parse.quote(f"{title} official trailer"))
            QTimer.singleShot(0, _launch)
        threading.Thread(target=_fetch, daemon=True).start()

    def _info_link_clicked(self, url):
        pass  # replaced by _trailer_btn_clicked

    def _show_meta(self, info, title):
        if not info:
            self.wl_placeholder.setText(f"No metadata found for '{title}'.")
            self.wl_placeholder.show(); self.wl_detail_w.hide()
            return
        t        = info.get("title", title)
        yr       = info.get("year","")
        sc       = info.get("score", 0)
        ov       = info.get("synopsis") or info.get("overview","")
        src_s    = info.get("source","")
        genres   = info.get("genres","")
        seasons  = info.get("seasons","")
        episodes = info.get("episodes","")
        runtime  = info.get("runtime","")
        genre_str = (", ".join(genres[:4]) if isinstance(genres,list) else
                     genres if isinstance(genres,str) else "")

        self.wl_d_title.setText(t)

        meta_parts = []
        if yr: meta_parts.append(str(yr))
        if sc and sc != "N/A": meta_parts.append(f"★ {sc}")
        if seasons and str(seasons) not in ("", "Unknown"):
            s = int(seasons) if str(seasons).isdigit() else seasons
            meta_parts.append(f"{s} Season" + ("s" if s != 1 else ""))
        if episodes and str(episodes) not in ("", "Unknown"):
            e = int(episodes) if str(episodes).isdigit() else episodes
            meta_parts.append(f"{e} Episode" + ("s" if e != 1 else ""))
        if runtime and not str(runtime).startswith("?"):
            meta_parts.append(str(runtime))
        if genre_str: meta_parts.append(genre_str)
        self.wl_d_meta.setText("  ·  ".join(meta_parts))

        self.wl_d_synopsis.setText(ov if ov else "No synopsis available.")
        self.wl_d_src_lbl.setText(f"via {src_s}" if src_s else "")

        self._wl_current_title = title
        self.wl_d_trailer_btn.show()

        self._load_poster(info.get("image_url", ""))

        self.wl_placeholder.hide()
        self.wl_detail_w.show()

    def _load_poster(self, url: str):
        """Fetch and display the poster for the currently-shown detail entry.
        Hides the poster slot entirely if there's no image_url or the
        download fails, rather than showing a broken/blank box."""
        if self._poster_worker and self._poster_worker.isRunning():
            self._poster_worker.terminate()
        self.wl_d_poster.clear()
        self.wl_d_poster.hide()
        if not url:
            return
        self._poster_worker = _PosterWorker(url)
        self._poster_worker.done.connect(lambda data, u=url: self._set_poster(data, u))
        self._poster_worker.error.connect(lambda e: None)  # silently keep poster hidden
        self._poster_worker.start()

    def _set_poster(self, data: bytes, url: str):
        # Guard against a late-arriving image for a title the user has since
        # navigated away from (e.g. rapid clicking down the watchlist).
        pix = QPixmap()
        if not pix.loadFromData(data):
            return
        pix = pix.scaled(180, 260, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                          Qt.TransformationMode.SmoothTransformation)
        self.wl_d_poster.setPixmap(pix)
        self.wl_d_poster.show()

    def _reset_trailer_btn(self):
        self.wl_d_trailer_btn.setText("▶  WATCH TRAILER")
        self.wl_d_trailer_btn.setEnabled(True)

    def _trailer_btn_clicked(self):
        title = self._wl_current_title
        if not title: return
        if WEBENGINE_OK:
            dlg = TrailerDialog(title, None, self)
            dlg.exec()
        else:
            import urllib.parse, webbrowser
            webbrowser.open("https://www.youtube.com/results?search_query=" +
                            urllib.parse.quote(f"{title} official trailer"))


    # ── Browser ────────────────────────────────────────────────────────────────
    VIDEO_EXTS = ('.mp4','.mkv','.avi','.mov','.wmv','.flv','.webm','.m4v')

    def _build_browser(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10,10,10,10)
        v.setSpacing(8)

        # Breadcrumb bar
        crumb_row = QHBoxLayout()
        self.br_back_btn = btn("↑ Up", cb=self._browser_up)
        self.br_home_btn = btn("⌂ Home", cb=self._browser_home)
        self.br_crumb    = lbl("", TEXT2, 12)
        self.br_crumb.setWordWrap(False)
        crumb_row.addWidget(self.br_back_btn)
        crumb_row.addWidget(self.br_home_btn)
        crumb_row.addWidget(self.br_crumb, 1)
        v.addLayout(crumb_row)
        v.addWidget(hline())

        self.br_list = QListWidget()
        self.br_list.itemDoubleClicked.connect(self._browser_activate)
        self.br_list.itemClicked.connect(self._browser_single_click)
        v.addWidget(self.br_list, 1)

        # Bottom: selected file info + Play button
        play_row = QHBoxLayout()
        self.br_selected = QLineEdit()
        self.br_selected.setReadOnly(True)
        self.br_selected.setPlaceholderText("Double-click a folder to open  ·  Double-click a file to play")
        self.br_play_btn = btn("▶  Play", "accent", self._browser_play)
        self.br_play_btn.setEnabled(False)
        play_row.addWidget(self.br_selected, 1)
        play_row.addWidget(self.br_play_btn)
        v.addLayout(play_row)

        # Init state
        self._browser_stack = []   # history of directories
        self._browser_cur   = ""   # current directory
        self._browser_selected_path = ""
        self._browser_home()
        return w

    def _browser_home(self):
        """Jump to the configured download dir, then common video dirs."""
        md = matrix_data()
        configured = md.get("settings", {}).get("download_dir", "")
        candidates = []
        if configured and os.path.isdir(configured):
            candidates.append(configured)
        candidates += [
            os.path.expanduser("~/Videos"),
            os.path.expanduser("~/Movies"),
            os.path.expanduser("~/videos"),
            "/media",
            "/mnt/media",
            os.path.expanduser("~"),
        ]
        for d in candidates:
            if os.path.isdir(d):
                self._browser_stack = []
                self._browser_navigate(d)
                return

    def _browser_navigate(self, directory):
        self._browser_cur = directory
        self._browser_selected_path = ""
        self.br_selected.clear()
        self.br_play_btn.setEnabled(False)
        self._browser_populate()

    def _browser_populate(self):
        directory = self._browser_cur
        self.br_crumb.setText(directory)
        self.br_list.clear()

        try:
            raw = sorted(os.listdir(directory), key=lambda x: (not os.path.isdir(os.path.join(directory,x)), x.lower()))
        except PermissionError:
            self.br_list.addItem(QListWidgetItem("  Permission denied")); return

        for name in raw:
            if name.startswith('.'): continue
            full = os.path.join(directory, name)
            if os.path.isdir(full):
                try:    count = len([x for x in os.listdir(full) if not x.startswith('.')])
                except OSError: count = 0
                label = f"  📁  {name}  ({count} items)"
                item  = QListWidgetItem(label)
                item.setForeground(QColor(BLUE))
            elif name.lower().endswith(self.VIDEO_EXTS):
                size_mb = os.path.getsize(full) // (1024*1024)
                label   = f"  🎬  {name}  ({size_mb} MB)" if size_mb > 0 else f"  🎬  {name}"
                item    = QListWidgetItem(label)
                item.setForeground(QColor(TEXT))
            else:
                continue
            item.setData(Qt.ItemDataRole.UserRole, full)
            self.br_list.addItem(item)

        self.br_back_btn.setEnabled(bool(self._browser_stack))

    def _browser_single_click(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and os.path.isfile(path) and path.lower().endswith(self.VIDEO_EXTS):
            self._browser_selected_path = path
            self.br_selected.setText(os.path.basename(path))
            self.br_play_btn.setEnabled(True)
        else:
            self._browser_selected_path = ""
            self.br_play_btn.setEnabled(False)

    def _browser_activate(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path: return
        if os.path.isdir(path):
            self._browser_stack.append(self._browser_cur)
            self._browser_navigate(path)
        elif path.lower().endswith(self.VIDEO_EXTS):
            self._browser_selected_path = path
            self.br_selected.setText(os.path.basename(path))
            self.br_play_btn.setEnabled(True)
            self._browser_play()
        else:
            try:
                subprocess.Popen(["xdg-open", path])
            except Exception as e:
                log.warning("Operation failed", error=str(e), location="_browser_activate")

    def _browser_up(self):
        if self._browser_stack:
            self._browser_navigate(self._browser_stack.pop())

    def keyPressEvent(self, e):
        key = e.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.br_list.hasFocus():
                item = self.br_list.currentItem()
                if item: self._browser_activate(item); return
            if self.cw_list.hasFocus():
                item = self.cw_list.currentItem()
                if item: self._resume(item); return
            for lst_name, lw in self.wl_lists.items():
                if lw.hasFocus():
                    item = lw.currentItem()
                    if item: self._wl_meta(item, lst_name); return
        super().keyPressEvent(e)

    def _browser_play(self):
        path = self._browser_selected_path
        if not path or not os.path.exists(path): return
        self._launch_mpv(path)   # adds to Continue Watching automatically

    # ── Continue Watching ──────────────────────────────────────────────────────
    def _build_continue(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10,10,10,10)
        v.setSpacing(8)
        v.addWidget(lbl("Continue Watching — double-click to resume", ACCENT, 13, True))
        self.cw_list = QListWidget()
        self.cw_list.setWordWrap(True)
        self.cw_list.itemDoubleClicked.connect(self._resume)
        self.cw_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.cw_list.customContextMenuRequested.connect(self._cw_ctx)
        v.addWidget(self.cw_list, 1)
        self.now_lbl = lbl("", TEXT2, 12); v.addWidget(self.now_lbl)
        return w

    def _resume(self, item):
        info  = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(info, dict): return

        # Stream entry — switch to STREAM tab
        if info.get("source") == "stream":
            title = info.get("title", "")
            ep    = info.get("episode", 0)
            self._tabs.setCurrentIndex(self._stream_tab_index)
            def _go_to_episode():
                if ep and WEBENGINE_OK and hasattr(self, "_stream_view"):
                    import urllib.parse
                    q = urllib.parse.quote(f"{title} episode {ep}")
                    self._stream_view.load(QUrl(f"https://animekai.be/search?keyword={q}"))
            self._ensure_stream_built(on_ready=_go_to_episode)
            return

        # Local file entry
        fpath = info.get("file_path", "")
        pos   = info.get("position", 0)
        title = info.get("title", "?")
        if fpath and os.path.exists(fpath):
            self._launch_mpv(fpath, start=pos)
        else:
            msg = f"File not found for '{title}'."
            if fpath: msg += f"\n\nLast known path:\n{fpath}"
            msg += "\n\nWould you like to browse for it?"
            reply = QMessageBox.question(self, "File Missing", msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                new_path, _ = QFileDialog.getOpenFileName(self, f"Find '{title}'",
                    os.path.expanduser("~/Videos"),
                    "Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.m4v *.wmv);;All Files (*)")
                if new_path:
                    md = matrix_data()
                    if title in md.get("watching", {}):
                        md["watching"][title]["file_path"] = new_path
                        save_json(MATRIX_PROGRESS, md)
                    self._launch_mpv(new_path, start=pos)

    def _cw_ctx(self, pos):
        item = self.cw_list.itemAt(pos)
        if not item: return
        info      = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(info, dict): return
        title     = info.get("title", "?")
        file_path = info.get("file_path", "")
        season    = info.get("current_season",  info.get("season",  0))
        episode   = info.get("current_episode", info.get("episode", 0))
        total_eps = info.get("total_episodes", 0)
        menu = QMenu(self)
        if file_path:
            sub_act = menu.addAction("⬇  Download Subtitles…")
            sub_act.triggered.connect(
                lambda: self._open_subtitle_dialog(title, season, episode, file_path, total_eps))
        menu.addAction("Remove from Continue Watching").triggered.connect(
            lambda: self._cw_remove(title))
        menu.exec(self.cw_list.mapToGlobal(pos))

    def _open_subtitle_dialog(self, title, season, episode, video_path, total_eps=0):
        """Open the subtitle search/download dialog for a video."""
        try:
            from subtitle_manager import SubtitleDialog
        except ImportError:
            QMessageBox.warning(
                self, "Module Missing",
                "subtitle_manager.py not found.\n"
                "Place it in the same folder as great_sage_gui.py.")
            return
        # If it's a single file (movie), clear season/episode so the dialog
        # opens in movie mode rather than pre-filling junk episode numbers
        is_movie = (total_eps == 1)
        if is_movie:
            season = 0
            episode = 0
        dlg = SubtitleDialog(self, title=title, season=season,
                             episode=episode, video_path=video_path,
                             auto_movie=is_movie)
        dlg.exec()

    def _cw_remove(self, title):
        md = matrix_data()
        watching = md.get("watching", {})
        norm = self._norm_title(title)
        for k in list(watching.keys()):
            kt = watching[k].get("title", k) if isinstance(watching[k], dict) else k
            if self._norm_title(kt) == norm:
                del watching[k]
        save_json(MATRIX_PROGRESS, md); self.refresh()

    def _find_subtitle(self, video_path: str) -> str:
        """
        Look for a matching subtitle in ~/Subtitles/ for the given video file.
        Matches on the video file stem (without extension), case-insensitive.
        Returns the subtitle path if found, else empty string.
        """
        sub_dir = os.path.expanduser("~/Subtitles")
        if not os.path.isdir(sub_dir):
            return ""
        stem = os.path.splitext(os.path.basename(video_path))[0].lower()
        for fname in os.listdir(sub_dir):
            if fname.lower().endswith((".srt", ".ass", ".ssa", ".vtt")):
                fstem = os.path.splitext(fname)[0].lower()
                # Strip language suffix like .en before comparing  e.g. "Show.S01E01.en" -> "Show.S01E01"
                fstem_clean = re.sub(r'\.[a-z]{2,3}$', '', fstem)
                if fstem == stem or fstem_clean == stem:
                    return os.path.join(sub_dir, fname)
        return ""


    def _launch_mpv(self, path, start=0):
        if not os.path.exists(path):
            QMessageBox.warning(self, "Not Found", f"File not found:\n{path}"); return

        filename = os.path.basename(path)

        mod, _ = matrix_mod()
        # Use the immediate parent folder name as the show title
        show = os.path.basename(os.path.dirname(path)) or filename

        # Extract season/episode from filename
        season, episode = 0, 0
        try:
            from gs_episode_tracker import get_episode_number as _get_ep
            episode = _get_ep(path)
        except Exception:
            pass
        if episode == 0:
            if mod and hasattr(mod, "MediaPlayer"):
                try: season, episode = mod.MediaPlayer._extract_season_episode(filename)
                except Exception as e:
                    log.warning("Operation failed", error=str(e), location="_launch_mpv")
            else:
                m = re.search(r'[Ss](\d{1,2})[Ee](\d{1,4})', filename)
                if m: season, episode = int(m.group(1)), int(m.group(2))
        # Also try to get season from existing method if episode was found by tracker
        if episode > 0 and season == 0:
            if mod and hasattr(mod, "MediaPlayer"):
                try: season, _ = mod.MediaPlayer._extract_season_episode(filename)
                except Exception: season = 1
            else:
                m = re.search(r'[Ss](\d{1,2})[Ee]', filename)
                season = int(m.group(1)) if m else 1

        # Count total episodes in folder and find this file's position
        total_eps  = 0
        file_index = 0
        if mod and hasattr(mod, "MediaPlayer"):
            try: total_eps = mod.MediaPlayer.count_episodes_in_folder(path)
            except Exception as e:
                log.warning("Operation failed", error=str(e), location="_launch_mpv")
        else:
            try:
                exts = ('.mp4','.mkv','.avi','.mov','.wmv','.flv','.webm','.m4v')
                immediate = os.path.dirname(path)
                all_files = sorted(
                    f for f in os.listdir(immediate)
                    if os.path.splitext(f)[1].lower() in set(exts)
                )
                total_eps = len(all_files)
                filename  = os.path.basename(path)
                try:    file_index = all_files.index(filename) + 1
                except ValueError: file_index = 0
            except Exception as e:
                log.warning("Operation failed", error=str(e), location="_launch_mpv")
        md = matrix_data()
        existing = md.get("watching", {}).get(show, {})

        entry = {
            "title":            show,
            "file_path":        path,
            "position":         start,
            "duration":         existing.get("duration", 0),
            "last_watched":     time.time(),
            "current_season":   season if season > 0 else existing.get("current_season", 1),
            "current_episode":  episode if episode > 0 else existing.get("current_episode", 0),
            "total_episodes":   total_eps if total_eps > 0 else existing.get("total_episodes", 0),
            "file_index":       file_index if file_index > 0 else existing.get("file_index", 0),
            "episodes_watched": existing.get("episodes_watched", []),
            "is_anime":         existing.get("is_anime", False),
        }
        # Record this episode as watched
        ep_key = [season, episode]
        if episode > 0 and ep_key not in entry["episodes_watched"]:
            entry["episodes_watched"].append(ep_key)

        md.setdefault("watching", {})[show] = entry
        save_json(MATRIX_PROGRESS, md)
        self.refresh()  # show in list right away
        self._cloud_push()  # sync progress to cloud

        log.mpv.info("Launching mpv", show=show, file=os.path.basename(path),
                     season=season, episode=episode, start=start)
        self.now_lbl.setText(f"Playing: {filename}")

        if self._mpv_process and self._mpv_process.poll() is None:
            try:
                self._mpv_process.terminate()
                self._mpv_process.wait(timeout=2)
            except Exception:
                try: self._mpv_process.kill()
                except Exception as e:
                    log.warning("Operation failed", error=str(e), location="_launch_mpv")
        self._mpv_process = None
        try: os.remove(MPV_SOCKET_PATH)
        except Exception as e:
            log.warning("Operation failed", error=str(e), location="_launch_mpv")

        t = threading.Thread(
            target=self._play_loop,
            args=(path, start, show),
            daemon=False
        )
        self._play_thread = t
        t.start()

    def _play_loop(self, path, start, show):
        import socket as _socket, json as _json

        LUA_SCRIPT  = str(SCRIPT_DIR / "next_episode.lua")

        current   = path
        cur_start = start

        # Auto-move to Watching when playback begins
        def _ensure_watching(show_name):
            if not show_name: return
            try:
                md = matrix_data()
                wl = md.setdefault("watchlist", {})
                for k in ("planning","watching","dropped","completed"):
                    wl.setdefault(k, [])
                # Check if already in watching
                in_watching = any(
                    (e.get("title","") if isinstance(e,dict) else str(e)).lower() == show_name.lower()
                    for e in wl["watching"]
                )
                if in_watching: return
                # Remove from planning (only — don't touch completed/dropped)
                wl["planning"] = [
                    e for e in wl["planning"]
                    if (e.get("title","") if isinstance(e,dict) else str(e)).lower() != show_name.lower()
                ]
                # Add to watching if not already there
                wl["watching"].append({
                    "title": show_name, "is_anime": False,
                    "added": time.time(), "watched": False,
                    "notes": "Auto-added when playback started",
                    "updated_at": _wl_now()
                })
                save_json(MATRIX_PROGRESS, md)
                QTimer.singleShot(0, self.refresh)
            except Exception as e:
                log.warning("Operation failed", error=str(e), location="_play_loop")
        _ensure_watching(show)
        self._request_cloud_push.emit()  # sync planning→watching immediately (thread-safe)

        while current:
            mod, _ = matrix_mod()

            # Find next episode
            cur_next = None
            if mod and hasattr(mod, "MediaPlayer"):
                try: cur_next = mod.MediaPlayer.find_next_episode(current)
                except Exception as e:
                    log.warning("Operation failed", error=str(e), location="_play_loop")

            # Show next episode status in the UI label so it's visible
            next_label = f"Next: {os.path.basename(cur_next)}" if cur_next else "Next: not found"
            QTimer.singleShot(0, lambda l=next_label: self.now_lbl.setText(l))

            # Build mpv command
            try: os.remove(MPV_SOCKET_PATH)
            except Exception as e:
                log.warning("Operation failed", error=str(e), location="_play_loop")

            cmd = [
                "mpv",
                f"--input-ipc-server={MPV_SOCKET_PATH}",
                "--really-quiet",
                "--fullscreen",
                "--keep-open=no",
                "--no-resume-playback",   # never let mpv's watch-later cache override our position
                "--osd-level=1",
                "--osd-playing-msg=",
                "--osd-duration=0",
                f"--script-opts=next_episode-has_next={'yes' if cur_next else 'no'}",
            ]
            if os.path.exists(LUA_SCRIPT):
                cmd += [f"--script={LUA_SCRIPT}"]
            if cur_start > 10:
                cmd.append(f"--start={int(cur_start)}")
            # Auto-load subtitle from ~/Subtitles/ if a matching file exists
            sub_path = self._find_subtitle(current)
            if sub_path:
                cmd.append(f"--sub-file={sub_path}")
            cmd.append(current)

            try:
                process = subprocess.Popen(cmd)
                self._mpv_process = process
                log.mpv.debug("mpv process started", pid=process.pid, file=os.path.basename(current))
            except FileNotFoundError:
                log.mpv.error("mpv not found — not installed")
                QTimer.singleShot(0, lambda: QMessageBox.critical(
                    self, "mpv not found",
                    "mpv is not installed.\nInstall with:  sudo pacman -S mpv"))
                return

            # Wait for IPC socket
            socket_ready = False
            for _ in range(40):
                if os.path.exists(MPV_SOCKET_PATH):
                    socket_ready = True
                    break
                time.sleep(0.1)
            if not socket_ready:
                log.mpv.warning("IPC socket did not appear in time", socket=MPV_SOCKET_PATH)

            def _ipc(cmd_dict):
                try:
                    with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
                        s.settimeout(1.0)
                        s.connect(MPV_SOCKET_PATH)
                        s.sendall((_json.dumps(cmd_dict) + "\n").encode())
                        data = b""
                        while True:
                            chunk = s.recv(4096)
                            if not chunk: break
                            data += chunk
                            if b"\n" in data: break
                        return _json.loads(data.split(b"\n")[0])
                except Exception:
                    return None

            last_pos      = float(cur_start)
            last_save     = 0.0
            duration      = 0.0
            next_confirmed = cur_next is not None  # whether we've confirmed next exists
            play_next_triggered = False
            play_session  = 0  # incremented on every play-next
            _file_switching = False  # True while waiting for mpv to confirm new file loaded
            _expected_file  = os.path.basename(current)

            # ── OP/ED chapter auto-skip state ────────────────────────────────
            _last_chapter_idx   = -1   # last chapter index seen; -1 = not yet read
            _ed_skip_armed      = False
            _ed_skip_at         = 0.0  # wall-clock time to fire next-episode

            while process.poll() is None:
                # Check if Lua flagged "play next" via user-data property
                flag = _ipc({"command": ["get_property", "user-data/gs-next"]})
                if flag and flag.get("data") == "yes":
                    _ipc({"command": ["set_property", "user-data/gs-next", "no"]})
                    if not play_next_triggered and cur_next and os.path.exists(cur_next):
                        play_next_triggered = True
                        # Zero out position SYNCHRONOUSLY before loadfile so the
                        # position-save loop cannot race and overwrite with old pos
                        try:
                            _next = cur_next
                            md = matrix_data()
                            watching = md.get("watching", {})
                            # Match on the CURRENT file (not _next) — the entry still
                            # has the old file_path at this point in time.
                            # Case-insensitive title match, then file_path match
                            target_key = show if show in watching else next(
                                (k for k in watching if k.lower() == show.lower()), None)
                            if target_key is None:
                                target_key = next(
                                    (k for k, v in watching.items()
                                     if isinstance(v, dict) and v.get("file_path") == current), None)
                            if target_key:
                                watching[target_key]["position"]     = 0
                                watching[target_key]["file_path"]    = _next
                                watching[target_key]["last_watched"] = time.time()
                                save_json(MATRIX_PROGRESS, md)
                        except Exception as e:
                            log.warning("Operation failed", error=str(e), location="_play_loop")
                        current   = cur_next
                        cur_start = 0
                        last_pos  = 0.0
                        duration  = 0.0
                        last_save    = time.time() + 5  # inhibit position save for 5s
                        play_session += 1               # invalidate any in-flight _save threads
                        _file_switching = True          # block position reads until new file confirmed
                        _expected_file  = os.path.basename(cur_next)
                        # Now load next file into the same mpv window
                        # Pass start=0 explicitly so mpv begins from the beginning,
                        # since --no-resume-playback only applies at launch, not to loadfile.
                        _ipc({"command": ["loadfile", cur_next, "replace", 0, "start=0"]})
                        # Find the next-next episode
                        mod_n, _ = matrix_mod()
                        cur_next = None
                        if mod_n and hasattr(mod_n, "MediaPlayer"):
                            try: cur_next = mod_n.MediaPlayer.find_next_episode(current)
                            except Exception as e:
                                log.warning("Operation failed", error=str(e), location="_play_loop")
                        next_confirmed = cur_next is not None
                        play_next_triggered = False
                        # Update the Lua script's has_next flag
                        _ipc({"command": ["script-message", "next-episode-has-next",
                                          "yes" if cur_next else "no"]})
                        
                        genre = _detect_show_genre(show)
                        track_event("episode_finished", {"show": show, "genre": genre})

                        # Update episode number in progress.json when advancing
                        try:
                            from gs_episode_tracker import get_episode_number as _get_ep
                            new_ep = _get_ep(current)
                            if new_ep > 0:
                                _md = matrix_data()
                                _w  = _md.get("watching", {})
                                if show in _w:
                                    _w[show]["current_episode"] = new_ep
                                    save_json(MATRIX_PROGRESS, _md)
                        except Exception:
                            pass
                        self._request_cloud_push.emit()  # always push on episode advance (thread-safe)

                # Get position — skip reads while mpv is switching files to avoid
                # capturing a stale position from the previous episode
                if _file_switching:
                    fn_resp = _ipc({"command": ["get_property", "filename"]})
                    reported = fn_resp.get("data", "") if fn_resp else ""
                    # Match on basename in case mpv returns full path
                    if reported and os.path.basename(reported) == _expected_file:
                        _file_switching = False  # new file confirmed, resume normal tracking
                    elif reported and reported != os.path.basename(current):
                        # mpv is already on a different file — unblock regardless
                        _file_switching = False
                else:
                    resp = _ipc({"command": ["get_property", "time-pos"]})
                    if resp and resp.get("error") == "success" and resp.get("data") is not None:
                        last_pos = float(resp["data"])

                # Get duration once (only when not switching files)
                if duration == 0 and not _file_switching:
                    dr = _ipc({"command": ["get_property", "duration"]})
                    if dr and dr.get("error") == "success" and dr.get("data"):
                        duration = float(dr["data"])

                # ── Position tracking ─────────────────────────────────────────
                # In-memory state updates every 3s (for accurate position reads).
                # Disk writes are throttled to every 30s to avoid fd exhaustion.
                # track_event watch_time is accumulated and flushed every 60s.
                now = time.time()
                if last_pos > 0 and not _file_switching and now - last_save >= 3:
                    last_save = now
                    pos_snap  = last_pos
                    dur_snap  = duration
                    fname     = current

                    # Accumulate watch-time — flush to behaviour log every 60s
                    _watch_time_accum = getattr(self, "_watch_time_accum", 0.0)
                    _watch_time_last_flush = getattr(self, "_watch_time_last_flush", now)
                    _watch_time_accum += 0.05   # ~3s per tick → 0.05 minutes
                    if now - _watch_time_last_flush >= 60:
                        track_event("watch_time", {"minutes": _watch_time_accum})
                        _watch_time_accum      = 0.0
                        _watch_time_last_flush = now
                    self._watch_time_accum      = _watch_time_accum
                    self._watch_time_last_flush = _watch_time_last_flush

                    # Only write to disk every 30s — in-memory position is always current
                    _last_disk_save = getattr(self, "_play_last_disk_save", 0.0)
                    if now - _last_disk_save >= 30:
                        self._play_last_disk_save = now
                        def _save(sk=show, p=pos_snap, d=dur_snap, f=fname):
                            if p <= 0: return
                            md = matrix_data()
                            watching = md.get("watching", {})
                            target_key = sk if sk in watching else next(
                                (k for k, v in watching.items()
                                 if isinstance(v, dict) and v.get("file_path") == f), None)
                            if target_key:
                                watching[target_key]["position"]     = p
                                watching[target_key]["duration"]     = d
                                watching[target_key]["file_path"]    = f
                                watching[target_key]["last_watched"] = time.time()
                                save_json(MATRIX_PROGRESS, md)
                            QTimer.singleShot(0, self.refresh)
                        threading.Thread(target=_save, daemon=False).start()

                # Re-check for next episode once we're past 85%
                # (handles case where cur_next was None at launch but file appeared)
                if not next_confirmed and duration > 0 and last_pos / duration > 0.85:
                    mod2, _ = matrix_mod()
                    if mod2 and hasattr(mod2, "MediaPlayer"):
                        try:
                            late_next = mod2.MediaPlayer.find_next_episode(current)
                            if late_next:
                                cur_next       = late_next
                                next_confirmed = True
                                # Tell the Lua script there IS a next episode
                                _ipc({"command": ["script-message",
                                                  "next-episode-has-next", "yes"]})
                        except Exception:
                            pass

                # ── OP/ED chapter auto-skip ───────────────────────────────────
                # Only run when not switching files (stale data during transitions)
                if not _file_switching:
                    ch_resp = _ipc({"command": ["get_property", "chapter"]})
                    if ch_resp and ch_resp.get("error") == "success":
                        ch_idx = ch_resp.get("data", -1)
                        if ch_idx is not None and ch_idx != _last_chapter_idx and ch_idx >= 0:
                            _last_chapter_idx = ch_idx
                            # Reset ED arm when chapter changes (new episode or chapter skip)
                            _ed_skip_armed = False
                            cl_resp = _ipc({"command": ["get_property", "chapter-list"]})
                            if cl_resp and cl_resp.get("error") == "success":
                                chapters = cl_resp.get("data", [])
                                if ch_idx < len(chapters):
                                    ch_title = chapters[ch_idx].get("title", "").lower()
                                    # OP / Intro → skip to next chapter immediately
                                    if re.search(r'\b(op|opening|intro)\b', ch_title):
                                        next_ch = ch_idx + 1
                                        if next_ch < len(chapters):
                                            skip_to = chapters[next_ch].get("time", None)
                                            if skip_to is not None:
                                                _ipc({"command": ["set_property", "time-pos", skip_to]})
                                                _ipc({"command": ["show-text", "⏭ Skipped intro", 2000]})
                                                log.debug("Auto-skipped OP chapter", chapter=ch_title)
                                        else:
                                            # OP is last chapter — skip to end
                                            _ipc({"command": ["add", "chapter", 1]})
                                    # ED / Ending / Credits → arm next-episode after 3s
                                    elif re.search(r'\b(ed|ending|credits|outro)\b', ch_title):
                                        if not _ed_skip_armed:
                                            _ed_skip_armed = True
                                            _ed_skip_at    = time.time() + 3.0
                                            _ipc({"command": ["show-text", "▶ Next episode in 3s…", 3000]})
                                            log.debug("ED chapter armed", chapter=ch_title)

                    # Fire the ED skip if the countdown expired
                    if _ed_skip_armed and time.time() >= _ed_skip_at:
                        _ed_skip_armed = False
                        _ipc({"command": ["set_property", "user-data/gs-next", "yes"]})
                # ── End OP/ED auto-skip ───────────────────────────────────────

                def _fmt(s):
                    s = int(s); return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"
                pos_str = _fmt(last_pos) if last_pos > 0 else "00:00:00"
                dur_str = _fmt(duration) if duration > 0 else "--:--:--"
                fname   = os.path.basename(current)
                label   = f"Playing: {fname}  {pos_str}/{dur_str}"
                if cur_next:
                    label += f"  |  Next: {os.path.basename(cur_next)}"
                QTimer.singleShot(0, lambda l=label: self.now_lbl.setText(l))

                time.sleep(0.5)

            exit_code = process.wait()

            # Final save with duration
            md = matrix_data()
            watching = md.get("watching", {})
            target_key = show if show in watching else next(
                (k for k, v in watching.items()
                 if isinstance(v, dict) and v.get("file_path") == current), None)
            if target_key:
                # If play_next just fired and we have no real position yet,
                # save 0 so the next resume starts from the beginning
                save_pos = last_pos if not play_next_triggered else min(last_pos, 5.0)
                watching[target_key]["position"]     = save_pos
                watching[target_key]["duration"]     = duration
                watching[target_key]["file_path"]    = current
                watching[target_key]["last_watched"] = time.time()
                save_json(MATRIX_PROGRESS, md)
            QTimer.singleShot(0, self.refresh)

            # If IPC already handled play-next (loadfile into same window),
            # mpv will exit normally (code 0) when user quits — don't launch again
            if play_next_triggered and exit_code != 10:
                QTimer.singleShot(0, lambda: self.now_lbl.setText(""))
                break

            # Exit 10 = Play Next accepted via exit (fallback for non-IPC)
            if exit_code == 10:
                genre = _detect_show_genre(show)
                track_event("episode_finished", {"show": show, "genre": genre})
            if exit_code == 10 and cur_next and os.path.exists(cur_next):
                next_filename = os.path.basename(cur_next)

                # Update episode info for the new file
                season, episode = 0, 0
                if mod and hasattr(mod, "MediaPlayer"):
                    try: season, episode = mod.MediaPlayer._extract_season_episode(next_filename)
                    except Exception as e:
                        log.warning("Operation failed", error=str(e), location="_play_loop")
                else:
                    m = re.search(r'[Ss](\d{1,2})[Ee](\d{1,4})', next_filename)
                    if m: season, episode = int(m.group(1)), int(m.group(2))

                md = matrix_data()
                if show in md.get("watching", {}):
                    if season  > 0: md["watching"][show]["current_season"]  = season
                    if episode > 0: md["watching"][show]["current_episode"] = episode
                    md["watching"][show]["file_path"] = cur_next
                    md["watching"][show]["position"]  = 0
                    ep_key = [season, episode]
                    watched = md["watching"][show].get("episodes_watched", [])
                    if episode > 0 and ep_key not in watched:
                        watched.append(ep_key)
                        md["watching"][show]["episodes_watched"] = watched
                    save_json(MATRIX_PROGRESS, md)

                QTimer.singleShot(0, lambda nf=next_filename: self.now_lbl.setText(
                    f"Loading next: {nf}..."))
                current   = cur_next
                cur_start = 0
                continue
            else:
                QTimer.singleShot(0, lambda: self.now_lbl.setText(""))
                # No next episode and mpv closed — check if series is finished
                if not cur_next:
                    self._auto_complete_show(show, current, duration, last_pos)
                break

    def _auto_complete_show(self, show, last_file, duration, last_pos):
        """Move show to Completed if it finished its last episode (>85% watched)."""
        if not show: return
        if 'duration' not in locals(): duration = 0
        if 'last_pos' not in locals(): last_pos = 0
        if duration > 0 and last_pos / duration < 0.80:
            return  # didn't finish — user quit early
        try:
            md  = matrix_data()
            wl  = md.setdefault("watchlist", {})
            for k in ("planning", "watching", "completed", "dropped"):
                wl.setdefault(k, [])

            show_lower = show.lower()

            # Already completed? Don't re-add
            in_completed = any(
                (e.get("title","") if isinstance(e,dict) else str(e)).lower() == show_lower
                for e in wl["completed"]
            )
            if in_completed:
                return

            # Look for entry in watchlist["watching"] list
            wl_entry = next(
                (e for e in wl["watching"]
                 if (e.get("title","") if isinstance(e,dict) else str(e)).lower() == show_lower),
                None)

            # Also check md["watching"] dict (shows played directly via file browser)
            dict_entry = md.get("watching", {}).get(show) or next(
                (v for v in md.get("watching", {}).values()
                 if isinstance(v, dict) and v.get("title","").lower() == show_lower),
                None)

            if not wl_entry and not dict_entry:
                return  # not tracked at all — don't auto-complete unknown shows

            # Remove from watchlist["watching"] if present
            wl["watching"] = [
                e for e in wl["watching"]
                if (e.get("title","") if isinstance(e,dict) else str(e)).lower() != show_lower
            ]

            # Build completed entry
            base = wl_entry if isinstance(wl_entry, dict) else {}
            completed_entry = {**base, "title": show,
                               "completed_date": time.time(), "auto_completed": True}
            wl["completed"].append(completed_entry)
            save_json(MATRIX_PROGRESS, md)
            QTimer.singleShot(0, self.refresh)
            QTimer.singleShot(0, lambda: self.now_lbl.setText(
                f"✅ '{show}' moved to Completed"))
            self._request_cloud_push.emit()  # auto-complete push (thread-safe)
        except Exception as e:
            log.warning("auto_complete error", error=str(e))

    def _build_stream_placeholder(self) -> QWidget:
        """Lightweight stand-in shown until the user first opens STREAM.
        Avoids paying for QWebEngineView + ad-blocker init at launch."""
        ph = QWidget()
        phv = QVBoxLayout(ph)
        phv.setAlignment(Qt.AlignmentFlag.AlignCenter)

        spinner = QLabel("◐")
        spinner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spinner.setStyleSheet(f"font-size:32px; color:{ACCENT};")

        msg = QLabel("Loading stream…")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setStyleSheet(f"color:{MUTED}; font-size:12px; letter-spacing:0.5px;")

        phv.addWidget(spinner); phv.addSpacing(10); phv.addWidget(msg)

        frames = ["◐", "◓", "◑", "◒"]
        state  = {"i": 0}
        timer  = QTimer(ph)
        def _tick():
            state["i"] = (state["i"] + 1) % len(frames)
            spinner.setText(frames[state["i"]])
        timer.timeout.connect(_tick)
        timer.start(120)
        # Keep references so the timer isn't garbage-collected and so we
        # can stop it cleanly once the real tab replaces this placeholder.
        ph._spinner_timer = timer

        return ph

    def _on_matrix_tab_changed(self, idx: int):
        if idx == self._stream_tab_index:
            self._ensure_stream_built()

    def _ensure_stream_built(self, on_ready=None):
        """Build the real STREAM tab on first use and swap it in for the
        placeholder. Safe to call repeatedly — no-ops (beyond queuing the
        callback) once a build has been kicked off.

        on_ready: optional callback invoked once the real tab exists —
        either immediately (already built) or after the deferred build
        completes (not built yet). Lets callers like _resume() chain work
        that needs self._stream_view without racing the deferred build.
        """
        if self._stream_built:
            if on_ready:
                on_ready()
            return
        if on_ready:
            self._stream_ready_callbacks.append(on_ready)
        if getattr(self, "_stream_build_pending", False):
            return
        self._stream_build_pending = True
        # Defer the actual (blocking) construction to the next event-loop
        # tick. Without this, _build_stream() runs synchronously in the
        # same call as the tab switch and Qt never gets a chance to paint
        # the spinner before the UI freezes for the build duration.
        QTimer.singleShot(0, self._do_build_stream)

    def _do_build_stream(self):
        self._stream_built = True
        self._stream_build_pending = False
        real = self._build_stream()
        spinner_timer = getattr(self._stream_tab, "_spinner_timer", None)
        if spinner_timer:
            spinner_timer.stop()
        self._tabs.removeTab(self._stream_tab_index)
        self._tabs.insertTab(self._stream_tab_index, real, "STREAM")
        self._tabs.setCurrentIndex(self._stream_tab_index)
        self._stream_tab = real
        callbacks, self._stream_ready_callbacks = self._stream_ready_callbacks, []
        for cb in callbacks:
            try:
                cb()
            except Exception as e:
                log.warning("stream ready callback failed", error=str(e))

    def _build_stream(self):
        """STREAM tab — AnimeKai + YouTube browser with landing page, ad blocking,
           persistent login, real-time episode tracking and full Matrix integration."""
        w = QWidget()
        wv = QVBoxLayout(w)
        wv.setContentsMargins(0,0,0,0)
        wv.setSpacing(0)

        if not WEBENGINE_OK:
            ph = QWidget()
            phv = QVBoxLayout(ph)
            phv.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ico = QLabel("📺"); ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ico.setStyleSheet("font-size:48px;")
            msg = QLabel("QtWebEngine not installed.\n\nRun:  sudo apt install python3-pyqt6.qtwebengine")
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            msg.setStyleSheet(f"color:{MUTED}; font-size:13px;")
            phv.addWidget(ico); phv.addSpacing(12); phv.addWidget(msg)
            wv.addWidget(ph); return w

        # ── Persistent profile (cookies survive restarts) ──────────────────────
        profile_path = str(Path.home() / ".great_sage_stream_profile")
        self._stream_profile = QWebEngineProfile("great_sage_stream")
        self._stream_profile.setPersistentStoragePath(profile_path)
        self._stream_profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        # Set a real browser UA so sites don't reject us
        self._stream_profile.setHttpUserAgent(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

        # ── Ad blocker (gs_adblock.py) ─────────────────────────────────────────
        # Installs network interceptor + cosmetic CSS script into the profile.
        self._adblock = _get_adblock_manager()
        self._adblock.install(self._stream_profile)

        # ── Web view ───────────────────────────────────────────────────────────
        self._stream_view = QWebEngineView()
        # Set background to black to avoid white flashes during initial load
        self._stream_view.setStyleSheet("background-color: #000000;")

        # ── Custom page (blocks popups, suppresses JS console noise) ──────────
        # We pass the view as parent so the page can access it for fullscreen
        self._stream_page = self._adblock.make_page(self._stream_profile, self._stream_view)
        self._stream_view.setPage(self._stream_page)

        # Wire fullscreen request from the page into our handler
        self._stream_page.fullScreenRequested.connect(self._on_fullscreen_requested)

        # Enable fullscreen and media capabilities on the view's settings too
        vs = self._stream_view.settings()
        vs.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        vs.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        vs.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)

        # ── Navigation bar ────────────────────────────────────────────────────
        nav_bar = QWidget()
        nav_bar.setStyleSheet(f"background:{BG2}; border-bottom:1px solid {BORDER};")
        nav_bar.setFixedHeight(48)
        nv = QHBoxLayout(nav_bar)
        nv.setContentsMargins(10,0,10,0)
        nv.setSpacing(5)

        def _nav_btn(text, tip=""):
            b = QPushButton(text)
            b.setFixedSize(32, 30)
            b.setToolTip(tip)
            b.setStyleSheet(
                f"background:transparent; border:1px solid {BORDER}; color:{MUTED};"
                f"font-size:15px; border-radius:3px; padding:2px 4px;")
            return b

        self._stream_back   = _nav_btn("←", "Back")
        self._stream_fwd    = _nav_btn("→", "Forward")
        self._stream_reload = _nav_btn("↻", "Reload")

        self._stream_back.clicked.connect(self._stream_view.back)
        self._stream_fwd.clicked.connect(self._stream_view.forward)
        self._stream_reload.clicked.connect(self._stream_view.reload)

        self._stream_url_bar = QLineEdit()
        self._stream_url_bar.setPlaceholderText("https://animekai.be")
        self._stream_url_bar.setStyleSheet(
            f"background:{BG3}; border:1px solid {BORDER}; color:{TEXT};"
            f"font-size:12px; padding:4px 12px; border-radius:3px;")
        self._stream_url_bar.returnPressed.connect(self._stream_navigate)

        self._stream_landing_btn = QPushButton("⌂  HOME")
        self._stream_landing_btn.setFixedHeight(30)
        self._stream_landing_btn.setToolTip("Return to StreamGate landing page")
        self._stream_landing_btn.setStyleSheet(
            f"background:{ACCENT}; border:none; color:#fff;"
            f"font-size:11px; font-weight:bold; letter-spacing:1px; padding:5px 16px; border-radius:4px;")
        self._stream_landing_btn.clicked.connect(
            lambda: self._stream_show_landing())

        nv.addWidget(self._stream_back)
        nv.addWidget(self._stream_fwd)
        nv.addWidget(self._stream_reload)
        nv.addWidget(self._stream_url_bar, 1)
        nv.addWidget(self._stream_landing_btn)
        wv.addWidget(nav_bar)
        self._stream_nav_bar = nav_bar

        # ── Loading progress bar (thin, below nav) ─────────────────────────────
        self._stream_progress = QProgressBar()
        self._stream_progress.setRange(0, 100)
        self._stream_progress.setFixedHeight(2)
        self._stream_progress.setTextVisible(False)
        self._stream_progress.setStyleSheet(
            f"QProgressBar{{background:{BG2}; border:none;}}"
            f"QProgressBar::chunk{{background:{ACCENT};}}")
        self._stream_progress.hide()
        wv.addWidget(self._stream_progress)

        wv.addWidget(self._stream_view, 1)

        # ── Now-watching bar (bottom) ───────────────────────────────────────────
        self._now_watch_bar = QWidget()
        self._now_watch_bar.setStyleSheet(
            f"background:{BG2}; border-top:1px solid {BORDER};")
        self._now_watch_bar.setFixedHeight(42)
        self._now_watch_bar.hide()
        nwv = QHBoxLayout(self._now_watch_bar)
        nwv.setContentsMargins(16,0,16,0); nwv.setSpacing(10)

        self._now_watch_dot = QLabel("●")
        self._now_watch_dot.setStyleSheet(f"color:{ACCENT2}; font-size:9px;")

        self._now_watch_lbl = QLabel("")
        self._now_watch_lbl.setStyleSheet(
            f"color:{TEXT}; font-size:13px; font-family:{FONT_UI};")

        self._now_watch_ep_badge = QLabel("")
        self._now_watch_ep_badge.setStyleSheet(
            f"background:{ACCENT2}; color:#000; font-size:9px; font-weight:bold;"
            f"padding:3px 8px; border-radius:3px; letter-spacing:1px;")
        self._now_watch_ep_badge.hide()

        self._now_watch_mark = QPushButton("✓  MARK WATCHED")
        self._now_watch_mark.setStyleSheet(
            f"background:transparent; border:1px solid {ACCENT2}; color:{ACCENT2};"
            f"font-size:9px; letter-spacing:1px; padding:5px 14px; border-radius:3px;")
        self._now_watch_mark.setToolTip("Manually confirm this episode as watched")
        self._now_watch_mark.clicked.connect(self._manual_mark_watched)

        self._now_watch_wl_btn = QPushButton("+ WATCHLIST")
        self._now_watch_wl_btn.setStyleSheet(
            f"background:transparent; border:1px solid {BORDER}; color:{MUTED};"
            f"font-size:9px; letter-spacing:1px; padding:5px 14px; border-radius:3px;")
        self._now_watch_wl_btn.setToolTip("Add this show to your Watchlist")
        self._now_watch_wl_btn.clicked.connect(self._stream_add_to_watchlist)

        nwv.addWidget(self._now_watch_dot)
        nwv.addWidget(self._now_watch_lbl, 1)
        nwv.addWidget(self._now_watch_ep_badge)
        nwv.addWidget(self._now_watch_mark)
        nwv.addWidget(self._now_watch_wl_btn)
        wv.addWidget(self._now_watch_bar)

        # ── Signals ────────────────────────────────────────────────────────────
        self._stream_view.urlChanged.connect(self._on_stream_url_changed)
        self._stream_page.titleChanged.connect(self._on_stream_title_changed)
        self._stream_view.loadStarted.connect(self._on_stream_load_started)
        self._stream_view.loadProgress.connect(self._on_stream_load_progress)
        self._stream_view.loadFinished.connect(self._on_stream_load_finished)

        # State
        self._stream_current_show = ""
        self._stream_current_ep   = 0
        self._stream_last_tracked = ("", 0)
        self._stream_logged_in    = False
        self._stream_fullscreen   = False

        # Escape (caught via eventFilter so it doesn't interfere with other
        # keys like space/arrows reaching the player) exits our fullscreen.
        self._stream_view.installEventFilter(self)

        # ── Landing page ───────────────────────────────────────────────────────
        self._stream_show_landing()
        return w

    def eventFilter(self, obj, event):
        if obj is getattr(self, "_stream_view", None) \
                and getattr(self, "_stream_fullscreen", False) \
                and event.type() == QEvent.Type.KeyPress \
                and event.key() == Qt.Key.Key_Escape:
            self._exit_stream_fullscreen()
            return True
        return super().eventFilter(obj, event)

    def _on_fullscreen_requested(self, request):
        """Handle fullscreen requests from the web page."""
        request.accept()
        if request.toggleOn():
            self._enter_stream_fullscreen()
        else:
            self._exit_stream_fullscreen()

    def _enter_stream_fullscreen(self):
        """Hide all surrounding chrome and make the stream view cover the
        whole window — entered when the page's player requests fullscreen."""
        if self._stream_fullscreen:
            return
        self._stream_fullscreen = True
        self._stream_nav_bar.hide()
        self._stream_progress.hide()
        self._now_watch_bar.hide()
        if hasattr(self, "_tabs"):
            self._tabs.tabBar().hide()
        # Hide the main window nav rail
        win = self._stream_view.window()
        nav_rail = win.findChild(QWidget, "NavRail")
        if nav_rail is None:
            # fallback: look for any widget named _nav_rail
            for child in win.findChildren(QWidget):
                if child.__class__.__name__ == "NavRail":
                    nav_rail = child
                    break
        if nav_rail:
            nav_rail.hide()
            self._stream_hidden_nav_rail = nav_rail
        else:
            self._stream_hidden_nav_rail = None
        win.showFullScreen()
        self._stream_view.setFocus()

    def _exit_stream_fullscreen(self):
        """Restore chrome and exit our window-level fullscreen. Also tells
        the page's own player to leave HTML fullscreen if it's still in it."""
        if not self._stream_fullscreen:
            return
        self._stream_fullscreen = False
        if hasattr(self, "_tabs"):
            self._tabs.tabBar().show()
        self._stream_nav_bar.show()
        if self._now_watch_lbl.text():
            self._now_watch_bar.show()
        # Restore nav rail
        if getattr(self, "_stream_hidden_nav_rail", None):
            self._stream_hidden_nav_rail.show()
            self._stream_hidden_nav_rail = None
        win = self._stream_view.window()
        win.showNormal()
        # Ask the page to drop out of its own HTML5 fullscreen too
        self._stream_page.runJavaScript(
            "if (document.fullscreenElement) { document.exitFullscreen(); }")

    def _stream_show_landing(self):
        """Show the StreamGate landing page in the web view."""
        html = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, 'Inter', sans-serif;
    background: #13111a;
    color: #e8e0f0;
    min-height: 100vh;
  }
  .nav {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 28px;
    height: 52px;
    background: #1a1825;
    border-bottom: 1px solid #2e2a3a;
    position: sticky;
    top: 0;
    z-index: 10;
  }
  .nav-logo {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 16px;
    font-weight: 600;
    color: #f0ecff;
    letter-spacing: -0.01em;
  }
  .logo-dot {
    width: 24px; height: 24px;
    background: linear-gradient(135deg, #7c6af0, #e0407b);
    border-radius: 50%;
    flex-shrink: 0;
  }

  .hero {
    padding: 64px 36px 56px;
    background: #13111a;
    position: relative;
    overflow: hidden;
  }
  .hero::before {
    content: '';
    position: absolute; inset: 0;
    background:
      radial-gradient(ellipse at 75% 40%, rgba(124,106,240,0.13) 0%, transparent 55%),
      radial-gradient(ellipse at 25% 85%, rgba(224,64,123,0.09) 0%, transparent 50%);
    pointer-events: none;
  }
  .hero-title {
    font-size: 46px; font-weight: 700;
    line-height: 1.12; letter-spacing: -0.025em;
    color: #f0ecff; max-width: 580px;
    margin-bottom: 18px; position: relative;
  }
  .accent-purple { color: #9d8ff5; }
  .accent-pink { color: #e06090; }
  .hero-sub {
    font-size: 14px; color: #8a82a0;
    max-width: 460px; line-height: 1.65;
    position: relative;
  }
  .section {
    padding: 44px 36px;
    border-top: 1px solid #2e2a3a;
  }
  .eyebrow {
    font-size: 10px; letter-spacing: 0.14em;
    color: #6a6282; margin-bottom: 8px;
    text-transform: uppercase;
  }
  .section-title {
    font-size: 26px; font-weight: 700;
    color: #f0ecff; letter-spacing: -0.015em;
    margin-bottom: 28px;
  }
  .platform-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 18px;
  }
  .platform-card {
    background: #1a1825;
    border: 1px solid #2e2a3a;
    border-radius: 14px; padding: 24px;
    transition: border-color 0.15s;
    cursor: pointer;
    text-decoration: none;
  }
  .platform-card:hover { border-color: #5a5278; }
  .p-icon {
    width: 42px; height: 42px; border-radius: 12px;
    display: flex; align-items: center; justify-content: center;
    font-size: 20px; margin-bottom: 16px;
  }
  .p-icon-a { background: rgba(124,106,240,0.18); }
  .p-icon-y { background: rgba(224,37,27,0.16); }
  .p-name {
    font-size: 17px; font-weight: 600;
    color: #f0ecff; margin-bottom: 10px;
  }
  .p-desc {
    font-size: 12px; color: #8a82a0;
    line-height: 1.65; margin-bottom: 16px;
  }
  .tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }
  .tag {
    font-size: 10px; padding: 3px 9px;
    border-radius: 10px; background: #2a2638;
    color: #9e96b8; border: 1px solid #3a3450;
    letter-spacing: 0.03em;
  }
  .p-link {
    font-size: 12px; font-weight: 500;
    display: inline-flex; align-items: center; gap: 5px;
    text-decoration: none; cursor: pointer;
    background: none; border: none;
  }
  .p-link-a { color: #9d8ff5; }
  .p-link-y { color: #f08080; }
  .trending-header {
    display: flex; align-items: flex-end;
    justify-content: space-between; margin-bottom: 22px;
  }
  .cards-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 14px;
  }
  .card {
    background: #1a1825;
    border: 1px solid #2e2a3a;
    border-radius: 12px; overflow: hidden;
    cursor: pointer;
    transition: border-color 0.15s, transform 0.15s;
    text-decoration: none;
  }
  .card:hover { border-color: #5a5278; transform: translateY(-3px); }
  .card-thumb {
    width: 100%; height: 100px;
    display: flex; align-items: center;
    justify-content: center; font-size: 32px;
    position: relative;
  }
  .badge {
    position: absolute; top: 8px; left: 8px;
    font-size: 9px; padding: 3px 8px;
    border-radius: 8px; font-weight: 700;
    letter-spacing: 0.05em;
  }
  .src-tag {
    position: absolute; top: 8px; right: 8px;
    font-size: 9px; padding: 3px 8px;
    border-radius: 8px; letter-spacing: 0.04em;
  }
  .badge-trending { background: #7c6af0; color: #fff; }
  .badge-viral { background: #e0251b; color: #fff; }
  .badge-new { background: #1a7a4a; color: #a0f0c0; }
  .src-a { background: rgba(124,106,240,0.28); color: #b5aaff; }
  .src-y { background: rgba(224,37,27,0.25); color: #ff9898; }
  .card-body { padding: 12px 14px; }
  .card-title { font-size: 12px; font-weight: 500; color: #e8e0f0; margin-bottom: 5px; }
  .card-meta { display: flex; justify-content: space-between; align-items: center; }
  .card-type { font-size: 10px; color: #6a6282; }
  .card-rating { font-size: 10px; color: #e0a020; }
</style>
</head>
<body>
<div class="nav">
  <div class="nav-logo">
    <div class="logo-dot"></div>
    StreamGate
  </div>

</div>

<div class="hero">
  <div class="hero-title">
    One place for<br>
    <span class="accent-purple">everything you</span><br>
    <span class="accent-pink">watch.</span>
  </div>
  <p class="hero-sub">
    Thousands of anime titles on AnimeKai. Billions of videos on YouTube.
    Two of the internet's best streaming destinations — right here.
  </p>
</div>

<div class="section">
  <div class="eyebrow">The Platforms</div>
  <div class="section-title">Two giants. One gateway.</div>
  <div class="platform-grid">
    <div class="platform-card" onclick="window.location='https://animekai.be'">
      <div class="p-icon p-icon-a">📺</div>
      <div class="p-name">AnimeKai</div>
      <p class="p-desc">A dedicated anime streaming platform with 12,000+ series — from classic shonen to the latest seasonal drops. Subbed and dubbed. No filler.</p>
      <div class="tags">
        <span class="tag">12,000+ titles</span>
        <span class="tag">Sub &amp; Dub</span>
        <span class="tag">Daily updates</span>
        <span class="tag">HD quality</span>
      </div>
      <button class="p-link p-link-a">Visit AnimeKai ↗</button>
    </div>
    <div class="platform-card" onclick="window.location='https://www.youtube.com'">
      <div class="p-icon p-icon-y">▶️</div>
      <div class="p-name">YouTube</div>
      <p class="p-desc">The world's largest video platform. Creators, live streams, podcasts, tutorials, music, documentaries — if it's been filmed, it's on YouTube.</p>
      <div class="tags">
        <span class="tag">800M+ videos</span>
        <span class="tag">Live streaming</span>
        <span class="tag">YouTube Music</span>
        <span class="tag">Free to watch</span>
      </div>
      <button class="p-link p-link-y">Visit YouTube ↗</button>
    </div>
  </div>
</div>

<div class="section">
  <div class="trending-header">
    <div>
      <div class="eyebrow">What's on</div>
      <div class="section-title" style="margin-bottom:0">Trending Across Both</div>
    </div>
  </div>
  <div class="cards-grid">
    <div class="card" onclick="window.location='https://animekai.be/watch/solo-leveling-season-2-arise-from-the-shadow'">
      <div class="card-thumb" style="background:#1f1630">
        <span>⚔️</span>
        <span class="badge badge-trending">Trending</span>
        <span class="src-tag src-a">AnimeKai</span>
      </div>
      <div class="card-body">
        <div class="card-title">Solo Leveling</div>
        <div class="card-meta">
          <span class="card-type">Anime Series</span>
          <span class="card-rating">★ 9.3</span>
        </div>
      </div>
    </div>
    <div class="card" onclick="window.location='https://www.youtube.com/results?search_query=MrBeast'">
      <div class="card-thumb" style="background:#1a1520">
        <span>🎬</span>
        <span class="badge badge-viral">Viral</span>
        <span class="src-tag src-y">YouTube</span>
      </div>
      <div class="card-body">
        <div class="card-title">MrBeast: $1M Island</div>
        <div class="card-meta">
          <span class="card-type">YouTube Original</span>
          <span class="card-rating">★ 9.1</span>
        </div>
      </div>
    </div>
    <div class="card" onclick="window.location='https://animekai.be/watch/jujutsu-kaisen-0'">
      <div class="card-thumb" style="background:#1a1228">
        <span>🥋</span>
        <span class="badge badge-new">New</span>
        <span class="src-tag src-a">AnimeKai</span>
      </div>
      <div class="card-body">
        <div class="card-title">Jujutsu Kaisen S3</div>
        <div class="card-meta">
          <span class="card-type">Anime Series</span>
          <span class="card-rating">★ 9.4</span>
        </div>
      </div>
    </div>
  </div>
</div>

</body>
</html>
"""
        self._stream_view.setHtml(html, QUrl("https://animekai.be"))

    # ── Stream navigation ──────────────────────────────────────────────────────
    def _stream_navigate(self):
        url = self._stream_url_bar.text().strip()
        if not url.startswith("http"):
            url = "https://" + url
        self._stream_view.load(QUrl(url))

    def _on_stream_load_started(self):
        self._stream_progress.setValue(0)
        self._stream_progress.show()

    def _on_stream_load_progress(self, pct):
        self._stream_progress.setValue(pct)

    def _on_stream_url_changed(self, url):
        url_str = url.toString()
        self._stream_url_bar.setText(url_str)
        self._parse_animekai_url(url_str)
        if "animekai" in url_str.lower():
            url_lower = url_str.lower()
            if "/watch/" in url_lower:
                if not getattr(self, "_scrape_timer_pending", False):
                    self._scrape_timer_pending = True
                    QTimer.singleShot(3000, self._run_animetsu_scrape)

    def _on_stream_title_changed(self, title):
        self._parse_animekai_title(title)

    # Sites where the popup killer should NOT run — their own JS handles layout
    _POPUP_KILLER_SKIP = (
        "youtube.com", "youtu.be", "google.com", "google.",
        "twitch.tv", "netflix.com", "crunchyroll.com",
    )

    def _on_stream_load_finished(self, ok):
        self._stream_progress.hide()
        self._stream_back.setEnabled(self._stream_view.history().canGoBack())
        self._stream_fwd.setEnabled(self._stream_view.history().canGoForward())
        url = self._stream_view.url().toString().lower()
        # Only inject popup killer on sites that actually have intrusive overlays
        # Skip trusted sites where it causes layout/scroll interference
        if not any(s in url for s in self._POPUP_KILLER_SKIP):
            self._stream_page.runJavaScript(self._adblock.popup_killer_js())
        if "animekai" in url and "/watch/" in url:
            QTimer.singleShot(2000, self._run_animetsu_scrape)

    def _check_login_status(self):
        """Detect if user is logged into AnimeKai by checking page content."""
        url = self._stream_view.url().toString()
        if "animekai" not in url.lower(): return
        js = """
        (function() {
            // AnimeKai shows a profile image/avatar when logged in
            // Check for avatar img, username text, or any user-specific nav elements
            var loggedIn = !!(
                document.querySelector('img.avatar, .header-actions img, ' +
                    '.nav-username, .user-dropdown, .profile-icon, ' +
                    '.header .dropdown img[src*="avatar"], ' +
                    '.header .dropdown img[src*="user"], ' +
                    'a[href*="/user/"], a[href*="/profile"], ' +
                    '.account-menu, .user-menu') ||
                // Look for any img in the top-right area that's not the logo
                (function() {
                    var imgs = document.querySelectorAll('header img, nav img, .header img')
                    for (var i = 0
                    i < imgs.length
                    i++) {
                        var src = imgs[i].src || ''
                        if (src.includes('avatar') || src.includes('user') ||
                            src.includes('profile') || src.includes('gravatar') ||
                            imgs[i].alt === 'avatar' || imgs[i].className.includes('avatar')) {
                            return true;
                        }
                    }
                    return false;
                })() ||
                // Check for username text anywhere in header/nav
                (function() {
                    var nav = document.querySelector('header, nav, .header, .navbar')
                    if (!nav) return false;
                    var text = nav.textContent || ''
                    // If there's a non-generic username (not just menu items)
                    return !!(
                        nav.querySelector('[class*="username"], [class*="user-name"], [class*="account"]')
                    );
                })()
            );
            // Also check: if page has a logout link, user is definitely logged in
            if (!loggedIn) {
                loggedIn = !!(document.querySelector('a[href*="logout"], a[href*="sign-out"], ' +
                    'button[data-action*="logout"]'));
            }
            return loggedIn;
        })();
        """
        self._stream_page.runJavaScript(js, 0, self._on_login_check)

    def _on_login_check(self, logged_in):
        if bool(logged_in):
            pass  # login indicator removed
        else:
            pass  # login indicator removed

    # ── Episode detection ──────────────────────────────────────────────────────
    def _parse_animekai_url(self, url: str):
        """Parse AnimeKai URL patterns:
           animekai.be/watch/{slug}/ep-{num}
           animekai.be/watch/{slug}#ep={num}
           animekai.be/watch/{slug}?ep={num}
        """
        if "animekai" not in url.lower():
            return
        # Pattern 1: /watch/slug/ep-12
        m = re.search(r'/watch/([^/#?]+)/ep-(\d+)', url)
        if m:
            slug = m.group(1)
            ep = int(m.group(2))
            title = slug.replace('-', ' ').title()
            self._update_now_watching(title, ep); return
        # Pattern 2: /watch/slug#ep=12 or /watch/slug?ep=12
        m = re.search(r'/watch/([^#?/]+)[#?]ep[=:](\d+)', url)
        if m:
            slug = m.group(1)
            ep = int(m.group(2))
            title = slug.replace('-', ' ').title()
            self._update_now_watching(title, ep)

    def _parse_animekai_title(self, title: str):
        """Parse AnimeKai page title variations:
           'Solo Leveling Episode 12 - AnimeKai'
           'Watch Solo Leveling Ep 12 Online'
        """
        if not title: return
        t_lower = title.lower()
        if 'animekai' not in t_lower and 'watch' not in t_lower and 'episode' not in t_lower:
            return
        _JUNK_TITLES = (
            'recently added', 'latest episodes', 'popular anime',
            'new release', 'home page', 'search results', 'genre',
            'schedule', 'top anime', 'trending', 'bookmark', 
        )
        if any(junk in t_lower for junk in _JUNK_TITLES):
            return
        patterns = [
            r'^(?:watch\s+)?(.+?)\s+[Ee]p(?:isode)?[\s.#]*(\d+)',
            r'^(.+?)\s*[-–]\s*[Ee]p(?:isode)?[\s.#]*(\d+)',
            r'^(.+?)\s+(\d+)\s*[-–]',
        ]
        for pat in patterns:
            m = re.search(pat, title, re.IGNORECASE)
            if m:
                show = m.group(1).strip()
                show = re.sub(r'\s*[-|]\s*(AnimeKai|Watch Online|HD|Sub|Dub).*$',
                               '', show, flags=re.IGNORECASE).strip()
                ep = int(m.group(2))
                if show and ep > 0:
                    self._update_now_watching(show, ep)
                    return

    def _run_animetsu_scrape(self):
        """Called via QTimer after page load to give SPA time to render."""
        self._scrape_timer_pending = False
        url = self._stream_view.url().toString().lower()
        if "/watch/" in url and "animekai" in url:
            self._stream_page.runJavaScript(
                self._animetsu_scrape_js(), 0, self._on_animetsu_scraped)

    def _animetsu_scrape_js(self) -> str:
        """JS injected on AnimeKai watch pages to extract series title + episode number."""
        return """
(function() {
    // AnimeKai URL: /watch/{slug}/ep-{num}
    var ep = 0;
    var urlMatch = window.location.href.match(/\/ep-(\d+)/i);
    if (urlMatch) ep = parseInt(urlMatch[1], 10);
    if (!ep) {
        urlMatch = window.location.href.match(/[?#&]ep[=:](\d+)/i);
        if (urlMatch) ep = parseInt(urlMatch[1], 10);
    }

    var seriesTitle = '';

    // 1. Page heading
    var h = document.querySelector('h1, h2, .film-name, .anime-name');
    if (h) {
        var t = (h.textContent || '').trim();
        if (t.length >= 3) seriesTitle = t;
    }

    // 2. og:title meta
    if (!seriesTitle) {
        var og = document.querySelector('meta[property="og:title"]');
        if (og) seriesTitle = (og.getAttribute('content') || '').trim();
    }

    // 3. Page title fallback
    if (!seriesTitle) {
        seriesTitle = document.title
            .replace(/\s*[Ee]pisode\s*\d+.*$/, '')
            .replace(/\s*[|\-]\s*AnimeKai.*$/i, '')
            .replace(/\s*[Ee]nglish\s*(Sub|Dub).*$/i, '')
            .trim();
    }

    return seriesTitle ? {title: seriesTitle, ep: ep || 1} : null;
})();
"""
    def _on_animetsu_scraped(self, result):
        """Callback from the DOM scraper JS — result is {title, ep} or None."""
        if not result or not isinstance(result, dict):
            return
        title = (result.get('title') or '').strip()
        # Strip trailing user counts like "48.3K users" or "8.3K users"
        import re as _re
        title = _re.sub(r'[\s\d.,]+K?\s*users?\s*$', '', title, flags=_re.IGNORECASE).strip()
        ep    = int(result.get('ep') or 1)
        if title and len(title) >= 3:
            self._update_now_watching(title, ep)

    def _update_now_watching(self, title: str, ep: int):
        if not title or len(title) < 2: return
        title = title.strip()
        # Block raw UUIDs/hex slugs (no spaces, all hex chars, long)
        import re as _re
        if _re.fullmatch(r'[0-9a-fA-F]{8,}', title.replace(' ', '')):
            return
        self._stream_current_show = title
        self._stream_current_ep   = ep
        # Update now-watching bar
        self._now_watch_lbl.setText(f"Now watching:  {title}")
        self._now_watch_ep_badge.setText(f"EP {ep}")
        self._now_watch_ep_badge.show()
        self._now_watch_bar.show()
        # Auto-track only on new episode (avoid hammering disk on every URL event)
        if (title.lower(), ep) != self._stream_last_tracked:
            self._stream_last_tracked = (title.lower(), ep)
            threading.Thread(
                target=self._auto_track, args=(title, ep), daemon=True
            ).start()

    @staticmethod
    def _norm_title(t: str) -> str:
        """Normalize a title for fuzzy matching — lowercase, collapse all
        punctuation/whitespace to single spaces. This lets slug-derived
        titles ('Solo Leveling Season 2 Arise From The Shadow') and
        page-heading titles ('Solo Leveling Season 2: Arise from the
        Shadow') match as the same show."""
        return re.sub(r'[^a-z0-9]+', ' ', t.lower()).strip()

    def _auto_track(self, title: str, ep: int):
        """Write episode progress to matrix_progress.json + auto-move lists."""
        if not hasattr(self, '_track_lock'):
            self._track_lock = threading.Lock()
        try:
            with self._track_lock:
                md = matrix_data()
                watching  = md.setdefault("watching", {})
                wl        = md.setdefault("watchlist", {})
                planning  = wl.setdefault("planning", [])
                wl_watching = wl.setdefault("watching", [])

                norm_title = self._norm_title(title)

                # Fuzzy key match in watching dict (normalized comparison so
                # minor punctuation/case differences between scrape sources
                # don't create duplicate entries for the same show)
                key = None
                for k in watching:
                    kt = watching[k].get("title", k) if isinstance(watching[k], dict) else k
                    if self._norm_title(kt) == norm_title:
                        key = k
                        break

                if key is None:
                    key = title
                    already_in_wl_watching = any(
                        self._norm_title(x.get("title","") if isinstance(x,dict) else str(x)) == norm_title
                        for x in wl_watching
                    )
                    if not already_in_wl_watching:
                        # Try to move from planning → wl["watching"] first
                        new_planning = []
                        found_in_planning = False
                        for e in planning:
                            t = e.get("title", "") if isinstance(e, dict) else str(e)
                            if self._norm_title(t) == norm_title:
                                found_in_planning = True
                                entry = e if isinstance(e, dict) else {"title": t}
                                entry["is_anime"] = True
                                entry["updated_at"] = _wl_now()
                                wl_watching.append(entry)
                            else:
                                new_planning.append(e)
                        if found_in_planning:
                            wl["planning"] = new_planning
                        else:
                            # Brand-new show not in any list — add to wl["watching"]
                            # so it appears in the Watchlist tab and syncs to cloud.
                            wl_watching.append({
                                "title":      title,
                                "is_anime":   True,
                                "added":      int(time.time()),
                                "watched":    False,
                                "notes":      "Auto-added from STREAM tab",
                                "updated_at": _wl_now(),
                            })
                            _sync_item_added(title, "Anime", "Watching")

                # Update or create watching dict entry (Continue Watching)
                now = int(time.time())
                if isinstance(watching.get(key), dict):
                    old_ep = watching[key].get("current_episode", 0)
                    watching[key]["current_episode"] = max(ep, old_ep)
                    if len(title) > len(watching[key].get("title", "")):
                        watching[key]["title"] = title
                    watching[key]["last_watched"] = now
                    watching[key]["source"]       = watching[key].get("source", "animekai")
                    eps_watched = watching[key].setdefault("episodes_watched", [])
                    if ep not in eps_watched:
                        eps_watched.append(ep)
                        eps_watched.sort()
                else:
                    watching[key] = {
                        "title":            title,
                        "current_episode":  ep,
                        "episodes_watched": [ep],
                        "source":           "animekai",
                        "is_anime":         True,
                        "started":          now,
                        "last_watched":     now,
                    }
                save_json(MATRIX_PROGRESS, md)
            # Refresh UI and push to cloud
            QTimer.singleShot(0, self.refresh)
            QTimer.singleShot(0, self._cloud_push)
        except Exception as e:
            log.warning("Operation failed", error=str(e), location="_auto_track")

    def _manual_mark_watched(self):
        if self._stream_current_show:
            threading.Thread(
                target=self._auto_track,
                args=(self._stream_current_show, self._stream_current_ep),
                daemon=True
            ).start()
            self._now_watch_mark.setText("✓  SAVED")
            QTimer.singleShot(2000, lambda: self._now_watch_mark.setText("✓  MARK WATCHED"))

    def _stream_add_to_watchlist(self):
        """Add currently-detected show to watchlist planning list."""
        if not self._stream_current_show: return
        title = self._stream_current_show
        md = matrix_data()
        wl = md.setdefault("watchlist", {})
        for lst in ("planning", "watching", "completed", "dropped"):
            for e in wl.get(lst, []):
                t = e.get("title","") if isinstance(e,dict) else str(e)
                if t.lower() == title.lower():
                    self._now_watch_wl_btn.setText("✓ ALREADY ADDED")
                    QTimer.singleShot(2000, lambda: self._now_watch_wl_btn.setText("+ WATCHLIST"))
                    return
        wl.setdefault("planning", []).append({
            "title": title, "is_anime": True,
            "added": int(time.time()), "watched": False,
            "notes": "Added from STREAM tab", "updated_at": _wl_now()
        })
        save_json(MATRIX_PROGRESS, md)
        _sync_item_added(title, "Anime", "Planning")
        self.refresh()
        self._now_watch_wl_btn.setText("✓ ADDED")
        QTimer.singleShot(2000, lambda: self._now_watch_wl_btn.setText("+ WATCHLIST"))

    # ── AnimeKai watch history sync ────────────────────────────────────────────
    def _animekai_sync(self):
        """Sync AnimeKai watch history from browser localStorage into Matrix."""
        if not WEBENGINE_OK or not hasattr(self, "_stream_page"): return
        pass  # sync btn removed
        

        # AnimeKai stores watch progress in localStorage, not a server endpoint.
        # We read it directly from the embedded browser's storage.
        js = r"""
        (function() {
            var results = []
            try {
                // Scan all localStorage keys for AnimeKai watch data
                for (var i = 0
                i < localStorage.length
                i++) {
                    var key = localStorage.key(i)
                    var val = localStorage.getItem(key)
                    if (!val) continue;
                    try {
                        var data = JSON.parse(val)
                        // AnimeKai stores episode progress as objects with ep/episode keys
                        if (data && typeof data === 'object') {
                            // Format: {title: "...", ep: 12} or {episode: 12, ...}
                            var title = data.title || data.name || data.anime_title || null
                            var ep = data.ep || data.episode || data.current_ep ||
                                     data.currentEpisode || data.last_ep || 0;
                            if (title && title.length > 1) {
                                results.push({title: title, ep: parseInt(ep) || 0, src: 'localStorage:' + key});
                            }
                        }
                    } catch(e) {}
                    // Also check raw string keys that look like episode markers
                    // e.g. key="solo-leveling" val="12"
                    if (!isNaN(parseInt(val)) && key.length > 3 && !key.startsWith('_')) {
                        var cleanTitle = key.replace(/-/g, ' ').replace(/_/g, ' ')
                        // Only if it looks like a title (letters, not a UUID/hash)
                        if (/^[a-zA-Z]/.test(key) && key.length < 80) {
                            results.push({title: cleanTitle, ep: parseInt(val) || 0, src: 'localStorage:' + key});
                        }
                    }
                }

                // Also check sessionStorage
                for (var j = 0
                j < sessionStorage.length
                j++) {
                    var skey = sessionStorage.key(j)
                    var sval = sessionStorage.getItem(skey)
                    if (!sval) continue;
                    try {
                        var sdata = JSON.parse(sval)
                        if (sdata && sdata.title) {
                            results.push({title: sdata.title,
                                ep: sdata.ep || sdata.episode || 0, src: 'sessionStorage'});
                        }
                    } catch(e) {}
                }

                // Also try AnimeKai's Continue Watching section on current page
                var cwItems = document.querySelectorAll(
                    '.continue-watching .item, .cw-item, [class*="continue"] .item, ' +
                    '.recently-watched .item, .watch-history .item'
                );
                cwItems.forEach(function(el) {
                    var titleEl = el.querySelector('.film-name, .name, a[title], h3, h4')
                    var epEl = el.querySelector('[class*="ep"], .tick-eps, [data-ep]');
                    var title = titleEl ? (titleEl.getAttribute('title') || titleEl.textContent.trim()) : null
                    var ep = epEl ? parseInt(epEl.textContent.replace(/\D/g,'')) || 0 : 0
                    if (title && title.length > 1)
                        results.push({title: title, ep: ep, src: 'dom'});
                });

                // Deduplicate by title, keep highest ep
                var map = {}
                results.forEach(function(r) {
                    var k = r.title.toLowerCase().trim()
                    if (!map[k] || r.ep > map[k].ep) map[k] = r
                });
                return JSON.stringify(Object.values(map));
            } catch(e) {
                return JSON.stringify([]);
            }
        })();
        """
        # Run on current page (no navigation needed)
        self._stream_page.runJavaScript(js, 0, self._on_sync_result)

    def _on_sync_result(self, result):
        import json as _json
        
        try:
            items = _json.loads(result or "[]")
            if not items:
                
                QMessageBox.information(self, "Sync",
                    "No watch history found in browser storage.\n\n"
                    "Watch some episodes on AnimeKai first — progress is\n"
                    "saved locally as you watch and will sync automatically.")
                return

            md = matrix_data()
            watching = md.setdefault("watching", {})
            wl       = md.setdefault("watchlist", {})
            synced = 0
            new_shows = 0
            now = int(time.time())

            for item in items:
                title = item.get("title", "").strip()
                ep    = int(item.get("ep", 0))
                if not title or len(title) < 2: continue

                # Fuzzy key match
                key = None
                for k in watching:
                    kt = watching[k].get("title", k) if isinstance(watching[k], dict) else k
                    if kt.lower() == title.lower():
                        key = k
                        break

                if key is None:
                    key = title
                    new_shows += 1
                    # Add to watchlist watching if not already there
                    wl_w = wl.setdefault("watching", [])
                    if not any(
                        (e.get("title","") if isinstance(e,dict) else str(e)).lower() == title.lower()
                        for e in wl_w
                    ):
                        wl_w.append({"title": title, "is_anime": True,
                                     "added": now, "watched": False,
                                     "notes": "Synced from AnimeKai history"})

                if isinstance(watching.get(key), dict):
                    old_ep = watching[key].get("current_episode", 0)
                    if ep > old_ep:
                        watching[key]["current_episode"] = ep
                        watching[key]["title"]           = title
                        watching[key]["last_watched"]    = now
                        synced += 1
                else:
                    watching[key] = {
                        "title":            title,
                        "current_episode":  ep,
                        "episodes_watched": [ep] if ep > 0 else [],
                        "source":           "animekai_sync",
                        "is_anime":         True,
                        "started":          now,
                        "last_watched":     now,
                    }
                    synced += 1

            if synced > 0 or new_shows > 0:
                save_json(MATRIX_PROGRESS, md)
                QTimer.singleShot(0, self.refresh)

            summary = f"✓  {synced} updated"
            if new_shows > 0: summary += f" · {new_shows} new"
            # Sync complete — no UI button to update

            # No return URL stored; simply stay on current page
            pass

        except Exception as e:
            log.warning("Sync failed", error=str(e), location="_animekai_sync")

    def refresh(self):
        md = matrix_data(); wl = md.get("watchlist",{})
        for lst, lw in self.wl_lists.items():
            lw.clear()
            for e in wl.get(lst,[]):
                t  = e.get("title","?") if isinstance(e,dict) else str(e)
                t  = _strip_markdown(_clean_media_title(t))
                an = " [Anime]" if isinstance(e,dict) and e.get("is_anime") else ""
                item = QListWidgetItem(f"  {t}{an}")
                item.setData(Qt.ItemDataRole.UserRole, e)
                item.setForeground(QColor(TEXT)); lw.addItem(item)
        self.cw_list.clear()

        def _fmt(s):
            s = int(s)
            h = s // 3600
            m = (s % 3600) // 60
            sc = s % 60
            return f"{h}:{m:02d}:{sc:02d}" if h else f"{m:02d}:{sc:02d}"

        # ── Collect all continue-watching entries (local + AnimeKai) ──────────
        entries = []  # list of (last_watched_ts, display_label, user_role_data, row_height)

        # 1) Local MPV entries (AnimeKai/stream entries handled in section 2)
        for key, info in md.get("watching", {}).items():
            if not isinstance(info, dict): continue
            if info.get("source") == "animekai":
                continue
            t    = info.get("title", key)
            pos  = info.get("position", 0)
            dur  = info.get("duration", 0)
            ep   = info.get("current_episode", 0)
            seas = info.get("current_season", 1)
            tot  = info.get("total_episodes", 0)
            fidx = info.get("file_index", 0)
            ts   = info.get("last_watched", 0)

            if (fidx == 0 or tot == 0) and info.get("file_path"):
                try:
                    fp    = info["file_path"]
                    exts  = ('.mp4','.mkv','.avi','.mov','.wmv','.flv','.webm','.m4v')
                    folder = os.path.dirname(fp)
                    files  = sorted(f for f in os.listdir(folder) if f.lower().endswith(exts))
                    tot   = len(files)
                    name  = os.path.basename(fp)
                    fidx  = files.index(name) + 1 if name in files else 0
                except Exception:
                    pass

            ep_badge = ""
            # Suppress episode badge for single-file entries (movies)
            _is_movie = (tot == 1)  # single file in folder = movie, suppress S/E badge
            if ep > 0 and not _is_movie:
                ep_badge = f"   ·   S{seas:02d}E{ep:02d}"
                if tot > 0:
                    ep_badge += f"  ({ep}/{tot})"

            time_line = ""
            if pos > 0:
                time_line = f"  {_fmt(pos)}"
                if dur > 0:
                    time_line += f" / {_fmt(dur)}"
                    pct    = min(pos / dur, 1.0)
                    filled = int(pct * 22)
                    bar    = "█" * filled + "░" * (22 - filled)
                    time_line += f"   [{bar}]  {int(pct*100)}%"

            label = f"  📁 {t}{ep_badge}"
            if time_line:
                label += f"\n{time_line}"

            entries.append((ts, label, info, 52 if time_line else 36))

        # 2) AnimeKai / stream entries — sourced from 'watching' entries with
        # source == "animekai", plus the legacy 'stream_watching' key.
        for key, info in md.get("watching", {}).items():
            if not isinstance(info, dict): continue
            if info.get("source") != "animekai": continue
            title = info.get("title", key)
            ep  = info.get("current_episode", 0)
            ts  = info.get("last_watched", 0)
            eps_list = info.get("episodes_watched", [])
            ep_badge = f"   ·   EP {ep}" if ep else ""
            if eps_list:
                ep_badge += f"  ({len(eps_list)} watched)"
            label = f"  🌐 {title}{ep_badge}\n  AnimeKai"
            role_data = {"title": title, "source": "stream", "episode": ep,
                         "last_watched": ts}
            entries.append((ts, label, role_data, 52))

        stream_watching = md.get("stream_watching", {})
        for title, info in stream_watching.items():
            if not isinstance(info, dict): continue
            ep  = info.get("current_episode", 0)
            ts  = info.get("last_watched", 0)
            eps_list = info.get("episodes_watched", [])
            ep_badge = f"   ·   EP {ep}" if ep else ""
            if eps_list:
                ep_badge += f"  ({len(eps_list)} watched)"
            label = f"  🌐 {title}{ep_badge}\n  AnimeKai"
            # Store enough for _resume to know it's a stream entry
            role_data = {"title": title, "source": "stream", "episode": ep,
                         "last_watched": ts}
            entries.append((ts, label, role_data, 52))

        # Sort: most recently watched first
        entries.sort(key=lambda x: x[0], reverse=True)

        for ts, label, role_data, height in entries:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, role_data)
            item.setForeground(QColor(TEXT))
            item.setSizeHint(QSize(0, height))
            self.cw_list.addItem(item)


# ═══════════════════════════════════════════════════════════════════════════════
# SAGE
# ═══════════════════════════════════════════════════════════════════════════════

class AddToWLDialog(QDialog):
    def __init__(self, text, parent=None):
        super().__init__(parent); self.setWindowTitle("Add to Watchlist")
        self.setMinimumSize(460,340); self.setStyleSheet(QSS)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16,16,16,16)
        lay.setSpacing(8)
        lay.addWidget(lbl("Select titles to add:",TEXT2))
        titles = re.findall(r'\d+\.\s+(.+?)(?:\s+[-\u2014]|\n|$)', text)
        if not titles:
            titles = [l.strip().lstrip("0123456789.-) ") for l in text.split("\n")
                      if 3 < len(l.strip().lstrip("0123456789.-) ")) < 80]
        titles = list(dict.fromkeys(titles))[:15]
        self.checks = []
        for t in titles:
            cb = QCheckBox(t); cb.setChecked(True); cb.setStyleSheet(f"color:{TEXT};")
            lay.addWidget(cb); self.checks.append((cb,t))
        row = QHBoxLayout()
        self.target = QComboBox()
        for n in ("planning","watching","dropped","completed"): self.target.addItem(n.capitalize(),n)
        row.addWidget(lbl("Add to:",TEXT2)); row.addWidget(self.target); row.addStretch()
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._do); bb.rejected.connect(self.reject); lay.addWidget(bb)

    def _do(self):
        lst = self.target.currentData()
        md = matrix_data()
        wl  = md.setdefault("watchlist",{})
        for k in ("planning","watching","dropped","completed"): wl.setdefault(k,[])
        added = 0
        for cb, title in self.checks:
            if not cb.isChecked(): continue
            exists = any((e.get("title","") if isinstance(e,dict) else str(e)).lower() == title.lower()
                         for l in wl.values() for e in l)
            if not exists:
                wl[lst].append({"title":title,"watched":False,"added":time.time(),"notes":"Added from Sage","updated_at":_wl_now()})
                added += 1
        save_json(MATRIX_PROGRESS, md)
        QMessageBox.information(self,"Done",f"Added {added} title(s) to {lst.capitalize()}.")
        self.accept()


# ═══════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════

class _CalendarWorker(QThread):
    done = pyqtSignal(dict, str)  # {date_str: [(time, show, ep)]}

    HEADERS = {
        "Content-Type": "application/json",
        "Accept":       "application/json",
        "User-Agent":   "GreatSage/1.0 (personal media app)",
    }

    @staticmethod
    def _match_score(watchlist_title, show_name):
        """
        Match a watchlist title against a schedule entry.
        Rules (in order):
          1. Exact match (case-insensitive)
          2. Watchlist title is fully contained in show name (handles "Demon Slayer" vs
             "Demon Slayer: Kimetsu no Yaiba" or "Solo Leveling Season 2")
          3. Show name is fully contained in watchlist title
          4. Word overlap >= 70% of watchlist words (minimum 2 words, 3+ chars each)
        Single-word titles must exact-match or be contained to avoid false positives.
        """
        a = watchlist_title.lower().strip()
        b = show_name.lower().strip()
        if not a or not b or len(a) < 3: return False
        # Exact
        if a == b: return True
        words_a = [w for w in a.split() if len(w) > 2]
        words_b = set(w for w in b.split() if len(w) > 2)
        # Single-word titles: only match if exact (already handled above)
        if len(words_a) <= 1: return False
        # Multi-word: watchlist title must be a leading substring of show name
        # e.g. "Demon Slayer" matches "Demon Slayer: Kimetsu" but not the reverse loosely
        if b.startswith(a) or b.startswith(a.rstrip(":")):
            return True
        if a.startswith(b):
            return True
        # Word overlap — at least 70% of watchlist meaningful words appear in show name
        overlap = sum(1 for w in words_a if w in words_b) / len(words_a)
        return overlap >= 0.7

    def run(self):
        import urllib.request, urllib.parse
        import concurrent.futures

        md = matrix_data()
        wl = md.get("watchlist", {})
        all_titles = []
        for lst in wl.values():
            for e in lst:
                t = e.get("title","") if isinstance(e,dict) else str(e)
                if t: all_titles.append(t)
        for info in md.get("watching",{}).values():
            if isinstance(info,dict):
                t = info.get("title","")
                if t and t not in all_titles: all_titles.append(t)

        if not all_titles:
            self.done.emit({}, "Add shows to your watchlist first."); return

        now        = dt.datetime.now()
        week_later = now + dt.timedelta(days=7)
        by_date    = {}
        lock       = threading.Lock()
        anilist_ok = threading.Event()
        tvmaze_ok  = threading.Event()

        def fetch_anilist(title):
            q = """query($s:String){Media(search:$s,type:ANIME,status:RELEASING){
                title{romaji english}
                nextAiringEpisode{airingAt episode}}}"""
            try:
                payload = json.dumps({"query": q, "variables": {"s": title}}).encode()
                req = urllib.request.Request(
                    "https://graphql.anilist.co", data=payload, headers=self.HEADERS)
                with urllib.request.urlopen(req, timeout=8) as r:
                    resp = json.loads(r.read())
                anilist_ok.set()
                media = (resp.get("data") or {}).get("Media")
                if not media: return
                romaji  = (media.get("title") or {}).get("romaji","")
                english = (media.get("title") or {}).get("english","")
                if not (self._match_score(title, romaji) or
                        self._match_score(title, english)): return
                nae = media.get("nextAiringEpisode")
                if not nae: return
                at = dt.datetime.fromtimestamp(nae["airingAt"])
                if now <= at <= week_later:
                    date_key  = at.strftime("%Y-%m-%d")
                    show_name = english or romaji or title
                    with lock:
                        by_date.setdefault(date_key, []).append(
                            (at.strftime("%H:%M"), show_name, nae["episode"]))
            except Exception:
                pass

        def fetch_tvmaze(title):
            try:
                search_url = (f"https://api.tvmaze.com/singlesearch/shows"
                              f"?q={urllib.parse.quote(title)}&embed=nextepisode")
                req = urllib.request.Request(
                    search_url, headers={"User-Agent": self.HEADERS["User-Agent"]})
                with urllib.request.urlopen(req, timeout=8) as r:
                    show_data = json.loads(r.read())
                tvmaze_ok.set()
                show_name = show_data.get("name","")
                show_id   = show_data.get("id")
                if not show_name or not show_id: return
                if not self._match_score(title, show_name): return

                # Try nextepisode from embedded data first (fastest path)
                embedded = (show_data.get("_embedded") or {})
                next_ep  = embedded.get("nextepisode")
                if next_ep:
                    airdate = next_ep.get("airdate","")
                    airtime = next_ep.get("airtime","") or "00:00"
                    ep_num  = next_ep.get("number") or 0
                    if airdate:
                        try:
                            ep_dt = dt.datetime.strptime(airdate, "%Y-%m-%d")
                            if now.date() <= ep_dt.date() <= week_later.date():
                                with lock:
                                    by_date.setdefault(airdate, []).append(
                                        (airtime, show_name, ep_num))
                                return
                        except Exception:
                            pass

                # Fallback: scan last 20 episodes for upcoming ones
                ep_url = f"https://api.tvmaze.com/shows/{show_id}/episodes?specials=0"
                req2   = urllib.request.Request(
                    ep_url, headers={"User-Agent": self.HEADERS["User-Agent"]})
                with urllib.request.urlopen(req2, timeout=8) as r2:
                    all_eps = json.loads(r2.read())
                for ep in all_eps[-20:]:
                    airdate = ep.get("airdate","")
                    if not airdate: continue
                    try:
                        ep_dt = dt.datetime.strptime(airdate, "%Y-%m-%d")
                    except Exception: continue
                    if not (now.date() <= ep_dt.date() <= week_later.date()): continue
                    airtime = ep.get("airtime","") or "00:00"
                    ep_num  = ep.get("number") or 0
                    with lock:
                        by_date.setdefault(airdate, []).append(
                            (airtime, show_name, ep_num))
            except urllib.request.HTTPError as e:
                if e.code == 404: return
            except Exception:
                pass

        # Fetch all titles in parallel — AniList and TVMaze simultaneously
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            futures = []
            for title in all_titles:
                futures.append(pool.submit(fetch_anilist, title))
                futures.append(pool.submit(fetch_tvmaze,  title))
            # Wait for all with a hard timeout of 25s
            concurrent.futures.wait(futures, timeout=25)

        # Wait for events with a short timeout to confirm services responded
        anilist_ok.wait(0.5)
        tvmaze_ok.wait(0.5)

        # Deduplicate and sort each day
        for k in by_date:
            seen = set()
            unique = []
            for entry in sorted(by_date[k]):
                key = (entry[1], entry[2])  # show + ep
                if key not in seen:
                    seen.add(key); unique.append(entry)
            by_date[k] = unique

        total = sum(len(v) for v in by_date.values())
        sources = []
        if anilist_ok.is_set(): sources.append("Anime")
        if tvmaze_ok.is_set():  sources.append("TV")

        if total:
            day_word = "day" if len(by_date) == 1 else "days"
            src_str  = " + ".join(sources) if sources else ""
            suffix   = f"  [{src_str}]" if src_str else ""
            msg = f"Found episodes on {len(by_date)} {day_word} this week{suffix}"
        elif not sources:
            msg = "Could not connect — check your internet connection"
        else:
            msg = f"Checked {len(all_titles)} title(s) — none airing this week"

        self.done.emit(by_date, msg)




class HighlightsDialog(QDialog):
    """Currently reading + watching + planning at a glance."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Highlights")
        self.resize(820, 560)
        self.setStyleSheet(f"background:{BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(0,0,0,0)
        root.setSpacing(0)

        hdr = QWidget(); hdr.setStyleSheet(f"background:{BG2};border-bottom:1px solid {BORDER};")
        hdr.setFixedHeight(52)
        hv = QHBoxLayout(hdr)
        hv.setContentsMargins(28,0,28,0)
        tl = QLabel("HIGHLIGHTS")
        tl.setStyleSheet(f"font-family:{FONT_DISPLAY};font-size:13px;font-weight:bold;color:{ACCENT};letter-spacing:4px;")
        hv.addWidget(tl); hv.addStretch()
        root.addWidget(hdr)

        body = QHBoxLayout()
        body.setContentsMargins(20,16,20,16)
        body.setSpacing(12)

        ld = legion_data()
        md = matrix_data()
        books    = ld.get("books", {})
        watching = md.get("watching", {})
        wl       = md.get("watchlist", {})

        for col_title, color, items in [
            ("CURRENTLY READING",  ACCENT,  [
                (f"{n}  Ch.{b.get('current_chapter',0)}",
                 f"{b.get('chapters_read',0)} chapters read  ·  {b.get('words_read',0):,} words")
                for n, b in books.items() if b.get("chapters_read",0)>0 or b.get("current_chapter",0)>0
            ]),
            ("CONTINUE WATCHING",  BLUE,    [
                ((info.get("title", k) if isinstance(info,dict) else k),
                 f"Ep.{info.get('current_episode',0) if isinstance(info,dict) else 0}")
                for k, info in watching.items()
            ]),
            ("PLANNING",           PURPLE,  [
                (e.get("title","?") if isinstance(e,dict) else str(e), "")
                for e in wl.get("planning",[])[:20]
            ]),
        ]:
            col = QFrame()
            col.setStyleSheet(f"background:{BG2};border:1px solid {BORDER};border-radius:6px;")
            cv = QVBoxLayout(col)
            cv.setContentsMargins(0,0,0,0)
            cv.setSpacing(0)
            hdr2 = QWidget()
            hdr2.setStyleSheet(f"background:{BG3};border-bottom:1px solid {BORDER};border-radius:6px 6px 0 0;")
            hv2 = QHBoxLayout(hdr2)
            hv2.setContentsMargins(14,10,14,10)
            hl = QLabel(col_title); hl.setStyleSheet(f"color:{color};font-size:9px;letter-spacing:2px;")
            cnt = QLabel(str(len(items))); cnt.setStyleSheet(f"color:{MUTED};font-size:9px;")
            hv2.addWidget(hl); hv2.addStretch(); hv2.addWidget(cnt)
            cv.addWidget(hdr2)
            lst = QListWidget()
            lst.setStyleSheet(
                f"QListWidget{{background:transparent;border:none;padding:4px;}}"
                f"QListWidget::item{{color:{TEXT2};padding:8px 14px;border-bottom:1px solid {BG3};}}"
                f"QListWidget::item:hover{{color:{TEXT};background:{BG3};}}")
            for title, sub in (items or [("Nothing here yet", "")]):
                item = QListWidgetItem(title)
                if sub: item.setToolTip(sub)
                item.setForeground(QColor(TEXT2))
                lst.addItem(item)
            cv.addWidget(lst, 1)
            body.addWidget(col, 1)

        root.addLayout(body, 1)

        close_btn = QPushButton("CLOSE")
        close_btn.setStyleSheet(accent_btn_style())
        close_btn.setStyleSheet(
            f"background:{BG2};color:{MUTED};border:1px solid {BORDER};"
            f"font-size:9px;letter-spacing:1px;padding:8px 20px;border-radius:3px;margin:0 20px 14px 20px;")
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)


class CalendarDialog(QDialog):
    """Calendar grid — click a date to see what's airing."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📅  What\'s Airing This Week")
        self.setMinimumSize(700, 460); self.setStyleSheet(QSS)
        self._data = {}  # {date_str: [(time, show, ep)]}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16,16,16,16)
        lay.setSpacing(10)
        lay.addWidget(lbl("What\'s Airing This Week", ACCENT, 16, True))
        lay.addWidget(hline())

        # ── Calendar grid (7 day buttons) ─────────────────────────────────
        self._today = dt.datetime.now()
        grid = QHBoxLayout()
        grid.setSpacing(6)
        self._day_btns = {}
        for i in range(7):
            day    = self._today + dt.timedelta(days=i)
            date_s = day.strftime("%Y-%m-%d")
            label  = day.strftime("%a") + "\n" + day.strftime("%d")
            b      = QPushButton(label)
            b.setCheckable(True)
            b.setFixedSize(80, 54)
            b.setStyleSheet(
                f"QPushButton{{background:{BG3};border:1px solid {BORDER};"
                f"border-radius:8px;color:{TEXT};font-size:12px;}}"
                f"QPushButton:checked{{background:{ACCENT};color:{BG};"
                f"border-color:{ACCENT};font-weight:bold;}}"
                f"QPushButton:hover{{border-color:{ACCENT};}}")
            b.clicked.connect(lambda _, d=date_s: self._select_day(d))
            grid.addWidget(b)
            self._day_btns[date_s] = b
        grid.addStretch()
        lay.addLayout(grid)

        lay.addWidget(hline())

        # ── Episode list for selected day ──────────────────────────────────
        self._day_label = lbl("Select a day above", TEXT2, 13, True)
        lay.addWidget(self._day_label)
        self._list = QListWidget()
        self._list.setStyleSheet(f"background:{BG3};border:none;")
        lay.addWidget(self._list, 1)

        self._status = lbl("Fetching schedule...", MUTED, 11)
        lay.addWidget(self._status)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject); lay.addWidget(bb)

        # Select today by default, fetch data
        self._selected = self._today.strftime("%Y-%m-%d")
        self._data     = {}
        self._worker   = _CalendarWorker()
        self._worker.done.connect(self._on_data)
        self._worker.start()

        # Animate status while loading
        self._dots = 0
        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick_loading)
        self._anim.start(500)

        # Hard timeout — if worker takes > 30s, show partial results anyway
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._force_done)
        self._timeout.start(60000)

    def _tick_loading(self):
        self._dots = (self._dots + 1) % 4
        self._status.setText("Fetching schedule" + "." * (self._dots + 1))

    def _force_done(self):
        """Called if worker takes too long — show whatever we have."""
        if not self._data:
            self._status.setText("Timed out — check your internet connection.")
            self._list.clear()
            item = QListWidgetItem("  Could not load schedule in time. Try again.")
            item.setForeground(QColor(RED)); self._list.addItem(item)

    def _on_data(self, data, status_msg):
        self._data = data
        # Stop loading animation and timeout guard
        if hasattr(self, "_anim"):   self._anim.stop()
        if hasattr(self, "_timeout"): self._timeout.stop()
        self._status.setText(status_msg)
        # Mark days that have episodes with a dot indicator
        for date_s, btn in self._day_btns.items():
            has = bool(data.get(date_s))
            text = btn.text()
            if has and "●" not in text:
                btn.setText(text + "\n●")
        # Select today
        self._select_day(self._selected)

    def _select_day(self, date_s):
        self._selected = date_s
        # Update button states
        for d, b in self._day_btns.items():
            b.setChecked(d == date_s)
        # Update label
        try:
            dt_obj = dt.datetime.strptime(date_s, "%Y-%m-%d")
            self._day_label.setText(dt_obj.strftime("%A, %d %B %Y"))
        except Exception:
            self._day_label.setText(date_s)
        # Populate list
        self._list.clear()
        eps = self._data.get(date_s, [])
        if not eps:
            if self._data or self._status.text() != "Fetching schedule...":
                item = QListWidgetItem("  Nothing airing from your watchlist on this day.")
                item.setForeground(QColor(MUTED)); self._list.addItem(item)
            else:
                item = QListWidgetItem("  Loading...")
                item.setForeground(QColor(MUTED)); self._list.addItem(item)
        else:
            for time_s, show, ep in eps:
                ep_str = f"Ep {ep}" if ep else ""
                item   = QListWidgetItem(f"  {time_s}   {show}   {ep_str}")
                item.setForeground(QColor(TEXT)); self._list.addItem(item)

class WrappedDialog(QDialog):
    """Stats & Wrapped — year or all-time."""
    def __init__(self, period="year", parent=None):
        super().__init__(parent)
        self._period = period
        year = dt.datetime.now().year
        title = f"🏆  Your {year} Wrapped" if period == "year" else "📊  All-Time Stats"
        self.setWindowTitle(title)
        self.setMinimumSize(560, 580); self.setStyleSheet(QSS)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20,20,20,16)
        lay.setSpacing(12)
        lay.addWidget(lbl(title, ACCENT, 18, True))
        lay.addWidget(hline())
        self._body = QVBoxLayout()
        lay.addLayout(self._body, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject); lay.addWidget(bb)
        self._populate()

    def _stat(self, label, value, color=TEXT):
        row = QHBoxLayout()
        row.addWidget(lbl(label, TEXT2, 13))
        row.addStretch()
        v = lbl(str(value), color, 14, True)
        row.addWidget(v)
        return row

    def _populate(self):
        ld = legion_data()
        md = matrix_data()
        bd = behaviour_data()
        sigs = bd.get("signals", {})
        sessions = bd.get("sessions", [])
        year = dt.datetime.now().year

        # Filter sessions by period
        cutoff = 0
        if self._period == "year":
            cutoff = dt.datetime(year, 1, 1).timestamp()
            sessions = [s for s in sessions if s.get("timestamp", 0) >= cutoff]

        books = ld.get("books", {})
        watching = md.get("watching", {})
        wl = md.get("watchlist", {})

        # Compute stats from filtered sessions
        chapters_read = sum(1 for s in sessions if s.get("type") == "chapter_finished")
        words_read    = sum(s.get("data",{}).get("words",0) for s in sessions if s.get("type")=="words_read")
        watch_mins    = sum(s.get("data",{}).get("minutes",0) for s in sessions if s.get("type")=="watch_time")
        eps_finished  = sum(1 for s in sessions if s.get("type")=="episode_finished")

        # If all-time, merge with cumulative signals (since sessions are capped)
        if self._period == "alltime":
            # For chapters_read, words_read, etc., we want the maximum of (sessions_sum, sigs_total)
            # because sigs_total is cumulative all-time, while sessions is capped.
            # However, sigs is more reliable for all-time.
            chapters_read = max(chapters_read, sigs.get("chapters_finished", 0))
            words_read    = max(words_read, sigs.get("total_words", 0))
            eps_finished  = max(eps_finished, sigs.get("episodes_finished", 0))
            watch_mins    = max(watch_mins, sigs.get("total_watch_minutes", 0))

        # Completed / Abandoned shows from watchlist (not period-filtered yet, usually okay)
        completed = len(wl.get("completed", []))
        abandoned = len(wl.get("dropped", []))

        # Busiest hour
        hours = [s.get("hour") for s in sessions if s.get("hour") is not None]
        busiest_hour = ""
        if hours:
            from collections import Counter
            peak = Counter(hours).most_common(1)[0][0]
            period_name = ("morning" if peak < 12 else
                           "afternoon" if peak < 17 else
                           "evening" if peak < 21 else "night")
            busiest_hour = f"{peak:02d}:00 ({period_name})"

        # Top genres from behavior
        # For year view, we need to count genres from the filtered sessions
        if self._period == "year":
            gc = {}
            for s in sessions:
                g = s.get("data", {}).get("genre")
                if g: gc[g] = gc.get(g, 0) + 1
        else:
            gc = sigs.get("genre_counts", {})
        
        top_genre = sorted(gc.items(), key=lambda x: -x[1])[0][0] if gc else "—"

        # Finish rate
        if self._period == "year":
            fin = sum(1 for s in sessions if s.get("type") == "chapter_finished")
            abd = sum(1 for s in sessions if s.get("type") == "chapter_abandoned")
        else:
            fin = sigs.get("chapters_finished", 0)
            abd = sigs.get("chapters_abandoned", 0)
        
        finish_rate = f"{int(fin/(fin+abd)*100)}%" if fin+abd > 0 else "—"

        stats = [
            ("📖  Chapters read",        chapters_read,  NEON),
            ("📝  Words read",           f"{int(words_read):,}" if words_read else "—", TEXT),
            ("🎬  Episodes watched",     eps_finished,   BLUE),
            ("⏱  Hours watched",        f"{int(watch_mins)//60}h {int(watch_mins)%60}m" if watch_mins else "—", BLUE),
            ("✅  Shows completed",      completed,      ACCENT2),
            ("❌  Shows dropped",        abandoned,      RED),
            ("💪  Chapter finish rate",  finish_rate,    ACCENT),
            ("🎯  Favourite genre",      top_genre,      PURPLE),
            ("🌙  Peak viewing time",    busiest_hour or "—", MUTED),
            ("📚  Books in library",     len(books),     ACCENT),
        ]

        for label, value, color in stats:
            self._body.addLayout(self._stat(label, value, color))

        self._body.addStretch()

        if self._period == "year" and not sessions:
            note = lbl("Start reading and watching to build up your stats!", MUTED, 12)
            note.setWordWrap(True)
            self._body.addWidget(note)


# ═══════════════════════════════════════════════════════════════════════════════
# MATRIX
# ═══════════════════════════════════════════════════════════════════════════════

class _TTSWorker(QThread):
    paragraph_started = pyqtSignal(int, str)   # index, text
    finished          = pyqtSignal()
    error             = pyqtSignal(str)

    def __init__(self, paragraphs: list[str], start_index: int = 0):
        super().__init__()
        self.paragraphs   = paragraphs
        self.start_index  = start_index
        self._stop        = False
        self._pause       = False

    def stop(self):  self._stop = True
    def pause(self): self._pause = True
    def resume(self):self._pause = False

    def run(self):
        try:
            import pyttsx3
        except ImportError:
            self.error.emit("pyttsx3 not installed. Run: pip install pyttsx3")
            return
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", 185)   # words per minute
            for i, para in enumerate(self.paragraphs[self.start_index:], start=self.start_index):
                if self._stop:
                    break
                while self._pause and not self._stop:
                    self.msleep(100)
                if self._stop:
                    break
                self.paragraph_started.emit(i, para)
                engine.say(para)
                engine.runAndWait()
            engine.stop()
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

