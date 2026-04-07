"""
lyrics.py — Great Sage Plugin
Synced lyrics display that highlights the current line based on playback position.
Fetches from lrclib.net (free, no API key). Caches per track.
Slot: dashboard_below_cards
"""

PLUGIN_NAME        = "Lyrics"
PLUGIN_ICON        = "♪"
PLUGIN_DESCRIPTION = "Synced lyrics with current line highlighted — sits under Now Playing"
PLUGIN_VERSION     = "1.0"
PLUGIN_AUTHOR      = "Great Sage"
PLUGIN_COLOR       = "#8B6FD4"

import subprocess, threading, shutil, os, json, re, urllib.request, urllib.parse
from PyQt6.QtCore    import Qt, QTimer, QObject, pyqtSignal, QRectF
from PyQt6.QtGui     import (QColor, QPainter, QLinearGradient, QBrush,
                              QPen, QFont, QFontMetrics)
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QScrollArea, QSizePolicy, QFrame,
                              QScrollBar)

# ── playerctl helpers ─────────────────────────────────────────────────────────

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

def _get_track(last_player=""):
    """Return track info for active player, preferring last_player when paused."""
    b = _binary()
    if not b: return None
    try:
        raw = subprocess.run([b, "--list-all"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        return None
    if not raw: return None

    players = [p.strip() for p in raw.splitlines() if p.strip()]
    PRIORITY = ["spotify", "mpv", "vlc", "rhythmbox", "firefox"]
    def _rank(p):
        for i, pref in enumerate(PRIORITY):
            if pref in p.lower(): return i
        return len(PRIORITY)

    # Playing first, then paused
    playing, paused = [], []
    for p in players:
        try:
            st = subprocess.run([b, "-p", p, "status"],
                                capture_output=True, text=True, timeout=2).stdout.strip()
            if st == "Playing": playing.append(p)
            elif st == "Paused": paused.append(p)
        except Exception:
            pass

    def _rank_paused(p):
        if last_player and last_player.lower() in p.lower():
            return -1
        return _rank(p)

    candidates = sorted(playing, key=_rank) or sorted(paused, key=_rank_paused)
    if not candidates: return None

    for p in candidates:
        try:
            r = subprocess.run(
                [b, "-p", p, "--format",
                 "{{xesam:title}}|||{{xesam:artist}}|||{{position}}|||{{status}}|||{{mpris:length}}",
                 "metadata"],
                capture_output=True, text=True, timeout=3)
            parts = r.stdout.strip().split("|||")
            if len(parts) < 2: continue
            title   = parts[0].strip()
            artist  = parts[1].strip()
            status  = parts[3].strip() if len(parts) > 3 else ""
            raw_len = parts[4].strip() if len(parts) > 4 else ""
            dur = 0.0
            try:
                if raw_len and raw_len != "0":
                    v = float(raw_len)
                    dur = v / 1_000_000 if v > 1000 else v
            except Exception:
                pass
            pos    = 0.0
            try:
                pv2 = subprocess.run([b, "-p", p, "position"],
                                     capture_output=True, text=True,
                                     timeout=2).stdout.strip()
                if pv2 and pv2 != "0":
                    pos = float(pv2)
            except Exception:
                pass
            if title:
                return {"title": title, "artist": artist,
                        "pos": pos, "dur": dur, "status": status, "player": p}
        except Exception:
            continue
    return None


# ── lrclib.net fetcher ────────────────────────────────────────────────────────

_lyrics_cache: dict = {}   # key: "artist|||title" → parsed lines list or None

def _clean_title(title, artist):
    """
    Clean YouTube-style titles and channel names for lyrics lookup.
    Handles: "Lady Gaga - Bloody Mary (Official Audio)" played via LadyGagaVEVO
    """
    import re

    # Clean the title
    clean = title
    patterns = [
        r"\s*\(Official\s*(Audio|Video|Music\s*Video|Lyric\s*Video|HD|4K|Visualizer)[^)]*\)",
        r"\s*\[Official\s*(Audio|Video|Music\s*Video)[^\]]*\]",
        r"\s*\(Lyrics?\s*(Video)?\)",  r"\s*\[Lyrics?\s*(Video)?\]",
        r"\s*\(Explicit\)",            r"\s*\[Explicit\]",
        r"\s*\(feat\.?\s*[^)]+\)",     r"\s*\[feat\.?\s*[^\]]+\]",
        r"\s*\(ft\.?\s*[^)]+\)",       r"\s*\(Prod\.?\s*[^)]+\)",
        r"\s*\(Remastered[^)]*\)",     r"\s*\(Remix[^)]*\)",
        r"\s*\(Live[^)]*\)",           r"\s*\(Acoustic[^)]*\)",
        r"\s*\|\s*.+$",                r"\s*-\s*Topic\s*$",
        r"\s*//\s*.+$",
    ]
    for pat in patterns:
        clean = re.sub(pat, "", clean, flags=re.IGNORECASE)
    clean = clean.strip(" -–—|")

    # Clean the artist — YouTube channel names like "LadyGagaVEVO", "TaylorSwiftVEVO"
    clean_artist = re.sub(r"VEVO$", "", artist, flags=re.IGNORECASE).strip()
    clean_artist = re.sub(r"Official$", "", clean_artist, flags=re.IGNORECASE).strip()
    clean_artist = re.sub(r"Music$", "", clean_artist, flags=re.IGNORECASE).strip()
    # "TaylorSwift" -> "Taylor Swift" (insert space before uppercase in camelCase)
    clean_artist = re.sub(r"([a-z])([A-Z])", r"\1 \2", clean_artist)

    # If title contains " - ", split into artist/title
    if " - " in clean:
        left, right = clean.split(" - ", 1)
        left = left.strip()
        right = right.strip()
        if not clean_artist or clean_artist.lower() in ("unknown", ""):
            return left, right
        # Check if left part matches artist (fuzzy)
        la = left.lower().replace(" ", "")
        ca = clean_artist.lower().replace(" ", "")
        if la == ca or la in ca or ca in la:
            return clean_artist, right
        return clean_artist, right  # always prefer split title

    return clean_artist, clean.strip()



def _fetch_lyrics(artist: str, title: str) -> list | None:
    """
    Fetch synced lyrics from lrclib.net.
    Returns list of (timestamp_seconds, line_text) tuples, sorted by time.
    Returns empty list if only plain lyrics available.
    Returns None on failure.
    """
    cache_key = f"{artist.lower()}|||{title.lower()}"
    if cache_key in _lyrics_cache:
        return _lyrics_cache[cache_key]

    def _search(a, t):
        """Single lrclib search, returns best result or None."""
        try:
            params = urllib.parse.urlencode({"artist_name": a, "track_name": t})
            url = f"https://lrclib.net/api/search?{params}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "GreatSage/1.0"
            })
            with urllib.request.urlopen(req, timeout=8) as r:
                results = json.loads(r.read())
            if not results:
                return None
            # Prefer synced, then plain
            for item in results:
                if item.get("syncedLyrics"):
                    return item
            for item in results:
                if item.get("plainLyrics"):
                    return item
        except urllib.error.URLError as e:
            # Network unreachable / timeout — signal with a sentinel
            return {"_network_error": str(e)}
        except Exception:
            pass
        return None

    # Try 1: exact artist + title
    best = _search(artist, title)

    # Bail early on network error without caching (so next poll retries)
    if isinstance(best, dict) and "_network_error" in best:
        return None

    # Try 2: title only (handles channel name mismatches)
    if not best:
        best = _search("", title)
        if isinstance(best, dict) and "_network_error" in best:
            return None

    # Try 3: strip featuring from title and retry
    if not best:
        import re
        t2 = re.sub(r"\s*(feat|ft)\.?\s+[^(\[]+", "", title, flags=re.IGNORECASE).strip()
        if t2 != title:
            best = _search(artist, t2)
            if isinstance(best, dict) and "_network_error" in best:
                return None

    if not best:
        _lyrics_cache[cache_key] = []
        return []

    synced = best.get("syncedLyrics", "")
    if synced:
        lines = _parse_lrc(synced)
        _lyrics_cache[cache_key] = lines
        return lines

    plain = best.get("plainLyrics", "").strip()
    if plain:
        lines = [(None, l) for l in plain.splitlines()]
        _lyrics_cache[cache_key] = lines
        return lines

    _lyrics_cache[cache_key] = []
    return []


