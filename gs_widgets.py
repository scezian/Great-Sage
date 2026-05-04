"""
gs_widgets.py — Great Sage
==========================
Shared UI components: NavRail, helper functions, toasts, overlays,
ambient canvas, memory palace, watchface.
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
    legion_data, matrix_data,
)

_mobile_server_port = 7331

def hline(color=None):
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    c = color or BORDER
    f.setStyleSheet(f"QFrame{{background:{c};border:none;max-height:1px;margin:2px 0;}}")
    return f

def vline(color=None):
    f = QFrame()
    f.setFrameShape(QFrame.Shape.VLine)
    c = color or BORDER
    f.setStyleSheet(f"QFrame{{background:{c};border:none;max-width:1px;}}")
    return f

def lbl(text, color=TEXT, size=13, bold=False):
    w  = QLabel(text)
    s  = f"color:{color}; font-size:{size}px;"
    if bold:
        s += "font-weight:bold; letter-spacing:0.5px;"
    w.setStyleSheet(s)
    return w

def btn(text, name=None, cb=None):
    b = QPushButton(text)
    if name: b.setObjectName(name)
    if cb:   b.clicked.connect(cb)
    return b

def tag(text, color=MUTED):
    """Small inline label chip."""
    l = QLabel(text)
    l.setStyleSheet(
        f"color:{color}; background:{BG3}; border:1px solid {BORDER};"
        f"font-size:9px; letter-spacing:1px; padding:1px 6px; border-radius:2px;")
    return l


# ═══════════════════════════════════════════════════════════════════════════════
# NAV RAIL — persistent left sidebar used by all pages
# ═══════════════════════════════════════════════════════════════════════════════

class NavRail(QFrame):
    """
    Vertical navigation rail — frosted capsule tile design.
    Settings removed from rail; menu button lives in the pill.
    """
    navigate = pyqtSignal(str)

    _ITEMS = [
        ("dashboard", "◈", "DASH"),
        ("legion",    "◎", "LEGION"),
        ("matrix",    "▣", "MATRIX"),
        ("sage",      "✦", "SAGE"),
        ("editor",    "✎", "EDITOR"),
        ("plugins",   "⬡", "PLUGINS"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NavRail")
        self.setFixedWidth(84)
        self._btns: dict[str, QWidget] = {}
        self._active = ""
        self._build()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QColor
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(BG))
        p.end()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 14, 8, 12)
        outer.setSpacing(0)

        pill = QFrame()
        pill.setObjectName("NavPill")
        pill.setStyleSheet("""
            QFrame#NavPill {
                background: #08080B;
                border: 1px solid #16161E;
                border-radius: 18px;
            }
        """)
        pill_v = QVBoxLayout(pill)
        pill_v.setContentsMargins(8, 14, 8, 12)
        pill_v.setSpacing(3)

        logo_w = QWidget()
        logo_w.setFixedSize(48, 48)
        logo_w.setStyleSheet(
            f"background:#130F04; border:1px solid {ACCENT}2A; border-radius:12px;")
        lv = QVBoxLayout(logo_w)
        lv.setContentsMargins(0, 0, 0, 0)
        logo_lbl = QLabel("◈")
        logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_lbl.setStyleSheet(
            f"background:transparent; border:none; color:{ACCENT}; font-size:20px;")
        lv.addWidget(logo_lbl)
        pill_v.addWidget(logo_w, 0, Qt.AlignmentFlag.AlignHCenter)
        pill_v.addSpacing(8)

        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 transparent,stop:0.5 #252535,stop:1 transparent);")
        pill_v.addWidget(sep)
        pill_v.addSpacing(8)

        for key, icon, label in self._ITEMS:
            tile = self._make_tile(key, icon, label)
            pill_v.addWidget(tile, 0, Qt.AlignmentFlag.AlignHCenter)
            self._btns[key] = tile

        pill_v.addStretch()

        # Menu button
        self._rail_menu_btn = QPushButton("⋯")
        self._rail_menu_btn.setFixedSize(48, 32)
        self._rail_menu_btn.setStyleSheet(f"""
            QPushButton {{
                background:#0C0C10; border:1px solid transparent; border-radius:8px;
                color:#6A6A88; font-size:16px; padding:0;
            }}
            QPushButton:hover {{ background:#111118; border-color:#222230; color:#A0A0C0; }}
        """)
        self._rail_menu_btn.clicked.connect(self._open_rail_menu)
        pill_v.addWidget(self._rail_menu_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        pill_v.addSpacing(4)

        # Network dot
        self._net_dot = QLabel("●")
        self._net_dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._net_dot.setFixedHeight(16)
        self._net_dot.setStyleSheet(
            f"color:{ACCENT2}66; font-size:7px; background:transparent; border:none;")
        self._net_dot.setToolTip("Online")
        pill_v.addWidget(self._net_dot)

        outer.addWidget(pill)

        self._net_timer = QTimer(self)
        self._net_timer.timeout.connect(self._check_network)
        self._net_timer.start(15000)
        self._last_net = None
        QTimer.singleShot(600, self._check_network)

    def _make_tile(self, key: str, icon: str, label: str) -> QWidget:
        tile = QWidget()
        tile.setFixedSize(64, 58)
        tile.setCursor(Qt.CursorShape.PointingHandCursor)
        tile.setToolTip(label)
        tile.setObjectName(f"tile_{key}")
        tv = QVBoxLayout(tile)
        tv.setContentsMargins(0, 8, 0, 8)
        tv.setSpacing(5)
        tv.setAlignment(Qt.AlignmentFlag.AlignCenter)
        streak = QWidget(tile)
        streak.setFixedHeight(1); streak.setFixedWidth(38); streak.move(13, 0)
        streak.setStyleSheet(
            f"background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 transparent,stop:0.5 {ACCENT},stop:1 transparent);")
        streak.setVisible(False)
        tile._streak = streak
        ico = QLabel(icon)
        ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ico.setStyleSheet("background:transparent; border:none; font-size:15px; color:#6A6A88;")
        ico.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        tile._ico = ico
        txt = QLabel(label)
        txt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        txt.setStyleSheet(
            f"background:transparent; border:none; "
            f"font-family:{FONT_UI}; font-size:7px; letter-spacing:1px; color:#5A5A78;")
        txt.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        tile._txt = txt
        tv.addWidget(ico); tv.addWidget(txt)
        tile.mousePressEvent = lambda e, k=key: self.navigate.emit(k)
        self._apply_style(tile, False)
        return tile

    @staticmethod
    def _apply_style(tile: QWidget, active: bool):
        if active:
            tile.setStyleSheet(
                "QWidget { background:#1A1106; border:1px solid #C9A84C44; border-radius:10px; }")
            tile._streak.setVisible(True)
            tile._ico.setStyleSheet(
                f"background:transparent; border:none; font-size:15px; color:{ACCENT};")
            tile._txt.setStyleSheet(
                f"background:transparent; border:none; "
                f"font-family:{FONT_UI}; font-size:7px; letter-spacing:1px; "
                f"color:#A07830; font-weight:bold;")
        else:
            tile.setStyleSheet(
                "QWidget { background:#0C0C10; border:1px solid transparent; border-radius:10px; }"
                "QWidget:hover { background:#111118; border:1px solid #222230; }")
            tile._streak.setVisible(False)
            tile._ico.setStyleSheet(
                "background:transparent; border:none; font-size:15px; color:#6A6A88;")
            tile._txt.setStyleSheet(
                f"background:transparent; border:none; "
                f"font-family:{FONT_UI}; font-size:7px; letter-spacing:1px; color:#5A5A78;")

    def set_active(self, key: str):
        if self._active == key:
            return
        if self._active in self._btns:
            self._apply_style(self._btns[self._active], False)
        self._active = key
        if key in self._btns:
            self._apply_style(self._btns[key], True)

    def _open_rail_menu(self):
        mw = self.window()
        if hasattr(mw, "_open_menu"):
            mw._open_menu()

    def _check_network(self):
        def _ping():
            try:
                import socket
                socket.setdefaulttimeout(3)
                socket.getaddrinfo("8.8.8.8", 53)
                return True
            except Exception:
                return False
        online = _ping()
        if online != self._last_net:
            self._last_net = online
            if online:
                self._net_dot.setStyleSheet(
                    f"color:{ACCENT2}66; font-size:7px; background:transparent; border:none;")
                self._net_dot.setToolTip("Online")
                log.network.info("Network state changed: ONLINE")
            else:
                self._net_dot.setStyleSheet(
                    f"color:{MUTED}; font-size:7px; background:transparent; border:none;")
                self._net_dot.setToolTip("Offline")
                log.network.warning("Network state changed: OFFLINE")
_AMBIENT_THEMES = {
    "cultivation": {
        "orb_colors":   [(60, 220, 130), (40, 180, 100), (20, 140, 80)],
        "particle_col": (100, 240, 160),
        "bg_top": (14, 32, 22),   # lum ≈ 27 — dark forest green, clearly non-black
        "bg_bot": ( 7, 16, 11),
        "mode": "mist",
    },
    "fantasy": {
        "orb_colors":   [(220, 180, 60), (200, 150, 40), (240, 210, 80)],
        "particle_col": (250, 220, 100),
        "bg_top": (28, 20,  8),   # lum ≈ 22 — dark amber
        "bg_bot": (14, 10,  4),
        "mode": "motes",
    },
    "sci-fi": {
        "orb_colors":   [(50, 140, 230), (30, 100, 210), (70, 190, 250)],
        "particle_col": (120, 210, 255),
        "bg_top": ( 6, 16, 40),   # lum ≈ 25 — deep space blue
        "bg_bot": ( 3,  8, 20),
        "mode": "stars",
    },
    "thriller": {
        "orb_colors":   [(200, 20, 30), (160, 10, 20), (220, 50, 50)],
        "particle_col": (220, 60, 60),
        "bg_top": (28,  6,  6),   # lum ≈ 16 — dark crimson (thriller can be darker)
        "bg_bot": (14,  3,  3),
        "mode": "mist",
    },
    "romance": {
        "orb_colors":   [(220, 80, 130), (200, 60, 110), (240, 130, 170)],
        "particle_col": (245, 160, 190),
        "bg_top": (32, 10, 18),   # lum ≈ 24 — dark rose
        "bg_bot": (16,  5,  9),
        "mode": "motes",
    },
    "default": {
        "orb_colors":   [(70, 120, 210), (50, 100, 190), (90, 150, 230)],
        "particle_col": (140, 200, 255),
        "bg_top": (12, 20, 50),   # lum ≈ 30 — deep navy
        "bg_bot": ( 6, 10, 25),
        "mode": "rain",
    },
}


# ── Reading Room genre profiles ────────────────────────────────────────────────
_GENRE_PROFILES = {
    "cultivation": {
        "label": "Cultivation", "bg": "#0A1A12",
        "text": "#D4EED8", "font": "Palatino Linotype, Georgia, serif",
        "size": 19, "line_height": 2.0,
    },
    "fantasy": {
        "label": "Fantasy", "bg": "#1A1208",
        "text": "#EEE4CC", "font": "Palatino Linotype, Georgia, serif",
        "size": 19, "line_height": 2.0,
    },
    "sci-fi": {
        "label": "Sci-Fi", "bg": "#060C1A",
        "text": "#C8DCF0", "font": "JetBrains Mono, Consolas, monospace",
        "size": 17, "line_height": 1.9,
    },
    "thriller": {
        "label": "Thriller", "bg": "#140608",
        "text": "#EED8D4", "font": "Palatino Linotype, Georgia, serif",
        "size": 19, "line_height": 2.0,
    },
    "romance": {
        "label": "Romance", "bg": "#180810",
        "text": "#F0D8E8", "font": "Palatino Linotype, Georgia, serif",
        "size": 19, "line_height": 2.0,
    },
    "default": {
        "label": "Reading", "bg": "#0A0C14",
        "text": "#E8E4DC", "font": "Palatino Linotype, Georgia, serif",
        "size": 19, "line_height": 2.0,
    },
}

# Sound files for ambient reading mode — paths to local audio files.
# Leave empty if you don't have audio files; the overlay works fine without sound.
_SOUND_FILES: dict = {
    # "rain":    "/path/to/rain.ogg",
    # "forest":  "/path/to/forest.ogg",
    # "cafe":    "/path/to/cafe.ogg",
    # "library": "/path/to/library.ogg",
}


class EyeBreakToast(QWidget):
    """Small notification reminding the reader to look away from the screen.
    Slides in from the top-centre, lingers, then fades out automatically.
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)

        lbl = QLabel("👁  Look away — focus on something 20 ft away for 20 seconds", self)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"""
            QLabel {{
                background: rgba(14, 28, 20, 220);
                color: #A0E8C0;
                font-family: {FONT_UI};
                font-size: 13px;
                letter-spacing: 1px;
                border: 1px solid rgba(80, 200, 130, 120);
                border-radius: 8px;
                padding: 10px 20px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(lbl)

        self._opacity = 0.0
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)
        self._phase = "in"   # in → hold → out

    def show_toast(self):
        self._opacity = 0.0
        self._phase = "in"
        self._position()
        self.show()
        self.raise_()
        self._anim_timer.start(16)   # ~60fps

    def _position(self):
        p = self.parent()
        if not p: return
        self.adjustSize()
        w = max(self.sizeHint().width(), 480)
        h = self.sizeHint().height()
        self.setFixedSize(w, h)
        x = (p.width() - w) // 2
        self.move(x, -h)   # start above

    def _tick(self):
        import math as _m
        p = self.parent()
        if not p:
            self._anim_timer.stop(); self.hide(); return

        w = self.width()
        h = self.height()
        target_y = 16   # final resting y

        if self._phase == "in":
            self._opacity = min(1.0, self._opacity + 0.06)
            cur_y = self.y()
            new_y = cur_y + max(2, int((target_y - cur_y) * 0.18))
            self.move((p.width() - w) // 2, new_y)
            if self._opacity >= 1.0 and abs(new_y - target_y) <= 2:
                self.move((p.width() - w) // 2, target_y)
                self._phase = "hold"
                QTimer.singleShot(4000, self._start_out)
        elif self._phase == "out":
            self._opacity = max(0.0, self._opacity - 0.04)
            cur_y = self.y()
            self.move((p.width() - w) // 2, cur_y - 3)
            if self._opacity <= 0:
                self._anim_timer.stop()
                self.hide()

        self.setWindowOpacity(self._opacity)

    def _start_out(self):
        self._phase = "out"

    def resizeEvent(self, e):
        super().resizeEvent(e)



class SyncToast(QWidget):
    """Brief notification that auto-sync found and is downloading new chapters."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self._lbl = QLabel("", self)
        self._lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl.setStyleSheet(f"""
            QLabel {{
                background: rgba(14, 20, 36, 220);
                color: #A0C8FF;
                font-family: {FONT_UI};
                font-size: 13px;
                letter-spacing: 1px;
                border: 1px solid rgba(80, 130, 220, 120);
                border-radius: 8px;
                padding: 10px 24px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._lbl)
        self._opacity = 0.0
        self._phase = "in"
        self._eff = QGraphicsOpacityEffect(self)
        self._eff.setOpacity(0.0)
        self.setGraphicsEffect(self._eff)
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)

    def show_toast(self, message):
        self._lbl.setText(message)
        self._opacity = 0.0
        self._phase = "in"
        self._position()
        self.show()
        self.raise_()
        self._anim_timer.start(16)

    def _position(self):
        p = self.parent()
        if not p: return
        self.adjustSize()
        w = max(self.sizeHint().width(), 420)
        h = self.sizeHint().height()
        self.setFixedSize(w, h)
        x = (p.width() - w) // 2
        # Bottom of page, above the stats/button bar (~110px from bottom)
        y = p.height() - h - 110
        self.move(x, y)

    def _tick(self):
        eff = self._eff
        if self._phase == "in":
            self._opacity = min(1.0, self._opacity + 0.05)
            eff.setOpacity(self._opacity)
            if self._opacity >= 1.0:
                self._phase = "hold"
                QTimer.singleShot(4000, self._start_fade)
        elif self._phase == "out":
            self._opacity = max(0.0, self._opacity - 0.04)
            eff.setOpacity(self._opacity)
            if self._opacity <= 0:
                self._anim_timer.stop()
                self.hide()

    def _start_fade(self):
        self._phase = "out"

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._position()


class _TouchScrollFilter(QObject):
    """Intercepts touch events on QTextEdit and converts them to smooth scroll actions,
    preventing text selection on touchscreen devices."""
    def __init__(self, scroll_widget, parent=None):
        super().__init__(parent)
        self._widget  = scroll_widget
        self._start_y = None

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        t = event.type()
        if t == QEvent.Type.TouchBegin:
            pts = event.points()
            if pts:
                self._start_y = pts[0].position().y()
            return True
        elif t == QEvent.Type.TouchUpdate:
            pts = event.points()
            if pts and self._start_y is not None:
                dy = self._start_y - pts[0].position().y()
                self._widget.verticalScrollBar().setValue(
                    self._widget.verticalScrollBar().value() + int(dy))
                self._start_y = pts[0].position().y()
            return True
        elif t == QEvent.Type.TouchEnd:
            self._start_y = None
            return True
        return False


class ReadingRoomOverlay(QWidget):
    """Fullscreen reading room.

    The ambient background is painted directly in this widget's own paintEvent —
    no child-canvas widget. This guarantees rendering on all Qt/X11 configurations
    regardless of compositor availability or widget z-order.
    """

    closed = pyqtSignal()

    def __init__(self, text, title, genre):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._profile  = _GENRE_PROFILES.get(genre, _GENRE_PROFILES["default"])
        self._genre    = genre
        self._snd_key  = self._pick_sound(genre)
        self._player   = None
        self._audio    = None
        self._volume   = 0.25
        self._ui_vis   = True

        # ── Ambient state (painted in paintEvent, no child widget needed) ──────
        self._amb_theme = _AMBIENT_THEMES.get(genre, _AMBIENT_THEMES["default"])
        self._amb_t     = 0.0
        import random as _rnd
        _rng = _rnd.Random(7)
        self._amb_orbs = []
        for i in range(5):
            self._amb_orbs.append({
                "x":    _rng.uniform(0.0, 1.0),
                "y":    _rng.uniform(0.0, 1.0),
                "r":    _rng.uniform(0.32, 0.50),   # large — fills the screen
                "spx":  _rng.uniform(-0.0003, 0.0003),
                "spy":  _rng.uniform(-0.0002, 0.0002),
                "phase":_rng.uniform(0, 6.28),
                "ci":   i % len(self._amb_theme["orb_colors"]),
            })
        mode = self._amb_theme["mode"]
        n    = 60 if mode in ("motes", "stars") else 80
        self._amb_particles = [self._amb_new_particle(_rng, born=True) for _ in range(n)]
        self._amb_rng = _rnd.Random(99)   # separate rng for respawns

        self._amb_timer = QTimer(self)
        self._amb_timer.timeout.connect(self._amb_tick)
        self._amb_timer.start(33)   # ~30 fps

        self._build(text, title)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._hide_ui)
        self._hide_timer.start(4000)
        self.setMouseTracking(True)
        QTimer.singleShot(500, self._start_sound)

    # ── Ambient helpers ────────────────────────────────────────────────────────

    def _amb_new_particle(self, rng=None, born=False):
        if rng is None:
            rng = self._amb_rng
        mode = self._amb_theme["mode"]
        if mode == "rain":
            return {
                "x":   rng.uniform(0.0, 1.0),
                "y":   rng.uniform(0.0, 1.0) if born else -0.01,
                "vy":  rng.uniform(0.003, 0.008),
                "vx":  rng.uniform(0.0003, 0.001),
                "len": rng.uniform(0.008, 0.018),
                "a":   rng.randint(60, 130),
            }
        elif mode == "stars":
            return {
                "x":     rng.uniform(0.0, 1.0),
                "y":     rng.uniform(0.0, 1.0),
                "sz":    rng.uniform(1.0, 2.8),
                "phase": rng.uniform(0, 6.28),
                "spd":   rng.uniform(0.02, 0.07),
                "a":     rng.randint(90, 220),
            }
        else:   # mist / motes
            return {
                "x":    rng.uniform(0.0, 1.0),
                "y":    rng.uniform(0.0, 1.0) if born else 1.01,
                "vx":   rng.uniform(-0.0004, 0.0004),
                "vy":   rng.uniform(-0.002, -0.0005),
                "sz":   rng.uniform(2.0, 5.0),
                "phase":rng.uniform(0, 6.28),
                "a":    rng.randint(80, 170),
                "life": 1.0,
            }

    def _amb_tick(self):
        import math as _m
        self._amb_t += 0.016
        mode = self._amb_theme["mode"]
        for o in self._amb_orbs:
            o["x"] += o["spx"]; o["y"] += o["spy"]
            if o["x"] < -0.5: o["x"] = 1.5
            if o["x"] >  1.5: o["x"] = -0.5
            if o["y"] < -0.5: o["y"] = 1.5
            if o["y"] >  1.5: o["y"] = -0.5
        dead = []
        for i, pt in enumerate(self._amb_particles):
            if mode == "rain":
                pt["y"] += pt["vy"]; pt["x"] += pt["vx"]
                if pt["y"] > 1.02: dead.append(i)
            elif mode == "stars":
                pt["phase"] += pt["spd"]
            else:
                pt["y"] += pt["vy"]; pt["x"] += pt["vx"]
                pt["life"] -= 0.002
                if pt["life"] <= 0 or pt["y"] < -0.02: dead.append(i)
        for i in reversed(dead):
            self._amb_particles[i] = self._amb_new_particle()
        self.update()   # triggers paintEvent on self

    # ── Core paintEvent — THE background ─────────────────────────────────────
    def paintEvent(self, _):
        import math as _m
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        if W == 0 or H == 0:
            p.end(); return

        t = self._amb_theme

        # 1. Background gradient
        grad = QLinearGradient(0, 0, 0, H)
        grad.setColorAt(0, QColor(*t["bg_top"]))
        grad.setColorAt(1, QColor(*t["bg_bot"]))
        p.fillRect(0, 0, W, H, QBrush(grad))

        # 2. Ambient orbs — large, high-alpha glows
        p.setPen(Qt.PenStyle.NoPen)
        for o in self._amb_orbs:
            cx = int(o["x"] * W); cy = int(o["y"] * H)
            r  = int(o["r"] * min(W, H))
            pulse = 0.90 + 0.10 * _m.sin(self._amb_t * 0.7 + o["phase"])
            rr = max(1, int(r * pulse))
            rc, gc, bc = t["orb_colors"][o["ci"]]
            gr = QRadialGradient(cx, cy, rr)
            gr.setColorAt(0,    QColor(rc, gc, bc, 210))
            gr.setColorAt(0.40, QColor(rc, gc, bc, 100))
            gr.setColorAt(0.75, QColor(rc, gc, bc,  28))
            gr.setColorAt(1,    QColor(rc, gc, bc,   0))
            p.setBrush(QBrush(gr))
            p.drawEllipse(cx - rr, cy - rr, rr * 2, rr * 2)

        # 3. Particles
        pr, pg, pb = t["particle_col"]
        mode = t["mode"]
        if mode == "rain":
            for pt in self._amb_particles:
                x1 = int(pt["x"] * W); y1 = int(pt["y"] * H)
                x2 = int(x1 + pt["vx"] * W * 6)
                y2 = int(y1 + pt["len"] * H)
                p.setPen(QPen(QColor(pr, pg, pb, pt["a"]), 1,
                              Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawLine(x1, y1, x2, y2)
        elif mode == "stars":
            p.setPen(Qt.PenStyle.NoPen)
            for pt in self._amb_particles:
                # ── Sound ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _pick_sound(genre):
        if genre in ("cultivation", "fantasy"): return "forest"
        if genre == "romance":                  return "cafe"
        if genre in ("sci-fi", "thriller"):     return "library"
        return "rain"

    def _start_sound(self):
        path = _SOUND_FILES.get(self._snd_key)
        if not path or not os.path.exists(path): return
        try:
            from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
            from PyQt6.QtCore import QUrl as _U
            self._audio = QAudioOutput()
            self._audio.setVolume(self._volume)
            self._player = QMediaPlayer()
            self._player.setAudioOutput(self._audio)
            self._player.setSource(_U.fromLocalFile(path))
            self._player.setLoops(-1)
            self._player.play()
        except Exception as e:
            log.warning("ReadingRoom sound error", error=str(e))

    def _stop_sound(self):
        try:
            if self._player: self._player.stop()
        except Exception: pass  # Ignored

    # ── UI construction ───────────────────────────────────────────────────────

    def _build(self, text, title):
        # paintEvent handles the background — no background widget needed.
        # setAutoFillBackground(False) ensures Qt doesn't pre-fill with palette colour.
        self.setAutoFillBackground(False)

        # Single overlay QWidget that holds all UI children.
        # WA_NoSystemBackground stops Qt from clearing it to the palette colour,
        # which would paint over our paintEvent output.
        self._overlay = QWidget(self)
        self._overlay.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._overlay.setAutoFillBackground(False)

        ov = QVBoxLayout(self._overlay)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(0)

        self._top_bar = self._make_top_bar()
        ov.addWidget(self._top_bar)

        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)

        left_pad = QWidget()
        left_pad.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        left_pad.setAutoFillBackground(False)

        self._card = self._make_card(text)

        right_pad = QWidget()
        right_pad.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        right_pad.setAutoFillBackground(False)

        mid.addWidget(left_pad,    22)
        mid.addWidget(self._card,  56)
        mid.addWidget(right_pad,   22)

        ov.addLayout(mid, 1)

        self._bot_bar = self._make_bot_bar(title)
        ov.addWidget(self._bot_bar)

    def _make_top_bar(self):
        bar = QWidget()
        bar.setFixedHeight(52)
        bar.setStyleSheet(
            "background: rgba(0,0,0,180);"
            "border-bottom: 1px solid rgba(255,255,255,40);"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(28, 0, 28, 0)
        h.setSpacing(16)

        exit_b = QPushButton("✕  EXIT READING ROOM")
        exit_b.setCursor(Qt.CursorShape.PointingHandCursor)
        exit_b.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.12);
                border: 1px solid rgba(255,255,255,0.30);
                border-radius: 6px; padding: 4px 16px;
                color: rgba(255,255,255,0.80);
                font-size: 10px; letter-spacing: 2px;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.25);
                border: 1px solid rgba(255,255,255,0.55);
                color: rgba(255,255,255,1.0);
            }
        """)
        exit_b.clicked.connect(self.close)

        p = self._profile
        genre_lbl = QLabel(f"◈  {p['label'].upper()}")
        genre_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.55); font-size:9px; letter-spacing:3px;")

        snd_lbl = QLabel("♪")
        snd_lbl.setStyleSheet("color:rgba(255,255,255,0.55); font-size:14px;")

        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(int(self._volume * 100))
        self._vol_slider.setFixedWidth(120)
        self._vol_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background: rgba(255,255,255,0.20);
                height: 3px; border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: rgba(255,255,255,0.70);
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: rgba(255,255,255,0.95);
                width: 12px; height: 12px;
                margin: -5px 0; border-radius: 6px;
            }
        """)
        self._vol_slider.valueChanged.connect(self._on_volume)

        h.addWidget(exit_b)
        h.addStretch()
        h.addWidget(genre_lbl)
        h.addStretch()
        h.addWidget(snd_lbl)
        h.addWidget(self._vol_slider)
        return bar

    def _make_card(self, text):
        """Semi-transparent card panel — sits over the ambient background."""
        p = self._profile
        outer = QWidget()
        outer.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        outer.setAutoFillBackground(False)

        ov = QVBoxLayout(outer)
        ov.setContentsMargins(0, 28, 0, 28)
        ov.setSpacing(0)

        glass = _GlassCard(p["bg"])
        inner = QHBoxLayout(glass)
        inner.setContentsMargins(48, 36, 8, 36)
        inner.setSpacing(0)

        reader = QTextEdit()
        reader.setReadOnly(True)
        reader.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        reader.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        reader.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        reader.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        reader.setFrameShape(QFrame.Shape.NoFrame)
        reader.setStyleSheet(f"""
            QTextEdit {{
                background: transparent;
                color: {p['text']};
                border: none;
                font-family: {p['font']};
                font-size: {p['size']}px;
                line-height: {p['line_height']};
                selection-background-color: rgba(255,255,255,0.12);
            }}
        ")
        reader.setPlainText(text)
        self._reader = reader

        reader.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self._touch_filter = _TouchScrollFilter(reader, reader)
        reader.installEventFilter(self._touch_filter)

        bm = _BookmarkBar(reader)
        reader.verticalScrollBar().valueChanged.connect(lambda _: bm.update())

        inner.addWidget(reader, 1)
        inner.addWidget(bm)

        ov.addWidget(glass, 1)
        return outer

    @staticmethod
    def _make_bot_bar(title):
        bar = QWidget()
        bar.setFixedHeight(40)
        bar.setStyleSheet(
            "background: rgba(0,0,0,160);"
            "border-top: 1px solid rgba(255,255,255,30);"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(28, 0, 28, 0)
        tl = QLabel(title[:80])
        tl.setStyleSheet("color:rgba(255,255,255,0.50); font-size:10px; letter-spacing:1px;")
        el = QLabel("Esc · exit   |   Space · scroll")
        el.setStyleSheet("color:rgba(255,255,255,0.30); font-size:9px; letter-spacing:1px;")
        h.addWidget(tl); h.addStretch(); h.addWidget(el)
        return bar

    # ── Resize / interaction ──────────────────────────────────────────────────

    def resizeEvent(self, e):
        self._overlay.setGeometry(0, 0, self.width(), self.height())
        super().resizeEvent(e)

    def _hide_ui(self):
        self._ui_vis = False
        self._top_bar.setVisible(False)
        self._bot_bar.setVisible(False)

    def _show_ui(self):
        if not self._ui_vis:
            self._ui_vis = True
            self._top_bar.setVisible(True)
            self._bot_bar.setVisible(True)
        self._hide_timer.start(4000)

    def mouseMoveEvent(self, e):
        self._show_ui()
        super().mouseMoveEvent(e)

    def _on_volume(self, val):
        self._volume = val / 100
        try:
            if self._audio: self._audio.setVolume(self._volume)
        except Exception: pass  # Ignored

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.close()
        elif k in (Qt.Key.Key_Space, Qt.Key.Key_Down,
                   Qt.Key.Key_PageDown, Qt.Key.Key_Right):
            sb = self._reader.verticalScrollBar()
            sb.setValue(sb.value() + 400)
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_PageUp, Qt.Key.Key_Left):
            sb = self._reader.verticalScrollBar()
            sb.setValue(sb.value() - 400)
        elif k == Qt.Key.Key_N:
            self._show_ui()
            if callable(getattr(self, 'on_next', None)): self.on_next()
        elif k == Qt.Key.Key_P:
            self._show_ui()
            if callable(getattr(self, 'on_prev', None)): self.on_prev()
        elif k == Qt.Key.Key_S:
            if callable(getattr(self, 'on_sage', None)): self.on_sage()
        else:
            super().keyPressEvent(e)

    def load_text(self, text, title):
        """Hot-reload text after chapter change without closing the overlay."""
        self._reader.setPlainText(text)
        self._reader.verticalScrollBar().setValue(0)
        try: self._bot_lbl.setText(title[:80])
        except Exception: pass  # Ignored

    def closeEvent(self, e):
        self._amb_timer.stop()
        self._stop_sound()
        self.releaseKeyboard()
        self.closed.emit()
        super().closeEvent(e)


