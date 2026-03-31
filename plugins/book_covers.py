"""
book_covers.py — Great Sage Plugin
Fetches and displays cover art for books in the Legion library.
Uses QListWidgetItem icons — no custom delegate needed.
"""

PLUGIN_NAME        = "Book Covers"
PLUGIN_ICON        = "📚"
PLUGIN_DESCRIPTION = "Cover art for your Legion library — fetched automatically"
PLUGIN_VERSION     = "1.1"
PLUGIN_AUTHOR      = "Great Sage"
PLUGIN_COLOR       = "#C9A84C"

import os, re, hashlib, threading, urllib.request, urllib.parse, json
from pathlib import Path
from PyQt6.QtCore    import Qt, QTimer, QSize, QObject, pyqtSignal
from PyQt6.QtGui     import (QColor, QPainter, QBrush, QPixmap, QImage,
                              QFont, QPen, QPainterPath, QIcon)
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QPushButton, QScrollArea, QApplication,
                              QListWidget, QListWidgetItem, QAbstractItemView)

CACHE_DIR = Path.home() / ".great_sage_covers"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

THUMB_W = 100
THUMB_H = 140

_px_cache: dict[str, QPixmap | None] = {}
_fetching: set[str] = set()

# ── Signal bridge ─────────────────────────────────────────────────────────────
class _Bridge(QObject):
    cover_ready = pyqtSignal(str)  # title

_bridge: "_Bridge | None" = None

def _get_bridge() -> "_Bridge":
    global _bridge
    if _bridge is None:
        _bridge = _Bridge()
    return _bridge

# ── Cover generation ──────────────────────────────────────────────────────────
def _title_color(title: str) -> QColor:
    h = int(hashlib.md5(title.encode()).hexdigest()[:6], 16)
    return QColor.fromHsv(h % 360, 130, 85)

