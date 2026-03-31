# ambient.py — Great Sage Plugin (with audio device selection)
PLUGIN_NAME        = "Ambient Mode"
PLUGIN_ICON        = "✦"
PLUGIN_DESCRIPTION = "Dashboard background that pulses with your music"
PLUGIN_VERSION     = "1.7"
PLUGIN_AUTHOR      = "Great Sage"
PLUGIN_COLOR       = "#9A70E0"

import sys
import os
import json
import math
import threading
import time
import traceback
import numpy as np
import sounddevice as sd
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QLinearGradient, QRadialGradient, QBrush
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton, QComboBox, QApplication, QMainWindow

# Debug logging function
def debug_log(msg):
    """Print debug message with timestamp"""
    import datetime
    timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] AMBIENT: {msg}")
    sys.stdout.flush()

# ── Colour schemes ────────────────────────────────────────────────
SCHEMES = {
    "Ice":     {"lo": (5, 12, 28), "hi": (20, 80, 180), "orb": (100, 200, 255)},
    "Amber":   {"lo": (12, 6, 0),  "hi": (80, 44, 0),   "orb": (201, 168, 76)},
    "Neon":    {"lo": (8, 0, 14),  "hi": (60, 0, 120),  "orb": (0, 255, 200)},
    "Crimson": {"lo": (14, 0, 6),  "hi": (90, 0, 30),   "orb": (224, 92, 106)},
    "Mono":    {"lo": (8, 11, 15), "hi": (20, 30, 40),  "orb": (212, 228, 238)},
}
SCHEME_NAMES = list(SCHEMES.keys())

# ── Effect modes ───────────────────────────────────────────────────
EFFECT_MODES = ["Orbs", "Aurora", "Nebula", "Ripples"]

# Global instances
_audio_capture = None
_background_widget = None
_control_widget = None
_slot_registered = False
_current_device = None

