"""
gs_settings_ui.py — Great Sage
================================
Settings page and related dialogs (RecNotificationDialog).
Extracted from gs_sage_ui.py so that settings/sync logic has a dedicated home.

Imports by other modules
------------------------
- gs_sage_ui      : nothing (no longer needed)
- gs_widgets.py   : from gs_settings_ui import RecNotificationDialog
- gs_matrix_ui    : never imports this directly — hooks are wired at runtime
"""
import os, re, threading

try:
    from gs_logger import log
except Exception as _log_err:
    class _NoopLog:
        def __getattr__(self, name): return _NoopLog()
        def __call__(self, *a, **kw): return None
    log = _NoopLog()

from gs_theme import *
from gs_widgets import lbl, btn, hline, _mobile_server_port

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QUrl
from PyQt6.QtGui import (
    QColor, QPainter, QLinearGradient, QBrush, QPen,
    QDesktopServices,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QLabel, QPushButton, QLineEdit,
    QFrame, QComboBox, QCheckBox, QDialog, QFileDialog,
    QMessageBox, QScrollArea, QFormLayout,
)
import types as _types

from great_sage_core import (
    SCRIPT_DIR, LEGION_PROGRESS, MATRIX_PROGRESS, LEGION_BOOKMARKS, SAGE_MEMORY_PATH,
    load_json, save_json,
    matrix_data,
    reload_module,
)
from gs_notifications import push_notification, dismiss_notification, get_notification_store


# ═══════════════════════════════════════════════════════════════════════════════
# RecNotificationDialog
# ═══════════════════════════════════════════════════════════════════════════════