class _GlassCard(QWidget):
    """Semi-transparent panel that floats over the ambient background.

    Colour: genre bg_hex + large offsets (80/90/110) so the card is clearly
    distinct from the near-black background even on dim monitors.
    Alpha 240 keeps it mostly opaque while letting the orb glow bleed through
    slightly at the edges.
    """
    def __init__(self, bg_hex, parent=None):
        super().__init__(parent)
        c = QColor(bg_hex)
        self._bg = QColor(
            min(255, c.red()   + 80),
            min(255, c.green() + 90),
            min(255, c.blue()  + 110),
            240
        )

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 14, 14)

        # Main fill
        p.fillPath(path, QBrush(self._bg))

        # Depth gradient — lighter at top, slightly darker at bottom
        depth = QLinearGradient(0, 0, 0, self.height())
        depth.setColorAt(0,    QColor(255, 255, 255, 25))
        depth.setColorAt(0.35, QColor(255, 255, 255,  5))
        depth.setColorAt(1,    QColor(  0,   0,   0, 18))
        p.fillPath(path, QBrush(depth))

        # Top sheen
        sheen = QLinearGradient(0, 0, 0, 100)
        sheen.setColorAt(0, QColor(255, 255, 255, 55))
        sheen.setColorAt(1, QColor(255, 255, 255,  0))
        p.fillPath(path, QBrush(sheen))

        # Border — soft outer glow then crisp inner line
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 20), 3.0))
        p.drawRoundedRect(1, 1, self.width() - 2, self.height() - 2, 13, 13)
        p.setPen(QPen(QColor(255, 255, 255, 70), 1.0))
        p.drawRoundedRect(1, 1, self.width() - 2, self.height() - 2, 13, 13)
        p.end()