# ── Audio capture ───────────────────────────────────────────────
class AudioCapture:
    BAR_COUNT = 60
    SAMPLE_RATE = 44100
    BLOCK_SIZE = 2048
    SMOOTHING = 0.75  # Reduced from 0.82 for faster response
    
    def __init__(self):
        self._bars = [0.0] * self.BAR_COUNT
        self._lock = threading.Lock()
        self._running = False
        self._peak = 1e-6
        self.error = ""
        self._thread = None
        self._stream = None
        self._device = None
        self._prev_bars = [0.0] * self.BAR_COUNT  # For detecting transients
        self._beat_detector = 0.0  # Simple beat detection
        self._test_mode = False  # For testing without audio input
        self._test_time = 0
        debug_log("AudioCapture initialized")
        
    def start(self, device=None):
        if self._running:
            debug_log("AudioCapture already running")
            return
        self._device = device
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        debug_log(f"AudioCapture started with device: {device}")
        
    def stop(self):
        debug_log("AudioCapture stopping")
        self._running = False
        if self._stream:
            try:
                self._stream.close()
            except:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        debug_log("AudioCapture stopped")
            
    def get_bars(self):
        with self._lock:
            return list(self._bars)
            
    def get_energy(self):
        """Return a simple energy value (0-1) for testing"""
        bars = self.get_bars()
        if bars:
            return sum(bars) / len(bars)
        return 0.0
    
    def get_beat(self):
        """Return beat detection value (0-1) for visual pulsing"""
        with self._lock:
            return self._beat_detector
    
    def set_test_mode(self, enabled: bool):
        """Enable test mode that generates fake beats for testing"""
        with self._lock:
            self._test_mode = enabled
            debug_log(f"Test mode: {'enabled' if enabled else 'disabled'}")
            
    def _run(self):
        prev = np.zeros(self.BAR_COUNT)
        debug_log(f"AudioCapture thread running, device={self._device}")
        
        def _cb(indata, frames, t, status):
            if not self._running:
                return
                
            try:
                # Test mode: generate fake audio data
                if self._test_mode:
                    with self._lock:
                        self._test_time += 1
                        
                        # Generate fake frequency bars
                        t = self._test_time * 0.1
                        
                        # Create rhythmic pattern
                        beat_phase = (t % 2.0) / 2.0  # 0 to 1 every 2 seconds
                        
                        # Bass drum on beats 1 and 3
                        if beat_phase < 0.1 or (beat_phase > 0.5 and beat_phase < 0.6):
                            bass_boost = 1.0
                            self._beat_detector = 0.8
                        else:
                            bass_boost = 0.1
                            self._beat_detector *= 0.9
                        
                        # Generate frequency bands
                        for i in range(self.BAR_COUNT):
                            if i < 10:  # Bass frequencies
                                base = 0.8 * bass_boost
                            elif i < 30:  # Mid frequencies
                                base = 0.3 + 0.3 * math.sin(t * 2)
                            else:  # Treble frequencies
                                base = 0.1 + 0.2 * math.sin(t * 8)
                            
                            # Add some randomness
                            base += np.random.random() * 0.1
                            prev[i] = self.SMOOTHING * prev[i] + (1.0 - self.SMOOTHING) * base
                        
                        self._bars = prev.tolist()
                    return
                
                # Normal audio processing
                mono = indata[:, 0] if indata.ndim > 1 else indata.ravel()
                
                # Check if we're getting actual audio (not silence)
                if np.max(np.abs(mono)) > 0.001:
                    pass  # Audio is coming in
                    
                window = np.hanning(len(mono))
                fft = np.abs(np.fft.rfft(mono * window, n=self.BLOCK_SIZE))
                freqs = np.fft.rfftfreq(self.BLOCK_SIZE, 1.0 / self.SAMPLE_RATE)
                
                lo, hi = math.log10(30), math.log10(18000)
                bands = np.logspace(lo, hi, self.BAR_COUNT + 1, base=10)
                
                mags = []
                for i in range(self.BAR_COUNT):
                    mask = (freqs >= bands[i]) & (freqs < bands[i + 1])
                    if mask.any():
                        mag = np.mean(fft[mask])
                    else:
                        mag = 0.0
                    mags.append(mag)
                mags = np.array(mags)
                
                pk = mags.max()
                if pk > 1e-6:
                    if pk > self._peak:
                        self._peak = pk
                    else:
                        self._peak = self._peak * 0.98 + pk * 0.02
                    
                    # Normalize with more aggressive scaling
                    norm = mags / (self._peak + 1e-6)
                    
                    # Apply compression to enhance transients
                    norm = np.power(norm, 0.8)  # Compress dynamic range
                    
                    # Less smoothing for better beat response
                    smoothed = self.SMOOTHING * prev + (1.0 - self.SMOOTHING) * norm
                    if smoothed.shape == prev.shape:
                        prev[:] = smoothed
                    
                    # Detect transients (beats) by comparing with previous frame
                    with self._lock:
                        self._prev_bars = list(self._bars)
                        self._bars = smoothed.tolist()
                        
                        # Simple beat detection: look for sudden increases in low frequencies
                        bass_energy = np.mean(smoothed[:10])  # Low frequency energy
                        prev_bass_energy = np.mean(prev[:10])
                        
                        if bass_energy > prev_bass_energy * 1.3:  # 30% increase threshold
                            self._beat_detector = min(1.0, bass_energy * 2)
                        else:
                            self._beat_detector *= 0.9  # Decay beat signal
                            
            except Exception as e:
                debug_log(f"Audio callback error: {e}")
                
        try:
            # Try default device first
            try:
                self._stream = sd.InputStream(
                    device=self._device,
                    channels=1,
                    samplerate=self.SAMPLE_RATE,
                    blocksize=self.BLOCK_SIZE,
                    callback=_cb,
                    latency="low"
                )
            except Exception as e:
                debug_log(f"Failed with device {self._device}, trying default: {e}")
                self._stream = sd.InputStream(
                    device=None,  # Let sounddevice choose
                    channels=1,
                    samplerate=self.SAMPLE_RATE,
                    blocksize=self.BLOCK_SIZE,
                    callback=_cb,
                    latency="low"
                )
            
            with self._stream:
                while self._running:
                    time.sleep(0.05)
        except Exception as e:
            self.error = str(e)
            debug_log(f"Audio stream error: {e}")
            debug_log("Auto-enabling test mode due to audio device failure")
            # Auto-enable test mode if audio fails
            with self._lock:
                self._test_mode = True
            
            # Continue running in test mode
            while self._running:
                _cb(None, None, None, None)  # Generate test data
                time.sleep(0.05)

