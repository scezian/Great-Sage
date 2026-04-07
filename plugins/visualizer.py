"""
visualizer.py — Great Sage Plugin
Reactive FFT visualizer. Captures system audio, displays as sleek frequency bars.
"""

PLUGIN_NAME        = "Visualizer"
PLUGIN_ICON        = "▋"
PLUGIN_DESCRIPTION = "Reactive audio visualizer on the Dashboard"
PLUGIN_VERSION     = "2.0"
PLUGIN_AUTHOR      = "Great Sage"
PLUGIN_COLOR       = "#00E5CC"

import math, threading, time
from PyQt6.QtCore    import Qt, QTimer, QPointF
from PyQt6.QtGui     import (QColor, QPainter, QLinearGradient, QBrush,
                              QPen, QPainterPath, QRadialGradient)
from PyQt6.QtCore    import QRectF
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox

# ── Palettes ───────────────────────────────────────────────────────────────────
PALETTES = {
    "Ice":     ["#0A1628", "#1A4A8A", "#2A8AE0", "#7AD0FF"],
    "Amber":   ["#1A0A00", "#8A4400", "#C9A84C", "#FFE4A0"],
    "Neon":    ["#0D0014", "#6600CC", "#00FFCC", "#ADFF02"],
    "Crimson": ["#14000A", "#8A0030", "#E05C6A", "#FFB0C0"],
    "Mono":    ["#080B0F", "#1E2D3D", "#4A6070", "#D4E4EE"],
}
PAL_NAMES = list(PALETTES.keys())


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
        self.gain     = 2.0   # amplification multiplier (1.0 = no boost)

    def start(self, device=None):
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
        import subprocess as _sp
        prev = np.zeros(self.BAR_COUNT)

        # Determine source: saved device OR auto-detect monitor
        device = self._device or self._find_monitor()

        # Try parec first (works with PipeWire monitor sources by name)
        # Fall back to sounddevice if parec not available
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

        # Fallback: sounddevice
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
        self._peak = max(pk, self._peak * 0.995 + pk * 0.005, 1e-6)
        norm     = np.clip((mags * self.gain) / self._peak, 0.0, 1.0)
        # Power curve: boost small signals
        norm     = np.power(norm, 0.6)
        # Reverse so bass is on left
        norm     = norm[::-1]
        smoothed = self.SMOOTHING * prev + (1.0 - self.SMOOTHING) * norm
        prev[:]  = smoothed
        with self._lock:
            self._bars = smoothed.tolist()

    def _run_parec(self, device, prev):
        """Capture audio via parec — works with PipeWire monitor sources."""
        import numpy as np, subprocess as _sp, struct
        SAMPLE_RATE = 44100
        CHANNELS    = 1
        CHUNK       = self.BLOCK_SIZE * 2  # 16-bit samples = 2 bytes each

        cmd = [
            "parec",
            "--device", device,
            "--rate", str(SAMPLE_RATE),
            "--channels", str(CHANNELS),
            "--format", "s16le",
            "--latency-msec", "50",
        ]
        try:
            proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.DEVNULL)
        except Exception as e:
            raise RuntimeError(f"parec failed: {e}")

        try:
            while self._running:
                raw = proc.stdout.read(CHUNK * CHANNELS * 2)
                if not raw:
                    break
                samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                if len(samples) >= self.BLOCK_SIZE:
                    self._process_chunk(samples[:self.BLOCK_SIZE], prev)
        finally:
            try: proc.terminate()
            except Exception: pass  # Ignored

    def _find_monitor(self):
        """Find the best audio monitor source — checks PipeWire/pactl first."""
        import subprocess as _sp, shutil
        # Try pactl to find a running monitor (works on PipeWire)
        if shutil.which("pactl"):
            try:
                r = _sp.run(["pactl", "list", "sources", "short"],
                            capture_output=True, text=True, timeout=3)
                lines = r.stdout.strip().splitlines()
                # Prefer bluetooth monitor if running
                for line in lines:
                    if "monitor" in line and "RUNNING" in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            return parts[1]
                # Fall back to any monitor
                for line in lines:
                    if "monitor" in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            return parts[1]
            except Exception:
                pass
        # Fallback: sounddevice
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
        self._mirror   = True
        self._bars     = [0.0] * AudioCapture.BAR_COUNT
        self._peaks    = [0.0] * AudioCapture.BAR_COUNT  # peak hold
        self._paused   = False
        self.setFixedHeight(self.HEIGHT)
        self.setMinimumWidth(300)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(28)

    def cycle_palette(self):
        self._pal_idx = (self._pal_idx + 1) % len(PAL_NAMES)

    def toggle_mirror(self): self._mirror = not self._mirror
    def toggle_pause(self):
        self._paused = not self._paused
        return self._paused

    def palette_name(self): return PAL_NAMES[self._pal_idx]

    def _tick(self):
        if not self._paused:
            new = self._capture.get_bars()
            # Update peak hold (slow decay)
            for i, v in enumerate(new):
                if v > self._peaks[i]:
                    self._peaks[i] = v
                else:
                    self._peaks[i] = max(0.0, self._peaks[i] - 0.008)
            self._bars = new
        self.update()

    def _get_colors(self):
        return [QColor(c) for c in PALETTES[PAL_NAMES[self._pal_idx]]]

    def _bar_color(self, frac, colors):
        """Interpolate colour along palette based on bar height."""
        n   = len(colors) - 1
        pos = frac * n
        i   = min(int(pos), n - 1)
        t   = pos - i
        c1, c2 = colors[i], colors[i + 1]
        return QColor(
            int(c1.red()   + t * (c2.red()   - c1.red())),
            int(c1.green() + t * (c2.green() - c1.green())),
            int(c1.blue()  + t * (c2.blue()  - c1.blue())),
            220,
        )

    def paintEvent(self, _):
        p  = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H  = self.width(), self.height()
        colors = self._get_colors()
        bg     = colors[0]

        # Background — solid dark with subtle gradient
        bg_grad = QLinearGradient(0, 0, 0, H)
        bg_grad.setColorAt(0, QColor(bg.red(), bg.green(), bg.blue(), 255))
        bg_grad.setColorAt(1, QColor(0, 0, 0, 255))
        p.fillRect(0, 0, W, H, QBrush(bg_grad))

        # Subtle centre glow
        accent = colors[-1]
        glow = QRadialGradient(W / 2, H, H * 1.2)
        glow.setColorAt(0, QColor(accent.red(), accent.green(), accent.blue(), 18))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(0, 0, W, H, QBrush(glow))

        n     = AudioCapture.BAR_COUNT
        gap   = 2.0
        bar_w = max(2.0, (W - gap * (n - 1)) / n)  # float to fill exactly
        bars  = list(self._bars)
        peaks = list(self._peaks)

        p.setPen(Qt.PenStyle.NoPen)

        for i in range(n):
            # Mirror: fold so centre bars = low freq edges = high freq
            if self._mirror:
                # Left half: bars[half-1] down to bars[0] (low freq at centre)
                # Right half: bars[0] up to bars[half-1] (mirror)
                half = n // 2
                if i < half:
                    src = half - 1 - i
                else:
                    src = i - half
                src = max(0, min(src, n // 2 - 1))
            else:
                src = i
            frac = bars[src]
            pk   = peaks[src]

            x  = float(i) * (bar_w + gap)
            bh = max(2.0, frac * (H - 8))

            # Bar gradient — bottom to top
            if bh > 2:
                bar_grad = QLinearGradient(x, H, x, H - bh)
                c_lo = self._bar_color(0.0, colors)
                c_hi = self._bar_color(frac, colors)
                c_lo.setAlpha(160)
                c_hi.setAlpha(230)
                bar_grad.setColorAt(0, c_lo)
                bar_grad.setColorAt(1, c_hi)
                p.setBrush(QBrush(bar_grad))

                path = QPainterPath()
                r    = min(2.0, bar_w / 2.0)
                path.addRoundedRect(x, float(H) - bh, bar_w, bh, r, r)
                p.drawPath(path)

            # Peak hold dot
            if pk > 0.04:
                py    = max(1.0, float(H) - pk * (H - 8) - 2)
                pk_color = self._bar_color(pk, colors)
                pk_color.setAlpha(255)
                p.setBrush(QBrush(pk_color))
                p.drawRect(QRectF(x, py, bar_w, 1.5))

        # Top reflection line
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

        self._pal_btn  = _btn(f"◐ {self._canvas.palette_name()}", "Cycle palette", self._cycle_pal)
        self._mir_btn  = _btn("⇌ MIR", "Toggle mirror", self._toggle_mirror)
        self._pause_btn = _btn("⏸", "Pause", self._toggle_pause)
        self._err_lbl  = QLabel("")
        self._err_lbl.setStyleSheet("color:#6A2030; font-size:9px;")

        ch.addWidget(self._pal_btn)
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
    if _capture is None or not _capture._running:
        _capture = AudioCapture()
        _capture.start()
    return _capture


# ── Plugin API ─────────────────────────────────────────────────────────────────
def build_page(parent, api):
    global _dashboard_widget
    colours = api.colours
    cap     = _get_capture()

    # ── Restore saved preferences ──────────────────────────────────────────────
    saved_pal    = api.load_plugin_data("palette")  # e.g. "Ice"
    saved_mirror = api.load_plugin_data("mirror")   # True/False
    saved_device = api.load_plugin_data("device")   # device name string or None

    # Apply saved device to capture immediately
    if saved_device:
        cap.stop()
        cap._device = saved_device
        cap.start()

    page = QWidget(parent)
    page.setStyleSheet(f"background:{colours['BG']};")
    v = QVBoxLayout(page)
    v.setContentsMargins(40, 32, 40, 32)
    v.setSpacing(16)

    v.addWidget(api.make_label("\u25cb  VISUALIZER", colours["ACCENT"], 18, bold=True))
    v.addWidget(api.make_label(
        "Reactive FFT visualizer — reacts to system audio in real time.",
        colours["TEXT2"], 13))

    status_lbl = api.make_label("Starting audio capture...", colours["MUTED"], 11)
    v.addWidget(status_lbl)

    # Preview
    preview = VisualizerWidget(cap)
    v.addWidget(preview)

    # Apply saved palette/mirror to both preview and dashboard canvas
    if saved_pal and saved_pal in PAL_NAMES:
        pal_idx = PAL_NAMES.index(saved_pal)
        preview._canvas._pal_idx = pal_idx
        preview._pal_btn.setText(f"\u25d0 {saved_pal}")
        if _dashboard_widget:
            _dashboard_widget._canvas._pal_idx = pal_idx
    if saved_mirror is not None:
        mirror_val = bool(saved_mirror)
        preview._canvas._mirror = mirror_val
        preview._mir_btn.setText("\u21cc MIR" if mirror_val else "\u25b6 SEQ")
        if _dashboard_widget:
            _dashboard_widget._canvas._mirror = mirror_val

    # Patch buttons to also persist on change
    _orig_cycle = preview._cycle_pal
    def _cycle_and_save():
        _orig_cycle()
        api.save_plugin_data("palette", preview._canvas.palette_name())
        if _dashboard_widget:
            _dashboard_widget._canvas._pal_idx = preview._canvas._pal_idx
    preview._pal_btn.clicked.disconnect()
    preview._pal_btn.clicked.connect(_cycle_and_save)

    _orig_mirror = preview._toggle_mirror
    def _mirror_and_save():
        _orig_mirror()
        api.save_plugin_data("mirror", preview._canvas._mirror)
        if _dashboard_widget:
            _dashboard_widget._canvas._mirror = preview._canvas._mirror
    preview._mir_btn.clicked.disconnect()
    preview._mir_btn.clicked.connect(_mirror_and_save)

    # Device selector
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
        # Add PipeWire/pactl monitor sources first
        if shutil.which("pactl"):
            try:
                r = _sp.run(["pactl", "list", "sources", "short"],
                            capture_output=True, text=True, timeout=3)
                for line in r.stdout.strip().splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        name = parts[1]
                        label = name
                        if "monitor" in name:
                            label = "● " + name  # mark monitors
                        dev_combo.addItem(label, name)
                        added.add(name)
                        if name == saved_device:
                            saved_idx = dev_combo.count() - 1
            except Exception:
                pass
        # Add sounddevice inputs
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
        if _capture: _capture.stop()
        _capture = AudioCapture()
        _capture.start(device=device)
        for w in (preview, _dashboard_widget):
            if w:
                w._capture = _capture
                w._canvas._capture = _capture
        api.save_plugin_data("device", device)

    dev_combo.currentIndexChanged.connect(_on_device)
    row.addWidget(dev_combo, 1)
    v.addLayout(row)

    hint = api.make_label(
        "Select a ● monitor source from the dropdown. The Bluetooth monitor works best when headphones are connected.",
        colours["MUTED"], 10)
    hint.setWordWrap(True)
    v.addWidget(hint)

    # ── Sensitivity/gain slider ────────────────────────────────────────────────
    from PyQt6.QtWidgets import QSlider
    from PyQt6.QtCore    import Qt

    saved_gain = api.load_plugin_data("gain")
    if saved_gain is None:
        saved_gain = 20  # default: 2.0x (stored as int 1-100)

    gain_hdr = api.make_label("SENSITIVITY", colours["MUTED"], 9)
    v.addWidget(gain_hdr)

    gain_row = QHBoxLayout()
    gain_row.setSpacing(12)
    gain_sl = QSlider(Qt.Orientation.Horizontal)
    gain_sl.setRange(5, 100)   # 0.5x to 10x
    gain_sl.setValue(int(saved_gain))
    gain_sl.setFixedWidth(200)
    gain_sl.setStyleSheet(
        "QSlider::groove:horizontal{background:#252530;height:3px;border-radius:2px;}"
        "QSlider::sub-page:horizontal{background:#4EC9A4;border-radius:2px;}"
        "QSlider::handle:horizontal{background:#4EC9A4;width:12px;height:12px;"
        "margin:-5px 0;border-radius:6px;}")
    gain_lbl = api.make_label(f"{saved_gain/10:.1f}x", colours["TEXT2"], 11)

    def _gain_changed(v_):
        gain_sl.setValue(v_)
        gain_lbl.setText(f"{v_/10:.1f}x")
        g = v_ / 10.0
        cap.gain = g
        if _dashboard_widget:
            _dashboard_widget._canvas._capture.gain = g
        api.save_plugin_data("gain", v_)

    gain_sl.valueChanged.connect(_gain_changed)

    # Apply saved gain immediately
    cap.gain = saved_gain / 10.0

    gain_row.addWidget(gain_sl)
    gain_row.addWidget(gain_lbl)
    gain_row.addStretch()
    v.addLayout(gain_row)
    v.addStretch()

    # Register into dashboard slot
    if _dashboard_widget is None:
        _dashboard_widget = VisualizerWidget(cap)
    # Sync dashboard widget to saved prefs
    if saved_pal and saved_pal in PAL_NAMES:
        _dashboard_widget._canvas._pal_idx = PAL_NAMES.index(saved_pal)
    if saved_mirror is not None:
        _dashboard_widget._canvas._mirror = bool(saved_mirror)
    api.register_slot("dashboard_top", _dashboard_widget)

    def _update_status():
        if cap.error:
            status_lbl.setText(f"\u26a0  {cap.error[:80]}")
            status_lbl.setStyleSheet(f"color:{colours['RED']}; font-size:11px;")
        else:
            status_lbl.setText("\u25cf Capturing system audio")
            status_lbl.setStyleSheet(f"color:{colours['ACCENT2']}; font-size:11px;")
    QTimer.singleShot(1500, _update_status)

    return page


def refresh(page):
    pass
