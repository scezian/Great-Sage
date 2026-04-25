#!/usr/bin/env python3
"""
subtitle_manager.py — Great Sage Subtitle Manager
═══════════════════════════════════════════════════

Searches and downloads subtitles from OpenSubtitles.com (REST API v1).
A free API key is required — get one at: https://www.opensubtitles.com/consumers

API key is stored in: ~/.great_sage_subtitles.json
Downloads are saved next to the video file (so mpv auto-loads them),
with a fallback to ~/Subtitles/.

Usage (standalone):
    python3 subtitle_manager.py "Brooklyn Nine-Nine" --season 1 --episode 3

Usage (as module from Great Sage GUI):
    from subtitle_manager import SubtitleDialog
    dlg = SubtitleDialog(parent, title="Brooklyn Nine-Nine", season=1, episode=3,
                         video_path="/path/to/file.mkv")
    dlg.exec()
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional

import requests

# ── Logging ────────────────────────────────────────────────────────────────────
try:
    from gs_logger import log as _gs_log
    log = _gs_log.ui
except Exception:
    class _NoopLog:
        def __getattr__(self, name): return lambda *a, **kw: None
    log = _NoopLog()

# ── Constants ──────────────────────────────────────────────────────────────────

CONFIG_FILE    = os.path.expanduser("~/.great_sage_subtitles.json")
FALLBACK_DIR   = os.path.expanduser("~/Subtitles")
API_BASE       = "https://api.opensubtitles.com/api/v1"
APP_NAME       = "GreatSage"
APP_VERSION    = "1.0"

LANGUAGE = "en"  # English only

# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def get_api_key() -> str:
    return _load_config().get("api_key", "")


def set_api_key(key: str) -> None:
    cfg = _load_config()
    cfg["api_key"] = key.strip()
    _save_config(cfg)


# ── API client ─────────────────────────────────────────────────────────────────

class OpenSubtitlesClient:
    """Thin wrapper around the OpenSubtitles.com REST API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.token: Optional[str] = None
        self._session = requests.Session()
        self._session.headers.update({
            "Api-Key":      api_key,
            "Content-Type": "application/json",
            "User-Agent":   f"{APP_NAME} v{APP_VERSION}",
        })

    # ── Search ────────────────────────────────────────────────────────────────

    def search(
        self,
        query:    str,
        season:   Optional[int] = None,
        episode:  Optional[int] = None,
        language: str = "en",
        year:     Optional[int] = None,
        movie:    bool = False,
    ) -> list[dict]:
        """
        Search for subtitles. Returns a list of result dicts, sorted by
        download count descending (most popular first).
        """
        params: dict = {
            "query":     query,
            "languages": language,
        }
        if season  is not None: params["season_number"]  = season
        if episode is not None: params["episode_number"] = episode
        if year    is not None: params["year"]            = year
        if movie:               params["type"]            = "movie"

        try:
            r = self._session.get(
                f"{API_BASE}/subtitles",
                params=params,
                timeout=12,
            )
            r.raise_for_status()
            data = r.json()
        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                "Could not reach OpenSubtitles. Check your internet connection."
            )
        except requests.exceptions.HTTPError as e:
            if r.status_code == 401:
                raise PermissionError(
                    "Invalid API key. Check Settings → Subtitles."
                )
            raise RuntimeError(f"API error: {r.status_code} — {e}")
        except Exception as e:
            raise RuntimeError(f"Search failed: {e}")

        results = data.get("data", [])
        # Sort by download count
        results.sort(
            key=lambda x: x.get("attributes", {}).get("download_count", 0),
            reverse=True,
        )
        return results

    # ── Download ──────────────────────────────────────────────────────────────

    def get_download_link(self, file_id: int) -> str:
        """Request a one-time download link for a subtitle file."""
        try:
            r = self._session.post(
                f"{API_BASE}/download",
                json={"file_id": file_id},
                timeout=12,
            )
            r.raise_for_status()
            return r.json()["link"]
        except requests.exceptions.HTTPError as e:
            if r.status_code == 406:
                raise RuntimeError(
                    "Daily download quota reached (20/day on free plan). "
                    "Try again tomorrow or upgrade your account."
                )
            raise RuntimeError(f"Download request failed: {e}")
        except Exception as e:
            raise RuntimeError(f"Could not get download link: {e}")

    def download_subtitle(self, file_id: int, dest_path: str) -> str:
        """
        Download a subtitle file to dest_path.
        If the downloaded file is a zip, extract the first .srt/.ass/.vtt inside.
        Returns the final saved path.
        """
        link = self.get_download_link(file_id)

        r = self._session.get(link, timeout=30, stream=True)
        r.raise_for_status()

        # Write to temp file first
        suffix = Path(link.split("?")[0]).suffix or ".srt"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            for chunk in r.iter_content(chunk_size=8192):
                tmp.write(chunk)
            tmp_path = tmp.name

        # Unzip if needed
        if zipfile.is_zipfile(tmp_path):
            with zipfile.ZipFile(tmp_path) as zf:
                sub_names = [
                    n for n in zf.namelist()
                    if n.lower().endswith((".srt", ".ass", ".vtt", ".ssa"))
                ]
                if not sub_names:
                    os.unlink(tmp_path)
                    raise RuntimeError("Zip contained no subtitle files.")
                extracted = zf.extract(sub_names[0], path=os.path.dirname(tmp_path))
                os.unlink(tmp_path)
                tmp_path = extracted

        # Move to final destination
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        shutil.move(tmp_path, dest_path)
        return dest_path


