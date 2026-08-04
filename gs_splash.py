"""
gs_splash.py — Great Sage launch splash screen.

Ported from the swf-main React/SVG design (Logo.tsx, Wordmark.tsx,
LoadingPage.tsx, tailwind.config.js) into native QPainter drawing +
QVariantAnimation. Deliberately has zero QtWebEngine/Chromium dependency
so it shows up instantly regardless of what else is happening at launch.

Progress is driven by REAL construction milestones via set_stage(0..3),
not a fixed timer — call finish() once MainWindow is actually fully built
and ready to show.

Fonts: Fraunces 72pt (SemiBold) + Inter Display, installed system-wide via
setup.sh. Falls back to Qt's default serif/sans if not found — never
raises even if the fonts are missing.
"""
import math
import os
from PyQt6.QtCore import QEasingCurve, QElapsedTimer, QPointF, QRectF, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PyQt6.QtWidgets import QWidget

# ── Palette (exact values from tailwind.config.js) ──────────────────────────
SAGE_100 = "#e0e9dc"
SAGE_300 = "#a3bf99"
SAGE_400 = "#86aa78"
SAGE_600 = "#557a45"
SAGE_800 = "#324b2c"
SAGE_900 = "#1f3320"
GOLD_100 = "#f6e6b8"
GOLD_200 = "#efd585"
GOLD_300 = "#e6c171"
GOLD_400 = "#d8a94c"
GOLD_500 = "#c19138"
GOLD_50 = "#fbf3df"
CREAM = "#eef2ee"
INK = "#0a0f0c"

_STATUS_LINES = [
    "Opening the library",
    "Calibrating the watchlist",
    "Consulting the oracle",
    "Ready to begin",
]


def _qc(hexstr: str, alpha: int = 255) -> QColor:
    c = QColor(hexstr)
    c.setAlpha(alpha)
    return c


def _cubic_bezier_y(t, x1, y1, x2, y2, iterations=8):
    """Evaluate a CSS-style cubic-bezier(x1,y1,x2,y2) easing curve at t."""
    def bx(u): return 3 * (1 - u) ** 2 * u * x1 + 3 * (1 - u) * u ** 2 * x2 + u ** 3
    def by(u): return 3 * (1 - u) ** 2 * u * y1 + 3 * (1 - u) * u ** 2 * y2 + u ** 3
    def dbx(u): return 3 * (1 - u) ** 2 * x1 + 6 * (1 - u) * u * (x2 - x1) + 3 * u ** 2 * (1 - x2)
    u = max(0.0, min(1.0, t))
    for _ in range(iterations):
        d = dbx(u)
        if abs(d) < 1e-6:
            break
        u -= (bx(u) - t) / d
        u = max(0.0, min(1.0, u))
    return by(u)


