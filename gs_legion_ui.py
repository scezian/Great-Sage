"""
gs_legion_ui.py — Great Sage
=============================
Legion module UI: novel reader page and all related dialogs.
"""
import os, re, subprocess, sys, threading, time
from pathlib import Path

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
    QPixmap, QPainter, QLinearGradient, QRadialGradient, QBrush, QPen, QPainterPath
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
    legion_mod, matrix_mod, sage_mod,
    _catalogue_panel_class, _clean_media_title, _strip_markdown, _detect_genre,
    _grep_book_for_term,
    sage_memory_load, sage_memory_append, sage_memory_extract,
    behaviour_data, behaviour_summary, track_event, stream_watch_context,
    FetchChapterWorker, SageWorker, MetadataWorker, AutoSyncWorker,
    _SageCompanionWorker, _NewChaptersWorker, _MetaRefreshWorker, _DiscoveryWorker,
    start_mobile_server,
)

from gs_widgets import ReadingRoomOverlay

class LegionPage(QWidget):
    def __init__(self):
        super().__init__()
        self._worker = None; self._font_size = 18
        self._current_url = ""; self._next_url = ""; self._prev_url = ""
        self._current_book = ""; self._book_data = {}
        # Local-file navigation state
        self._current_ch_num = 0    # chapter number currently on screen
        self._total_ch_local = 0    # how many chapters exist in the .txt file
        self._reading_local  = False  # True = came from local file
        self._chapter_loading = False # True while a new chapter is being set up
        # Scroll position memory: {book_name: {ch_num: fraction 0.0-1.0}}
        self._scroll_positions = {}
        self._build()
        # Eye-break reminder — fires every 15 minutes while reading
        self._eye_toast = EyeBreakToast(self)
        self._eye_timer = QTimer(self)
        self._eye_timer.setInterval(15 * 60 * 1000)   # 15 minutes
        self._eye_timer.timeout.connect(self._eye_toast.show_toast)

    def _build(self):
        root = QHBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # Sidebar
        sidebar = QFrame(); sidebar.setObjectName("sidebar"); sidebar.setFixedWidth(360)
        sv = QVBoxLayout(sidebar); sv.setContentsMargins(0,20,0,12); sv.setSpacing(0)
        hdr_w = QWidget()
        hdr_w.setStyleSheet(f"background:{BG2}; border-bottom:1px solid {BORDER};")
        hdr_w.setFixedHeight(52)
        hw = QHBoxLayout(hdr_w); hw.setContentsMargins(16,0,12,0)
        back_b = QPushButton("← HOME")
        back_b.setStyleSheet(
            f"background:transparent;border:none;color:{MUTED};"
            f"font-size:9px;letter-spacing:1.5px;padding:4px 0;")
        back_b.clicked.connect(lambda: self.window()._navigate("dashboard"))
        tl = QLabel("LEGION"); tl.setStyleSheet(
            f"font-family:{FONT_DISPLAY};font-size:13px;font-weight:bold;"
            f"color:{ACCENT};letter-spacing:3px;margin-left:10px;")
        self._menu_btn = QPushButton("☰")
        self._menu_btn.setFixedSize(28, 28)
        self._menu_btn.setStyleSheet(
            f"background:transparent;border:none;color:{MUTED};"
            f"font-size:16px;padding:0;")
        self._menu_btn.clicked.connect(self._toggle_menu)
        hw.addWidget(back_b); hw.addWidget(tl); hw.addStretch()
        hw.addWidget(self._menu_btn)
        sv.addWidget(hdr_w); sv.addWidget(hline())

        self._list_tabs = QTabWidget()
        self._list_tabs.setStyleSheet(
            f"QTabWidget::pane{{border:none;background:transparent;}}"
            f"QTabBar::tab{{background:transparent;color:{MUTED};border:none;padding:5px 9px;font-size:12px;}}"
            f"QTabBar::tab:selected{{color:{ACCENT};border-bottom:2px solid {ACCENT};}}")

        self.jumpin_list = QListWidget()
        self.jumpin_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.jumpin_list.setIconSize(QSize(100, 140))
        self.jumpin_list.setGridSize(QSize(115, 175))
        self.jumpin_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.jumpin_list.setMovement(QListWidget.Movement.Static)
        self.jumpin_list.setWordWrap(True)
        self.jumpin_list.setSpacing(6)
        self.jumpin_list.setStyleSheet(
            f"QListWidget{{background:transparent;border:none;padding:8px;}}"
            f"QListWidget::item{{background:transparent;border-radius:6px;color:{TEXT2};"
            f"font-size:10px;text-align:center;}}"
            f"QListWidget::item:hover{{background:{BG2};}}"
            f"QListWidget::item:selected{{background:{BG3};color:{ACCENT};}}")
        self.jumpin_list.itemClicked.connect(self._book_clicked)
        self._list_tabs.addTab(self.jumpin_list, "Jump In")

        self._bm_tabs = QTabWidget()
        self._bm_tabs.setStyleSheet(self._list_tabs.styleSheet())
        self._bm_lists = {}
        for n in ("planning","reading","dropped","completed"):
            lw = QListWidget()
            lw.itemDoubleClicked.connect(lambda item, name=n: self._bm_double(item, name))
            lw.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            lw.customContextMenuRequested.connect(
                lambda pos, lw=lw, name=n: self._bm_context(pos, lw, name))
            self._bm_lists[n] = lw; self._bm_tabs.addTab(lw, n.capitalize())
        self._list_tabs.addTab(self._bm_tabs, "Bookmarks")
        sv.addWidget(self._list_tabs, 1)

        sv.addSpacing(8)
        btn_row = QHBoxLayout(); btn_row.setContentsMargins(12,0,12,0); btn_row.setSpacing(6)
        add_b = QPushButton("+ ADD BOOK"); add_b.setObjectName("accent")
        add_b.setStyleSheet(f"font-size:10px;letter-spacing:1px;padding:8px;")
        add_b.clicked.connect(self._add_book)
        ref_b = QPushButton("↻"); ref_b.setFixedWidth(34)
        ref_b.setStyleSheet(f"font-size:14px;padding:6px;border:1px solid {BORDER};border-radius:4px;color:{MUTED};background:transparent;")
        ref_b.clicked.connect(self.refresh)
        btn_row.addWidget(add_b, 1); btn_row.addWidget(ref_b)
        sv.addLayout(btn_row)

        disc_b = QPushButton("◈  DISCOVER NOVELS")
        disc_b.setStyleSheet(
            f"background:transparent; border:1px solid {ACCENT}; color:{ACCENT};"
            f"font-size:9px; letter-spacing:1px; padding:7px; margin:6px 0 0 0; border-radius:3px;")
        disc_b.clicked.connect(self._open_discovery)
        sv.addWidget(disc_b)
        root.addWidget(sidebar)

        # ── Reader panel (stacked: detail view / reader view) ──────────────────
        right = QWidget()
        rv = QVBoxLayout(right); rv.setContentsMargins(0,0,0,0); rv.setSpacing(0)

        self._right_stack = QStackedWidget()

        # ── 0: Detail view ────────────────────────────────────────────────────
        detail_w = QWidget()
        dv = QVBoxLayout(detail_w); dv.setContentsMargins(20,16,20,12); dv.setSpacing(10)

        self._detail_title = QLabel("")
        self._detail_title.setStyleSheet(
            f"font-size:20px;font-weight:bold;color:{ACCENT};font-family:{FONT_BODY};")
        self._detail_title.setWordWrap(True)
        dv.addWidget(self._detail_title)

        self._detail_meta = QLabel("")
        self._detail_meta.setStyleSheet(f"color:{TEXT2};font-size:13px;")
        self._detail_meta.setWordWrap(True)
        dv.addWidget(self._detail_meta)

        dv.addWidget(hline())

        self._detail_synopsis = QTextEdit()
        self._detail_synopsis.setReadOnly(True)
        self._detail_synopsis.setStyleSheet(
            f"background:{BG3};border:none;padding:12px;color:{TEXT};"
            f"font-family:{FONT_BODY};font-size:15px;")
        dv.addWidget(self._detail_synopsis, 1)

        # Progress row
        self._detail_progress = QLabel("")
        self._detail_progress.setStyleSheet(
            f"color:{ACCENT2};font-size:10px;letter-spacing:1px;")
        dv.addWidget(self._detail_progress)

        # Download status row
        self._detail_dl_status = QLabel("")
        self._detail_dl_status.setStyleSheet(
            f"color:{MUTED};font-size:10px;letter-spacing:1px;")
        dv.addWidget(self._detail_dl_status)
        dv.addSpacing(6)

        # Action buttons
        def _ab(text, style="", cb=None):
            b = QPushButton(text)
            if style == "accent":
                b.setStyleSheet(
                    f"background:{ACCENT};color:{BG};border:none;font-weight:bold;"
                    f"font-size:9px;letter-spacing:1.2px;padding:7px 16px;border-radius:3px;")
            elif style == "danger":
                b.setStyleSheet(
                    f"background:transparent;color:{RED};border:1px solid #2A1018;"
                    f"font-size:9px;letter-spacing:1px;padding:7px 14px;border-radius:3px;")
            else:
                b.setStyleSheet(
                    f"background:transparent;color:{TEXT2};border:1px solid {BORDER};"
                    f"font-size:9px;letter-spacing:1px;padding:7px 14px;border-radius:3px;"
                    f"QPushButton:hover{{color:{TEXT};border-color:{ACCENT};}}")
            if cb: b.clicked.connect(cb)
            return b

        btn_row = QHBoxLayout(); btn_row.setSpacing(6)
        self._btn_read     = _ab("▶  READ",   "accent", self._detail_read)
        self._btn_download = _ab("↓  DOWNLOAD",         self._detail_download)
        self._btn_dl_pause = _ab("⏸  PAUSE",            self._detail_pause)
        self._btn_dl_resume= _ab("▶  RESUME",           self._detail_resume)
        self._btn_dl_cancel= _ab("✕  CANCEL", "danger", self._detail_cancel)
        self._btn_refresh  = _ab("↺  INFO",             self._detail_refresh_meta)
        self._btn_new_chs  = _ab("↓  NEW CH",           self._detail_check_new)
        self._btn_delete   = _ab("REMOVE",    "danger",  self._detail_delete)

        self._btn_reset_time = _ab("↺  RESET TIME", self._detail_reset_time)
        btn_row.addWidget(self._btn_read)
        btn_row.addWidget(self._btn_delete)
        btn_row.addWidget(self._btn_reset_time)
        btn_row.addStretch()
        # Hidden but kept for download manager wiring
        for b_ in (self._btn_download, self._btn_dl_pause, self._btn_dl_resume,
                   self._btn_dl_cancel, self._btn_refresh, self._btn_new_chs):
            b_.setVisible(False)
            btn_row.addWidget(b_)
        dv.addLayout(btn_row)
        self._right_stack.addWidget(detail_w)   # index 0

        # ── 1: Reader view ────────────────────────────────────────────────────
        reader_w = QWidget()
        reader_w.setStyleSheet(f"background:{BG};")
        rw = QVBoxLayout(reader_w); rw.setContentsMargins(0,0,0,0); rw.setSpacing(0)

        # Reader top bar
        top_bar_w = QWidget()
        top_bar_w.setStyleSheet(f"background:{BG2};border-bottom:1px solid {BORDER};border-radius:0;")
        top_bar_w.setFixedHeight(44)
        tb = QHBoxLayout(top_bar_w); tb.setContentsMargins(16,0,16,0); tb.setSpacing(10)
        self._back_btn = QPushButton("← INFO")
        self._back_btn.setStyleSheet(
            f"background:transparent;border:none;color:{MUTED};"
            f"font-size:9px;letter-spacing:1.5px;padding:4px 6px;")
        self._back_btn.clicked.connect(lambda: (self._save_reading_time(), self._right_stack.setCurrentIndex(0))[-1])
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
        self._rr_btn = QPushButton("\u2b1b ROOM")
        self._rr_btn.setFixedWidth(62)
        self._rr_btn.setStyleSheet(
            f"background:transparent;border:1px solid {BORDER};color:{MUTED};"
            f"font-size:8px;letter-spacing:1px;padding:3px;border-radius:3px;")
        self._rr_btn.setToolTip("Open Reading Room \u2014 distraction-free mode")
        self._rr_btn.clicked.connect(self._open_reading_room)
        self._sage_top_btn = QPushButton("\u2736 SAGE")
        self._sage_top_btn.setFixedWidth(68)
        self._sage_top_btn.setStyleSheet(
            f"background:transparent;border:1px solid {BORDER};color:{ACCENT};"
            f"font-size:8px;letter-spacing:1px;padding:3px;border-radius:3px;")
        self._sage_top_btn.setToolTip("Toggle Sage AI panel")
        self._sage_top_btn.clicked.connect(self._toggle_sage_panel)

        self._notes_top_btn = QPushButton("\u270e NOTES")
        self._notes_top_btn.setFixedWidth(72)
        self._notes_top_btn.setStyleSheet(
            f"background:transparent;border:1px solid {BORDER};color:{ACCENT2};"
            f"font-size:8px;letter-spacing:1px;padding:3px;border-radius:3px;")
        self._notes_top_btn.setToolTip("Toggle chapter notes panel")
        self._notes_top_btn.clicked.connect(self._toggle_notes_panel)
        for b_ in (self._prev_btn, self._next_btn, self._fa_btn, self._fb_btn,
                   self._rr_btn, self._sage_top_btn, self._notes_top_btn):
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

        self.reader = QTextEdit(); self.reader.setReadOnly(True)
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
        self._sage_panel.setStyleSheet(f"background:{BG2};border-left:1px solid {BORDER};")
        self._sage_panel.setVisible(False)
        sp = QVBoxLayout(self._sage_panel); sp.setContentsMargins(10,10,10,10); sp.setSpacing(8)
        sp.addWidget(lbl("✦  SAGE", ACCENT, 13, True))
        sp.addWidget(lbl("Ask about characters, places, or anything", TEXT2, 10))
        sp.addWidget(hline())

        # Quick-ask chips
        chips_row = QHBoxLayout(); chips_row.setSpacing(4)
        for chip_text, chip_query in [("Who is…", "Who is "), ("What is…", "What is "), ("Ask", "")]:
            c = QPushButton(chip_text)
            c.setStyleSheet(
                f"background:{BG3};border:1px solid {BORDER};color:{TEXT2};"
                f"font-size:9px;letter-spacing:0.5px;padding:3px 8px;border-radius:10px;")
            if chip_query:
                c.clicked.connect(lambda _, q=chip_query: (
                    self._sage_q.setText(q),
                    self._sage_q.setFocus(),
                    self._sage_q.setCursorPosition(len(q))
                ))
            chips_row.addWidget(c)
        chips_row.addStretch()
        sp.addLayout(chips_row)

        self._sage_q = QLineEdit()
        self._sage_q.setPlaceholderText("Who is Feng Yuan?  /  What is the Spirit Sea?")
        self._sage_q.setStyleSheet(
            f"background:{BG3};border:1px solid {BORDER};color:{TEXT};"
            f"font-size:13px;padding:8px;border-radius:4px;")
        self._sage_q.returnPressed.connect(self._sage_ask)
        sp.addWidget(self._sage_q)
        self._sage_ask_btn = btn("Ask Sage", "accent", self._sage_ask)
        sp.addWidget(self._sage_ask_btn)

        self._sage_answer = QTextEdit(); self._sage_answer.setReadOnly(True)
        self._sage_answer.setStyleSheet(
            f"background:{BG3};border:none;padding:12px;"
            f"font-family:{FONT_BODY};font-size:13px;color:{TEXT};line-height:1.7;")
        sp.addWidget(self._sage_answer, 1)
        self._sage_busy = QProgressBar(); self._sage_busy.setRange(0,0)
        self._sage_busy.setVisible(False); self._sage_busy.setFixedHeight(3)
        sp.addWidget(self._sage_busy)
        reader_body.addWidget(self._sage_panel)

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

        self._reader_body = reader_body  # store ref for notes panel toggle
        reader_body.setSizes([900, 320, 0, 0])
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
        self.progress_bar = QProgressBar(); self.progress_bar.setRange(0,0)
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
        self._right_stack.addWidget(reader_w)   # index 1

        # ── 2: Downloads panel ────────────────────────────────────────────────
        dl_w = QWidget()
        dl_w.setStyleSheet(f"background:{BG};")
        dlv = QVBoxLayout(dl_w); dlv.setContentsMargins(20, 16, 20, 16); dlv.setSpacing(12)

        dl_hdr = QHBoxLayout()
        dl_title = QLabel("↓  DOWNLOADS")
        dl_title.setStyleSheet(
            f"font-size:13px;font-weight:bold;color:{ACCENT};"
            f"letter-spacing:2px;font-family:{FONT_DISPLAY};")
        dl_close = QPushButton("✕  CLOSE")
        dl_close.setStyleSheet(
            f"background:transparent;border:none;color:{MUTED};"
            f"font-size:9px;letter-spacing:1px;padding:4px 8px;")
        dl_close.clicked.connect(lambda: self._right_stack.setCurrentIndex(0))
        dl_hdr.addWidget(dl_title); dl_hdr.addStretch(); dl_hdr.addWidget(dl_close)
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

        self._right_stack.addWidget(dl_w)   # index 2

        rv.addWidget(self._right_stack, 1)
        root.addWidget(right, 1)

        # Start on a welcome message
        self._detail_title.setText("Great Sage  —  Legion")
        self._detail_synopsis.setPlainText(
            "Select a book from the list on the left to see its details.")
        self._detail_progress.setText("")
        self._detail_dl_status.setText("")
        self._update_detail_buttons(None)


    def _toggle_sage_panel(self):
        visible = self._sage_panel.isVisible()
        if visible:
            sizes = self._reader_body.sizes()
            self._sage_panel.setVisible(False)
            self._reader_body.setSizes([sizes[0] + sizes[1], 0, sizes[2]])
            self._sage_top_btn.setText("✦ SAGE")
        else:
            self._sage_panel.setVisible(True)
            total = sum(self._reader_body.sizes())
            sage_w = 340
            rest   = max(200, total - sage_w - self._reader_body.sizes()[2])
            notes_w = self._reader_body.sizes()[2]
            self._reader_body.setSizes([rest, sage_w, notes_w])
            self._sage_top_btn.setText("✕ SAGE")

    def _toggle_notes_panel(self):
        if not self._catalogue_panel:
            self._notes_top_btn.setToolTip("catalogue.py not found — place it in the same folder as great_sage_gui.py")
            self.reader_status.setText("Notes unavailable — catalogue.py missing from app folder.")
            return
        visible = self._catalogue_panel.isVisible()
        if visible:
            # Collapse notes panel: reclaim its width back to the reader
            sizes = self._reader_body.sizes()
            # sizes = [reader, sage, notes]
            self._catalogue_panel.setVisible(False)
            self._reader_body.setSizes([sizes[0] + sizes[2], sizes[1], 0])
            self._notes_top_btn.setText("✎ NOTES")
        else:
            # Expand notes panel to 320px, taken from reader (never shrink Sage)
            self._catalogue_panel.setVisible(True)
            sizes = self._reader_body.sizes()
            notes_w = 320
            reader_w = max(300, sizes[0] - notes_w)
            self._reader_body.setSizes([reader_w, sizes[1], notes_w])
            self._notes_top_btn.setText("✕ NOTES")

    def _sage_ask(self):
        q = self._sage_q.text().strip()
        if not q: return
        book    = self._current_book or "this book"
        cur_ch  = self._current_ch_num or 0
        self._sage_busy.setVisible(True)
        self._sage_ask_btn.setEnabled(False)
        self._sage_answer.setPlainText("Scanning chapters…")
        self._sage_worker = _SageCompanionWorker(q, book, current_chapter=cur_ch)
        self._sage_worker.done.connect(self._sage_answered)
        self._sage_worker.start()

    def _sage_answered(self, answer):
        self._sage_busy.setVisible(False)
        self._sage_ask_btn.setEnabled(True)
        self._sage_answer.setPlainText(answer or "(No response)")

    def _apply_font(self):
        self.reader.setStyleSheet(
            f"background:{BG3};color:{TEXT};border:none;padding:18px;"
            f"font-family:{FONT_BODY};font-size:{self._font_size}px;line-height:1.9;")

    def _on_scroll(self, value=None):
        sb  = self.reader.verticalScrollBar()
        top = sb.minimum(); bot = sb.maximum()
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
        self._right_stack.setCurrentIndex(2)
        # Start a refresh timer while panel is visible
        if not hasattr(self, "_dl_panel_timer"):
            self._dl_panel_timer = QTimer(self)
            self._dl_panel_timer.timeout.connect(self._refresh_downloads_panel)
        self._dl_panel_timer.start(3000)

    def _refresh_downloads_panel(self):
        """Rebuild the downloads panel content from current legion data."""
        # Stop if panel not visible
        if self._right_stack.currentIndex() != 2:
            if hasattr(self, "_dl_panel_timer"):
                self._dl_panel_timer.stop()
            return

        ld  = legion_data()
        books = ld.get("books", {})

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
            rl  = QVBoxLayout(row); rl.setContentsMargins(14, 10, 14, 10); rl.setSpacing(4)

            # Title + badge
            top = QHBoxLayout(); top.setSpacing(8)
            name_lbl = QLabel(name)
            name_lbl.setStyleSheet(f"font-size:13px;font-weight:bold;color:{TEXT};")
            badge_colors = {
                "downloading": ("#00C896", "#001A10"),
                "queued":      ("#A080FF", "#0D0820"),
                "paused":      ("#FFB020", "#1A0D00"),
                "failed":      ("#FF4060", "#1A0008"),
                "completed":   ("#4080FF", "#000D1A"),
            }
            bc, bg = badge_colors.get(stat, (MUTED, BG3))
            badge_text = {
                "downloading": f"⏳ Downloading",
                "queued":      "⏸ Queued",
                "paused":      "⏸ Paused",
                "failed":      "❌ Failed",
                "completed":   "✅ Complete",
            }.get(stat, stat)
            badge = QLabel(badge_text)
            badge.setStyleSheet(
                f"background:{bg};color:{bc};border:1px solid {bc};"
                f"font-size:9px;letter-spacing:1px;padding:2px 8px;border-radius:3px;")
            top.addWidget(name_lbl); top.addStretch(); top.addWidget(badge)
            rl.addLayout(top)

            # Chapter count
            info_parts = [f"{cnt} chapters downloaded"]
            if fails:
                info_parts.append(f"{fails} failed")
            info_lbl = QLabel("  ·  ".join(info_parts))
            info_lbl.setStyleSheet(f"font-size:10px;color:{MUTED};letter-spacing:0.5px;")
            rl.addWidget(info_lbl)

            # Action buttons row
            if stat in ("downloading", "queued", "paused"):
                btn_rl = QHBoxLayout(); btn_rl.setSpacing(6)
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
        mod, _ = legion_mod()
        if mod and hasattr(mod, "download_manager"):
            mod.download_manager.pause_download(name)

    def _dl_panel_resume(self, name):
        mod, _ = legion_mod()
        if not mod: return
        ld = legion_data()
        b  = ld.get("books", {}).get(name)
        if b:
            b["download_state"]["status"] = "queued"
            b["download_state"]["pause_requested"] = False
            save_json(LEGION_PROGRESS, ld)
            if hasattr(mod, "download_manager"):
                mod.download_manager.queue_download(name, b, ld)

    def _dl_panel_cancel(self, name):
        ld = legion_data()
        b  = ld.get("books", {}).get(name)
        if b:
            b["download_state"]["status"] = "cancelled"
            save_json(LEGION_PROGRESS, ld)
        self._refresh_downloads_panel()

    def _show_toast(self, message):
        """Sync notification — just refresh downloads panel if it's open."""
        if self._right_stack.currentIndex() == 2:
            self._refresh_downloads_panel()

    def refresh(self):
        ld = legion_data(); books = ld.get("books",{})
        self.jumpin_list.clear(); self._book_data = {}
        sorted_books = sorted(books.items(),
            key=lambda x: x[1].get("last_read", x[1].get("chapters_read", 0)),
            reverse=True)
        for name, b in sorted_books:
            ch = b.get("chapters_read", 0); w = b.get("words_read", 0)
            display = name if len(name) <= 20 else name[:18] + "…"
            item = QListWidgetItem(display)
            item.setToolTip(f"{name}\nChapter {ch} · {w:,} words read")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
            try:
                from plugins.book_covers import get_cover
                from PyQt6.QtGui import QIcon
                source_url = b.get("current_url", "") or b.get("url", "")
                px = get_cover(name, source_url)
                if px and not px.isNull():
                    item.setIcon(QIcon(px))
            except Exception:
                pass
            self.jumpin_list.addItem(item)
            self._book_data[name] = b
        bm = bookmarks_data()
        for lst_name, lw in self._bm_lists.items():
            lw.clear()
            for e in bm.get(lst_name,[]):
                t = e.get("title","?") if isinstance(e,dict) else str(e)
                item = QListWidgetItem(f"  {t}")
                item.setData(Qt.ItemDataRole.UserRole, e)
                item.setForeground(QColor(TEXT)); lw.addItem(item)

    def _book_clicked(self, item):
        name = item.data(Qt.ItemDataRole.UserRole)
        if not name: return
        b = self._book_data.get(name, {})
        self._current_book = name
        # Reset local-file navigation state for this new book
        self._current_ch_num = 0
        self._total_ch_local = 0
        self._reading_local  = False
        self._next_url = ""; self._prev_url = ""
        self._right_stack.setCurrentIndex(0)  # always show detail on explicit click
        self._show_detail(name, b, from_list="jumpin")
        # Auto-fetch metadata if we have a URL but no metadata yet
        url = b.get("current_url","") or b.get("url","")
        if url and not b.get("metadata"):
            self._refresh_meta_silent(name, url)

    def _bm_double(self, item, list_name):
        e = item.data(Qt.ItemDataRole.UserRole)
        if not e: return
        title = e.get("title","?") if isinstance(e,dict) else str(e)
        ld    = legion_data()
        prog  = ld.get("books",{}).get(title, {})
        entry = dict(e) if isinstance(e,dict) else {"title":title}
        for k in ("chapters_read","words_read","current_url","last_title",
                  "metadata","minutes_read","download_state"):
            if k in prog: entry.setdefault(k, prog[k])
        self._current_book = title
        # Reset navigation state for this book
        self._current_ch_num = 0
        self._total_ch_local = 0
        self._reading_local  = False
        self._next_url = ""; self._prev_url = ""
        self._show_detail(title, entry, from_list=list_name)

    def _refresh_meta_silent(self, name, url):
        """Silently fetch and cache metadata for a book without user interaction."""
        if not hasattr(self, "_meta_workers"):
            self._meta_workers = []
        w = _MetaRefreshWorker(url)
        self._meta_workers.append(w)  # keep reference so GC doesn't destroy running thread
        def _done(meta, err, worker=w):
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
            # Clean up finished worker from list
            try: self._meta_workers.remove(worker)
            except ValueError: pass
        w.done.connect(_done)
        w.start()

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
            mod, _ = legion_mod()
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
        dl_labels = {
            "downloading": f"⏳ Downloading... ({dl_count} chapters)",
            "completed":   f"✅ Downloaded ({dl_count} chapters)",
            "paused":      f"⏸ Paused ({dl_count} chapters downloaded)",
            "failed":      f"❌ Failed ({len(dl_state.get('failed_chapters',[]))} chapters failed)",
            "queued":      f"⏳ Queued ({dl_count} chapters so far)",
            "cancelled":   "✕ Download cancelled",
        }
        self._detail_dl_status.setText(dl_labels.get(dl_status, ""))

        self._update_detail_buttons(book)
        # Switch to detail unless user is reading (1) or browsing downloads (2)
        if self._right_stack.currentIndex() not in (1, 2):
            self._right_stack.setCurrentIndex(0)

    def _update_detail_buttons(self, book):
        self._btn_read.setVisible(bool(book))
        self._btn_delete.setVisible(bool(book))
        self._btn_reset_time.setVisible(bool(book))

    def _detail_read(self):
        """Open the book at the last chapter the user was on."""
        name = getattr(self, "_detail_book_name", None)
        if not name: return
        self._current_book = name

        # Always read fresh from disk
        ld   = legion_data()
        book = ld.get("books", {}).get(name, {})
        self._book_data[name] = book
        self._detail_book     = book

        url = book.get("current_url", "") or book.get("url", "")

        # Auto-bookmark
        bm = bookmarks_data()
        already = any(
            (e.get("title", "") if isinstance(e, dict) else str(e)).lower() == name.lower()
            for lst in bm.values() for e in lst
        )
        if not already:
            bm.setdefault("Reading", []).append({
                "title": name, "url": url,
                "metadata": book.get("metadata", {}),
                "added": time.time()
            })
            save_json(LEGION_BOOKMARKS, bm)

        # Get the real chapter list from the file (story numbers, e.g. [549, 550, ...])
        ch_list = []
        mod2, _ = legion_mod()
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
        mod, _ = legion_mod()
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
            except Exception: pass

        if file_ch:
            title_f, paras_f = file_ch
            self._current_ch_num = ch_num
            self._reading_local  = True
            self._right_stack.setCurrentIndex(1)
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
            ld = legion_data(); b = ld.get("books",{}).get(name)
            if b:
                b["current_chapter"] = ch_num
                b["last_title"]      = title_f
                # words_read accumulated in _next_chapter on completion, not on every open
                save_json(LEGION_PROGRESS, ld)
                # Update in-memory cache
                self._book_data[name] = b
                for i in range(self.jumpin_list.count()):
                    it = self.jumpin_list.item(i)
                    if it and it.data(Qt.ItemDataRole.UserRole) == name:
                        it.setText(f"  {name}")
                        it.setToolTip(f"Chapter {ch_num} - {b.get('words_read',0):,} words read")
                        break
            # Record chapter open time for minutes_read tracking
            self._chapter_open_time = time.time()
            self._chapter_open_words = words
            track_event("words_read", {"words": words, "book": name})
            # Update catalogue panel context
            if self._catalogue_panel:
                self._catalogue_panel.set_context(name, ch_num)
            # Start/restart heartbeat timer — saves progress every 2 min while reading
            if not hasattr(self, "_heartbeat_timer"):
                self._heartbeat_timer = QTimer()
                self._heartbeat_timer.timeout.connect(self._heartbeat_save)
            self._heartbeat_timer.start(60_000)  # 1 minute
            return

        # No local chapter — fall back to web scrape
        self._reading_local = False
        # Update notes panel context even for web-read chapters
        if self._catalogue_panel:
            self._catalogue_panel.set_context(name, ch_num)
        if fallback_url:
            self._right_stack.setCurrentIndex(1)
            self._load_url(fallback_url)
        else:
            book = self._book_data.get(name, {})
            saved_url = book.get("current_url","") or book.get("url","")
            if saved_url:
                self._right_stack.setCurrentIndex(1)
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

        mod, err = legion_mod()
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
        b  = ld.get("books", {}).get(name, {})
        if not b:
            QMessageBox.warning(self, "Error", f"Book '{name}' not found in library."); return

        # Preserve already-downloaded count if resuming
        existing_state = b.get("download_state", {})
        b["download_state"] = {
            "status":                    "queued",
            "last_downloaded_chapter":   existing_state.get("last_downloaded_chapter"),
            "last_downloaded_chapter_num": existing_state.get("last_downloaded_chapter_num", 0),
            "total_chapters_downloaded": existing_state.get("total_chapters_downloaded", 0),
            "download_path":             existing_state.get("download_path"),
            "failed_chapters":           [],
            "timestamp":                 time.time(),
            "pause_requested":           False,
        }
        ld["books"][name] = b
        save_json(LEGION_PROGRESS, ld)
        log.legion.info("Download queued", book=name, url=url, already_downloaded=already)

        try:
            mod.download_manager.queue_download(name, b, ld)
        except Exception as e:
            log.legion.exc("Failed to queue download", e, book=name)
            QMessageBox.critical(self, "Queue Failed",
                f"Failed to start download:\n{str(e)}\n\n"
                "Check that legion.py loaded correctly and try again.")
            return

        self._detail_book = b
        self._show_detail(name, b, self._detail_from_list)

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
        b  = ld.get("books", {}).get(name)
        if not b:
            self._dl_poll_timer.stop()
            self._detail_dl_status.setText("")  # clear stale label
            return
        status = b.get("download_state", {}).get("status", "idle")
        # Update detail labels live — but never kick user out of reader
        if getattr(self, "_detail_book_name", "") == name:
            self._detail_book = b
            # Only do a full _show_detail refresh if NOT currently reading
            if self._right_stack.currentIndex() == 1:
                # User is reading — just silently update the book data and buttons
                dl_state  = b.get("download_state", {})
                dl_status = dl_state.get("status", "idle")
                dl_count  = dl_state.get("total_chapters_downloaded", 0)
                dl_labels = {
                    "downloading": f"Downloading... ({dl_count} chapters)",
                    "completed":   f"✓ Downloaded ({dl_count} chapters)",
                    "paused":      f"⏸ Paused ({dl_count} chapters)",
                    "failed":      f"❌ Failed ({len(dl_state.get('failed_chapters',[]))} chapters failed)",
                    "queued":      f"⏳ Queued ({dl_count} chapters so far)",
                    "cancelled":   "✕ Download cancelled",
                }
                self._detail_dl_status.setText(dl_labels.get(dl_status, ""))
                self._update_detail_buttons(b)
            else:
                self._show_detail(name, b, getattr(self, "_detail_from_list", "jumpin"))
        # Stop polling once done
        if status not in ("queued", "downloading"):
            self._dl_poll_timer.stop()
            # If no book currently selected, clear the label
            if not getattr(self, "_detail_book_name", ""):
                self._detail_dl_status.setText("")

    def _detail_pause(self):
        name = getattr(self,"_detail_book_name",None)
        if not name: return
        mod, _ = legion_mod()
        if mod and hasattr(mod,"download_manager"):
            try: mod.download_manager.pause_download(name)
            except Exception: pass
        ld = legion_data(); b = ld.get("books",{}).get(name,{})
        b.setdefault("download_state",{})["pause_requested"] = True
        b["download_state"]["status"] = "paused"
        save_json(LEGION_PROGRESS, ld)
        self._detail_book = b; self._show_detail(name, b, self._detail_from_list)

    def _detail_resume(self):
        name = getattr(self,"_detail_book_name",None)
        if not name: return
        mod, _ = legion_mod()
        ld = legion_data(); b = ld.get("books",{}).get(name,{})
        if not b: return
        b.setdefault("download_state",{})["status"] = "queued"
        b["download_state"]["pause_requested"] = False
        save_json(LEGION_PROGRESS, ld)
        if mod and hasattr(mod,"download_manager"):
            try: mod.download_manager.queue_download(name, b, ld)
            except Exception: pass
        self._detail_book = b; self._show_detail(name, b, self._detail_from_list)
        self._start_download_poll(name)

    def _detail_cancel(self):
        name = getattr(self,"_detail_book_name",None)
        if not name: return
        ld = legion_data(); b = ld.get("books",{}).get(name,{})
        b.setdefault("download_state",{})["status"] = "cancelled"
        b["download_state"]["pause_requested"] = True
        save_json(LEGION_PROGRESS, ld)
        self._detail_book = b; self._show_detail(name, b, self._detail_from_list)


    def _detail_check_new(self):
        """Check for new chapters online and offer to download them."""
        name = getattr(self, "_detail_book_name", None)
        book = getattr(self, "_detail_book", {})
        if not name: return
        self._btn_new_chs.setEnabled(False)
        self._btn_new_chs.setText("⏳ Checking...")
        self._new_ch_worker = _NewChaptersWorker(book)
        self._new_ch_worker.done.connect(
            lambda count, err: self._on_new_chapters_checked(name, book, count, err))
        self._new_ch_worker.start()

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
        mod, err = legion_mod()
        if not mod:
            QMessageBox.warning(self, "Error", f"Can't load legion.py:\n{err}"); return
        if not hasattr(mod, "download_manager"):
            QMessageBox.warning(self, "Error", "download_manager not found in legion.py"); return
        ld = legion_data(); b = ld.get("books", {}).get(name, {})
        if not b: return
        existing = b.get("download_state", {})
        # Keep last_downloaded_chapter so it resumes from where it left off
        b["download_state"] = {
            "status":                      "queued",
            "last_downloaded_chapter":     existing.get("last_downloaded_chapter"),
            "last_downloaded_chapter_num": existing.get("last_downloaded_chapter_num", 0),
            "total_chapters_downloaded":   existing.get("total_chapters_downloaded", 0),
            "download_path":               existing.get("download_path"),
            "failed_chapters":             [],
            "timestamp":                   time.time(),
            "pause_requested":             False,
        }
        ld["books"][name] = b
        save_json(LEGION_PROGRESS, ld)
        try:
            mod.download_manager.queue_download(name, b, ld)
        except Exception as e:
            QMessageBox.critical(self, "Queue Failed", str(e)); return
        self._detail_book = b
        self._show_detail(name, b, self._detail_from_list)
        self._start_download_poll(name)

    def _detail_refresh_meta(self):
        name = getattr(self,"_detail_book_name",None)
        book = getattr(self,"_detail_book",{})
        if not name: return
        url = book.get("current_url","") or book.get("url","")
        if not url:
            self._detail_meta.setText("No URL saved — can't fetch metadata."); return
        self._detail_meta.setText("Fetching metadata...  ⏳")
        self._meta_worker = _MetaRefreshWorker(url)
        self._meta_worker.done.connect(
            lambda meta, err: self._on_meta_refreshed(name, meta, err))
        self._meta_worker.start()

    def _on_meta_refreshed(self, name, meta, err):
        if err:
            self._detail_meta.setText(f"⚠ {err}"); return
        ld = legion_data(); b = ld.get("books",{}).get(name,{})
        b["metadata"] = meta
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
        mod, _ = legion_mod()
        if mod:
            legion_dir = os.path.dirname(os.path.abspath(mod.__file__))
            fname = re.sub(r'[^\w\-_\. ]', '_', name) + ".txt"
            candidate = os.path.join(legion_dir, fname)
            if os.path.exists(candidate):
                txt_path = candidate

        # 2. Stop any active download for this book
        if mod and hasattr(mod, "download_manager"):
            try:
                mod.download_manager.active_downloads.pop(name, None)
                book_dl = mod.download_manager.active_downloads.get(name, {})
                if book_dl.get("download_state"):
                    book_dl["download_state"]["pause_requested"] = True
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
        mod, err = legion_mod()
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
        mod, err = legion_mod()
        if mod:
            try: mod.remove_from_bookmarks(title); self.refresh(); return
            except Exception: pass
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
        self._worker = FetchChapterWorker(url)
        self._worker.status.connect(lambda s: self.reader_status.setText(s))
        self._worker.done.connect(self._chapter_done)
        self._worker.error.connect(self._chapter_error)
        self._worker.start()

    def _chapter_done(self, title, paragraphs, next_url, prev_url, url_ch_num=0):
        self.progress_bar.setVisible(False)
        self._next_url = next_url; self._prev_url = prev_url
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
            ld = legion_data(); b = ld.get("books",{}).get(self._current_book)
            if b:
                b["current_url"]     = self._current_url
                # Extract real chapter number from title e.g. "Chapter 551: ..."
                m_ch = re.search(r'[Cc]hapter\s+(\d+)', title)
                if m_ch:
                    b["current_chapter"] = int(m_ch.group(1))
                    self._current_ch_num = int(m_ch.group(1))
                elif url_ch_num:
                    b["current_chapter"] = url_ch_num
                    self._current_ch_num = url_ch_num
                b["last_title"]      = title
                if next_url: b["next_url"] = next_url
                b["words_read"]      = b.get("words_read", 0) + words
                save_json(LEGION_PROGRESS, ld)
                if self._current_book in self._book_data:
                    self._book_data[self._current_book] = b
            # Update catalogue panel with resolved chapter number
            if self._catalogue_panel:
                self._catalogue_panel.set_context(self._current_book, self._current_ch_num)

    def _chapter_error(self, msg):
        log.legion.error("Chapter load error", book=getattr(self,"_current_book","?"),
                         chapter=getattr(self,"_current_ch_num",0), error=msg)
        self.progress_bar.setVisible(False)
        self.reader.setPlainText(
            f"Error loading chapter:\n\n{msg}\n\n"
            "Tips:\n- Check the URL is a valid chapter page\n"
            "- Some sites block scrapers - try opening in browser first\n"
            "- Try a mirror: novelbin.me or novelfull.com")
        self.reader_status.setText("Failed.")

    def _heartbeat_save(self):
        """Called every 2 minutes while reading — saves scroll position and time."""
        name = getattr(self, "_current_book", None)
        if not name or self._right_stack.currentIndex() != 1: return
        # Save scroll position
        sb = self.reader.verticalScrollBar()
        if sb.maximum() > 0:
            frac = (sb.value() - sb.minimum()) / (sb.maximum() - sb.minimum())
            self._scroll_positions.setdefault(name, {})[self._current_ch_num] = frac
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
            ld = legion_data(); b = ld.get("books", {}).get(name)
            if b:
                b["minutes_read"] = round(b.get("minutes_read", 0) + elapsed_mins, 1)
                save_json(LEGION_PROGRESS, ld)
                self._book_data[name] = b
        except Exception: pass
        self._chapter_open_time = None

    def _next_chapter(self):
        if self._reading_local and self._current_ch_num > 0:
            self._save_reading_time()
            # Accumulate words_read on chapter completion (not on open)
            words_just_read = len(self.reader.toPlainText().split())
            ld = legion_data(); b = ld.get("books", {}).get(self._current_book)
            if b:
                b["chapters_read"] = b.get("chapters_read", 0) + 1
                b["words_read"]    = b.get("words_read", 0) + words_just_read
                save_json(LEGION_PROGRESS, ld)
                self._book_data[self._current_book] = b
            track_event("chapter_finished", {"book": self._current_book, "ch": self._current_ch_num})

            # Look up actual next story chapter number from the file
            next_num = None
            mod, _ = legion_mod()
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
        mod, _ = legion_mod()
        if mod and hasattr(mod, "_get_chapter_list_from_file"):
            try:
                raw = mod._get_chapter_list_from_file(name)
                # Returns [(story_num, subtitle), ...] e.g. [(549, "Holy Grail vs Bloodline"), ...]
                for num, subtitle in raw:
                    label = f"Chapter {num}" + (f": {subtitle}" if subtitle and subtitle != f"Chapter {num}" else "")
                    chapters.append((num, label))
            except Exception: pass

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
                    f = item.font(); f.setBold(True); item.setFont(f)
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

        btns = QHBoxLayout(); btns.setSpacing(8)
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
            mod, _ = legion_mod()
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
        self._font_size = max(10, min(30, self._font_size + d)); self._apply_font()

    def _open_reading_room(self):
        text = self.reader.toPlainText()
        if not text.strip():
            return
        title = self.chapter_title.text() or self._current_book
        book_data = self._book_data.get(self._current_book, {})
        genre = _detect_genre(self._current_book, book_data)
        # Get main window reference to hide/restore it
        main_win = self.window()
        self._reading_room = ReadingRoomOverlay(text, title, genre)

        # Keyboard shortcut callbacks
        def _rr_next():
            self._next_chapter()
            QTimer.singleShot(1800, lambda: (
                self._reading_room.load_text(
                    self.reader.toPlainText(),
                    self.chapter_title.text() or self._current_book)
                if self._reading_room else None))
        def _rr_prev():
            self._prev_chapter()
            QTimer.singleShot(1800, lambda: (
                self._reading_room.load_text(
                    self.reader.toPlainText(),
                    self.chapter_title.text() or self._current_book)
                if self._reading_room else None))
        def _rr_sage():
            self._reading_room.close()
            if hasattr(main_win, '_navigate'):
                main_win._navigate("sage")

        self._reading_room.on_next = _rr_next
        self._reading_room.on_prev = _rr_prev
        self._reading_room.on_sage = _rr_sage

        def _on_rr_closed():
            main_win.showNormal()
            main_win.raise_()
            main_win.activateWindow()
        self._reading_room.closed.connect(_on_rr_closed)
        main_win.showMinimized()
        self._reading_room.showFullScreen()
        self._reading_room.raise_()
        self._reading_room.activateWindow()
        self._reading_room.setFocus()
        self._reading_room.grabKeyboard()

    def _open_discovery(self):
        dlg = LegionDiscoveryDialog(self)
        def _on_book_chosen(title, url):
            # If this book was deleted this session, un-delete it
            # Add the book to Jump In so user can then download it
            ld = legion_data()
            if title not in ld.get("books", {}):
                ld.setdefault("books", {})[title] = {
                    "current_url": url, "next_url": None, "last_title": "Not started",
                    "chapters_read": 0, "words_read": 0, "minutes_read": 0,
                    "current_chapter": None,
                    "new_chapters_waiting": 0, "metadata": {},
                    "download_state": {"status": "idle", "last_downloaded_chapter": None,
                        "last_downloaded_chapter_num": 0, "total_chapters_downloaded": 0,
                        "download_path": None, "failed_chapters": [], "timestamp": None,
                        "pause_requested": False}}
                save_json(LEGION_PROGRESS, ld)
                self.refresh()
            # Show the book's detail panel
            book = ld["books"][title]
            self._book_data[title] = book
            self._show_detail(title, book, "jumpin")
            # Select it in the list
            for i in range(self.jumpin_list.count()):
                it = self.jumpin_list.item(i)
                if it and it.data(Qt.ItemDataRole.UserRole) == title:
                    self.jumpin_list.setCurrentItem(it)
                    break
        dlg.book_chosen.connect(_on_book_chosen)
        dlg.exec()

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


