"""
now_playing.py — Great Sage Plugin
CarPlay-style now playing card with large album art.
Slot: dashboard_below_cards (sits under the clock)
"""

PLUGIN_NAME        = "Now Playing"
PLUGIN_ICON        = "▶"
PLUGIN_DESCRIPTION = "Media player card with album art — sits under the clock"
PLUGIN_VERSION     = "1.5"
PLUGIN_AUTHOR      = "Great Sage"
PLUGIN_COLOR       = "#4EC9A4"

import subprocess, threading, shutil, os, urllib.request
from PyQt6.QtCore    import Qt, QTimer, QObject, pyqtSignal, QRectF, QPointF
from PyQt6.QtGui     import (QColor, QPainter, QLinearGradient, QRadialGradient,
                              QBrush, QPen, QPixmap, QImage, QPainterPath,
                              QConicalGradient)
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QPushButton, QSizePolicy)

# ── playerctl ─────────────────────────────────────────────────────────────────

def _binary():
    b = shutil.which("playerctl")
    if b: return b
    for p in ("/usr/bin/playerctl", "/usr/local/bin/playerctl", "/bin/playerctl"):
        if os.path.exists(p): return p
    return None

def _pc(*args, player=None):
    b = _binary()
    if not b: return ""
    cmd = [b]
    if player: cmd += ["-p", player]
    cmd += list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        return r.stdout.strip()
    except Exception:
        return ""

def _fmt(s):
    s = max(0, int(s))
    return f"{s//60}:{s%60:02d}"

