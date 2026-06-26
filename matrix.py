#!/usr/bin/env python3
"""
Matrix - Torrent Search and Downloader
Matrix — Media Manager
"""

import os
import sys
import re
import json
import tempfile
from great_sage_core import sage_mod, matrix_data, save_json, MATRIX_PROGRESS
from typing import Optional
import time
import glob
import subprocess
import shutil
import socket
import threading
import requests
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple, Union

# ── Logging ────────────────────────────────────────────────────────────────────
try:
    from gs_logger import log as _gs_log
    log = _gs_log.matrix
except Exception:
    class _NoopLog:
        def __getattr__(self, name): return lambda *a, **kw: None
    log = _NoopLog()

# ============================================================================
# CONSTANTS
# ============================================================================

# File paths — PROGRESS_FILE aliases MATRIX_PROGRESS from great_sage_core so
# Storage and the UI always read/write the same file.
CONFIG_DIR    = os.path.expanduser('~/.config/matrix')
PROGRESS_FILE = MATRIX_PROGRESS   # alias — do not change
SYNC_CONFIG   = os.path.join(CONFIG_DIR, 'sync_config.json')

# Ensure config directory exists
os.makedirs(CONFIG_DIR, exist_ok=True)

# Video file extensions
VIDEO_EXTS = ['.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v']

# API endpoints
JIKAN_API = "https://api.jikan.moe/v4"
TVMAZE_API = "https://api.tvmaze.com"
YTS_API = "https://yts.mx/api/v2/list_movies.json"
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")  # Set via Settings > TMDB API Key
TMDB_API = "https://api.themoviedb.org/3"

# Title corrections (common misspellings/abbreviations)
TITLE_CORRECTIONS = {
    'moratla': 'Mortal Kombat',
    'mr.d': 'Mr. D',
    'mortal': 'Mortal Kombat',
    'mr robot': 'Mr. Robot',
    'mr.robot': 'Mr. Robot',
    'breakingbad': 'Breaking Bad',
    'gameofthrones': 'Game of Thrones',
    'walkingdead': 'The Walking Dead',
    'got': 'Game of Thrones',
    'twd': 'The Walking Dead',
    'bb': 'Breaking Bad',
    'mr': 'Mr. Robot',
    'the office': 'The Office',
    'parks and rec': 'Parks and Recreation',
    'b99': 'Brooklyn Nine-Nine',
    'suits': 'Suits',
    'himym': 'How I Met Your Mother',
    'tbbt': 'The Big Bang Theory',
    'friends': 'Friends'
}

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