class _BookmarkBar(QWidget):
    def __init__(self, reader, parent=None):
        super().__init__(parent)
        self._reader = reader
        self.setFixedWidth(14)

    def _progress(self):
        sb = self._reader.verticalScrollBar()
        lo, hi = sb.minimum(), sb.maximum()
        if hi <= lo: return 0.0
        return (sb.value() - lo) / (hi - lo)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        H = self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 14)))
        p.drawRoundedRect(4, 0, 6, H, 3, 3)
        prog = self._progress()
        bm_h = max(36, int(H * 0.07))
        bm_y = int(prog * (H - bm_h))
        p.setBrush(QBrush(QColor(255, 255, 255, 50)))
        p.drawRoundedRect(3, bm_y, 8, bm_h, 3, 3)
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        mid = 7
        p.drawLine(mid - 3, bm_y + bm_h - 6, mid, bm_y + bm_h - 2)
        p.drawLine(mid + 3, bm_y + bm_h - 6, mid, bm_y + bm_h - 2)
        p.end()


# AmbientCanvas is kept for API compatibility (no longer used by ReadingRoomOverlay)
class AmbientCanvas(QWidget):
    """Legacy ambient canvas — retained for compatibility. ReadingRoomOverlay
    now paints its own background directly via paintEvent."""

    THEMES = _AMBIENT_THEMES   # expose for external access if needed

    def __init__(self, genre, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._timer = QTimer(self)

    def stop(self):
        self._timer.stop()

    def paintEvent(self, _):
        pass   # no-op — ReadingRoomOverlay owns the painting


# ── Memory Palace HTML scene ───────────────────────────────────────────────────
_MEMORY_PALACE_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #030810;
    color: #C8D4E4;
    font-family: 'Palatino Linotype', Palatino, Georgia, serif;
    overflow: hidden;
    height: 100vh;
    display: flex;
    flex-direction: column;
  }

  #header {
    padding: 18px 36px 12px;
    border-bottom: 1px solid #0E1A2A;
    display: flex;
    align-items: baseline;
    gap: 16px;
  }
  #header h1 {
    font-size: 18px;
    letter-spacing: 4px;
    color: #C8A96E;
    font-weight: normal;
  }
  #header span {
    font-size: 11px;
    letter-spacing: 2px;
    color: #2A3A50;
  }

  #room {
    flex: 1;
    display: flex;
    flex-direction: column;
    padding: 0 36px 24px;
    overflow: hidden;
  }

  .section-label {
    font-size: 9px;
    letter-spacing: 3px;
    color: #1E3050;
    margin: 24px 0 12px;
    text-transform: uppercase;
  }

  /* ── Bookshelves ── */
  #shelves {
    flex: 1;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: #0E1A2A transparent;
  }
  .shelf {
    border-bottom: 3px solid #0A1520;
    background: linear-gradient(180deg, transparent 92%, #060E1A 100%);
    padding: 8px 0 0;
    display: flex;
    flex-wrap: nowrap;
    gap: 6px;
    overflow-x: auto;
    scrollbar-width: none;
    min-height: 90px;
  }
  .book {
    flex-shrink: 0;
    width: 28px;
    border-radius: 2px 0 0 2px;
    display: flex;
    align-items: flex-end;
    justify-content: center;
    padding-bottom: 6px;
    cursor: default;
    transition: transform 0.15s, filter 0.15s;
    position: relative;
  }
  .book:hover {
    transform: translateY(-8px);
    filter: brightness(1.4);
    z-index: 10;
  }
  .book-spine {
    writing-mode: vertical-rl;
    text-orientation: mixed;
    transform: rotate(180deg);
    font-size: 9px;
    letter-spacing: 1px;
    color: rgba(255,255,255,0.55);
    white-space: nowrap;
    overflow: hidden;
    max-height: 72px;
    text-overflow: ellipsis;
    user-select: none;
  }
  .book-tooltip {
    display: none;
    position: absolute;
    bottom: calc(100% + 8px);
    left: 50%;
    transform: translateX(-50%);
    background: #0A1828;
    border: 1px solid #1E3040;
    border-radius: 4px;
    padding: 6px 10px;
    font-size: 11px;
    color: #C8D4E4;
    white-space: nowrap;
    z-index: 100;
    pointer-events: none;
  }
  .book:hover .book-tooltip { display: block; }

  /* ── Poster wall ── */
  #poster-wall {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    padding: 4px 0;
    overflow-y: auto;
    max-height: 130px;
    scrollbar-width: thin;
    scrollbar-color: #0E1A2A transparent;
  }
  .poster {
    width: 68px;
    height: 100px;
    border-radius: 3px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-end;
    padding: 6px 4px;
    cursor: default;
    transition: transform 0.15s;
    position: relative;
    overflow: hidden;
  }
  .poster:hover { transform: scale(1.08); z-index: 10; }
  .poster-title {
    font-size: 8px;
    letter-spacing: 0.5px;
    color: rgba(255,255,255,0.80);
    text-align: center;
    line-height: 1.3;
    background: linear-gradient(transparent, rgba(0,0,0,0.75));
    width: 100%;
    padding: 12px 3px 3px;
    word-break: break-word;
  }
  .poster-icon {
    font-size: 28px;
    position: absolute;
    top: 18px;
    opacity: 0.6;
  }
  .empty-msg {
    font-size: 12px;
    color: #1E3050;
    letter-spacing: 1px;
    padding: 16px 0;
  }