def _state(last_player: str = ""):
    b = _binary()
    if not b:
        return {"ok": False}
    try:
        raw = subprocess.run([b, "--list-all"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        return {"ok": True, "active": False}
    if not raw:
        return {"ok": True, "active": False}

    players = [p.strip() for p in raw.splitlines() if p.strip()]
    PRIORITY = ["spotify", "mpv", "vlc", "rhythmbox", "firefox"]

    def _rank(p):
        for i, pref in enumerate(PRIORITY):
            if pref in p.lower(): return i
        return len(PRIORITY)

    playing, paused = [], []
    for p in players:
        try:
            st = subprocess.run([b, "-p", p, "status"],
                                capture_output=True, text=True,
                                timeout=2).stdout.strip()
            if st == "Playing": playing.append(p)
            elif st == "Paused": paused.append(p)
        except Exception:
            pass

    def _rank_paused(p):
        if last_player and last_player.lower() in p.lower():
            return -1
        return _rank(p)

    candidates = sorted(playing, key=_rank) or sorted(paused, key=_rank_paused)
    if not candidates:
        return {"ok": True, "active": False}

    for p in candidates:
        try:
            # Single --format call for all text metadata (same as lyrics plugin)
            r = subprocess.run(
                [b, "-p", p, "--format",
                 "{{xesam:title}}|||{{xesam:artist}}|||{{xesam:album}}|||"
                 "{{mpris:artUrl}}|||{{mpris:length}}",
                 "metadata"],
                capture_output=True, text=True, timeout=3)
            parts = r.stdout.strip().split("|||")
            # Parse from end — Firefox omits album creating extra empty splits
            # length is always last, art_url always second-to-last
            raw_len = parts[-1].strip() if len(parts) >= 1 else ""
            art_url = parts[-2].strip() if len(parts) >= 2 else ""
            title   = parts[0].strip() if len(parts) >= 1 else ""
            artist  = parts[1].strip() if len(parts) >= 2 else ""
            album   = parts[-3].strip() if len(parts) >= 3 else ""

            # Get status and position separately (reliable on all players)
            status = subprocess.run([b, "-p", p, "status"],
                                    capture_output=True, text=True,
                                    timeout=2).stdout.strip()
            pos_raw = subprocess.run([b, "-p", p, "position"],
                                     capture_output=True, text=True,
                                     timeout=2).stdout.strip()

            if not title and not status:
                continue

            dur_s = 0.0
            try:
                if raw_len and raw_len != "0":
                    v = float(raw_len)
                    dur_s = v / 1_000_000 if v > 1000 else v
            except Exception:
                pass

            pos_s = 0.0
            try:
                if pos_raw and pos_raw != "0":
                    pos_s = float(pos_raw)
                    if dur_s > 0 and pos_s > dur_s * 1.5:
                        pos_s = pos_s / 1_000_000
                    # Cap at duration — Firefox reports pos slightly > dur due to float precision
                    if dur_s > 0:
                        pos_s = min(pos_s, dur_s * 0.9999)
            except Exception:
                pass

            return {
                "ok":     True,
                "active": True,
                "playing": status == "Playing",
                "paused":  status == "Paused",
                "player":  p.split(".")[0].capitalize(),
                "title":   title  or "Unknown",
                "artist":  artist or "",
                "album":   album  or "",
                "art_url": art_url,
                "pos": pos_s,
                "dur": dur_s,
            }
        except Exception:
            continue

    return {"ok": True, "active": False}




def _fetch_art(url: str):
    if not url: return None
    try:
        if url.startswith(("https://", "http://")):
            req = urllib.request.Request(
                url, headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = r.read()
            img = QImage(); img.loadFromData(data)
            return QPixmap.fromImage(img) if not img.isNull() else None
        elif url.startswith("file://"):
            px = QPixmap(url[7:])
            return px if not px.isNull() else None
    except Exception:
        pass
    return None


# ── Signal bridge ─────────────────────────────────────────────────────────────

class _Bridge(QObject):
    result_ready = pyqtSignal(object)
    art_ready    = pyqtSignal(object, str)


# ── Album art widget ──────────────────────────────────────────────────────────

class _ArtWidget(QWidget):
    """Large album art with rounded corners and a glow behind it."""
    def __init__(self, size=120, parent=None):
        super().__init__(parent)
        self._size = size
        self._px   = None
        self.setFixedSize(size, size)

    def set_pixmap(self, px):
        if px:
            self._px = px.scaled(
                self._size, self._size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
        else:
            self._px = None
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        S = self._size
        r = 12.0
        rect = QRectF(0, 0, S, S)
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)

        if self._px:
            p.setClipPath(path)
            pw, ph = self._px.width(), self._px.height()
            ox = (pw - S) // 2
            oy = (ph - S) // 2
            p.drawPixmap(0, 0, self._px, ox, oy, S, S)
            # Bottom gradient overlay for text contrast
            g = QLinearGradient(0, S * 0.5, 0, S)
            g.setColorAt(0, QColor(0, 0, 0, 0))
            g.setColorAt(1, QColor(0, 0, 0, 140))
            p.fillPath(path, QBrush(g))
            p.setClipping(False)
        else:
            p.fillPath(path, QBrush(QColor(18, 18, 28)))
            p.setPen(QPen(QColor(50, 50, 80)))
            p.setClipPath(path)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "♪")
            p.setClipping(False)

        # Subtle border
        p.setPen(QPen(QColor(255, 255, 255, 25), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), r, r)
        p.end()


# ── Main card widget ──────────────────────────────────────────────────────────


class NowPlayingWidget(QWidget):
    """
    CarPlay-style card:
    [ Art ]  Title
             Artist · Album
             [===progress===]  0:54 / 4:39
             ⏮  ▶  ⏭  ⏹    SPOTIFY
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cur_player  = None
        self._cur_art_url = ""
        self._bridge = _Bridge(self)
        self._bridge.result_ready.connect(self._apply)
        self._bridge.art_ready.connect(self._apply_art)
        self._full_title  = ""   # full title for scrolling
        self._scroll_pos  = 0    # current scroll offset
        self._scroll_pause= 0    # pause ticks
        self._scroll_timer = None
        self._last_title   = ""    # track change detection
        self._last_pos     = -1.0  # previous position for stale detection
        self._stale_count  = 0     # how many polls pos has been stuck

        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(2000)
        QTimer.singleShot(500, self._poll)

        self._scroll_timer = QTimer(self)
        self._scroll_timer.timeout.connect(self._scroll_title)
        self._scroll_timer.start(120)   # scroll every 120ms


    def _build(self):
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(210)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background:transparent;")

        # Outer wrapper centres a fixed-width card
        wrapper = QHBoxLayout(self)
        wrapper.setContentsMargins(48, 16, 48, 16)
        wrapper.setSpacing(0)

        # Card container — fixed width so progress bar doesn't stretch
        card = QWidget(self)
        card.setStyleSheet("background:transparent;")
        card.setFixedWidth(480)
        outer = QHBoxLayout(card)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(20)
        wrapper.addWidget(card)
        wrapper.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # ── Left: album art ───────────────────────────────────────────────────
        self._art = _ArtWidget(size=140, parent=self)
        outer.addWidget(self._art)

        # ── Right: info + controls ────────────────────────────────────────────
        right = QVBoxLayout()
        right.setSpacing(6)
        right.setContentsMargins(0, 4, 0, 4)

        # Source badge + title row
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.setContentsMargins(0, 0, 0, 0)

        self._src = QLabel("")
        self._src.setStyleSheet(
            "background:rgba(78,201,164,0.12);"
            "color:#4EC9A4;font-size:8px;letter-spacing:2px;"
            "padding:2px 7px;border-radius:8px;"
            "border:1px solid rgba(78,201,164,0.25);")
        self._src.setVisible(False)
        top_row.addWidget(self._src)
        top_row.addStretch()
        right.addLayout(top_row)

        # Title
        self._title = QLabel("Nothing playing")
        self._title.setStyleSheet(
            "background:transparent;color:#3A3A50;"
            "font-size:16px;font-weight:700;")
        right.addWidget(self._title)

        # Artist
        self._artist = QLabel("")
        self._artist.setStyleSheet(
            "background:transparent;color:#303050;"
            "font-size:12px;letter-spacing:0.5px;")
        right.addWidget(self._artist)

        right.addSpacing(2)



        right.addSpacing(4)

        # Controls row
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(10)
        ctrl_row.setContentsMargins(0, 0, 0, 0)
        ctrl_row.setAlignment(Qt.AlignmentFlag.AlignLeft |
                              Qt.AlignmentFlag.AlignVCenter)

        def _btn(icon, tip, cb, primary=False):
            b = QPushButton(icon)
            b.setToolTip(tip)
            b.clicked.connect(cb)
            if primary:
                b.setFixedSize(48, 48)
                b.setStyleSheet(
                    "QPushButton{background:rgba(78,201,164,0.22);"
                    "border:1px solid rgba(78,201,164,0.65);"
                    "color:#4EC9A4;font-size:18px;"
                    "border-radius:24px;padding:0;}"
                    "QPushButton:hover{background:rgba(78,201,164,0.35);"
                    "border-color:#4EC9A4;}"
                    "QPushButton:pressed{background:rgba(78,201,164,0.5);}")
            else:
                b.setFixedSize(36, 36)
                b.setStyleSheet(
                    "QPushButton{background:rgba(255,255,255,0.06);"
                    "border:1px solid rgba(255,255,255,0.12);"
                    "color:#8080B0;font-size:14px;"
                    "border-radius:18px;padding:0;}"
                    "QPushButton:hover{background:rgba(255,255,255,0.12);"
                    "color:#C0C0E0;border-color:rgba(255,255,255,0.25);}"
                    "QPushButton:pressed{background:rgba(255,255,255,0.18);}")
            return b

        self._prev_btn = _btn("⏮", "Previous",  lambda: self._cmd("previous"))
        self._play_btn = _btn("▶", "Play/Pause", lambda: self._cmd("play-pause"), True)
        self._next_btn = _btn("⏭", "Next",       lambda: self._cmd("next"))
        self._stop_btn = _btn("⏹", "Stop",       lambda: self._cmd("stop"))

        ctrl_row.addWidget(self._prev_btn)
        ctrl_row.addWidget(self._play_btn)
        ctrl_row.addWidget(self._next_btn)
        ctrl_row.addSpacing(4)
        ctrl_row.addWidget(self._stop_btn)
        ctrl_row.addStretch()
        right.addLayout(ctrl_row)

        outer.addLayout(right, 1)

    def _set_title(self, title: str):
        """Set the full title — only reset scroll if title changed."""
        if title == self._full_title:
            return  # same title, let scroll continue
        self._full_title  = title
        self._scroll_pos  = 0
        self._scroll_pause = 25   # pause 25 ticks (~3s) at start
        self._update_title_display()

    def _scroll_title(self):
        """Called every 120ms to advance the scroll."""
        if not self._full_title:
            return
        # Only scroll if title is long enough to need it (>35 chars roughly)
        if len(self._full_title) <= 20:
            return
        if self._scroll_pause > 0:
            self._scroll_pause -= 1
            return
        self._scroll_pos += 1
        # Padded title: original + spaces + original for seamless loop
        padded = self._full_title + "     "
        if self._scroll_pos >= len(padded):
            self._scroll_pos = 0
            self._scroll_pause = 15  # brief pause on loop
        self._update_title_display()

    def _update_title_display(self):
        if not self._full_title:
            return
        padded = self._full_title + "     " + self._full_title
        display = padded[self._scroll_pos:self._scroll_pos + 22]
        self._title.setText(display)

    def _poll(self):
        bridge  = self._bridge
        last_p  = self._cur_player or ""
        def _fetch():
            s = _state(last_player=last_p)
            bridge.result_ready.emit(s)
        threading.Thread(target=_fetch, daemon=True).start()

    def _cmd(self, action):
        p = self._cur_player
        threading.Thread(
            target=lambda: _pc(action, player=p),
            daemon=True).start()
        QTimer.singleShot(500, self._poll)

    def _apply(self, s):
        if not s.get("ok") or not s.get("active"):
            self._title.setStyleSheet(
                "background:transparent;color:#252535;font-size:16px;font-weight:700;")
            self._set_title("Nothing playing")
            self._artist.setText("")
            self._src.setVisible(False)
            self._play_btn.setText("▶")
            self._play_btn.setStyleSheet(
                "QPushButton{background:transparent;border:none;"
                "color:#202030;font-size:16px;"
                "border-radius:21px;padding:0;}"
                "QPushButton:hover{color:#4EC9A4;}")
            self._art.set_pixmap(None)
            return

        self._cur_player = s.get("player", "").lower()
        playing = s.get("playing", False)

        # Source badge
        player_name = s.get("player", "").upper()[:8]
        self._src.setText(player_name)
        self._src.setVisible(bool(player_name))

        # Play button
        self._play_btn.setText("⏸" if playing else "▶")
        if playing:
            self._play_btn.setStyleSheet(
                "QPushButton{background:rgba(78,201,164,0.22);"
                "border:1px solid rgba(78,201,164,0.65);"
                "color:#4EC9A4;font-size:18px;"
                "border-radius:24px;padding:0;}"
                "QPushButton:hover{background:rgba(78,201,164,0.30);}"
                "QPushButton:pressed{background:rgba(78,201,164,0.45);}")
        else:
            self._play_btn.setStyleSheet(
                "QPushButton{background:rgba(201,168,76,0.18);"
                "border:1px solid rgba(201,168,76,0.55);"
                "color:#C9A84C;font-size:18px;"
                "border-radius:24px;padding:0;}"
                "QPushButton:hover{background:rgba(201,168,76,0.25);}")

        title  = s.get("title",  "")
        artist = s.get("artist", "")
        album  = s.get("album",  "")

        self._title.setStyleSheet(
            "background:transparent;color:#C8C4BE;font-size:16px;font-weight:700;")
        self._set_title(title)

        sub = artist
        if album and album != title and album != artist:
            sub = f"{artist}  ·  {album}" if artist else album
        self._artist.setStyleSheet(
            "background:transparent;color:#5050A0;"
            "font-size:12px;letter-spacing:0.5px;")
        self._artist.setText(sub[:50])



        # Fetch art if URL changed
        art_url = s.get("art_url", "")
        if art_url and art_url != self._cur_art_url:
            self._cur_art_url = art_url
            bridge = self._bridge
            threading.Thread(
                target=lambda u=art_url: bridge.art_ready.emit(_fetch_art(u), u),
                daemon=True).start()
        elif not art_url:
            self._art.set_pixmap(None)

    def _apply_art(self, px, url):
        if url == self._cur_art_url:
            self._art.set_pixmap(px)


# ── Plugin ────────────────────────────────────────────────────────────────────

_slot_widget = None

def build_page(parent, api):
    global _slot_widget
    colours = api.colours

    page = QWidget()
    page.setStyleSheet(f"background:{colours['BG']};")
    v = QVBoxLayout(page)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)

    # Header
    hdr = QWidget()
    hdr.setFixedHeight(52)
    hdr.setStyleSheet(
        f"background:{colours['BG2']};"
        f"border-bottom:1px solid {colours['BORDER']};")
    hh = QHBoxLayout(hdr); hh.setContentsMargins(28, 0, 28, 0)
    tl = QLabel("▶  NOW PLAYING")
    tl.setStyleSheet(
        f"color:{colours['ACCENT']};font-size:14px;"
        f"font-weight:bold;letter-spacing:3px;")
    hh.addWidget(tl); hh.addStretch()
    v.addWidget(hdr)

    body = QWidget()
    body.setStyleSheet(f"background:{colours['BG']};")
    bv = QVBoxLayout(body)
    bv.setContentsMargins(32, 28, 32, 28)
    bv.setSpacing(16)

    has = bool(_binary())
    st = QLabel("● playerctl found — album art + controls active" if has
                else "⚠  playerctl not installed — sudo apt install playerctl")
    st.setStyleSheet(
        f"color:{colours['ACCENT2'] if has else '#E05A6A'};font-size:12px;")
    bv.addWidget(st)

    desc = QLabel(
        "Large card under the clock with album art, track info, "
        "progress bar, and controls.\n"
        "Works with Spotify, mpv, VLC, and any MPRIS player.")
    desc.setStyleSheet(f"color:{colours['TEXT2']};font-size:13px;")
    desc.setWordWrap(True)
    bv.addWidget(desc)

    frame = QWidget()
    frame.setStyleSheet(
        f"background:{colours['BG2']};"
        f"border:1px solid {colours['BORDER']};border-radius:8px;")
    fv = QVBoxLayout(frame)
    fv.setContentsMargins(0, 0, 0, 0)
    fv.addWidget(NowPlayingWidget())
    bv.addWidget(frame)
    bv.addStretch()
    v.addWidget(body, 1)

    _slot_widget = NowPlayingWidget()
    api.register_slot("dashboard_below_cards", _slot_widget)
    return page


def refresh(page):
    pass


