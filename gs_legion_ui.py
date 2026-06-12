"""
gs_legion_ui.py — Great Sage
=============================
Legion module UI: novel reader page and all related dialogs.
"""
import os, re, time

try:
    from gs_logger import log
except Exception as _log_err:
    class _NoopLog:
        def __getattr__(self, name): return _NoopLog()
        def __call__(self, *a, **kw): return None
    log = _NoopLog()

from gs_theme import *
from gs_widgets import lbl, btn, hline, vline, tag, NavRail, EyeBreakToast, SyncToast, _TouchScrollFilter

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
    QPixmap, QPainter, QLinearGradient, QRadialGradient, QBrush, QPen, QPainterPath,
    QTextCharFormat
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QLabel, QPushButton, QLineEdit, QTextEdit, QSlider,
    QFrame, QListWidget, QListWidgetItem, QTabWidget, QComboBox,
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QMessageBox, QAbstractItemView,
    QProgressBar, QGroupBox, QFormLayout, QStatusBar, QMenu, QSplitter, QScrollArea,
    QGraphicsOpacityEffect,
)
from great_sage_core import (
    SCRIPT_DIR, LEGION_PROGRESS, MATRIX_PROGRESS, LEGION_BOOKMARKS, SAGE_MEMORY_PATH,
    load_json, save_json,
    legion_data, matrix_data, bookmarks_data,
    _catalogue_panel_class, _clean_media_title, _strip_markdown, _detect_genre,
    _grep_book_for_term,
    sage_memory_load, sage_memory_append, sage_memory_extract,
    behaviour_data, behaviour_summary, track_event, stream_watch_context,
    FetchChapterWorker, SageWorker, MetadataWorker, AutoSyncWorker,
    _SageCompanionWorker, _NewChaptersWorker, _MetaRefreshWorker,
    start_mobile_server,
    set_session_groq_model, get_session_groq_model,
    GROQ_MODEL_VERSATILE, GROQ_MODEL_INSTANT,
)

def _get_legion_mod():
    from great_sage_core import legion_mod
    return legion_mod()

from gs_widgets import ReadingRoomOverlay
from gs_matrix_ui import CalendarDialog, HighlightsDialog, WrappedDialog

_BG_MODES = {
    "dark":  (BG3,      TEXT),
    "sepia": ("#F5ECD7", "#3A2A1A"),
    "white": ("#FFFFFF", "#1A1A1A"),
}