</style>
</head>
<body>

<div id="header">
  <h1>◈ MEMORY PALACE</h1>
  <span id="counts"></span>
</div>

<div id="room">
  <div class="section-label">Library — Books Read</div>
  <div id="shelves"></div>

  <div class="section-label">Viewing Room — Shows Watched</div>
  <div id="poster-wall"></div>
</div>

<script>
const BOOK_COLORS = [
  ['#1A3A5C','#2A5A8C'],['#3C1A1A','#6C2A2A'],['#1A3C1A','#2A6C2A'],
  ['#3C2E1A','#6C501A'],['#1E1A3C','#30286C'],['#3C1A30','#6C2A50'],
  ['#1A3A3A','#2A6060'],['#2E2E1A','#50501A'],['#2A1A3C','#4A2A6C'],
  ['#1A2E3C','#2A4A6C'],
];

const books = __BOOKS__
const shows = __SHOWS__

document.getElementById('counts').textContent =
  books.length + ' book' + (books.length !== 1 ? 's' : '') + '  ·  ' +
  shows.length + ' show' + (shows.length !== 1 ? 's' : '')

// ── XSS escape helper ─────────────────────────────────────────────────────
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
                  .replace(/>/g,'&gt;').replace(/"/g,'&quot;')
                  .replace(/'/g,'&#39;');
}

// ── Build shelves ──────────────────────────────────────────────────────────
const shelvesEl = document.getElementById('shelves')

if (books.length === 0) {
  shelvesEl.innerHTML = '<div class="empty-msg">No books read yet — start reading in Legion.</div>';
} else {
  const SHELF_SIZE = 18
  for (let s = 0
  s < books.length
  s += SHELF_SIZE) {
    const shelf = document.createElement('div')
    shelf.className = 'shelf'
    const batch = books.slice(s, s + SHELF_SIZE)
    batch.forEach((book, i) => {
      const ci = (s / SHELF_SIZE + i) % BOOK_COLORS.length
      const [bg1, bg2] = BOOK_COLORS[ci]
      const height = 60 + (i % 5) * 8
      const el = document.createElement('div')
      el.className = 'book'
      el.style.cssText = `height:${height}px
      background:linear-gradient(90deg,${bg1},${bg2})
      `
      el.innerHTML = `
        <div class="book-spine">${esc(book.title)}</div>
        <div class="book-tooltip">${esc(book.title)}<br><span style="color:#4A6080;font-size:10px">${esc(book.info)}</span></div>
      `;
      shelf.appendChild(el);
    });
    shelvesEl.appendChild(shelf);
  }
}

// ── Build poster wall ──────────────────────────────────────────────────────
const POSTER_COLORS = [
  ['#0D1E3A','#4A9EE0'],['#1A0D26','#9A70E0'],['#0D1A0D','#4FC4A0'],
  ['#2A0D0D','#E05C6A'],['#1A1A0D','#E8C97A'],['#0D1A1A','#00E5CC'],
];
const POSTER_ICONS = ['🎬','📺','🎭','🎌','🎪','⚔️','🔮','🌌']

const wallEl = document.getElementById('poster-wall')

if (shows.length === 0) {
  wallEl.innerHTML = '<div class="empty-msg">No shows tracked yet — add shows in Matrix.</div>';
} else {
  shows.forEach((show, i) => {
    const [bg1, bg2] = POSTER_COLORS[i % POSTER_COLORS.length]
    const icon = POSTER_ICONS[i % POSTER_ICONS.length]
    const el = document.createElement('div')
    el.className = 'poster'
    el.style.cssText = `background:linear-gradient(160deg,${bg1},${bg2})
    `
    el.innerHTML = `
      <div class="poster-icon">${icon}</div>
      <div class="poster-title">${esc(show.title)}</div>
    `;
    el.title = esc(show.title) + ' — ' + esc(show.info)
    wallEl.appendChild(el);
  });
}
</script>
</body>
</html>"""


class MemoryPalaceWindow(QWidget):
    """3D room that fills with books on shelves and show posters as you read/watch."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("◈ Memory Palace")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.resize(1280, 800)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0,0,0); root.setSpacing(0)

        bar = QWidget()
        bar.setFixedHeight(36)
        bar.setStyleSheet("background:#050810;border-bottom:1px solid #0A1520;")
        bv = QHBoxLayout(bar)
        bv.setContentsMargins(16,0,16,0)
        tl = QLabel("◈  MEMORY PALACE")
        tl.setStyleSheet(f"color:{ACCENT};font-size:11px;letter-spacing:3px;")
        cb = QPushButton("✕  CLOSE")
        cb.setStyleSheet("background:transparent;border:none;color:#4A6070;"
                         "font-size:9px;letter-spacing:2px;")
        cb.clicked.connect(self.close)
        bv.addWidget(tl); bv.addStretch(); bv.addWidget(cb)
        root.addWidget(bar)

        if WEBENGINE_OK:
            self._view = QWebEngineView()
            root.addWidget(self._view, 1)
            self._load_scene()
        else:
            self._build_native(root)

    @staticmethod
    def _build_native(root):
        """Native Qt fallback — renders when QtWebEngine is not installed."""
        ld = legion_data()
        md = matrix_data()
        books = [
            (n, b.get("chapters_read", 0))
            for n, b in ld.get("books", {}).items()
            if b.get("chapters_read", 0) > 0 or b.get("current_chapter", 0) > 0
        ]
        shows = []
        for key, info in md.get("watching", {}).items():
            if isinstance(info, dict):
                ep = len(info.get("episodes_watched", []))
                shows.append((info.get("title", key), ep))
            else:
                shows.append((key, 0))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:#030810;}"
                             "QScrollBar:vertical{background:#030810;width:4px;border:none;}"
                             "QScrollBar::handle:vertical{background:#0E1A2A;border-radius:2px;}")
        container = QWidget(); container.setStyleSheet("background:#030810;")
        cv = QVBoxLayout(container)
        cv.setContentsMargins(36, 28, 36, 28)
        cv.setSpacing(32)

        BOOK_COLORS = [
            "#1A3A5C","#3C1A1A","#1A3C1A","#3C2E1A","#1E1A3C",
            "#3C1A30","#1A3A3A","#2E2E1A","#2A1A3C","#1A2E3C",
        ]

        # ── Library section ─────────────────────────────────────────────────
        lib_hdr = QLabel("LIBRARY  —  BOOKS READ")
        lib_hdr.setStyleSheet(f"color:#1E3050;font-size:9px;letter-spacing:3px;")
        cv.addWidget(lib_hdr)

        if not books:
            cv.addWidget(lbl("No books read yet — start reading in Legion.", MUTED, 12))
        else:
            SHELF_SIZE = 16
            for shelf_start in range(0, len(books), SHELF_SIZE):
                shelf_w = QWidget()
                shelf_w.setStyleSheet(
                    "background:transparent;border-bottom:3px solid #0A1520;")
                sh = QHBoxLayout(shelf_w)
                sh.setContentsMargins(0, 4, 0, 0); sh.setSpacing(5)
                batch = books[shelf_start:shelf_start + SHELF_SIZE]
                for i, (title, ch_read) in enumerate(batch):
                    color = BOOK_COLORS[(shelf_start + i) % len(BOOK_COLORS)]
                    height = 60 + (i % 5) * 8
                    spine = QLabel(title[:18])
                    spine.setFixedSize(26, height)
                    spine.setStyleSheet(
                        f"background:{color};color:rgba(255,255,255,0.55);"
                        f"font-size:8px;padding:2px;border-radius:1px 0 0 1px;")
                    spine.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)
                    spine.setToolTip(f"{title}\n{ch_read} chapters read")
                    sh.addWidget(spine)
                sh.addStretch()
                cv.addWidget(shelf_w)

        # ── Viewing room section ─────────────────────────────────────────────
        cv.addSpacing(8)
        view_hdr = QLabel("VIEWING ROOM  —  SHOWS WATCHED")
        view_hdr.setStyleSheet(f"color:#1E3050;font-size:9px;letter-spacing:3px;")
        cv.addWidget(view_hdr)

        if not shows:
            cv.addWidget(lbl("No shows tracked yet — add shows in Matrix.", MUTED, 12))
        else:
            POSTER_COLORS = [
                ("#0D1E3A","#4A9EE0"),("#1A0D26","#9A70E0"),("#0D1A0D","#4FC4A0"),
                ("#2A0D0D","#E05C6A"),("#1A1A0D","#E8C97A"),("#0D1A1A","#00E5CC"),
            ]
            ICONS = ["🎬","📺","🎭","🎌","🎪","⚔️","🔮","🌌"]
            wall_w = QWidget(); wall_w.setStyleSheet("background:transparent;")
            wl = QHBoxLayout(wall_w)
            wl.setSpacing(12)
            wl.setContentsMargins(0,0,0,0)
            for i, (title, eps) in enumerate(shows):
                bg1, bg2 = POSTER_COLORS[i % len(POSTER_COLORS)]
                icon = ICONS[i % len(ICONS)]
                card = QFrame()
                card.setFixedSize(70, 105)
                card.setStyleSheet(
                    f"QFrame{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                    f"stop:0 {bg1},stop:1 {bg2});border-radius:3px;}}")
                card.setToolTip(f"{title}\n{eps} episodes watched")
                cl = QVBoxLayout(card)
                cl.setContentsMargins(4,6,4,4)
                cl.setSpacing(2)
                ico_lbl = QLabel(icon)
                ico_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                ico_lbl.setStyleSheet("font-size:22px;background:transparent;")
                t_lbl = QLabel(title[:20])
                t_lbl.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)
                t_lbl.setWordWrap(True)
                t_lbl.setStyleSheet("font-size:8px;color:rgba(255,255,255,0.75);background:transparent;")
                cl.addWidget(ico_lbl, 1)
                cl.addWidget(t_lbl)
                wl.addWidget(card)
            wl.addStretch()
            cv.addWidget(wall_w)

        cv.addStretch()
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

    def _load_scene(self):
        import json as _json
        ld = legion_data()
        md = matrix_data()
        books = [
            {"title": n, "info": f"{b.get('chapters_read',0)} chapters read"}
            for n,b in ld.get("books",{}).items() if b.get("chapters_read",0) > 0
        ]
        shows = []
        for key,info in md.get("watching",{}).items():
            if isinstance(info, dict):
                ep = info.get("episodes_watched",[])
                shows.append({"title": info.get("title",key),
                               "info": f"{len(ep)} episodes watched"})
            else:
                shows.append({"title": key, "info": "In watchlist"})

        html = _MEMORY_PALACE_HTML \
            .replace("__BOOKS__", _json.dumps(books)) \
            .replace("__SHOWS__",  _json.dumps(shows))
        self._view.setHtml(html, QUrl("http://localhost/"))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)