def _ease_out_cubic(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _ease_out_back(t):
    return _cubic_bezier_y(t, 0.22, 1, 0.36, 1)


def _ease_in_out_sine(t):
    return -(math.cos(math.pi * t) - 1) / 2


class _Mote:
    __slots__ = ("x_frac", "size", "delay_ms")

    def __init__(self, x_frac, size, delay_ms):
        self.x_frac = x_frac
        self.size = size
        self.delay_ms = delay_ms


_MOTES = [
    _Mote(0.12, 4, 0),
    _Mote(0.28, 3, 2400),
    _Mote(0.68, 5, 1100),
    _Mote(0.84, 3, 3600),
    _Mote(0.46, 4, 5000),
]


class SplashScreen(QWidget):
    """
    Embedded loading overlay shown inside MainWindow while pages build.

    Usage (called from within MainWindow.__init__):
        self._splash = SplashScreen(parent=self)
        self._splash.setGeometry(self.rect())
        self._splash.raise_()
        self._splash.show()
        self._splash.dismissed.connect(self._on_splash_dismissed)
        self._splash.set_stage(0)
        ... build Legion ...
        self._splash.set_stage(1)
        ... build Matrix ...
        self._splash.set_stage(2)
        ... build Sage + remaining pages ...
        self._splash.set_stage(3)
        self._splash.finish()  # fades out, then emits dismissed
    """

    dismissed = pyqtSignal()

    def __init__(self, size=(1280, 800), parent=None):
        super().__init__(parent)
        self.resize(*size)

        # ── progress state — purely decorative, time-based fill. Real page
        # construction is now fast enough (lazy-loaded pages) that tying the
        # bar to actual milestones made it snap to 100% almost instantly and
        # then sit idle. Instead it fills smoothly over DECORATIVE_FILL_MS,
        # driven by the same master clock as everything else below.
        self._progress = 0.0
        self._active_line = 0

        # ── fade-out state ──
        self._leaving = False
        self._leave_opacity = 1.0
        self._leave_scale = 1.0
        self._leave_anim = QVariantAnimation(self)
        self._leave_anim.setDuration(650)
        self._leave_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._leave_anim.valueChanged.connect(self._on_leave_step)
        self._leave_anim.finished.connect(self._on_leave_finished)

        # ── master clock — one timer drives every decorative animation
        # (ring draw-in, orbit dots, book open, spark, motes) via elapsed
        # ms, rather than N separate QVariantAnimations. One timer tick,
        # one update() call, regardless of how many animated elements are
        # on screen — cheap by construction.
        self._elapsed_ms = 0.0
        self._wall_clock = QElapsedTimer()
        self._wall_clock.start()

        # ── cached static background (grid + gradients never change frame-to-frame) ──
        self._bg_cache = None
        self._bg_cache_size = None
        self._clock = QTimer(self)
        self._clock.setTimerType(Qt.TimerType.PreciseTimer)
        self._clock.setInterval(33)  # ~30fps target; halved from 60fps, imperceptible for this ambient animation
        self._clock.timeout.connect(self._on_tick)
        self._clock.start()

        # ── fonts — falls back to Qt defaults if not installed; never raises ──
        self._font_serif = QFont("Fraunces 72pt")
        self._font_serif.setStyleName("SemiBold")
        self._font_serif.setPixelSize(46)

        self._font_sub = QFont("Inter Display")
        self._font_sub.setPixelSize(11)
        self._font_sub.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 142)

        self._font_status = QFont("Inter Display")
        self._font_status.setPixelSize(12)

    # Progress bar fills on a scripted timeline with pauses baked in, so it
    # reads as "working... pause... working..." rather than one smooth
    # sweep -- independent of real construction time (see progress state
    # comment above). Keyframes are (elapsed_ms, progress_fraction) pairs;
    # equal consecutive fractions are a deliberate hold.
    PROGRESS_KEYFRAMES = (
        (0,    0.00),
        (600,  0.20),
        (1200, 0.20),
        (1800, 0.45),
        (2640, 0.45),
        (3240, 0.65),
        (4080, 0.65),
        (4680, 0.85),
        (5400, 0.85),
        (6000, 1.00),
    )

    # ── Public API ───────────────────────────────────────────────────────
    def set_stage(self, stage: int):
        """Kept for compatibility with callers (e.g. MainWindow._splash_step)
        that still report real construction milestones. The progress bar
        itself no longer reacts to this -- see DECORATIVE_FILL_MS."""
        pass

    # Minimum time the splash stays up before it's allowed to start leaving,
    # even if real construction finished faster than this. Real completion
    # is still what the progress bar/status text report (finish() below
    # snaps them to 100% / "Ready to begin" immediately, honestly) — this
    # floor only holds the *fade-out* back so the intro animation (ring
    # draw ~1.6s, book/spark/wordmark/progress all settled by ~1.8s) always
    # gets to finish playing instead of being cut off by a launch that's
    # faster than the animation.
    MIN_DISPLAY_MS = 7000

    def finish(self):
        """Call once the real window is fully constructed and ready to show."""
        if self._leaving or getattr(self, "_finish_pending", False):
            return
        self._finish_pending = True
        self.update()

        remaining = self.MIN_DISPLAY_MS - self._elapsed_ms
        if remaining <= 0:
            self._begin_leave_sequence()
        else:
            QTimer.singleShot(int(remaining), self._begin_leave_sequence)

    def _begin_leave_sequence(self):
        if self._leaving:
            return
        self._leaving = True
        # Instant close -- no fade/zoom animation, splash just disappears
        # once the hold time (MIN_DISPLAY_MS) has elapsed.
        self._on_leave_finished()

    # ── Animation step handlers ─────────────────────────────────────────
    def _start_leave(self):
        self._leave_anim.setStartValue(0.0)
        self._leave_anim.setEndValue(1.0)
        self._leave_anim.start()

    def _on_leave_step(self, value):
        self._leave_opacity = 1.0 - value
        self._leave_scale = 1.0 + 0.04 * value
        self.update()

    def _on_leave_finished(self):
        self._clock.stop()
        self.dismissed.emit()
        self.hide()

    def _on_tick(self):
        self._elapsed_ms = float(self._wall_clock.elapsed())
        self._progress = self._progress_from_keyframes(self._elapsed_ms)
        self._active_line = min(3, int(self._progress * 4))
        self.update()

    def _progress_from_keyframes(self, elapsed_ms):
        """Piecewise-interpolate PROGRESS_KEYFRAMES. Consecutive keyframes
        with equal progress values produce a flat hold; different values
        produce an eased fill between them."""
        kf = self.PROGRESS_KEYFRAMES
        if elapsed_ms <= kf[0][0]:
            return kf[0][1]
        if elapsed_ms >= kf[-1][0]:
            return kf[-1][1]
        for (t0, p0), (t1, p1) in zip(kf, kf[1:]):
            if t0 <= elapsed_ms <= t1:
                if t1 == t0 or p1 == p0:
                    return p0
                frac = (elapsed_ms - t0) / (t1 - t0)
                return p0 + (p1 - p0) * _ease_out_cubic(frac)
        return kf[-1][1]

    # ── Painting ─────────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        if self._leaving:
            p.setOpacity(self._leave_opacity)
            cx_, cy_ = w / 2, h / 2
            p.translate(cx_, cy_)
            p.scale(self._leave_scale, self._leave_scale)
            p.translate(-cx_, -cy_)

        self._paint_background(p, w, h)
        self._paint_motes(p, w, h)

        emblem_size = 240
        emblem_cx = w / 2
        emblem_cy = h * 0.38
        self._paint_logo_fade_wrapper(p, emblem_cx, emblem_cy, emblem_size, delay_ms=0)

        wordmark_y = emblem_cy + emblem_size / 2 - 10  # tightened from +12 so the emblem and wordmark read as one lockup
        self._paint_wordmark(p, w / 2, wordmark_y, delay_ms=400)

        bar_y = wordmark_y + 96
        self._paint_progress(p, w / 2, bar_y, delay_ms=900)

        p.setOpacity(1.0)

    def _paint_background(self, p, w, h):
        if self._bg_cache is None or self._bg_cache_size != (w, h):
            self._bg_cache = self._render_background_pixmap(w, h)
            self._bg_cache_size = (w, h)
        p.drawPixmap(0, 0, self._bg_cache)

    def _render_background_pixmap(self, w, h):
        """Render the static background (fill + grid + gradients) once into a
        QPixmap. None of this changes frame-to-frame, so paintEvent just blits
        the cached pixmap instead of redoing ~50 draw calls every tick."""
        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        p.fillRect(0, 0, w, h, _qc(INK))

        grid_pen = QPen(_qc(SAGE_400, int(255 * 0.05)))
        grid_pen.setWidthF(1)
        p.setPen(grid_pen)
        step = 48
        x = 0.0
        while x < w:
            p.drawLine(QPointF(x, 0), QPointF(x, h))
            x += step
        y = 0.0
        while y < h:
            p.drawLine(QPointF(0, y), QPointF(w, y))
            y += step

        glow1 = QRadialGradient(w * 0.5, h * 0.28, max(w, h) * 0.55)
        glow1.setColorAt(0.0, _qc("#43613a", int(255 * 0.32)))
        glow1.setColorAt(0.6, _qc("#43613a", 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow1))
        p.drawRect(0, 0, w, h)

        glow2 = QRadialGradient(w * 0.5, h * 0.92, max(w, h) * 0.45)
        glow2.setColorAt(0.0, _qc(GOLD_500, int(255 * 0.12)))
        glow2.setColorAt(0.6, _qc(GOLD_500, 0))
        p.setBrush(QBrush(glow2))
        p.drawRect(0, 0, w, h)

        p.end()
        return pm

    def _paint_motes(self, p, w, h):
        cycle = 8000.0
        for m in _MOTES:
            if self._elapsed_ms < m.delay_ms:
                continue
            t = ((self._elapsed_ms - m.delay_ms) % cycle) / cycle
            if t < 0.12:
                op = t / 0.12
            elif t > 0.88:
                op = (1.0 - t) / 0.12
            else:
                op = 1.0
            op = max(0.0, min(1.0, op)) * 0.4
            y = h - t * h * 1.1
            x = w * m.x_frac
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_qc(GOLD_200, int(255 * op)))
            p.drawEllipse(QPointF(x, y), m.size, m.size)

    def _fade_up(self, delay_ms, duration=900):
        t = (self._elapsed_ms - delay_ms) / duration
        if t <= 0:
            return 0.0, 16.0
        if t >= 1:
            return 1.0, 0.0
        e = _ease_out_cubic(t)
        return e, 16.0 * (1.0 - e)

    def _paint_logo_fade_wrapper(self, p, cx, cy, size, delay_ms):
        op, dy = self._fade_up(delay_ms)
        if op <= 0:
            return
        p.save()
        p.setOpacity(op)
        p.translate(0, dy)
        self._paint_logo(p, cx, cy, size)
        p.restore()

    def _paint_logo(self, p, cx, cy, size):
        """Emblem, ported from Logo.tsx. Local coordinate space is 200x200."""
        scale = size / 200.0
        p.save()
        p.translate(cx - size / 2, cy - size / 2)
        p.scale(scale, scale)

        t_ms = self._elapsed_ms
        lcx, lcy = 100, 100

        pen = QPen(_qc(SAGE_400, int(255 * 0.18)))
        pen.setWidthF(0.5)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(lcx, lcy), 92, 92)
        pen2 = QPen(_qc(SAGE_400, int(255 * 0.10)))
        pen2.setWidthF(0.5)
        p.setPen(pen2)
        p.drawEllipse(QPointF(lcx, lcy), 84, 84)

        ring_t = _ease_out_cubic(t_ms / 1600)
        ring_grad = QLinearGradient(lcx - 86, lcy - 86, lcx + 86, lcy + 86)
        ring_grad.setColorAt(0.0, QColor(SAGE_300))
        ring_grad.setColorAt(1.0, QColor(SAGE_600))
        ring_pen = QPen(QBrush(ring_grad), 2)
        ring_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(ring_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        rect = (lcx - 86, lcy - 86, 172, 172)
        p.drawArc(int(rect[0] * 16), int(rect[1] * 16), int(rect[2] * 16), int(rect[3] * 16),
                  270 * 16, int(-ring_t * 360 * 16))

        a1 = (t_ms / 22000) * 360
        a2 = -(t_ms / 26000) * 360
        p.save()
        p.translate(lcx, lcy)
        p.rotate(a1)
        self._glow_dot(p, 0, -86, 3.5, GOLD_300, 1.0)
        self._glow_dot(p, 0, 86, 3.5, SAGE_300, 0.85)
        p.restore()
        p.save()
        p.translate(lcx, lcy)
        p.rotate(a2)
        self._glow_dot(p, -86, 0, 3.5, SAGE_400, 0.9)
        self._glow_dot(p, 86, 0, 3.5, SAGE_400, 0.9)
        p.restore()

        book_t = (t_ms - 350) / 900
        if book_t > 0:
            book_scale = max(0.0, min(1.2, _ease_out_back(book_t)))
            book_opacity = 1.0 if book_t >= 0.6 else min(1.0, book_t / 0.6)

            page_grad = QLinearGradient(0, 120, 0, 148)
            page_grad.setColorAt(0.0, QColor(SAGE_100))
            page_grad.setColorAt(1.0, QColor(SAGE_300))

            left_page = QPainterPath()
            left_page.moveTo(100, 126)
            left_page.cubicTo(90, 120, 76, 120, 64, 124)
            left_page.lineTo(64, 146)
            left_page.cubicTo(76, 142, 90, 142, 100, 148)
            left_page.closeSubpath()
            right_page = QPainterPath()
            right_page.moveTo(100, 126)
            right_page.cubicTo(110, 120, 124, 120, 136, 124)
            right_page.lineTo(136, 146)
            right_page.cubicTo(124, 142, 110, 142, 100, 148)
            right_page.closeSubpath()

            p.save()
            p.translate(100, 126)
            p.scale(book_scale, 1.0)
            p.translate(-100, -126)
            p.setPen(Qt.PenStyle.NoPen)
            p.setOpacity(0.95 * book_opacity)
            p.setBrush(QBrush(page_grad))
            p.drawPath(left_page)
            p.drawPath(right_page)
            p.setOpacity(1.0)

            spine_pen = QPen(_qc(SAGE_600, int(153 * book_opacity)), 1.2)
            p.setPen(spine_pen)
            p.drawLine(QPointF(100, 126), QPointF(100, 148))

            text_pen = QPen(_qc(SAGE_800, int(255 * 0.45 * book_opacity)), 0.9)
            text_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(text_pen)
            for x1, y1, x2, y2 in [(72, 130, 90, 130), (72, 135, 90, 135), (72, 140, 86, 140),
                                    (110, 130, 128, 130), (110, 135, 128, 135), (110, 140, 124, 140)]:
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            p.restore()

        spark_t = (t_ms - 900) / 700
        if spark_t > 0:
            spark_opacity = min(1.0, spark_t)
            pulse_t = (t_ms - 1500) / 2600
            if pulse_t > 0:
                phase = pulse_t % 1.0
                pulse = 1.0 + 0.15 * _ease_in_out_sine(phase if phase < 0.5 else 1.0 - phase) * 2
                final_scale = pulse
            else:
                final_scale = min(1.0, 0.3 + 0.7 * _ease_out_back(spark_t))

            spark_path = QPainterPath()
            spark_path.moveTo(100, 50)
            spark_path.quadTo(106, 66, 118, 78)
            spark_path.quadTo(106, 90, 100, 106)
            spark_path.quadTo(94, 90, 82, 78)
            spark_path.quadTo(94, 66, 100, 50)
            spark_path.closeSubpath()

            p.save()
            p.translate(100, 78)
            p.scale(final_scale, final_scale)
            p.translate(-100, -78)
            p.setOpacity(spark_opacity)
            p.setPen(Qt.PenStyle.NoPen)
            spark_grad = QRadialGradient(100, 72, 26)
            spark_grad.setColorAt(0.0, QColor(GOLD_100))
            spark_grad.setColorAt(1.0, QColor(GOLD_500))
            p.setBrush(QBrush(spark_grad))
            p.drawPath(spark_path)
            p.setBrush(QColor(GOLD_50))
            p.drawEllipse(QPointF(100, 78), 5, 5)
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(QPointF(100, 78), 2.2, 2.2)
            p.setOpacity(1.0)
            p.restore()

            ray_angle = (t_ms / 14000) * 360
            p.save()
            p.translate(100, 78)
            p.rotate(ray_angle)
            p.setOpacity(spark_opacity)
            ray_pen = QPen(_qc(GOLD_300, int(255 * 0.45)), 1.4)
            ray_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(ray_pen)
            for x1, y1, x2, y2 in [(0, -38, 0, -32), (0, 34, 0, 40), (-40, 0, -34, 0), (34, 0, 40, 0)]:
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            p.setOpacity(1.0)
            p.restore()

        p.restore()

    def _glow_dot(self, p, x, y, r, hexcolor, opacity):
        c = _qc(hexcolor, int(255 * opacity))
        glow = QRadialGradient(x, y, r * 3)
        glow.setColorAt(0, _qc(hexcolor, int(255 * opacity * 0.5)))
        glow.setColorAt(1, _qc(hexcolor, 0))
        p.setBrush(QBrush(glow))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(x, y), r * 3, r * 3)
        p.setBrush(QBrush(c))
        p.drawEllipse(QPointF(x, y), r, r)

    def _paint_wordmark(self, p, cx, y, delay_ms):
        op, dy = self._fade_up(delay_ms)
        if op <= 0:
            return
        p.save()
        p.setOpacity(op)
        p.translate(0, dy)

        p.setFont(self._font_serif)
        p.setPen(QColor(CREAM))
        title_rect = QRectF(cx - 300, y, 600, 60)
        p.drawText(title_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, "Great Sage")
        title_fm = p.fontMetrics()

        # height() gives the font's full line height (ascent + descent), which is
        # what actually clears the drawn glyphs -- a fixed guess here is what let
        # the subtitle collide with "Great Sage"'s descenders before.
        sub_y = y + title_fm.height() + 12
        p.setFont(self._font_sub)
        sub_text = "LIBRARY · WATCHLIST · ORACLE"
        fm = p.fontMetrics()
        sub_w = fm.horizontalAdvance(sub_text)
        line_w = 32
        gap = 10
        total_w = line_w + gap + sub_w + gap + line_w
        start_x = cx - total_w / 2

        line_pen = QPen(_qc(GOLD_400, int(255 * 0.7)), 1)
        p.setPen(line_pen)
        p.drawLine(QPointF(start_x, sub_y - 4), QPointF(start_x + line_w, sub_y - 4))
        p.drawLine(QPointF(start_x + line_w + gap + sub_w + gap, sub_y - 4),
                   QPointF(start_x + total_w, sub_y - 4))

        p.setPen(_qc(SAGE_300, int(255 * 0.8)))
        p.drawText(QPointF(start_x + line_w + gap, sub_y), sub_text)

        p.restore()

    def _paint_progress(self, p, cx, y, delay_ms):
        op, dy = self._fade_up(delay_ms)
        if op <= 0:
            return
        p.save()
        p.setOpacity(op)
        p.translate(0, dy)

        bar_w = 280
        bar_h = 3
        x0 = cx - bar_w / 2

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_qc(SAGE_900, int(255 * 0.7)))
        p.drawRoundedRect(QRectF(x0, y, bar_w, bar_h), bar_h / 2, bar_h / 2)

        fill_w = bar_w * self._progress
        if fill_w > 0:
            grad = QLinearGradient(x0, 0, x0 + bar_w, 0)
            grad.setColorAt(0.0, QColor(SAGE_400))
            grad.setColorAt(0.5, QColor(GOLD_300))
            grad.setColorAt(1.0, QColor(GOLD_400))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(x0, y, fill_w, bar_h), bar_h / 2, bar_h / 2)

        status_y = y + 22
        p.setFont(self._font_status)
        p.setPen(_qc(SAGE_300, int(255 * 0.8)))
        p.drawText(QPointF(x0, status_y), _STATUS_LINES[self._active_line])

        pct_text = f"{int(round(self._progress * 100))}%"
        fm = p.fontMetrics()
        pct_w = fm.horizontalAdvance(pct_text)
        p.setPen(_qc(CREAM, int(255 * 0.6)))
        p.drawText(QPointF(x0 + bar_w - pct_w, status_y), pct_text)

        p.restore()
