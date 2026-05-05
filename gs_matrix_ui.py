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
    Qt, QThread, pyqtSignal, QTimer, QSize, QRectF, QRect, QUrl, QPoint, QObject
)
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
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
    _SageCompanionWorker, _NewChaptersWorker, _MetaRefreshWorker, _DiscoveryWorker,
    start_mobile_server,
)

# MPV_SOCKET_PATH is defined in gs_theme.py (imported via 'from gs_theme import *' above).
# Do NOT redefine it here — both gs_matrix_ui and any external IPC caller must
# use the same socket path or IPC commands will be sent to the wrong socket.
# Current value: MPV_SOCKET_PATH = "/tmp/mpvsocket_gs"  (see gs_theme.py)


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


# ══════════════════════════════════════════════════════════════════════════════
# AD / POPUP INTERCEPTOR  (blocks redirect scripts on anime sites)
# ══════════════════════════════════════════════════════════════════════════════
AD_BLOCK_DOMAINS = {
    # Google / standard ad networks
    "doubleclick.net","googlesyndication.com","adservice.google.com",
    "pagead2.googlesyndication.com","adnxs.com","rubiconproject.com",
    "openx.net","pubmatic.com","casalemedia.com","2mdn.net",
    "scorecardresearch.com","quantserve.com","advertising.com",
    "moatads.com","amazon-adsystem.com","adsafeprotected.com",
    "cdn.viglink.com","outbrain.com","taboola.com","criteo.com",
    # Anime-site specific ad/tracker networks
    "trafficjunky.net","exoclick.com","juicyads.com","plugrush.com",
    "propellerads.com","popcash.net","popads.net","hilltopads.net",
    "adsterra.com","valueimpression.com","bidgear.com","clickaine.com",
    "realsrv.com","dtcn.com","sublimemedia.net","tsyndicate.com",
    "justadsbro.com","content.ad","mgid.com","adskeeper.co.uk",
    "moonads.pro","cpmstar.com","media.net","vidazoo.com",
    "connatix.com","undertone.com","33across.com","sharethrough.com",
    "anistream.xyz","gogocdn.net","cdn77.app","redirect.disqus.com",
    # Malware / phishing redirect patterns common on anime sites
    "1movies.bz","fullxxxmovies.net","watchseries.gy",
    "clickadu.com","adtelligent.com","trckng.net","trkbc.com",
    "moonmgr.com","phncdn.com","ero-advertising.com",
}

POPUP_URL_PATTERNS = [
    "redirect","popup","popunder","clickunder","/pop/","track.",
    "/ad/","/ads/","/click/","afu.php","out.php","go.php",
    "?aff=","&aff=","?ref=ad","clicktracking","ad-click",
    "surveywall","push-notification","subscribe-push",
]

if WEBENGINE_OK:
    from PyQt6.QtWebEngineCore import (
        QWebEngineUrlRequestInterceptor, QWebEngineUrlRequestInfo,
        QWebEngineProfile, QWebEnginePage, QWebEngineSettings
    )

    class AnimeInterceptor(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info: QWebEngineUrlRequestInfo):
            url = info.requestUrl().toString()
            host = info.requestUrl().host().lower()

            # Skip blocking for YouTube and Google-owned domains needed for player init
            # and video playback (googlevideo.com is the primary stream source).
            # Blocking these often results in black screens or player hangs.
            if any(x in host for x in [
                "youtube.com", "googlevideo.com", "ytimg.com", 
                "ggpht.com", "google.com", "gstatic.com"
            ]):
                return

            # Strip leading www.
            host = host.lstrip("www.")
            # Block known ad domains
            for domain in AD_BLOCK_DOMAINS:
                if host == domain or host.endswith("." + domain):
                    info.block(True); return
            # Block popup/redirect URL patterns (only for non-main frame requests)
            if info.resourceType() not in (
                QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMainFrame,
                QWebEngineUrlRequestInfo.ResourceType.ResourceTypeSubFrame,
            ):
                for pat in POPUP_URL_PATTERNS:
                    if pat in url.lower():
                        info.block(True); return

    class AnimePage(QWebEnginePage):
        """Custom page that blocks ad popups but allows fullscreen video windows."""
        def __init__(self, profile, parent=None):
            super().__init__(profile, parent)
            # Enable features needed for video playback and livestreams
            s = self.settings()
            s.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
            s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
            s.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)
            s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
            s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
            s.setAttribute(QWebEngineSettings.WebAttribute.DnsPrefetchEnabled, True)
            self.fullScreenRequested.connect(self._handle_fullscreen)

        def _handle_fullscreen(self, request):
            request.accept()
            # In PyQt6 QWebEnginePage doesn't have .view(), but we pass it as parent
            view = self.parent()
            if view:
                if request.toggleOn():
                    view.window().showFullScreen()
                else:
                    view.window().showNormal()

        def createWindow(self, _type):
            # Allow fullscreen/video popup windows, block everything else
            from PyQt6.QtWebEngineCore import QWebEnginePage as _P
            if _type in (_P.WebWindowType.WebBrowserWindow,
                         _P.WebWindowType.WebDialog):
                # These are likely video player popups — allow them on the same view
                return self
            return None  # block ad popups

        def javaScriptConsoleMessage(self, level, message, line, source):
            pass  # suppress console noise