class LegionDiscoveryDialog(QDialog):
    """AI-powered novel discovery — describe what you want, Sage finds it."""
    book_chosen = pyqtSignal(str, str)  # emits (title, url)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("◈  Discover Novels")
        self.setModal(True)
        self.resize(720, 580)
        self.setStyleSheet(f"background:{BG}; color:{TEXT};")
        if parent:
            pg = parent.window().geometry()
            self.move(pg.x() + (pg.width() - 720) // 2,
                      pg.y() + (pg.height() - 580) // 2)
        self._worker = None
        self._build()

    def _build(self):
        v = QVBoxLayout(self); v.setContentsMargins(24, 20, 24, 20); v.setSpacing(14)

        title_lbl = lbl("◈  LEGION DISCOVERY", ACCENT, 15, True)
        sub_lbl   = lbl("Describe the novel you're looking for in plain language", MUTED, 11)
        v.addWidget(title_lbl); v.addWidget(sub_lbl); v.addWidget(hline())

        self._input = QTextEdit()
        self._input.setPlaceholderText(
            "e.g. A cultivation novel with a cold, calculating MC who uses politics and strategy "
            "rather than brute force. Ongoing, at least 500 chapters, no harem.")
        self._input.setFixedHeight(90)
        self._input.setStyleSheet(
            f"background:{BG3}; border:1px solid {BORDER}; color:{TEXT};"
            f"font-size:13px; padding:10px; border-radius:4px;")
        v.addWidget(self._input)

        row = QHBoxLayout()
        self._search_btn = btn("◈  FIND NOVELS", "accent", self._search)
        self._spin = QProgressBar(); self._spin.setRange(0, 0)
        self._spin.setFixedHeight(4); self._spin.setVisible(False)
        row.addWidget(self._search_btn); row.addWidget(self._spin, 1)
        v.addLayout(row)

        v.addWidget(hline())
        self._results_lbl = lbl("Results will appear here", MUTED, 11)
        v.addWidget(self._results_lbl)

        self._results_list = QListWidget()
        self._results_list.setStyleSheet(
            f"QListWidget{{background:{BG3}; border:1px solid {BORDER}; color:{TEXT}; font-size:13px;}}"
            f"QListWidget::item{{padding:6px 10px; border-radius:4px;}}"
            f"QListWidget::item:hover{{background:{BG2};}}"
            f"QListWidget::item:selected{{background:{ACCENT}; color:{BG};}}")
        self._results_list.itemDoubleClicked.connect(self._open_book)
        v.addWidget(self._results_list, 1)

        hint = lbl("Double-click a result to add it to your Jump In list", MUTED, 10)
        v.addWidget(hint)

    def _search(self):
        query = self._input.toPlainText().strip()
        if not query: return
        self._search_btn.setEnabled(False)
        self._spin.setVisible(True)
        self._results_list.clear()
        self._results_lbl.setText("Sage is searching...")
        self._worker = _DiscoveryWorker(query)
        self._worker.done.connect(self._on_results)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_results(self, results: list):
        self._search_btn.setEnabled(True); self._spin.setVisible(False)
        if not results:
            self._results_lbl.setText("No results found — try a different description")
            return
        self._results_lbl.setText(f"{len(results)} novel(s) found — double-click to add to Jump In")
        for r in results:
            title = r.get("title", "Unknown")
            url   = r.get("url", "")
            desc  = r.get("desc", "")
            text  = f"  {title}\n  {desc[:90]}{'…' if len(desc) > 90 else ''}" if desc else f"  {title}"
            item  = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, (title, url))
            item.setSizeHint(QSize(0, 58 if desc else 34))
            self._results_list.addItem(item)

    def _on_error(self, msg: str):
        self._search_btn.setEnabled(True); self._spin.setVisible(False)
        self._results_lbl.setText(f"Error: {msg}")

    def _open_book(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            title, url = data
            self.book_chosen.emit(title, url)
            self.accept()


class AddBookDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent); self.result_data = ("","")
        self.setWindowTitle("Add Book"); self.setMinimumWidth(440); self.setStyleSheet(QSS)
        lay = QFormLayout(self); lay.setSpacing(12); lay.setContentsMargins(18,18,18,18)
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
        self.result_data = (self.t.text().strip(), self.u.text().strip()); self.accept()



