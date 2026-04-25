"""
visualizer.py — Great Sage Plugin
Reactive FFT visualizer. Captures system audio, displays as sleek frequency bars.
"""

PLUGIN_NAME        = "Visualizer"
PLUGIN_ICON        = "▋"
PLUGIN_DESCRIPTION = "Reactive audio visualizer on the Dashboard"
PLUGIN_VERSION     = "3.1"
PLUGIN_AUTHOR      = "Great Sage"
PLUGIN_COLOR       = "#00E5CC"

import math, threading, time, select, os, json
from PyQt6.QtCore    import Qt, QTimer, QPointF
from PyQt6.QtGui     import (QColor, QPainter, QLinearGradient, QBrush,
                              QPen, QPainterPath, QRadialGradient)
from PyQt6.QtCore    import QRectF
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox

try:
    from gs_logger import log as _gs_log
    log = _gs_log.plugin
except Exception:
    class _NoopLog:
        def __getattr__(self, name): return lambda *a, **kw: None
    log = _NoopLog()

# ── Settings persistence (own file — api.save_plugin_data silently fails) ─────
_SETTINGS_FILE = os.path.join(
    os.path.expanduser("~"), ".config", "great_sage", "visualizer_settings.json"
)

def _load_settings() -> dict:
    try:
        if os.path.exists(_SETTINGS_FILE):
            with open(_SETTINGS_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_settings(data: dict):
    try:
        os.makedirs(os.path.dirname(_SETTINGS_FILE), exist_ok=True)
        tmp = _SETTINGS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _SETTINGS_FILE)
    except Exception:
        pass

# ── Palettes ───────────────────────────────────────────────────────────────────
PALETTES = {
    "Ice":     ["#0A1628", "#1A4A8A", "#2A8AE0", "#7AD0FF"],
    "Amber":   ["#1A0A00", "#8A4400", "#C9A84C", "#FFE4A0"],
    "Neon":    ["#0D0014", "#6600CC", "#00FFCC", "#ADFF02"],
    "Crimson": ["#14000A", "#8A0030", "#E05C6A", "#FFB0C0"],
    "Mono":    ["#080B0F", "#1E2D3D", "#4A6070", "#D4E4EE"],
    "Violet":  ["#0A0020", "#3A0080", "#9A40FF", "#E0C0FF"],
    "Sunset":  ["#1A0500", "#8A2000", "#FF6820", "#FFD080"],
    "Aurora":  ["#000A10", "#004A3A", "#00C87A", "#A0FFD0"],
}
PAL_NAMES = list(PALETTES.keys())

# ── Bar modes ──────────────────────────────────────────────────────────────────
BAR_MODES = ["Bars", "Wave", "Blocks", "Mirror Bars", "Circles", "Spikes"]