class MediaItem:
    """Represents a media item (movie, episode, etc.)"""
    def __init__(self, title: str, media_type: str = "unknown"):
        self.title        = title
        self.media_type   = media_type   # 'series', 'movie', 'unknown'
        self.file_path    = None
        self.duration     = 0
        self.position     = 0
        self.last_watched = None
        self.metadata     = {}
        self.is_anime     = False
        # Episode tracking
        self.current_episode = 0     # episode number currently watching
        self.current_season  = 1     # season number
        self.total_episodes  = 0     # total in series (0 = unknown)
        self.episodes_watched = []   # list of (season, episode) tuples watched

    def to_dict(self) -> Dict:
        return {
            'title':            self.title,
            'type':             self.media_type,
            'file_path':        self.file_path,
            'duration':         self.duration,
            'position':         self.position,
            'last_watched':     self.last_watched,
            'metadata':         self.metadata,
            'is_anime':         self.is_anime,
            'current_episode':  self.current_episode,
            'current_season':   self.current_season,
            'total_episodes':   self.total_episodes,
            'episodes_watched': self.episodes_watched,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'MediaItem':
        item = cls(data.get('title', 'Unknown'), data.get('type', 'unknown'))
        item.file_path        = data.get('file_path')
        item.duration         = data.get('duration', 0)
        item.position         = data.get('position', 0)
        item.last_watched     = data.get('last_watched')
        item.metadata         = data.get('metadata', {})
        item.is_anime         = data.get('is_anime', False)
        item.current_episode  = data.get('current_episode', 0)
        item.current_season   = data.get('current_season', 1)
        item.total_episodes   = data.get('total_episodes', 0)
        item.episodes_watched = data.get('episodes_watched', [])
        return item

class WatchlistItem:
    """Represents an item in the watchlist"""
    def __init__(self, title: str, is_anime: bool = False, notes: str = ""):
        self.title = title
        self.watched = False
        self.added = time.time()
        self.is_anime = is_anime
        self.notes = notes

    def to_dict(self) -> Dict:
        return {
            'title': self.title,
            'watched': self.watched,
            'added': self.added,
            'is_anime': self.is_anime,
            'notes': self.notes
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'WatchlistItem':
        item = cls(data.get('title', 'Unknown'))
        item.watched = data.get('watched', False)
        item.added = data.get('added', time.time())
        item.is_anime = data.get('is_anime', False)
        item.notes = data.get('notes', '')
        return item

def _is_anime_with_cache(title: str, file_path: str) -> bool:
    """
    Checks if a given title/file_path corresponds to an anime using a cache
    and Sage AI as a fallback.
    """
    md = matrix_data()
    cache = md.get("anime_cache", {})
    title_lower = title.lower()
    
    # If cached and less than 30 days old, return cached value
    if title_lower in cache:
        entry = cache[title_lower]
        if time.time() - entry.get("timestamp", 0) < 30 * 86400: # 30 days in seconds
            log.info(f"Using cached anime classification for '{title}'")
            return entry.get("is_anime", False)
    
    # Try filename pattern first (fast path)
    if re.search(r'\[(SubsPlease|Erai-raws|Anime|HorribleSubs|ASW|Judas|EMBER|Ohys-Raws|DB|Coalgirls|FFF|DameDesuYo|Leopard-Raws)\]', os.path.basename(file_path), re.IGNORECASE):
        result = True
        log.info(f"Classified '{title}' as anime via filename pattern.")
    else:
        # Ask Sage AI
        log.info(f"Asking Sage AI to classify '{title}' as anime or live-action.")
        try:
            mod, err = sage_mod()
            if mod and hasattr(mod, "groq_chat"):
                prompt = f"Is '{title}' an anime (Japanese animated series) or a live-action show/movie? Reply with ONLY 'anime' or 'live-action'."
                response, error = mod.groq_chat(prompt)
                if response:
                    result = response.strip().lower() == 'anime'
                    log.info(f"Sage AI classified '{title}' as {'anime' if result else 'live-action'}.")
                else:
                    result = False
                    log.warning(f"Sage AI could not classify '{title}': {error}")
            else:
                result = False
                log.warning("Sage AI (groq_chat) not available for anime classification.")
        except Exception as e:
            result = False
            log.error(f"Error calling Sage AI for anime classification of '{title}': {str(e)}")
    
    # Cache the result — re-read fresh to avoid overwriting concurrent saves
    cache[title_lower] = {"is_anime": result, "timestamp": time.time()}
    fresh_md = matrix_data()
    existing_cache = fresh_md.get("anime_cache", {})
    existing_cache.update(cache)
    fresh_md["anime_cache"] = existing_cache
    save_json(MATRIX_PROGRESS, fresh_md)
    
    return result

class MediaPlayer:
    """Handles media playback with mpv"""
    
    @staticmethod
    def _socket_is_ready(sock_path: str, timeout_sec: float = 3.0) -> bool:
        """
        Checks if the MPV IPC socket exists and is connectable.
        Returns True if connection is successful, False otherwise.
        """
        if not os.path.exists(sock_path):
            log.debug(f"MPV socket file not found at {sock_path}")
            return False
        
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout_sec)
            sock.connect(sock_path)
            sock.close()
            log.debug(f"MPV socket is ready at {sock_path}")
            return True
        except (ConnectionRefusedError, socket.timeout, FileNotFoundError) as e:
            log.debug(f"MPV socket check failed for {sock_path}: {type(e).__name__} - {str(e)}")
            return False
        except Exception as e:
            log.warning(f"Unexpected error during MPV socket check for {sock_path}: {type(e).__name__} - {str(e)}")
            return False

    @staticmethod
    def _mpv_command(sock_path: str, *args) -> Optional[dict]:
        """
        Send a command to a running mpv via IPC socket with retries and increased timeout.
        Returns response or None.
        """
        last_error_message = "MPV command failed: Unknown error."
        for attempt in range(3): # Maximum 3 retries (0, 1, 2)
            try:
                # 3. Check socket exists before attempting connection
                if not os.path.exists(sock_path):
                    log.debug(f"MPV command attempt {attempt + 1}/3: Socket file not found at {sock_path}. Waiting...")
                    time.sleep(0.3)
                    last_error_message = "MPV command failed: Socket file not found."
                    continue # Try again

                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(3.0)  # Increased timeout to 3.0 seconds
                sock.connect(sock_path)

                cmd = json.dumps({"command": list(args)}) + "\n"
                sock.send(cmd.encode())
                resp = sock.recv(4096).decode()
                sock.close()
                return json.loads(resp.split("\n")[0])
            except (ConnectionRefusedError, socket.timeout, FileNotFoundError) as e:
                # FileNotFoundError here catches if os.path.exists returns True, but file vanishes
                # before socket.connect, or if socket.connect itself raises it for some reason.
                last_error_message = f"MPV command failed: {type(e).__name__} - {str(e)}"
                log.debug(f"MPV command attempt {attempt + 1}/3 failed for {sock_path}: {last_error_message}")
                time.sleep(0.5 * (attempt + 1))  # Increasing delay
            except Exception as e:
                last_error_message = f"MPV command failed: Unexpected error - {type(e).__name__} - {str(e)}"
                log.warning(f"MPV command attempt {attempt + 1}/3 failed unexpectedly for {sock_path}: {last_error_message}")
                time.sleep(0.5 * (attempt + 1)) # Still retry
        
        log.error(f"MPV command failed permanently for {sock_path} after 3 attempts. Last error: {last_error_message}")
        return None

    @staticmethod
    def play(file_path: str, start_time: float = 0) -> Tuple[bool, float, float]:
        """
        Play a media file in mpv and track position via IPC.
        Returns (finished, last_position, duration).
        finished=True means the file reached >= 90% — treat as watched.
        """
        if not os.path.exists(file_path):
            log.warning(f"File not found: {file_path}")
            return False, 0, 0

        duration = MediaPlayer._get_duration(file_path)
        socket_path = os.path.join(tempfile.gettempdir(), "mpvsocket")

        cmd = [
            "mpv",
            f"--input-ipc-server={socket_path}",
            "--really-quiet",
        ]
        if start_time > 0:
            cmd.append(f"--start={start_time}")
        cmd.append(file_path)

        try:
            process = subprocess.Popen(cmd)
            # Wait for socket to appear
            for _ in range(20):
                if os.path.exists(socket_path):
                    break
                time.sleep(0.1)

            last_position = start_time
            last_save_time = 0.0

            # --- Anime OP/ED auto-skip state (must be outside the loop) ---
            show_title = os.path.basename(os.path.dirname(file_path)) or os.path.basename(file_path)
            is_anime = _is_anime_with_cache(show_title, file_path)
            _last_chapter_idx_mpv = -1
            _ed_countdown_active  = False
            _ed_countdown_start   = 0.0
            # --- End state init ---

            while process.poll() is None:
                resp = MediaPlayer._mpv_command(socket_path, "get_property", "time-pos")
                if resp and resp.get("error") == "success" and resp.get("data") is not None:
                    last_position = float(resp["data"])

                dur_str = (lambda _s: f"{_s//3600:02d}:{(_s%3600)//60:02d}:{_s%60:02d}")(duration) if duration > 0 else "--:--:--"
                pos_str = (lambda _s: f"{_s//3600:02d}:{(_s%3600)//60:02d}:{_s%60:02d}")(last_position) if last_position > 0 else "00:00:00"
                print(f"\r  Progress : {pos_str}/{dur_str}  ", end="", flush=True)

                if duration > 0 and last_position >= duration * 0.9:
                    process.wait(timeout=3)
                    return True, last_position, duration

                # --- Anime OP/ED auto-skip ---
                if is_anime:
                    chapter_resp = MediaPlayer._mpv_command(socket_path, "get_property", "chapter")
                    if chapter_resp and chapter_resp.get("error") == "success":
                        current_chapter = chapter_resp.get("data", -1)

                        if current_chapter is not None and current_chapter != _last_chapter_idx_mpv and current_chapter >= 0:
                            _last_chapter_idx_mpv = current_chapter
                            _ed_countdown_active  = False  # reset on any chapter change
                            list_resp = MediaPlayer._mpv_command(socket_path, "get_property", "chapter-list")
                            if list_resp and list_resp.get("error") == "success":
                                chapters = list_resp.get("data", [])
                                if current_chapter < len(chapters):
                                    chap_title = chapters[current_chapter].get("title", "").lower()

                                    if re.search(r'\b(op|opening|intro)\b', chap_title):
                                        next_ch = current_chapter + 1
                                        if next_ch < len(chapters):
                                            skip_to = chapters[next_ch].get("time", None)
                                            if skip_to is not None:
                                                MediaPlayer._mpv_command(socket_path, "set_property", "time-pos", skip_to)
                                        MediaPlayer._mpv_command(socket_path, "show-text", "Skipped intro", 2000)

                                    elif re.search(r'\b(ed|ending|credits|outro)\b', chap_title):
                                        _ed_countdown_active = True
                                        _ed_countdown_start  = time.time()
                                        MediaPlayer._mpv_command(socket_path, "show-text", "Next episode in 3s...", 3000)

                        if _ed_countdown_active and (time.time() - _ed_countdown_start) >= 3:
                            _ed_countdown_active = False
                            MediaPlayer._mpv_command(socket_path, "set_property", "user-data/gs-next", "yes")
                # --- End of anime OP/ED auto-skip ---

                time.sleep(1)

            try:
                process.wait(timeout=5)
            except Exception:
                pass

            return False, last_position, duration

        except FileNotFoundError:
            log.warning("mpv not found. Install mpv: sudo apt install mpv")
            return False, 0, 0
        except Exception as e:
            log.warning(f"Playback error: {e}")
            return False, 0, 0

    @staticmethod
    def play_episode_with_next(file_path: str, next_file: str = None,
                                on_progress: callable = None,
                                start_time: float = 0) -> Tuple[bool, bool]:
        """
        Play a single episode in mpv.  When it finishes (or reaches 90%):
          - Show an on-screen message: "Press N to play next, or wait 10s"
          - If user presses N in mpv within 10 seconds → load next file in SAME window
          - If 10 seconds elapse without N → auto-load next file in SAME window
          - If user presses Q / closes mpv → stop, no next episode

        Returns (user_quit: bool, played_next: bool).
        Uses mpv's loadfile command so everything stays in the same window.
        start_time: resume position in seconds (0 = from beginning).
        """
        if not os.path.exists(file_path):
            log.warning(f"File not found: {file_path}")
            return True, False

        duration     = MediaPlayer._get_duration(file_path)
        socket_path  = os.path.join(tempfile.gettempdir(), "mpvsocket")

        # Only spawn a new mpv process if one isn't already running on the socket.
        # When called after a loadfile swap the process is still alive — we must
        # NOT delete the socket or launch a new process.
        process = None

        # Check if a LIVE mpv is actually running on the socket (not just a stale file)
        def _socket_is_live(path):
            if not os.path.exists(path):
                return False
            test = MediaPlayer._mpv_command(path, "get_property", "mpv-version")
            return test is not None and test.get("error") == "success"

        mpv_already_running = _socket_is_live(socket_path)

        if not mpv_already_running:
            # Fresh start — clean up any stale socket first
            try:
                os.remove(socket_path)
            except Exception:
                pass

            cmd = [
                "mpv",
                f"--input-ipc-server={socket_path}",
                "--really-quiet",
                "--keep-open=no",
            ]
            if start_time > 0:
                cmd.append(f"--start={start_time}")
            cmd.append(file_path)

            try:
                process = subprocess.Popen(cmd)
                for _ in range(30):
                    if _socket_is_live(socket_path):
                        break
                    time.sleep(0.1)
            except FileNotFoundError:
                log.warning("mpv not found. Install: sudo apt install mpv")
                return True, False
        else:
            # mpv is already running (called after a loadfile swap)
            process = None  # detect quit via socket disappearing

        # ── Shared state for monitor thread ──────────────────────────────────
        _state = {
            "last_position": 0.0,
            "last_save_time": 0.0,
            "near_end_shown": False,
            "result": None,   # set to (user_quit, load_next) when done
            "stop": False,    # set True to kill mpv and exit
        }

        def _monitor():
            try:
                if mpv_already_running:
                    time.sleep(1.5)

                title_display = extract_show_title(os.path.basename(file_path)).strip(" -–—")
                print(f"\n  Playing  : {title_display}")
                print(f"  Progress : --:--:--/--:--:--", end="", flush=True)

                while True:
                    # User pressed q — kill mpv and exit
                    if _state["stop"]:
                        MediaPlayer._mpv_command(socket_path, "quit")
                        _state["result"] = (True, False)
                        return

                    resp = MediaPlayer._mpv_command(socket_path, "get_property", "time-pos")

                    if not os.path.exists(socket_path):
                        break
                    if process is not None and process.poll() is not None:
                        break

                    if resp and resp.get("error") == "success" and resp.get("data") is not None:
                        _state["last_position"] = float(resp["data"])

                    last_position = _state["last_position"]
                    dur_str = (lambda _s: f"{_s//3600:02d}:{(_s%3600)//60:02d}:{_s%60:02d}")(duration) if duration > 0 else "--:--:--"
                    pos_str = (lambda _s: f"{_s//3600:02d}:{(_s%3600)//60:02d}:{_s%60:02d}")(last_position) if last_position > 0 else "00:00:00"
                    print(f"\r  Progress : {pos_str}/{dur_str}  ", end="", flush=True)

                    now = time.time()
                    if on_progress and last_position > 0 and now - _state["last_save_time"] >= 10:
                        _state["last_save_time"] = now
                        try:
                            on_progress(file_path, last_position, duration)
                        except Exception:
                            pass

                    finished = duration > 0 and last_position >= duration * 0.9
                    if finished and next_file and not _state["near_end_shown"]:
                        _state["near_end_shown"] = True
                        msg = "Next episode in 10s — Q to stop"
                        MediaPlayer._mpv_command(socket_path, "show-text", msg, 10000)

                        countdown = 10
                        while countdown > 0:
                            if _state["stop"]:
                                MediaPlayer._mpv_command(socket_path, "quit")
                                _state["result"] = (True, False)
                                return
                            if not os.path.exists(socket_path) or (
                                    process is not None and process.poll() is not None):
                                if on_progress:
                                    try: on_progress(file_path, last_position, duration)
                                    except Exception: pass  # Ignored
                                _state["result"] = (True, False)
                                return
                            time.sleep(1)
                            countdown -= 1
                            if countdown > 0:
                                MediaPlayer._mpv_command(
                                    socket_path, "show-text",
                                    f"Next: {os.path.basename(next_file)} — {countdown}s (Q to cancel)",
                                    1100
                                )
                                print(f"\r  Progress : {pos_str}/{dur_str}  — next in {countdown}s  ",
                                      end="", flush=True)

                        if on_progress:
                            try: on_progress(file_path, last_position, duration)
                            except Exception: pass  # Ignored
                        MediaPlayer._mpv_command(socket_path, "loadfile", next_file, "replace")
                        _state["result"] = (False, True)
                        return

                    time.sleep(1)

                # mpv closed — final save
                if on_progress and _state["last_position"] > 0:
                    try:
                        on_progress(file_path, _state["last_position"], duration)
                    except Exception:
                        pass

                user_quit = not (duration > 0 and _state["last_position"] >= duration * 0.9)
                _state["result"] = (user_quit, False)

            except Exception as e:
                _state["result"] = (True, False)

        # ── Run monitor in background thread, listen for q ────────────────
        t = threading.Thread(target=_monitor, daemon=True)
        t.start()

        old_settings = None
        fd = None
        try:
            import sys, tty, termios, select
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            tty.setraw(fd)
            while _state["result"] is None:
                rlist, _, _ = select.select([sys.stdin], [], [], 0.2)
                if rlist:
                    ch = sys.stdin.read(1).lower()
                    if ch == "q":
                        _state["stop"] = True
                        break
        except Exception:
            pass
        finally:
            # Wait for monitor thread to stop printing BEFORE restoring terminal
            t.join(timeout=5)
            # ALWAYS restore terminal — this must never be skipped
            if fd is not None and old_settings is not None:
                try:
                    import termios as _termios
                    _termios.tcsetattr(fd, _termios.TCSADRAIN, old_settings)
                except Exception:
                    pass
            # Nuclear fallback: if terminal is still broken, reset via stty
            try:
                import subprocess as _subprocess
                _subprocess.run(["stty", "sane"], stderr=_subprocess.DEVNULL)
            except Exception:
                pass

        # Clear the progress lines cleanly before returning to menu
        print("\r" + " " * 70 + "\r", end="", flush=True)
        print("\033[1A\033[2K", end="", flush=True)  # erase Playing line
        print("\033[1A\033[2K", end="", flush=True)  # erase blank line above

        result = _state.get("result") or (True, False)
        return result[0], result[1]
    
    @staticmethod
    def _get_duration(file_path: str) -> float:
        """Get media duration using ffprobe"""
        try:
            cmd = [
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'csv=p=0', file_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass
        return 0
    
    @staticmethod
    def _extract_episode_number(filename: str) -> Optional[int]:
        """
        Extract episode number from a filename using multiple regex patterns.
        Returns None if nothing matches — caller can then try AI fallback.
        
        Handles formats like:
          S01E05, s1e5, 1x05               → episode 5
          Episode 05, Ep.5, EP05           → episode 5
          - 05 -, [05], (05)               → episode 5
          Show Name 101.mkv                → episode 101
          05.mkv, 5 - Title.mkv           → episode 5
        """
        name = os.path.splitext(filename)[0]  # strip extension

        patterns = [
            # Standard SxxExx / sXeX / 1x05
            (r'[Ss]\d{1,2}[Ee](\d{1,4})',             1),
            (r'\d{1,2}[xX](\d{1,4})',                  1),
            # "Episode 05" / "Ep.5" / "EP05"
            (r'[Ee]p(?:isode)?\.?\s*(\d{1,4})',        1),
            # Surrounded by brackets/dashes: "- 05 -" / "[05]" / "(05)"
            (r'[-–\[( ]\s*(\d{1,3})\s*[-–\]) ]',       1),
            # Three-digit combined season+episode: 101, 212, 312 (season < 10, ep < 100)
            (r'(?<!\d)([1-9])(\d{2})(?!\d)',           None),  # special: returns s*100+e
            # Bare number at start or end: "05 - Title" or "Title - 05"
            (r'^(\d{1,3})\s*[-–.]',                    1),
            (r'[-–.]\s*(\d{1,3})\s*$',                 1),
        ]

        for pattern, group in patterns:
            m = re.search(pattern, name)
            if m:
                if group is None:
                    # Three-digit: e.g. "101" → season 1, ep 1 → return 101 as-is
                    return int(m.group(0))
                try:
                    return int(m.group(group))
                except (IndexError, ValueError):
                    continue

        return None

    @staticmethod
    def _extract_season_episode(filename: str) -> tuple:
        """
        Extract (season, episode) from a filename.
        Returns (1, episode) if no season found, (0, 0) if nothing found.
        Handles: S01E05, s1e5, 1x05, 101 (season 1 ep 1), bare episode numbers.
        """
        name = os.path.splitext(filename)[0]

        # S01E05 / s1e5 — most reliable
        m = re.search(r'[Ss](\d{1,2})[Ee](\d{1,4})', name)
        if m:
            return int(m.group(1)), int(m.group(2))

        # 1x05 format
        m = re.search(r'(\d{1,2})[xX](\d{1,4})', name)
        if m:
            return int(m.group(1)), int(m.group(2))

        # Three-digit combined: 101 → S1E01, 212 → S2E12
        m = re.search(r'(?<!\d)([1-9])(\d{2})(?!\d)', name)
        if m:
            return int(m.group(1)), int(m.group(2))

        # No season found — fall back to episode-only with season=1
        ep = MediaPlayer._extract_episode_number(filename)
        if ep is not None:
            return 1, ep

        return 0, 0

    @staticmethod
    def fetch_total_episodes(title: str, is_anime: bool = False) -> int:
        """
        Fetch total episode count for a series from TMDB or Jikan.
        Always tries both sources and picks the best result:
        - If is_anime=True, Jikan result wins outright.
        - Otherwise, if Jikan finds a confident match it overrides a low TMDB count.
        Returns 0 if unknown or it's a movie.
        """
        import urllib.request, urllib.parse

        jikan_count = 0
        tmdb_count  = 0

        # ── Jikan (MyAnimeList) ───────────────────────────────────────────────
        try:
            query = urllib.parse.quote(title)
            url   = f"https://api.jikan.moe/v4/anime?q={query}&limit=1"
            req   = urllib.request.Request(url, headers={"User-Agent": "GreatSage/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
            results = data.get("data", [])
            if results:
                ep = results[0].get("episodes")
                if ep:
                    jikan_count = int(ep)
        except Exception:
            pass

        # If explicitly marked as anime and Jikan found something, trust it fully
        if is_anime and jikan_count:
            return jikan_count

        # ── TMDB (TV shows) ──────────────────────────────────────────────────
        if TMDB_API_KEY:
            try:
                query  = urllib.parse.quote(title)
                url    = f"{TMDB_API}/search/tv?api_key={TMDB_API_KEY}&query={query}&language=en-US"
                req    = urllib.request.Request(url, headers={"User-Agent": "GreatSage/1.0"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = json.loads(r.read())
                results = data.get("results", [])
                if results:
                    show_id    = results[0]["id"]
                    detail_url = f"{TMDB_API}/tv/{show_id}?api_key={TMDB_API_KEY}"
                    req2 = urllib.request.Request(detail_url,
                                                  headers={"User-Agent": "GreatSage/1.0"})
                    with urllib.request.urlopen(req2, timeout=8) as r2:
                        detail = json.loads(r2.read())
                    ep = detail.get("number_of_episodes")
                    if ep:
                        tmdb_count = int(ep)
            except Exception:
                pass

        # ── Pick the best result ─────────────────────────────────────────────
        # If Jikan found something and TMDB returned a suspiciously low number
        # (or nothing), prefer Jikan — it's likely an anime misidentified by TMDB.
        if jikan_count and (tmdb_count == 0 or tmdb_count < jikan_count):
            return jikan_count

        if tmdb_count:
            return tmdb_count

        return 0

    @staticmethod
    def count_episodes_in_folder(file_path: str) -> int:
        """
        Count video files in the same folder as file_path.
        This gives the total episodes for a downloaded series without
        any API calls — if you downloaded it, the folder has them all.
        Returns 0 if the folder can't be read.
        """
        try:
            folder = os.path.dirname(os.path.abspath(file_path))
            return sum(
                1 for f in os.listdir(folder)
                if any(f.lower().endswith(ext) for ext in VIDEO_EXTS)
            )
        except Exception:
            return 0

    @staticmethod
    def _ai_find_next_episode(current_filename: str, candidates: List[str]) -> Optional[str]:
        """
        When regex can't figure out episode order, ask Groq.
        Sends only filenames — lightweight, fast call.
        Returns the filename of the next episode, or None.
        """
        try:
            import urllib.request as _ur
            import urllib.error  as _ue

            # Read API key from sage.py config if available, else look for env var
            groq_key = os.environ.get("GROQ_API_KEY", "")
            if not groq_key:
                # Try reading it from sage.py in the same directory
                sage_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sage.py")
                if os.path.exists(sage_path):
                    with open(sage_path, "r") as sf:
                        for line in sf:
                            if line.strip().startswith("GROQ_API_KEY"):
                                groq_key = line.split("=")[1].strip().strip('"').strip("'")
                                break
            if not groq_key:
                return None

            files_list = "\n".join(f"  - {f}" for f in candidates)
            prompt = (
                f"I just finished watching: {current_filename}\n\n"
                f"These are the other video files in the same folder:\n{files_list}\n\n"
                "Which of these files is the next episode I should watch after the one I just finished? "
                "Reply with ONLY the exact filename, nothing else. "
                "If there is no logical next episode, reply with: NONE"
            )

            payload = json.dumps({
                "model":       "llama-3.1-8b-instant",  # fast small model for this simple task
                "messages":    [{"role": "user", "content": prompt}],
                "temperature": 0.0,  # deterministic
                "max_tokens":  100,
                "stream":      False,
            }).encode()

            req = _ur.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=payload,
                headers={
                    "Content-Type":  "application/json",
                    "Authorization": f"Bearer {groq_key}",
                },
                method="POST",
            )
            with _ur.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())

            answer = data["choices"][0]["message"]["content"].strip()
            if answer.upper() == "NONE" or not answer:
                return None

            # Verify the returned filename actually exists in candidates
            answer_clean = answer.strip('"').strip("'").strip()
            if answer_clean in candidates:
                return answer_clean

            # Fuzzy match in case the model added/removed punctuation
            for c in candidates:
                if c.lower() == answer_clean.lower():
                    return c

            return None

        except Exception:
            return None

    @staticmethod
    def find_next_episode(current_file: str) -> Optional[str]:
        """
        Find the next episode in the same directory.
        Uses multi-pattern regex first, then falls back to AI for unusual naming.
        """
        if not current_file:
            return None

        directory = os.path.dirname(current_file)
        filename  = os.path.basename(current_file)

        # Get all video files in the directory, sorted
        try:
            all_files = sorted([
                f for f in os.listdir(directory)
                if any(f.lower().endswith(ext) for ext in VIDEO_EXTS)
            ])
        except Exception:
            return None

        if not all_files:
            return None

        # ── Strategy 1: regex episode number extraction ────────────────────
        current_ep = MediaPlayer._extract_episode_number(filename)

        if current_ep is not None:
            next_ep = current_ep + 1

            # Look for a file whose extracted number matches next_ep
            for f in all_files:
                if f == filename:
                    continue
                ep = MediaPlayer._extract_episode_number(f)
                if ep == next_ep:
                    return os.path.join(directory, f)

            # If no regex match for next_ep, fall back to sorted position.
            # Accept the next file as long as its episode number isn't LESS
            # than current (avoids going backwards; allows same/unknown numbers).
            if filename in all_files:
                idx = all_files.index(filename)
                if idx + 1 < len(all_files):
                    candidate = all_files[idx + 1]
                    cand_ep = MediaPlayer._extract_episode_number(candidate)
                    if cand_ep is None or cand_ep >= current_ep:
                        return os.path.join(directory, candidate)

        # ── Strategy 2: sorted-position only (no numbers found at all) ─────
        if current_ep is None:
            if filename in all_files:
                idx = all_files.index(filename)
                if idx + 1 < len(all_files):
                    # Ask AI to confirm this is really the next episode
                    candidates = all_files[max(0, idx-1):idx+5]  # small window
                    next_by_ai = MediaPlayer._ai_find_next_episode(filename, candidates)
                    if next_by_ai:
                        return os.path.join(directory, next_by_ai)
                    # If AI unavailable, trust alphabetical sort
                    return os.path.join(directory, all_files[idx + 1])

        return None
    
    @staticmethod
    def play_with_next_detection(file_path: str, on_next: callable = None,
                                  on_progress: callable = None, start_time: float = 0):
        """
        Play episodes one after another in the SAME mpv window.
        After each episode ends, shows a 10-second countdown in the player.
        The next episode loads into the same window via loadfile — no new window.
        on_next(next_file) — called when a new episode starts (for storage updates).
        on_progress(file, pos, dur) — called whenever progress should be saved.
        start_time: resume position in seconds for the first episode only.
        """
        current    = file_path
        first_play = True

        while True:
            next_file = MediaPlayer.find_next_episode(current)

            user_quit, played_next = MediaPlayer.play_episode_with_next(
                current,
                next_file=next_file,
                on_progress=on_progress,
                start_time=start_time if first_play else 0,
            )
            first_play = False

            if played_next and next_file:
                # Notify storage that we moved to a new episode
                if on_next:
                    try:
                        on_next(next_file)
                    except Exception:
                        pass
                current = next_file
                # The file is ALREADY loaded inside the running mpv window via loadfile.
                # DO NOT call MediaPlayer.play() here — that would open a second window.
                # Instead we loop back to play_episode_with_next which monitors the
                # EXISTING socket and tracks progress for the new file.
                continue

            break

# ============================================================================
# UI COMPONENTS
# ============================================================================

class Storage:
    """Handles all data persistence"""
    
    def __init__(self):
        self.data = self._load()
    
    def _load(self) -> Dict:
        """Load all data from file"""
        try:
            if os.path.exists(PROGRESS_FILE):
                with open(PROGRESS_FILE, 'r') as f:
                    data = json.load(f)
                    return self._migrate_data(data)
        except Exception as e:
            log.warning(f"Error loading data: {e}")
            log.error("Storage._load failed", path=PROGRESS_FILE, error=str(e))
        
        return self._get_default_data()
    
    def _get_default_data(self) -> Dict:
        """Get default data structure"""
        return {
            'watchlist': {
                'planning':  [],
                'watching':  [],
                'dropped':   [],
                'completed': [],
            },
            'watching': {},   # continue-watching progress (keyed by show path)
            'completed': {},  # legacy, kept for migration
            'settings': {
                'download_dir': os.path.expanduser('~/Videos'),
                'auto_next': True,
            }
        }
    
    def _migrate_data(self, data: Dict) -> Dict:
        """Migrate old flat watchlist to new 4-list structure."""
        # Old format: data['watchlist'] was a list of WatchlistItem dicts
        if isinstance(data.get('watchlist'), list):
            old_items = data['watchlist']
            new_wl = {'planning': [], 'watching': [], 'dropped': [], 'completed': []}
            for item in old_items:
                if isinstance(item, dict):
                    if item.get('watched'):
                        new_wl['completed'].append(item)
                    else:
                        new_wl['planning'].append(item)
            data['watchlist'] = new_wl
        # Ensure all four keys exist
        if isinstance(data.get('watchlist'), dict):
            for key in ('planning', 'watching', 'dropped', 'completed'):
                if key not in data['watchlist']:
                    data['watchlist'][key] = []
        # Move items in data['watching'] progress into watchlist['watching'] if not there
        for title in data.get('watching', {}).keys():
            wl = data.get('watchlist', {})
            all_titles = [e.get('title','').lower() for lst in wl.values() for e in lst]
            if title.lower() not in all_titles:
                wl.setdefault('watching', []).append({
                    'title': title, 'watched': False,
                    'added': time.time(), 'is_anime': False,
                    'notes': 'Migrated from Continue Watching'
                })
        return data
    
    def save(self):
        """Save all data to file. Uses atomic write (tmp → rename) to prevent
        corruption if the process is killed mid-write."""
        try:
            tmp_path = PROGRESS_FILE + ".tmp"
            with open(tmp_path, 'w') as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp_path, PROGRESS_FILE)   # atomic on Linux
            # Rolling backup — one generation, catches mid-crash scenarios
            bak_path = PROGRESS_FILE + ".bak"
            try:
                import shutil as _sh
                _sh.copy2(PROGRESS_FILE, bak_path)
            except Exception:
                pass
            return True
        except Exception as e:
            log.warning(f"Error saving data: {e}")
            log.error("Storage.save failed", path=PROGRESS_FILE, error=str(e))
            return False
    
    # ── Watchlist (4-list: planning / watching / dropped / completed) ──────────

    def _wl(self) -> Dict:
        """Return the watchlist dict, ensuring it has all four sub-lists."""
        wl = self.data.setdefault('watchlist', {})
        if isinstance(wl, list):          # shouldn't happen after migration
            wl = {'planning': wl, 'watching': [], 'dropped': [], 'completed': []}
            self.data['watchlist'] = wl
        for key in ('planning', 'watching', 'dropped', 'completed'):
            wl.setdefault(key, [])
        return wl

    def get_watchlist_list(self, list_name: str) -> List[WatchlistItem]:
        """Return items from a specific list (planning/watching/dropped/completed)."""
        items = []
        for item_data in self._wl().get(list_name, []):
            try:
                items.append(WatchlistItem.from_dict(item_data))
            except Exception:
                continue
        return items

    def get_watchlist(self) -> List[WatchlistItem]:
        """Flat list of all watchlist items across all four sub-lists (for compatibility)."""
        items = []
        for lst_name in ('planning', 'watching', 'dropped', 'completed'):
            items.extend(self.get_watchlist_list(lst_name))
        return items

    def all_watchlisted_titles(self) -> set:
        """Return lowercase set of all titles in any watchlist sub-list."""
        titles = set()
        for lst in self._wl().values():
            for e in lst:
                t = e.get('title', '') if isinstance(e, dict) else ''
                if t:
                    titles.add(t.lower())
        return titles

    def add_to_watchlist_list(self, title: str, list_name: str,
                               is_anime: bool = False, notes: str = '') -> str:
        """
        Add title to a specific sub-list.
        Moves it out of any other sub-list it was already in.
        Returns 'added', 'duplicate', or 'error'.
        """
        wl = self._wl()
        # Duplicate check in target list
        for e in wl[list_name]:
            if e.get('title', '').lower() == title.lower():
                return 'duplicate'
        # Remove from any other list (item moves)
        for lst_key in ('planning', 'watching', 'dropped', 'completed'):
            if lst_key == list_name:
                continue
            wl[lst_key] = [e for e in wl[lst_key]
                           if e.get('title', '').lower() != title.lower()]
        item = WatchlistItem(title, is_anime=is_anime, notes=notes or f'Added to {list_name}')
        wl[list_name].append(item.to_dict())
        self.save()
        return 'added'

    def add_to_watchlist(self, title: str, is_anime: bool = False) -> bool:
        """Compatibility shim — adds to Planning list."""
        return self.add_to_watchlist_list(title, 'planning', is_anime) == 'added'

    def remove_from_watchlist_list(self, list_name: str, index: int) -> bool:
        """Remove item from a specific sub-list by index."""
        lst = self._wl().get(list_name, [])
        if 0 <= index < len(lst):
            del lst[index]
            self.save()
            return True
        return False

    def remove_from_watchlist(self, index: int) -> bool:
        """Compatibility shim — removes from flat combined list by global index."""
        offset = 0
        for lst_name in ('planning', 'watching', 'dropped', 'completed'):
            lst = self._wl()[lst_name]
            if index < offset + len(lst):
                local_idx = index - offset
                del lst[local_idx]
                self.save()
                return True
            offset += len(lst)
        return False

    def update_watchlist_item(self, index: int, **kwargs) -> bool:
        """Compatibility shim — updates item by global flat index."""
        offset = 0
        for lst_name in ('planning', 'watching', 'dropped', 'completed'):
            lst = self._wl()[lst_name]
            if index < offset + len(lst):
                local_idx = index - offset
                item = WatchlistItem.from_dict(lst[local_idx])
                for key, value in kwargs.items():
                    if hasattr(item, key):
                        setattr(item, key, value)
                lst[local_idx] = item.to_dict()
                self.save()
                return True
            offset += len(lst)
        return False

    def update_watchlist_list_item(self, list_name: str, index: int, **kwargs) -> bool:
        """Update item in a specific sub-list by local index."""
        lst = self._wl().get(list_name, [])
        if 0 <= index < len(lst):
            item = WatchlistItem.from_dict(lst[index])
            for key, value in kwargs.items():
                if hasattr(item, key):
                    setattr(item, key, value)
            lst[index] = item.to_dict()
            self.save()
            return True
        return False

    def sync_watching_to_watchlist(self):
        """Ensure every title in continue-watching is in the Watching sub-list."""
        wl = self._wl()
        all_titles = {e.get('title','').lower() for lst in wl.values() for e in lst}
        changed = False
        for title in self.data.get('watching', {}).keys():
            if title.lower() not in all_titles:
                item = WatchlistItem(title, notes='Auto-added from Continue Watching')
                wl['watching'].append(item.to_dict())
                changed = True
        if changed:
            self.save()
    
    def get_watching(self) -> Dict[str, MediaItem]:
        """Get currently watching items"""
        watching = {}
        for key, item_data in self.data.get('watching', {}).items():
            try:
                watching[key] = MediaItem.from_dict(item_data)
            except (KeyError, TypeError, ValueError):
                continue
        return watching
    
    def update_watching(self, show_key: str, item: MediaItem):
        """Update watching progress"""
        self.data['watching'][show_key] = item.to_dict()
        self.save()
    
    def remove_watching(self, show_key: str):
        """Remove from watching"""
        if show_key in self.data.get('watching', {}):
            del self.data['watching'][show_key]
            self.save()
    
    def move_to_completed(self, show_key: str):
        """Move from watching to completed"""
        if show_key in self.data.get('watching', {}):
            item_data = self.data['watching'][show_key]
            item_data['completed'] = True
            item_data['completed_at'] = time.time()
            
            if 'completed' not in self.data:
                self.data['completed'] = {}
            
            self.data['completed'][show_key] = item_data
            del self.data['watching'][show_key]
            self.save()
    
    def get_torrent_downloads(self) -> Dict:
        """Get torrent downloads"""
        return self.data.get('torrent_downloads', {})
    
    def add_torrent_download(self, name: str, magnet: str, path: str):
        """Add torrent to downloads"""
        if 'torrent_downloads' not in self.data:
            self.data['torrent_downloads'] = {}
        
        self.data['torrent_downloads'][name] = {
            'magnet': magnet,
            'path': path,
            'status': 'downloading',
            'added': time.time()
        }
        self.save()
    
    def remove_torrent_download(self, name: str):
        """Remove torrent from downloads list"""
        if name in self.data.get('torrent_downloads', {}):
            del self.data['torrent_downloads'][name]
            self.save()

# ============================================================================
# TITLE CORRECTION
# ============================================================================

def normalize_title(title: str) -> str:
    """Normalize title for comparison"""
    return re.sub(r'[^a-z0-9]', '', title.lower())

def correct_title(title: str) -> str:
    """Apply title corrections"""
    normalized = normalize_title(title)
    
    # Direct match first
    for bad, good in TITLE_CORRECTIONS.items():
        if normalized == normalize_title(bad):
            return good
    
    # Fuzzy matching
    for bad, good in TITLE_CORRECTIONS.items():
        bad_norm = normalize_title(bad)
        if (bad_norm in normalized or normalized in bad_norm or
            abs(len(bad_norm) - len(normalized)) <= 2):
            if (bad_norm in normalized or normalized in bad_norm or
                (len(bad_norm) >= 3 and normalized.startswith(bad_norm[:3]))):
                return good
    
    return title

def extract_show_title(filename: str) -> str:
    """
    Extract the BASE show title from a filename, stripping episode numbers,
    quality tags and other metadata. Used as the storage key in Continue Watching
    so all episodes of the same show share one entry.

    Examples:
      Gintama - 241 [720p].mkv      → Gintama
      Breaking.Bad.S03E07.mkv       → Breaking Bad
      One Piece 1050.mp4            → One Piece
      Arcane.Episode.9.1080p.mkv    → Arcane
    """
    name = os.path.splitext(filename)[0]

    # Handle movies with year: "Movie Name (2021)" → "Movie Name"
    year_match = re.search(r'(.*?)[.\s(](\d{4})[.\s)]', name)
    if year_match:
        title = year_match.group(1).replace('.', ' ').strip()
        if title:
            return re.sub(r'\s+', ' ', title).strip()

    # Strip episode patterns — order matters, most specific first
    episode_patterns = [
        r'[Ss]\d{1,2}[Ee]\d{1,4}',          # S01E05
        r'\d{1,2}[xX]\d{1,4}',               # 1x05
        r'[Ee]p(?:isode)?\.?\s*\d{1,4}',    # Episode 5 / Ep.5
        r'[-–\s]\d{1,4}[-–\s]*$',           # " - 241" or " 241" at end
        r'[-–\s]\d{1,4}[-–\s]',             # " - 241 -" in middle
        r'\[\d{1,4}\]',                      # [241]
        r'\(\d{1,4}\)',                      # (241)
        r'\b\d{1,4}\s*$',                   # bare number at end
    ]
    for pattern in episode_patterns:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)

    # Strip quality / release tags
    quality_patterns = [
        r'\[.*?\]',
        r'\(.*?\)',
        r'\.(1080p|720p|480p|2160p|4K|WEBRip|BluRay|WEB-DL|HDTV|DVDRip|BRRip|x264|x265|HEVC|AVC|AAC|AC3).*',
    ]
    for pattern in quality_patterns:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)

    # Clean separators and whitespace
    name = re.sub(r'[._]+', ' ', name)
    name = re.sub(r'^[-\u2013\s]+', '', name)      # strip leading dashes/spaces
    name = re.sub(r'[-–]+$', '', name)        # strip trailing dashes
    name = re.sub(r'\s+', ' ', name).strip()

    return name if name else os.path.splitext(filename)[0]


class MetadataFetcher:
    """
    Fetch metadata from three sources in order:
      1. Jikan (MyAnimeList) — best for anime
      2. TMDB  — best for movies and most TV shows
      3. TVMaze — fallback for TV shows TMDB misses
    Title matching is fuzzy so abbreviations, alternate titles and
    transliterations all have a chance of hitting.
    """

    # ── Title matching ────────────────────────────────────────────────────────

    @staticmethod
    def _norm(s: str) -> str:
        """Lowercase, strip punctuation, collapse spaces."""
        return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', '', s.lower())).strip()

    @staticmethod
    def title_matches(query: str, result_title: str,
                      alt_titles: list = None) -> bool:
        """
        Fuzzy title match.
        Checks: exact, contained, core-word subset, and any alternate titles.
        """
        q = MetadataFetcher._norm(query)
        r = MetadataFetcher._norm(result_title)

        if not q or not r:
            return False

        # Exact
        if q == r:
            return True

        # Containment — only allow if the shorter string is substantial (≥5 chars)
        # Prevents "Temple" matching "28 Years Later: The Bone Temple" via Jikan
        shorter, longer = (q, r) if len(q) <= len(r) else (r, q)
        if len(shorter) >= 5 and shorter in longer:
            # Extra guard: shorter must cover at least 60% of the longer title's length
            if len(shorter) >= len(longer) * 0.6:
                return True

        # Core-word subset — ignore season/part qualifiers
        qualifiers = {'season', 'part', 'cour', 'arc', 'series',
                      'the', 'a', 'an', 'i', 'ii', 'iii', 'iv', 'v'}
        q_words = set(q.split()) - qualifiers
        r_words = set(r.split()) - qualifiers

        # Word subset only if query words dominate the result (tight match)
        if (q_words and len(q_words) >= 2
                and q_words.issubset(r_words)
                and len(r_words) <= len(q_words) + 1):
            return True

        # Check alternate titles (e.g. Japanese vs English)
        if alt_titles:
            for alt in alt_titles:
                a = MetadataFetcher._norm(str(alt))
                if not a:
                    continue
                if q == a:
                    return True
                s2, l2 = (q, a) if len(q) <= len(a) else (a, q)
                if len(s2) >= 5 and s2 in l2 and len(s2) >= len(l2) * 0.6:
                    return True

        return False

    # ── Public entry point ────────────────────────────────────────────────────

    @staticmethod
    def fetch_movie_info(title: str, is_anime: bool = False) -> Optional[Dict]:
        """
        Try Jikan → TMDB → TVMaze in order.
        Always tries all three regardless of is_anime flag so nothing slips through.
        """
        # Refresh TMDB key from settings at call-time (user may have set it since startup)
        global TMDB_API_KEY
        try:
            import json as _j, os as _os
            _cfg = _os.path.expanduser("~/.config/matrix/progress.json")
            if _os.path.exists(_cfg):
                with open(_cfg) as _f:
                    _k = json.load(_f).get("settings", {}).get("tmdb_api_key", "")
                if _k:
                    TMDB_API_KEY = _k
        except Exception:
            pass

        t = correct_title(title)

        # 1. Jikan — only for anime, skipped for movies/live-action
        #    Avoids false matches like "Temple" → random anime
        if is_anime:
            info = MetadataFetcher._fetch_jikan(t)
            if info:
                return info

        # 2. TMDB — best for movies and mainstream TV
        info = MetadataFetcher._fetch_tmdb(t)
        if info:
            return info

        # 3. TVMaze — fallback for TV shows
        info = MetadataFetcher._fetch_tvmaze(t)
        if info:
            return info

        # 4. Last resort: try Jikan even for non-anime (some shows are on MAL)
        if not is_anime:
            info = MetadataFetcher._fetch_jikan(t)
            if info:
                return info

        return None

    # ── Jikan ────────────────────────────────────────────────────────────────

    @staticmethod
    def _fetch_jikan(title: str) -> Optional[Dict]:
        """Jikan v4 (MyAnimeList). Returns first match across main + alt titles."""
        try:
            resp = requests.get(
                f"{JIKAN_API}/anime",
                params={'q': title, 'limit': 8},
                timeout=10
            )
            resp.raise_for_status()
            entries = resp.json().get('data', [])

            for entry in entries:
                # Collect all title variants
                alts = [t2.get('title', '') for t2 in entry.get('titles', [])]
                alts += [entry.get('title_english', ''), entry.get('title_japanese', '')]

                if MetadataFetcher.title_matches(title, entry['title'], alts):
                    genres = [g['name'] for g in entry.get('genres', [])]
                    themes = [t2['name'] for t2 in entry.get('themes', [])]
                    images = entry.get('images', {}) or {}
                    img_url = (images.get('jpg', {}) or {}).get('large_image_url') \
                        or (images.get('jpg', {}) or {}).get('image_url', '')
                    return {
                        'source':       'Jikan / MAL',
                        'title':        entry.get('title_english') or entry['title'],
                        'original_title': entry['title'],
                        'synopsis':     entry.get('synopsis', ''),
                        'episodes':     entry.get('episodes') or 'Unknown',
                        'score':        entry.get('score') or 'N/A',
                        'year':         entry.get('year') or 'Unknown',
                        'release_date': entry.get('aired', {}).get('string', 'Unknown'),
                        'genres':       genres + themes,
                        'type':         entry.get('type', 'Unknown'),
                        'status':       entry.get('status', 'Unknown'),
                        'rating':       entry.get('rating', 'Unknown'),
                        'studios':      [s['name'] for s in entry.get('studios', [])],
                        'image_url':    img_url,
                    }
        except Exception as e:
            log.warning(f"Jikan error: {e}")
            log.warning("Jikan metadata fetch failed", title=title, error=str(e))
        return None

    @staticmethod
    def _fetch_tmdb(title: str) -> Optional[Dict]:
        """
        TMDB multi-search (covers movies + TV in one call).
        Tries movies first, then TV shows, picks best title match.
        """
        if not TMDB_API_KEY:
            return None
        base_params = {'api_key': TMDB_API_KEY, 'language': 'en-US'}
        try:
            # Multi-search hits both movies and TV
            resp = requests.get(
                f"{TMDB_API}/search/multi",
                params={**base_params, 'query': title, 'include_adult': False},
                timeout=10
            )
            resp.raise_for_status()
            results = resp.json().get('results', [])

            for r in results:
                media_type = r.get('media_type', '')
                if media_type not in ('movie', 'tv'):
                    continue

                result_title = r.get('title') or r.get('name', '')
                orig_title   = r.get('original_title') or r.get('original_name', '')

                if not MetadataFetcher.title_matches(title, result_title,
                                                      [orig_title]):
                    continue

                # Fetch full details for genres and extra fields
                detail_url = f"{TMDB_API}/{media_type}/{r['id']}"
                det = requests.get(detail_url,
                                   params=base_params,
                                   timeout=10).json()

                genres = [g['name'] for g in det.get('genres', [])]
                poster_path = det.get('poster_path', '')
                img_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else ''

                if media_type == 'movie':
                    return {
                        'source':       'TMDB',
                        'title':        result_title,
                        'original_title': orig_title,
                        'synopsis':     det.get('overview', ''),
                        'release_date': det.get('release_date', 'Unknown'),
                        'year':         (det.get('release_date') or '')[:4] or 'Unknown',
                        'score':        round(det.get('vote_average', 0), 1) or 'N/A',
                        'genres':       genres,
                        'type':         'Movie',
                        'status':       det.get('status', 'Unknown'),
                        'runtime':      f"{det.get('runtime', '?')} min",
                        'image_url':    img_url,
                    }
                else:  # tv
                    return {
                        'source':       'TMDB',
                        'title':        result_title,
                        'original_title': orig_title,
                        'synopsis':     det.get('overview', ''),
                        'release_date': det.get('first_air_date', 'Unknown'),
                        'year':         (det.get('first_air_date') or '')[:4] or 'Unknown',
                        'score':        round(det.get('vote_average', 0), 1) or 'N/A',
                        'episodes':     det.get('number_of_episodes', 'Unknown'),
                        'seasons':      det.get('number_of_seasons', 'Unknown'),
                        'genres':       genres,
                        'type':         'TV',
                        'status':       det.get('status', 'Unknown'),
                        'network':      ', '.join(
                            n['name'] for n in det.get('networks', [])
                        ) or 'Unknown',
                        'image_url':    img_url,
                    }

        except Exception as e:
            log.warning(f"TMDB error: {e}")
            log.warning("TMDB metadata fetch failed", title=title, error=str(e))
        return None

    # ── TVMaze ───────────────────────────────────────────────────────────────

    @staticmethod
    def _fetch_tvmaze(title: str) -> Optional[Dict]:
        """TVMaze — final fallback for TV shows."""
        try:
            resp = requests.get(
                f"{TVMAZE_API}/search/shows",
                params={'q': title},
                timeout=10
            )
            resp.raise_for_status()
            results = resp.json()

            for entry in results:
                show = entry.get('show', {})
                alts = [show.get('name', '')]
                if MetadataFetcher.title_matches(title, show.get('name', ''), alts):
                    img = show.get('image') or {}
                    return {
                        'source':       'TVMaze',
                        'title':        show.get('name', ''),
                        'synopsis':     re.sub(r'<[^>]+>', '', show.get('summary', '')),
                        'release_date': show.get('premiered', 'Unknown'),
                        'year':         (show.get('premiered') or '')[:4] or 'Unknown',
                        'score':        show.get('rating', {}).get('average') or 'N/A',
                        'genres':       show.get('genres', []),
                        'type':         show.get('type', 'Unknown'),
                        'status':       show.get('status', 'Unknown'),
                        'network':      (show.get('network') or {}).get('name', 'Unknown'),
                        'image_url':    img.get('original') or img.get('medium', ''),
                    }
        except Exception as e:
            log.warning(f"TVMaze error: {e}")
            log.warning("TVMaze metadata fetch failed", title=title, error=str(e))
        return None

# ============================================================================
# TORRENT SEARCH
# ============================================================================

def load_sync_config() -> dict:
    try:
        if os.path.exists(SYNC_CONFIG):
            with open(SYNC_CONFIG, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_sync_config(cfg: dict):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(SYNC_CONFIG, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        log.warning(f"Could not save sync config: {e}")


# ── Trakt OAuth device flow ──────────────────────────────────────────────────

def trakt_device_auth(client_id: str, client_secret: str) -> dict:
    """
    Run the Trakt device-code OAuth flow.
    Returns token dict on success, empty dict on failure.
    """
    try:
        # Step 1 — request device code
        r = requests.post(
            f"{TRAKT_DEVICE_URL}/code",
            json={"client_id": client_id},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        device_code    = data["device_code"]
        user_code      = data["user_code"]
        verify_url     = data["verification_url"]
        expires_in     = data.get("expires_in", 600)
        interval       = data.get("interval", 5)

        log.warning(f"\n  Open this URL in your browser:\n  [bold]{verify_url}[/bold]")
        log.warning(f"\n  Enter this code: [bold]{user_code}[/bold]")
        log.warning("\n  Waiting for authorisation... (press Ctrl+C to cancel)\n")

        # Step 2 — poll for token
        deadline = time.time() + expires_in
        while time.time() < deadline:
            time.sleep(interval)
            poll = requests.post(
                f"{TRAKT_DEVICE_URL}/token",
                json={
                    "code":          device_code,
                    "client_id":     client_id,
                    "client_secret": client_secret,
                },
                timeout=15,
            )
            if poll.status_code == 200:
                token = poll.json()
                token["obtained_at"] = time.time()
                log.warning("  ✅ Trakt authorised!")
                return token
            elif poll.status_code == 400:
                continue   # pending
            elif poll.status_code == 404:
                log.warning("  ❌ Invalid device code.")
                return {}
            elif poll.status_code == 409:
                log.warning("  ❌ Already authorised.")
                return {}
            elif poll.status_code == 410:
                log.warning("  ❌ Code expired.")
                return {}
            elif poll.status_code == 429:
                time.sleep(interval)   # slow down
    except KeyboardInterrupt:
        log.warning("\n  Cancelled.")
    except Exception as e:
        log.warning(f"  ❌ Trakt auth error: {e}")
        log.error("Trakt auth failed", error=str(e))
    return {}


def trakt_refresh_token(cfg: dict) -> dict:
    """Refresh Trakt access token using refresh_token. Returns updated cfg."""
    try:
        r = requests.post(
            TRAKT_TOKEN_URL,
            json={
                "refresh_token": cfg["trakt_token"]["refresh_token"],
                "client_id":     cfg["trakt_client_id"],
                "client_secret": cfg["trakt_client_secret"],
                "grant_type":    "refresh_token",
            },
            timeout=15,
        )
        if r.status_code == 200:
            cfg["trakt_token"] = r.json()
            cfg["trakt_token"]["obtained_at"] = time.time()
            save_sync_config(cfg)
            return cfg
    except Exception:
        pass
    return cfg


def _trakt_headers(cfg: dict) -> dict:
    """Build Trakt API headers, refreshing token if needed."""
    token = cfg.get("trakt_token", {})
    obtained  = token.get("obtained_at", 0)
    expires   = token.get("expires_in", 7776000)
    if time.time() > obtained + expires - 86400:
        cfg = trakt_refresh_token(cfg)
        token = cfg.get("trakt_token", {})
    return {
        "Content-Type":      "application/json",
        "trakt-api-version": "2",
        "trakt-api-key":     cfg["trakt_client_id"],
        "Authorization":     f"Bearer {token.get('access_token', '')}",
    }


def fetch_trakt_lists(cfg: dict) -> dict:
    """
    Fetch all watchlist + watched history from Trakt.
    Returns dict keyed by internal list name → list of title strings.
    """
    headers  = _trakt_headers(cfg)
    username = cfg.get("trakt_username", "me")
    result   = {"planning": [], "watching": [], "completed": [], "dropped": []}

    try:
        # Watchlist (planning)
        r = requests.get(f"{TRAKT_API}/users/{username}/watchlist",
                         headers=headers, timeout=15)
        if r.status_code == 200:
            for entry in r.json():
                media = entry.get("movie") or entry.get("show") or {}
                title = media.get("title", "")
                if title:
                    result["planning"].append({"title": title, "is_anime": False,
                                               "source": "trakt"})

        # Watched history — movies
        r = requests.get(f"{TRAKT_API}/users/{username}/watched/movies",
                         headers=headers, timeout=15)
        if r.status_code == 200:
            for entry in r.json():
                title = entry.get("movie", {}).get("title", "")
                if title:
                    result["completed"].append({"title": title, "is_anime": False,
                                                "source": "trakt"})

        # Watched history — shows
        r = requests.get(f"{TRAKT_API}/users/{username}/watched/shows",
                         headers=headers, timeout=15)
        if r.status_code == 200:
            for entry in r.json():
                title = entry.get("show", {}).get("title", "")
                if title:
                    result["completed"].append({"title": title, "is_anime": False,
                                                "source": "trakt"})

        # Custom lists — look for watching/dropped
        r = requests.get(f"{TRAKT_API}/users/{username}/lists",
                         headers=headers, timeout=15)
        if r.status_code == 200:
            for lst in r.json():
                lst_name = lst.get("name", "").lower()
                internal = None
                for key, mapped in TRAKT_LIST_MAP.items():
                    if key in lst_name:
                        internal = mapped
                        break
                if not internal:
                    continue
                slug = lst.get("ids", {}).get("slug", "")
                items_r = requests.get(
                    f"{TRAKT_API}/users/{username}/lists/{slug}/items",
                    headers=headers, timeout=15,
                )
                if items_r.status_code == 200:
                    for entry in items_r.json():
                        media = entry.get("movie") or entry.get("show") or {}
                        title = media.get("title", "")
                        if title:
                            result[internal].append({"title": title, "is_anime": False,
                                                     "source": "trakt"})

    except Exception as e:
        log.warning(f"  Trakt fetch error: {e}")
        log.error("Trakt fetch failed", error=str(e))

    return result


# ── AniList ──────────────────────────────────────────────────────────────────

def fetch_anilist_lists(username: str) -> dict:
    """
    Fetch all anime lists from AniList for a given username.
    Returns dict keyed by internal list name → list of title strings.
    """
    query = """
    query ($username: String) {
      MediaListCollection(userName: $username, type: ANIME) {
        lists {
          name
          status
          entries {
            media {
              title { english romaji }
            }
          }
        }
      }
    }
    """
    result = {"planning": [], "watching": [], "completed": [], "dropped": []}
    try:
        r = requests.post(
            ANILIST_API,
            json={"query": query, "variables": {"username": username}},
            timeout=15,
        )
        r.raise_for_status()
        lists = (r.json()
                  .get("data", {})
                  .get("MediaListCollection", {})
                  .get("lists", []))
        for lst in lists:
            status   = lst.get("status", "")
            internal = ANILIST_LIST_MAP.get(status)
            if not internal:
                continue
            for entry in lst.get("entries", []):
                titles = entry.get("media", {}).get("title", {})
                title  = titles.get("english") or titles.get("romaji") or ""
                if title:
                    result[internal].append({"title": title, "is_anime": True,
                                             "source": "anilist"})
    except Exception as e:
        log.warning(f"  AniList fetch error: {e}")
        log.error("AniList fetch failed", error=str(e))
    return result


# ── Merge into Matrix watchlist ──────────────────────────────────────────────

def merge_external_lists(storage, external: dict, source_name: str) -> dict:
    """
    Merge external list data into Matrix watchlist.
    external: {list_name: [{title, is_anime, source}, ...]}
    Returns counts: {added: n, moved: n, skipped: n}
    """
    counts = {"added": 0, "moved": 0, "skipped": 0}
    wl     = storage._wl()

    for target_list, entries in external.items():
        for entry in entries:
            title    = entry.get("title", "").strip()
            is_anime = entry.get("is_anime", False)
            if not title:
                continue

            # Find if title exists in any list already
            found_in = None
            found_idx = None
            for lst_name in ("planning", "watching", "dropped", "completed"):
                for i, e in enumerate(wl[lst_name]):
                    if e.get("title", "").lower() == title.lower():
                        found_in  = lst_name
                        found_idx = i
                        break
                if found_in:
                    break

            if found_in is None:
                # Brand new — add to target list
                item = WatchlistItem(title, is_anime=is_anime,
                                     notes=f"Synced from {source_name}")
                wl[target_list].append(item.to_dict())
                counts["added"] += 1

            elif found_in != target_list:
                # Exists but in a different list — move it
                item_data = wl[found_in].pop(found_idx)
                item_data["notes"] = (item_data.get("notes", "") +
                                      f" | Moved to {target_list} by {source_name} sync")
                wl[target_list].append(item_data)
                counts["moved"] += 1

            else:
                counts["skipped"] += 1

    storage.save()
    return counts


# ── Settings + sync UI ───────────────────────────────────────────────────────