# ══════════════════════════════════════════════════════════════════════════════
# ANIMEKAI WATCH HISTORY SYNC WORKER
# ══════════════════════════════════════════════════════════════════════════════

class MatrixPage(QWidget):
    def __init__(self):
        super().__init__()
        self._meta_worker  = None
        self._mpv_process  = None
        self._play_thread  = None
        self._build()
        self.refresh()

    def _build(self):
        import types as _mt
        from PyQt6.QtGui import (QPainter as _mP, QLinearGradient as _mG,
                                  QColor as _mC, QBrush as _mB, QPen as _mPen,
                                  QRadialGradient as _mRG)
        root = QVBoxLayout(self)
        root.setContentsMargins(0,0,0,0)
        root.setSpacing(0)

        header_w = QWidget()
        header_w.setFixedHeight(64)
        def _hdr_paint(self_w, event):
            p = _mP(self_w)
            p.setRenderHint(_mP.RenderHint.Antialiasing)
            W, H = self_w.width(), self_w.height()
            g = _mG(0, 0, W, 0)
            g.setColorAt(0, _mC("#0D1520")); g.setColorAt(0.4, _mC(BG2)); g.setColorAt(1.0, _mC("#0A0F18"))
            p.fillRect(0, 0, W, H, _mB(g))
            rg = _mRG(0, H // 2, W * 0.5)
            c1 = _mC(BLUE)
            c1.setAlpha(30)
            rg.setColorAt(0, c1); rg.setColorAt(1, _mC(0, 0, 0, 0))
            p.fillRect(0, 0, W, H, _mB(rg))
            bl = _mG(0, 0, W, 0)
            bl.setColorAt(0, _mC(BLUE).darker(200)); bl.setColorAt(0.2, _mC(BLUE))
            bl.setColorAt(0.8, _mC(BLUE)); bl.setColorAt(1.0, _mC(BLUE).darker(200))
            p.setPen(_mPen(_mB(bl), 1)); p.drawLine(0, H - 1, W, H - 1); p.end()
        header_w.paintEvent = _mt.MethodType(_hdr_paint, header_w)

        hv = QHBoxLayout(header_w)
        hv.setContentsMargins(24, 0, 24, 0)
        hv.setSpacing(0)
        back_b = QPushButton("⬡  HOME")
        back_b.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;color:{MUTED};"
            f"font-size:9px;letter-spacing:2px;padding:4px 10px 4px 0;}}"
            f"QPushButton:hover{{color:{BLUE};}}")
        back_b.setCursor(__import__('PyQt6.QtCore', fromlist=['Qt']).Qt.CursorShape.PointingHandCursor)
        back_b.clicked.connect(lambda: self.window()._navigate("dashboard"))
        sep_lbl = QLabel(">"); sep_lbl.setStyleSheet(f"color:{MUTED};font-size:12px;background:transparent;padding:0 6px;")
        ico_lbl = QLabel("▣"); ico_lbl.setStyleSheet(f"color:{BLUE};font-size:18px;background:transparent;padding-right:10px;")
        title_l = QLabel("MATRIX")
        title_l.setStyleSheet(f"font-family:{FONT_DISPLAY};font-size:15px;font-weight:bold;color:{TEXT};letter-spacing:5px;background:transparent;")
        sub_l = QLabel("MEDIA MANAGER")
        sub_l.setStyleSheet(f"color:{MUTED};font-size:8px;letter-spacing:3px;background:transparent;margin-left:14px;margin-top:2px;")
        hv.addWidget(back_b); hv.addWidget(sep_lbl); hv.addWidget(ico_lbl)
        hv.addWidget(title_l); hv.addWidget(sub_l); hv.addStretch()
        root.addWidget(header_w)

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
        self._stream_tab = self._build_stream()
        tabs.addTab(self._stream_tab,         "STREAM")
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
                f"border-left:3px solid {BLUE};}}")
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
        dv = QVBoxLayout(self.wl_detail_w)
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
                            "watched":False,"notes":"Added via GUI"})
            save_json(MATRIX_PROGRESS, md)
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

    def _wl_remove(self, title, lst_name):
        md = matrix_data(); wl = md.get("watchlist",{})
        wl[lst_name] = [e for e in wl.get(lst_name,[])
            if (e.get("title","") if isinstance(e,dict) else str(e)).lower() != title.lower()]
        save_json(MATRIX_PROGRESS, md); self.refresh()

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
        t       = info.get("title", title)
        yr      = info.get("year","")
        sc      = info.get("score", 0)
        ov      = info.get("synopsis") or info.get("overview","")
        src_s   = info.get("source","")
        genres  = info.get("genres","")
        genre_str = (", ".join(genres[:4]) if isinstance(genres,list) else
                     genres if isinstance(genres,str) else "")

        self.wl_d_title.setText(t)

        meta_parts = []
        if yr: meta_parts.append(str(yr))
        if sc and sc != "N/A": meta_parts.append(f"★ {sc}")
        if genre_str: meta_parts.append(genre_str)
        self.wl_d_meta.setText("  ·  ".join(meta_parts))

        self.wl_d_synopsis.setText(ov if ov else "No synopsis available.")
        self.wl_d_src_lbl.setText(f"via {src_s}" if src_s else "")

        self._wl_current_title = title
        self.wl_d_trailer_btn.show()

        self.wl_placeholder.hide()
        self.wl_detail_w.show()

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
            self._tabs.setCurrentIndex(3)
            if ep and WEBENGINE_OK and hasattr(self, "_stream_view"):
                import urllib.parse
                q = urllib.parse.quote(f"{title} episode {ep}")
                self._stream_view.load(QUrl(f"https://animekai.to/search?q={q}"))
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
        if title in md.get("watching",{}): del md["watching"][title]
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
        if mod and hasattr(mod, "MediaPlayer"):
            try: season, episode = mod.MediaPlayer._extract_season_episode(filename)
            except Exception as e:
                log.warning("Operation failed", error=str(e), location="_launch_mpv")
        else:
            m = re.search(r'[Ss](\d{1,2})[Ee](\d{1,4})', filename)
            if m: season, episode = int(m.group(1)), int(m.group(2))

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
                immediate   = os.path.dirname(path)
                parent      = os.path.dirname(immediate)
                folder_name = os.path.basename(immediate).lower()
                root = parent if any(k in folder_name for k in ("season","series","s01","s02","s03","s04","s05")) else immediate
                all_files = sorted(
                    os.path.join(dp, f)
                    for dp, _, files in os.walk(root)
                    for f in files if f.lower().endswith(exts)
                )
                total_eps  = len(all_files)
                # 1-based position of this file in the sorted list
                try:    file_index = all_files.index(path) + 1
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
            daemon=True
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
                    "notes": "Auto-added when playback started"
                })
                save_json(MATRIX_PROGRESS, md)
                QTimer.singleShot(0, self.refresh)
            except Exception as e:
                log.warning("Operation failed", error=str(e), location="_play_loop")
        _ensure_watching(show)

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
                            target_key = show if show in watching else next(
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

                # Get position — skip reads while mpv is switching files to avoid
                # capturing a stale position from the previous episode
                if _file_switching:
                    fn_resp = _ipc({"command": ["get_property", "filename"]})
                    if fn_resp and fn_resp.get("data") == _expected_file:
                        _file_switching = False  # new file confirmed, resume normal tracking
                else:
                    resp = _ipc({"command": ["get_property", "time-pos"]})
                    if resp and resp.get("error") == "success" and resp.get("data") is not None:
                        last_pos = float(resp["data"])

                # Get duration once (only when not switching files)
                if duration == 0 and not _file_switching:
                    dr = _ipc({"command": ["get_property", "duration"]})
                    if dr and dr.get("error") == "success" and dr.get("data"):
                        duration = float(dr["data"])

                # Save position + duration every 3s
                now = time.time()
                if last_pos > 0 and not _file_switching and now - last_save >= 3:
                    last_save = now
                    pos_snap = last_pos
                    dur_snap = duration
                    fname = current
                    track_event("watch_time", {"minutes": 0.05})  # ~3s per save cycle
                    def _save(sk=show, p=pos_snap, d=dur_snap, f=fname, sess=play_session):
                        if sess != play_session: return  # stale — a newer episode is playing
                        if p <= 0: return                # never persist a zero from a stale tick
                        md = matrix_data()
                        watching = md.get("watching", {})
                        # Find the right key — prefer exact match, fall back to file_path match
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
                    threading.Thread(target=_save, daemon=True).start()

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
        except Exception as e:
            log.warning("auto_complete error", error=str(e))

    def _build_stream(self):
        """STREAM tab — embedded AnimeKai browser with ad blocking, persistent login,
           real-time episode tracking and full Matrix/Sage integration."""
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

        # ── Ad / popup interceptor ─────────────────────────────────────────────
        self._interceptor = AnimeInterceptor()
        self._stream_profile.setUrlRequestInterceptor(self._interceptor)

        # ── Web view ───────────────────────────────────────────────────────────
        self._stream_view = QWebEngineView()
        # Set background to black to avoid white flashes during initial load
        self._stream_view.setStyleSheet("background-color: #000000;")

        # ── Custom page (blocks popups, suppresses JS console noise) ──────────
        # We pass the view as parent so the page can access it for fullscreen
        self._stream_page = AnimePage(self._stream_profile, self._stream_view)
        self._stream_view.setPage(self._stream_page)

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
        self._stream_home_btn = _nav_btn("⌂", "Go to AnimeKai")

        self._stream_back.clicked.connect(self._stream_view.back)
        self._stream_fwd.clicked.connect(self._stream_view.forward)
        self._stream_reload.clicked.connect(self._stream_view.reload)
        self._stream_home_btn.clicked.connect(
            lambda: self._stream_view.load(QUrl("https://animekai.to")))

        self._stream_url_bar = QLineEdit()
        self._stream_url_bar.setPlaceholderText("https://animekai.to")
        self._stream_url_bar.setStyleSheet(
            f"background:{BG3}; border:1px solid {BORDER}; color:{TEXT};"
            f"font-size:12px; padding:4px 12px; border-radius:3px;")
        self._stream_url_bar.returnPressed.connect(self._stream_navigate)

        # Login status — shown as a small dot indicator on the home button
        self._stream_login_lbl = QPushButton("⌂")
        self._stream_login_lbl.setFixedSize(32, 30)
        self._stream_login_lbl.setToolTip("Not logged in — log into AnimeKai to enable sync")
        self._stream_login_lbl.setStyleSheet(
            f"background:transparent; border:1px solid {BORDER}; color:{MUTED};"
            f"font-size:14px; border-radius:3px; padding:2px 4px;")
        self._stream_login_lbl.clicked.connect(
            lambda: self._stream_view.load(QUrl("https://animekai.to")))

        self._stream_sync_btn = QPushButton("⟳  SYNC HISTORY")
        self._stream_sync_btn.setStyleSheet(
            f"background:transparent; border:1px solid {ACCENT2}; color:{ACCENT2};"
            f"font-size:9px; letter-spacing:1px; padding:5px 12px; border-radius:3px;")
        self._stream_sync_btn.setToolTip(
            "Pull your full AnimeKai watch history into Matrix")
        self._stream_sync_btn.clicked.connect(self._animekai_sync)

        self._stream_yt_btn = QPushButton("▶ YT")
        self._stream_yt_btn.setFixedSize(46, 30)
        self._stream_yt_btn.setToolTip("Open YouTube")
        self._stream_yt_btn.setStyleSheet(
            f"background:#FF0000; border:none; color:white;"
            f"font-size:9px; font-weight:bold; letter-spacing:1px; border-radius:3px;")
        self._stream_yt_btn.clicked.connect(
            lambda: self._stream_view.load(QUrl("https://www.youtube.com")))

        nv.addWidget(self._stream_back)
        nv.addWidget(self._stream_fwd)
        nv.addWidget(self._stream_reload)
        nv.addWidget(self._stream_url_bar, 1)
        nv.addWidget(self._stream_yt_btn)
        nv.addWidget(self._stream_login_lbl)
        nv.addWidget(self._stream_sync_btn)
        wv.addWidget(nav_bar)

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

        # Load AnimeKai
        self._stream_view.load(QUrl("https://animekai.to"))
        return w

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
        # Instant login detection from URL alone
        if "animekai" in url_str.lower():
            url_lower = url_str.lower()
            if any(p in url_lower for p in ("/home", "/user/", "/profile", "/watchlist", "/history", "/bookmarks")):
                self._on_login_check(True)

    def _on_stream_title_changed(self, title):
        self._parse_animekai_title(title)

    def _on_stream_load_finished(self, ok):
        self._stream_progress.hide()
        self._stream_back.setEnabled(self._stream_view.history().canGoBack())
        self._stream_fwd.setEnabled(self._stream_view.history().canGoForward())
        # URL-based login hint: animekai.to/home is the logged-in landing page
        url = self._stream_view.url().toString().lower()
        if "animekai" in url:
            if "/home" in url or "/user/" in url or "/profile" in url or "/watchlist" in url:
                # These pages only exist when logged in
                self._on_login_check(True)
            else:
                self._check_login_status()

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
            self._stream_login_lbl.setStyleSheet(
                f"background:transparent; border:1px solid {ACCENT2}; color:{ACCENT2};"
                f"font-size:14px; border-radius:3px; padding:2px 4px;")
            self._stream_login_lbl.setToolTip("● Logged in — AnimeKai session active")
        else:
            self._stream_login_lbl.setStyleSheet(
                f"background:transparent; border:1px solid {BORDER}; color:{MUTED};"
                f"font-size:14px; border-radius:3px; padding:2px 4px;")
            self._stream_login_lbl.setToolTip("○ Not logged in — click to go to AnimeKai")

    # ── Episode detection ──────────────────────────────────────────────────────
    def _parse_animekai_url(self, url: str):
        """Parse AnimeKai URL patterns:
           animekai.to/watch/{slug}#ep={num}
           animekai.to/watch/{slug}?ep={num}
           animekai.to/{slug}/ep-{num}
        """
        # Pattern 1: /watch/slug#ep=12 or /watch/slug?ep=12
        m = re.search(r'/watch/([^#?/]+)[#?]ep[=:](\d+)', url)
        if m:
            slug = m.group(1)
            ep = int(m.group(2))
            title = re.sub(r'-[a-z0-9]{3,6}$', '', slug).replace('-', ' ').title()
            self._update_now_watching(title, ep); return
        # Pattern 2: /slug/ep-12
        m = re.search(r'/([a-z0-9-]+)/ep-(\d+)', url)
        if m:
            slug = m.group(1)
            ep = int(m.group(2))
            title = slug.replace('-', ' ').title()
            self._update_now_watching(title, ep)

    def _parse_animekai_title(self, title: str):
        """Parse page title variations:
           'Solo Leveling Episode 12 - AnimeKai'
           'Watch Solo Leveling Ep 12 Online'
           'Solo Leveling - Episode 12'
        """
        if not title: return
        t_lower = title.lower()
        # Must be an AnimeKai page or watch page
        if not any(k in t_lower for k in ('animekai', 'watch', 'episode', ' ep ')):
            return
        # Block generic site page titles that are not real shows
        _JUNK_TITLES = (
            'recently added', 'latest episodes', 'popular anime',
            'new release', 'home page', 'search results', 'genre',
            'schedule', 'top anime', 'trending', 'bookmark',
        )
        if any(junk in t_lower for junk in _JUNK_TITLES):
            return
        # Try multiple patterns
        patterns = [
            r'^(?:watch\s+)?(.+?)\s+[Ee]p(?:isode)?[\s.#]*(\d+)',
            r'^(.+?)\s*[-–]\s*[Ee]p(?:isode)?[\s.#]*(\d+)',
            r'^(.+?)\s+(\d+)\s*[-–]',
        ]
        for pat in patterns:
            m = re.search(pat, title, re.IGNORECASE)
            if m:
                show = m.group(1).strip()
                # Strip site name suffixes
                show = re.sub(r'\s*[-|]\s*(AnimeKai|Watch Online|HD|Sub|Dub).*$',
                               '', show, flags=re.IGNORECASE).strip()
                ep = int(m.group(2))
                if show and ep > 0:
                    self._update_now_watching(show, ep)
                    return

    def _update_now_watching(self, title: str, ep: int):
        if not title or len(title) < 2: return
        title = title.strip()
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

                # Fuzzy key match in watching dict
                key = None
                for k in watching:
                    kt = watching[k].get("title", k) if isinstance(watching[k], dict) else k
                    if kt.lower() == title.lower():
                        key = k
                        break

                if key is None:
                    key = title
                    # Auto-move from planning → watching in watchlist
                    new_planning = []
                    found_in_planning = False
                    for e in planning:
                        t = e.get("title", "") if isinstance(e, dict) else str(e)
                        if t.lower() == title.lower():
                            found_in_planning = True
                            # Move to watchlist watching
                            entry = e if isinstance(e, dict) else {"title": t}
                            entry["is_anime"] = True
                            if not any(
                                (x.get("title","") if isinstance(x,dict) else str(x)).lower() == title.lower()
                                for x in wl_watching
                            ):
                                wl_watching.append(entry)
                        else:
                            new_planning.append(e)
                    if found_in_planning:
                        wl["planning"] = new_planning

                # Update or create watching entry
                now = int(time.time())
                if isinstance(watching.get(key), dict):
                    old_ep = watching[key].get("current_episode", 0)
                    watching[key]["current_episode"] = max(ep, old_ep)
                    watching[key]["title"]           = title
                    watching[key]["last_watched"]    = now
                    watching[key]["source"]          = watching[key].get("source", "animekai")
                    # Track episode list
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
            # Refresh UI on main thread
            QTimer.singleShot(0, self.refresh)
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
            "notes": "Added from STREAM tab"
        })
        save_json(MATRIX_PROGRESS, md)
        self.refresh()
        self._now_watch_wl_btn.setText("✓ ADDED")
        QTimer.singleShot(2000, lambda: self._now_watch_wl_btn.setText("+ WATCHLIST"))

    # ── AnimeKai watch history sync ────────────────────────────────────────────
    def _animekai_sync(self):
        """Sync AnimeKai watch history from browser localStorage into Matrix."""
        if not WEBENGINE_OK or not hasattr(self, "_stream_page"): return
        self._stream_sync_btn.setText("⟳  SYNCING...")
        self._stream_sync_btn.setEnabled(False)

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
        self._stream_sync_btn.setEnabled(True)
        try:
            items = _json.loads(result or "[]")
            if not items:
                self._stream_sync_btn.setText("⟳  SYNC HISTORY")
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
            self._stream_sync_btn.setText(summary)
            QTimer.singleShot(4000, lambda: self._stream_sync_btn.setText("⟳  SYNC HISTORY"))

            # No return URL stored; simply stay on current page
            pass

        except Exception as e:
            self._stream_sync_btn.setText("⟳  SYNC HISTORY")


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

        # 1) Local MPV entries
        for key, info in md.get("watching", {}).items():
            if not isinstance(info, dict): continue
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
                if fidx > 0 and tot > 0:
                    ep_badge += f"  ({fidx}/{tot})"

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

        # 2) AnimeKai stream entries (handled via stream_watching key below)

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
                wl[lst].append({"title":title,"watched":False,"added":time.time(),"notes":"Added from Sage"})
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