# ── Audio capture ──────────────────────────────────────────────────────────────
class AudioCapture:
    BAR_COUNT   = 60
    SAMPLE_RATE = 44100
    BLOCK_SIZE  = 2048
    SMOOTHING   = 0.65

    def __init__(self):
        self._bars    = [0.0] * self.BAR_COUNT
        self._lock    = threading.Lock()
        self._running = False
        self._peak    = 1e-6
        self.error    = ""
        self.gain     = 2.0
        self._device  = None

    def start(self, device=None):
        if device is not None:
            self._device  = device
        self._running = True
        self.error    = ""
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._running = False

    def get_bars(self):
        with self._lock:
            return list(self._bars)

    def _run(self):
        import numpy as np
        prev = np.zeros(self.BAR_COUNT)

        device = self._device or self._find_monitor()

        import shutil
        use_parec = bool(shutil.which("parec")) and device

        if use_parec:
            try:
                self._run_parec(device, prev)
                return
            except Exception as e:
                self.error = str(e)
                self._running = False
                return

        try:
            import sounddevice as sd
            try:
                dev_info = sd.query_devices(device, "input")
                sample_rate = int(dev_info["default_samplerate"])
            except Exception:
                sample_rate = self.SAMPLE_RATE

            def _cb(indata, frames, t, status):
                if not self._running: return
                try:
                    self._process_chunk(indata[:, 0] if indata.ndim > 1 else indata.ravel(), prev)
                except Exception:
                    pass

            with sd.InputStream(device=device, channels=1, samplerate=sample_rate,
                                 blocksize=self.BLOCK_SIZE, callback=_cb, latency="low"):
                while self._running:
                    time.sleep(0.05)
        except Exception as e:
            self.error    = str(e)
            self._running = False

    def _process_chunk(self, mono, prev):
        import numpy as np
        win   = np.hanning(len(mono))
        fft   = np.abs(np.fft.rfft(mono * win, n=self.BLOCK_SIZE))
        freqs = np.fft.rfftfreq(self.BLOCK_SIZE, 1.0 / self.SAMPLE_RATE)
        lo, hi = math.log10(30), math.log10(18000)
        bands  = np.logspace(lo, hi, self.BAR_COUNT + 1, base=10)
        mags   = np.array([
            np.mean(fft[(freqs >= bands[i]) & (freqs < bands[i+1])])
            if ((freqs >= bands[i]) & (freqs < bands[i+1])).any() else 0.0
            for i in range(self.BAR_COUNT)
        ])
        pk = mags.max()
        if pk > self._peak * 0.1:
            self._peak = pk
        else:
            self._peak = max(pk, self._peak * 0.995 + pk * 0.005, 1e-6)
        norm     = np.clip((mags * self.gain) / self._peak, 0.0, 1.0)
        norm     = np.power(norm, 0.6)
        norm     = norm[::-1]
        smoothed = self.SMOOTHING * prev + (1.0 - self.SMOOTHING) * norm
        prev[:]  = smoothed
        with self._lock:
            self._bars = smoothed.tolist()

    def _run_parec(self, device, prev):
        import numpy as np
        CHUNK = self.BLOCK_SIZE * 2

        cmd = [
            "parec",
            "--device", device,
            "--rate", str(self.SAMPLE_RATE),
            "--channels", "1",
            "--format", "s16le",
            "--latency-msec", "50",
        ]
        try:
            import subprocess as _sp
            proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.DEVNULL)
        except Exception as e:
            raise RuntimeError(f"parec failed: {e}")

        try:
            while self._running:
                r, _, _ = select.select([proc.stdout], [], [], 0.5)
                if r:
                    raw = proc.stdout.read(CHUNK * 2)
                    if not raw:
                        break
                    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                    if len(samples) >= self.BLOCK_SIZE:
                        self._process_chunk(samples[:self.BLOCK_SIZE], prev)
        finally:
            try: proc.terminate()
            except Exception: pass

    def _find_monitor(self):
        import subprocess as _sp, shutil
        if shutil.which("pactl"):
            try:
                r = _sp.run(["pactl", "list", "sources", "short"],
                            capture_output=True, text=True, timeout=3)
                lines = r.stdout.strip().splitlines()
                for line in lines:
                    if "monitor" in line and "RUNNING" in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            return parts[1]
                for line in lines:
                    if "monitor" in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            return parts[1]
            except Exception:
                pass
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            for d in devices:
                if d["name"].lower() == "cava" and d["max_input_channels"] > 0:
                    return d["name"]
            for p in ["pipewire", "pulse"]:
                for d in devices:
                    if p.lower() in d["name"].lower() and d["max_input_channels"] > 0:
                        return d["name"]
        except Exception:
            pass
        return None