def _parse_lrc(lrc: str) -> list:
    """Parse LRC format into list of (seconds_float, text) tuples."""
    lines = []
    pattern = re.compile(r'\[(\d+):(\d+)\.(\d+)\](.*)')
    for line in lrc.splitlines():
        m = pattern.match(line.strip())
        if m:
            mins     = int(m.group(1))
            secs     = int(m.group(2))
            frac_str = m.group(3)
            text     = m.group(4).strip()
            # Normalise fraction to seconds regardless of digit count:
            # [mm:ss.xx]  → xx/100   [mm:ss.xxx] → xxx/1000
            frac = int(frac_str) / (10 ** len(frac_str))
            ts   = mins * 60 + secs + frac
            lines.append((ts, text))
    lines.sort(key=lambda x: x[0])
    return lines


def _current_line_index(lines: list, pos: float) -> int:
    """Return index of the current line given playback position."""
    if not lines: return 0
    idx = 0
    for i, (ts, _) in enumerate(lines):
        if ts is None: continue
        if ts <= pos:
            idx = i
        else:
            break
    return idx


# ── Signal bridge ─────────────────────────────────────────────────────────────

class _Bridge(QObject):
    update_ready = pyqtSignal(object, object, float, str)  # track, lines, pos, status


# ── Lyrics line widget ────────────────────────────────────────────────────────

