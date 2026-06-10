"""
gs_sage_ui.py — Great Sage
===========================
Sage AI page and Settings page.
"""
import os, re, subprocess, tempfile, threading

try:
    from gs_logger import log
except Exception as _log_err:
    class _NoopLog:
        def __getattr__(self, name): return _NoopLog()
        def __call__(self, *a, **kw): return None
    log = _NoopLog()

from gs_matrix_ui import AddToWLDialog

from gs_theme import *
from gs_widgets import lbl, btn, hline, vline, tag, NavRail, EyeBreakToast, SyncToast, _mobile_server_port

from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QSize, QRectF, QRect, QUrl, QPoint, QObject,
)
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    WEBENGINE_OK = True
except ImportError:
    WEBENGINE_OK = False
from PyQt6.QtGui import (
    QColor, QFont, QPalette, QTextCursor, QTextOption, QKeySequence, QShortcut,
    QPixmap, QPainter, QLinearGradient, QRadialGradient, QBrush, QPen, QPainterPath,
    QDesktopServices
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
    _SageCompanionWorker, _NewChaptersWorker, _MetaRefreshWorker,
    start_mobile_server,
)


class SagePage(QWidget):
    def __init__(self):
        super().__init__()
        self._worker = None; self._chat_history = []; self._last_response = ""
        self._build()

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0,0,0,0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setFixedWidth(258)
        sidebar.setStyleSheet(
            f"QFrame {{ background:{BG}; border-right:1px solid #1A1A24; }}")
        sv = QVBoxLayout(sidebar)
        sv.setContentsMargins(0,0,0,0)
        sv.setSpacing(0)

        tab_bar = QWidget()
        tab_bar.setFixedHeight(36)
        tab_bar.setStyleSheet(f"background:{BG}; border-bottom:1px solid #1A1A24;")
        tb = QHBoxLayout(tab_bar)
        tb.setContentsMargins(0,0,0,0)
        tb.setSpacing(0)
        self._tab_btns = {}
        self._tab_pages = {}

        def _make_tab(key, label, accent_color):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setStyleSheet(f"""
                QPushButton {{
                    background:transparent; border:none;
                    border-bottom:1px solid transparent;
                    font-family:{FONT_UI}; font-size:8px; letter-spacing:2px;
                    color:#505068; padding:0; margin-bottom:-1px;
                }}
                QPushButton:hover {{ color:#8888A8; }}
                QPushButton:checked {{ color:{accent_color}; border-bottom:1px solid {accent_color}; }}
            """)
            b.clicked.connect(lambda _, k=key: self._switch_tab(k))
            tb.addWidget(b)
            self._tab_btns[key] = b

        _make_tab("discover", "DISCOVER", ACCENT)
        _make_tab("analyse",  "ANALYSE",  ACCENT)
        _make_tab("chat",     "CHAT",     ACCENT2)
        sv.addWidget(tab_bar)

        self._tab_stack = QStackedWidget()
        self._tab_stack.setStyleSheet("background:transparent;")
        sv.addWidget(self._tab_stack, 1)
        self._nav_btns = {}

        def _section_hdr(parent_layout, label, color):
            hdr = QWidget()
            hdr.setFixedHeight(32)
            hdr.setStyleSheet("background:transparent;")
            hl = QHBoxLayout(hdr)
            hl.setContentsMargins(18,0,18,0)
            hl.setSpacing(8)
            pip = QWidget()
            pip.setFixedSize(18, 1)
            pip.setStyleSheet(f"background:{color}; border:none;")
            lbl_w = QLabel(label)
            lbl_w.setStyleSheet(
                f"font-family:{FONT_UI}; font-size:8px; letter-spacing:3px; "
                f"color:#505068; background:transparent; border:none;")
            chev = QLabel("›")
            chev.setStyleSheet("color:#505068; font-size:12px; background:transparent; border:none;")
            hl.addWidget(pip); hl.addWidget(lbl_w, 1); hl.addWidget(chev)
            parent_layout.addWidget(hdr)
            body = QWidget(); body.setStyleSheet("background:transparent;")
            bv = QVBoxLayout(body)
            bv.setContentsMargins(0,0,0,0)
            bv.setSpacing(0)
            parent_layout.addWidget(body)
            def _toggle():
                body.setVisible(not body.isVisible())
                chev.setText("›" if body.isVisible() else "‹")
            hdr.mousePressEvent = lambda e, t=_toggle: t()
            return bv

        def _nav_item(layout, key, num, icon_char, label_text, desc_text, section):
            b = QPushButton(f"{num}  {icon_char}  {label_text}")
            b.setToolTip(desc_text)
            b.setStyleSheet(f"""
                QPushButton {{
                    background:transparent; border:none;
                    border-left:2px solid transparent;
                    font-family:{FONT_UI}; font-size:12px;
                    color:#9898B8; text-align:left;
                    padding:9px 18px 9px 14px;
                }}
                QPushButton:hover {{
                    background:#0F0F15; border-left:2px solid #303048; color:#D8D4CC;
                }}
            """)
            b.clicked.connect(lambda _, k=key: self._run(k))
            layout.addWidget(b)
            self._nav_btns[key] = (b, section)

        disc_w = QWidget()
        disc_v = QVBoxLayout(disc_w)
        disc_v.setContentsMargins(0,0,0,0); disc_v.setSpacing(0)
        rec_bv = _section_hdr(disc_v, "RECOMMENDATIONS", ACCENT)
        _nav_item(rec_bv, "novels",     "01", "▫", "Novel Recs",        "tailored to your taste",       "discover")
        _nav_item(rec_bv, "shows",      "02", "▣", "Show & Anime Recs", "screen picks for tonight",     "discover")
        _nav_item(rec_bv, "similar",    "03", "◎", "Something Similar", "more of what you love",        "discover")
        mood_bv = _section_hdr(disc_v, "MOOD", ACCENT)
        _nav_item(mood_bv, "mood_light", "04", "○", "Light & Fun",      "easy reads & comfort watches",  "discover")
        _nav_item(mood_bv, "mood_heavy", "05", "◆", "Intense & Deep",   "darker, heavier stories",       "discover")
        _nav_item(mood_bv, "whats_next", "06", "→", "What's Next?",    "your logical next chapter",     "discover")
        qp_w = QWidget(); qp_w.setStyleSheet("background:transparent;")
        qp_l = QHBoxLayout(qp_w)
        qp_l.setContentsMargins(14,6,14,8)
        qp_l.setSpacing(8)
        qp_btn = QPushButton("⚡  Quick Pick")
        qp_btn.setStyleSheet(f"""
            QPushButton {{
                background:#100E07; border:1px solid #2A2208; border-radius:4px;
                font-family:{FONT_UI}; font-size:12px; letter-spacing:0.5px;
                color:#A08830; padding:9px 12px; text-align:left;
            }}
            QPushButton:hover {{ background:#140F07; border-color:#3A3010; color:{ACCENT}; }}
        """)
        qp_btn.clicked.connect(lambda: self._run("quick"))
        qp_badge = QLabel("INSTANT")
        qp_badge.setStyleSheet(
            f"font-family:{FONT_UI}; font-size:7px; letter-spacing:1.5px; "
            f"color:#504010; background:#1A1206; border:1px solid #2A1E08; "
            f"border-radius:2px; padding:2px 5px;")
        qp_l.addWidget(qp_btn, 1); qp_l.addWidget(qp_badge)
        disc_v.addWidget(qp_w); disc_v.addStretch()
        self._tab_pages["discover"] = disc_w; self._tab_stack.addWidget(disc_w)

        anal_w = QWidget()
        anal_v = QVBoxLayout(anal_w)
        anal_v.setContentsMargins(0,0,0,0); anal_v.setSpacing(0)
        tool_bv = _section_hdr(anal_v, "TOOLS", ACCENT2)
        _nav_item(tool_bv, "explain",  "01", "?", "Would I Like This?", "taste-match any title",    "analyse")
        _nav_item(tool_bv, "chapter",  "02", "¶", "Chapter Summary",    "catch up on a book",       "analyse")
        _nav_item(tool_bv, "priority", "03", "↑", "Rank My Watchlist",  "prioritise what to watch", "analyse")
        _nav_item(tool_bv, "profile",  "04", "✦", "View My Profile",    "your full taste map",      "analyse")
        anal_v.addStretch()
        self._tab_pages["analyse"] = anal_w; self._tab_stack.addWidget(anal_w)

        chat_w = QWidget()
        chat_v = QVBoxLayout(chat_w)
        chat_v.setContentsMargins(16,20,16,16); chat_v.setSpacing(14)
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background:#080D0C; border:1px solid #0F2820; border-radius:6px; }")
        card_v = QVBoxLayout(card)
        card_v.setContentsMargins(18,16,18,16)
        card_v.setSpacing(8)
        card_title = QLabel("Chat with Sage")
        card_title.setStyleSheet(
            f"font-family:{FONT_UI}; font-size:12px; letter-spacing:0.5px; "
            f"color:{ACCENT2}; background:transparent; border:none;")
        card_sub = QLabel(
            "Ask anything about novels, shows,\n"
            "or your taste profile. Sage has\n"
            "full context of your history.")
        card_sub.setStyleSheet(
            f"font-family:{FONT_UI}; font-size:10px; letter-spacing:0.3px; "
            f"color:#2A7060; background:transparent; border:none;")
        open_chat_btn = QPushButton("Open Chat  →")
        open_chat_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; border:1px solid #1A4030; border-radius:3px;
                font-family:{FONT_UI}; font-size:9px; letter-spacing:1px;
                color:{ACCENT2}; padding:7px 14px; margin-top:4px;
            }}
            QPushButton:hover {{ background:#0A1A14; border-color:#2A6048; }}
        """)
        open_chat_btn.clicked.connect(lambda: self._stack.setCurrentIndex(1))
        card_v.addWidget(card_title); card_v.addWidget(card_sub); card_v.addWidget(open_chat_btn)
        chat_v.addWidget(card)
        div = QWidget()
        div.setFixedHeight(1)
        div.setStyleSheet("background:#1A1A24; border:none;")
        chat_v.addWidget(div)
        for color, text in [
            (ACCENT,   "Remembers your reading history\nand current books"),
            (ACCENT2,  "Knows your watchlist and\nviewing habits"),
            ("#505068", "Retains mood & taste\nacross sessions"),
        ]:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(2,0,2,0); rl.setSpacing(10)
            pip = QLabel("·"); pip.setFixedWidth(10)
            pip.setStyleSheet(
                f"color:{color}; font-size:14px; background:transparent; border:none;")
            txt = QLabel(text)
            txt.setWordWrap(True)
            txt.setStyleSheet(
                f"font-family:{FONT_UI}; font-size:10px; letter-spacing:0.3px; "
                f"color:#505068; background:transparent; border:none;")
            rl.addWidget(pip, 0); rl.addWidget(txt, 1)
            chat_v.addWidget(row)
        chat_v.addStretch()
        self._tab_pages["chat"] = chat_w; self._tab_stack.addWidget(chat_w)

        footer = QWidget()
        footer.setFixedHeight(32)
        footer.setStyleSheet(f"background:{BG}; border-top:1px solid #1A1A24;")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(18,0,18,0)
        fl.setSpacing(8)
        self.groq_lbl = QLabel("● GROQ CONNECTED")
        self.groq_lbl.setStyleSheet(
            f"font-family:{FONT_UI}; font-size:8px; letter-spacing:1.5px; color:#236050; border:none;")
        model_lbl = QLabel("llama-3.3-70b")
        model_lbl.setStyleSheet(
            f"font-family:{FONT_UI}; font-size:8px; color:#404058; border:none;")
        fl.addWidget(self.groq_lbl, 1); fl.addWidget(model_lbl)
        sv.addWidget(footer)
        root.addWidget(sidebar)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_output())
        self._stack.addWidget(self._build_chat())
        root.addWidget(self._stack, 1)

        self._switch_tab("discover")
        self._set_nav_active("novels")

    def _switch_tab(self, key):
        for k, b in self._tab_btns.items():
            b.setChecked(k == key)
        self._tab_stack.setCurrentWidget(self._tab_pages[key])

    def _set_nav_active(self, key):
        for k, (b, section) in self._nav_btns.items():
            if k == key:
                color = ACCENT if section == "discover" else ACCENT2
                bg    = "#0C0B07" if section == "discover" else "#080D0C"
                b.setStyleSheet(f"""
                    QPushButton {{
                        background:{bg}; border:none;
                        border-left:2px solid {color};
                        font-family:{FONT_UI}; font-size:12px;
                        color:{color}; text-align:left;
                        padding:9px 18px 9px 14px;
                    }}
                """)
            else:
                b.setStyleSheet(f"""
                    QPushButton {{
                        background:transparent; border:none;
                        border-left:2px solid transparent;
                        font-family:{FONT_UI}; font-size:12px;
                        color:#9898B8; text-align:left;
                        padding:9px 18px 9px 14px;
                    }}
                    QPushButton:hover {{
                        background:#0F0F15; border-left:2px solid #303048; color:#D8D4CC;
                    }}
                """)
    def _build_output(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(20,14,20,10)
        v.setSpacing(8)

        # Title row with refresh button
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0,0,0,0)
        self.out_title = lbl("Select a mode from the left", ACCENT, 16, True)
        title_row.addWidget(self.out_title, 1)
        self._refresh_btn = QPushButton("↻  REFRESH")
        self._refresh_btn.setStyleSheet(
            f"background:transparent; border:1px solid {BORDER}; color:{MUTED};"
            f"font-size:9px; letter-spacing:1.5px; padding:6px 14px; border-radius:3px;")
        self._refresh_btn.setVisible(False)
        self._refresh_btn.clicked.connect(self._on_refresh)
        title_row.addWidget(self._refresh_btn)
        v.addLayout(title_row)

        self.explain_row = QWidget()
        er = QHBoxLayout(self.explain_row)
        er.setContentsMargins(0,0,0,0)
        self.explain_input = QLineEdit(); self.explain_input.setPlaceholderText("Enter a title to analyse...")
        er.addWidget(self.explain_input,1)
        er.addWidget(btn("Analyse","accent",lambda: self._run("explain_go")))
        self.explain_row.setVisible(False); v.addWidget(self.explain_row)

        self.chapter_row = QWidget()
        cr = QHBoxLayout(self.chapter_row)
        cr.setContentsMargins(0,0,0,0)
        self.chapter_input = QComboBox()
        self.chapter_input.setEditable(True)
        self.chapter_input.setPlaceholderText("Book title for summary...")
        cr.addWidget(self.chapter_input,1)
        cr.addWidget(btn("Summarise","accent",lambda: self._run("chapter_go")))
        self.chapter_row.setVisible(False); v.addWidget(self.chapter_row)

        v.addWidget(hline())
        self.out_area = QTextEdit()
        self.out_area.setReadOnly(True)
        v.addWidget(self.out_area,1)

        bar_row = QHBoxLayout()
        self.spin = QProgressBar()
        self.spin.setRange(0,0)
        self.spin.setVisible(False)
        self.spin.setFixedHeight(4)
        self.add_wl_btn = btn("+ Add Recommendations to Watchlist", cb=self._add_rec_to_wl)
        self.add_wl_btn.setVisible(False)
        self.trailer_btn = btn("▶ WATCH TRAILER", cb=self._sage_watch_trailer)
        self.trailer_btn.setStyleSheet(
            f"background:transparent; border:1px solid {ACCENT2}; color:{ACCENT2};"
            f"font-size:9px; letter-spacing:1px; padding:6px 14px; border-radius:3px;")
        self.trailer_btn.setVisible(False)
        bar_row.addWidget(self.spin,1); bar_row.addWidget(self.trailer_btn); bar_row.addWidget(self.add_wl_btn)
        v.addLayout(bar_row)

        # ── Plugin slot: sage_below_output ────────────────────────────────────
        try:
            from plugin_manager import SlotHost as _SH
            v.addWidget(_SH("sage_below_output"))
        except Exception:
            pass
        return w

    def _build_chat(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0,0,0,0)
        v.setSpacing(0)
        # Header
        top_bar = QWidget()
        top_bar.setStyleSheet(f"background:{BG2};border-bottom:1px solid {BORDER};")
        top_bar.setFixedHeight(52)
        th = QHBoxLayout(top_bar)
        th.setContentsMargins(28,0,28,0)
        tl = QLabel("CHAT WITH SAGE")
        tl.setStyleSheet(f"font-family:{FONT_DISPLAY};font-size:13px;font-weight:bold;color:{ACCENT};letter-spacing:3px;")
        back_b = QPushButton("← BACK")
        back_b.setStyleSheet(
            f"background:transparent;border:none;color:{MUTED};"
            f"font-size:9px;letter-spacing:1.5px;")
        back_b.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        th.addWidget(tl); th.addStretch(); th.addWidget(back_b)
        v.addWidget(top_bar)
        # Chat area
        self.chat_area = QTextEdit()
        self.chat_area.setReadOnly(True)
        self.chat_area.setStyleSheet(
            f"background:{BG};border:none;padding:20px 28px;"
            f"font-family:{FONT_BODY};font-size:16px;color:{TEXT};")
        v.addWidget(self.chat_area, 1)
        self.chat_typing = QLabel("")
        self.chat_typing.setStyleSheet(f"color:{MUTED};font-size:10px;padding:0 28px;letter-spacing:1px;")
        v.addWidget(self.chat_typing)
        # Input row
        input_bar = QWidget()
        input_bar.setStyleSheet(f"background:{BG2};border-top:1px solid {BORDER};")
        ib = QHBoxLayout(input_bar)
        ib.setContentsMargins(20,12,20,12)
        ib.setSpacing(8)
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Message Sage...")
        self.chat_input.returnPressed.connect(self._send_chat)
        send_btn = QPushButton("SEND")
        send_btn.setStyleSheet(accent_btn_style())
        send_btn.setStyleSheet(f"font-size:9px;letter-spacing:1.5px;padding:8px 18px;")
        send_btn.clicked.connect(self._send_chat)
        clear_btn = QPushButton("✕")
        clear_btn.setFixedWidth(32)
        clear_btn.setStyleSheet(f"background:transparent;border:none;color:{MUTED};font-size:14px;")
        clear_btn.clicked.connect(lambda: (self.chat_area.clear(), self._chat_history.clear()))
        ib.addWidget(self.chat_input, 1); ib.addWidget(send_btn); ib.addWidget(clear_btn)
        v.addWidget(input_bar)
        return w

    def refresh(self):
        self.groq_lbl.setText("○ CHECKING...")
        self.groq_lbl.setStyleSheet(f"font-family:{FONT_UI};font-size:8px;letter-spacing:1.5px;color:{MUTED};border:none;")

        def _check():
            mod, err = sage_mod()
            if mod:
                _s = matrix_data().get("settings", {})
                if _s.get("groq_api_key") and hasattr(mod, "GROQ_API_KEY"):
                    mod.GROQ_API_KEY = _s["groq_api_key"]
                if _s.get("groq_model") and hasattr(mod, "GROQ_MODEL"):
                    mod.GROQ_MODEL = _s["groq_model"]
            if err:
                QTimer.singleShot(0, lambda e=err: (
                    self.groq_lbl.setText(f"○ SAGE.PY ERR"),
                    self.groq_lbl.setStyleSheet(f"color:{RED};font-size:8px;letter-spacing:1px;padding:0 18px 8px 18px;")))
                return
            try:
                ok, _, errmsg = mod.check_groq()
                if ok:
                    QTimer.singleShot(0, lambda: (
                        self.groq_lbl.setText("● GROQ CONNECTED"),
                        self.groq_lbl.setStyleSheet(f"font-family:{FONT_UI};font-size:8px;letter-spacing:1.5px;color:#236050;border:none;")))
                else:
                    msg = (errmsg or "")[:35]
                    QTimer.singleShot(0, lambda m=msg: (
                        self.groq_lbl.setText(f"○ GROQ ERR"),
                        self.groq_lbl.setStyleSheet(f"font-family:{FONT_UI};font-size:8px;letter-spacing:1.5px;color:{RED};border:none;")))
            except Exception as exc:
                msg = str(exc)[:30]
                QTimer.singleShot(0, lambda m=msg: self.groq_lbl.setText(f"○ ERROR"))

        threading.Thread(target=_check, daemon=True).start()

    def _run(self, mode):
        self._stack.setCurrentIndex(0)
        self.explain_row.setVisible(mode == "explain")
        self.chapter_row.setVisible(mode in ("chapter","chapter_go"))
        _tab = "analyse" if mode in ("explain","chapter","priority","profile","explain_go","chapter_go","chapter_summary") else "discover"
        self._switch_tab(_tab)
        _active_key = {"explain_go":"explain","chapter_go":"chapter","chapter_summary":"chapter"}.get(mode, mode)
        if _active_key in self._nav_btns:
            self._set_nav_active(_active_key)

        titles = {"novels":"Novel Recommendations","shows":"Show & Anime Recommendations",
            "similar":"More of What You Love","mood_light":"Light & Fun Picks",
            "mood_heavy":"Intense & Deep Picks","whats_next":"What's Next For You",
            "quick":"Quick Pick","explain":"Would I Like This?","priority":"Watchlist Priority",
            "profile":"Your Taste Profile","chapter":"Chapter Summary"}
        self.out_title.setText(titles.get(mode, "Sage"))

        # Track mode for refresh — only show button for generative modes
        refreshable = {"novels","shows","similar","mood_light","mood_heavy",
                       "whats_next","quick","priority","profile"}
        self._current_mode  = mode
        self._current_extra = ""
        self._refresh_btn.setVisible(mode in refreshable)

        if mode == "explain":
            self.out_area.setPlainText("Enter a title above and click Analyse."); return
        if mode == "chapter":
            # Populate book picker with known books
            self.chapter_input.clear()
            ld = legion_data()
            for name in sorted(ld.get("books", {}).keys()):
                self.chapter_input.addItem(name)
            self.out_area.setPlainText("Select or type a book title above and click Summarise."); return
        if mode == "explain_go":
            extra = self.explain_input.text().strip()
            if not extra: return
            self.out_title.setText("Would I Like This?")
            self._current_mode = "explain"; self._current_extra = extra
            self._refresh_btn.setVisible(True)
            self._start_worker("explain", extra=extra); return
        if mode == "chapter_go":
            extra = self.chapter_input.currentText().strip()
            if not extra: return
            self.out_title.setText("Chapter Summary")
            self._current_mode = "chapter_summary"; self._current_extra = extra
            self._refresh_btn.setVisible(True)
            self._start_worker("chapter_summary", extra=extra); return
        self._start_worker(mode)

    def _on_refresh(self):
        """Re-run the current mode to get fresh recommendations."""
        if not hasattr(self, "_current_mode") or not self._current_mode: return
        self._refresh_btn.setText("↻  REFRESHING...")
        self._refresh_btn.setEnabled(False)
        self._start_worker(self._current_mode, extra=getattr(self, "_current_extra", ""))

    def _start_worker(self, mode, user_msg="", extra=""):
        self.out_area.setPlainText("Sage is thinking...")
        self.spin.setVisible(True); self.add_wl_btn.setVisible(False); self._last_response = ""
        if self._worker and self._worker.isRunning():
            self._worker._stop = True
            self._worker.wait(500)
        self._worker = SageWorker(mode, user_msg=user_msg, extra=extra)
        self._worker.chunk_ready.connect(self._on_chunk)
        self._worker.done.connect(self._sage_done)
        self._worker.error.connect(self._sage_error)
        self._worker.start()

    def _on_chunk(self, chunk):
        if self.out_area.toPlainText() == "Sage is thinking...":
            self.out_area.clear()
        self.out_area.moveCursor(QTextCursor.MoveOperation.End)
        self.out_area.insertPlainText(chunk)
        self.spin.setVisible(False)

    @staticmethod
    def _strip_md(text: str) -> str:
        """Strip markdown formatting for plain-text display."""
        t = re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', text)   # **bold** / *italic*
        t = re.sub(r'^#{1,6}\s+', '', t, flags=re.M)      # ## headers
        t = re.sub(r'`+([^`]*)`+', r'\1', t)               # `code`
        t = re.sub(r'^\s*[-*]\s+', '  • ', t, flags=re.M) # - bullets → •
        t = re.sub(r'\n{3,}', '\n\n', t)                   # collapse blank lines
        return t.strip()

    def _speak_sage(self, text: str):
        """Speak Sage output using Piper TTS if enabled in settings."""
        try:
            md = matrix_data()
            s  = md.get("settings", {})
            if not s.get("sage_voice"): return
            piper  = s.get("piper_binary", "").strip()
            model  = s.get("piper_model", "").strip()
            if not piper or not model: return
            # Truncate to first 600 chars for voice (keep it concise)
            speak_text = self._strip_md(text)[:600]
            def _run():
                wav = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                        wav = tf.name
                    result = subprocess.run(
                        [piper, "--model", model, "--output_file", wav],
                        input=speak_text.encode(), capture_output=True, timeout=30)
                    if result.returncode == 0 and os.path.exists(wav):
                        proc = subprocess.Popen(["aplay", wav],
                                                stdout=subprocess.DEVNULL,
                                                stderr=subprocess.DEVNULL)
                        proc.wait()   # wait for aplay to finish before deleting
                except Exception:
                    pass
                finally:
                    try:
                        if wav and os.path.exists(wav):
                            os.unlink(wav)
                    except Exception:
                        pass
            threading.Thread(target=_run, daemon=True).start()
        except Exception:
            pass

    def _sage_done(self, text):
        self.spin.setVisible(False)
        self._last_response = text
        self.out_area.setPlainText(self._strip_md(text))
        self.out_area.moveCursor(QTextCursor.MoveOperation.Start)
        self._speak_sage(text)
        self.add_wl_btn.setVisible(True)
        self._refresh_btn.setText("↻  REFRESH")
        self._refresh_btn.setEnabled(True)
        # Show trailer button for recommendation modes
        show_trailer = self._current_mode in {
            "novels","shows","similar","mood_light","mood_heavy",
            "whats_next","quick","explain"
        }
        self.trailer_btn.setVisible(show_trailer and WEBENGINE_OK)
        # Push quick pick result to Watchface if it's open
        if self._current_mode == "quick":
            try:
                mw = self.window()
                if hasattr(mw, "_watchface") and mw._watchface and mw._watchface.isVisible():
                    mw._watchface.update_sage_pick(text)
            except Exception:
                pass

    def _sage_watch_trailer(self):
        """Extract all recommended titles from Sage output and let user pick one."""
        if not self._last_response: return
        text = self._last_response

        # Extract all titles from numbered list e.g. "1. **Title** —"
        titles = []
        for m in re.finditer(r"^\d+[.)]\s+\*{0,2}(.+?)\*{0,2}\s*(?:[-\u2014(]|$)", text, re.M):
            t = m.group(1).strip().rstrip("*- ")
            if t and len(t) > 1:
                titles.append(t)

        # Fallback: all bold **Title** instances
        if not titles:
            for m in re.finditer(r"\*{1,2}(.+?)\*{1,2}", text):
                t = m.group(1).strip()
                if t and len(t) > 1:
                    titles.append(t)

        # Last fallback: first line
        if not titles:
            first = text.split("\n")[0].strip()[:60]
            if first:
                titles.append(first)

        if not titles: return

        if len(titles) == 1:
            # Only one title — open directly
            TrailerDialog(titles[0], parent=self).exec()
            return

        # Multiple titles — show picker dialog
        dlg = QDialog(self)
        dlg.setWindowTitle("Watch a Trailer")
        dlg.setModal(True)
        dlg.setFixedWidth(400)
        dlg.setStyleSheet(f"background:{BG}; color:{TEXT};")

        # Center on parent window
        pg = self.window().geometry()
        dlg.move(pg.x() + (pg.width() - 400) // 2,
                 pg.y() + (pg.height() - 100) // 2)

        v = QVBoxLayout(dlg)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(10)
        hdr = QLabel("Which title would you like a trailer for?")
        hdr.setStyleSheet(f"color:{TEXT}; font-size:13px;")
        hdr.setWordWrap(True)
        v.addWidget(hdr)
        v.addSpacing(4)

        def _open(title):
            dlg.accept()
            TrailerDialog(title, parent=self).exec()

        for title in titles:
            b = QPushButton(title)
            b.setStyleSheet(
                f"background:{BG2}; border:1px solid {BORDER}; color:{TEXT};"
                f"font-size:12px; padding:10px 14px; border-radius:3px; text-align:left;")
            b.clicked.connect(lambda checked=False, t=title: _open(t))
            v.addWidget(b)

        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(
            f"background:transparent; border:none; color:{MUTED}; font-size:10px; padding:6px;")
        cancel.clicked.connect(dlg.reject)
        v.addSpacing(4)
        v.addWidget(cancel)
        dlg.exec()

    def _sage_error(self, msg):
        self.spin.setVisible(False)
        self._refresh_btn.setText("↻  REFRESH")
        self._refresh_btn.setEnabled(True)
        self.out_area.setPlainText(
            f"Error: {msg}\n\n"
            "Check:\n- Your Groq API key is set in .env\n"
            "- You have internet access\n"
            "- sage.py is in the same folder as this file")

    def _add_rec_to_wl(self):
        if self._last_response:
            dlg = AddToWLDialog(self._last_response, self)
            dlg.exec()

    def _open_chat(self): self._stack.setCurrentIndex(1)

    def _send_chat(self):
        msg = self.chat_input.text().strip()
        if not msg: return
        self.chat_input.clear()
        self.chat_area.append(f'<span style="color:{ACCENT};font-weight:bold;">You:</span>  {msg}<br>')
        self.chat_typing.setText("Sage is thinking...")
        if self._worker and self._worker.isRunning():
            self._worker._stop = True
            self._worker.wait(500)
        # Pass a copy of history so the worker has full context
        self._worker = SageWorker("chat", user_msg=msg, history=list(self._chat_history))
        self._worker.done.connect(lambda text, m=msg: self._chat_done(text, m))
        self._worker.error.connect(lambda e: (
            self.chat_typing.setText(""),
            self.chat_area.append(f'<span style="color:{RED};">Error: {e}</span><br>')))
        self._worker.start()

    def _chat_done(self, text, user_msg):
        self.chat_typing.setText("")
        self.chat_area.append(f'<span style="color:{NEON};font-weight:bold;">Sage:</span>  {text}<br>')
        # chat_with_sage() expects history as list of (role, content) TUPLES
        self._chat_history.append(("user",      user_msg))
        self._chat_history.append(("assistant", text))
        if len(self._chat_history) > 20:
            self._chat_history = self._chat_history[-20:]


class SettingsPage(QWidget):
    def __init__(self):
        super().__init__(); self._build()

    def _build(self):
        # ── Outer layout ─────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header bar ───────────────────────────────────────────────────────
        header_w = QWidget()
        header_w.setFixedHeight(52)
        import types as _ht
        from PyQt6.QtGui import QPainter as _hP, QLinearGradient as _hG, QColor as _hC, QBrush as _hB, QPen as _hPen
        def _hdr_paint(self_w, event):
            p = _hP(self_w)
            p.setRenderHint(_hP.RenderHint.Antialiasing)
            W, H = self_w.width(), self_w.height()
            g = _hG(0, 0, W, 0)
            g.setColorAt(0, _hC(BG2).lighter(118)); g.setColorAt(0.4, _hC(BG2)); g.setColorAt(1.0, _hC(BG2).lighter(108))
            p.fillRect(0, 0, W, H, _hB(g))
            sep = _hG(0, 0, W, 0)
            sep.setColorAt(0, _hC(ACCENT).darker(400)); sep.setColorAt(0.15, _hC(ACCENT).darker(180))
            sep.setColorAt(0.85, _hC(ACCENT).darker(180)); sep.setColorAt(1.0, _hC(ACCENT).darker(400))
            p.setPen(_hPen(_hB(sep), 1)); p.drawLine(0, H - 1, W, H - 1); p.end()
        header_w.paintEvent = _ht.MethodType(_hdr_paint, header_w)
        hv = QHBoxLayout(header_w)
        hv.setContentsMargins(28, 0, 28, 0)
        back_b3 = QPushButton("← HOME")
        back_b3.setStyleSheet(
            f"background:transparent;border:none;color:{MUTED};"
            f"font-size:9px;letter-spacing:1.5px;padding:4px 0;")
        back_b3.clicked.connect(lambda: self.window()._navigate("dashboard"))
        tl = QLabel("SETTINGS")
        tl.setStyleSheet(
            f"font-family:{FONT_DISPLAY};font-size:13px;font-weight:bold;"
            f"color:{ACCENT};letter-spacing:4px;margin-left:14px;")
        hv.addWidget(back_b3); hv.addWidget(tl); hv.addStretch()
        root.addWidget(header_w)

        # ── Body: vertical nav + content panels ──────────────────────────────
        body = QWidget()
        body.setStyleSheet(f"background:{BG};")
        body_h = QHBoxLayout(body)
        body_h.setContentsMargins(0, 0, 0, 0)
        body_h.setSpacing(0)
        root.addWidget(body, 1)

        # Left nav rail
        nav_rail = QWidget()
        nav_rail.setFixedWidth(168)
        nav_rail.setStyleSheet(
            f"background:#08080f; border-right:1px solid #1a1a28;")
        nav_v = QVBoxLayout(nav_rail)
        nav_v.setContentsMargins(0, 20, 0, 20)
        nav_v.setSpacing(2)
        body_h.addWidget(nav_rail)

        # Right stacked panels
        self._settings_stack = QStackedWidget()
        self._settings_stack.setStyleSheet(f"background:{BG};")
        body_h.addWidget(self._settings_stack, 1)

        # Nav button style helpers
        _nav_btn_base = (
            f"QPushButton{{background:transparent;border:none;"
            f"border-left:2px solid transparent;"
            f"font-family:{FONT_UI};font-size:9px;letter-spacing:2px;"
            f"color:#505068;text-align:left;padding:10px 20px;}}"
            f"QPushButton:hover{{background:#0f0f18;color:#9898b8;"
            f"border-left:2px solid #303048;}}"
        )
        _nav_btn_active = (
            f"QPushButton{{background:#0f0f18;border:none;"
            f"border-left:2px solid {ACCENT};"
            f"font-family:{FONT_UI};font-size:9px;letter-spacing:2px;"
            f"color:{ACCENT};text-align:left;padding:10px 20px;}}"
        )
        self._nav_buttons = {}
        self._nav_btn_base_ss   = _nav_btn_base
        self._nav_btn_active_ss = _nav_btn_active

        def _make_nav_btn(label, index):
            b = QPushButton(label)
            b.setStyleSheet(_nav_btn_base if index != 0 else _nav_btn_active)
            b.clicked.connect(lambda _, i=index: self._switch_settings_panel(i))
            nav_v.addWidget(b)
            self._nav_buttons[index] = b

        _make_nav_btn("API KEYS",   0)
        _make_nav_btn("PATHS",      1)
        _make_nav_btn("VOICE",      2)
        _make_nav_btn("COMPANION",  3)
        _make_nav_btn("CLOUD SYNC", 4)
        nav_v.addStretch()

        # Shared field/label style helpers
        def _section_lbl(text):
            l = QLabel(text)
            l.setStyleSheet(
                f"color:#505068;font-size:9px;letter-spacing:3px;"
                f"font-family:{FONT_UI};background:transparent;")
            return l

        def _divider():
            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setStyleSheet(f"color:#1a1a28;background:#1a1a28;max-height:1px;")
            return line

        def _field_label(text):
            l = QLabel(text)
            l.setStyleSheet(
                f"color:#6868a0;font-size:9px;letter-spacing:1px;"
                f"font-family:{FONT_UI};background:transparent;")
            return l

        def _hint(text):
            l = QLabel(text)
            l.setStyleSheet(f"color:#404058;font-size:10px;background:transparent;")
            l.setWordWrap(True)
            return l

        def _panel_scroll():
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setStyleSheet(
                f"QScrollArea{{background:{BG};border:none;}}"
                f"QScrollBar:vertical{{background:#0d0d14;width:6px;border-radius:3px;}}"
                f"QScrollBar::handle:vertical{{background:#2a2a3a;border-radius:3px;}}"
                f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}")
            inner = QWidget()
            inner.setStyleSheet(f"background:{BG};")
            iv = QVBoxLayout(inner)
            iv.setContentsMargins(36, 28, 36, 28)
            iv.setSpacing(14)
            scroll.setWidget(inner)
            return scroll, iv

        def _save_btn():
            b = QPushButton("SAVE SETTINGS")
            b.setStyleSheet(
                f"QPushButton{{background:{ACCENT};color:#0d0d14;border:none;"
                f"border-radius:4px;font-family:{FONT_UI};font-size:9px;"
                f"letter-spacing:2px;font-weight:bold;padding:9px 28px;}}"
                f"QPushButton:hover{{background:#dbb85c;}}")
            b.clicked.connect(self._save)
            return b

        # ── Panel 0: API Keys ─────────────────────────────────────────────────
        p0_scroll, p0 = _panel_scroll()
        p0.addWidget(_section_lbl("GROQ API  —  USED BY SAGE"))
        gf0 = QFormLayout()
        gf0.setSpacing(10)
        gf0.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("gsk_...")
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("llama-3.3-70b-versatile")
        for field in (self.key_edit, self.model_edit):
            field.setStyleSheet(
                f"QLineEdit{{background:#1a1a28;border:1px solid #2a2a3a;"
                f"border-radius:4px;color:{TEXT};font-size:12px;padding:7px 10px;}}"
                f"QLineEdit:focus{{border-color:{ACCENT}44;}}")
        gf0.addRow(_field_label("API KEY"), self.key_edit)
        gf0.addRow(_field_label("MODEL"),   self.model_edit)
        p0.addLayout(gf0)
        p0.addWidget(_divider())
        p0.addWidget(_section_lbl("TMDB API  —  SHOW & MOVIE METADATA"))
        gf1 = QFormLayout()
        gf1.setSpacing(10)
        gf1.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.tmdb_key_edit = QLineEdit()
        self.tmdb_key_edit.setPlaceholderText("Get free key at themoviedb.org/settings/api")
        self.tmdb_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.tmdb_key_edit.setStyleSheet(
            f"QLineEdit{{background:#1a1a28;border:1px solid #2a2a3a;"
            f"border-radius:4px;color:{TEXT};font-size:12px;padding:7px 10px;}}"
            f"QLineEdit:focus{{border-color:{ACCENT}44;}}")
        gf1.addRow(_field_label("API KEY"), self.tmdb_key_edit)
        p0.addLayout(gf1)
        p0.addWidget(_hint("Free tier is sufficient. Without a key, metadata fallback is disabled."))
        p0.addSpacing(8)
        p0.addWidget(_save_btn())
        p0.addStretch()
        self._settings_stack.addWidget(p0_scroll)

        # ── Panel 1: Paths ────────────────────────────────────────────────────
        p1_scroll, p1 = _panel_scroll()
        p1.addWidget(_section_lbl("DIRECTORIES"))
        dl_row = QHBoxLayout()
        self.dl_edit = QLineEdit(os.path.expanduser("~/Videos"))
        self.dl_edit.setStyleSheet(
            f"QLineEdit{{background:#1a1a28;border:1px solid #2a2a3a;"
            f"border-radius:4px;color:{TEXT};font-size:12px;padding:7px 10px;}}"
            f"QLineEdit:focus{{border-color:{ACCENT}44;}}")
        browse_b = QPushButton("BROWSE")
        browse_b.setStyleSheet(
            f"QPushButton{{background:#1a1a28;border:1px solid #2a2a3a;"
            f"border-radius:4px;color:#6868a0;font-family:{FONT_UI};"
            f"font-size:9px;letter-spacing:1px;padding:7px 14px;}}"
            f"QPushButton:hover{{border-color:{ACCENT}44;color:{ACCENT};}}")
        browse_b.clicked.connect(lambda: self.dl_edit.setText(
            QFileDialog.getExistingDirectory(self, "Download Dir") or self.dl_edit.text()))
        dl_row.addWidget(self.dl_edit, 1)
        dl_row.addWidget(browse_b)
        gf2 = QFormLayout()
        gf2.setSpacing(10)
        gf2.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        gf2.addRow(_field_label("VIDEO DOWNLOAD DIR"), dl_row)
        p1.addLayout(gf2)
        p1.addWidget(_divider())
        p1.addWidget(_section_lbl("DATA FILE LOCATIONS  —  READ ONLY"))
        for lab, path in [
            ("Legion progress",  LEGION_PROGRESS),
            ("Matrix progress",  MATRIX_PROGRESS),
            ("Legion bookmarks", LEGION_BOOKMARKS),
            ("Sage memory",      SAGE_MEMORY_PATH),
        ]:
            row_w = QWidget(); row_w.setStyleSheet("background:transparent;")
            row_v = QVBoxLayout(row_w); row_v.setContentsMargins(0,4,0,4); row_v.setSpacing(2)
            lbl_top = QLabel(lab)
            lbl_top.setStyleSheet(f"color:#6868a0;font-size:9px;letter-spacing:1px;background:transparent;")
            lbl_path = QLabel(path)
            lbl_path.setStyleSheet(f"color:#7878a8;font-size:11px;background:transparent;")
            lbl_path.setWordWrap(True)
            row_v.addWidget(lbl_top); row_v.addWidget(lbl_path)
            p1.addWidget(row_w)
        p1.addSpacing(8)
        p1.addWidget(_save_btn())
        p1.addStretch()
        self._settings_stack.addWidget(p1_scroll)

        # ── Panel 2: Voice ────────────────────────────────────────────────────
        p2_scroll, p2 = _panel_scroll()
        p2.addWidget(_section_lbl("SAGE VOICE  —  PIPER TTS"))
        self.voice_chk = QCheckBox("Enable Sage Voice — read responses aloud")
        self.voice_chk.setStyleSheet(
            f"QCheckBox{{color:{TEXT};font-size:12px;background:transparent;}}"
            f"QCheckBox::indicator{{width:14px;height:14px;}}")
        p2.addWidget(self.voice_chk)
        gf3 = QFormLayout()
        gf3.setSpacing(10)
        gf3.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.piper_edit = QLineEdit()
        self.piper_edit.setPlaceholderText("Path to piper binary  (e.g. /usr/local/bin/piper)")
        self.model_voice_edit = QLineEdit()
        self.model_voice_edit.setPlaceholderText("Path to .onnx voice model  (e.g. ~/piper/en_US-amy-medium.onnx)")
        for field in (self.piper_edit, self.model_voice_edit):
            field.setStyleSheet(
                f"QLineEdit{{background:#1a1a28;border:1px solid #2a2a3a;"
                f"border-radius:4px;color:{TEXT};font-size:12px;padding:7px 10px;}}"
                f"QLineEdit:focus{{border-color:{ACCENT}44;}}")
        gf3.addRow(_field_label("PIPER BINARY"), self.piper_edit)
        gf3.addRow(_field_label("VOICE MODEL"),  self.model_voice_edit)
        p2.addLayout(gf3)
        p2.addWidget(_hint("Install: pip install piper-tts  or  https://github.com/rhasspy/piper"))
        p2.addSpacing(8)
        p2.addWidget(_save_btn())
        p2.addStretch()
        self._settings_stack.addWidget(p2_scroll)

        # ── Panel 3: Companion ────────────────────────────────────────────────
        p3_scroll, p3 = _panel_scroll()
        p3.addWidget(_section_lbl("MOBILE COMPANION"))
        import socket as _sock
        try:
            s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80)); local_ip = s.getsockname()[0]; s.close()
        except Exception:
            local_ip = "localhost"
        url = f"http://{local_ip}:{_mobile_server_port}"
        open_lbl = QLabel("Open on your phone:")
        open_lbl.setStyleSheet(f"color:{TEXT2};font-size:12px;background:transparent;")
        url_lbl = QLabel(f"<b style='color:{NEON};'>{url}</b>")
        url_lbl.setStyleSheet(f"font-size:14px;background:transparent;")
        url_lbl.setTextFormat(Qt.TextFormat.RichText)
        wifi_lbl = QLabel("Make sure your phone is on the same Wi-Fi network.")
        wifi_lbl.setStyleSheet(f"color:{MUTED};font-size:11px;background:transparent;")
        p3.addWidget(open_lbl)
        p3.addWidget(url_lbl)
        p3.addWidget(wifi_lbl)
        p3.addStretch()
        self._settings_stack.addWidget(p3_scroll)

        # ── Panel 4: Cloud Sync ───────────────────────────────────────────────
        p4_scroll, p4 = _panel_scroll()

        # Status banner
        self._sync_status_lbl = QLabel("NOT SIGNED IN")
        self._sync_status_lbl.setStyleSheet(
            f"color:#505068;font-size:9px;letter-spacing:3px;"
            f"font-family:{FONT_UI};background:transparent;")
        self._sync_user_lbl = QLabel("")
        self._sync_user_lbl.setStyleSheet(
            f"color:{ACCENT};font-size:12px;background:transparent;")

        p4.addWidget(_section_lbl("GREAT SAGE CLOUD  —  BACKUP & SYNC"))
        p4.addWidget(self._sync_status_lbl)
        p4.addWidget(self._sync_user_lbl)
        p4.addWidget(_divider())

        # Login form
        self._sync_login_widget = QWidget()
        self._sync_login_widget.setStyleSheet("background:transparent;")
        lv = QVBoxLayout(self._sync_login_widget)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(10)

        lv.addWidget(_section_lbl("SIGN IN TO YOUR ACCOUNT"))
        gf_sync = QFormLayout()
        gf_sync.setSpacing(10)
        gf_sync.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        _field_ss = (
            f"QLineEdit{{background:#1a1a28;border:1px solid #2a2a3a;"
            f"border-radius:4px;color:{TEXT};font-size:12px;padding:7px 10px;}}"
            f"QLineEdit:focus{{border-color:{ACCENT}44;}}"
        )
        self._sync_email_edit = QLineEdit()
        self._sync_email_edit.setPlaceholderText("your@email.com")
        self._sync_email_edit.setStyleSheet(_field_ss)

        self._sync_pass_edit = QLineEdit()
        self._sync_pass_edit.setPlaceholderText("Password")
        self._sync_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._sync_pass_edit.setStyleSheet(_field_ss)

        gf_sync.addRow(_field_label("EMAIL"),    self._sync_email_edit)
        gf_sync.addRow(_field_label("PASSWORD"), self._sync_pass_edit)
        lv.addLayout(gf_sync)

        self._sync_msg_lbl = QLabel("")
        self._sync_msg_lbl.setStyleSheet(
            f"color:#e06c6c;font-size:11px;background:transparent;")
        self._sync_msg_lbl.setWordWrap(True)
        lv.addWidget(self._sync_msg_lbl)

        _btn_ss = (
            f"QPushButton{{background:{ACCENT};color:#0d0d14;border:none;"
            f"border-radius:4px;font-family:{FONT_UI};font-size:9px;"
            f"letter-spacing:2px;font-weight:bold;padding:9px 28px;}}"
            f"QPushButton:hover{{background:#dbb85c;}}"
            f"QPushButton:disabled{{background:#2a2a3a;color:#505068;}}"
        )
        _btn_ghost_ss = (
            f"QPushButton{{background:transparent;border:1px solid #2a2a3a;"
            f"border-radius:4px;font-family:{FONT_UI};font-size:9px;"
            f"letter-spacing:2px;color:#6868a0;padding:9px 28px;}}"
            f"QPushButton:hover{{border-color:{ACCENT}44;color:{ACCENT};}}"
        )

        login_btn = QPushButton("SIGN IN")
        login_btn.setStyleSheet(_btn_ss)
        login_btn.clicked.connect(self._sync_login)

        lv.addWidget(login_btn)
        lv.addWidget(_hint(
            "Sign in to backup your watchlist and reading progress to the cloud. "
            "On a fresh install, sign in here and hit Restore to get everything back."
        ))
        lv.addWidget(_divider())
        lv.addWidget(_section_lbl("NO ACCOUNT YET?"))
        signup_link = QLabel(
            "<a href='https://entertainment-app-we-7xnp.bolt.host' "
            f"style='color:{ACCENT};'>Create your account on TrackFlix</a>"
            " — then come back here to sign in."
        )
        signup_link.setStyleSheet(f"color:#404058;font-size:10px;background:transparent;")
        signup_link.setWordWrap(True)
        signup_link.setOpenExternalLinks(True)
        signup_link.setTextFormat(Qt.TextFormat.RichText)
        lv.addWidget(signup_link)
        p4.addWidget(self._sync_login_widget)

        # Logged-in actions widget (hidden until signed in)
        self._sync_actions_widget = QWidget()
        self._sync_actions_widget.setStyleSheet("background:transparent;")
        av = QVBoxLayout(self._sync_actions_widget)
        av.setContentsMargins(0, 0, 0, 0)
        av.setSpacing(12)

        av.addWidget(_section_lbl("SYNC ACTIONS"))

        push_btn = QPushButton("⬆  BACKUP TO CLOUD")
        push_btn.setStyleSheet(_btn_ss)
        push_btn.setToolTip("Push your current watchlist and progress to the cloud")
        push_btn.clicked.connect(self._sync_push)

        pull_btn = QPushButton("⬇  RESTORE FROM CLOUD")
        pull_btn.setStyleSheet(_btn_ghost_ss)
        pull_btn.setToolTip("Overwrite local data with cloud backup (use after fresh install)")
        pull_btn.clicked.connect(self._sync_pull)

        logout_btn = QPushButton("SIGN OUT")
        logout_btn.setStyleSheet(_btn_ghost_ss)
        logout_btn.clicked.connect(self._sync_logout)

        self._sync_action_msg = QLabel("")
        self._sync_action_msg.setStyleSheet(
            f"color:#6ca86c;font-size:11px;background:transparent;")
        self._sync_action_msg.setWordWrap(True)

        trackflix_btn = QPushButton("🌐  OPEN TRACKFLIX")
        trackflix_btn.setStyleSheet(_btn_ghost_ss)
        trackflix_btn.setToolTip("Open TrackFlix in your browser")
        trackflix_btn.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://entertainment-app-we-7xnp.bolt.host")))

        av.addWidget(push_btn)
        av.addWidget(pull_btn)
        av.addWidget(self._sync_action_msg)
        av.addWidget(_divider())
        av.addWidget(trackflix_btn)
        av.addWidget(_divider())
        av.addWidget(logout_btn)

        p4.addWidget(self._sync_actions_widget)
        self._sync_actions_widget.hide()

        p4.addStretch()
        self._settings_stack.addWidget(p4_scroll)

        self._load()
        self._sync_refresh_ui()

    def _switch_settings_panel(self, index: int):
        self._settings_stack.setCurrentIndex(index)
        for i, b in self._nav_buttons.items():
            b.setStyleSheet(
                self._nav_btn_active_ss if i == index else self._nav_btn_base_ss)

    def _load(self):
        md       = matrix_data()
        settings = md.get("settings", {})
        saved_key   = settings.get("groq_api_key", "")
        saved_model = settings.get("groq_model", "")
        if not saved_key or not saved_model:
            sage_path = SCRIPT_DIR/"sage.py"
            if sage_path.exists():
                try:
                    for line in sage_path.read_text().splitlines():
                        if not saved_key and "GROQ_API_KEY" in line and "=" in line and not line.strip().startswith("#"):
                            v = line.split("=",1)[1].strip().strip('"').strip("'").split("#")[0].strip()
                            if v and "your-api-key" not in v: saved_key = v
                        if not saved_model and line.strip().startswith("GROQ_MODEL") and "=" in line:
                            v = line.split("=",1)[1].strip().strip('"').strip("'").split("#")[0].strip()
                            if v and v != "active_model": saved_model = v
                except Exception: pass  # Ignored
        if saved_key:   self.key_edit.setText(saved_key)
        if saved_model: self.model_edit.setText(saved_model)
        else:           self.model_edit.setText("llama-3.3-70b-versatile")
        self.tmdb_key_edit.setText(settings.get("tmdb_api_key", ""))
        self.dl_edit.setText(settings.get("download_dir", os.path.expanduser("~/Videos")))
        self.voice_chk.setChecked(settings.get("sage_voice", False))
        self.piper_edit.setText(settings.get("piper_binary", ""))
        self.model_voice_edit.setText(settings.get("piper_model", ""))

    def _save(self):
        new_key   = self.key_edit.text().strip()
        new_model = self.model_edit.text().strip() or "llama-3.3-70b-versatile"
        # Save to JSON — persistent across restarts
        md = matrix_data()
        md.setdefault("settings", {})
        if new_key:   md["settings"]["groq_api_key"] = new_key
        if new_model: md["settings"]["groq_model"]   = new_model
        new_tmdb_key = self.tmdb_key_edit.text().strip()
        if new_tmdb_key:
            md["settings"]["tmdb_api_key"] = new_tmdb_key
        md["settings"]["download_dir"]  = self.dl_edit.text()
        md["settings"]["sage_voice"]    = self.voice_chk.isChecked()
        md["settings"]["piper_binary"]  = self.piper_edit.text().strip()
        md["settings"]["piper_model"]   = self.model_voice_edit.text().strip()
        save_json(MATRIX_PROGRESS, md)
        # Also patch sage.py with robust regex (handles trailing comments)
        sage_path = SCRIPT_DIR/"sage.py"
        if new_key and sage_path.exists():
            try:
                txt = sage_path.read_text()
                txt = re.sub(r'GROQ_API_KEY\s*=\s*["\'][^"\']*["\'][^\n]*',
                             f'GROQ_API_KEY = "{new_key}"', txt)
                txt = re.sub(r'GROQ_MODEL\s*=\s*["\'][^"\']*["\'][^\n]*',
                             f'GROQ_MODEL = "{new_model}"', txt)
                sage_path.write_text(txt)
            except Exception: pass  # Ignored
        from great_sage_core import reload_module as _rm; _rm("sage")
        QMessageBox.information(self, "Saved", "Settings saved.")

    # ── Cloud Sync helpers ────────────────────────────────────────────────────

    def _get_sync(self):
        """Lazy-import GreatSageSync — returns None if gs_sync not available."""
        try:
            from gs_sync import GreatSageSync
            if not hasattr(self, "_sync_client"):
                self._sync_client = GreatSageSync()
            return self._sync_client
        except ImportError:
            return None

    def _sync_refresh_ui(self):
        sync = self._get_sync()
        if sync and sync.is_logged_in():
            profile = sync._get_profile()
            name = ""
            if profile:
                name = profile.get("display_name") or profile.get("username", "")
            self._sync_status_lbl.setText("SIGNED IN")
            self._sync_status_lbl.setStyleSheet(
                f"color:#6ca86c;font-size:9px;letter-spacing:3px;"
                f"font-family:{FONT_UI};background:transparent;")
            self._sync_user_lbl.setText(f"@{name}" if name else "")
            self._sync_login_widget.hide()
            self._sync_actions_widget.show()
            # Auto-push on every app launch + start periodic timer
            self._sync_push(silent=True)
            self._start_autosync_timer()
        else:
            self._sync_status_lbl.setText("NOT SIGNED IN")
            self._sync_status_lbl.setStyleSheet(
                f"color:#505068;font-size:9px;letter-spacing:3px;"
                f"font-family:{FONT_UI};background:transparent;")
            self._sync_user_lbl.setText("")
            self._sync_login_widget.show()
            self._sync_actions_widget.hide()

    def _sync_login(self):
        sync = self._get_sync()
        if not sync:
            self._sync_msg_lbl.setText("gs_sync.py not found in project directory.")
            return
        email    = self._sync_email_edit.text().strip()
        password = self._sync_pass_edit.text()
        if not email or not password:
            self._sync_msg_lbl.setText("Email and password are required.")
            return
        self._sync_msg_lbl.setText("Signing in…")
        QApplication.processEvents()
        try:
            sync.login(email, password)
            self._sync_msg_lbl.setText("")
            self._sync_pass_edit.clear()
            self._sync_refresh_ui()
            # Auto-push immediately after login
            self._sync_push(silent=True)
            # Start auto-sync timer (every 10 minutes)
            self._start_autosync_timer()
        except Exception as e:
            err = str(e)
            if "Invalid login" in err or "400" in err:
                self._sync_msg_lbl.setText("Invalid email or password.")
            else:
                self._sync_msg_lbl.setText(f"Login failed: {err[:80]}")

    def _sync_push(self, silent=False):
        sync = self._get_sync()
        if not sync:
            return
        if not silent:
            self._sync_action_msg.setStyleSheet(
                f"color:#6868a0;font-size:11px;background:transparent;")
            self._sync_action_msg.setText("Backing up…")
            QApplication.processEvents()
        try:
            ok = sync.push()
            if not silent:
                if ok:
                    self._sync_action_msg.setStyleSheet(
                        f"color:#6ca86c;font-size:11px;background:transparent;")
                    self._sync_action_msg.setText("✓ Backup complete.")
                else:
                    self._sync_action_msg.setStyleSheet(
                        f"color:#e06c6c;font-size:11px;background:transparent;")
                    self._sync_action_msg.setText("Backup failed — check logs.")
        except Exception as e:
            if not silent:
                self._sync_action_msg.setStyleSheet(
                    f"color:#e06c6c;font-size:11px;background:transparent;")
                self._sync_action_msg.setText(f"Error: {str(e)[:80]}")

    def _start_autosync_timer(self):
        """Auto-backup every 10 minutes while signed in."""
        if not hasattr(self, "_autosync_timer"):
            self._autosync_timer = QTimer(self)
            self._autosync_timer.timeout.connect(lambda: self._sync_push(silent=True))
        self._autosync_timer.start(10 * 60 * 1000)  # 10 minutes

    def _sync_pull(self):
        sync = self._get_sync()
        if not sync:
            return
        reply = QMessageBox.question(
            self, "Restore from Cloud",
            "This will overwrite your local watchlist with the cloud backup.\n"
            "Your local watching progress will be preserved.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._sync_action_msg.setStyleSheet(
            f"color:#6868a0;font-size:11px;background:transparent;")
        self._sync_action_msg.setText("Restoring…")
        QApplication.processEvents()
        try:
            ok = sync.restore_to_disk()
            if ok:
                self._sync_action_msg.setStyleSheet(
                    f"color:#6ca86c;font-size:11px;background:transparent;")
                self._sync_action_msg.setText(
                    "✓ Restored. Restart Great Sage to see changes.")
            else:
                self._sync_action_msg.setStyleSheet(
                    f"color:#e06c6c;font-size:11px;background:transparent;")
                self._sync_action_msg.setText("Restore failed — check logs.")
        except Exception as e:
            self._sync_action_msg.setStyleSheet(
                f"color:#e06c6c;font-size:11px;background:transparent;")
            self._sync_action_msg.setText(f"Error: {str(e)[:80]}")

    def _sync_logout(self):
        sync = self._get_sync()
        if sync:
            sync.logout()
        if hasattr(self, "_autosync_timer"):
            self._autosync_timer.stop()
        self._sync_refresh_ui()

    def refresh(self): self._load()

# ─── Ambient themes: colours tested for visibility on real monitors ────────────
# All bg_top values have luminance > 20 (above the "looks black" threshold of ~12)
# Orb alpha 210 ensures glow is clearly visible even on dim displays