class RecNotificationDialog(QDialog):
    """Popup shown when a new recommendation arrives from a friend."""

    def __init__(self, rec: dict, parent=None):
        super().__init__(parent)
        self._rec    = rec
        self._result = None
        self.setWindowTitle("New Recommendation")
        self.setMinimumWidth(380)
        self.setStyleSheet(f"background:{BG};color:{TEXT};font-family:{FONT_UI};")

        lay = QVBoxLayout(self)
        lay.setSpacing(14)
        lay.setContentsMargins(24, 24, 24, 20)

        # Header
        from_lbl = QLabel(f"<b>{rec.get('sender', 'Someone')}</b> recommends:")
        from_lbl.setStyleSheet(f"font-size:12px;color:{TEXT2};background:transparent;")
        lay.addWidget(from_lbl)

        # Title
        title_lbl = QLabel(rec.get("title", ""))
        title_lbl.setStyleSheet(
            f"font-size:18px;font-weight:bold;color:{TEXT};background:transparent;")
        title_lbl.setWordWrap(True)
        lay.addWidget(title_lbl)

        # Type badge
        type_lbl = QLabel(rec.get("type", "Anime").upper())
        type_lbl.setStyleSheet(
            f"font-size:9px;letter-spacing:2px;color:{ACCENT};background:transparent;")
        lay.addWidget(type_lbl)

        # Message
        msg = rec.get("message", "").strip()
        if msg:
            msg_lbl = QLabel(f'"{msg}"')
            msg_lbl.setStyleSheet(
                f"font-size:12px;color:{TEXT2};font-style:italic;"
                f"background:transparent;")
            msg_lbl.setWordWrap(True)
            lay.addWidget(msg_lbl)

        lay.addSpacing(6)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        add_btn = QPushButton("Add to Watchlist")
        add_btn.setStyleSheet(
            f"background:{ACCENT};color:#fff;border:none;border-radius:6px;"
            f"padding:8px 18px;font-size:12px;font-family:{FONT_UI};")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._on_add)

        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.setStyleSheet(
            f"background:transparent;color:{TEXT2};border:1px solid #444;"
            f"border-radius:6px;padding:8px 18px;font-size:12px;font-family:{FONT_UI};")
        dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss_btn.clicked.connect(self._on_dismiss)

        btn_row.addWidget(add_btn)
        btn_row.addWidget(dismiss_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

    def _on_add(self):
        self._result = "add"
        self.accept()

    def _on_dismiss(self):
        self._result = "dismiss"
        self.reject()

    @property
    def result_action(self):
        return self._result


# ═══════════════════════════════════════════════════════════════════════════════
# SettingsPage
# ═══════════════════════════════════════════════════════════════════════════════

class SettingsPage(QWidget):
    # Emitted from the poll thread to show a rec dialog on the main thread.
    # QTimer.singleShot is not thread-safe in PyQt6; signals are.
    rec_received = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        # Route incoming recs to the notification store (bell) instead of
        # popping a dialog directly.  The panel's click handler opens the
        # dialog when the user chooses to act on the notification.
        self.rec_received.connect(self._store_rec_notification)
        self._build()
        # Register instant-push hooks so Matrix can trigger cloud operations
        # immediately when an item is added or removed, without any import coupling.
        try:
            import gs_matrix_ui as _mx
            _mx._sync_item_added   = self.sync_item_added
            _mx._sync_item_removed = self.sync_item_removed
        except Exception:
            pass

    def _build(self):
        # ── Outer layout ─────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header bar ───────────────────────────────────────────────────────
        header_w = QWidget()
        header_w.setFixedHeight(52)
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
        header_w.paintEvent = _types.MethodType(_hdr_paint, header_w)
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
                f"QScrollBar:vertical{{background:#0d0d14;width:8px;border-radius:4px;}}"
                f"QScrollBar::handle:vertical{{background:#2a2a3a;border-radius:4px;}}"
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
            "<a href='https://greatsag3.netlify.app' "
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
            QUrl("https://greatsag3.netlify.app")))

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
                except Exception: pass
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
            except Exception: pass
        reload_module("sage")
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
            # Push persistent bell notification — dedup + cooldown handled inside push_notification()
            push_notification(
                title="Cloud sync — not signed in",
                body="Open Settings → Cloud to sign in and enable sync.",
                notif_type="warning",
                notif_id="cloud_not_logged_in",
            )

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
            dismiss_notification("cloud_not_logged_in")
            self._sync_refresh_ui()
            self._sync_push(silent=True)
            self._start_autosync_timer()
        except Exception as e:
            err = str(e)
            if "Invalid login" in err or "400" in err:
                self._sync_msg_lbl.setText("Invalid email or password.")
            else:
                self._sync_msg_lbl.setText(f"Login failed: {err[:80]}")

    def _sync_push(self, silent=False):
        import logging as _logging
        _log = _logging.getLogger("great_sage.sync")
        sync = self._get_sync()
        if not sync:
            return
        if not silent:
            self._sync_action_msg.setStyleSheet(
                f"color:#6868a0;font-size:11px;background:transparent;")
            self._sync_action_msg.setText("Backing up…")
            QApplication.processEvents()

        def _do():
            try:
                ok = sync.push()
                if ok:
                    _log.info("[cloud] Push complete")
                else:
                    _log.warning("[cloud] Push returned False")
                if not silent:
                    msg = "✓ Backup complete." if ok else "Backup failed — check logs."
                    QTimer.singleShot(0, lambda: self._sync_action_msg.setText(msg))
            except Exception as e:
                _log.error(f"[cloud] Push error: {e}")
                if not silent:
                    QTimer.singleShot(0, lambda: self._sync_action_msg.setText(
                        f"Error: {str(e)[:80]}"))

        threading.Thread(target=_do, daemon=True).start()

    def _start_autosync_timer(self):
        """Sync cycle every 3 minutes: pull first (merge cloud changes) then push."""
        if not hasattr(self, "_autosync_timer"):
            self._autosync_timer = QTimer(self)
            self._autosync_timer.timeout.connect(self._sync_cycle)
        self._autosync_timer.start(3 * 60 * 1000)
        # Delay rec polling by 5s so the main window is fully visible before
        # any notification dialog tries to attach to it as a parent.
        QTimer.singleShot(5000, self._start_rec_polling)

    def _sync_cycle(self):
        """Pull cloud changes (last-write-wins merge) then push local state."""
        import logging as _logging
        _log = _logging.getLogger("great_sage.sync")
        sync = self._get_sync()
        if not sync:
            return

        def _do():
            auth_failed = False
            try:
                sync.restore_to_disk()
            except Exception as e:
                err = str(e)
                _log.warning(f"[cloud] Sync-cycle pull error: {e}")
                if "Not logged in" in err or "401" in err or "token" in err.lower():
                    auth_failed = True
            # ── Legion pull (mirror of restore_to_disk for webnovels) ─────────
            try:
                from gs_legion_sync import legion_restore_to_disk, drain_pending_pushes
                legion_restore_to_disk()
                drain_pending_pushes()
            except Exception as e:
                _log.warning(f"[cloud] Legion restore error: {e}")
            try:
                ok = sync.push()
                if ok:
                    _log.info("[cloud] Sync-cycle push complete")
                else:
                    _log.warning("[cloud] Sync-cycle push returned False")
            except Exception as e:
                err = str(e)
                _log.error(f"[cloud] Sync-cycle push error: {e}")
                if "Not logged in" in err or "401" in err or "token" in err.lower():
                    auth_failed = True
            # Legion progress is pushed in real time by gs_legion_sync.push_book /
            # push_reader_progress — no bulk backfill needed here. Running
            # backfill_library() on every cycle resets all webnovel progress to 0.

            if auth_failed:
                push_notification(
                    title="Cloud sync — session expired",
                    body="Your session expired. Open Settings → Cloud to sign back in.",
                    notif_type="warning",
                    notif_id="cloud_not_logged_in",
                )

        threading.Thread(target=_do, daemon=True, name="gs_sync_cycle").start()

    def _start_rec_polling(self):
        """Poll recommendations inbox every 2 minutes while signed in."""
        if getattr(self, "_rec_polling_started", False):
            return
        self._rec_polling_started = True

        import logging as _logging
        _log = _logging.getLogger("great_sage.sync")
        sync = self._get_sync()
        if not sync:
            self._rec_polling_started = False
            return

        def _on_recs(recs):
            for rec in recs:
                _log.info(f"[cloud] New recommendation: {rec.get('title')} from {rec.get('sender')}")
                self.rec_received.emit(rec)

        sync.start_polling(interval=120, callback=_on_recs)

    def sync_item_added(self, title: str, media_type: str, status: str = "Planning",
                        episode: int = 0, notes: str = "", rating: int = 0,
                        cover_url: str = "") -> None:
        """
        Immediately push a single newly-added watchlist item to the cloud.
        Called from gs_matrix_ui via the _sync_item_added hook — no import
        coupling needed between MatrixPage and SettingsPage.
        """
        import logging as _logging
        _log = _logging.getLogger("great_sage.sync")
        sync = self._get_sync()
        if not sync or not sync.is_logged_in():
            return

        def _do():
            ok = sync.push_single(title, media_type, status,
                                  episode=episode, notes=notes,
                                  rating=rating, cover_url=cover_url)
            if ok:
                _log.info(f"[cloud] Instant push: '{title}' → {status}")
            else:
                _log.warning(f"[cloud] Instant push failed for '{title}'")

        threading.Thread(target=_do, daemon=True, name="gs_sync_add").start()

    def sync_item_removed(self, title: str) -> None:
        """
        Immediately delete a watchlist item from the cloud.
        Called from gs_matrix_ui via the _sync_item_removed hook — no import
        coupling needed between MatrixPage and SettingsPage.
        """
        import logging as _logging
        _log = _logging.getLogger("great_sage.sync")
        sync = self._get_sync()
        if not sync or not sync.is_logged_in():
            return

        def _do():
            ok = sync.delete_item(title)
            if ok:
                _log.info(f"[cloud] Instant delete: '{title}'")
            else:
                _log.warning(f"[cloud] Instant delete failed for '{title}'")

        threading.Thread(target=_do, daemon=True, name="gs_sync_remove").start()

    def _store_rec_notification(self, rec: dict):
        """
        Called on the main thread when a rec arrives via the sync poll.
        Pushes the rec into NotificationStore so the bell badge appears.
        The RecNotificationDialog is only opened when the user clicks the
        notification in the panel — not immediately on arrival.
        """
        import logging as _logging
        _log = _logging.getLogger("great_sage.sync")
        try:
            sender   = rec.get("sender", "Someone")
            title    = rec.get("title", "")
            notif_id = f"rec-{rec.get('id', '')}"
            push_notification(
                title=f"{sender} recommended {title}",
                body=rec.get("message", ""),
                notif_type="friend_rec",
                notif_id=notif_id,
                data=rec,
                cooldown=False,   # recs are unique by id, no cooldown needed
            )
            _log.info(f"[cloud] Rec stored in notification bell: {title} from {sender}")
        except Exception as e:
            _log.warning(f"[cloud] Failed to store rec notification: {e}")
            # Fallback — show the old dialog so the rec is never silently lost
            self._show_rec_notification(rec)

    def _show_rec_notification(self, rec: dict):
        """Show a notification dialog for an incoming recommendation."""
        import logging as _logging
        from PyQt6.QtWidgets import QApplication as _QApp
        _log = _logging.getLogger("great_sage.sync")
        sync = self._get_sync()
        if not sync:
            return
        parent = self.window()
        if parent is None or not parent.isVisible():
            parent = _QApp.activeWindow() or self
        dlg = RecNotificationDialog(rec, parent=parent)
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        dlg.raise_()
        dlg.activateWindow()
        dlg.exec()
        if dlg.result_action == "add":
            ok = sync.accept_recommendation(rec["id"], rec["title"], rec["type"])
            if ok:
                _log.info(f"[cloud] Accepted recommendation: {rec['title']}")
        elif dlg.result_action == "dismiss":
            sync.dismiss_recommendation(rec["id"])
            _log.info(f"[cloud] Dismissed recommendation: {rec['title']}")

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
        # Reset cooldown so the not-logged-in notification fires immediately
        from gs_notifications import _reset_cooldown
        _reset_cooldown("cloud_not_logged_in")
        self._sync_refresh_ui()

    def cleanup(self):
        """Called on app exit — cleanly closes the shared HTTP session."""
        if hasattr(self, "_autosync_timer"):
            self._autosync_timer.stop()
        if hasattr(self, "_sync_client"):
            try:
                self._sync_client.close()
            except Exception:
                pass

    def refresh(self): self._load()