def _make_placeholder(title: str) -> QPixmap:
    px = QPixmap(THUMB_W, THUMB_H)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    bg = _title_color(title)
    path = QPainterPath()
    path.addRoundedRect(0, 0, THUMB_W, THUMB_H, 4, 4)
    p.fillPath(path, QBrush(bg))
    from PyQt6.QtGui import QLinearGradient
    g = QLinearGradient(0, 0, 0, THUMB_H)
    g.setColorAt(0, QColor(255, 255, 255, 35))
    g.setColorAt(1, QColor(0, 0, 0, 70))
    p.fillPath(path, QBrush(g))
    words = [w for w in title.split() if w and w[0].isalpha()]
    initials = (words[0][0] + words[1][0]).upper() if len(words) >= 2 else (words[0][:2].upper() if words else "?")
    f = QFont("Arial", 14, QFont.Weight.Bold)
    p.setFont(f)
    p.setPen(QPen(QColor(255, 255, 255, 230)))
    p.drawText(0, 0, THUMB_W, THUMB_H, Qt.AlignmentFlag.AlignCenter, initials)
    p.setPen(QPen(QColor(255, 255, 255, 35), 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(0.5, 0.5, THUMB_W - 1, THUMB_H - 1, 4, 4)
    p.end()
    return px

def _cache_path(title: str) -> Path:
    safe = re.sub(r'[^\w\-]', '_', title)[:60]
    return CACHE_DIR / f"{safe}.jpg"

def _load_disk(title: str) -> QPixmap | None:
    cp = _cache_path(title)
    if cp.exists():
        px = QPixmap(str(cp))
        if not px.isNull():
            return px.scaled(THUMB_W, THUMB_H,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
    return None

def _px_from_url(img_url: str, title: str) -> "QPixmap | None":
    """Download image from URL and return scaled QPixmap."""
    try:
        req = urllib.request.Request(img_url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"})
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = r.read()
        if len(raw) < 500:
            return None
        img = QImage(); img.loadFromData(raw)
        if img.isNull():
            return None
        px = QPixmap.fromImage(img).scaled(THUMB_W, THUMB_H,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation)
        px.save(str(_cache_path(title)), "JPEG")
        return px
    except Exception:
        return None


def _try_fetch(title: str, source_url: str = "") -> "QPixmap | None":
    # 0. Scrape cover from the book's own source URL (novelbin, webnovel etc.)
    if source_url:
        try:
            import re as _re
            req = urllib.request.Request(source_url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"})
            with urllib.request.urlopen(req, timeout=8) as r:
                html = r.read().decode("utf-8", errors="ignore")
            # Common cover image patterns across novel sites
            patterns = [
                r'class="[^"]*book-img[^"]*"[^>]*>\s*<img[^>]+src="([^"]+)"',
                r'class="[^"]*cover[^"]*"[^>]*>\s*<img[^>]+src="([^"]+)"',
                r'property="og:image"\s+content="([^"]+)"',
                r'<img[^>]+class="[^"]*lazy[^"]*"[^>]+data-src="([^"]+)"',
                r'<img[^>]+id="[^"]*cover[^"]*"[^>]+src="([^"]+)"',
            ]
            for pat in patterns:
                m = _re.search(pat, html, _re.IGNORECASE)
                if m:
                    img_url = m.group(1)
                    if img_url.startswith("//"):
                        img_url = "https:" + img_url
                    elif img_url.startswith("/"):
                        from urllib.parse import urlparse
                        parsed = urlparse(source_url)
                        img_url = f"{parsed.scheme}://{parsed.netloc}{img_url}"
                    px = _px_from_url(img_url, title)
                    if px: return px
        except Exception:
            pass

    # 1. NovelUpdates search (best for Chinese/Korean/Japanese web novels)
    try:
        import re
        search_url = (f"https://www.novelupdates.com/?s={urllib.parse.quote(title)}"
                      f"&post_type=seriesplans")
        req = urllib.request.Request(search_url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"})
        with urllib.request.urlopen(req, timeout=8) as r:
            html = r.read().decode("utf-8", errors="ignore")
        covers = re.findall(
            r'src="(https://cdn\.novelupdates\.com/images/[^"]+)"', html)
        if covers:
            px = _px_from_url(covers[0], title)
            if px: return px
    except Exception:
        pass

    # 2. Google Books (works for published novels)
    try:
        url = (f"https://www.googleapis.com/books/v1/volumes"
               f"?q={urllib.parse.quote(title)}&maxResults=1")
        req = urllib.request.Request(url, headers={"User-Agent": "GreatSage/1.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read())
        items = data.get("items", [])
        if items:
            # Only use if title matches closely
            result_title = items[0].get("volumeInfo", {}).get("title", "").lower()
            if any(w.lower() in result_title for w in title.split()[:3]):
                links = items[0].get("volumeInfo", {}).get("imageLinks", {})
                img_url = links.get("thumbnail") or links.get("smallThumbnail")
                if img_url:
                    px = _px_from_url(img_url.replace("http://", "https://"), title)
                    if px: return px
    except Exception:
        pass

    return None

def _fetch_cover(title: str, source_url: str = ""):
    if title in _fetching:
        return
    _fetching.add(title)
    def _run():
        px = _load_disk(title) or _try_fetch(title, source_url)
        _px_cache[title] = px
        _fetching.discard(title)
        _get_bridge().cover_ready.emit(title)
    threading.Thread(target=_run, daemon=True).start()

def get_cover(title: str, source_url: str = "") -> QPixmap:
    if title in _px_cache:
        return _px_cache[title] or _make_placeholder(title)
    px = _load_disk(title)
    if px:
        _px_cache[title] = px
        return px
    _fetch_cover(title, source_url)
    return _make_placeholder(title)

# ── Apply icon to a list item ─────────────────────────────────────────────────
def _apply_icon(item: QListWidgetItem, title: str):
    px = get_cover(title)
    item.setIcon(QIcon(px))

# ── Install into Legion list ──────────────────────────────────────────────────
def _find_legion():
    for w in QApplication.topLevelWidgets():
        if hasattr(w, "_page_objs"):
            return w._page_objs.get("legion")
    return None

def _install(legion=None):
    if legion is None:
        legion = _find_legion()
    if not legion or not hasattr(legion, "jumpin_list"):
        return
    lw: QListWidget = legion.jumpin_list
    lw.setIconSize(QSize(THUMB_W, THUMB_H))

    # Connect signal to update icons when covers arrive
    bridge = _get_bridge()
    try:
        bridge.cover_ready.disconnect()
    except Exception:
        pass
    bridge.cover_ready.connect(lambda title: _update_icon(lw, title))

    # Apply icons to current items
    for i in range(lw.count()):
        item = lw.item(i)
        title = item.data(Qt.ItemDataRole.UserRole) or ""
        if title:
            _apply_icon(item, title)

    # Trigger Legion refresh so new items get icons via gui patch
    try:
        legion.refresh()
    except Exception:
        pass

def _update_icon(lw: QListWidget, title: str):
    """Called when a cover fetch completes — update matching item."""
    px = _px_cache.get(title)
    if not px:
        return
    for i in range(lw.count()):
        item = lw.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == title:
            item.setIcon(QIcon(px))
            break

# ── Plugin entry points ───────────────────────────────────────────────────────
def build_page(parent, api):
    colours = api.colours

    QTimer.singleShot(600, _install)

    page = QWidget(parent)
    page.setStyleSheet(f"background:{colours['BG']};")
    root = QVBoxLayout(page)
    root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

    hdr = QWidget()
    hdr.setStyleSheet(
        f"background:{colours['BG2']};border-bottom:1px solid {colours['BORDER']};")
    hdr.setFixedHeight(52)
    hh = QHBoxLayout(hdr); hh.setContentsMargins(28, 0, 28, 0)
    tl = QLabel("📚  BOOK COVERS")
    tl.setStyleSheet(
        f"color:{colours['ACCENT']};font-size:13px;font-weight:bold;letter-spacing:3px;")
    hh.addWidget(tl); hh.addStretch()
    root.addWidget(hdr)

    scroll = QScrollArea(); scroll.setWidgetResizable(True)
    scroll.setStyleSheet(
        "QScrollArea{border:none;background:transparent;}"
        f"QScrollBar:vertical{{background:{colours['BG']};width:4px;border:none;}}"
        f"QScrollBar::handle:vertical{{background:{colours['BORDER']};border-radius:2px;}}")
    body = QWidget(); body.setStyleSheet("background:transparent;")
    bv = QVBoxLayout(body)
    bv.setContentsMargins(32, 28, 32, 28); bv.setSpacing(16)
    scroll.setWidget(body); root.addWidget(scroll, 1)

    def _sec(t):
        l = QLabel(t)
        l.setStyleSheet(f"color:{colours['MUTED']};font-size:9px;letter-spacing:3px;background:transparent;")
        return l

    bv.addWidget(_sec("STATUS"))
    cache_count = len(list(CACHE_DIR.glob("*.jpg")))
    status_lbl = QLabel(f"{cache_count} cover(s) cached in {CACHE_DIR}")
    status_lbl.setStyleSheet(
        f"background:{colours['BG2']};color:{colours['TEXT2']};font-size:12px;"
        f"padding:12px 16px;border-radius:6px;border:1px solid {colours['BORDER']};")
    status_lbl.setWordWrap(True)
    bv.addWidget(status_lbl)

    def _clear():
        for f in CACHE_DIR.glob("*.jpg"): f.unlink()
        _px_cache.clear()
        status_lbl.setText("Cache cleared — covers will re-fetch on next visit")
        _install()

    def _refetch():
        _px_cache.clear()
        status_lbl.setText("Re-fetching covers…")
        _install()

    clear_btn = QPushButton("🗑  Clear Cache")
    clear_btn.setStyleSheet(
        f"QPushButton{{background:transparent;border:1px solid {colours['BORDER']};"
        f"color:{colours['MUTED']};font-size:11px;padding:10px 18px;border-radius:4px;}}"
        f"QPushButton:hover{{border-color:#E05A6A;color:#E05A6A;}}")
    clear_btn.clicked.connect(_clear)

    refetch_btn = QPushButton("↻  Re-fetch All")
    refetch_btn.setStyleSheet(
        f"QPushButton{{background:{colours['BG2']};border:1px solid {colours['BORDER']};"
        f"color:{colours['TEXT2']};font-size:11px;padding:10px 18px;border-radius:4px;}}"
        f"QPushButton:hover{{border-color:{colours['ACCENT']};color:{colours['ACCENT']};}}")
    refetch_btn.clicked.connect(_refetch)

    btn_row = QHBoxLayout()
    btn_row.addWidget(clear_btn); btn_row.addWidget(refetch_btn); btn_row.addStretch()
    bv.addLayout(btn_row)

    # Manual cover set
    bv.addWidget(_sec("MANUAL COVER"))
    manual_row = QHBoxLayout(); manual_row.setSpacing(10)
    manual_input = QLabel("Select a book in Legion, then set its cover manually:")
    manual_input.setStyleSheet(
        f"background:transparent;color:{colours['TEXT2']};font-size:11px;")
    manual_input.setWordWrap(True)
    bv.addWidget(manual_input)

    manual_btn_row = QHBoxLayout(); manual_btn_row.setSpacing(8)
    from PyQt6.QtWidgets import QLineEdit
    manual_title = QLineEdit()
    manual_title.setPlaceholderText("Book title (exact)…")
    manual_title.setStyleSheet(
        f"QLineEdit{{background:{colours['BG2']};border:1px solid {colours['BORDER']};"
        f"color:{colours['TEXT']};font-size:11px;padding:6px 10px;border-radius:4px;}}"
        f"QLineEdit:focus{{border-color:{colours['ACCENT']};}}")
    manual_browse = QPushButton("Set Cover Image…")
    manual_browse.setStyleSheet(
        f"QPushButton{{background:{colours['BG2']};border:1px solid {colours['BORDER']};"
        f"color:{colours['TEXT2']};font-size:11px;padding:7px 14px;border-radius:4px;}}"
        f"QPushButton:hover{{border-color:{colours['ACCENT']};color:{colours['ACCENT']};}}")

    def _set_manual_cover():
        from PyQt6.QtWidgets import QFileDialog
        t = manual_title.text().strip()
        if not t:
            manual_title.setPlaceholderText("Enter book title first!")
            return
        fn, _ = QFileDialog.getOpenFileName(
            page, "Choose Cover Image",
            os.path.expanduser("~/Pictures"),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if fn:
            from PyQt6.QtGui import QIcon
            img = QImage(fn)
            if not img.isNull():
                px = QPixmap.fromImage(img).scaled(
                    THUMB_W, THUMB_H,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                px.save(str(_cache_path(t)), "JPEG")
                _px_cache[t] = px
                legion = _find_legion()
                if legion:
                    lw = legion.jumpin_list
                    for i in range(lw.count()):
                        item = lw.item(i)
                        if item.data(Qt.ItemDataRole.UserRole) == t:
                            item.setIcon(QIcon(px))
                            break
                manual_title.setText("")
                manual_title.setPlaceholderText(f"✓ Cover set for '{t}'")

    manual_browse.clicked.connect(_set_manual_cover)
    manual_btn_row.addWidget(manual_title, 1)
    manual_btn_row.addWidget(manual_browse)
    bv.addLayout(manual_btn_row)

    tip = QLabel(
        "Covers fetched from NovelUpdates (web novels) and Google Books.\n"
        "Use 'Set Cover Image' to manually assign any image to a book.\n"
        "All covers cached locally — only fetched once.")
    tip.setStyleSheet(
        f"background:{colours['BG2']};color:{colours['MUTED']};"
        f"font-size:11px;padding:14px;border-radius:6px;border:1px solid {colours['BORDER']};")
    tip.setWordWrap(True)
    bv.addWidget(tip)
    bv.addStretch()
    return page

def refresh(page):
    QTimer.singleShot(200, _install)
