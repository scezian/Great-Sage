"""
lyrics.py — Great Sage Plugin
Synced lyrics display that highlights the current line based on playback position.
Fetches from lrclib.net (free, no API key). Caches per track.
Slot: dashboard_below_cards
"""

PLUGIN_NAME        = "Lyrics"
PLUGIN_ICON        = "♪"
PLUGIN_DESCRIPTION = "Synced lyrics with current line highlighted — fetches from LRCLIB and Netease"
PLUGIN_VERSION     = "1.0"
PLUGIN_AUTHOR      = "Great Sage"
PLUGIN_COLOR       = "#8B6FD4"

import subprocess, threading, shutil, os, json, re, urllib.request, urllib.parse, time
from PyQt6.QtCore    import Qt, QTimer, QObject, pyqtSignal, QRectF
from PyQt6.QtGui     import (QColor, QPainter, QLinearGradient, QBrush,
                              QPen, QFont, QFontMetrics)
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QScrollArea, QSizePolicy, QFrame,
                              QScrollBar)

# ── Precompiled title-cleaning patterns ──────────────────────────────────────

_CLEAN_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
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
]]

_CAMEL_RE    = re.compile(r"([a-z])([A-Z])")
_VEVO_RE     = re.compile(r"VEVO$", re.IGNORECASE)
_OFFICIAL_RE = re.compile(r"Official$", re.IGNORECASE)
_MUSIC_RE    = re.compile(r"Music$", re.IGNORECASE)
_FEAT_RE     = re.compile(r"\s*(feat|ft)\.?\s+[^(\[]+", re.IGNORECASE)

# ── Thread safety ─────────────────────────────────────────────────────────────

_cache_lock = threading.Lock()

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
            pos_broken = False
            try:
                pv2 = subprocess.run([b, "-p", p, "position"],
                                     capture_output=True, text=True,
                                     timeout=2).stdout.strip()
                if pv2 and pv2 != "0":
                    raw_pos = float(pv2)
                    raw_len_val = float(raw_len) if raw_len and raw_len != "0" else 0
                    if raw_len_val > 0 and _is_broken_position(raw_pos, raw_len_val):
                        pos_broken = True
                        pos = 0.0
                    elif dur > 0 and raw_pos > dur * 100:
                        pos = raw_pos / 1000.0
                    else:
                        pos = raw_pos
                    if not pos_broken and dur > 0:
                        pos = min(pos, dur)
            except Exception:
                pass
            if title:
                return {"title": title, "artist": artist,
                        "pos": pos, "dur": dur, "status": status,
                        "player": p, "pos_broken": pos_broken}
        except Exception:
            continue
    return None


# ── lrclib.net fetcher ────────────────────────────────────────────────────────

_lyrics_cache: dict = {}   # key: "artist|||title" → parsed lines list or None
_fetch_in_flight: set = set()  # keys currently being fetched
_CACHE_MAX = 200           # evict oldest when exceeded

# ── Wall-clock position tracker (Firefox/YouTube MPRIS position is broken) ────
_wc_track_key:    str   = ""
_wc_start_time:   float = 0.0
_wc_paused_at:    float = 0.0
_wc_paused_accum: float = 0.0

def _is_broken_position(raw_pos: float, raw_len: float) -> bool:
    """True when Firefox reports position ≈ length (frozen garbage value)."""
    if raw_len <= 0:
        return False
    expected = raw_len / 1000.0
    return abs(raw_pos - expected) < expected * 0.001

def _wc_get_pos(track_key: str, status: str) -> float:
    """Synthesize playback position in seconds using wall clock."""
    global _wc_track_key, _wc_start_time, _wc_paused_at, _wc_paused_accum
    now = time.time()
    if track_key != _wc_track_key:
        _wc_track_key    = track_key
        _wc_start_time   = now
        _wc_paused_at    = 0.0
        _wc_paused_accum = 0.0
        return 0.0
    if status == "Paused":
        if _wc_paused_at == 0.0:
            _wc_paused_at = now
        return (_wc_paused_at - _wc_start_time) - _wc_paused_accum
    else:
        if _wc_paused_at > 0.0:
            _wc_paused_accum += now - _wc_paused_at
            _wc_paused_at = 0.0
        return (now - _wc_start_time) - _wc_paused_accum

def _clean_title(title, artist):
    """
    Clean YouTube-style titles and channel names for lyrics lookup.
    Handles: "Lady Gaga - Bloody Mary (Official Audio)" played via LadyGagaVEVO
    """
    # Clean the title using precompiled patterns
    clean = title
    for pat in _CLEAN_PATTERNS:
        clean = pat.sub("", clean)
    clean = clean.strip(" -–—|")

    # Clean the artist — YouTube channel names like "LadyGagaVEVO", "TaylorSwiftVEVO"
    clean_artist = _VEVO_RE.sub("", artist).strip()
    clean_artist = _OFFICIAL_RE.sub("", clean_artist).strip()
    clean_artist = _MUSIC_RE.sub("", clean_artist).strip()
    clean_artist = _CAMEL_RE.sub(r"\1 \2", clean_artist)

    # If title contains " - ", split into artist/title (handles YouTube/label MPRIS)
    if " - " in clean:
        left, right = clean.split(" - ", 1)
        left  = left.strip()
        right = right.strip()
        # Always use the split — the title part is more reliable than MPRIS artist
        return left, right

    return clean_artist, clean.strip()