# ── Path helpers ───────────────────────────────────────────────────────────────

def subtitle_path_for_video(video_path: str, language: str = "en", ext: str = ".srt") -> str:
    """
    Return the subtitle save path inside ~/Subtitles/.
    e.g. /Videos/Show.S01E03.mkv → ~/Subtitles/Show.S01E03.en.srt
    """
    stem = Path(video_path).stem
    os.makedirs(FALLBACK_DIR, exist_ok=True)
    return str(Path(FALLBACK_DIR) / f"{stem}{ext}")


def _ext_for_result(result: dict) -> str:
    """Pull subtitle extension from an API result dict."""
    fmt = result.get("attributes", {}).get("format", "srt") or "srt"
    return f".{fmt.lower()}"


# ── Result formatting ─────────────────────────────────────────────────────────

def format_result(r: dict) -> dict:
    """Flatten an API result into a simple dict for display."""
    attr = r.get("attributes", {})
    files = attr.get("files", [{}])
    file0 = files[0] if files else {}
    return {
        "file_id":       file0.get("file_id"),
        "file_name":     file0.get("file_name", "Unknown"),
        "release":       attr.get("release", ""),
        "language":      attr.get("language", ""),
        "downloads":     attr.get("download_count", 0),
        "rating":        attr.get("ratings", 0),
        "hearing_impaired": attr.get("hearing_impaired", False),
        "trusted":       attr.get("from_trusted", False),
        "upload_date":   attr.get("upload_date", "")[:10],
        "format":        attr.get("format", "srt"),
        "fps":           attr.get("fps", 0),
        "votes":         attr.get("votes", 0),
    }


# ══════════════════════════════════════════════════════════════════════════════
# PyQt6 GUI
# ══════════════════════════════════════════════════════════════════════════════

def _qt_available() -> bool:
    try:
        import PyQt6.QtWidgets  # noqa: F401
        return True
    except ImportError:
        return False