class LegionPage(QWidget):
    def __init__(self):
        super().__init__()
        self._worker = None
        self._font_size = 18
        # Reader settings — loaded from progress on init, saved on change
        self._rs_defaults = {
            "font_size":   18,
            "line_height": 1.9,
            "padding_h":   80,    # horizontal padding px
            "padding_v":   36,    # vertical padding px
            "bg_mode":     "dark", # "dark" | "sepia" | "white"
            "tts_rate":    185,
        }
        self._rs = {**self._rs_defaults, **legion_data().get("reader_settings", {})}
        self._font_size = self._rs["font_size"]
        self._current_url = ""; self._next_url = ""; self._prev_url = ""
        self._current_book = ""; self._book_data = {}
        # Local-file navigation state
        self._current_ch_num = 0    # chapter number currently on screen
        self._total_ch_local = 0    # how many chapters exist in the .txt file
        self._reading_local  = False  # True = came from local file
        self._chapter_loading = False # True while a new chapter is being set up
        # Scroll position memory: {book_name: {ch_num: fraction 0.0-1.0}}
        self._scroll_positions = {}

        
        # Worker cleanup
        self._meta_workers = [] # Track all background workers for cleanup
        self._max_stored_workers = 5 # Keep a small pool of recently active workers
        
        self._build()
        # Eye-break reminder — fires every 15 minutes while reading
        self._eye_toast = EyeBreakToast(self)
        self._eye_timer = QTimer(self)
        self._eye_timer.setInterval(15 * 60 * 1000)   # 15 minutes
        self._eye_timer.timeout.connect(self._eye_toast.show_toast)
        # Stop all workers cleanly when the application quits
        app = QApplication.instance()
        if app:
            app.aboutToQuit.connect(self.cleanup_all_workers)

    def _cleanup_worker(self, worker):
        """Remove worker from _meta_workers list after it finishes."""
        try:
            if worker in self._meta_workers:
                self._meta_workers.remove(worker)
                log.debug("Cleaned up worker", worker=worker)
        except Exception as e:
            log.warning("Worker cleanup failed", error=str(e), worker=worker)

    def cleanup_all_workers(self):
        """Stop all running QThreads. Call this before the widget is destroyed."""
        # FetchChapterWorker (web scrape)
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(1000)
            self._worker = None
        # Sage companion worker
        if hasattr(self, '_sage_worker') and self._sage_worker and self._sage_worker.isRunning():
            self._sage_worker.terminate()
            self._sage_worker.wait(1000)
            self._sage_worker = None

        # Meta workers
        for w in list(self._meta_workers):
            try:
                if w.isRunning():
                    w.terminate()
                    w.wait(500)
            except Exception:
                pass
        self._meta_workers.clear()
        log.debug("LegionPage: all workers stopped")

    def hideEvent(self, event):
        """Stop all running workers when the page is hidden or the app closes."""
        self.cleanup_all_workers()
        super().hideEvent(event)

    def _make_icon_list(self):
        """Create a standard icon-mode QListWidget matching the Jump In grid style."""
        lw = QListWidget()
        lw.setViewMode(QListWidget.ViewMode.IconMode)
        lw.setIconSize(QSize(100, 140))
        lw.setGridSize(QSize(120, 185))
        lw.setResizeMode(QListWidget.ResizeMode.Adjust)
        lw.setMovement(QListWidget.Movement.Static)
        lw.setWordWrap(True)
        lw.setSpacing(8)
        # Pixel-level scrolling so scrollbar maximum() is meaningful for infinite scroll
        lw.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        lw.setHorizontalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        lw.setStyleSheet(
            f"QListWidget{{background:transparent;border:none;padding:12px;}}"
            f"QListWidget::item{{background:transparent;border-radius:8px;color:{TEXT2};"
            f"font-size:10px;text-align:center;}}"
            f"QListWidget::item:hover{{background:{BG2};}}"
            f"QListWidget::item:selected{{background:{BG3};color:{ACCENT};}}")
        return lw

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0,0,0,0)
        root.setSpacing(0)

        # ── Sidebar: nav only ──────────────────────────────────────────────────
        sidebar = QFrame(); sidebar.setObjectName("sidebar"); sidebar.setFixedWidth(220)
        sv = QVBoxLayout(sidebar)
        sv.setContentsMargins(0,0,0,12)
        sv.setSpacing(0)

        hdr_w = QWidget()
        hdr_w.setStyleSheet(f"background:{BG2}; border-bottom:1px solid {BORDER};")
        hdr_w.setFixedHeight(52)
        hw = QHBoxLayout(hdr_w)
        hw.setContentsMargins(16,0,12,0)
        self._menu_btn = QPushButton("☰")
        self._menu_btn.setFixedSize(28, 28)
        self._menu_btn.setStyleSheet(
            f"background:transparent;border:none;color:{MUTED};"
            f"font-size:16px;padding:0;")
        self._menu_btn.clicked.connect(self._toggle_menu)
        hw.addStretch()
        hw.addWidget(self._menu_btn)
        sv.addWidget(hdr_w); sv.addWidget(hline())

        # Tab buttons — control the right-panel stack
        _tab_btn_style_active = (
            f"background:{BG3};color:{ACCENT};border:none;border-left:2px solid {ACCENT};"
            f"font-size:12px;padding:12px 16px;text-align:left;border-radius:0;")
        _tab_btn_style_idle = (
            f"background:transparent;color:{MUTED};border:none;"
            f"font-size:12px;padding:12px 16px;text-align:left;border-radius:0;"
            f"border-left:2px solid transparent;")

        self._tab_btns = []
        tab_labels = [("Jump In", 0), ("Bookmarks", 1)]
        for label, idx in tab_labels:
            b = QPushButton(label)
            b.setStyleSheet(_tab_btn_style_idle)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, i=idx: self._switch_tab(i))
            sv.addWidget(b)
            self._tab_btns.append(b)

        self._tab_btn_style_active = _tab_btn_style_active
        self._tab_btn_style_idle   = _tab_btn_style_idle

        sv.addStretch(1)
        sv.addWidget(hline())

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(12,8,12,0)
        btn_row.setSpacing(6)
        add_b = QPushButton("+ ADD BOOK"); add_b.setStyleSheet(accent_btn_style())
        add_b.setStyleSheet(f"font-size:10px;letter-spacing:1px;padding:8px;")
        add_b.clicked.connect(self._add_book)
        ref_b = QPushButton("↻"); ref_b.setFixedWidth(34)
        ref_b.setStyleSheet(
            f"font-size:14px;padding:6px;border:1px solid {BORDER};"
            f"border-radius:4px;color:{MUTED};background:transparent;")
        ref_b.clicked.connect(self.refresh)
        btn_row.addWidget(add_b, 1); btn_row.addWidget(ref_b)
        sv.addLayout(btn_row)

        root.addWidget(sidebar)

        # ── Right panel: stacked content area ─────────────────────────────────
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0,0,0,0)
        rv.setSpacing(0)

        self._right_stack = QStackedWidget()

        # ── Right panel: stacked content area (continued from sidebar build above)
        rv.addWidget(self._right_stack, 1)

        # ── Stack page 0: Jump In ──────────────────────────────────────────────
        jumpin_w = QWidget()
        jumpin_w.setStyleSheet(f"background:{BG};")
        ji_v = QVBoxLayout(jumpin_w)
        ji_v.setContentsMargins(0,0,0,0)
        ji_v.setSpacing(0)

        # Jump In header bar
        ji_hdr = QWidget()
        ji_hdr.setStyleSheet(f"background:{BG2};border-bottom:1px solid {BORDER};")
        ji_hdr.setFixedHeight(44)
        ji_hdr_l = QHBoxLayout(ji_hdr)
        ji_hdr_l.setContentsMargins(20,0,20,0)
        ji_hdr_l.addWidget(lbl("JUMP IN", ACCENT, 11, True))
        ji_hdr_l.addStretch()
        ji_v.addWidget(ji_hdr)

        self.jumpin_list = self._make_icon_list()
        self.jumpin_list.itemClicked.connect(self._book_clicked)
        ji_v.addWidget(self.jumpin_list, 1)
        self._right_stack.addWidget(jumpin_w)   # index 0

        # ── Stack page 1: Bookmarks ────────────────────────────────────────────
        bm_w = QWidget()
        bm_w.setStyleSheet(f"background:{BG};")
        bm_v = QVBoxLayout(bm_w)
        bm_v.setContentsMargins(0,0,0,0)
        bm_v.setSpacing(0)

        bm_hdr = QWidget()
        bm_hdr.setStyleSheet(f"background:{BG2};border-bottom:1px solid {BORDER};")
        bm_hdr.setFixedHeight(44)
        bm_hdr_l = QHBoxLayout(bm_hdr)
        bm_hdr_l.setContentsMargins(20,0,20,0)
        bm_hdr_l.addWidget(lbl("BOOKMARKS", ACCENT, 11, True))
        bm_hdr_l.addStretch()
        bm_v.addWidget(bm_hdr)

        self._bm_tabs = QTabWidget()
        self._bm_tabs.setStyleSheet(
            f"QTabWidget::pane{{border:none;background:transparent;}}"
            f"QTabBar::tab{{background:transparent;color:{MUTED};border:none;"
            f"padding:8px 14px;font-size:12px;}}"
            f"QTabBar::tab:selected{{color:{ACCENT};border-bottom:2px solid {ACCENT};}}")
        self._bm_lists = {}
        for n in ("planning","reading","dropped","completed"):
            lw = QListWidget()
            lw.setStyleSheet(
                f"QListWidget{{background:transparent;border:none;padding:8px;}}"
                f"QListWidget::item{{background:{BG2};border-radius:6px;color:{TEXT};"
                f"font-size:12px;padding:10px 14px;margin:3px 0;}}"
                f"QListWidget::item:hover{{background:{BG3};}}"
                f"QListWidget::item:selected{{background:{BG3};color:{ACCENT};}}")
            lw.itemClicked.connect(lambda item, nm=n: self._bm_clicked(item, nm))
            lw.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            lw.customContextMenuRequested.connect(
                lambda pos, lw_=lw, nm=n: self._bm_context(pos, lw_, nm))
            self._bm_lists[n] = lw
            self._bm_tabs.addTab(lw, n.capitalize())
        bm_v.addWidget(self._bm_tabs, 1)
        self._right_stack.addWidget(bm_w)   # index 1


        # Stack indices: jumpin=0, bookmarks=1, reader=2, downloads=3

        # We keep _detail_* attributes as no-ops so nothing else breaks
        self._detail_title     = QLabel()
        self._detail_meta      = QLabel()
        self._detail_synopsis  = QTextEdit()
        self._detail_progress  = QLabel()
        self._detail_dl_status = QLabel()

        def _noop_btn(text):
            b = QPushButton(text); b.setVisible(False); return b
        self._btn_read      = _noop_btn("▶  READ")
        self._btn_download  = _noop_btn("↓  DOWNLOAD")
        self._btn_dl_pause  = _noop_btn("⏸  PAUSE")
        self._btn_dl_resume = _noop_btn("▶  RESUME")
        self._btn_dl_cancel = _noop_btn("✕  CANCEL")
        self._btn_refresh   = _noop_btn("↺  INFO")
        self._btn_new_chs   = _noop_btn("↓  NEW CH")
        self._btn_delete    = _noop_btn("REMOVE")
        self._btn_reset_time= _noop_btn("↺  RESET TIME")

        # ── 1: Reader view ────────────────────────────────────────────────────
        reader_w = QWidget()
        reader_w.setStyleSheet(f"background:{BG};")
        rw = QVBoxLayout(reader_w)
        rw.setContentsMargins(0,0,0,0)
        rw.setSpacing(0)

        # Reader top bar
        top_bar_w = QWidget()
        top_bar_w.setStyleSheet(f"background:{BG2};border-bottom:1px solid {BORDER};border-radius:0;")
        top_bar_w.setFixedHeight(44)
        tb = QHBoxLayout(top_bar_w)
        tb.setContentsMargins(16,0,16,0)
        tb.setSpacing(10)
        self._back_btn = QPushButton("← INFO")
        self._back_btn.setStyleSheet(
            f"background:transparent;border:none;color:{MUTED};"
            f"font-size:9px;letter-spacing:1.5px;padding:4px 6px;")
        self._back_btn.clicked.connect(lambda: (self._save_reading_time(), self._right_stack.setCurrentIndex(getattr(self, "_current_tab_idx", 0)))[-1])
        tb.addWidget(self._back_btn)
        sep = QLabel("│"); sep.setStyleSheet(f"color:{BORDER};")
        tb.addWidget(sep)
        self.chapter_title = QLabel("")
        self.chapter_title.setStyleSheet(f"color:{TEXT2};font-size:12px;letter-spacing:1px;")
        tb.addWidget(self.chapter_title, 1)
        self._prev_btn = QPushButton("←"); self._prev_btn.setFixedWidth(30)
        self._prev_btn.setStyleSheet(f"background:transparent;border:1px solid {BORDER};color:{MUTED};border-radius:3px;font-size:14px;padding:2px;")
        self._prev_btn.clicked.connect(self._prev_chapter)
        self._next_btn = QPushButton("→"); self._next_btn.setFixedWidth(30)
        self._next_btn.setStyleSheet(f"background:transparent;border:1px solid {BORDER};color:{MUTED};border-radius:3px;font-size:14px;padding:2px;")
        self._next_btn.clicked.connect(self._next_chapter)
        self._fa_btn = QPushButton("A−"); self._fa_btn.setFixedWidth(32)
        self._fa_btn.setStyleSheet(f"background:transparent;border:none;color:{MUTED};font-size:11px;")
        self._fa_btn.clicked.connect(lambda: self._font_delta(-1))
        self._fb_btn = QPushButton("A+"); self._fb_btn.setFixedWidth(32)
        self._fb_btn.setStyleSheet(f"background:transparent;border:none;color:{MUTED};font-size:11px;")
        self._fb_btn.clicked.connect(lambda: self._font_delta(+1))


        self._sage_top_btn = QPushButton("\u2736 SAGE")
        self._sage_top_btn.setFixedWidth(68)
        self._sage_top_btn.setStyleSheet(
            f"background:transparent;border:1px solid {BORDER};color:{ACCENT};"
            f"font-size:8px;letter-spacing:1px;padding:3px;border-radius:3px;")
        self._sage_top_btn.setToolTip("Toggle Sage AI panel")
        self._sage_top_btn.clicked.connect(self._toggle_sage_panel)

        self._lens_top_btn = QPushButton("◈ LENS")
        self._lens_top_btn.setFixedWidth(68)
        self._lens_top_btn.setStyleSheet(
            f"background:transparent;border:1px solid {BORDER};color:{MUTED};"
            f"font-size:9px;letter-spacing:1px;padding:5px 10px;border-radius:3px;")
        self._lens_top_btn.setToolTip("Toggle Lens — paste a description to visualize it")
        self._lens_top_btn.clicked.connect(self._toggle_lens_panel)

        self._notes_top_btn = QPushButton("\u270e NOTES")
        self._notes_top_btn.setFixedWidth(72)
        self._notes_top_btn.setStyleSheet(
            f"background:transparent;border:1px solid {BORDER};color:{ACCENT2};"
            f"font-size:8px;letter-spacing:1px;padding:3px;border-radius:3px;")
        self._notes_top_btn.setToolTip("Toggle chapter notes panel")
        self._notes_top_btn.clicked.connect(self._toggle_notes_panel)
        
        for b_ in (self._prev_btn, self._next_btn, self._fa_btn, self._fb_btn,
                   self._sage_top_btn, self._lens_top_btn, self._notes_top_btn):
            tb.addWidget(b_)
        rw.addWidget(top_bar_w)

        # Thin reading-progress bar — fills as you scroll through the chapter
        self._read_progress = QProgressBar()
        self._read_progress.setRange(0, 1000)
        self._read_progress.setValue(0)
        self._read_progress.setTextVisible(False)
        self._read_progress.setFixedHeight(3)
        self._read_progress.setStyleSheet(
            f"QProgressBar{{background:{BG2};border:none;border-radius:0;}}"
            f"QProgressBar::chunk{{background:{ACCENT};border-radius:0;}}")
        rw.addWidget(self._read_progress)
        rw.addWidget(hline())

        # Reader + Sage sidebar split
        reader_body = QSplitter(Qt.Orientation.Horizontal)
        reader_body.setHandleWidth(2)
        reader_body.setStyleSheet(f"QSplitter::handle{{background:{BORDER};}}")

        self.reader = QTextEdit()
        self.reader.setReadOnly(True)
        self.reader.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        self.reader.setStyleSheet(
            f"background:{BG};border:none;padding:36px 80px;"
            f"font-family:{FONT_BODY};font-size:18px;color:{TEXT};"
            f"selection-background-color:#1E3020;")
        self.reader.document().setDocumentMargin(0)
        self._apply_font()
        self.reader.verticalScrollBar().valueChanged.connect(self._on_scroll)
        # Touch scroll filter — prevents selection on swipe, converts to scroll
        self.reader.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self._touch_filter = _TouchScrollFilter(self.reader, self.reader)
        self.reader.installEventFilter(self._touch_filter)
        reader_body.addWidget(self.reader)

        # Sage reading companion panel
        self._sage_panel = QFrame()
        self._sage_panel.setFixedWidth(540)
        self._sage_panel.setStyleSheet(f"background:{BG2};border-left:1px solid {BORDER};")
        self._sage_panel.setVisible(False)
        sp = QVBoxLayout(self._sage_panel)
        sp.setContentsMargins(10,10,10,10)
        sp.setSpacing(8)
        sp.addWidget(lbl("✦  SAGE", ACCENT, 13, True))
        sp.addWidget(hline())

        # Model switcher chips
        model_row = QHBoxLayout()
        model_row.setSpacing(4)

        def _model_chip_style(active: bool) -> str:
            if active:
                return (f"background:{ACCENT};border:1px solid {ACCENT};color:{BG};"
                        f"font-size:9px;letter-spacing:0.5px;padding:3px 8px;border-radius:10px;font-weight:bold;")
            return (f"background:{BG3};border:1px solid {BORDER};color:{TEXT2};"
                    f"font-size:9px;letter-spacing:0.5px;padding:3px 8px;border-radius:10px;")

        self._sage_model_versatile_btn = QPushButton("Versatile")
        self._sage_model_instant_btn   = QPushButton("Instant")

        def _set_model(model: str):
            set_session_groq_model(model)
            self._sage_model_versatile_btn.setStyleSheet(
                _model_chip_style(model == GROQ_MODEL_VERSATILE))
            self._sage_model_instant_btn.setStyleSheet(
                _model_chip_style(model == GROQ_MODEL_INSTANT))

        self._sage_model_versatile_btn.clicked.connect(lambda: _set_model(GROQ_MODEL_VERSATILE))
        self._sage_model_instant_btn.clicked.connect(lambda: _set_model(GROQ_MODEL_INSTANT))

        # Default: highlight whichever matches saved settings (or versatile)
        _saved_model = matrix_data().get("settings", {}).get("groq_model", GROQ_MODEL_VERSATILE)
        _active_start = GROQ_MODEL_INSTANT if "instant" in _saved_model else GROQ_MODEL_VERSATILE
        self._sage_model_versatile_btn.setStyleSheet(_model_chip_style(_active_start == GROQ_MODEL_VERSATILE))
        self._sage_model_instant_btn.setStyleSheet(_model_chip_style(_active_start == GROQ_MODEL_INSTANT))

        model_row.addWidget(self._sage_model_versatile_btn)
        model_row.addWidget(self._sage_model_instant_btn)
        model_row.addStretch()
        sp.addLayout(model_row)

        # ── Template chips: Who is / What is / Ask ────────────────────────
        _tpl_base = (
            f"font-size:9px;letter-spacing:0.5px;padding:3px 10px;border-radius:10px;"
            f"border:1px solid {BORDER};background:#1e1915;color:#8a6d35;"
        )
        _tpl_hover_chapter = f"border-color:{ACCENT};color:{ACCENT};"
        _tpl_sel_chapter   = f"border-color:#8a6d35;color:{TEXT};background:#261f18;"
        _tpl_base_web      = (
            f"font-size:9px;letter-spacing:0.5px;padding:3px 10px;border-radius:10px;"
            f"border:1px solid #2a3a45;background:#151c22;color:#6ab0d4;"
        )
        _tpl_sel_web = f"border-color:#4a8aaa;color:#9dd0f0;background:#1a2830;"

        def _tpl_style(kind: str, selected: bool) -> str:
            if kind == "web":
                base = _tpl_base_web
                return base + (_tpl_sel_web if selected else "")
            base = _tpl_base
            return base + (_tpl_sel_chapter if selected else "")

        self._sage_tpl_whois  = QPushButton("Who is")
        self._sage_tpl_whatis = QPushButton("What is")
        self._sage_tpl_ask    = QPushButton("Ask")
        self._sage_active_tpl: str | None = None  # "whois" | "whatis" | "ask" | None

        def _apply_tpl_styles():
            t = self._sage_active_tpl
            self._sage_tpl_whois.setStyleSheet(_tpl_style("chapter", t == "whois"))
            self._sage_tpl_whatis.setStyleSheet(_tpl_style("chapter", t == "whatis"))
            self._sage_tpl_ask.setStyleSheet(_tpl_style("web", t == "ask"))

        def _set_tpl(name: str, prefix: str):
            if self._sage_active_tpl == name:
                # toggle off
                self._sage_active_tpl = None
                _apply_tpl_styles()
                self._sage_q.setPlaceholderText("Who is Feng Yuan?  /  What is the Spirit Sea?")
                return
            self._sage_active_tpl = name
            _apply_tpl_styles()
            current = self._sage_q.text().strip()
            # Strip any previous prefix before applying new one
            for p in ("Who is ", "What is ", ""):
                if current.lower().startswith(p.lower()) and p:
                    current = current[len(p):].lstrip()
                    break
            self._sage_q.setText(prefix + current)
            self._sage_q.setFocus()
            self._sage_q.setCursorPosition(len(self._sage_q.text()))
            if name == "ask":
                self._sage_q.setPlaceholderText("Ask anything… (web search)")
            elif name == "whois":
                self._sage_q.setPlaceholderText("Who is Feng Yuan?")
            else:
                self._sage_q.setPlaceholderText("What is the Spirit Sea?")

        self._sage_tpl_whois.clicked.connect(lambda: _set_tpl("whois",  "Who is "))
        self._sage_tpl_whatis.clicked.connect(lambda: _set_tpl("whatis", "What is "))
        self._sage_tpl_ask.clicked.connect(lambda: _set_tpl("ask",    ""))

        _apply_tpl_styles()

        tpl_row = QHBoxLayout()
        tpl_row.setSpacing(4)
        tpl_row.addWidget(self._sage_tpl_whois)
        tpl_row.addWidget(self._sage_tpl_whatis)
        tpl_row.addWidget(self._sage_tpl_ask)
        tpl_row.addStretch()
        sp.addLayout(tpl_row)

        self._sage_q = QLineEdit()
        self._sage_q.setPlaceholderText("Who is Feng Yuan?  /  What is the Spirit Sea?")
        self._sage_q.setStyleSheet(
            f"background:{BG3};border:1px solid {BORDER};color:{TEXT};"
            f"font-size:13px;padding:8px;border-radius:4px;")
        self._sage_q.returnPressed.connect(self._sage_ask)
        sp.addWidget(self._sage_q)
        self._sage_ask_btn = btn("Ask Sage", "accent", self._sage_ask)
        sp.addWidget(self._sage_ask_btn)

        # Web label — shown above answer box for Ask (web) queries
        self._sage_web_label = QLabel("🌐  WEB RESULT")
        self._sage_web_label.setStyleSheet(
            f"background:#151c22;border:1px solid #2a3a45;border-bottom:none;"
            f"color:#6ab0d4;font-size:9px;letter-spacing:1px;padding:4px 8px;"
            f"border-radius:4px 4px 0 0;")
        self._sage_web_label.setVisible(False)
        sp.addWidget(self._sage_web_label)

        self._sage_answer = QTextEdit()
        self._sage_answer.setReadOnly(True)
        self._sage_answer.setStyleSheet(
            f"background:{BG3};border:none;padding:12px;"
            f"font-family:{FONT_BODY};font-size:13px;color:{TEXT};line-height:1.7;")
        sp.addWidget(self._sage_answer, 1)
        self._sage_busy = QProgressBar()
        self._sage_busy.setRange(0,0)
        self._sage_busy.setVisible(False); self._sage_busy.setFixedHeight(3)
        sp.addWidget(self._sage_busy)
        reader_body.addWidget(self._sage_panel)

        # ── Lens panel ────────────────────────────────────────────────────────
        self._lens_panel = QFrame()
        self._lens_panel.setFixedWidth(540)
        self._lens_panel.setStyleSheet(f"background:{BG2};border-left:1px solid {BORDER};")
        self._lens_panel.setVisible(False)
        lp = QVBoxLayout(self._lens_panel)
        lp.setContentsMargins(14, 14, 14, 14)
        lp.setSpacing(10)

        # Header
        lens_hdr = QHBoxLayout()
        lens_hdr.addWidget(lbl("◈  LENS", ACCENT, 10, True))
        lens_close = QPushButton("✕")
        lens_close.setStyleSheet(f"background:transparent;border:none;color:{MUTED};font-size:12px;")
        lens_close.clicked.connect(self._toggle_lens_panel)
        lens_hdr.addStretch(); lens_hdr.addWidget(lens_close)
        lp.addLayout(lens_hdr)
        lp.addWidget(hline())

        # Paste area
        lens_hint = QLabel("Paste a character or place description:")
        lens_hint.setStyleSheet(f"color:{MUTED};font-size:10px;letter-spacing:0.5px;")
        lp.addWidget(lens_hint)

        self._lens_input = QTextEdit()
        self._lens_input.setPlaceholderText(
            'e.g. She had silver hair that fell to her waist, eyes like shards of moonstone...')
        self._lens_input.setFixedHeight(110)
        self._lens_input.setStyleSheet(
            f"background:{BG3};border:1px solid {BORDER};color:{TEXT};"
            f"font-family:{FONT_BODY};font-size:12px;padding:8px;border-radius:3px;")
        lp.addWidget(self._lens_input)

        # Source selector
        src_row = QHBoxLayout()
        src_lbl = QLabel("SOURCE")
        src_lbl.setStyleSheet(f"color:{MUTED};font-size:9px;letter-spacing:1px;")
        src_row.addWidget(src_lbl)
        self._lens_src_btns = {}
        for src_id, src_label in [("flux", "FLUX"), ("anime", "ANIME")]:
            b = QPushButton(src_label)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton{{background:{BG3};border:1px solid {BORDER};color:{MUTED};"
                f"font-size:9px;letter-spacing:1px;padding:4px 10px;border-radius:3px;}}"
                f"QPushButton:checked{{background:{ACCENT};color:{BG};border-color:{ACCENT};}}"
                f"QPushButton:hover:!checked{{color:{TEXT};}}"
            )
            b.clicked.connect(lambda checked, sid=src_id: self._lens_set_source(sid))
            src_row.addWidget(b)
            self._lens_src_btns[src_id] = b
        src_row.addStretch()
        lp.addLayout(src_row)
        self._lens_source = "flux"
        self._lens_src_btns["flux"].setChecked(True)

        # Generate button
        self._lens_btn = QPushButton("◈  VISUALIZE")
        self._lens_btn.setStyleSheet(
            f"background:{ACCENT};color:{BG};border:none;font-weight:bold;"
            f"font-size:9px;letter-spacing:1.5px;padding:9px;border-radius:3px;")
        self._lens_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lens_btn.clicked.connect(self._lens_generate)
        lp.addWidget(self._lens_btn)

        # Status label
        self._lens_status = QLabel("")
        self._lens_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lens_status.setStyleSheet(f"color:{MUTED};font-size:10px;")
        lp.addWidget(self._lens_status)

        # Image display
        self._lens_image_lbl = QLabel()
        self._lens_image_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lens_image_lbl.setMinimumHeight(200)
        self._lens_image_lbl.setStyleSheet(
            f"background:{BG3};border-radius:4px;color:{MUTED};font-size:11px;")
        self._lens_image_lbl.setText("Image will appear here")
        lp.addWidget(self._lens_image_lbl, 1)

        # Catalogue notes panel
        _CatPanel = _catalogue_panel_class()
        if _CatPanel:
            self._catalogue_panel = _CatPanel()
            reader_body.addWidget(self._catalogue_panel)
        else:
            self._catalogue_panel = None

        # ── Plugin slot: reader_sidebar ───────────────────────────────────────
        try:
            from plugin_manager import SlotHost as _SH, SlotRegistry as _SR
            self._reader_slot = _SH("reader_sidebar")
            self._reader_slot.setMinimumWidth(0)
            reader_body.addWidget(self._reader_slot)

            def _on_sidebar_changed():
                """Expand/collapse the sidebar splitter when a plugin registers/unregisters."""
                has = bool(_SR.instance().entries("reader_sidebar"))
                sizes = self._reader_body.sizes()
                if has and sizes[-1] < 50:
                    # Give sidebar 300px taken from reader
                    total = sum(sizes)
                    new_reader = max(300, sizes[0] - 300)
                    self._reader_body.setSizes([new_reader, sizes[1], sizes[2], 300])
                elif not has and sizes[-1] > 0:
                    self._reader_body.setSizes([sizes[0] + sizes[-1], sizes[1], sizes[2], 0])
            _SR.instance().add_listener("reader_sidebar", _on_sidebar_changed)
        except Exception:
            self._reader_slot = None

        # Lens panel — must be last in splitter so index [-1] is always lens
        reader_body.addWidget(self._lens_panel)

        self._reader_body = reader_body  # store ref for notes panel toggle
        reader_body.setSizes([900, 320, 0, 0, 0])
        rw.addWidget(reader_body, 1)

        # ── Bottom chapter navigation bar ────────────────────────────────
        self._bottom_nav = QFrame()
        self._bottom_nav.setStyleSheet(
            f"QFrame{{background:{BG2};border-top:1px solid {BORDER};}}")
        self._bottom_nav.setVisible(False)
        bnv = QHBoxLayout(self._bottom_nav)
        bnv.setContentsMargins(20, 10, 20, 10); bnv.setSpacing(10)

        self._bn_prev = QPushButton("← PREV")
        self._bn_prev.setFixedHeight(34)
        self._bn_prev.setStyleSheet(
            f"QPushButton{{background:transparent;color:{MUTED};border:1px solid {BORDER};"
            f"border-radius:3px;font-size:9px;letter-spacing:1px;padding:0 16px;}}"
            f"QPushButton:hover{{background:{BG3};color:{TEXT};border-color:{ACCENT};}}"
            f"QPushButton:disabled{{color:{BORDER};border-color:{BG2};}}")
        self._bn_prev.clicked.connect(self._prev_chapter)

        self._bn_toc = QPushButton("≡  CHAPTERS")
        self._bn_toc.setFixedHeight(34)
        self._bn_toc.setStyleSheet(
            f"QPushButton{{background:transparent;color:{MUTED};border:1px solid {BORDER};"
            f"border-radius:3px;font-size:9px;letter-spacing:1px;padding:0 14px;}}"
            f"QPushButton:hover{{background:{BG3};color:{TEXT};}}")
        self._bn_toc.clicked.connect(self._show_chapter_list)

        self._bn_next = QPushButton("NEXT →")
        self._bn_next.setFixedHeight(34)
        self._bn_next.setStyleSheet(
            f"QPushButton{{background:{ACCENT};color:{BG};border:none;"
            f"border-radius:3px;font-size:9px;font-weight:bold;letter-spacing:1px;padding:0 20px;}}"
            f"QPushButton:hover{{background:#F0D98A;color:{BG};}}"
            f"QPushButton:disabled{{background:{BG3};color:{MUTED};}}")
        self._bn_next.clicked.connect(self._next_chapter)

        self.reader_status = QLabel("")
        self.reader_status.setStyleSheet(f"color:{MUTED};font-size:10px;letter-spacing:1px;")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0,0)
        self.progress_bar.setVisible(False); self.progress_bar.setFixedHeight(2)
        bnv.addWidget(self._bn_prev)
        bnv.addWidget(self._bn_toc)
        bnv.addStretch()
        bnv.addWidget(self.reader_status)
        bnv.addWidget(self.progress_bar)
        bnv.addStretch()
        bnv.addWidget(self._bn_next)
        rw.addWidget(self._bottom_nav)

        status_row = QHBoxLayout()
        rw.addLayout(status_row)
        self._right_stack.addWidget(reader_w)   # index 3

        # ── 2: Downloads panel ────────────────────────────────────────────────
        dl_w = QWidget()
        dl_w.setStyleSheet(f"background:{BG};")
        dlv = QVBoxLayout(dl_w)
        dlv.setContentsMargins(20, 16, 20, 16)
        dlv.setSpacing(12)

        dl_hdr = QHBoxLayout()
        dl_title = QLabel("↓  DOWNLOADS")
        dl_title.setStyleSheet(
            f"font-size:13px;font-weight:bold;color:{ACCENT};"
            f"letter-spacing:2px;font-family:{FONT_DISPLAY};")
        self._dl_badge = QLabel("")
        self._dl_badge.setStyleSheet(f"color:{MUTED};font-size:10px;margin-left:8px;")
        dl_close = QPushButton("✕  CLOSE")
        dl_close.setStyleSheet(
            f"background:transparent;border:1px solid {BORDER};color:{MUTED};"
            f"font-size:8px;letter-spacing:1px;padding:3px 8px;border-radius:3px;")
        dl_close.clicked.connect(lambda: self._right_stack.setCurrentIndex(getattr(self, "_current_tab_idx", 0)))
        dl_hdr.addWidget(dl_title); dl_hdr.addWidget(self._dl_badge); dl_hdr.addStretch(); dl_hdr.addWidget(dl_close)
        dlv.addLayout(dl_hdr)
        dlv.addWidget(hline())

        self._dl_panel_list = QWidget()
        self._dl_panel_list.setLayout(QVBoxLayout())
        self._dl_panel_list.layout().setContentsMargins(0, 0, 0, 0)
        self._dl_panel_list.layout().setSpacing(8)

        dl_scroll = QScrollArea()
        dl_scroll.setWidget(self._dl_panel_list)
        dl_scroll.setWidgetResizable(True)
        dl_scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        dlv.addWidget(dl_scroll, 1)

        self._right_stack.addWidget(dl_w)   # index 4

        rv.addWidget(self._right_stack, 1)
        root.addWidget(right, 1)

        # Start on Jump In tab
        self._switch_tab(0)


    # ── Tab switching ──────────────────────────────────────────────────────────
    def _switch_tab(self, idx: int):
        self._current_tab_idx = idx
        self._right_stack.setCurrentIndex(idx)
        for i, b in enumerate(self._tab_btns):
            b.setStyleSheet(
                self._tab_btn_style_active if i == idx else self._tab_btn_style_idle)

    # ── BookDetailDialog signal handlers ───────────────────────────────────────
    def _detail_delete_name(self, name: str):
        """Delete a book by name — called from BookDetailDialog signal."""
        self._detail_book_name = name
        self._detail_book      = legion_data().get("books", {}).get(name, {})
        self._detail_from_list = "jumpin"
        self._detail_delete()

    def _detail_reset_time_name(self, name: str):
        self._detail_book_name = name
        self._detail_reset_time()

    def _detail_download_name(self, name: str):
        ld   = legion_data()
        book = ld.get("books", {}).get(name, {})
        self._detail_book_name = name
        self._detail_book      = book
        self._detail_from_list = "jumpin"
        self._detail_download()

    def _detail_move_to_bookmarks(self, name: str):
        """Move a book from Jump In to Bookmarks (completed by default)."""
        from PyQt6.QtWidgets import QInputDialog
        statuses = ["Completed", "Reading", "Plan to Read", "Dropped"]
        status, ok = QInputDialog.getItem(
            self, "Move to Bookmarks",
            f"Move '{name}' to Bookmarks as:",
            statuses, 0, False
        )
        if not ok:
            return
        # Add to bookmarks
        bm = bookmarks_data()
        bm.setdefault("bookmarks", {})
        ld   = legion_data()
        book = ld.get("books", {}).get(name, {})
        bm["bookmarks"][name] = {
            "title":    name,
            "url":      book.get("current_url", "") or book.get("url", ""),
            "status":   status,
            "metadata": book.get("metadata", {}),
        }
        save_json(LEGION_BOOKMARKS, bm)
        # Remove from Jump In
        ld["books"].pop(name, None)
        save_json(LEGION_PROGRESS, ld)
        # Refresh both lists
        self.refresh()
        log.legion.info("Book moved to bookmarks", book=name, status=status)

    # ── Discovery — source / browse / search ───────────────────────────────────

    def _on_rs_changed(self, key, value):
        self._rs[key] = value
        
        # Update UI labels
        if key == "font_size":
            self._rs_fs_val.setText(f"{value}px")
            self._font_size = value
        elif key == "line_height":
            self._rs_lh_val.setText(f"{value:.1f}")
        elif key == "padding_h":
            self._rs_hm_val.setText(f"{value}px")
        elif key == "padding_v":
            self._rs_vp_val.setText(f"{value}px")
        elif key == "bg_mode":
            self._update_bg_btn_styles()
            
        self._apply_font()
        
        # Debounced save
        if not hasattr(self, "_rs_save_timer"):
            self._rs_save_timer = QTimer(self)
            self._rs_save_timer.setSingleShot(True)
            self._rs_save_timer.setInterval(500)
            self._rs_save_timer.timeout.connect(self._save_rs)
        self._rs_save_timer.start()

    def _update_bg_btn_styles(self):
        for mode, btn in self._bg_btns.items():
            active = (self._rs["bg_mode"] == mode)
            btn.setChecked(active)
            if active:
                btn.setStyleSheet(f"background:{ACCENT}; color:{BG}; font-size:9px; border-radius:3px;")
            else:
                btn.setStyleSheet(f"background:{BG3}; color:{TEXT2}; border:1px solid {BORDER}; font-size:9px; border-radius:3px;")

    def _reset_rs(self):
        self._rs = dict(self._rs_defaults)
        self._font_size = self._rs["font_size"]
        
        # Sync sliders
        self._rs_fs_slider.setValue(self._rs["font_size"])
        self._rs_lh_slider.setValue(int(self._rs["line_height"] * 100))
        self._rs_hm_slider.setValue(self._rs["padding_h"])
        self._rs_vp_slider.setValue(self._rs["padding_v"])
        
        self._update_bg_btn_styles()
        self._apply_font()
        self._save_rs()

    def _save_rs(self):
        ld = legion_data()
        ld["reader_settings"] = dict(self._rs)
        save_json(LEGION_PROGRESS, ld)

    def _toggle_sage_panel(self):
        visible = self._sage_panel.isVisible()
        sizes = self._reader_body.sizes()  # [rs_panel, reader, sage, notes/slot]
        if visible:
            self._sage_panel.setVisible(False)
            # Return sage width to reader; leave rs_panel and notes untouched
            self._reader_body.setSizes([sizes[0], sizes[1] + sizes[2], 0, sizes[3]])
            self._sage_top_btn.setText("✦ SAGE")
        else:
            self._sage_panel.setVisible(True)
            sage_w  = 420
            reader_w = max(200, sizes[1] - sage_w)
            self._reader_body.setSizes([sizes[0], reader_w, sage_w, sizes[3]])
            self._sage_top_btn.setText("✕ SAGE")

    def _toggle_lens_panel(self):
        visible = self._lens_panel.isVisible()
        sizes = list(self._reader_body.sizes())  # lens is always last
        if visible:
            self._lens_panel.setVisible(False)
            sizes[1] += sizes[-1]
            sizes[-1] = 0
            self._reader_body.setSizes(sizes)
            self._lens_top_btn.setText("◈ LENS")
        else:
            self._lens_panel.setVisible(True)
            lens_w   = 540
            sizes[1] = max(300, sizes[1] - lens_w)
            sizes[-1] = lens_w
            self._reader_body.setSizes(sizes)
            self._lens_top_btn.setText("✕ LENS")

    def _lens_set_source(self, src_id: str):
        self._lens_source = src_id
        for sid, btn in self._lens_src_btns.items():
            btn.setChecked(sid == src_id)
        # Show cached result for this source immediately if available
        desc = self._lens_input.toPlainText().strip()
        cache = getattr(self, "_lens_cache", {})
        cached_px = cache.get((desc, src_id))
        if cached_px:
            self._lens_status.setText("")
            scaled = cached_px.scaled(
                512, 768,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._lens_image_lbl.setPixmap(scaled)
        else:
            self._lens_image_lbl.setText("Image will appear here")
            self._lens_status.setText("")

    def _lens_generate(self):
        desc = self._lens_input.toPlainText().strip()
        if not desc:
            self._lens_status.setText("Paste a description first.")
            return
        src = getattr(self, "_lens_source", "flux")
        # Return cached result instantly if prompt+source already generated
        cache = getattr(self, "_lens_cache", {})
        if (desc, src) in cache:
            self._lens_on_result(cache[(desc, src)])
            return
        # Cancel any running worker before starting a new one
        if getattr(self, "_lens_worker", None) is not None:
            try:
                self._lens_worker.done.disconnect()
                self._lens_worker.error.disconnect()
                self._lens_worker.finished.disconnect()
            except Exception:
                pass
            self._lens_worker = None
        self._lens_btn.setEnabled(False)
        self._lens_status.setText("Generating…")
        self._lens_image_lbl.setText("")
        self._lens_worker = _LensWorker(desc, source=src)
        self._lens_worker.done.connect(lambda px, d=desc, s=src: self._lens_on_result(px, d, s))
        self._lens_worker.error.connect(self._lens_on_error)
        self._lens_worker.finished.connect(lambda: setattr(self, "_lens_worker", None))
        self._lens_worker.start()

    def _lens_on_result(self, pixmap, desc=None, src=None):
        # Store in cache if we know the key
        if desc is not None and src is not None:
            if not hasattr(self, "_lens_cache"):
                self._lens_cache = {}
            self._lens_cache[(desc, src)] = pixmap
        self._lens_btn.setEnabled(True)
        self._lens_status.setText("")
        scaled = pixmap.scaled(
            512, 768,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._lens_image_lbl.setPixmap(scaled)

    def _lens_on_error(self, msg: str):
        self._lens_btn.setEnabled(True)
        self._lens_status.setText(f"Error: {msg}")
        self._lens_image_lbl.setText("Could not generate image.")

    def _toggle_notes_panel(self):
        if not self._catalogue_panel:
            self._notes_top_btn.setToolTip("catalogue.py not found — place it in the same folder as great_sage_gui.py")
            self.reader_status.setText("Notes unavailable — catalogue.py missing from app folder.")
            return
        visible = self._catalogue_panel.isVisible()
        sizes = self._reader_body.sizes()  # [rs_panel, reader, sage, notes/slot]
        if visible:
            # Collapse notes: return its width to reader
            self._catalogue_panel.setVisible(False)
            self._reader_body.setSizes([sizes[0], sizes[1] + sizes[3], sizes[2], 0])
            self._notes_top_btn.setText("✎ NOTES")
        else:
            # Expand notes to 320px, taken from reader (never shrink Sage)
            self._catalogue_panel.setVisible(True)
            notes_w  = 320
            reader_w = max(300, sizes[1] - notes_w)
            self._reader_body.setSizes([sizes[0], reader_w, sizes[2], notes_w])
            self._notes_top_btn.setText("✕ NOTES")

    def _sage_ask(self):
        q = self._sage_q.text().strip()
        if not q: return
        book    = self._current_book or "this book"
        cur_ch  = self._current_ch_num or 0
        is_web  = getattr(self, "_sage_active_tpl", None) == "ask"
        self._sage_busy.setVisible(True)
        self._sage_ask_btn.setEnabled(False)
        self._sage_web_label.setVisible(False)
        self._sage_answer.setPlainText("Searching the web…" if is_web else "Scanning chapters…")
        self._sage_worker = _SageCompanionWorker(
            q, book, current_chapter=cur_ch, web_search=is_web)
        self._sage_worker.done.connect(lambda ans, _w=is_web: self._sage_answered(ans, _w))
        self._sage_worker.start()

    def _sage_answered(self, answer: str, is_web: bool = False):
        self._sage_busy.setVisible(False)
        self._sage_ask_btn.setEnabled(True)
        self._sage_web_label.setVisible(is_web)
        # Square off top corners of answer box when web label is showing
        if is_web:
            from gs_theme import BG3, FONT_BODY, TEXT
            self._sage_answer.setStyleSheet(
                f"background:{BG3};border:none;padding:12px;"
                f"font-family:{FONT_BODY};font-size:13px;color:{TEXT};line-height:1.7;"
                f"border-radius:0 0 4px 4px;")
        else:
            from gs_theme import BG3, FONT_BODY, TEXT
            self._sage_answer.setStyleSheet(
                f"background:{BG3};border:none;padding:12px;"
                f"font-family:{FONT_BODY};font-size:13px;color:{TEXT};line-height:1.7;")
        self._sage_answer.setPlainText(answer or "(No response)")

    def _apply_font(self):
        bg, fg = _BG_MODES.get(self._rs.get("bg_mode", "dark"), (BG3, TEXT))
        ph = self._rs.get("padding_h", 80)
        pv = self._rs.get("padding_v", 36)
        lh = self._rs.get("line_height", 1.9)
        fs = self._rs.get("font_size", self._font_size)
        self.reader.setStyleSheet(
            f"background:{bg};color:{fg};border:none;"
            f"padding:{pv}px {ph}px;"
            f"font-family:{FONT_BODY};font-size:{fs}px;line-height:{lh};")

    def _on_scroll(self, value=None):
        sb  = self.reader.verticalScrollBar()
        top = sb.minimum()
        bot = sb.maximum()
        if bot <= top:
            frac = 1.0
        else:
            v    = sb.value() if value is None else value
            frac = (v - top) / (bot - top)
        # Update the reading progress bar
        self._read_progress.setValue(int(frac * 1000))
        # Show bottom nav when reader is near the end (>88%)
        if hasattr(self, "_bottom_nav"):
            self._bottom_nav.setVisible(frac >= 0.88)
        # Save scroll position per chapter (not during initial load)
        if self._current_book and self._current_ch_num > 0 and not self._chapter_loading:
            self._scroll_positions \
                .setdefault(self._current_book, {})[self._current_ch_num] = frac

    def _restore_scroll(self, ch_num):
        """Restore saved scroll position for this chapter, if any."""
        frac = self._scroll_positions.get(self._current_book, {}).get(ch_num, 0.0)
        if frac <= 0.0:
            return
        sb = self.reader.verticalScrollBar()
        # Scroll bar range may not be fully set yet right after setPlainText;
        # defer slightly to let Qt lay out the text
        def _apply():
            sb.setValue(int(sb.minimum() + frac * (sb.maximum() - sb.minimum())))
        QTimer.singleShot(50, _apply)

    def _toggle_menu(self):
        """Show a small popup menu with Downloads option."""
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {BG2};
                border: 1px solid {BORDER};
                color: {TEXT};
                font-size: 12px;
                padding: 4px 0;
            }}
            QMenu::item {{
                padding: 8px 24px 8px 16px;
                letter-spacing: 0.5px;
            }}
            QMenu::item:selected {{
                background: {BG3};
                color: {ACCENT};
            }}
            QMenu::separator {{
                height: 1px;
                background: {BORDER};
                margin: 4px 0;
            }}
        """)
        dl_action = menu.addAction("↓  Downloads")
        action = menu.exec(self._menu_btn.mapToGlobal(
            self._menu_btn.rect().bottomLeft()))
        if action == dl_action:
            self._open_downloads_panel()

    def _open_downloads_panel(self):
        """Switch right panel to downloads view and populate it."""
        self._refresh_downloads_panel()
        self._right_stack.setCurrentIndex(4)
        # Start a refresh timer while panel is visible
        if not hasattr(self, "_dl_panel_timer"):
            self._dl_panel_timer = QTimer(self)
            self._dl_panel_timer.timeout.connect(self._refresh_downloads_panel)
        self._dl_panel_timer.start(1500)

    def _refresh_downloads_panel(self):
        """Rebuild the downloads panel content from current legion data."""
        # Stop if panel not visible
        if self._right_stack.currentIndex() != 4:
            if hasattr(self, "_dl_panel_timer"):
                self._dl_panel_timer.stop()
            return

        ld  = legion_data()
        books = ld.get("books", {})
        mod, _ = _get_legion_mod()

        # Update header count badge
        count = 0
        if mod and hasattr(mod, "download_manager"):
            count = len(mod.download_manager.active_downloads) + \
                    len(mod.download_manager.get_queue_snapshot())
        if hasattr(self, "_dl_badge"):
            self._dl_badge.setText(f"({count})" if count else "")
        
        # Find header to insert/update badge
        # In _build, downloads header is at the top of the layout
        # We'll just find it by name if possible, or search
        # Actually I'll just look at _build again to see where it is
        
        # Clear existing rows
        layout = self._dl_panel_list.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        has_any = False
        STATUS_ORDER = ["downloading", "queued", "paused", "failed", "completed", "idle"]

        sorted_books = sorted(
            [(n, b) for n, b in books.items() if b.get("download_state", {}).get("status", "idle") != "idle"],
            key=lambda x: STATUS_ORDER.index(x[1].get("download_state", {}).get("status", "idle"))
        )

        for name, book in sorted_books:
            has_any = True
            dl   = book.get("download_state", {})
            stat = dl.get("status", "idle")
            cnt  = dl.get("total_chapters_downloaded", 0)
            fails= len(dl.get("failed_chapters", []))

            row = QWidget()
            row.setStyleSheet(f"background:{BG2};border-radius:6px;")
            rl  = QVBoxLayout(row)
            rl.setContentsMargins(14, 10, 14, 10)
            rl.setSpacing(4)

            # Title + badge
            top = QHBoxLayout()
            top.setSpacing(8)
            name_lbl = QLabel(name)
            name_lbl.setStyleSheet(f"font-size:13px;font-weight:bold;color:{TEXT};")
            badge_colors = {
                "downloading": ("#00C896", "#001A10"),
                "queued":      ("#A080FF", "#0D0820"),
                "paused":      ("#FFB020", "#1A0D00"),
                "failed":      ("#FF4060", "#1A0008"),
                "completed":   ("#4080FF", "#000D1A"),
            }
            bc, b_bg = badge_colors.get(stat, (MUTED, BG3))
            badge_text = {
                "downloading": f"⏳ Downloading",
                "queued":      "⏸ Queued",
                "paused":      "⏸ Paused",
                "failed":      "❌ Failed",
                "completed":   "✅ Complete",
            }.get(stat, stat)
            badge = QLabel(badge_text)
            badge.setStyleSheet(
                f"background:{b_bg};color:{bc};border:1px solid {bc};"
                f"font-size:9px;letter-spacing:1px;padding:2px 8px;border-radius:3px;")
            top.addWidget(name_lbl); top.addStretch(); top.addWidget(badge)
            rl.addLayout(top)

            # Chapter count / Queue position
            if stat == "queued":
                position = 1
                if mod and hasattr(mod, "download_manager"):
                    snapshot = mod.download_manager.get_queue_snapshot()
                    if name in snapshot:
                        position = snapshot.index(name) + 1
                info_lbl = QLabel(f"Position {position} in queue  ·  {cnt} chapters downloaded")
            else:
                info_parts = [f"{cnt} chapters downloaded"]
                if fails:
                    info_parts.append(f"{fails} failed")
                info_lbl = QLabel("  ·  ".join(info_parts))
            
            info_lbl.setStyleSheet(f"font-size:10px;color:{MUTED};letter-spacing:0.5px;")
            rl.addWidget(info_lbl)

            # Progress bar for downloading
            if stat == "downloading":
                pbar = QProgressBar()
                known_total = book.get("total_chapters")
                if known_total and known_total > 0:
                    pbar.setRange(0, known_total)
                    pbar.setValue(cnt)
                else:
                    pbar.setRange(0, 0)
                pbar.setFixedHeight(3)
                pbar.setTextVisible(False)
                pbar.setStyleSheet(
                    f"QProgressBar{{background:{BG3};border:none;border-radius:0;}}"
                    f"QProgressBar::chunk{{background:{ACCENT};border-radius:0;}}")
                rl.addWidget(pbar)
                
                # ETA
                eta_text = ""
                if mod and hasattr(mod, "download_manager"):
                    rate = mod.download_manager.get_chapter_rate(name)
                    if rate > 0 and known_total and known_total > cnt:
                        remaining = known_total - cnt
                        minutes   = remaining / rate
                        if minutes < 60:
                            eta_text = f"~{int(minutes)}m remaining"
                        else:
                            eta_text = f"~{int(minutes/60)}h {int(minutes%60)}m remaining"
                if eta_text:
                    eta_lbl = QLabel(eta_text)
                    eta_lbl.setStyleSheet(f"font-size:9px;color:{MUTED};letter-spacing:0.5px;")
                    rl.addWidget(eta_lbl)

            # Action buttons row
            if stat in ("downloading", "queued", "paused"):
                btn_rl = QHBoxLayout()
                btn_rl.setSpacing(6)
                if stat == "downloading":
                    pb = QPushButton("⏸ Pause")
                    pb.setStyleSheet(
                        f"background:transparent;border:1px solid {BORDER};color:{TEXT2};"
                        f"font-size:9px;letter-spacing:1px;padding:4px 12px;border-radius:3px;")
                    pb.clicked.connect(lambda _, n=name: self._dl_panel_pause(n))
                    btn_rl.addWidget(pb)
                elif stat == "paused":
                    rb = QPushButton("▶ Resume")
                    rb.setStyleSheet(
                        f"background:transparent;border:1px solid {BORDER};color:{TEXT2};"
                        f"font-size:9px;letter-spacing:1px;padding:4px 12px;border-radius:3px;")
                    rb.clicked.connect(lambda _, n=name: self._dl_panel_resume(n))
                    btn_rl.addWidget(rb)
                cb = QPushButton("✕ Cancel")
                cb.setStyleSheet(
                    f"background:transparent;border:1px solid #2A1018;color:{RED};"
                    f"font-size:9px;letter-spacing:1px;padding:4px 12px;border-radius:3px;")
                cb.clicked.connect(lambda _, n=name: self._dl_panel_cancel(n))
                btn_rl.addWidget(cb); btn_rl.addStretch()
                rl.addLayout(btn_rl)

            layout.addWidget(row)

        if not has_any:
            empty = QLabel("No active downloads.")
            empty.setStyleSheet(f"color:{MUTED};font-size:13px;padding:20px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(empty)

        layout.addStretch()

    def _dl_panel_pause(self, name):
        mod, _ = _get_legion_mod()
        if mod and hasattr(mod, "download_manager"):
            mod.download_manager.pause_download(name)

    def _dl_panel_resume(self, name):
        mod, _ = _get_legion_mod()
        if not mod: return
        ld = legion_data()
        book_data = ld.get("books", {}).get(name)
        if book_data:
            book_data["download_state"]["status"] = "queued"
            book_data["download_state"]["pause_requested"] = False
            save_json(LEGION_PROGRESS, ld)
            if hasattr(mod, "download_manager"):
                mod.download_manager.queue_download(name, book_data, ld)

    def _dl_panel_cancel(self, name):
        ld = legion_data()
        book_data = ld.get("books", {}).get(name)
        if book_data:
            book_data["download_state"]["status"] = "cancelled"
            save_json(LEGION_PROGRESS, ld)
        self._refresh_downloads_panel()

    def _show_toast(self, message):
        """Sync notification — just refresh downloads panel if it's open."""
        if self._right_stack.currentIndex() == 4:
            self._refresh_downloads_panel()

    def refresh(self):
        ld = legion_data(); books = ld.get("books",{})
        self.jumpin_list.clear()
        self._book_data = {}
        sorted_books = sorted(books.items(),
            key=lambda x: x[1].get("last_read", x[1].get("chapters_read", 0)),
            reverse=True)
        for name, book in sorted_books:
            ch = book.get("chapters_read", 0); w = book.get("words_read", 0)
            display = name if len(name) <= 20 else name[:18] + "…"
            item = QListWidgetItem(display)
            item.setToolTip(f"{name}\nChapter {ch} · {w:,} words read")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
            cover_set = False
            try:
                from plugins.book_covers import get_cover
                from PyQt6.QtGui import QIcon
                source_url = book.get("current_url", "") or book.get("url", "")
                px = get_cover(name, source_url)
                if px and not px.isNull():
                    item.setIcon(QIcon(px))
                    cover_set = True
            except Exception:
                pass
            if not cover_set:
                from PyQt6.QtGui import QIcon
                item.setIcon(QIcon(self._make_placeholder_cover(name, 100, 140)))
            self.jumpin_list.addItem(item)
            self._book_data[name] = book
        bm = bookmarks_data()
        for lst_name, lw in self._bm_lists.items():
            lw.clear()
            for e in bm.get(lst_name,[]):
                t = e.get("title","?") if isinstance(e,dict) else str(e)
                item = QListWidgetItem(f"  {t}")
                item.setData(Qt.ItemDataRole.UserRole, e)
                item.setForeground(QColor(TEXT)); lw.addItem(item)

        # If HTML Legion requested to open a specific book in the native UI, do it once.
        try:
            mw = self.window()
            pending = getattr(mw, "_pending_legion_book", "") if mw else ""
        except Exception:
            pending = ""
        if pending:
            try:
                mw._pending_legion_book = ""
            except Exception:
                pass
            for i in range(self.jumpin_list.count()):
                it = self.jumpin_list.item(i)
                if it and it.data(Qt.ItemDataRole.UserRole) == pending:
                    self.jumpin_list.setCurrentItem(it)
                    self._book_clicked(it)
                    break

    def _book_clicked(self, item):
        name = item.data(Qt.ItemDataRole.UserRole)
        if not name: return
        b = self._book_data.get(name, {})
        self._current_book = name
        self._current_ch_num = 0
        self._total_ch_local = 0
        self._reading_local  = False
        self._next_url = ""; self._prev_url = ""
        # Auto-fetch metadata if missing
        url = b.get("current_url","") or b.get("url","")
        if url and not b.get("metadata"):
            self._refresh_meta_silent(name, url)
        dlg = BookDetailDialog(name, b, from_list="jumpin", parent=self)
        dlg.read_requested.connect(self._detail_read)
        dlg.delete_requested.connect(self._detail_delete_name)
        dlg.reset_time_requested.connect(self._detail_reset_time_name)
        dlg.download_requested.connect(self._detail_download_name)
        dlg.bookmark_requested.connect(self._detail_move_to_bookmarks)
        dlg.exec()

    def _bm_clicked(self, item, list_name):
        e = item.data(Qt.ItemDataRole.UserRole)
        if not e: return
        title = e.get("title","?") if isinstance(e,dict) else str(e)
        ld    = legion_data()
        prog  = ld.get("books",{}).get(title, {})
        entry = dict(e) if isinstance(e,dict) else {"title":title}
        for k in ("chapters_read","words_read","current_url","last_title",
                  "metadata","minutes_read","download_state"):
            if k in prog: entry.setdefault(k, prog[k])
        # Normalise: bookmark entries store the url under "url"; ensure
        # "current_url" is always populated so BookDetailDialog and
        # _detail_read can find it regardless of which key was used.
        if not entry.get("current_url"):
            entry["current_url"] = entry.get("url", "")
        self._current_book = title
        self._current_ch_num = 0
        self._total_ch_local = 0
        self._reading_local  = False
        self._next_url = ""; self._prev_url = ""
        dlg = BookDetailDialog(title, entry, self)
        dlg.read_requested.connect(self._detail_read)
        dlg.delete_requested.connect(self._detail_delete_name)
        dlg.reset_time_requested.connect(self._detail_reset_time_name)
        dlg.download_requested.connect(self._detail_download_name)
        dlg.exec()

    def _bm_double(self, item, list_name):
        """Keep for backward compat — delegates to _bm_clicked."""
        self._bm_clicked(item, list_name)

    def _refresh_meta_silent(self, name, url):
        """Silently fetch and cache metadata for a book without user interaction."""
        # self._meta_workers is already initialized in __init__
        w = _MetaRefreshWorker(url)
        log.debug(f"Adding worker: {type(w).__name__}", worker_id=id(w))
        
        # Connect finished signal to cleanup method
        w.finished.connect(lambda: self._cleanup_worker(w))
        
        # Modify the existing _done handler
        def _done(meta, err): # Removed worker=w from args since it's now handled by the outer scope lambda
            if not err and meta:
                ld = legion_data()
                if name in ld.get("books", {}):
                    ld["books"][name].setdefault("metadata", {}).update(meta)
                    save_json(LEGION_PROGRESS, ld)
                    if getattr(self, "_detail_book_name", "") == name:
                        self._detail_book = ld["books"][name]
                        self._book_data[name] = ld["books"][name]
                        self._show_detail(name, ld["books"][name],
                                          from_list=getattr(self, "_detail_from_list", "jumpin"))
            # Original cleanup removed, now handled by w.finished.connect(lambda: self._cleanup_worker(w))
        
        w.done.connect(_done)
        w.start()
        
        self._meta_workers.append(w)  # keep reference so GC doesn't destroy running thread

    def _show_detail(self, name, book, from_list="jumpin"):
        """Populate the detail panel and switch to it."""
        # Always refresh from disk so progress shown is current
        ld = legion_data()
        fresh = ld.get("books", {}).get(name)
        if fresh:
            book = fresh
            self._book_data[name] = book
        self._detail_book_name  = name
        self._detail_book       = book
        self._detail_from_list  = from_list

        meta     = book.get("metadata", {})
        ch_read  = book.get("chapters_read", 0)
        words    = book.get("words_read", 0)
        mins     = book.get("minutes_read", 0)
        last_ch  = book.get("last_title", "Not started")
        dl_state = book.get("download_state", {})
        dl_status= dl_state.get("status", "idle")
        dl_count = dl_state.get("total_chapters_downloaded", 0)

        self._detail_title.setText(name)

        # Metadata line
        meta_parts = []
        if meta.get("author"):  meta_parts.append(f"Author: {meta['author']}")
        if meta.get("genres"):  meta_parts.append(f"Genres: {meta['genres']}")
        if meta.get("status"):  meta_parts.append(f"Status: {meta['status']}")
        if meta.get("year"):    meta_parts.append(f"Year: {meta['year']}")
        if meta and not meta_parts:
            meta_parts.append("Info loaded")
        self._detail_meta.setText("   ·   ".join(meta_parts) if meta_parts else "No metadata yet — click ↻ Refresh Info")

        # Synopsis
        syn = (meta.get("synopsis","") or meta.get("description","")
               or meta.get("summary","") or meta.get("overview",""))
        if syn:
            self._detail_synopsis.setPlainText(syn[:2000] + ("..." if len(syn)>2000 else ""))
        elif meta:
            # We have metadata but no synopsis — show whatever text fields we have
            extra = " | ".join(f"{k}: {v}" for k,v in meta.items()
                               if isinstance(v,str) and len(v) > 10
                               and k not in ("url","image","cover","thumbnail"))
            self._detail_synopsis.setPlainText(extra[:1000] if extra else "No synopsis available.")
        else:
            self._detail_synopsis.setPlainText("No synopsis available.")

        # Progress — derive last chapter title from local file using chapters_read (authoritative)
        # last_title in JSON may be stale from old web scrapes at a lower chapter number
        prog_parts = []
        if ch_read:  prog_parts.append(f"{ch_read} chapters read")
        if words:    prog_parts.append(f"{words:,} words")
        mins_i = int(round(mins))
        if mins_i >= 60: prog_parts.append(f"{mins_i//60}h {mins_i%60}m")
        elif mins_i:     prog_parts.append(f"{mins_i}m")

        # Show the chapter the user is currently on using current_chapter (story number)
        last_label = ""
        cur_ch = book.get("current_chapter")
        if cur_ch:
            mod, _ = _get_legion_mod()
            if mod and hasattr(mod, "get_chapter_from_file"):
                try:
                    t, _ = mod.get_chapter_from_file(name, int(cur_ch))
                    if t:
                        last_label = f"Last: Ch.{cur_ch} — {t[:55]}"
                except Exception:
                    pass
            if not last_label:
                last_label = f"Last: Ch.{cur_ch}"
        if last_label:
            prog_parts.append(last_label)

        # "Not started yet" only when truly nothing has happened —
        # current_chapter > 0 means user has opened at least one chapter
        if not prog_parts:
            if cur_ch and int(cur_ch) > 0:
                prog_parts.append(f"Ch.{cur_ch} opened")
            else:
                prog_parts.append("Not started yet")

        self._detail_progress.setText("  ·  ".join(prog_parts))

        # Download status
        _last_err = dl_state.get('last_error', '')
        _fail_msg = f"❌ {_last_err}" if _last_err else f"❌ Failed ({len(dl_state.get('failed_chapters', []))} chapters failed)"
        dl_labels = {
            "downloading": f"⏳ Downloading... ({dl_count} chapters)",
            "completed":   f"✅ Downloaded ({dl_count} chapters)",
            "paused":      f"⏸ Paused ({dl_count} chapters downloaded)",
            "failed":      _fail_msg,
            "queued":      f"⏳ Queued ({dl_count} chapters so far)",
            "cancelled":   "✕ Download cancelled",
        }
        self._detail_dl_status.setText(dl_labels.get(dl_status, ""))

        self._update_detail_buttons(book)
        # _show_detail no longer drives the right stack; BookDetailDialog handles display

    def _update_detail_buttons(self, book):
        self._btn_read.setVisible(bool(book))
        self._btn_delete.setVisible(bool(book))
        self._btn_reset_time.setVisible(bool(book))

    def _detail_read(self):
        """Open the book at the last chapter the user was on."""
        # When called via signal from BookDetailDialog, _current_book is already set.
        # Fall back to _detail_book_name for legacy call paths.
        name = self._current_book or getattr(self, "_detail_book_name", None)
        if not name: return
        self._detail_book_name = name

        # Always read fresh from disk
        ld   = legion_data()
        book = ld.get("books", {}).get(name, {})
        self._book_data[name] = book
        self._detail_book     = book

        url = book.get("current_url", "") or book.get("url", "")

        # Auto-bookmark
        bm = bookmarks_data()

        # If legion_data has no URL (book lives only in Bookmarks, never added
        # to Jump In), fall back to whichever bookmark list has it.
        if not url:
            for _lst in bm.values():
                for _bm_e in _lst:
                    if isinstance(_bm_e, dict) and                             _bm_e.get("title", "").lower() == name.lower():
                        url = (_bm_e.get("current_url", "")
                               or _bm_e.get("url", ""))
                        if url:
                            break
                if url:
                    break

        already = any(
            (e.get("title", "") if isinstance(e, dict) else str(e)).lower() == name.lower()
            for lst in bm.values() for e in lst
        )
        if not already:
            bm.setdefault("reading", []).append({
                "title": name, "url": url,
                "metadata": book.get("metadata", {}),
                "added": time.time()
            })
            save_json(LEGION_BOOKMARKS, bm)

        # Get the real chapter list from the file (story numbers, e.g. [549, 550, ...])
        ch_list = []
        mod2, _ = _get_legion_mod()
        if mod2 and hasattr(mod2, "_get_chapter_list_from_file"):
            try:
                ch_list = mod2._get_chapter_list_from_file(name)
            except Exception:
                pass

        valid_nums = [n for n, _ in ch_list]  # ordered list of story chapter numbers

        if not valid_nums:
            # No local file — just open via URL
            self._load_chapter(1, url)
            return

        # Try to find the best chapter to resume from:
        # 1. saved current_chapter if it's a valid story number
        # 2. extract from last_title (e.g. "Ch.552 — Chapter 552 552: ...")
        # 3. fall back to the first downloaded chapter
        resume_ch = None

        saved = book.get("current_chapter")
        if saved and int(saved) in valid_nums:
            resume_ch = int(saved)

        if resume_ch is None:
            # Try to extract from last_title
            last_title = book.get("last_title", "")
            if last_title:
                m = re.search(r'[Cc]hapter\s+(\d+)', last_title)
                if m:
                    n = int(m.group(1))
                    if n in valid_nums:
                        resume_ch = n

        if resume_ch is None:
            resume_ch = valid_nums[0]  # first downloaded chapter

        # Fix the stale current_chapter in JSON right now so next time it's correct
        if book.get("current_chapter") != resume_ch:
            book["current_chapter"] = resume_ch
            save_json(LEGION_PROGRESS, ld)
            self._book_data[name] = book

        self._load_chapter(resume_ch, url)

    def _load_chapter(self, ch_num, fallback_url=""):
        """
        Central chapter loader.
        1. Try local .txt file for ch_num.
        2. If not found, try fallback_url via web scrape.
        3. If no url either, tell the user.

        Always updates _current_ch_num, _reading_local, _total_ch_local.
        """
        name = self._current_book
        if not name: return
        log.legion.info("Loading chapter", book=name, chapter=ch_num, has_url=bool(fallback_url))

        # Count how many chapters are in the local file
        mod, _ = _get_legion_mod()
        total_local = 0
        chapters = []  # always defined — prevents NameError in the body block below
        if mod and hasattr(mod, "_get_chapter_list_from_file"):
            try:
                chapters = mod._get_chapter_list_from_file(name)
                total_local = len(chapters)
            except Exception as e:
                log.legion.exc("Failed to get chapter list from file", e, book=name)
        self._total_ch_local = total_local

        # Try local file first
        file_ch = None
        if mod and hasattr(mod, "get_chapter_from_file") and ch_num >= 1:
            try:
                title_f, paras_f = mod.get_chapter_from_file(name, ch_num)
                if paras_f:
                    file_ch = (title_f, paras_f)
            except Exception: pass  # Ignored

        if file_ch:
            title_f, paras_f = file_ch
            self._current_ch_num = ch_num
            self._reading_local  = True
            self._right_stack.setCurrentIndex(3)
            self._read_progress.setValue(0)
            # Show real chapter number from title if available (e.g. "Chapter 549: ...")
            real_num = ch_num
            m_real = re.search(r'[Cc]hapter\s+(\d+)', title_f)
            if m_real:
                real_num = int(m_real.group(1))
            self._display_ch_num = real_num  # store for status bar
            self.chapter_title.setText(f"Ch.{real_num}  {title_f}")
            text = "\n\n".join(paras_f)
            # Disconnect scroll handler while loading to prevent it saving a false bottom position
            try: self.reader.verticalScrollBar().valueChanged.disconnect(self._on_scroll)
            except (TypeError, RuntimeError): pass  # not connected yet
            self.reader.setPlainText(text)
            self._chapter_loading = True
            self.reader.moveCursor(QTextCursor.MoveOperation.Start)
            if hasattr(self, '_eye_timer') and not self._eye_timer.isActive():
                self._eye_timer.start()
            self.reader.verticalScrollBar().setValue(0)

            def _finish_load():
                self.reader.moveCursor(QTextCursor.MoveOperation.Start)
                self.reader.verticalScrollBar().setValue(0)
                # Clear any stale saved position for this chapter
                self._scroll_positions.setdefault(self._current_book, {}).pop(ch_num, None)
                self._chapter_loading = False
                # Reconnect scroll handler now that we're settled at top
                try: self.reader.verticalScrollBar().valueChanged.connect(self._on_scroll)
                except (TypeError, RuntimeError): pass  # already connected

            QTimer.singleShot(200, _finish_load)
            words = len(text.split())

            # has_prev/has_next based on position in ordered chapter list
            ch_nums_ordered = [n for n, _ in chapters] if chapters else []
            try:
                idx_in_list = ch_nums_ordered.index(ch_num)
                has_prev = idx_in_list > 0
                has_next = idx_in_list < len(ch_nums_ordered) - 1
            except ValueError:
                has_prev = ch_num > 1
                has_next = total_local > 0

            display_num = getattr(self, "_display_ch_num", ch_num)
            pos_str = f"{idx_in_list + 1}/{len(ch_nums_ordered)}" if ch_nums_ordered else f"{ch_num}"
            nav_str = ""
            if has_prev: nav_str += "< Prev  "
            nav_str += f"Ch.{display_num} ({pos_str})"
            if has_next: nav_str += "  Next >"
            else:        nav_str += "  (end of downloads)"
            self.reader_status.setText(f"{words:,} words  ·  {nav_str}")
            self._prev_btn.setEnabled(has_prev)
            self._next_btn.setEnabled(has_next or bool(fallback_url))
            # Sync bottom nav buttons
            if hasattr(self, "_bn_prev"): self._bn_prev.setEnabled(has_prev)
            if hasattr(self, "_bn_next"): self._bn_next.setEnabled(has_next or bool(fallback_url))
            if hasattr(self, "_bottom_nav"): self._bottom_nav.setVisible(False)

            # (chapter_loading released by the scroll timer above)

            # Save current_chapter = open chapter; chapters_read = completed count (unchanged here)
            ld = legion_data(); book_data = ld.get("books", {}).get(name)
            if book_data:
                book_data["current_chapter"] = ch_num
                book_data["last_title"]      = title_f
                # words_read accumulated in _next_chapter on completion, not on every open
                save_json(LEGION_PROGRESS, ld)
                # Update in-memory cache
                self._book_data[name] = book_data
                for i in range(self.jumpin_list.count()):
                    it = self.jumpin_list.item(i)
                    if it and it.data(Qt.ItemDataRole.UserRole) == name:
                        it.setText(f"  {name}")
                        it.setToolTip(f"Chapter {ch_num} - {book_data.get('words_read',0):,} words read")
                        break
            # Record chapter open time for minutes_read tracking
            self._chapter_open_time = time.time()
            self._chapter_open_words = words
            self._words_tracked_this_chapter = False
            # Update catalogue panel context
            if self._catalogue_panel:
                self._catalogue_panel.set_context(name, ch_num)
            # Start/restart heartbeat timer — saves progress every 2 min while reading
            if not hasattr(self, "_heartbeat_timer"):
                self._heartbeat_timer = QTimer(self)
                self._heartbeat_timer.timeout.connect(self._heartbeat_save)
            self._heartbeat_timer.start(120_000)  # 2 minutes
            return

        # No local chapter — fall back to web scrape
        self._reading_local = False
        # Update notes panel context even for web-read chapters
        if self._catalogue_panel:
            self._catalogue_panel.set_context(name, ch_num)
        if fallback_url:
            self._right_stack.setCurrentIndex(3)
            self._load_url(fallback_url)
        else:
            book = self._book_data.get(name, {})
            saved_url = book.get("current_url","") or book.get("url","")
            if saved_url:
                self._right_stack.setCurrentIndex(3)
                self._load_url(saved_url)
            else:
                QMessageBox.information(self, "No Chapter",
                    f"Chapter {ch_num} not found in downloaded file\n"
                    f"and no URL is saved for this book.\n\n"
                    f"Downloaded: {total_local} chapters  |  Requested: Ch.{ch_num}")

    def _detail_download(self):
        name = getattr(self, "_detail_book_name", None)
        book = getattr(self, "_detail_book", {})
        if not name: return
        url = book.get("current_url","") or book.get("url","")
        if not url:
            log.legion.warning("Download attempted with no URL", book=name)
            QMessageBox.warning(self, "No URL", "No chapter URL saved — can't download."); return

        mod, err = _get_legion_mod()
        if not mod:
            log.legion.error("legion.py unavailable for download", error=err, book=name)
            QMessageBox.warning(self, "Error", f"Can't load legion.py:\n{err}"); return
        if not hasattr(mod, "download_manager"):
            QMessageBox.warning(self, "Error",
                "legion.py doesn't expose a DownloadManager.\n"
                "Make sure you're using the latest version of legion.py."); return

        # Confirm
        dl_state = book.get("download_state", {})
        already  = dl_state.get("total_chapters_downloaded", 0)
        msg = f"Download all chapters of '{name}' in the background?"
        if already:
            msg += f"\n\n{already} chapters already downloaded — will continue from where it stopped."
        reply = QMessageBox.question(self, "Download", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes: return

        # Set up download_state and queue
        ld = legion_data()
        book_data = ld.get("books", {}).get(name, {})
        if not book_data:
            QMessageBox.warning(self, "Error", f"Book '{name}' not found in library."); return

        # Preserve already-downloaded count if resuming
        existing_state = book_data.get("download_state", {})
        book_data["download_state"] = {
            "status":                    "queued",
            "last_downloaded_chapter":   existing_state.get("last_downloaded_chapter"),
            "last_downloaded_chapter_num": existing_state.get("last_downloaded_chapter_num", 0),
            "total_chapters_downloaded": existing_state.get("total_chapters_downloaded", 0),
            "download_path":             existing_state.get("download_path"),
            "failed_chapters":           [],
            "timestamp":                 time.time(),
            "pause_requested":           False,
        }
        ld["books"][name] = book_data
        save_json(LEGION_PROGRESS, ld)
        log.legion.info("Download queued", book=name, url=url, already_downloaded=already)

        try:
            mod.download_manager.queue_download(name, book_data, ld)
        except Exception as e:
            log.legion.exc("Failed to queue download", e, book=name)
            QMessageBox.critical(self, "Queue Failed",
                f"Failed to start download:\n{str(e)}\n\n"
                "Check that legion.py loaded correctly and try again.")
            return

        self._detail_book = book_data
        self._show_detail(name, book_data, self._detail_from_list)

        # Start live progress polling (every 3s while active)
        self._start_download_poll(name)

    def _start_download_poll(self, name):
        """Poll legion_data() every 3 seconds while a download is active, updating the detail view."""
        if hasattr(self, "_dl_poll_timer") and self._dl_poll_timer.isActive():
            self._dl_poll_timer.stop()
        self._dl_poll_name = name
        self._dl_poll_timer = QTimer(self)
        self._dl_poll_timer.timeout.connect(self._poll_download_progress)
        self._dl_poll_timer.start(3000)

    def _poll_download_progress(self):
        name = getattr(self, "_dl_poll_name", "")
        if not name:
            self._dl_poll_timer.stop(); return
        ld = legion_data()
        book_data = ld.get("books", {}).get(name)
        if not book_data:
            self._dl_poll_timer.stop()
            self._detail_dl_status.setText("")  # clear stale label
            return
        status = book_data.get("download_state", {}).get("status", "idle")
        # Update detail labels live — but never kick user out of reader
        if getattr(self, "_detail_book_name", "") == name:
            self._detail_book = book_data
            # Only do a full _show_detail refresh if NOT currently reading
            if self._right_stack.currentIndex() == 3:
                # User is reading — just silently update the book data and buttons
                dl_state  = book_data.get("download_state", {})
                dl_status = dl_state.get("status", "idle")
                dl_count  = dl_state.get("total_chapters_downloaded", 0)
                _last_err = dl_state.get('last_error', '')
                _fail_msg = f"❌ {_last_err}" if _last_err else f"❌ Failed ({len(dl_state.get('failed_chapters', []))} chapters failed)"
                dl_labels = {
                    "downloading": f"Downloading... ({dl_count} chapters)",
                    "completed":   f"✓ Downloaded ({dl_count} chapters)",
                    "paused":      f"⏸ Paused ({dl_count} chapters)",
                    "failed":      _fail_msg,
                    "queued":      f"⏳ Queued ({dl_count} chapters so far)",
                    "cancelled":   "✕ Download cancelled",
                }
                self._detail_dl_status.setText(dl_labels.get(dl_status, ""))
                self._update_detail_buttons(book_data)
            else:
                self._show_detail(name, book_data, getattr(self, "_detail_from_list", "jumpin"))
        # Stop polling once done
        if status not in ("queued", "downloading"):
            self._dl_poll_timer.stop()
            # If no book currently selected, clear the label
            if not getattr(self, "_detail_book_name", ""):
                self._detail_dl_status.setText("")

    def _detail_pause(self):
        name = getattr(self,"_detail_book_name",None)
        if not name: return
        mod, _ = _get_legion_mod()
        if mod and hasattr(mod,"download_manager"):
            try: mod.download_manager.pause_download(name)
            except Exception: pass  # Ignored
        ld = legion_data(); book_data = ld.get("books", {}).get(name,{})
        book_data.setdefault("download_state",{})["pause_requested"] = True
        book_data["download_state"]["status"] = "paused"
        save_json(LEGION_PROGRESS, ld)
        self._detail_book = book_data
        self._show_detail(name, book_data, self._detail_from_list)

    def _detail_resume(self):
        name = getattr(self,"_detail_book_name",None)
        if not name: return
        mod, _ = _get_legion_mod()
        ld = legion_data(); book_data = ld.get("books", {}).get(name,{})
        if not book_data: return
        book_data.setdefault("download_state",{})["status"] = "queued"
        book_data["download_state"]["pause_requested"] = False
        save_json(LEGION_PROGRESS, ld)
        if mod and hasattr(mod,"download_manager"):
            try: mod.download_manager.queue_download(name, book_data, ld)
            except Exception: pass  # Ignored
        self._detail_book = book_data
        self._show_detail(name, book_data, self._detail_from_list)
        self._start_download_poll(name)

    def _detail_cancel(self):
        name = getattr(self,"_detail_book_name",None)
        if not name: return
        ld = legion_data(); book_data = ld.get("books", {}).get(name,{})
        book_data.setdefault("download_state",{})["status"] = "cancelled"
        book_data["download_state"]["pause_requested"] = True
        save_json(LEGION_PROGRESS, ld)
        self._detail_book = book_data
        self._show_detail(name, book_data, self._detail_from_list)


    def _detail_check_new(self):
        """Check for new chapters online and offer to download them."""
        name = getattr(self, "_detail_book_name", None)
        book = getattr(self, "_detail_book", {})
        if not name: return
        self._btn_new_chs.setEnabled(False)
        self._btn_new_chs.setText("⏳ Checking...")
        w = _NewChaptersWorker(book) # Renamed to 'w' for consistency with prompt
        log.debug(f"Adding worker: {type(w).__name__}", worker_id=id(w))

        # Connect finished signal to cleanup method
        w.finished.connect(lambda: self._cleanup_worker(w))
        
        w.done.connect(
            lambda count, err: self._on_new_chapters_checked(name, book, count, err))
        w.start()

        self._meta_workers.append(w)

    def _on_new_chapters_checked(self, name, book, count, err):
        self._btn_new_chs.setEnabled(True)
        self._btn_new_chs.setText("⬇  New Chapters")
        if err:
            QMessageBox.warning(self, "Check Failed", f"Could not check for new chapters:\n{err}")
            return
        if count == 0:
            QMessageBox.information(self, "Up to Date",
                f"No new chapters found for '{name}'.\nYou have the latest version.")
            return
        reply = QMessageBox.question(self, "New Chapters Found",
            f"Found {count} new chapter{'s' if count != 1 else ''} for '{name}'.\n\nDownload them now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._queue_incremental_download(name, book)

    def _queue_incremental_download(self, name, book):
        """Queue an incremental download — appends from last downloaded chapter."""
        mod, err = _get_legion_mod()
        if not mod:
            QMessageBox.warning(self, "Error", f"Can't load legion.py:\n{err}"); return
        if not hasattr(mod, "download_manager"):
            QMessageBox.warning(self, "Error", "download_manager not found in legion.py"); return
        ld = legion_data(); book_data = ld.get("books", {}).get(name, {})
        if not book_data: return
        existing = book_data.get("download_state", {})
        # Keep last_downloaded_chapter so it resumes from where it left off
        book_data["download_state"] = {
            "status":                      "queued",
            "last_downloaded_chapter":     existing.get("last_downloaded_chapter"),
            "last_downloaded_chapter_num": existing.get("last_downloaded_chapter_num", 0),
            "total_chapters_downloaded":   existing.get("total_chapters_downloaded", 0),
            "download_path":               existing.get("download_path"),
            "failed_chapters":             [],
            "timestamp":                   time.time(),
            "pause_requested":             False,
        }
        ld["books"][name] = book_data
        save_json(LEGION_PROGRESS, ld)
        try:
            mod.download_manager.queue_download(name, book_data, ld)
        except Exception as e:
            QMessageBox.critical(self, "Queue Failed", str(e)); return
        self._detail_book = book_data
        self._show_detail(name, book_data, self._detail_from_list)
        self._start_download_poll(name)

    def _detail_refresh_meta(self):
        name = getattr(self,"_detail_book_name",None)
        book = getattr(self,"_detail_book",{})
        if not name: return
        url = book.get("current_url","") or book.get("url","")
        if not url:
            self._detail_meta.setText("No URL saved — can't fetch metadata."); return
        self._detail_meta.setText("Fetching metadata...  ⏳")
        w = _MetaRefreshWorker(url) # Renamed to 'w' for consistency with prompt
        log.debug(f"Adding worker: {type(w).__name__}", worker_id=id(w))
        
        # Connect finished signal to cleanup method
        w.finished.connect(lambda: self._cleanup_worker(w))
        
        # Connect done signal (existing)
        w.done.connect(
            lambda meta, err: self._on_meta_refreshed(name, meta, err))
        w.start()

        self._meta_workers.append(w)

    def _on_meta_refreshed(self, name, meta, err):
        if err:
            self._detail_meta.setText(f"⚠ {err}"); return
        ld = legion_data(); book_data = ld.get("books", {}).get(name,{})
        book_data["metadata"] = meta
        save_json(LEGION_PROGRESS, ld)
        self._detail_book["metadata"] = meta
        self._show_detail(name, self._detail_book, self._detail_from_list)

    def _detail_delete(self):
        name = getattr(self, "_detail_book_name", None)
        if not name: return
        reply = QMessageBox.question(self, "Remove Book",
            f"Remove '{name}' from your library?\nThis will also delete the downloaded .txt file.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes: return

        # 1. Work out where the .txt file is — it always lives next to legion.py
        txt_path = None
        mod, _ = _get_legion_mod()
        if mod and hasattr(mod, "get_book_path"):
            try:
                candidate = mod.get_book_path(name)
                if os.path.exists(candidate):
                    txt_path = candidate
                # Also check the parent library dir for cleanup
                import pathlib
                lib_dir = pathlib.Path(candidate).parent
            except Exception:
                pass

        # 2. Stop any active download for this book
        if mod and hasattr(mod, "download_manager"):
            try:
                mod.download_manager.cancel_book(name)
            except Exception:
                pass

        # 3. Remove from JSON and save
        ld = legion_data()
        ld.get("books", {}).pop(name, None)
        save_json(LEGION_PROGRESS, ld)

        # 4. Delete the .txt file
        if txt_path:
            try:
                os.remove(txt_path)
            except Exception as e:
                QMessageBox.warning(self, "Could not delete file", str(e))

        # 5. Update UI
        self._detail_book_name = None
        self._detail_title.setText("Book removed.")
        self._detail_synopsis.clear()
        self._update_detail_buttons(None)
        self.refresh()

    def _detail_reset_time(self):
        """Reset the reading time counter for the current book."""
        name = getattr(self, "_detail_book_name", None)
        if not name: return
        try:
            ld = legion_data()
            books = ld.get("books", {})
            if name not in books: return
            books[name]["minutes_read"] = 0
            save_json(LEGION_PROGRESS, ld)
            self._book_data[name] = books[name]
            self._show_detail(name, books[name], getattr(self, "_detail_from_list", "jumpin"))
        except Exception:
            pass


    def _bm_context(self, pos, lw, list_name):
        item = lw.itemAt(pos)
        if not item: return
        e = item.data(Qt.ItemDataRole.UserRole)
        title = e.get("title","") if isinstance(e,dict) else str(e)
        url   = e.get("url","") if isinstance(e,dict) else ""
        menu  = QMenu(self)
        for target in ("planning","reading","dropped","completed"):
            if target != list_name:
                act = menu.addAction(f"Move to {target.capitalize()}")
                act.triggered.connect(lambda _, t=target, ti=title, u=url: self._bm_move(ti,u,t))
        menu.addSeparator()
        menu.addAction("Remove").triggered.connect(lambda: self._bm_remove(title))
        menu.exec(lw.mapToGlobal(pos))

    def _bm_move(self, title, url, target):
        mod, err = _get_legion_mod()
        if mod:
            try: mod.add_to_bookmarks(title, url, target)
            except Exception: self._bm_move_raw(title, url, target)
        else: self._bm_move_raw(title, url, target)
        self.refresh()

    def _bm_move_raw(self, title, url, target):
        bm = bookmarks_data()
        for k in bm:
            bm[k] = [e for e in bm[k]
                if (e.get("title","") if isinstance(e,dict) else str(e)).lower() != title.lower()]
        bm.setdefault(target,[]).append({"title":title,"url":url,"metadata":{},"added":time.time()})
        save_json(LEGION_BOOKMARKS, bm)

    def _bm_remove(self, title):
        mod, err = _get_legion_mod()
        if mod:
            try: mod.remove_from_bookmarks(title); self.refresh(); return
            except Exception: pass  # Ignored
        bm = bookmarks_data()
        for k in bm:
            bm[k] = [e for e in bm[k]
                if (e.get("title","") if isinstance(e,dict) else str(e)).lower() != title.lower()]
        save_json(LEGION_BOOKMARKS, bm); self.refresh()

    def _load_url(self, url):
        if not url: return
        self._current_url = url
        self.reader.setPlainText("Loading chapter...")
        self.chapter_title.setText("Loading...")
        self._read_progress.setValue(0)
        self.progress_bar.setVisible(True)
        if self._worker and self._worker.isRunning(): self._worker.terminate()
        self._worker = FetchChapterWorker(url, self._current_book)
        self._worker.status.connect(lambda s: self.reader_status.setText(s))
        self._worker.done.connect(self._chapter_done)
        self._worker.error.connect(self._chapter_error)
        self._worker.start()

    def _chapter_done(self, title, paragraphs, next_url, prev_url, url_ch_num=0):
        self.progress_bar.setVisible(False)
        self._next_url = next_url
        self._prev_url = prev_url
        self._reading_local = False   # came from web scrape
        self.chapter_title.setText(title)
        text = "\n\n".join(paragraphs)
        self.reader.setPlainText(text)
        self.reader.moveCursor(QTextCursor.MoveOperation.Start)
        words = len(text.split())
        nav = ("< prev  " if prev_url else "") + ("next >" if next_url else "end of site")
        self.reader_status.setText(f"{words:,} words  ·  {nav}  [web]")
        self._prev_btn.setEnabled(bool(prev_url))
        self._next_btn.setEnabled(bool(next_url))
        if self._current_book:
            ld = legion_data(); book_data = ld.get("books", {}).get(self._current_book)
            if book_data:
                book_data["current_url"]     = self._current_url
                # Extract real chapter number from title e.g. "Chapter 551: ..."
                m_ch = re.search(r'[Cc]hapter\s+(\d+)', title)
                if m_ch:
                    book_data["current_chapter"] = int(m_ch.group(1))
                    self._current_ch_num = int(m_ch.group(1))
                elif url_ch_num:
                    book_data["current_chapter"] = url_ch_num
                    self._current_ch_num = url_ch_num
                book_data["last_title"]      = title
                if next_url: book_data["next_url"] = next_url
                book_data["words_read"]      = book_data.get("words_read", 0) + words
                save_json(LEGION_PROGRESS, ld)
                if self._current_book in self._book_data:
                    self._book_data[self._current_book] = book_data
            # Update catalogue panel with resolved chapter number
            if self._catalogue_panel:
                self._catalogue_panel.set_context(self._current_book, self._current_ch_num)

    def _chapter_error(self, msg):
        log.legion.error("Chapter load error", book=getattr(self,"_current_book","?"),
                         chapter=getattr(self,"_current_ch_num",0), error=msg)
        self.progress_bar.setVisible(False)
        if msg.startswith("Cannot load legion.py:"):
            display_msg = (
                "Legion module failed to load.\n\n"
                "Please restart Great Sage. If the problem persists,\n"
                "run: pip install beautifulsoup4 --break-system-packages")
        else:
            display_msg = (
                f"Error loading chapter:\n\n{msg}\n\n"
                "Tips:\n- Check the URL is a valid chapter page\n"
                "- Some sites block scrapers - try opening in browser first\n"
                "- Try a mirror: novelbin.me or novelfull.com")
        self.reader.setPlainText(display_msg)
        self.reader_status.setText("Failed.")

    def _heartbeat_save(self):
        """Called every 2 minutes while reading — saves scroll position and time."""
        name = getattr(self, "_current_book", None)
        if not name or self._right_stack.currentIndex() != 3: return
        # Save scroll position
        sb = self.reader.verticalScrollBar()
        if sb.maximum() > 0:
            frac = (sb.value() - sb.minimum()) / (sb.maximum() - sb.minimum())
            self._scroll_positions.setdefault(name, {})[self._current_ch_num] = frac
            
            # When a chapter is completed (scroll position >= 95%), track words:
            if frac >= 0.95 and not getattr(self, "_words_tracked_this_chapter", False):
                track_event("words_read", {"words": getattr(self, "_chapter_open_words", 0), "book": name})
                self._words_tracked_this_chapter = True
        # Save elapsed time
        self._save_reading_time()
        # Restart timer for next interval
        self._chapter_open_time = time.time()

    def _save_reading_time(self):
        """Save minutes spent on the current chapter before navigating away."""
        name = getattr(self, "_current_book", None)
        open_time = getattr(self, "_chapter_open_time", None)
        if not name or not open_time: return
        elapsed_mins = (time.time() - open_time) / 60.0
        # Must have spent at least 30 seconds on the chapter (filters accidental opens)
        if elapsed_mins < 0.5: return
        # Cap at 15 min per chapter — a long webnovel chapter takes ~8-12 min to read.
        # This prevents idle time (left app open, download running) inflating the count.
        elapsed_mins = min(elapsed_mins, 15.0)
        try:
            ld = legion_data(); book_data = ld.get("books", {}).get(name)
            if book_data:
                book_data["minutes_read"] = round(book_data.get("minutes_read", 0) + elapsed_mins, 1)
                save_json(LEGION_PROGRESS, ld)
                self._book_data[name] = book_data
        except Exception: pass  # Ignored
        self._chapter_open_time = None

    def _next_chapter(self):
        if self._reading_local and self._current_ch_num > 0:
            self._save_reading_time()
            # Accumulate words_read on chapter completion (not on open)
            words_just_read = len(self.reader.toPlainText().split())
            ld = legion_data(); book_data = ld.get("books", {}).get(self._current_book)
            if book_data:
                book_data["chapters_read"] = book_data.get("chapters_read", 0) + 1
                book_data["words_read"]    = book_data.get("words_read", 0) + words_just_read
                save_json(LEGION_PROGRESS, ld)
                self._book_data[self._current_book] = book_data
            genre = _detect_genre(self._current_book, book_data) if book_data else ""
            track_event("chapter_finished", {
                "book": self._current_book, 
                "ch": self._current_ch_num,
                "genre": genre
            })

            # Look up actual next story chapter number from the file
            next_num = None
            mod, _ = _get_legion_mod()
            if mod and hasattr(mod, "_get_chapter_list_from_file"):
                try:
                    ch_list = mod._get_chapter_list_from_file(self._current_book)
                    nums = [n for n, _ in ch_list]
                    idx = nums.index(self._current_ch_num) if self._current_ch_num in nums else -1
                    if idx >= 0 and idx + 1 < len(nums):
                        next_num = nums[idx + 1]
                except Exception:
                    pass
            if next_num is None:
                next_num = self._current_ch_num + 1  # fallback

            if self._current_book:
                self._scroll_positions.setdefault(self._current_book, {}).pop(next_num, None)
            book = self._book_data.get(self._current_book, {})
            url  = book.get("next_url", "") or book.get("current_url", "") or book.get("url", "")
            self._load_chapter(next_num, url)
        elif self._next_url:
            self._load_url(self._next_url)
        else:
            self.reader_status.setText("No next chapter — end of downloads and no web URL saved.")

    def _show_chapter_list(self):
        """Show a scrollable chapter list dialog so the user can jump to any chapter."""
        name = getattr(self, "_current_book", None)
        if not name: return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Chapters — {name}")
        dlg.setMinimumSize(380, 520)
        dlg.setStyleSheet(f"background:{BG};color:{TEXT};")
        vlay = QVBoxLayout(dlg)
        vlay.setContentsMargins(16, 16, 16, 16); vlay.setSpacing(10)

        # Search filter
        search = QLineEdit()
        search.setPlaceholderText("Search chapters...")
        search.setStyleSheet(
            f"background:{BG2};color:{TEXT};border:1px solid {BORDER};"
            f"border-radius:6px;padding:6px 10px;font-size:13px;")
        vlay.addWidget(search)

        lw = QListWidget()
        lw.setStyleSheet(
            f"QListWidget{{background:{BG2};border:1px solid {BORDER};border-radius:8px;"
            f"padding:4px;outline:none;}}"
            f"QListWidget::item{{padding:8px 12px;border-radius:5px;color:{TEXT};}}"
            f"QListWidget::item:hover{{background:{BG3};}}"
            f"QListWidget::item:selected{{background:{ACCENT};color:{BG};}}")
        vlay.addWidget(lw, 1)

        current_ch = getattr(self, "_current_ch_num", 0)
        total      = getattr(self, "_total_ch_local", 0)

        # Build chapter list — local chapters first, then web-based count
        chapters = []  # list of (story_ch_num, label)
        mod, _ = _get_legion_mod()
        if mod and hasattr(mod, "_get_chapter_list_from_file"):
            try:
                raw = mod._get_chapter_list_from_file(name)
                # Returns [(story_num, subtitle), ...] e.g. [(549, "Holy Grail vs Bloodline"), ...]
                for num, subtitle in raw:
                    label = f"Chapter {num}" + (f": {subtitle}" if subtitle and subtitle != f"Chapter {num}" else "")
                    chapters.append((num, label))
            except Exception: pass  # Ignored

        if not chapters and total > 0:
            chapters = [(i+1, f"Chapter {i+1}") for i in range(total)]
        if not chapters:
            top = max(current_ch + 20, 50)
            chapters = [(i+1, f"Chapter {i+1}") for i in range(top)]

        def _populate(filter_text=""):
            lw.clear()
            ft = filter_text.lower()
            for num, label in chapters:
                if ft and ft not in label.lower() and ft not in str(num): continue
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, num)
                if num == current_ch:
                    item.setForeground(QColor(ACCENT))
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                lw.addItem(item)
            for i in range(lw.count()):
                if lw.item(i).data(Qt.ItemDataRole.UserRole) == current_ch:
                    lw.scrollToItem(lw.item(i), QAbstractItemView.ScrollHint.PositionAtCenter)
                    lw.setCurrentRow(i); break

        _populate()
        search.textChanged.connect(_populate)

        def _jump():
            sel = lw.currentItem()
            if not sel: return
            ch_num = sel.data(Qt.ItemDataRole.UserRole)
            dlg.accept()
            self._load_chapter(ch_num)

        lw.itemDoubleClicked.connect(lambda _: _jump())

        btns = QHBoxLayout()
        btns.setSpacing(8)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            f"QPushButton{{background:{BG3};color:{TEXT};border:1px solid {BORDER};"
            f"border-radius:6px;padding:6px 18px;font-size:13px;}}"
            f"QPushButton:hover{{background:{BORDER};}}")
        go_btn = QPushButton("Jump to Chapter")
        go_btn.setStyleSheet(
            f"QPushButton{{background:{ACCENT};color:{BG};border:none;"
            f"border-radius:6px;padding:6px 18px;font-size:13px;font-weight:bold;}}"
            f"QPushButton:hover{{opacity:0.85;}}")
        cancel_btn.clicked.connect(dlg.reject)
        go_btn.clicked.connect(_jump)
        btns.addWidget(cancel_btn); btns.addWidget(go_btn)
        vlay.addLayout(btns)
        dlg.exec()

    def _prev_chapter(self):
        self._save_reading_time()
        if self._reading_local and self._current_ch_num > 0:
            # Look up actual previous chapter number from the file (handles gaps)
            prev_num = None
            mod, _ = _get_legion_mod()
            if mod and hasattr(mod, "_get_chapter_list_from_file"):
                try:
                    ch_list = mod._get_chapter_list_from_file(self._current_book)
                    nums = [n for n, _ in ch_list]
                    idx = nums.index(self._current_ch_num) if self._current_ch_num in nums else -1
                    if idx > 0:
                        prev_num = nums[idx - 1]
                except Exception:
                    pass
            if prev_num is None and self._current_ch_num > 1:
                prev_num = self._current_ch_num - 1  # fallback
            if prev_num:
                if self._current_book:
                    self._scroll_positions.setdefault(self._current_book, {}).pop(prev_num, None)
                self._load_chapter(prev_num)
            else:
                self.reader_status.setText("Already at the first chapter.")
        elif self._prev_url:
            self._load_url(self._prev_url)
        else:
            self.reader_status.setText("Already at the first chapter.")

    def _font_delta(self, d):
        self._rs["font_size"] = max(12, min(32, self._rs.get("font_size", 18) + d))
        self._font_size = self._rs["font_size"]
        if hasattr(self, "_rs_fs_slider"):
            self._rs_fs_slider.setValue(self._font_size)
        self._apply_font()
        self._save_rs()


    def _add_book(self):
        dlg = AddBookDialog(self)
        if dlg.exec():
            title, url = dlg.result_data
            if title and url:
                ld = legion_data()
                ld.setdefault("books", {})[title] = {
                    "current_url": url, "next_url": None, "last_title": "Not started",
                    "chapters_read": 0, "words_read": 0, "minutes_read": 0,
                    "new_chapters_waiting": 0, "metadata": {},
                    "download_state": {"status": "idle", "last_downloaded_chapter": None,
                        "last_downloaded_chapter_num": 0, "total_chapters_downloaded": 0,
                        "download_path": None, "failed_chapters": [], "timestamp": None,
                        "pause_requested": False}}
                save_json(LEGION_PROGRESS, ld)
                self.refresh()
                # Trigger auto-sync for the new book
                main_win = self.window()
                if hasattr(main_win, "_run_auto_sync"):
                    QTimer.singleShot(500, main_win._run_auto_sync)


# ═══════════════════════════════════════════════════════════════════════════════
# BOOK DETAIL DIALOG  (opened when clicking any book in Jump In or Bookmarks)
# ═══════════════════════════════════════════════════════════════════════════════

class BookDetailDialog(QDialog):
    """Full book detail popup window — title, metadata, synopsis, action buttons."""
    read_requested       = pyqtSignal()
    delete_requested     = pyqtSignal(str)
    reset_time_requested = pyqtSignal(str)
    download_requested   = pyqtSignal(str)
    bookmark_requested   = pyqtSignal(str)   # book name

    def __init__(self, name: str, book: dict, from_list: str = "jumpin", parent=None):
        super().__init__(parent)
        self._name      = name
        self._book      = book
        self._from_list = from_list
        self.setWindowTitle(name)
        self.setModal(True)
        self.setMinimumSize(700, 480)
        self.setStyleSheet(f"background:{BG}; color:{TEXT};")
        if parent:
            pg = parent.window().geometry()
            w, h = 720, 520
            self.setGeometry(pg.x() + (pg.width()-w)//2, pg.y() + (pg.height()-h)//2, w, h)
        self._build()

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left: cover art ────────────────────────────────────────────────────
        cover_panel = QWidget()
        cover_panel.setFixedWidth(200)
        cover_panel.setStyleSheet(f"background:{BG2};")
        cv = QVBoxLayout(cover_panel)
        cv.setContentsMargins(16, 20, 16, 20)
        cv.setSpacing(0)

        self._cover_lbl = QLabel()
        self._cover_lbl.setFixedSize(168, 236)
        self._cover_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover_lbl.setStyleSheet(
            f"background:{BG3};border-radius:6px;color:{MUTED};font-size:11px;")
        self._cover_lbl.setText("Loading\ncover…")
        cv.addWidget(self._cover_lbl)
        cv.addStretch()

        # Download status badge (below cover)
        dl_state  = self._book.get("download_state", {})
        dl_status = dl_state.get("status", "idle")
        dl_count  = dl_state.get("total_chapters_downloaded", 0)
        if dl_status not in ("idle", "cancelled"):
            badge_map = {
                "downloading": (f"⏳ Downloading ({dl_count} ch)", ACCENT2),
                "completed":   (f"✅ Downloaded ({dl_count} ch)",  NEON),
                "paused":      (f"⏸ Paused ({dl_count} ch)",      ACCENT),
                "failed":      (f"❌ Failed",                       RED),
                "queued":      (f"⏳ Queued",                       PURPLE),
            }
            badge_text, badge_col = badge_map.get(dl_status, ("", MUTED))
            if badge_text:
                dl_badge = QLabel(badge_text)
                dl_badge.setWordWrap(True)
                dl_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                dl_badge.setStyleSheet(
                    f"color:{badge_col};font-size:9px;letter-spacing:0.5px;"
                    f"padding:6px 4px;")
                cv.addWidget(dl_badge)

        root.addWidget(cover_panel)
        root.addWidget(vline())

        # ── Right: info + actions ──────────────────────────────────────────────
        info_panel = QWidget()
        iv = QVBoxLayout(info_panel)
        iv.setContentsMargins(24, 20, 24, 16)
        iv.setSpacing(8)

        # Title
        title_lbl = QLabel(self._name)
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet(
            f"font-size:20px;font-weight:bold;color:{ACCENT};"
            f"font-family:{FONT_BODY};")
        iv.addWidget(title_lbl)

        # Meta line: author · genres · status
        meta = self._book.get("metadata", {})
        meta_parts = []
        if meta.get("author"):  meta_parts.append(meta["author"])
        if meta.get("status"):  meta_parts.append(meta["status"])
        meta_lbl = QLabel("  ·  ".join(meta_parts) if meta_parts else "No metadata yet")
        meta_lbl.setStyleSheet(f"color:{TEXT2};font-size:13px;")
        meta_lbl.setWordWrap(True)
        iv.addWidget(meta_lbl)

        # Genre pills
        if meta.get("genres"):
            genre_row = QHBoxLayout()
            genre_row.setSpacing(6)
            genre_row.setContentsMargins(0,0,0,0)
            for g in str(meta["genres"]).split(",")[:6]:
                g = g.strip()
                if not g: continue
                pill = QLabel(g)
                pill.setStyleSheet(
                    f"background:{BG3};color:{ACCENT2};border:1px solid {BORDER};"
                    f"border-radius:10px;font-size:9px;padding:3px 10px;"
                    f"letter-spacing:0.5px;")
                genre_row.addWidget(pill)
            genre_row.addStretch()
            iv.addLayout(genre_row)

        iv.addWidget(hline())

        # Progress stats
        ch_read = self._book.get("chapters_read", 0)
        words   = self._book.get("words_read", 0)
        mins    = int(round(self._book.get("minutes_read", 0)))
        cur_ch  = self._book.get("current_chapter")
        last_t  = self._book.get("last_title", "")
        dl_total= self._book.get("total_chapters")

        stat_parts = []
        if ch_read:  stat_parts.append(f"📖  {ch_read} chapters read")
        if words:    stat_parts.append(f"📝  {words:,} words")
        if mins >= 60: stat_parts.append(f"⏱  {mins//60}h {mins%60}m")
        elif mins:     stat_parts.append(f"⏱  {mins}m")
        if cur_ch:   stat_parts.append(f"📌  Ch.{cur_ch}")
        if dl_total: stat_parts.append(f"📚  {dl_total} chapters total")

        for s in stat_parts:
            sl = QLabel(s)
            sl.setStyleSheet(f"color:{TEXT2};font-size:11px;letter-spacing:0.3px;")
            iv.addWidget(sl)

        if last_t and last_t != "Not started":
            ll = QLabel(f"Last: {last_t[:80]}")
            ll.setStyleSheet(f"color:{MUTED};font-size:10px;")
            ll.setWordWrap(True)
            iv.addWidget(ll)

        iv.addWidget(hline())

        # Synopsis
        syn = (meta.get("synopsis","") or meta.get("description","")
               or meta.get("summary","") or meta.get("overview",""))
        if not syn and not ch_read:
            syn = "No synopsis available — click ↻ Refresh Info to fetch it."
        syn_box = QTextEdit()
        syn_box.setReadOnly(True)
        syn_box.setPlainText(syn or "No synopsis available.")
        syn_box.setStyleSheet(
            f"background:{BG3};border:none;padding:12px;color:{TEXT};"
            f"font-family:{FONT_BODY};font-size:13px;")
        iv.addWidget(syn_box, 1)

        iv.addWidget(hline())

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        def _mk(text, style="", cb=None):
            b = QPushButton(text)
            if style == "accent":
                b.setStyleSheet(
                    f"background:{ACCENT};color:{BG};border:none;font-weight:bold;"
                    f"font-size:9px;letter-spacing:1.2px;padding:8px 18px;border-radius:3px;")
            elif style == "danger":
                b.setStyleSheet(
                    f"background:transparent;color:{RED};border:1px solid #2A1018;"
                    f"font-size:9px;letter-spacing:1px;padding:8px 14px;border-radius:3px;")
            else:
                b.setStyleSheet(
                    f"background:transparent;color:{TEXT2};border:1px solid {BORDER};"
                    f"font-size:9px;letter-spacing:1px;padding:8px 14px;border-radius:3px;")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if cb: b.clicked.connect(cb)
            return b

        def _read():
            self.read_requested.emit()
            self.accept()

        def _delete():
            reply = QMessageBox.question(self, "Remove Book",
                f"Remove '{self._name}' from your library?\n"
                "This will also delete the downloaded .txt file.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.delete_requested.emit(self._name)
                self.accept()

        def _reset():
            self.reset_time_requested.emit(self._name)
            self.accept()

        def _preview():
            self.accept()
            dlg = BookPreviewDialog(self._name, self._book, self.parent())
            dlg.exec()

        def _bookmark():
            self.bookmark_requested.emit(self._name)
            self.accept()

        btn_row.addWidget(_mk("▶  READ",        "accent", _read))
        btn_row.addWidget(_mk("REMOVE",          "danger", _delete))
        if ch_read == 0:
            btn_row.addWidget(_mk("PREVIEW",     "",       _preview))
        else:
            btn_row.addWidget(_mk("↺  RESET TIME", "",    _reset))
        if self._from_list == "jumpin":
            btn_row.addWidget(_mk("☆  BOOKMARK", "",      _bookmark))
        btn_row.addStretch()
        btn_row.addWidget(_mk("✕  CLOSE",        "",       self.reject))
        iv.addLayout(btn_row)

        root.addWidget(info_panel, 1)

        # Load cover art in background
        QTimer.singleShot(0, self._load_cover)

    def _load_cover(self):
        try:
            from plugins.book_covers import get_cover
            src = self._book.get("current_url","") or self._book.get("url","")
            px  = get_cover(self._name, src)
            if px and not px.isNull():
                scaled = px.scaled(168, 236,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                self._cover_lbl.setPixmap(scaled)
                self._cover_lbl.setText("")
        except Exception:
            self._cover_lbl.setText("No cover")


# ═══════════════════════════════════════════════════════════════════════════════
# BOOK PREVIEW  — worker + dialog
# ═══════════════════════════════════════════════════════════════════════════════

class BookPreviewWorker(QThread):
    """
    Reads the first ~20 local chapters, samples the text, sends it to Groq,
    and emits a structured result dict (or an error string).
    """
    done  = pyqtSignal(object)   # dict on success, str on error

    # Groq prompt — asks for exactly the three sections we display
    _PROMPT = """\
You are analysing the opening of a web novel. Below are excerpts from the first chapters.
Respond with ONLY a JSON object — no markdown, no extra text — in this exact shape:

{{
  "facts": {{
    "main_character": "...",
    "setting": "...",
    "power_system": "...",
    "protagonist_type": "...",
    "tone": "...",
    "early_cast": "..."
  }},
  "vibe": "One paragraph (4-6 sentences) written like a friend describing what the novel actually feels like to read — pacing, emotional hook, world-building style.",
  "score": 4,
  "verdict": "One punchy sentence summarising the first impression."
}}

score must be an integer 1-5.
verdict must be under 15 words.

NOVEL EXCERPTS:
{excerpts}"""

    def __init__(self, book_name: str, book: dict, parent=None):
        super().__init__(parent)
        self._name = book_name
        self._book = book

    def run(self):
        try:
            excerpts = self._gather_excerpts()
            if not excerpts:
                self.done.emit("No downloaded chapters found for this book.")
                return

            result = self._call_groq(excerpts)
            self.done.emit(result)
        except Exception as e:
            self.done.emit(f"Preview failed: {e}")

    # ── helpers ──────────────────────────────────────────────────────────────

    def _gather_excerpts(self) -> str:
        """
        Returns sampled text from chapters 1-3 (full-ish) + ch 10 + ch 20.
        Falls back to reading directly from the .txt file via legion helpers.
        """
        from great_sage_core import legion_mod
        mod, _ = legion_mod()
        if not mod:
            return ""

        get_ch   = getattr(mod, "get_chapter_from_file", None)
        get_path = getattr(mod, "get_book_path", None)
        if not (get_ch and get_path):
            return ""

        parts = []
        # First 3 chapters in full (up to 4 000 chars each)
        for n in range(1, 4):
            title, paras = get_ch(self._name, n)
            if paras:
                body = "\n\n".join(paras)[:4000]
                parts.append(f"--- Chapter {n}: {title} ---\n{body}")

        # Chapter 10 and 20 as mid/late samples (up to 2 000 chars each)
        for n in (10, 20):
            title, paras = get_ch(self._name, n)
            if paras:
                body = "\n\n".join(paras)[:2000]
                parts.append(f"--- Chapter {n}: {title} ---\n{body}")

        return "\n\n".join(parts)

    def _call_groq(self, excerpts: str) -> dict:
        import json as _json
        from great_sage_core import sage_mod, matrix_data, get_session_groq_model

        mod, err = sage_mod()
        if not mod or not hasattr(mod, "groq_chat"):
            return f"Sage unavailable: {err or 'sage.py not loaded'}"

        _s = matrix_data().get("settings", {})
        if _s.get("groq_api_key") and hasattr(mod, "GROQ_API_KEY"):
            mod.GROQ_API_KEY = _s["groq_api_key"]
        active_model = get_session_groq_model() or _s.get("groq_model")
        if active_model and hasattr(mod, "GROQ_MODEL"):
            mod.GROQ_MODEL = active_model

        prompt = self._PROMPT.format(excerpts=excerpts)
        resp, error = mod.groq_chat(prompt)
        if error:
            return f"Groq error: {error}"

        # Strip accidental markdown fences
        text = resp.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"```$", "", text).strip()

        try:
            return _json.loads(text)
        except Exception:
            return f"Could not parse Groq response:\n{text[:400]}"


class BookPreviewDialog(QDialog):
    """Full-page preview dialog — extracted facts, vibe summary, first impression."""

    def __init__(self, name: str, book: dict, parent=None):
        super().__init__(parent)
        self._name = name
        self._book = book
        self.setWindowTitle(f"Preview — {name}")
        self.setModal(True)
        self.setMinimumSize(700, 520)
        self.setStyleSheet(f"background:{BG}; color:{TEXT};")
        if parent:
            pg = parent.window().geometry()
            w, h = 720, 540
            self.setGeometry(
                pg.x() + (pg.width() - w) // 2,
                pg.y() + (pg.height() - h) // 2,
                w, h,
            )
        self._build()
        self._start_worker()

    # ── layout ───────────────────────────────────────────────────────────────

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left cover panel (mirrors BookDetailDialog) ───────────────────
        cover_panel = QWidget()
        cover_panel.setFixedWidth(200)
        cover_panel.setStyleSheet(f"background:{BG2};")
        cv = QVBoxLayout(cover_panel)
        cv.setContentsMargins(16, 20, 16, 20)
        cv.setSpacing(0)

        self._cover_lbl = QLabel()
        self._cover_lbl.setFixedSize(168, 236)
        self._cover_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover_lbl.setStyleSheet(
            f"background:{BG3};border-radius:6px;color:{MUTED};font-size:11px;")
        self._cover_lbl.setText("Loading\ncover…")
        cv.addWidget(self._cover_lbl)
        cv.addStretch()
        root.addWidget(cover_panel)
        root.addWidget(vline())

        # ── Right info panel ──────────────────────────────────────────────
        info_panel = QWidget()
        iv = QVBoxLayout(info_panel)
        iv.setContentsMargins(24, 20, 24, 16)
        iv.setSpacing(8)

        # Title + meta
        title_lbl = QLabel(self._name)
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet(
            f"font-size:20px;font-weight:bold;color:{ACCENT};"
            f"font-family:{FONT_BODY};")
        iv.addWidget(title_lbl)

        meta = self._book.get("metadata", {})
        meta_parts = []
        if meta.get("author"): meta_parts.append(meta["author"])
        if meta.get("status"): meta_parts.append(meta["status"])
        meta_lbl = QLabel("  ·  ".join(meta_parts) if meta_parts else "")
        meta_lbl.setStyleSheet(f"color:{TEXT2};font-size:13px;")
        iv.addWidget(meta_lbl)
        iv.addWidget(hline())

        # ── Stacked widget: loading / results / error ─────────────────────
        self._stack = QStackedWidget()

        # -- Loading page --
        loading_w = QWidget()
        lv = QVBoxLayout(loading_w)
        lv.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lv.setSpacing(10)
        scan_lbl = QLabel("Scanning first 20 chapters…")
        scan_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scan_lbl.setStyleSheet(f"color:{TEXT2};font-size:13px;")
        hint_lbl = QLabel("Sending to Groq for analysis")
        hint_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint_lbl.setStyleSheet(f"color:{MUTED};font-size:10px;")
        lv.addWidget(scan_lbl)
        lv.addWidget(hint_lbl)
        self._stack.addWidget(loading_w)   # index 0

        # -- Results page --
        results_w = QWidget()
        rv = QVBoxLayout(results_w)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)

        # Section: Extracted facts (2-col grid via QFormLayout)
        facts_lbl = QLabel("EXTRACTED FACTS")
        facts_lbl.setStyleSheet(
            f"color:{MUTED};font-size:9px;letter-spacing:1.2px;")
        rv.addWidget(facts_lbl)

        self._facts_form = QWidget()
        self._facts_layout = QFormLayout(self._facts_form)
        self._facts_layout.setContentsMargins(0, 0, 0, 0)
        self._facts_layout.setSpacing(4)
        self._facts_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        rv.addWidget(self._facts_form)
        rv.addWidget(hline())

        # Section: Vibe
        vibe_lbl = QLabel("VIBE")
        vibe_lbl.setStyleSheet(
            f"color:{MUTED};font-size:9px;letter-spacing:1.2px;")
        rv.addWidget(vibe_lbl)

        self._vibe_box = QTextEdit()
        self._vibe_box.setReadOnly(True)
        self._vibe_box.setStyleSheet(
            f"background:{BG3};border:none;padding:10px;color:{TEXT};"
            f"font-family:{FONT_BODY};font-size:12px;")
        self._vibe_box.setFixedHeight(110)
        rv.addWidget(self._vibe_box)
        rv.addWidget(hline())

        # Section: First impression
        imp_lbl = QLabel("FIRST IMPRESSION")
        imp_lbl.setStyleSheet(
            f"color:{MUTED};font-size:9px;letter-spacing:1.2px;")
        rv.addWidget(imp_lbl)

        imp_row = QHBoxLayout()
        imp_row.setSpacing(10)
        self._score_lbl = QLabel()
        self._score_lbl.setStyleSheet(
            f"color:{ACCENT};font-size:13px;font-weight:bold;letter-spacing:2px;")
        self._verdict_lbl = QLabel()
        self._verdict_lbl.setWordWrap(True)
        self._verdict_lbl.setStyleSheet(
            f"color:{TEXT2};font-size:11px;font-style:italic;")
        imp_row.addWidget(self._score_lbl)
        imp_row.addWidget(self._verdict_lbl, 1)
        rv.addLayout(imp_row)

        self._stack.addWidget(results_w)   # index 1

        # -- Error page --
        error_w = QWidget()
        ev = QVBoxLayout(error_w)
        ev.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_lbl = QLabel()
        self._error_lbl.setWordWrap(True)
        self._error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_lbl.setStyleSheet(f"color:{RED};font-size:12px;")
        ev.addWidget(self._error_lbl)
        self._stack.addWidget(error_w)     # index 2

        iv.addWidget(self._stack, 1)
        iv.addWidget(hline())

        # ── Buttons ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        def _mk(text, style="", cb=None):
            b = QPushButton(text)
            if style == "accent":
                b.setStyleSheet(
                    f"background:{ACCENT};color:{BG};border:none;font-weight:bold;"
                    f"font-size:9px;letter-spacing:1.2px;padding:8px 18px;border-radius:3px;")
            else:
                b.setStyleSheet(
                    f"background:transparent;color:{TEXT2};border:1px solid {BORDER};"
                    f"font-size:9px;letter-spacing:1px;padding:8px 14px;border-radius:3px;")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if cb:
                b.clicked.connect(cb)
            return b

        btn_row.addWidget(_mk("← BACK", "", self.reject))
        btn_row.addStretch()
        iv.addLayout(btn_row)

        root.addWidget(info_panel, 1)

        # Load cover art
        QTimer.singleShot(0, self._load_cover)

    # ── worker ────────────────────────────────────────────────────────────────

    def _start_worker(self):
        self._stack.setCurrentIndex(0)
        self._worker = BookPreviewWorker(self._name, self._book, self)
        self._worker.done.connect(self._on_result)
        self._worker.start()

    def _on_result(self, result):
        if isinstance(result, str):
            # error
            self._error_lbl.setText(result)
            self._stack.setCurrentIndex(2)
            return

        # Populate facts
        label_map = {
            "main_character": "Main character",
            "setting":        "Setting",
            "power_system":   "Power system",
            "protagonist_type": "Protagonist type",
            "tone":           "Tone",
            "early_cast":     "Early cast",
        }
        facts = result.get("facts", {})
        for key, display in label_map.items():
            val = facts.get(key, "—")
            if not val:
                val = "—"
            key_lbl = QLabel(display.upper())
            key_lbl.setStyleSheet(
                f"color:{MUTED};font-size:9px;letter-spacing:0.5px;")
            val_lbl = QLabel(str(val))
            val_lbl.setWordWrap(True)
            val_lbl.setStyleSheet(f"color:{ACCENT2};font-size:11px;")
            self._facts_layout.addRow(key_lbl, val_lbl)

        # Vibe
        self._vibe_box.setPlainText(result.get("vibe", ""))

        # Score + verdict
        score   = int(result.get("score", 0))
        verdict = result.get("verdict", "")
        score_text = f"{'[ ' + '#' * score + ' ' * (5 - score) + ' ]'}"
        self._score_lbl.setText(f"{score}/5  {score_text}")
        self._verdict_lbl.setText(f'"{verdict}"')

        self._stack.setCurrentIndex(1)

    def _load_cover(self):
        try:
            from plugins.book_covers import get_cover
            src = self._book.get("current_url", "") or self._book.get("url", "")
            px  = get_cover(self._name, src)
            if px and not px.isNull():
                scaled = px.scaled(
                    168, 236,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._cover_lbl.setPixmap(scaled)
                self._cover_lbl.setText("")
        except Exception:
            pass


class AddBookDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent); self.result_data = ("","")
        self.setWindowTitle("Add Book"); self.setMinimumWidth(440); self.setStyleSheet(QSS)
        lay = QFormLayout(self)
        lay.setSpacing(12)
        lay.setContentsMargins(18,18,18,18)
        self.t = QLineEdit(); self.t.setPlaceholderText("Auto-filled from URL")
        self.u = QLineEdit(); self.u.setPlaceholderText("https://novelbin.com/b/.../chapter-1")
        self.u.textChanged.connect(self._url_changed)
        lay.addRow("Book Title:", self.t); lay.addRow("First Chapter URL:", self.u)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject); lay.addRow(bb)

    def _url_changed(self, url):
        """Auto-populate title from URL slug if title field is empty or was auto-filled."""
        import re as _re
        url = url.strip()
        if not url:
            return
        # Extract slug: novelbin.com/b/sage-of-humanity/chapter-1 → sage-of-humanity
        m = _re.search(r'/b/([^/]+)', url)
        if not m:
            # Try generic: anything between domain and /chapter
            m = _re.search(r'\.(?:com|me|net|org)/([^/]+)', url)
        if m:
            slug = m.group(1)
            # Convert slug to title: "sage-of-humanity" → "Sage Of Humanity"
            title = ' '.join(w.capitalize() for w in slug.replace('-', ' ').replace('_', ' ').split())
            self.t.setText(title)

    def _ok(self):
        self.result_data = (self.t.text().strip(), self.u.text().strip())
        self.accept()