# ── Ambient background widget ───────────────────────────────────────────────
class AmbientBackground(QWidget):
    def __init__(self, capture, parent=None):
        super().__init__(parent)
        self._capture = capture
        self._scheme_idx = 0
        self._intensity = 0.7
        self._effect_mode = 0  # 0=Orbs 1=Aurora 2=Nebula 3=Ripples
        self._energy = [0.0, 0.0, 0.0]
        self._frame_count = 0

        # Orb phase accumulators
        self._bass_px = 0.0
        self._mid_px  = 0.0;  self._mid_py  = 0.5
        self._tre_px  = 1.2;  self._tre_py  = 2.1

        # Aurora: each column of vertical rays has an independent height phase
        # We use 40 columns so rays look dense and overlapping
        import random
        rng = random.Random(7)
        self._aurora_col_phase  = [rng.random() * math.pi * 2 for _ in range(40)]
        self._aurora_col_speed  = [0.012 + rng.random() * 0.018 for _ in range(40)]
        self._aurora_col_hue    = [rng.random() for _ in range(40)]   # 0-1, maps to tint variation
        self._aurora_sway_phase = 0.0

        # Nebula: fixed star field
        rng2 = random.Random(42)
        self._stars = [(rng2.random(), rng2.random(), rng2.random() * 2.5 + 0.5)
                       for _ in range(180)]
        self._nebula_twinkle = [rng2.random() * math.pi * 2 for _ in range(180)]
        self._nebula_phase   = 0.0

        # Ripples: list of [cx_frac, cy_frac, radius_frac, alpha, width_px]
        self._ripples         = []
        self._ripple_cooldown = 0
        
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        
        debug_log(f"AmbientBackground created")
        
    def set_scheme(self, idx):
        self._scheme_idx = idx % len(SCHEME_NAMES)

    def set_effect_mode(self, idx):
        self._effect_mode = idx % len(EFFECT_MODES)
        
    def set_intensity(self, v: float):
        self._intensity = max(0.0, min(1.0, v))
        
    def cleanup(self):
        if self._timer:
            self._timer.stop()
        
    def _tick(self):
        # Orb phases
        self._bass_px += 0.007
        self._mid_px  += 0.011;  self._mid_py  += 0.008
        self._tre_px  += 0.014;  self._tre_py  += 0.013

        # Aurora phases — each column advances independently
        for i in range(len(self._aurora_col_phase)):
            self._aurora_col_phase[i] += self._aurora_col_speed[i]
        self._aurora_sway_phase += 0.005

        # Nebula shimmer
        self._nebula_phase += 0.03

        bars = self._capture.get_bars()
        beat = self._capture.get_beat()
        n    = len(bars)

        if n > 0:
            bass   = sum(bars[:n//6])      / max(n//6, 1)
            mid    = sum(bars[n//6:n//2])  / max(n//3, 1)
            treble = sum(bars[n//2:])      / max(n//2, 1)
            s = 0.75
            self._energy[0] = s * self._energy[0] + (1 - s) * bass
            self._energy[1] = s * self._energy[1] + (1 - s) * mid
            self._energy[2] = s * self._energy[2] + (1 - s) * treble
            if beat > 0.1:
                self._energy[0] = min(1.0, self._energy[0] + beat * 0.5)
                self._energy[1] = min(1.0, self._energy[1] + beat * 0.35)
                self._energy[2] = min(1.0, self._energy[2] + beat * 0.25)

        # Ripples: spawn on beat, advance all rings
        if self._effect_mode == 3:
            if self._ripple_cooldown > 0:
                self._ripple_cooldown -= 1
            if beat > 0.2 and self._ripple_cooldown == 0:
                import random
                # Spawn 1-2 rings per beat
                for _ in range(2 if beat > 0.6 else 1):
                    cx = 0.5 + (random.random() - 0.5) * 0.30
                    cy = 0.5 + (random.random() - 0.5) * 0.25
                    self._ripples.append([cx, cy, 0.0, min(1.0, beat * 1.6), max(3, int(beat * 9))])
                self._ripple_cooldown = 4

            # Advance each ring
            alive = []
            for r in self._ripples:
                r[2] += 0.010 + self._energy[0] * 0.007
                r[3] -= 0.018
                if r[3] > 0:
                    alive.append(r)
            self._ripples = alive

        self._frame_count += 1
        self.update()
        
    def _draw_orb(self, painter, x, y, radius, color_center, color_edge):
        if radius <= 0:
            return
        gradient = QRadialGradient(x, y, radius)
        gradient.setColorAt(0, color_center)
        gradient.setColorAt(1, color_edge)
        painter.setBrush(QBrush(gradient))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(int(x - radius), int(y - radius), int(radius * 2), int(radius * 2))

    def _draw_aurora(self, painter, W, H, scheme, bass, mid, treble, intensity):
        """Vertical light-sheet aurora — tall glowing columns that sway and pulse."""
        orb = scheme["orb"]
        energy = bass * 0.55 + mid * 0.30 + treble * 0.15

        n_cols = len(self._aurora_col_phase)   # 40 columns
        col_w  = W / n_cols

        painter.setPen(Qt.PenStyle.NoPen)

        for i in range(n_cols):
            phase  = self._aurora_col_phase[i]
            hue_t  = self._aurora_col_hue[i]   # 0-1

            # Ray height: bass drives overall brightness, treble adds shimmer
            height_frac = (0.25
                           + 0.35 * abs(math.sin(phase))
                           + bass  * intensity * 0.55 * abs(math.sin(phase * 0.7))
                           + treble * intensity * 0.15 * abs(math.sin(phase * 2.3)))
            height_frac = min(1.0, height_frac)

            # Gentle horizontal sway — columns drift left/right slightly
            sway_x = math.sin(self._aurora_sway_phase + i * 0.4) * col_w * 0.6
            cx = i * col_w + col_w * 0.5 + sway_x

            ray_h  = int(H * height_frac)
            ray_top = H - ray_h   # rays grow upward from bottom

            # Tint: interpolate between orb colour and a cooler secondary
            # hue_t near 0 → pure orb colour; near 1 → shifted hue (blue-green lean)
            r = int(orb[0] * (1 - hue_t * 0.5))
            g = int(orb[1] * (0.7 + hue_t * 0.3))
            b = int(orb[2] * (0.6 + hue_t * 0.6))
            r, g, b = min(255, r), min(255, g), min(255, b)

            # Alpha driven by energy + individual phase
            alpha_peak = int(min(210, (55 + energy * intensity * 155)
                                      * abs(math.sin(phase * 0.5 + 0.5))))
            alpha_peak = max(20, alpha_peak)

            # Vertical gradient: bright mid-column, fades top and bottom
            if ray_h > 2:
                grad = QLinearGradient(cx, ray_top, cx, H)
                grad.setColorAt(0.0, QColor(r, g, b, 0))
                grad.setColorAt(0.2, QColor(r, g, b, alpha_peak // 2))
                grad.setColorAt(0.5, QColor(r, g, b, alpha_peak))
                grad.setColorAt(0.8, QColor(r, g, b, alpha_peak // 3))
                grad.setColorAt(1.0, QColor(r, g, b, 0))
                painter.setBrush(QBrush(grad))
                # Column rect with slight overlap so no gaps
                painter.drawRect(int(cx - col_w * 0.9), ray_top,
                                 int(col_w * 1.8), ray_h)

    def _draw_nebula(self, painter, W, H, scheme, bass, mid, treble, intensity):
        orb = scheme["orb"]
        energy = bass * 0.4 + mid * 0.4 + treble * 0.2

        painter.setPen(Qt.PenStyle.NoPen)
        for i, (xf, yf, base_sz) in enumerate(self._stars):
            twinkle  = 0.6 + 0.4 * math.sin(self._nebula_phase * 1.7 + self._nebula_twinkle[i])
            freq_frac = i / len(self._stars)
            band = bass if freq_frac < 0.33 else (mid if freq_frac < 0.66 else treble)

            brightness = twinkle * (0.4 + band * intensity * 1.4)
            sz = base_sz * (0.5 + band * intensity * 1.8) * (W / 1200)
            sz = max(0.8, sz)
            alpha = int(min(230, brightness * 200))

            r = min(255, int(orb[0] * brightness * 1.1))
            g = min(255, int(orb[1] * brightness * 1.1))
            b = min(255, int(orb[2] * brightness * 1.1))
            painter.setBrush(QBrush(QColor(r, g, b, alpha)))
            cx, cy = int(xf * W), int(yf * H)
            painter.drawEllipse(int(cx - sz), int(cy - sz), int(sz * 2), int(sz * 2))

            if sz > 1.5 and alpha > 80:
                halo_sz = sz * 3
                grad = QRadialGradient(cx, cy, halo_sz)
                grad.setColorAt(0, QColor(r, g, b, int(alpha * 0.35)))
                grad.setColorAt(1, QColor(r, g, b, 0))
                painter.setBrush(QBrush(grad))
                painter.drawEllipse(int(cx - halo_sz), int(cy - halo_sz),
                                    int(halo_sz * 2), int(halo_sz * 2))

        # Two soft nebula cloud blobs
        for (xf, yf) in [(0.35, 0.45), (0.68, 0.55)]:
            cx = int(xf * W + math.sin(self._nebula_phase * 0.3) * W * 0.02)
            cy = int(yf * H + math.cos(self._nebula_phase * 0.25) * H * 0.02)
            blob_r = int(W * 0.28 * (0.5 + energy * intensity * 0.7))
            if blob_r > 0:
                grad = QRadialGradient(cx, cy, blob_r)
                grad.setColorAt(0,   QColor(orb[0], orb[1], orb[2], int(30 + energy * intensity * 45)))
                grad.setColorAt(0.5, QColor(orb[0]//2, orb[1]//2, orb[2]//2, int(15 + energy * intensity * 20)))
                grad.setColorAt(1,   QColor(0, 0, 0, 0))
                painter.setBrush(QBrush(grad))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(cx - blob_r, cy - blob_r, blob_r * 2, blob_r * 2)

    def _draw_ripples(self, painter, W, H, scheme, bass, mid, treble, intensity):
        from PyQt6.QtGui import QPen
        orb = scheme["orb"]
        energy = bass * 0.6 + mid * 0.3 + treble * 0.1

        # Soft breathing glow at centre — always visible, no beat needed
        breath = 0.5 + 0.5 * math.sin(self._nebula_phase * 0.35)
        br = int(W * 0.18 * (0.5 + energy * intensity * 0.5 + breath * 0.12))
        if br > 0:
            grad = QRadialGradient(W / 2, H / 2, br)
            grad.setColorAt(0.0, QColor(orb[0], orb[1], orb[2], int(60 + energy * intensity * 60)))
            grad.setColorAt(0.5, QColor(orb[0], orb[1], orb[2], int(25 + energy * intensity * 30)))
            grad.setColorAt(1.0, QColor(orb[0], orb[1], orb[2], 0))
            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(int(W/2 - br), int(H/2 - br), br*2, br*2)

        # Expanding beat rings — thick, bright, clearly visible
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for ripple in self._ripples:
            cx_frac, cy_frac, radius_frac, alpha, spawn_width = ripple
            cx = int(cx_frac * W)
            cy = int(cy_frac * H)
            # Map to pixels; use the smaller dimension so rings stay on screen
            r_px = int(radius_frac * min(W, H) * 0.75)
            if r_px <= 0:
                continue

            # Fade and thin as ring expands
            life = 1.0 - radius_frac          # 1.0 fresh → 0.0 at max radius
            ring_alpha = int(min(235, alpha * life * 220 * intensity))
            ring_w     = max(1, int(spawn_width * life))

            if ring_alpha < 5:
                continue

            # Primary ring — solid, thick
            pen = QPen(QColor(orb[0], orb[1], orb[2], ring_alpha))
            pen.setWidth(ring_w)
            painter.setPen(pen)
            painter.drawEllipse(cx - r_px, cy - r_px, r_px * 2, r_px * 2)

            # Outer glow halo — wider, more transparent
            if ring_w >= 2:
                halo_r = r_px + ring_w
                pen2 = QPen(QColor(orb[0], orb[1], orb[2], ring_alpha // 3))
                pen2.setWidth(ring_w * 2 + 2)
                painter.setPen(pen2)
                painter.drawEllipse(cx - halo_r, cy - halo_r, halo_r * 2, halo_r * 2)
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        W = self.width()
        H = self.height()

        if W <= 0 or H <= 0:
            return

        scheme     = SCHEMES[SCHEME_NAMES[self._scheme_idx]]
        lo         = scheme["lo"]
        hi         = scheme["hi"]
        orb_color  = scheme["orb"]
        bass, mid, treble = self._energy
        intensity  = self._intensity
        beat       = self._capture.get_beat()

        energy_total = min(1.0, (bass * 0.5 + mid * 0.3 + treble * 0.2) * intensity * 2.0)
        if beat > 0.1:
            energy_total = min(1.0, energy_total + beat * 0.3)

        def lerp(a, b, t):
            return int(a + t * (b - a))

        # Background — Nebula/Ripples want a much darker canvas
        if self._effect_mode in (2, 3):
            bg_r = max(lo[0], lerp(lo[0], hi[0], energy_total) // 4)
            bg_g = max(lo[1], lerp(lo[1], hi[1], energy_total) // 4)
            bg_b = max(lo[2], lerp(lo[2], hi[2], energy_total) // 4)
        else:
            bg_r = lerp(lo[0], hi[0], energy_total)
            bg_g = lerp(lo[1], hi[1], energy_total)
            bg_b = lerp(lo[2], hi[2], energy_total)

        bg_grad = QLinearGradient(0, 0, 0, H)
        bg_grad.setColorAt(0, QColor(bg_r, bg_g, bg_b, 255))
        bg_grad.setColorAt(1, QColor(max(0, bg_r - 6), max(0, bg_g - 6), max(0, bg_b - 6), 255))
        painter.fillRect(0, 0, W, H, QBrush(bg_grad))

        if self._effect_mode == 0:      # ── Orbs ──
            bass_r  = int(W * 0.38 * (0.65 + bass * intensity * 0.9) * (1.0 + beat * 0.6))
            bass_a  = int(min(210, 55 + bass * intensity * 155 + beat * 90))
            bass_cx = int(W * 0.50 + math.sin(self._bass_px) * W * 0.05)
            self._draw_orb(painter, bass_cx, int(H * 0.72), bass_r,
                           QColor(orb_color[0], orb_color[1], orb_color[2], bass_a),
                           QColor(orb_color[0]//3, orb_color[1]//3, orb_color[2]//3, 0))
            mid_r  = int(W * 0.22 * (0.55 + mid * intensity * 1.1) * (1.0 + beat * 0.5))
            mid_a  = int(min(175, 45 + mid * intensity * 130 + beat * 70))
            mid_cx = int(W * 0.26 + math.sin(self._mid_px) * W * 0.09)
            mid_cy = int(H * 0.46 + math.cos(self._mid_py) * H * 0.07)
            self._draw_orb(painter, mid_cx, mid_cy, mid_r,
                           QColor(orb_color[0], orb_color[1], orb_color[2], mid_a),
                           QColor(0, 0, 0, 0))
            treble_r  = int(W * 0.16 * (0.50 + treble * intensity * 1.2) * (1.0 + beat * 0.4))
            treble_a  = int(min(155, 40 + treble * intensity * 115 + beat * 55))
            treble_cx = int(W * 0.76 + math.cos(self._tre_px) * W * 0.07)
            treble_cy = int(H * 0.30 + math.sin(self._tre_py) * H * 0.06)
            self._draw_orb(painter, treble_cx, treble_cy, treble_r,
                           QColor(orb_color[0], orb_color[1], orb_color[2], treble_a),
                           QColor(0, 0, 0, 0))

        elif self._effect_mode == 1:    # ── Aurora ──
            self._draw_aurora(painter, W, H, scheme, bass, mid, treble, intensity)

        elif self._effect_mode == 2:    # ── Nebula ──
            self._draw_nebula(painter, W, H, scheme, bass, mid, treble, intensity)

        elif self._effect_mode == 3:    # ── Ripples ──
            self._draw_ripples(painter, W, H, scheme, bass, mid, treble, intensity)

        painter.end()
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update()
        
    def _fill_parent(self):
        """Called by SlotHost to make this widget fill the parent area"""
        if self.parent():
            parent = self.parent()
            self.setGeometry(0, 0, parent.width(), parent.height())

# ── Control panel widget ───────────────────────────────────────────────
class AmbientControlPanel(QWidget):
    SETTINGS_FILE = os.path.join(
        os.path.expanduser("~"), ".config", "great_sage", "ambient_settings.json"
    )

    def __init__(self, background, capture, parent=None, api=None):
        super().__init__(parent)
        self._background = background
        self._capture = capture
        self._api = api

        # Load persisted settings from JSON file (guaranteed across restarts)
        saved = self._load_settings_file()
        self._saved_scheme    = saved.get("scheme_index", 0)
        self._saved_intensity = saved.get("intensity", 70)
        self._saved_device    = saved.get("device_id", None)
        self._saved_effect    = saved.get("effect_mode", 0)
        debug_log(f"Loaded settings: scheme={self._saved_scheme}, intensity={self._saved_intensity}, device={self._saved_device}")

        layout = QVBoxLayout()
        layout.setSpacing(10)
        
        title = QLabel("Ambient Mode Settings")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title)
        
        # Audio device selector
        device_layout = QHBoxLayout()
        device_layout.addWidget(QLabel("Audio Input:"))
        self.device_combo = QComboBox()
        self._populate_devices()
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        device_layout.addWidget(self.device_combo, 1)
        layout.addLayout(device_layout)

        # Restore saved device selection
        self._restore_device(self._saved_device)

        # Color scheme selector
        scheme_layout = QHBoxLayout()
        scheme_layout.addWidget(QLabel("Color Scheme:"))
        self.scheme_combo = QComboBox()
        self.scheme_combo.addItems(SCHEME_NAMES)
        self.scheme_combo.setCurrentIndex(self._saved_scheme)
        self.scheme_combo.currentIndexChanged.connect(self._on_scheme_changed)
        scheme_layout.addWidget(self.scheme_combo)
        layout.addLayout(scheme_layout)

        # Effect mode selector
        effect_layout = QHBoxLayout()
        effect_layout.addWidget(QLabel("Effect Mode:"))
        self.effect_combo = QComboBox()
        self.effect_combo.addItems(EFFECT_MODES)
        self.effect_combo.setCurrentIndex(self._saved_effect)
        self.effect_combo.currentIndexChanged.connect(self._on_effect_changed)
        effect_layout.addWidget(self.effect_combo)
        layout.addLayout(effect_layout)

        if self._background:
            self._background.set_effect_mode(self._saved_effect)

        # Intensity slider
        intensity_layout = QHBoxLayout()
        intensity_layout.addWidget(QLabel("Intensity:"))
        self.intensity_slider = QSlider(Qt.Orientation.Horizontal)
        self.intensity_slider.setRange(0, 100)
        self.intensity_slider.setValue(self._saved_intensity)
        self.intensity_slider.valueChanged.connect(self._on_intensity_changed)
        intensity_layout.addWidget(self.intensity_slider)
        self.intensity_label = QLabel(f"{self._saved_intensity}%")
        intensity_layout.addWidget(self.intensity_label)
        layout.addLayout(intensity_layout)

        # Apply saved values to background widget
        if self._background:
            self._background.set_scheme(self._saved_scheme)
            self._background.set_intensity(self._saved_intensity / 100.0)
        
        # Test button
        test_btn = QPushButton("Test Audio")
        test_btn.clicked.connect(self._test_audio)
        layout.addWidget(test_btn)
        
        # Test mode button
        self.test_mode_btn = QPushButton("Enable Test Mode")
        self.test_mode_btn.clicked.connect(self._toggle_test_mode)
        layout.addWidget(self.test_mode_btn)
        
        # Status
        self.status_label = QLabel("Status: Active")
        self.status_label.setStyleSheet("color: #4CAF50;")
        layout.addWidget(self.status_label)
        
        # Energy meter (debug)
        self.energy_label = QLabel("Audio Level: --")
        self.energy_label.setStyleSheet("font-family: monospace;")
        layout.addWidget(self.energy_label)
        
        # Start a timer to update energy meter
        self._energy_timer = QTimer(self)
        self._energy_timer.timeout.connect(self._update_energy)
        self._energy_timer.start(200)
        
        layout.addStretch()
        self.setLayout(layout)
        
        debug_log("Control panel created")
        
    def _load_settings_file(self):
        """Read settings JSON from disk. Returns {} on any error."""
        try:
            if os.path.exists(self.SETTINGS_FILE):
                with open(self.SETTINGS_FILE, "r") as f:
                    data = json.load(f)
                    debug_log(f"Settings file read: {data}")
                    return data
        except Exception as e:
            debug_log(f"Could not read settings file: {e}")
        return {}

    def _save_settings(self):
        """Write current control values to JSON on disk."""
        # Guard: don't save if widgets not fully constructed yet
        if not hasattr(self, 'scheme_combo') or not hasattr(self, 'intensity_slider') or not hasattr(self, 'device_combo'):
            return
        data = {
            "scheme_index": self.scheme_combo.currentIndex(),
            "intensity":    self.intensity_slider.value(),
            "device_id":    self.device_combo.currentData(),
            "effect_mode":  self.effect_combo.currentIndex() if hasattr(self, 'effect_combo') else 0,
        }
        try:
            os.makedirs(os.path.dirname(self.SETTINGS_FILE), exist_ok=True)
            with open(self.SETTINGS_FILE, "w") as f:
                json.dump(data, f, indent=2)
            debug_log(f"Settings saved: {data}")
        except Exception as e:
            debug_log(f"Could not save settings: {e}")

    def _restore_device(self, device_id):
        """Select the combo item whose data matches device_id."""
        if device_id is None:
            self.device_combo.setCurrentIndex(0)
            return
        for i in range(self.device_combo.count()):
            if self.device_combo.itemData(i) == device_id:
                self.device_combo.setCurrentIndex(i)
                return
        self.device_combo.setCurrentIndex(0)  # fall back to default

    def _populate_devices(self):
        """Populate audio input devices"""
        self.device_combo.clear()
        try:
            devices = sd.query_devices()
            default_input = sd.default.device[0]  # Default input device
            
            # Add default option first
            self.device_combo.addItem("Default System Input", None)
            
            # Add specific devices
            for i, dev in enumerate(devices):
                if dev['max_input_channels'] > 0:
                    name = dev['name']
                    # Mark if this is the default device
                    if i == default_input:
                        name = f"{name} (Default)"
                    self.device_combo.addItem(f"{i}: {name}", i)
            
            if self.device_combo.count() == 1:  # Only default option
                self.device_combo.addItem("No input devices found", -1)
                self.status_label.setText("Status: No audio input")
                self.status_label.setStyleSheet("color: #f44336;")
            else:
                # Select default device
                self.device_combo.setCurrentIndex(0)
        except Exception as e:
            debug_log(f"Error querying devices: {e}")
            self.device_combo.addItem("Error", -1)
            
    def _on_device_changed(self, idx):
        """Change audio input device"""
        device_id = self.device_combo.currentData()
        if device_id is not None and device_id >= 0 and self._capture:
            debug_log(f"Switching to device {device_id}")
            self._capture.stop()
            self._capture.start(device=device_id)
            self.status_label.setText("Status: Restarting...")
            QTimer.singleShot(1000, lambda: self.status_label.setText("Status: Active"))
        self._save_settings()
            
    def _on_scheme_changed(self, idx):
        if self._background:
            self._background.set_scheme(idx)
        self._save_settings()

    def _on_effect_changed(self, idx):
        if self._background:
            self._background.set_effect_mode(idx)
        self._save_settings()
            
    def _on_intensity_changed(self, value):
        intensity = value / 100.0
        if self._background:
            self._background.set_intensity(intensity)
        self.intensity_label.setText(f"{value}%")
        self._save_settings()
        
    def _test_audio(self):
        """Test if audio is being captured"""
        if self._capture:
            energy = self._capture.get_energy()
            debug_log(f"Test audio: energy={energy}")
            self.status_label.setText(f"Test: energy={energy:.3f}")
            QTimer.singleShot(2000, lambda: self.status_label.setText("Status: Active"))
        else:
            debug_log("Test audio: capture is None")
    
    def _toggle_test_mode(self):
        """Toggle test mode for testing beat detection"""
        if self._capture:
            current_test = getattr(self._capture, '_test_mode', False)
            new_test = not current_test
            self._capture.set_test_mode(new_test)
            
            if new_test:
                self.test_mode_btn.setText("Disable Test Mode")
                self.test_mode_btn.setStyleSheet("background: #4CAF50; color: white;")
                self.status_label.setText("Status: Test Mode Active")
            else:
                self.test_mode_btn.setText("Enable Test Mode")
                self.test_mode_btn.setStyleSheet("")
                self.status_label.setText("Status: Active")
            
    def _update_energy(self):
        """Update energy meter display"""
        if self._capture:
            energy = self._capture.get_energy()
            # Create a simple bar
            bar_len = int(energy * 30)
            bar = "█" * bar_len + "░" * (30 - bar_len)
            self.energy_label.setText(f"Audio Level: [{bar}] {energy:.3f}")

# ── Plugin interface functions ───────────────────────────────────────────────
def build_page(parent, api):
    """Build the plugin's main UI page"""
    global _audio_capture, _background_widget, _control_widget, _slot_registered

    debug_log("=" * 50)
    debug_log(f"build_page called, parent={parent}")

    # ── Always initialise audio + background, even when called with no parent ──
    if _audio_capture is None:
        debug_log("Creating AudioCapture")
        _audio_capture = AudioCapture()
        try:
            _audio_capture.start()
            debug_log("AudioCapture started successfully")
        except Exception as e:
            debug_log(f"AudioCapture start error: {e}")

    if _background_widget is None:
        debug_log("Creating AmbientBackground")
        _background_widget = AmbientBackground(_audio_capture)

    # Register slot as soon as we have a real parent
    if parent is not None:
        _register_background(parent)

    # ── Only build the control-panel widget when we have a real parent ──────
    # _activate_background_plugins calls us with parent=None; in that case we
    # just want audio+background running, not a half-baked UI widget.
    if parent is None:
        debug_log("build_page: no parent — skipping UI widget (background-only init)")
        return None

    # Build (or reuse) the settings panel.
    # If the cached widget has been destroyed (e.g. after a reload), discard it.
    if _control_widget is not None and not _control_widget.isVisible() and _control_widget.parent() is None:
        debug_log("build_page: discarding stale _control_widget")
        _control_widget.deleteLater()
        _control_widget = None  # type: ignore

    if _control_widget is None:
        _control_widget = AmbientControlPanel(_background_widget, _audio_capture, parent, api=api)

    debug_log("build_page complete")
    debug_log("=" * 50)

    return _control_widget

def get_background_widget(*args, **kwargs):
    global _background_widget, _audio_capture
    if _background_widget is None and _audio_capture is not None:
        _background_widget = AmbientBackground(_audio_capture)
    return _background_widget

def _register_background(parent=None):
    """Register the background widget via SlotRegistry — the proper plugin API.
    Safe to call multiple times — skips if already registered."""
    global _slot_registered, _background_widget
    if _slot_registered:
        return
    try:
        from plugin_manager import SlotRegistry
        reg = SlotRegistry.instance()
        reg.register("dashboard_background", "Ambient Mode", _background_widget)
        _slot_registered = True
        debug_log("SUCCESS: Registered via SlotRegistry('dashboard_background')")
    except Exception as e:
        debug_log(f"SlotRegistry registration failed: {e}")

def on_enable(parent, api):
    global _audio_capture, _background_widget
    debug_log("on_enable called")
    if _audio_capture is None:
        _audio_capture = AudioCapture()
        _audio_capture.start()
    elif not _audio_capture._running:
        _audio_capture.start()
    if _background_widget is None:
        _background_widget = AmbientBackground(_audio_capture)
    _background_widget.show()
    # Register background slot — retry once after short delay if not ready yet
    _register_background(parent)
    if not _slot_registered:
        QTimer.singleShot(500, _register_background)

def on_disable(parent, api):
    global _audio_capture, _background_widget, _slot_registered
    debug_log("on_disable called")
    if _audio_capture:
        _audio_capture.stop()
    if _background_widget:
        _background_widget.hide()
    # Unregister from slot so re-enable can re-register cleanly
    try:
        from plugin_manager import SlotRegistry
        SlotRegistry.instance().unregister("dashboard_background", "Ambient Mode")
    except Exception:
        pass
    _slot_registered = False

def get_settings_widget(parent, api):
    global _control_widget, _background_widget, _audio_capture
    if _control_widget is None:
        _control_widget = AmbientControlPanel(_background_widget, _audio_capture, parent, api=api)
    return _control_widget

def cleanup(parent, api):
    global _audio_capture, _background_widget, _control_widget, _slot_registered
    debug_log("cleanup called")
    if _audio_capture:
        _audio_capture.stop()
        _audio_capture = None
    if _background_widget:
        _background_widget.cleanup()
        _background_widget.deleteLater()
        _background_widget = None
    _control_widget = None
    _slot_registered = False

def refresh(page):
    """Called when user navigates to this plugin page"""
    debug_log("refresh called")
    # Do NOT repopulate devices here — that fires currentIndexChanged which
    # restarts the audio stream and causes a stutter every time you open the page.
    # The device list is populated once during __init__ and is stable.