def _fetch_lyrics(artist: str, title: str) -> list | None:
    """
    Fetch synced lyrics with a provider fallback chain:
      1. LRCLIB  — open-source, reliable, no API key
      2. Netease — fallback, good for tracks missing from LRCLIB

    Returns list of (timestamp_seconds, line_text) tuples, or [] for plain-only,
    or None on total network failure (not cached so next poll retries).
    """
    cache_key = f"{artist.lower()}|||{title.lower()}"
    with _cache_lock:
        if cache_key in _lyrics_cache:
            return _lyrics_cache[cache_key]
        if cache_key in _fetch_in_flight:
            return None
        _fetch_in_flight.add(cache_key)

    # ── Provider 1: LRCLIB ───────────────────────────────────────────────────

    def _fetch_lrclib(a: str, t: str) -> list | None:
        try:
            params = urllib.parse.urlencode({"artist_name": a, "track_name": t})
            url = f"https://lrclib.net/api/search?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "GreatSage/1.0"})
            with urllib.request.urlopen(req, timeout=4) as r:
                results = json.loads(r.read())
            if not results:
                return {"_not_found": True}
            for item in results:
                if item.get("syncedLyrics"):
                    lines = _parse_lrc(item["syncedLyrics"])
                    if lines:
                        return lines
            for item in results:
                if item.get("plainLyrics"):
                    return [(None, l) for l in item["plainLyrics"].splitlines()]
            return {"_not_found": True}
        except urllib.error.URLError as e:
            return {"_network_error": str(e)}
        except Exception:
            return {"_not_found": True}

    # ── Provider 2: Netease Cloud Music ──────────────────────────────────────

    def _fetch_netease(a: str, t: str) -> list | None:
        try:
            params = urllib.parse.urlencode({
                "s": f"{a} {t}", "type": "1", "limit": "10",
            })
            req = urllib.request.Request(
                f"https://music.163.com/api/search/get?{params}",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://music.163.com"})
            with urllib.request.urlopen(req, timeout=4) as r:
                data = json.loads(r.read().decode("utf-8", errors="replace"))

            songs = data.get("result", {}).get("songs") or []
            if not songs:
                return {"_not_found": True}

            # Score each result — prefer exact title + artist match
            def _score(s):
                st = s.get("name", "").lower()
                sa = " ".join(ar.get("name", "") for ar in s.get("artists", [])).lower()
                tl, al = t.lower(), a.lower()
                score = 0
                if st == tl:             score += 10
                elif tl in st:           score += 5
                if al and al in sa:      score += 8
                return score

            songs.sort(key=_score, reverse=True)
            best = songs[0]

            # Reject if title doesn't match at all
            if t.lower() not in best.get("name", "").lower():
                return {"_not_found": True}

            lrc_url = f"https://music.163.com/api/song/lyric?id={best['id']}&lv=1&kv=1&tv=-1"
            req2 = urllib.request.Request(lrc_url,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://music.163.com"})
            with urllib.request.urlopen(req2, timeout=4) as r2:
                ldata = json.loads(r2.read().decode("utf-8", errors="replace"))

            lrc_body = ldata.get("lrc", {}).get("lyric", "").strip()
            if lrc_body:
                lines = _parse_lrc(lrc_body)
                # Strip Chinese metadata headers
                lines = [(ts, tx) for ts, tx in lines
                         if not any(c in tx for c in ("作词", "作曲", "编曲", "制作人", "混音"))]
                if lines:
                    return lines

            return {"_not_found": True}
        except urllib.error.URLError as e:
            return {"_network_error": str(e)}
        except Exception:
            return {"_not_found": True}

    # ── Fallback chain ────────────────────────────────────────────────────────

    def _strip_feat(t: str) -> str:
        return re.sub(r"\s*(feat|ft)\.?\s+[^(\[]+", "", t, flags=re.IGNORECASE).strip()

    # Only search with artist+title — no title-only query (causes wrong matches)
    queries = [(artist, title)]
    t_stripped = _strip_feat(title)
    if t_stripped != title:
        queries.append((artist, t_stripped))

    any_network_error = False

    try:
        for provider in [_fetch_lrclib, _fetch_netease]:
            for a_q, t_q in queries:
                result = provider(a_q, t_q)

                if result is None or (isinstance(result, dict) and "_network_error" in result):
                    any_network_error = True
                    continue

                if isinstance(result, dict) and "_not_found" in result:
                    continue

                with _cache_lock:
                    if len(_lyrics_cache) >= _CACHE_MAX:
                        # Evict oldest entry
                        oldest = next(iter(_lyrics_cache))
                        del _lyrics_cache[oldest]
                    _lyrics_cache[cache_key] = result
                return result

        if any_network_error:
            return None

        with _cache_lock:
            if len(_lyrics_cache) >= _CACHE_MAX:
                oldest = next(iter(_lyrics_cache))
                del _lyrics_cache[oldest]
            _lyrics_cache[cache_key] = []
        return []
    finally:
        with _cache_lock:
            _fetch_in_flight.discard(cache_key)


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
    # Drop trailing empty lines
    while lines and not lines[-1][1].strip():
        lines.pop()
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

_REUSE_LINES = object()  # sentinel: means "reuse existing lines, don't touch cache"


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
        self._shown_empty  = False
        self._lines        = []
        self._line_widgets = []
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
            try:
                if not track:
                    bridge.update_ready.emit(None, None, 0.0, "")
                    return
                key = f"{track['artist']}|||{track['title']}"
                cl_artist, cl_title = _clean_title(track["title"], track["artist"])
                track["clean_title"] = cl_title
                cache_key = f"{cl_artist.lower()}|||{cl_title.lower()}"

                # If already cached, just emit position update — no network call
                if cache_key in _lyrics_cache:
                    lines = _lyrics_cache[cache_key]
                    pos = (_wc_get_pos(key, track["status"])
                           if track.get("pos_broken") else track["pos"])
                    bridge.update_ready.emit(track, lines, pos, track["status"])
                    return

                lines = _fetch_lyrics(cl_artist, cl_title)
                pos = (_wc_get_pos(key, track["status"])
                       if track.get("pos_broken") else track["pos"])
                bridge.update_ready.emit(track, lines, pos, track["status"])
            except RuntimeError:
                pass  # bridge C++ object deleted — widget was destroyed, ignore
        threading.Thread(target=_fetch, daemon=True).start()

    def _apply(self, track, lines, pos: float, status: str):
        if track is None:
            self._show_status("♪  Play something to see lyrics")
            return

        # Delayed highlight sentinel — reuse existing lines, just update position
        if lines is _REUSE_LINES:
            lines = self._lines if self._lines else None
            if lines:
                self._is_synced = bool(any(ts is not None for ts, _ in lines))
            else:
                return  # nothing to highlight yet

        key = f"{track['artist']}|||{track['title']}"

        if track.get("player"):
            self._last_player = track["player"]
        self._last_status = status

        # New track
        if key != self._cur_key:
            self._cur_key     = key
            self._cur_idx     = -1
            self._shown_empty = False
            self._first_load  = True
            if lines is not None:
                # Fetch already complete — build immediately
                self._lines     = lines
                self._is_synced = bool(any(ts is not None for ts, _ in lines)) if lines else False
                self._rebuild_lines()
            else:
                # Fetch still in progress — show nothing yet
                self._lines     = []
                self._is_synced = False
            return

        # Same track — fetch just completed with real lyrics
        if lines is not None and not self._lines:
            self._lines     = lines
            self._is_synced = bool(any(ts is not None for ts, _ in lines)) if lines else False
            self._shown_empty = False
            self._first_load  = True
            self._rebuild_lines()

        if not self._lines:
            if not self._shown_empty:
                self._shown_empty = True
                self._show_status(
                    f"♪  No lyrics found for\n{track.get('clean_title') or track['title']}")
            return

        self._shown_empty = False

        if not self._is_synced:
            self._first_load = False
            return

        # On first load, delay highlight until widgets are painted & pos is fresh
        if self._first_load:
            self._first_load = False
            last_p = self._last_player
            bridge = self._bridge
            def _delayed_highlight():
                import threading
                def _fetch():
                    try:
                        fresh = _get_track(last_player=last_p)
                        if not fresh:
                            return
                        fkey = f"{fresh['artist']}|||{fresh['title']}"
                        fst  = fresh.get("status", "")
                        fp   = (_wc_get_pos(fkey, fst)
                                if fresh.get("pos_broken") else fresh.get("pos", 0))
                        bridge.update_ready.emit(fresh, _REUSE_LINES, fp, fst)
                    except RuntimeError:
                        pass  # bridge deleted
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
        from itertools import chain as _chain
        for i in _chain(range(idx, min(idx + 3, len(self._line_widgets))),
                        range(idx - 1, -1, -1)):
            if 0 <= i < len(self._line_widgets) and self._line_widgets[i] is not None:
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
        "● Synced lyrics — LRCLIB · Netease"
        if has else "⚠  playerctl not installed — sudo apt install playerctl")
    st.setStyleSheet(
        f"color:{colours['ACCENT2'] if has else '#E05A6A'};font-size:12px;")
    bv.addWidget(st)

    desc = QLabel(
        "Displays synced lyrics below the Now Playing card.\n"
        "The current line scrolls and lights up as the song plays.\n"
        "Fetches from LRCLIB first, then Netease as fallback.")
    desc.setStyleSheet(f"color:{colours['TEXT2']};font-size:13px;")
    desc.setWordWrap(True)
    bv.addWidget(desc)

    note = QLabel(
        "💡  Lyrics are fetched once per track and cached for the session (up to 200 tracks). "
        "Providers are tried in order: LRCLIB → Netease. "
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
