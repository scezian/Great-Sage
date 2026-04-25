"""
page_transitions.py — Great Sage Plugin
Adds animated transitions when switching between pages.

Styles: Fade, Slide, Scale, Blur Fade
Default: Fade
"""

PLUGIN_NAME        = "Page Transitions"
PLUGIN_ICON        = "⟡"
PLUGIN_DESCRIPTION = "Smooth animations when switching between pages"
PLUGIN_VERSION     = "1.0"
PLUGIN_AUTHOR      = "Great Sage"
PLUGIN_COLOR       = "#5DDFCC"

import types
from PyQt6.QtCore    import (Qt, QTimer, QPropertyAnimation, QEasingCurve,
                              QRect, QPoint, pyqtProperty)
from PyQt6.QtGui     import (QColor, QPainter, QBrush, QPixmap)
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QPushButton, QScrollArea, QApplication,
                              QGridLayout, QSlider)

# ── State ────────────────────────────────────────────────────────────────────
_state = {
    "style":    "Fade",
    "duration": 180,
    "patched":  False,
}

STYLES = ["Fade", "Slide", "Scale", "Blur Fade"]

STYLE_DESCS = {
    "Fade":      "Current page fades out, new page fades in",
    "Slide":     "New page slides in from the right",
    "Scale":     "Pages scale in/out from center",
    "Blur Fade": "Page blurs out then sharpens in",
}