# ── Canvas ─────────────────────────────────────────────────────────────────────
class VisualizerCanvas(QWidget):
    HEIGHT = 96

    def __init__(self, capture, parent=None):
        super().__init__(parent)
        self._capture  = capture
        self._pal_idx  = 0
        self._mode_idx = 0
        self._mirror   = True
        self._bars     = [0.0] * AudioCapture.BAR_COUNT
        self._peaks    = [0.0] * AudioCapture.BAR_COUNT
        self._paused   = False
        self.setFixedHeight(self.HEIGHT)
        self.setMinimumWidth(300)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(28)

    def cycle_palette(self):
        self._pal_idx = (self._pal_idx + 1) % len(PAL_NAMES)

    def cycle_mode(self):
        self._mode_idx = (self._mode_idx + 1) % len(BAR_MODES)

    def toggle_mirror(self): self._mirror = not self._mirror
    def toggle_pause(self):
        self._paused = not self._paused
        return self._paused

    def palette_name(self): return PAL_NAMES[self._pal_idx]
    def mode_name(self):    return BAR_MODES[self._mode_idx]

    def set_palette_by_name(self, name: str):
        if name in PALETTES:
            self._pal_idx = PAL_NAMES.index(name)
            self.update()

    def set_mode_by_name(self, name: str):
        if name in BAR_MODES:
            self._mode_idx = BAR_MODES.index(name)
            self.update()

    def set_mirror(self, enabled: bool):
        self._mirror = enabled
        self.update()

    def _tick(self):
        # Lazy start — begin capture on first tick, not at plugin load
        if not self._capture._running:
            self._capture.start()
        if not self._paused:
            new = self._capture.get_bars()
            for i, v in enumerate(new):
                if v > self._peaks[i]:
                    self._peaks[i] = v
                else:
                    self._peaks[i] = max(0.0, self._peaks[i] - 0.008)
            self._bars = new
        self.update()

    def _get_colors(self):
        return [QColor(c) for c in PALETTES[PAL_NAMES[self._pal_idx]]]

    def _bar_color(self, frac, colors, alpha=220):
        n   = len(colors) - 1
        pos = frac * n
        i   = min(int(pos), n - 1)
        t   = pos - i
        c1, c2 = colors[i], colors[i + 1]
        return QColor(
            int(c1.red()   + t * (c2.red()   - c1.red())),
            int(c1.green() + t * (c2.green() - c1.green())),
            int(c1.blue()  + t * (c2.blue()  - c1.blue())),
            alpha,
        )

    def _mirrored_bars(self, src):
        n = len(src)
        result = []
        for i in range(n):
            half = n // 2
            s = (half - 1 - i) if i < half else (i - half)
            s = max(0, min(s, half - 1))
            result.append(src[s])
        return result

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H   = self.width(), self.height()
        colors = self._get_colors()
        bg     = colors[0]

        bg_grad = QLinearGradient(0, 0, 0, H)
        bg_grad.setColorAt(0, QColor(bg.red(), bg.green(), bg.blue(), 255))
        bg_grad.setColorAt(1, QColor(0, 0, 0, 255))
        p.fillRect(0, 0, W, H, QBrush(bg_grad))

        accent = colors[-1]
        glow = QRadialGradient(W / 2, H, H * 1.2)
        glow.setColorAt(0, QColor(accent.red(), accent.green(), accent.blue(), 18))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(0, 0, W, H, QBrush(glow))

        n     = AudioCapture.BAR_COUNT
        gap   = 2.0
        bar_w = max(2.0, (W - gap * (n - 1)) / n)
        bars  = self._mirrored_bars(list(self._bars)) if self._mirror else list(self._bars)
        peaks = self._mirrored_bars(list(self._peaks)) if self._mirror else list(self._peaks)

        p.setPen(Qt.PenStyle.NoPen)
        mode = self._mode_idx

        if mode == 0:
            for i in range(n):
                frac = bars[i]
                x    = float(i) * (bar_w + gap)
                bh   = max(2.0, frac * (H - 8))
                if bh > 2:
                    bar_grad = QLinearGradient(x, H, x, H - bh)
                    c_lo = self._bar_color(0.0, colors, 160)
                    c_hi = self._bar_color(frac, colors, 230)
                    bar_grad.setColorAt(0, c_lo)
                    bar_grad.setColorAt(1, c_hi)
                    p.setBrush(QBrush(bar_grad))
                    path = QPainterPath()
                    r    = min(2.0, bar_w / 2.0)
                    path.addRoundedRect(x, float(H) - bh, bar_w, bh, r, r)
                    p.drawPath(path)
                if peaks[i] > 0.04:
                    py       = max(1.0, float(H) - peaks[i] * (H - 8) - 2)
                    pk_color = self._bar_color(peaks[i], colors, 255)
                    p.setBrush(QBrush(pk_color))
                    p.drawRect(QRectF(x, py, bar_w, 1.5))

        elif mode == 1:
            path = QPainterPath()
            for i in range(n):
                x = float(i) * (bar_w + gap) + bar_w / 2.0
                y = float(H) - bars[i] * (H - 12) - 4
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            stroke_col = self._bar_color(0.8, colors, 200)
            p.setPen(QPen(stroke_col, 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            fill_path = QPainterPath(path)
            fill_path.lineTo(float(W), float(H))
            fill_path.lineTo(0.0, float(H))
            fill_path.closeSubpath()
            fill_grad = QLinearGradient(0, 0, 0, H)
            fill_grad.setColorAt(0, self._bar_color(0.9, colors, 90))
            fill_grad.setColorAt(1, self._bar_color(0.1, colors, 8))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(fill_grad))
            p.drawPath(fill_path)

        elif mode == 2:
            segs   = 8
            seg_h  = (H - 10) / segs - 1
            for i in range(n):
                frac   = bars[i]
                x      = float(i) * (bar_w + gap)
                filled = round(frac * segs)
                for s in range(filled):
                    f   = s / segs
                    sy  = float(H) - 4 - (s + 1) * (seg_h + 1)
                    col = self._bar_color(f, colors, 200 + int(55 * f))
                    p.setBrush(QBrush(col))
                    p.drawRect(QRectF(x, sy, bar_w, seg_h))

        elif mode == 3:
            cy = float(H) / 2.0
            for i in range(n):
                frac = bars[i]
                x    = float(i) * (bar_w + gap)
                bh   = max(2.0, frac * (cy - 6))
                top_grad = QLinearGradient(x, cy, x, cy - bh)
                top_grad.setColorAt(0, self._bar_color(0.1, colors, 80))
                top_grad.setColorAt(1, self._bar_color(frac, colors, 230))
                p.setBrush(QBrush(top_grad))
                path = QPainterPath()
                path.addRoundedRect(x, cy - bh, bar_w, bh, min(2.0, bar_w / 2.0), min(2.0, bar_w / 2.0))
                p.drawPath(path)
                bot_grad = QLinearGradient(x, cy, x, cy + bh)
                bot_grad.setColorAt(0, self._bar_color(0.1, colors, 60))
                bot_grad.setColorAt(1, self._bar_color(frac, colors, 130))
                p.setBrush(QBrush(bot_grad))
                path2 = QPainterPath()
                path2.addRoundedRect(x, cy, bar_w, bh, min(2.0, bar_w / 2.0), min(2.0, bar_w / 2.0))
                p.drawPath(path2)
            p.setPen(QPen(self._bar_color(0.5, colors, 50), 1.0))
            p.drawLine(QPointF(0, cy), QPointF(float(W), cy))
            p.setPen(Qt.PenStyle.NoPen)

        elif mode == 4:
            cx    = float(W) / 2.0
            cy    = float(H) / 2.0
            r0    = 14.0
            r_max = min(cx, cy) - 4.0
            step  = math.pi * 2.0 / n
            for i in range(n):
                frac  = bars[i]
                angle = i * step - math.pi / 2.0
                r     = r0 + frac * (r_max - r0)
                x0    = cx + math.cos(angle) * r0
                y0    = cy + math.sin(angle) * r0
                x1    = cx + math.cos(angle) * r
                y1    = cy + math.sin(angle) * r
                col   = self._bar_color(frac, colors, 180 + int(75 * frac))
                p.setPen(QPen(col, max(1.0, bar_w * 0.6)))
                p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
            p.setPen(Qt.PenStyle.NoPen)

        elif mode == 5:
            for i in range(n):
                frac = bars[i]
                x    = float(i) * (bar_w + gap)
                bh   = max(2.0, frac * (H - 8))
                col  = self._bar_color(frac, colors, 210)
                p.setBrush(QBrush(col))
                path = QPainterPath()
                path.moveTo(x + bar_w / 2.0, float(H) - bh)
                path.lineTo(x, float(H))
                path.lineTo(x + bar_w, float(H))
                path.closeSubpath()
                p.drawPath(path)
                if peaks[i] > 0.06:
                    py       = max(1.0, float(H) - peaks[i] * (H - 8) - 2)
                    pk_color = self._bar_color(peaks[i], colors, 255)
                    p.setBrush(QBrush(pk_color))
                    p.drawRect(QRectF(x + bar_w / 2.0 - 0.5, py, 1.0, 1.5))

        line_col = QColor(accent.red(), accent.green(), accent.blue(), 30)
        p.setPen(QPen(line_col, 1))
        p.drawLine(0, 0, W, 0)
        p.end()


# ── Control bar ────────────────────────────────────────────────────────────────
class VisualizerWidget(QWidget):
    CTRL_H = 28

    def __init__(self, capture, parent=None):
        super().__init__(parent)
        self._capture = capture
        self.setStyleSheet("background:#000000;")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._canvas = VisualizerCanvas(capture, self)
        root.addWidget(self._canvas)

        ctrl = QWidget()
        ctrl.setFixedHeight(self.CTRL_H)
        ctrl.setStyleSheet("background:#08080C; border-top:1px solid #1A1A28;")
        ch = QHBoxLayout(ctrl)
        ch.setContentsMargins(14, 0, 14, 0)
        ch.setSpacing(6)

        def _btn(label, tip, cb):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.setFixedHeight(20)
            b.setStyleSheet(
                "QPushButton{background:transparent;border:none;"
                "color:#303045;font-size:9px;letter-spacing:1.5px;padding:0 6px;}"
                "QPushButton:hover{color:#C9A84C;}")
            b.clicked.connect(cb)
            return b

        self._pal_btn   = _btn(f"◐ {self._canvas.palette_name()}", "Cycle palette",   self._cycle_pal)
        self._mode_btn  = _btn(f"▋ {self._canvas.mode_name()}",    "Cycle bar style", self._cycle_mode)
        self._mir_btn   = _btn("⇌ MIR",                            "Toggle mirror",   self._toggle_mirror)
        self._pause_btn = _btn("⏸",                                "Pause",           self._toggle_pause)
        self._err_lbl   = QLabel("")
        self._err_lbl.setStyleSheet("color:#6A2030; font-size:9px;")

        ch.addWidget(self._pal_btn)
        ch.addWidget(self._mode_btn)
        ch.addWidget(self._mir_btn)
        ch.addStretch()
        ch.addWidget(self._err_lbl)
        ch.addWidget(self._pause_btn)
        root.addWidget(ctrl)

        self._err_timer = QTimer(self)
        self._err_timer.timeout.connect(self._check_err)
        self._err_timer.start(2000)

    def _cycle_pal(self):
        self._canvas.cycle_palette()
        self._pal_btn.setText(f"◐ {self._canvas.palette_name()}")

    def _cycle_mode(self):
        self._canvas.cycle_mode()
        self._mode_btn.setText(f"▋ {self._canvas.mode_name()}")

    def _toggle_mirror(self):
        self._canvas.toggle_mirror()
        self._mir_btn.setText("⇌ MIR" if self._canvas._mirror else "▶ SEQ")

    def _toggle_pause(self):
        paused = self._canvas.toggle_pause()
        self._pause_btn.setText("▶" if paused else "⏸")

    def _check_err(self):
        err = self._capture.error
        self._err_lbl.setText(f"⚠ {err[:40]}" if err else "")


# ── Plugin globals ─────────────────────────────────────────────────────────────
_capture          = None
_dashboard_widget = None


def _get_capture():
    global _capture
    if _capture is None:
        _capture = AudioCapture()
        # Don't start yet — canvas starts it lazily on first tick
    return _capture


def _apply_prefs_to_widget(w, saved_pal, saved_mirror, saved_mode):
    """Apply saved preferences to any VisualizerWidget."""
    if saved_pal and saved_pal in PAL_NAMES:
        w._canvas.set_palette_by_name(saved_pal)
        w._pal_btn.setText(f"◐ {saved_pal}")
    if saved_mirror is not None:
        w._canvas.set_mirror(bool(saved_mirror))
        w._mir_btn.setText("⇌ MIR" if bool(saved_mirror) else "▶ SEQ")
    if saved_mode and saved_mode in BAR_MODES:
        w._canvas.set_mode_by_name(saved_mode)
        w._mode_btn.setText(f"▋ {saved_mode}")


# ── Plugin API ─────────────────────────────────────────────────────────────────
def build_page(parent, api):
    global _dashboard_widget, _capture
    colours = api.colours
    cap     = _get_capture()

    saved        = _load_settings()
    saved_pal    = saved.get("palette")
    saved_mirror = saved.get("mirror")
    saved_device = saved.get("device")
    saved_mode   = saved.get("mode")
    saved_gain   = saved.get("gain")
    if saved_gain is None:
        saved_gain = 20

    # Apply saved device — store on capture so lazy start picks it up
    if saved_device:
        cap._device = saved_device

    # Apply saved gain
    cap.gain = saved_gain / 10.0

    page = QWidget(parent)
    page.setStyleSheet(f"background:{colours['BG']};")
    v = QVBoxLayout(page)
    v.setContentsMargins(40, 32, 40, 32)
    v.setSpacing(16)

    v.addWidget(api.make_label("○  VISUALIZER", colours["ACCENT"], 18, bold=True))
    v.addWidget(api.make_label(
        "Reactive FFT visualizer — reacts to system audio in real time.",
        colours["TEXT2"], 13))

    status_lbl = api.make_label("Starting audio capture...", colours["MUTED"], 11)
    v.addWidget(status_lbl)

    preview = VisualizerWidget(cap)
    v.addWidget(preview)

    # Apply saved prefs to preview
    _apply_prefs_to_widget(preview, saved_pal, saved_mirror, saved_mode)

    # ── Patch buttons to persist on change and sync dashboard ─────────────────
    _orig_cycle_pal = preview._cycle_pal
    def _cycle_pal_and_save():
        _orig_cycle_pal()
        name = preview._canvas.palette_name()
        _save_settings({**_load_settings(), "palette": name})
        if _dashboard_widget:
            _dashboard_widget._canvas.set_palette_by_name(name)
            _dashboard_widget._pal_btn.setText(f"◐ {name}")
    preview._pal_btn.clicked.disconnect()
    preview._pal_btn.clicked.connect(_cycle_pal_and_save)

    _orig_cycle_mode = preview._cycle_mode
    def _cycle_mode_and_save():
        _orig_cycle_mode()
        name = preview._canvas.mode_name()
        _save_settings({**_load_settings(), "mode": name})
        if _dashboard_widget:
            _dashboard_widget._canvas.set_mode_by_name(name)
            _dashboard_widget._mode_btn.setText(f"▋ {name}")
    preview._mode_btn.clicked.disconnect()
    preview._mode_btn.clicked.connect(_cycle_mode_and_save)

    _orig_mirror = preview._toggle_mirror
    def _mirror_and_save():
        _orig_mirror()
        val = preview._canvas._mirror
        _save_settings({**_load_settings(), "mirror": val})
        if _dashboard_widget:
            _dashboard_widget._canvas.set_mirror(val)
            _dashboard_widget._mir_btn.setText("⇌ MIR" if val else "▶ SEQ")
    preview._mir_btn.clicked.disconnect()
    preview._mir_btn.clicked.connect(_mirror_and_save)

    # ── Device selector ────────────────────────────────────────────────────────
    row = QHBoxLayout()
    row.addWidget(api.make_label("Device:", colours["MUTED"], 11))
    dev_combo = QComboBox()
    dev_combo.setFixedHeight(26)
    dev_combo.setStyleSheet(
        f"QComboBox{{background:#17171D;border:1px solid {colours['BORDER']};"
        f"color:{colours['TEXT']};padding:2px 8px;border-radius:2px;}}")
    try:
        import sounddevice as sd, subprocess as _sp, shutil
        dev_combo.addItem("auto", None)
        saved_idx = 0
        added = set()
        if shutil.which("pactl"):
            try:
                r = _sp.run(["pactl", "list", "sources", "short"],
                            capture_output=True, text=True, timeout=3)
                for line in r.stdout.strip().splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        name  = parts[1]
                        label = ("● " + name) if "monitor" in name else name
                        dev_combo.addItem(label, name)
                        added.add(name)
                        if name == saved_device:
                            saved_idx = dev_combo.count() - 1
            except Exception:
                pass
        for d in sd.query_devices():
            if d.get("max_input_channels", 0) > 0 and d["name"] not in added:
                dev_combo.addItem(d["name"], d["name"])
                if d["name"] == saved_device:
                    saved_idx = dev_combo.count() - 1
        if saved_idx:
            dev_combo.setCurrentIndex(saved_idx)
    except Exception as e:
        dev_combo.addItem(f"Error: {e}", None)

    def _on_device(idx):
        global _capture
        device = dev_combo.itemData(idx)
        if _capture:
            _capture.stop()
        new_cap = AudioCapture()
        new_cap.gain    = _capture.gain if _capture else cap.gain
        new_cap._device = device
        new_cap.start()
        _capture = new_cap
        for w in (preview, _dashboard_widget):
            if w:
                w._capture = new_cap
                w._canvas._capture = new_cap
        _save_settings({**_load_settings(), "device": device})

    dev_combo.currentIndexChanged.connect(_on_device)
    row.addWidget(dev_combo, 1)
    v.addLayout(row)

    hint = api.make_label(
        "Select a ● monitor source from the dropdown. "
        "The Bluetooth monitor works best when headphones are connected.",
        colours["MUTED"], 10)
    hint.setWordWrap(True)
    v.addWidget(hint)

    # ── Gain slider ────────────────────────────────────────────────────────────
    from PyQt6.QtWidgets import QSlider

    gain_hdr = api.make_label("SENSITIVITY", colours["MUTED"], 9)
    v.addWidget(gain_hdr)

    gain_row = QHBoxLayout()
    gain_row.setSpacing(12)
    gain_sl = QSlider(Qt.Orientation.Horizontal)
    gain_sl.setRange(5, 100)
    gain_sl.setValue(int(saved_gain))
    gain_sl.setFixedWidth(200)
    gain_sl.setStyleSheet(
        "QSlider::groove:horizontal{background:#252530;height:3px;border-radius:2px;}"
        "QSlider::sub-page:horizontal{background:#4EC9A4;border-radius:2px;}"
        "QSlider::handle:horizontal{background:#4EC9A4;width:12px;height:12px;"
        "margin:-5px 0;border-radius:6px;}")
    gain_lbl = api.make_label(f"{saved_gain/10:.1f}x", colours["TEXT2"], 11)

    def _gain_changed(v_):
        gain_lbl.setText(f"{v_/10:.1f}x")
        g = v_ / 10.0
        if _capture:
            _capture.gain = g
        if _dashboard_widget:
            _dashboard_widget._canvas._capture.gain = g
        _save_settings({**_load_settings(), "gain": v_})

    gain_sl.valueChanged.connect(_gain_changed)
    gain_row.addWidget(gain_sl)
    gain_row.addWidget(gain_lbl)
    gain_row.addStretch()
    v.addLayout(gain_row)
    v.addStretch()

    # ── Register dashboard slot ────────────────────────────────────────────────
    # Always rebuild dashboard widget so saved prefs are applied fresh on every launch
    _dashboard_widget = VisualizerWidget(cap)
    _apply_prefs_to_widget(_dashboard_widget, saved_pal, saved_mirror, saved_mode)
    _dashboard_widget._canvas._capture.gain = cap.gain

    api.register_slot("dashboard_top", _dashboard_widget)

    def _update_status():
        if cap.error:
            status_lbl.setText(f"⚠  {cap.error[:80]}")
            status_lbl.setStyleSheet(f"color:{colours['RED']}; font-size:11px;")
        else:
            status_lbl.setText("● Capturing system audio")
            status_lbl.setStyleSheet(f"color:{colours['ACCENT2']}; font-size:11px;")
    QTimer.singleShot(1500, _update_status)

    return page


def refresh(page):
    global _capture
    try:
        for child in page.findChildren(QLabel):
            txt = child.text()
            if "audio" in txt.lower() or "capture" in txt.lower() or "Starting" in txt:
                if _capture and _capture.error:
                    child.setText(f"⚠  {_capture.error[:80]}")
                    child.setStyleSheet("color:#E05C6A; font-size:11px;")
                elif _capture and _capture._running:
                    child.setText("● Capturing system audio")
                    child.setStyleSheet("color:#4EC9A4; font-size:11px;")
                break
    except Exception:
        pass