# ═══════════════════════════════════════════════════════════════════════════════
# CALENDAR DIALOG
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
        import datetime as dt, urllib.request, urllib.parse
        import concurrent.futures, threading

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

        # Deduplicate and sort each day
        for k in by_date:
            seen = set(); unique = []
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
        root = QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        hdr = QWidget(); hdr.setStyleSheet(f"background:{BG2};border-bottom:1px solid {BORDER};")
        hdr.setFixedHeight(52)
        hv = QHBoxLayout(hdr); hv.setContentsMargins(28,0,28,0)
        tl = QLabel("HIGHLIGHTS")
        tl.setStyleSheet(f"font-family:{FONT_DISPLAY};font-size:13px;font-weight:bold;color:{ACCENT};letter-spacing:4px;")
        hv.addWidget(tl); hv.addStretch()
        root.addWidget(hdr)

        body = QHBoxLayout(); body.setContentsMargins(20,16,20,16); body.setSpacing(12)

        ld = legion_data(); md = matrix_data()
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
            cv = QVBoxLayout(col); cv.setContentsMargins(0,0,0,0); cv.setSpacing(0)
            hdr2 = QWidget()
            hdr2.setStyleSheet(f"background:{BG3};border-bottom:1px solid {BORDER};border-radius:6px 6px 0 0;")
            hv2 = QHBoxLayout(hdr2); hv2.setContentsMargins(14,10,14,10)
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
        close_btn.setObjectName("accent")
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

        lay = QVBoxLayout(self); lay.setContentsMargins(16,16,16,16); lay.setSpacing(10)
        lay.addWidget(lbl("What\'s Airing This Week", ACCENT, 16, True))
        lay.addWidget(hline())

        # ── Calendar grid (7 day buttons) ─────────────────────────────────
        import datetime as dt
        self._today = dt.datetime.now()
        grid = QHBoxLayout(); grid.setSpacing(6)
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
        import datetime as dt
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
        import datetime as dt
        self._period = period
        year = dt.datetime.now().year
        title = f"🏆  Your {year} Wrapped" if period == "year" else "📊  All-Time Stats"
        self.setWindowTitle(title)
        self.setMinimumSize(560, 580); self.setStyleSheet(QSS)
        lay = QVBoxLayout(self); lay.setContentsMargins(20,20,20,16); lay.setSpacing(12)
        lay.addWidget(lbl(title, ACCENT, 18, True))
        lay.addWidget(hline())
        self._body = QVBoxLayout(); lay.addLayout(self._body, 1)
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
        import datetime as dt
        ld = legion_data(); md = matrix_data()
        bd = behaviour_data()
        sigs = bd.get("signals", {})
        sessions = bd.get("sessions", [])
        year = dt.datetime.now().year

        # Filter sessions by period
        if self._period == "year":
            cutoff = dt.datetime(year, 1, 1).timestamp()
            sessions = [s for s in sessions if s.get("timestamp", 0) >= cutoff]

        books = ld.get("books", {})
        watching = md.get("watching", {})
        wl = md.get("watchlist", {})

        # Compute stats
        chapters_read = sum(b.get("chapters_read", 0) for b in books.values())
        words_read = sigs.get("total_words", 0) if self._period == "alltime" else sum(
            s.get("data",{}).get("words",0) for s in sessions if s.get("type")=="words_read")
        watch_mins = sigs.get("total_watch_minutes", 0) if self._period == "alltime" else sum(
            s.get("data",{}).get("minutes",0) for s in sessions if s.get("type")=="watch_time")
        eps_finished = sigs.get("episodes_finished", 0) if self._period == "alltime" else sum(
            1 for s in sessions if s.get("type")=="episode_finished")
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

        # Top genres from behaviour
        gc = sigs.get("genre_counts", {})
        top_genre = sorted(gc.items(), key=lambda x: -x[1])[0][0] if gc else "—"

        # Finish rate
        fin = sigs.get("chapters_finished", 0)
        abd = sigs.get("chapters_abandoned", 0)
        finish_rate = f"{int(fin/(fin+abd)*100)}%" if fin+abd > 0 else "—"

        stats = [
            ("📖  Chapters read",        chapters_read,  NEON),
            ("📝  Words read",           f"{words_read:,}" if words_read else "—", TEXT),
            ("🎬  Episodes watched",     eps_finished,   BLUE),
            ("⏱  Hours watched",        f"{watch_mins//60}h {watch_mins%60}m" if watch_mins else "—", BLUE),
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