if _qt_available():
    from PyQt6.QtCore    import Qt, QThread, pyqtSignal, QTimer
    from PyQt6.QtGui     import QColor, QFont
    from PyQt6.QtWidgets import (
        QApplication, QDialog, QDialogButtonBox,
        QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
        QListWidgetItem, QMessageBox, QPushButton, QSizePolicy,
        QSpinBox, QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
        QCheckBox, QScrollArea,
    )

    # ── Colour palette (matches Great Sage dark theme) ────────────────────────
    BG      = "#0D1117"
    BG2     = "#161B22"
    BG3     = "#21262D"
    ACCENT  = "#C9A84C"
    TEXT    = "#E6EDF3"
    TEXT2   = "#8B949E"
    BORDER  = "#30363D"
    GREEN   = "#3FB950"
    RED     = "#F85149"
    BLUE    = "#58A6FF"

    QSS = f"""
        QDialog, QWidget {{
            background: {BG};
            color: {TEXT};
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            font-size: 13px;
        }}
        QLineEdit, QSpinBox, QComboBox {{
            background: {BG2};
            border: 1px solid {BORDER};
            border-radius: 5px;
            padding: 6px 10px;
            color: {TEXT};
        }}
        QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
            border-color: {ACCENT};
        }}
        QComboBox::drop-down {{ border: none; }}
        QComboBox QAbstractItemView {{
            background: {BG2};
            border: 1px solid {BORDER};
            selection-background-color: {BG3};
        }}
        QListWidget {{
            background: {BG2};
            border: 1px solid {BORDER};
            border-radius: 6px;
            outline: none;
        }}
        QListWidget::item {{
            border-bottom: 1px solid {BORDER};
            padding: 0px;
        }}
        QListWidget::item:selected {{
            background: {BG3};
        }}
        QListWidget::item:hover {{
            background: {BG3};
        }}
        QPushButton {{
            background: {BG2};
            border: 1px solid {BORDER};
            border-radius: 5px;
            padding: 6px 18px;
            color: {TEXT};
        }}
        QPushButton:hover {{ background: {BG3}; border-color: {ACCENT}; }}
        QPushButton:disabled {{ color: {TEXT2}; }}
        QPushButton#primary {{
            background: {ACCENT};
            color: #000;
            border: none;
            font-weight: bold;
        }}
        QPushButton#primary:hover {{ background: #DEB85A; }}
        QPushButton#primary:disabled {{ background: {BG3}; color: {TEXT2}; }}
        QPushButton#danger {{
            color: {RED};
            border-color: {RED};
        }}
        QLabel#section {{
            color: {TEXT2};
            font-size: 10px;
            letter-spacing: 2px;
        }}
        QFrame#separator {{
            background: {BORDER};
            max-height: 1px;
        }}
        QScrollBar:vertical {{
            background: {BG2}; width: 6px; border-radius: 3px;
        }}
        QScrollBar::handle:vertical {{
            background: {BORDER}; border-radius: 3px; min-height: 20px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    """

    # ── Search worker thread ──────────────────────────────────────────────────

    class _SearchWorker(QThread):
        results_ready = pyqtSignal(list)
        error         = pyqtSignal(str)

        def __init__(self, client, query, season, episode, language, movie):
            super().__init__()
            self._client   = client
            self._query    = query
            self._season   = season
            self._episode  = episode
            self._language = language
            self._movie    = movie

        def run(self):
            try:
                raw     = self._client.search(
                    self._query, self._season, self._episode,
                    self._language, movie=self._movie,
                )
                results = [format_result(r) for r in raw]
                self.results_ready.emit(results)
            except Exception as e:
                self.error.emit(str(e))

    # ── Download worker thread ────────────────────────────────────────────────

    class _DownloadWorker(QThread):
        done  = pyqtSignal(str)   # final path
        error = pyqtSignal(str)

        def __init__(self, client, file_id, dest_path):
            super().__init__()
            self._client    = client
            self._file_id   = file_id
            self._dest_path = dest_path

        def run(self):
            try:
                path = self._client.download_subtitle(self._file_id, self._dest_path)
                self.done.emit(path)
            except Exception as e:
                self.error.emit(str(e))

    # ── Result row widget ─────────────────────────────────────────────────────

    class _ResultRow(QWidget):
        def __init__(self, result: dict, parent=None):
            super().__init__(parent)
            self._result = result
            self._build()

        def _build(self):
            h = QHBoxLayout(self)
            h.setContentsMargins(12, 8, 12, 8)
            h.setSpacing(10)

            # Left: name + release
            info = QVBoxLayout()
            info.setSpacing(2)

            name = self._result.get("file_name", "") or self._result.get("release", "Unknown")
            name_lbl = QLabel(name[:72] + ("…" if len(name) > 72 else ""))
            name_lbl.setStyleSheet(f"color:{TEXT}; font-size:13px;")
            info.addWidget(name_lbl)

            rel = self._result.get("release", "")
            if rel and rel != name:
                rel_lbl = QLabel(rel[:80])
                rel_lbl.setStyleSheet(f"color:{TEXT2}; font-size:11px;")
                info.addWidget(rel_lbl)

            h.addLayout(info, 1)

            # Right: badges
            badges = QHBoxLayout()
            badges.setSpacing(6)

            dl  = self._result.get("downloads", 0)
            fmt = self._result.get("format",   "srt").upper()

            if self._result.get("trusted"):
                badges.addWidget(self._badge("✓ TRUSTED", GREEN))
            if self._result.get("hearing_impaired"):
                badges.addWidget(self._badge("HI", BLUE))

            badges.addWidget(self._badge(fmt, ACCENT))
            badges.addWidget(self._badge(f"↓ {dl:,}", TEXT2))

            date = self._result.get("upload_date", "")
            if date:
                badges.addWidget(self._badge(date, TEXT2))

            h.addLayout(badges)

        @staticmethod
        def _badge(text: str, color: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color:{color}; font-size:10px; letter-spacing:1px;"
                f"border:1px solid {color}33; border-radius:3px;"
                f"padding:1px 5px; background:{color}18;"
            )
            return lbl

        def result(self) -> dict:
            return self._result

    # ── API key setup screen ──────────────────────────────────────────────────

    class _ApiKeyScreen(QWidget):
        key_saved = pyqtSignal(str)

        def __init__(self, parent=None):
            super().__init__(parent)
            v = QVBoxLayout(self)
            v.setContentsMargins(40, 40, 40, 40)
            v.setSpacing(16)
            v.addStretch()

            title = QLabel("OpenSubtitles API Key Required")
            title.setStyleSheet(f"color:{ACCENT}; font-size:16px; font-weight:bold;")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.addWidget(title)

            info = QLabel(
                "Great Sage uses the OpenSubtitles.com REST API.\n"
                "A free API key is required — takes 30 seconds to get.\n\n"
                "1. Go to: opensubtitles.com/consumers\n"
                "2. Sign up / log in\n"
                "3. Create a new consumer → copy the API key\n"
                "4. Paste it below"
            )
            info.setStyleSheet(f"color:{TEXT2}; font-size:13px; line-height:1.6;")
            info.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.addWidget(info)

            self._key_input = QLineEdit()
            self._key_input.setPlaceholderText("Paste API key here…")
            self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self._key_input.setFixedHeight(38)
            v.addWidget(self._key_input)

            row = QHBoxLayout()
            open_btn = QPushButton("Open opensubtitles.com →")
            open_btn.clicked.connect(lambda: __import__("webbrowser").open(
                "https://www.opensubtitles.com/consumers"))
            save_btn = QPushButton("Save Key & Continue")
            save_btn.setObjectName("primary")
            save_btn.clicked.connect(self._save)
            row.addWidget(open_btn); row.addWidget(save_btn)
            v.addLayout(row)

            v.addStretch()

        def _save(self):
            key = self._key_input.text().strip()
            if not key:
                QMessageBox.warning(self, "Missing Key", "Please enter an API key.")
                return
            set_api_key(key)
            self.key_saved.emit(key)

    # ── Main subtitle dialog ──────────────────────────────────────────────────

    class SubtitleDialog(QDialog):
        """
        Full subtitle search + download dialog.

        Parameters
        ----------
        parent     : QWidget parent
        title      : Show/movie name (pre-filled in search box)
        season     : Season number (0 = movie)
        episode    : Episode number
        video_path : Path to the currently playing video file
        """

        subtitle_downloaded = pyqtSignal(str)  # emits the saved .srt path

        def __init__(
            self,
            parent     = None,
            title:  str = "",
            season: int = 0,
            episode:int = 0,
            video_path: str = "",
            auto_movie: bool = False,
        ):
            super().__init__(parent)
            self.setWindowTitle("◈  Subtitle Manager")
            self.setMinimumSize(760, 560)
            self.setStyleSheet(QSS)

            self._title      = title
            self._season     = season
            self._episode    = episode
            self._video_path = video_path
            self._auto_movie = auto_movie
            self._results:  list[dict] = []
            self._client:   Optional[OpenSubtitlesClient] = None
            self._worker:   Optional[_SearchWorker]  = None
            self._dl_worker:Optional[_DownloadWorker] = None

            self._stack = QStackedWidget(self)
            root = QVBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)
            root.addWidget(self._stack)

            # Page 0: API key setup
            self._key_screen = _ApiKeyScreen()
            self._key_screen.key_saved.connect(self._on_key_saved)
            self._stack.addWidget(self._key_screen)

            # Page 1: Search + results
            self._search_page = self._build_search_page()
            self._stack.addWidget(self._search_page)

            # Decide which page to show
            api_key = get_api_key()
            if api_key:
                self._client = OpenSubtitlesClient(api_key)
                self._stack.setCurrentIndex(1)
            else:
                self._stack.setCurrentIndex(0)

            # Auto-enable movie mode if flagged (suppresses bad S/E data)
            if auto_movie:
                self._movie_check.setChecked(True)  # triggers _toggle_movie_mode

        # ── Page builders ─────────────────────────────────────────────────────

        def _build_search_page(self) -> QWidget:
            page = QWidget()
            v    = QVBoxLayout(page)
            v.setContentsMargins(20, 20, 20, 20)
            v.setSpacing(12)

            # ── Header ────────────────────────────────────────────────────────
            hdr = QHBoxLayout()
            title_lbl = QLabel("◈  SUBTITLE MANAGER")
            title_lbl.setStyleSheet(
                f"color:{ACCENT}; font-size:12px; font-weight:bold; letter-spacing:3px;")
            hdr.addWidget(title_lbl)
            hdr.addStretch()

            settings_btn = QPushButton("⚙  Settings")
            settings_btn.setFixedHeight(28)
            settings_btn.clicked.connect(self._open_settings)
            hdr.addWidget(settings_btn)
            v.addLayout(hdr)

            sep = QFrame(); sep.setObjectName("separator")
            sep.setFrameShape(QFrame.Shape.HLine)
            v.addWidget(sep)

            # ── Search controls ───────────────────────────────────────────────
            row1 = QHBoxLayout(); row1.setSpacing(8)

            self._query_input = QLineEdit()
            self._query_input.setPlaceholderText("Show or movie title…")
            self._query_input.setText(self._title)
            self._query_input.setFixedHeight(36)
            self._query_input.returnPressed.connect(self._do_search)
            row1.addWidget(self._query_input, 3)


            v.addLayout(row1)

            # Season / Episode / Movie row
            row2 = QHBoxLayout(); row2.setSpacing(8)

            self._movie_check = QCheckBox("Movie (no S/E)")
            self._movie_check.setStyleSheet(f"color:{TEXT2}; font-size:12px;")
            self._movie_check.toggled.connect(self._toggle_movie_mode)
            row2.addWidget(self._movie_check)

            row2.addStretch()

            s_lbl = QLabel("Season:")
            s_lbl.setStyleSheet(f"color:{TEXT2}; font-size:12px;")
            row2.addWidget(s_lbl)
            self._season_spin = QSpinBox()
            self._season_spin.setRange(0, 99)
            self._season_spin.setValue(max(0, self._season))
            self._season_spin.setFixedWidth(60); self._season_spin.setFixedHeight(36)
            row2.addWidget(self._season_spin)

            e_lbl = QLabel("Episode:")
            e_lbl.setStyleSheet(f"color:{TEXT2}; font-size:12px;")
            row2.addWidget(e_lbl)
            self._episode_spin = QSpinBox()
            self._episode_spin.setRange(0, 9999)
            self._episode_spin.setValue(max(0, self._episode))
            self._episode_spin.setFixedWidth(70); self._episode_spin.setFixedHeight(36)
            row2.addWidget(self._episode_spin)

            self._search_btn = QPushButton("Search")
            self._search_btn.setObjectName("primary")
            self._search_btn.setFixedHeight(36)
            self._search_btn.clicked.connect(self._do_search)
            row2.addWidget(self._search_btn)

            v.addLayout(row2)

            # Video path info
            if self._video_path:
                fname = os.path.basename(self._video_path)
                vp_lbl = QLabel(f"📹  {fname}")
                vp_lbl.setStyleSheet(f"color:{TEXT2}; font-size:11px;")
                vp_lbl.setWordWrap(True)
                v.addWidget(vp_lbl)

            sep2 = QFrame(); sep2.setObjectName("separator")
            sep2.setFrameShape(QFrame.Shape.HLine)
            v.addWidget(sep2)

            # ── Status / results area ──────────────────────────────────────────
            self._status_lbl = QLabel("Enter a title and press Search.")
            self._status_lbl.setStyleSheet(f"color:{TEXT2}; font-size:12px;")
            v.addWidget(self._status_lbl)

            self._results_list = QListWidget()
            self._results_list.setSelectionMode(
                QListWidget.SelectionMode.SingleSelection)
            self._results_list.itemDoubleClicked.connect(self._download_selected)
            v.addWidget(self._results_list, 1)

            # ── Bottom bar ────────────────────────────────────────────────────
            sep3 = QFrame(); sep3.setObjectName("separator")
            sep3.setFrameShape(QFrame.Shape.HLine)
            v.addWidget(sep3)

            bot = QHBoxLayout()
            self._preview_lbl = QLabel("")
            self._preview_lbl.setStyleSheet(f"color:{TEXT2}; font-size:11px;")
            self._preview_lbl.setWordWrap(True)
            bot.addWidget(self._preview_lbl, 1)

            self._dl_btn = QPushButton("⬇  Download Selected")
            self._dl_btn.setObjectName("primary")
            self._dl_btn.setFixedHeight(36)
            self._dl_btn.setEnabled(False)
            self._dl_btn.clicked.connect(self._download_selected)
            bot.addWidget(self._dl_btn)

            close_btn = QPushButton("Close")
            close_btn.setFixedHeight(36)
            close_btn.clicked.connect(self.reject)
            bot.addWidget(close_btn)

            v.addLayout(bot)

            self._results_list.itemSelectionChanged.connect(self._on_selection_changed)

            return page

        # ── Slots ─────────────────────────────────────────────────────────────

        def _on_key_saved(self, key: str):
            self._client = OpenSubtitlesClient(key)
            self._stack.setCurrentIndex(1)

        def _toggle_movie_mode(self, movie: bool):
            self._season_spin.setEnabled(not movie)
            self._episode_spin.setEnabled(not movie)

        def _do_search(self):
            if not self._client:
                return

            query = self._query_input.text().strip()
            if not query:
                self._status_lbl.setText("Please enter a title to search.")
                return

            lang = LANGUAGE
            season  = self._season_spin.value()  if not self._movie_check.isChecked() else None
            episode = self._episode_spin.value() if not self._movie_check.isChecked() else None
            # Treat 0 as "not specified"
            if season  == 0: season  = None
            if episode == 0: episode = None
            movie = self._movie_check.isChecked()

            self._search_btn.setEnabled(False)
            self._dl_btn.setEnabled(False)
            self._results_list.clear()
            self._results.clear()
            self._status_lbl.setText("Searching…")
            self._preview_lbl.setText("")

            self._worker = _SearchWorker(
                self._client, query, season, episode, lang, movie)
            self._worker.results_ready.connect(self._on_results)
            self._worker.error.connect(self._on_search_error)
            self._worker.finished.connect(lambda: self._search_btn.setEnabled(True))
            self._worker.start()

        def _on_results(self, results: list[dict]):
            self._results = results
            self._results_list.clear()

            if not results:
                self._status_lbl.setText("No subtitles found. Try adjusting the title or season/episode.")
                return

            self._status_lbl.setText(
                f"{len(results)} subtitle(s) found — double-click or select + Download")

            for r in results:
                item   = QListWidgetItem()
                row_w  = _ResultRow(r)
                item.setSizeHint(row_w.sizeHint())
                item.setData(Qt.ItemDataRole.UserRole, r)
                self._results_list.addItem(item)
                self._results_list.setItemWidget(item, row_w)

        def _on_search_error(self, msg: str):
            self._status_lbl.setText(f"Error: {msg}")
            # If it looks like an auth error, send user back to key setup
            if "API key" in msg or "Invalid" in msg or "401" in msg:
                QTimer.singleShot(1500, lambda: self._stack.setCurrentIndex(0))

        def _on_selection_changed(self):
            items = self._results_list.selectedItems()
            if not items:
                self._dl_btn.setEnabled(False)
                self._preview_lbl.setText("")
                return
            r = items[0].data(Qt.ItemDataRole.UserRole)
            self._dl_btn.setEnabled(bool(r and r.get("file_id")))

            # Show destination path in preview
            if self._video_path and r:
                ext  = f".{r.get('format','srt')}"
                lang = LANGUAGE
                dest = subtitle_path_for_video(self._video_path, lang, ext)
                self._preview_lbl.setText(f"Will save to: {dest}")
            elif r:
                self._preview_lbl.setText(r.get("file_name",""))

        def _download_selected(self):
            items = self._results_list.selectedItems()
            if not items:
                return
            r = items[0].data(Qt.ItemDataRole.UserRole)
            if not r or not r.get("file_id"):
                return
            self._start_download(r)

        def _start_download(self, result: dict):
            lang = LANGUAGE
            ext     = f".{result.get('format','srt')}"
            file_id = result["file_id"]

            if self._video_path and os.path.exists(self._video_path):
                dest = subtitle_path_for_video(self._video_path, lang, ext)
            else:
                os.makedirs(FALLBACK_DIR, exist_ok=True)
                name = result.get("file_name", f"subtitle.{lang}") + ext
                dest = os.path.join(FALLBACK_DIR, name)

            self._dl_btn.setEnabled(False)
            self._dl_btn.setText("Downloading…")
            self._status_lbl.setText(f"Downloading → {os.path.basename(dest)}")

            self._dl_worker = _DownloadWorker(self._client, file_id, dest)
            self._dl_worker.done.connect(self._on_download_done)
            self._dl_worker.error.connect(self._on_download_error)
            self._dl_worker.start()

        def _on_download_done(self, path: str):
            self._dl_btn.setEnabled(True)
            self._dl_btn.setText("⬇  Download Selected")
            self._status_lbl.setText(f"✓ Saved: {path}")
            self.subtitle_downloaded.emit(path)

            reply = QMessageBox.information(
                self, "Subtitle Downloaded",
                f"Subtitle saved to:\n{path}\n\n"
                + ("Subtitle saved to ~/Subtitles/. Load it in mpv with --sub-file=<path> or copy it next to your video."
                   if self._video_path else ""),
                QMessageBox.StandardButton.Ok,
            )

        def _on_download_error(self, msg: str):
            self._dl_btn.setEnabled(True)
            self._dl_btn.setText("⬇  Download Selected")
            self._status_lbl.setText(f"Download failed: {msg}")
            QMessageBox.critical(self, "Download Failed", msg)

        def _open_settings(self):
            dlg = _SettingsDialog(self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                # Re-init client with new key if changed
                key = get_api_key()
                if key:
                    self._client = OpenSubtitlesClient(key)

    # ── Settings sub-dialog ───────────────────────────────────────────────────

    class _SettingsDialog(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Subtitle Settings")
            self.setFixedSize(440, 200)
            self.setStyleSheet(QSS)

            v = QVBoxLayout(self)
            v.setContentsMargins(24, 24, 24, 24)
            v.setSpacing(14)

            v.addWidget(QLabel("API Key:"))
            self._key_input = QLineEdit()
            self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self._key_input.setText(get_api_key())
            self._key_input.setFixedHeight(36)
            v.addWidget(self._key_input)

            show_btn = QPushButton("Show / Hide")
            show_btn.setFixedHeight(30)
            show_btn.clicked.connect(lambda: self._key_input.setEchoMode(
                QLineEdit.EchoMode.Normal
                if self._key_input.echoMode() == QLineEdit.EchoMode.Password
                else QLineEdit.EchoMode.Password
            ))
            v.addWidget(show_btn)


            v.addStretch()

            btns = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Save |
                QDialogButtonBox.StandardButton.Cancel
            )
            btns.accepted.connect(self._save)
            btns.rejected.connect(self.reject)
            v.addWidget(btns)

        def _save(self):
            key = self._key_input.text().strip()
            if key:
                set_api_key(key)
            self.accept()


# ══════════════════════════════════════════════════════════════════════════════
# CLI interface
# ══════════════════════════════════════════════════════════════════════════════

def _cli():
    import argparse

    p = argparse.ArgumentParser(description="Great Sage Subtitle Manager")
    p.add_argument("title", nargs="?", default=None, help="Show or movie title")
    p.add_argument("--season",  "-s", type=int, default=None, help="Season number")
    p.add_argument("--episode", "-e", type=int, default=None, help="Episode number")
    p.add_argument("--language","-l", default=None,           help="Language code (default: en)")
    p.add_argument("--movie",   "-m", action="store_true",    help="Search as movie")
    p.add_argument("--key",         default=None,             help="Set API key")
    p.add_argument("--output",  "-o", default=None,           help="Output path for subtitle")
    args = p.parse_args()

    if args.key:
        set_api_key(args.key)
        print(f"API key saved to {CONFIG_FILE}")

    if not args.title:
        return  # key-only mode

    api_key = get_api_key()
    if not api_key:
        print("No API key set. Get one free at https://www.opensubtitles.com/consumers")
        print("Then run: python3 subtitle_manager.py --key YOUR_KEY_HERE")
        return

    lang = LANGUAGE

    print(f"Searching for: {args.title!r}  (S{args.season}E{args.episode}, lang={lang})")

    client  = OpenSubtitlesClient(api_key)
    results = client.search(args.title, args.season, args.episode, lang, movie=args.movie)

    if not results:
        print("No subtitles found.")
        return

    formatted = [format_result(r) for r in results]

    print(f"\nFound {len(formatted)} result(s):\n")
    for i, r in enumerate(formatted[:10]):
        trusted = " ✓" if r["trusted"] else ""
        hi      = " [HI]" if r["hearing_impaired"] else ""
        print(f"  [{i}] {r['file_name'][:60]}")
        print(f"       ↓ {r['downloads']:,}  |  {r['format'].upper()}  |  {r['upload_date']}{trusted}{hi}")
    print()

    choice = input(f"Download which? (0–{min(9, len(formatted)-1)}, Enter to skip): ").strip()
    if not choice:
        return

    try:
        idx = int(choice)
        r   = formatted[idx]
    except (ValueError, IndexError):
        print("Invalid choice.")
        return

    if args.output:
        dest = args.output
    else:
        os.makedirs(FALLBACK_DIR, exist_ok=True)
        dest = os.path.join(FALLBACK_DIR, f"{r['file_name']}.{r['format']}")

    print(f"Downloading to: {dest}")
    saved = client.download_subtitle(r["file_id"], dest)
    print(f"✓ Saved: {saved}")


if __name__ == "__main__":
    _cli()