# WATCHFACE / AMBIENT MODE
# ═══════════════════════════════════════════════════════════════════════════════

class WatchfaceWindow(QWidget):
    """Full-screen ambient display — now reading, now watching, Sage daily pick, clock."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Great Sage — Ambient")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setStyleSheet(f"background:{BG};")
        self._build()
        self._refresh()
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start(1000)
        self._data_timer = QTimer(self)
        self._data_timer.timeout.connect(self._refresh)
        self._data_timer.start(30000)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(80, 60, 80, 60)
        root.setSpacing(0)

        # Clock
        self._clock_lbl = QLabel()
        self._clock_lbl.setStyleSheet(
            f"font-family:{FONT_DISPLAY}; font-size:96px; font-weight:bold;"
            f"color:{TEXT}; letter-spacing:4px;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self._clock_lbl)

        self._date_lbl = QLabel()
        self._date_lbl.setStyleSheet(f"color:{MUTED}; font-size:18px; letter-spacing:3px;")
        root.addWidget(self._date_lbl)
        root.addSpacing(60)

        # Reading now
        row1 = QHBoxLayout()
        row1.setSpacing(32)
        ico1 = QLabel("◈"); ico1.setStyleSheet(f"color:{ACCENT}; font-size:36px;")
        col1 = QVBoxLayout()
        col1.setSpacing(4)
        self._reading_title = QLabel("—")
        self._reading_title.setStyleSheet(
            f"font-family:{FONT_DISPLAY}; font-size:22px; color:{TEXT}; letter-spacing:1px;")
        self._reading_sub = QLabel("READING")
        self._reading_sub.setStyleSheet(f"color:{MUTED}; font-size:10px; letter-spacing:3px;")
        col1.addWidget(self._reading_sub); col1.addWidget(self._reading_title)
        row1.addWidget(ico1); row1.addLayout(col1); row1.addStretch()
        root.addLayout(row1)
        root.addSpacing(32)

        # Watching now
        row2 = QHBoxLayout()
        row2.setSpacing(32)
        ico2 = QLabel("◎"); ico2.setStyleSheet(f"color:{BLUE}; font-size:36px;")
        col2 = QVBoxLayout()
        col2.setSpacing(4)
        self._watching_title = QLabel("—")
        self._watching_title.setStyleSheet(
            f"font-family:{FONT_DISPLAY}; font-size:22px; color:{TEXT}; letter-spacing:1px;")
        self._watching_sub = QLabel("WATCHING")
        self._watching_sub.setStyleSheet(f"color:{MUTED}; font-size:10px; letter-spacing:3px;")
        col2.addWidget(self._watching_sub); col2.addWidget(self._watching_title)
        row2.addWidget(ico2); row2.addLayout(col2); row2.addStretch()
        root.addLayout(row2)
        root.addSpacing(32)

        # Sage pick
        row3 = QHBoxLayout()
        row3.setSpacing(32)
        ico3 = QLabel("✦"); ico3.setStyleSheet(f"color:{PURPLE}; font-size:36px;")
        col3 = QVBoxLayout()
        col3.setSpacing(4)
        self._sage_pick = QLabel("Ask Sage for a pick →")
        self._sage_pick.setStyleSheet(
            f"font-family:{FONT_DISPLAY}; font-size:22px; color:{TEXT}; letter-spacing:1px;")
        self._sage_sub = QLabel("SAGE DAILY PICK")
        self._sage_sub.setStyleSheet(f"color:{MUTED}; font-size:10px; letter-spacing:3px;")
        col3.addWidget(self._sage_sub); col3.addWidget(self._sage_pick)
        row3.addWidget(ico3); row3.addLayout(col3); row3.addStretch()
        root.addLayout(row3)

        root.addStretch()

        # Close hint
        hint = QLabel("Press  Esc  or  Ctrl+W  to exit ambient mode")
        hint.setStyleSheet(f"color:{MUTED}; font-size:11px; letter-spacing:2px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignRight)
        root.addWidget(hint)

        self._tick_clock()

    def _tick_clock(self):
        import datetime as _dt
        now = _dt.datetime.now()
        self._clock_lbl.setText(now.strftime("%H:%M:%S"))
        self._date_lbl.setText(now.strftime("%A, %d %B %Y").upper())

    def _refresh(self):
        ld = legion_data()
        md = matrix_data()
        books    = ld.get("books", {})
        watching = md.get("watching", {})
        reading  = [(n, b.get("current_chapter", 0))
                    for n, b in books.items() if b.get("chapters_read", 0) > 0]
        if reading:
            n, ch = reading[-1]
            self._reading_title.setText(f"{n[:50]}  ·  Ch.{ch}")
        else:
            self._reading_title.setText("Nothing reading right now")

        if watching:
            sk   = list(watching.keys())[-1]
            info = watching[sk]
            t    = info.get("title", sk) if isinstance(info, dict) else sk
            ep   = info.get("current_episode", 0) if isinstance(info, dict) else 0
            self._watching_title.setText(f"{t[:50]}" + (f"  ·  Ep.{ep}" if ep else ""))
        else:
            self._watching_title.setText("Nothing watching right now")

    def update_sage_pick(self, text: str):
        """Call this after Sage generates a quick pick."""
        import re as _re
        m = _re.search(r'\d+[.)]\s*\*{0,2}(.+?)\*{0,2}\s*[-—]', text)
        pick = m.group(1).strip() if m else text.split("\n")[0][:60]
        self._sage_pick.setText(pick)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()
        elif e.key() == Qt.Key.Key_W and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.close()
        else:
            super().keyPressEvent(e)



# ── Auto-sync worker: checks for new chapters on launch + every 6 hours ───────

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN WINDOW
# ═══════════════════════════════════════════════════════════════════════════════