class _LyricLine(QLabel):
    """A single lyric line with distance-based styling."""
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.set_state("dim")

    def set_active(self, active: bool):
        self.set_state("active" if active else "dim")

    def set_state(self, state: str):
        styles = {
            "active": ("background:transparent;color:#F0EDE8;"
                       "font-size:18px;font-weight:700;"
                       "letter-spacing:0.3px;padding:4px 0;"),
            "prev1":  ("background:transparent;color:#8080A8;"
                       "font-size:14px;font-weight:400;padding:3px 0;"),
            "next1":  ("background:transparent;color:#606088;"
                       "font-size:14px;font-weight:400;padding:3px 0;"),
            "prev2":  ("background:transparent;color:#404060;"
                       "font-size:13px;font-weight:400;padding:2px 0;"),
            "next2":  ("background:transparent;color:#363555;"
                       "font-size:13px;font-weight:400;padding:2px 0;"),
            "dim":    ("background:transparent;color:#252540;"
                       "font-size:13px;font-weight:400;padding:2px 0;"),
        }
        self.setStyleSheet(styles.get(state, styles["dim"]))


# ── Main lyrics widget ────────────────────────────────────────────────────────

class LyricsWidget(QWidget):
    HEIGHT = 220

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bridge       = _Bridge(self)
        self._bridge.update_ready.connect(self._apply)
        self._cur_key      = ""
        self._last_player  = ""
        self._last_status  = ""
        self._first_load   = False
        self._lines        = []        # list of (ts, text)
        self._line_widgets = []        # list of _LyricLine
        self._cur_idx      = -1
        self._is_synced    = False
        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(1000)        # 1s — tighter for line sync
        QTimer.singleShot(800, self._poll)

    def _build(self):
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background:transparent;")

        root = QVBoxLayout(self)
        root.setContentsMargins(48, 12, 48, 12)
        root.setSpacing(0)

        # Scroll area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea{border:none;background:transparent;}"
            "QWidget{background:transparent;}")

        self._content = QWidget()
        self._content.setStyleSheet("background:transparent;")
        self._cv = QVBoxLayout(self._content)
        self._cv.setContentsMargins(0, 8, 0, 80)
        self._cv.setSpacing(8)
        self._cv.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._status_lbl = QLabel("♪  Play something to see lyrics")
        self._status_lbl.setStyleSheet(
            "background:transparent;color:#252540;"
            "font-size:13px;letter-spacing:0.5px;")
        self._cv.addWidget(self._status_lbl)

        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll, 1)

    def _poll(self):
        bridge      = self._bridge
        last_player = self._last_player
        cur_status  = getattr(self, "_last_status", "")

        # Back off when nothing is happening — avoids hammering playerctl
        # Playing → 1s, Paused → 4s, nothing playing → 10s
        if cur_status == "Paused":
            next_interval = 4000
        elif cur_status == "":
            next_interval = 10000
        else:
            next_interval = 1000

        if self._timer.interval() != next_interval:
            self._timer.setInterval(next_interval)

        def _fetch():
            track = _get_track(last_player=last_player)
            if not track:
                bridge.update_ready.emit(None, None, 0.0, "")
                return
            key = f"{track['artist']}|||{track['title']}"
            cl_artist, cl_title = _clean_title(track["title"], track["artist"])
            track["clean_title"] = cl_title
            lines = _fetch_lyrics(cl_artist, cl_title)
            bridge.update_ready.emit(track, lines, track["pos"], track["status"])
        threading.Thread(target=_fetch, daemon=True).start()

    def _apply(self, track, lines, pos: float, status: str):
        if track is None:
            self._show_status("♪  Play something to see lyrics")
            return
        # lines=None means re-use existing lines (called from delayed highlight)
        if lines is None:
            lines = self._lines if self._lines else []

        key = f"{track['artist']}|||{track['title']}"

        # Remember which player delivered this track
        if track.get("player"):
            self._last_player = track["player"]
        self._last_status = status

        # Firefox stale position fix: if pos > 95% of dur, treat as 0
        dur = track.get("dur", 0)
        if dur > 0 and pos > dur * 0.95:
            pos = 0.0

        # New track — rebuild line widgets
        if key != self._cur_key:
            self._cur_key    = key
            self._cur_idx    = -1
            self._lines      = lines or []
            self._is_synced  = bool(lines and any(ts is not None for ts, _ in lines))
            self._first_load = True
            self._rebuild_lines()

        if not self._lines:
            if lines is None:
                # None means network error (not cached) — don't say "not found"
                self._show_status("♪  Could not reach lrclib.net")
            else:
                self._show_status(
                    f"♪  No lyrics found for\n{track.get('clean_title') or track['title']}")
            return

        if not self._is_synced:
            return

        # On first load, delay highlight until widgets are painted & pos is fresh
        if self._first_load:
            self._first_load = False
            last_p = self._last_player
            bridge = self._bridge
            def _delayed_highlight():
                import threading
                def _fetch():
                    fresh = _get_track(last_player=last_p)
                    if not fresh:
                        return
                    fp  = fresh.get("pos", 0)
                    fd  = fresh.get("dur", 0)
                    fst = fresh.get("status", "")
                    if fd > 0 and fp > fd * 0.95:
                        fp = 0.0
                    bridge.update_ready.emit(fresh, None, fp, fst)
                threading.Thread(target=_fetch, daemon=True).start()
            QTimer.singleShot(1000, _delayed_highlight)
            return

        # Synced — find and highlight current line
        new_idx = _current_line_index(self._lines, pos)
        if new_idx != self._cur_idx or status == "Playing":
            old_idx_val = self._cur_idx
            self._cur_idx = new_idx
            # Update line styles with distance-based fading
            for i, lw in enumerate(self._line_widgets):
                if lw is None:
                    continue
                dist = i - new_idx
                if dist == 0:
                    lw.set_state("active")
                elif dist == -1:
                    lw.set_state("prev1")
                elif dist == 1:
                    lw.set_state("next1")
                elif dist == -2:
                    lw.set_state("prev2")
                elif dist == 2:
                    lw.set_state("next2")
                else:
                    lw.set_state("dim")
            if old_idx_val != new_idx:
                self._scroll_to(new_idx)

    def _rebuild_lines(self):
        # Clear
        while self._cv.count():
            item = self._cv.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._line_widgets = []
        # Note: do NOT null _status_lbl here — _show_status manages its own label

        if not self._lines:
            lbl = QLabel("♪  No lyrics found")
            lbl.setStyleSheet(
                "background:transparent;color:#252540;"
                "font-size:13px;")
            self._cv.addWidget(lbl)
            return

        for ts, text in self._lines:
            if not text.strip():
                # Empty line — add spacer
                spacer = QLabel("")
                spacer.setFixedHeight(8)
                spacer.setStyleSheet("background:transparent;")
                self._cv.addWidget(spacer)
                self._line_widgets.append(None)
            else:
                lw = _LyricLine(text)
                self._cv.addWidget(lw)
                self._line_widgets.append(lw)

    def _scroll_to(self, idx: int):
        """Scroll to centre the active line in the view."""
        # Find the target widget
        lw = None
        for i in range(idx, min(idx + 3, len(self._line_widgets))):
            if i < len(self._line_widgets) and self._line_widgets[i] is not None:
                lw = self._line_widgets[i]
                break
        if lw is None:
            return

        def _do_scroll():
            try:
                pos_y = lw.mapTo(self._content, lw.rect().topLeft()).y()
                lw_h  = max(lw.height(), 20)
                # Centre active line in view
                target = max(0, pos_y - (self.HEIGHT // 2) + lw_h // 2)
                self._scroll.verticalScrollBar().setValue(target)
            except Exception:
                pass

        # Fire immediately and again after 50ms in case first fires before paint
        QTimer.singleShot(0,  _do_scroll)
        QTimer.singleShot(50, _do_scroll)

    def _show_status(self, msg: str):
        # Clear line widgets and show message
        if self._lines:
            self._lines = []
            self._line_widgets = []
            while self._cv.count():
                item = self._cv.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
        # Always add a fresh label — avoids writing into stale/wrong children
        # after deleteLater() (which defers destruction, so findChildren is unreliable)
        lbl = QLabel(msg)
        lbl.setStyleSheet(
            "background:transparent;color:#252540;"
            "font-size:13px;letter-spacing:0.5px;")
        lbl.setWordWrap(True)
        self._cv.addWidget(lbl)


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

    hdr = QWidget()
    hdr.setFixedHeight(52)
    hdr.setStyleSheet(
        f"background:{colours['BG2']};"
        f"border-bottom:1px solid {colours['BORDER']};")
    hh = QHBoxLayout(hdr)
    hh.setContentsMargins(28, 0, 28, 0)
    tl = QLabel("♪  LYRICS")
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
    st = QLabel(
        "● Synced lyrics from lrclib.net — current line highlighted"
        if has else "⚠  playerctl not installed — sudo apt install playerctl")
    st.setStyleSheet(
        f"color:{colours['ACCENT2'] if has else '#E05A6A'};font-size:12px;")
    bv.addWidget(st)

    desc = QLabel(
        "Displays synced lyrics below the Now Playing card.\n"
        "The current line scrolls and lights up as the song plays.\n"
        "Lyrics from lrclib.net — no account needed.")
    desc.setStyleSheet(f"color:{colours['TEXT2']};font-size:13px;")
    desc.setWordWrap(True)
    bv.addWidget(desc)

    note = QLabel(
        "💡  Lyrics are fetched once per track and cached for the session. "
        "If a song has no synced lyrics, plain lyrics are shown without highlighting.")
    note.setStyleSheet(
        f"background:{colours['BG2']};color:{colours['MUTED']};"
        f"font-size:11px;padding:12px;border-radius:4px;"
        f"border:1px solid {colours['BORDER']};")
    note.setWordWrap(True)
    bv.addWidget(note)
    bv.addStretch()
    v.addWidget(body, 1)

    _slot_widget = LyricsWidget()
    api.register_slot("dashboard_below_cards", _slot_widget)
    return page


def refresh(page):
    pass