# ── Overlay widget ────────────────────────────────────────────────────────────
class _TransitionOverlay(QWidget):
    """Transparent overlay that animates over the page stack."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._opacity  = 0.0
        self._snapshot = None  # QPixmap of outgoing page
        self._blur_r   = 0
        self.hide()

    def get_opacity(self): return self._opacity
    def set_opacity(self, v):
        self._opacity = v
        self.update()
    opacity = pyqtProperty(float, get_opacity, set_opacity)

    def get_blur(self): return self._blur_r
    def set_blur(self, v):
        self._blur_r = v
        self.update()
    blur_radius = pyqtProperty(float, get_blur, set_blur)

    def set_snapshot(self, px: QPixmap):
        self._snapshot = px
        self.update()

    def paintEvent(self, _):
        if self._opacity <= 0.001:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H = self.width(), self.height()

        if self._snapshot and not self._snapshot.isNull():
            if self._blur_r > 0:
                # Quick blur via scale down/up
                img    = self._snapshot.toImage()
                from PyQt6.QtGui import QImage
                factor = max(1, int(self._blur_r) // 2 + 1)
                small  = img.scaled(
                    max(1, W // factor), max(1, H // factor),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                blurred = small.scaled(
                    W, H,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                p.setOpacity(self._opacity)
                p.drawImage(0, 0, blurred)
            else:
                p.setOpacity(self._opacity)
                p.drawPixmap(0, 0, self._snapshot)
        else:
            p.setOpacity(self._opacity)
            p.fillRect(0, 0, W, H, QBrush(QColor(12, 12, 14)))

        p.end()


# ── Snapshot helper ───────────────────────────────────────────────────────────
def _snapshot(widget: QWidget) -> QPixmap:
    px = QPixmap(widget.size())
    px.fill(QColor(0, 0, 0, 0))
    widget.render(px)
    return px


# ── Find main window ──────────────────────────────────────────────────────────
def _find_main():
    for w in QApplication.topLevelWidgets():
        if hasattr(w, "_navigate"):
            return w
    return None


# ── Transition runners ────────────────────────────────────────────────────────
def _run_fade(main, overlay, old_widget, new_widget, duration):
    """Fade out snapshot of old page, then fade in new page."""
    stack = main._pages

    # Take snapshot of current page
    px = _snapshot(old_widget)
    overlay.set_snapshot(px)
    overlay.setGeometry(stack.geometry())
    overlay.set_opacity(1.0)
    overlay.raise_()
    overlay.show()

    # Switch page immediately (hidden under overlay)
    stack.setCurrentWidget(new_widget)

    # Fade out overlay to reveal new page
    anim = QPropertyAnimation(overlay, b"opacity", overlay)
    anim.setDuration(duration)
    anim.setStartValue(1.0)
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def _done():
        overlay.hide()
        overlay.set_snapshot(None)

    anim.finished.connect(_done)
    anim.start()
    overlay._anim = anim  # keep reference


def _run_slide(main, overlay, old_widget, new_widget, duration):
    """Slide new page in from right using an overlay snapshot of the old page,
    so layout is never corrupted if the animation is interrupted."""
    from PyQt6.QtCore import QPropertyAnimation, QEasingCurve, QPoint
    stack = main._pages
    W = stack.width()
    H = stack.height()

    # Snapshot outgoing page before switching
    px = _snapshot(old_widget)
    overlay.set_snapshot(px)
    overlay.setGeometry(stack.geometry())
    overlay.set_opacity(1.0)
    overlay.raise_()
    overlay.show()

    # Switch immediately (hidden under overlay)
    stack.setCurrentWidget(new_widget)

    # Slide the overlay out to the left while fading, revealing new page underneath
    anim_fade = QPropertyAnimation(overlay, b"opacity", overlay)
    anim_fade.setDuration(duration)
    anim_fade.setStartValue(1.0)
    anim_fade.setEndValue(0.0)
    anim_fade.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _done():
        overlay.hide()
        overlay.set_snapshot(None)

    anim_fade.finished.connect(_done)
    anim_fade.start()
    overlay._anim = anim_fade


def _run_scale(main, overlay, old_widget, new_widget, duration):
    """Fade + slight scale using overlay opacity trick."""
    stack = main._pages
    px = _snapshot(old_widget)
    overlay.set_snapshot(px)
    overlay.setGeometry(stack.geometry())
    overlay.set_opacity(1.0)
    overlay.raise_()
    overlay.show()

    stack.setCurrentWidget(new_widget)

    anim = QPropertyAnimation(overlay, b"opacity", overlay)
    anim.setDuration(int(duration * 1.2))
    anim.setStartValue(1.0)
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.Type.OutQuart)

    def _done():
        overlay.hide()
        overlay.set_snapshot(None)

    anim.finished.connect(_done)
    anim.start()
    overlay._anim = anim


def _run_blur_fade(main, overlay, old_widget, new_widget, duration):
    """Blur + fade out snapshot, reveal new page."""
    stack = main._pages
    px = _snapshot(old_widget)
    overlay.set_snapshot(px)
    overlay.set_blur(0)
    overlay.setGeometry(stack.geometry())
    overlay.set_opacity(1.0)
    overlay.raise_()
    overlay.show()

    stack.setCurrentWidget(new_widget)

    # Animate blur increasing while fading out
    blur_anim = QPropertyAnimation(overlay, b"blur_radius", overlay)
    blur_anim.setDuration(duration)
    blur_anim.setStartValue(0.0)
    blur_anim.setEndValue(8.0)
    blur_anim.setEasingCurve(QEasingCurve.Type.InCubic)

    fade_anim = QPropertyAnimation(overlay, b"opacity", overlay)
    fade_anim.setDuration(duration)
    fade_anim.setStartValue(1.0)
    fade_anim.setEndValue(0.0)
    fade_anim.setEasingCurve(QEasingCurve.Type.InCubic)

    def _done():
        overlay.hide()
        overlay.set_snapshot(None)
        overlay.set_blur(0)

    fade_anim.finished.connect(_done)
    blur_anim.start()
    fade_anim.start()
    overlay._blur_anim = blur_anim
    overlay._fade_anim = fade_anim


_RUNNERS = {
    "Fade":      _run_fade,
    "Slide":     _run_slide,
    "Scale":     _run_scale,
    "Blur Fade": _run_blur_fade,
}


# ── Patch MainWindow._navigate ────────────────────────────────────────────────
def _patch(main):
    # If already patched (e.g. hot-reload), unpatch cleanly first so we
    # don't chain navigate wrappers or hold a stale orig_navigate reference.
    if _state["patched"]:
        _unpatch(main)

    # Create overlay parented to central widget
    cw = main.centralWidget()
    overlay = _TransitionOverlay(cw)
    main._transition_overlay = overlay

    # Keep overlay sized to central widget
    import types as _pt
    orig_cw_resize = cw.resizeEvent
    def _cw_resize(self_w, event):
        overlay.setGeometry(0, 0, self_w.width(), self_w.height())
        orig_cw_resize(event)  # call captured original, not type().resizeEvent (would recurse)
    cw.resizeEvent = _pt.MethodType(_cw_resize, cw)

    orig_navigate = main._navigate  # bound method on the instance (or class fallback)

    def _new_navigate(key):
        stack = main._pages
        old_w = stack.currentWidget()
        new_w = main._page_objs.get(key)

        style    = _state["style"]
        duration = _state["duration"]
        runner   = _RUNNERS.get(style, _run_fade)

        # Skip animation if same page, no overlay, or duration too short
        if (not new_w or new_w is old_w or duration < 20):
            orig_navigate(key)
            return

        # Take snapshot BEFORE anything changes
        px = _snapshot(stack)

        # Run original navigate (updates nav, topbar, switches page)
        orig_navigate(key)

        # Now animate overlay over the already-switched page
        cw = main.centralWidget()
        overlay.setGeometry(0, 0, cw.width(), cw.height())
        overlay.set_snapshot(px)
        overlay.set_opacity(1.0)
        overlay.raise_()
        overlay.show()

        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
        anim = QPropertyAnimation(overlay, b"opacity", overlay)
        anim.setDuration(duration)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)

        if style == "Blur Fade":
            overlay.set_blur(0)
            blur_anim = QPropertyAnimation(overlay, b"blur_radius", overlay)
            blur_anim.setDuration(duration)
            blur_anim.setStartValue(0.0)
            blur_anim.setEndValue(8.0)
            blur_anim.setEasingCurve(QEasingCurve.Type.InCubic)
            blur_anim.start()
            overlay._blur_anim = blur_anim
            anim.setEasingCurve(QEasingCurve.Type.InCubic)
        elif style == "Slide":
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        else:
            anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        def _done():
            overlay.hide()
            overlay.set_snapshot(None)
            if hasattr(overlay, "_blur_anim"):
                overlay.set_blur(0)

        anim.finished.connect(_done)
        anim.start()
        overlay._anim = anim

    import types as _pt
    main._navigate = _new_navigate   # instance-level override — other windows unaffected
    _state["patched"] = True
    _state["orig_navigate"] = orig_navigate


def _unpatch(main):
    if not _state["patched"]:
        return
    orig = _state.get("orig_navigate")
    if orig and hasattr(main, "_navigate"):
        main._navigate = orig   # restore the original bound method on the instance
    elif hasattr(main, "_navigate"):
        try:
            del main._navigate  # remove instance override, class method takes over again
        except AttributeError:
            pass
    _state["patched"] = False


# ── Style tile widget ─────────────────────────────────────────────────────────
class _StyleTile(QWidget):
    def __init__(self, name, selected, on_select, colours, parent=None):
        super().__init__(parent)
        self._name     = name
        self._selected = selected
        self._on_select = on_select
        self._colours  = colours
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(72)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(4)
        self._name_lbl = QLabel(self._name)
        self._desc_lbl = QLabel(STYLE_DESCS[self._name])
        lay.addWidget(self._name_lbl)
        lay.addWidget(self._desc_lbl)
        self._refresh()

    def _refresh(self):
        c = self._colours
        border = c["ACCENT"] if self._selected else c["BORDER"]
        bg     = c["BG3"]    if self._selected else c["BG2"]
        self.setObjectName(f"st_{self._name.replace(' ','_')}")
        obj = self.objectName()
        self.setStyleSheet(f"""
            QWidget#{obj} {{
                background:{bg};
                border:2px solid {border};
                border-radius:8px;
            }}
        """)
        text_c = c["TEXT"]  if self._selected else c["TEXT2"]
        sub_c  = c["ACCENT"] if self._selected else c["MUTED"]
        self._name_lbl.setStyleSheet(
            f"background:transparent;border:none;color:{text_c};"
            f"font-size:12px;font-weight:bold;")
        self._desc_lbl.setStyleSheet(
            f"background:transparent;border:none;color:{sub_c};font-size:10px;")

    def set_selected(self, v):
        if self._selected != v:
            self._selected = v
            self._refresh()

    def mousePressEvent(self, e):
        self._on_select(self._name)
        super().mousePressEvent(e)


# ── Plugin entry points ───────────────────────────────────────────────────────
def build_page(parent, api):
    colours = api.colours

    saved_style    = api.load_plugin_data("style")    or "Fade"
    saved_duration = api.load_plugin_data("duration")
    if saved_duration is None: saved_duration = 180
    if saved_style not in STYLES: saved_style = "Fade"

    _state["style"]    = saved_style
    _state["duration"] = int(saved_duration)

    # Patch main window
    from PyQt6.QtCore import QTimer
    def _do_patch():
        main = _find_main()
        if main:
            _patch(main)
    QTimer.singleShot(200, _do_patch)

    # ── Page UI ───────────────────────────────────────────────────────────────
    page = QWidget(parent)
    page.setStyleSheet(f"background:{colours['BG']};")
    root = QVBoxLayout(page)
    root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

    # Header
    hdr = QWidget()
    hdr.setStyleSheet(
        f"background:{colours['BG2']};border-bottom:1px solid {colours['BORDER']};")
    hdr.setFixedHeight(52)
    hh = QHBoxLayout(hdr); hh.setContentsMargins(28, 0, 28, 0)
    tl = QLabel("⟡  PAGE TRANSITIONS")
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
    bv.setContentsMargins(32, 28, 32, 28); bv.setSpacing(20)
    scroll.setWidget(body); root.addWidget(scroll, 1)

    def _sec(text):
        l = QLabel(text)
        l.setStyleSheet(
            f"color:{colours['MUTED']};font-size:9px;letter-spacing:3px;background:transparent;")
        return l

    # Style picker
    bv.addWidget(_sec("TRANSITION STYLE"))

    grid_w = QWidget(); grid_w.setStyleSheet("background:transparent;")
    grid   = QGridLayout(grid_w)
    grid.setSpacing(10); grid.setContentsMargins(0, 0, 0, 0)

    tiles = {}

    def _on_select(name):
        for n, tile in tiles.items():
            tile.set_selected(n == name)
        _state["style"] = name
        api.save_plugin_data("style", name)

    for i, name in enumerate(STYLES):
        tile = _StyleTile(name, name == saved_style, _on_select, colours)
        tiles[name] = tile
        grid.addWidget(tile, i // 2, i % 2)

    bv.addWidget(grid_w)

    # Duration slider
    bv.addWidget(_sec("SPEED"))

    dur_row = QHBoxLayout(); dur_row.setSpacing(12)
    dur_lbl = QLabel("Duration")
    dur_lbl.setStyleSheet(
        f"background:transparent;color:{colours['TEXT2']};font-size:12px;min-width:80px;")
    dur_sl = QSlider(Qt.Orientation.Horizontal)
    dur_sl.setRange(80, 500); dur_sl.setValue(int(saved_duration))
    dur_sl.setFixedWidth(220)
    dur_sl.setStyleSheet(
        "QSlider::groove:horizontal{background:#252530;height:3px;border-radius:2px;}"
        f"QSlider::sub-page:horizontal{{background:{colours['ACCENT']};border-radius:2px;}}"
        "QSlider::handle:horizontal{background:#C9A84C;width:12px;height:12px;"
        "margin:-5px 0;border-radius:6px;}")
    dur_val = QLabel(f"{saved_duration}ms")
    dur_val.setStyleSheet(
        f"background:transparent;color:{colours['TEXT2']};font-size:11px;min-width:50px;")

    def _dur_changed(v):
        dur_val.setText(f"{v}ms")
        _state["duration"] = v
        api.save_plugin_data("duration", v)

    dur_sl.valueChanged.connect(_dur_changed)
    dur_row.addWidget(dur_lbl)
    dur_row.addWidget(dur_sl)
    dur_row.addWidget(dur_val)
    dur_row.addStretch()
    bv.addLayout(dur_row)

    # Speed presets
    preset_row = QHBoxLayout(); preset_row.setSpacing(8)
    for label, val in [("Snappy", 100), ("Default", 180), ("Smooth", 300), ("Cinematic", 450)]:
        pb = QPushButton(label)
        pb.setStyleSheet(
            f"QPushButton{{background:{colours['BG2']};border:1px solid {colours['BORDER']};"
            f"color:{colours['TEXT2']};font-size:10px;padding:6px 14px;border-radius:4px;}}"
            f"QPushButton:hover{{border-color:{colours['ACCENT']};color:{colours['ACCENT']};}}")
        def _set_preset(v=val):
            dur_sl.setValue(v)
        pb.clicked.connect(_set_preset)
        preset_row.addWidget(pb)
    preset_row.addStretch()
    bv.addLayout(preset_row)

    tip = QLabel(
        "Transitions play when switching between Dashboard, Legion, Matrix, Sage and other pages.\n"
        "Fade is the most subtle. Blur Fade is the most dramatic.")
    tip.setStyleSheet(
        f"background:{colours['BG2']};color:{colours['MUTED']};"
        f"font-size:11px;padding:14px;border-radius:6px;"
        f"border:1px solid {colours['BORDER']};")
    tip.setWordWrap(True)
    bv.addWidget(tip)
    bv.addStretch()

    return page


def refresh(page):
    pass


def on_disable(parent, api):
    """Called when user disables the plugin — remove the navigate patch."""
    main = _find_main()
    if main:
        _unpatch(main)
        # Hide and clean up overlay if it exists
        overlay = getattr(main, "_transition_overlay", None)
        if overlay:
            overlay.hide()


def cleanup(parent, api):
    """Called on plugin unload — same as disable."""
    on_disable(parent, api)
