#!/usr/bin/env python3
"""
Great Sage — Bug Report Module
Lets users describe bugs and submit them as pre-filled GitHub issues,
with auto-collected version, OS, and log info attached.
"""
import os
import sys
import platform
import subprocess
import urllib.parse
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTextEdit, QFrame, QComboBox, QScrollArea, QStackedWidget,
)
from PyQt6.QtCore import Qt, QTimer

from great_sage_core import SCRIPT_DIR

try:
    from gs_theme import (
        BG, BG2, BG3, BORDER, BORDER2, PANEL,
        TEXT, TEXT2, MUTED, ACCENT, ACCENT2,
        FONT_UI, FONT_DISPLAY,
    )
except ImportError:
    BG = "#0D0D14"; BG2 = "#13131E"; BG3 = "#1A1A28"
    BORDER = "#1A1A28"; BORDER2 = "#252535"; PANEL = "#111118"
    TEXT = "#E0E0F0"; TEXT2 = "#A0A0C0"; MUTED = "#505068"
    ACCENT = "#C9A84C"; ACCENT2 = "#4CAACC"
    FONT_UI = "monospace"; FONT_DISPLAY = "monospace"

BUG_RED = "#E05555"

# ── Helpers ────────────────────────────────────────────────────────────────────

def _section_lbl(text):
    l = QLabel(text)
    l.setStyleSheet(
        f"color:{MUTED};font-size:9px;letter-spacing:3px;"
        f"font-family:{FONT_UI};background:transparent;")
    return l

def _field_label(text):
    l = QLabel(text)
    l.setStyleSheet(
        f"color:#6868A0;font-size:9px;letter-spacing:1px;"
        f"font-family:{FONT_UI};background:transparent;")
    return l

def _divider():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"color:{BORDER};background:{BORDER};max-height:1px;")
    return line

def _panel_scroll():
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setStyleSheet(
        f"QScrollArea{{background:{BG};border:none;}}"
        f"QScrollBar:vertical{{background:#0d0d14;width:6px;border-radius:3px;}}"
        f"QScrollBar::handle:vertical{{background:#2a2a3a;border-radius:3px;}}"
        f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}")
    inner = QWidget()
    inner.setStyleSheet(f"background:{BG};")
    iv = QVBoxLayout(inner)
    iv.setContentsMargins(36, 28, 36, 28)
    iv.setSpacing(14)
    scroll.setWidget(inner)
    return scroll, iv

def _field_style():
    return (
        f"QLineEdit,QTextEdit,QComboBox{{"
        f"background:#1a1a28;border:1px solid #2a2a3a;"
        f"border-radius:4px;color:{TEXT};font-size:12px;"
        f"padding:7px 10px;font-family:{FONT_UI};}}"
        f"QLineEdit:focus,QTextEdit:focus,QComboBox:focus{{"
        f"border-color:{ACCENT}44;}}"
        f"QComboBox::drop-down{{border:none;width:20px;}}"
        f"QComboBox::down-arrow{{color:{MUTED};}}"
    )

# ── Collect debug info ─────────────────────────────────────────────────────────

def _get_version():
    try:
        vf = Path(SCRIPT_DIR) / "VERSION"
        if vf.exists():
            return vf.read_text().strip()
    except Exception:
        pass
    return "unknown"

def _get_os_info():
    try:
        return platform.platform()
    except Exception:
        return "unknown"

def _get_python_version():
    return sys.version.split()[0]

def _get_log_snippet(lines=30):
    try:
        log_dir = Path(SCRIPT_DIR) / "logs"
        if log_dir.exists():
            logs = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            if logs:
                text = logs[0].read_text(errors="replace").splitlines()
                return "\n".join(text[-lines:]), str(logs[0].name)
    except Exception as e:
        return f"Could not read logs: {e}", ""
    return "No log files found.", ""

def _get_sysinfo():
    info = {
        "Great Sage version": _get_version(),
        "Python": _get_python_version(),
        "OS": _get_os_info(),
    }
    try:
        import psutil
        ram = psutil.virtual_memory().total / (1024 ** 3)
        info["RAM"] = f"{ram:.1f} GiB"
    except ImportError:
        pass
    try:
        info["PyQt6"] = __import__("PyQt6.QtCore", fromlist=["PYQT_VERSION_STR"]).PYQT_VERSION_STR
    except Exception:
        pass
    try:
        wd = os.environ.get("WAYLAND_DISPLAY")
        xd = os.environ.get("DISPLAY")
        if wd:
            info["Display server"] = f"Wayland ({wd})"
        elif xd:
            info["Display server"] = f"X11 ({xd})"
    except Exception:
        pass
    return info


# ── BugReportPage ──────────────────────────────────────────────────────────────

class BugReportPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{BG};")
        self._build()

    def refresh(self):
        self._refresh_sysinfo()
        self._refresh_logs()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        header_w = QWidget()
        header_w.setFixedHeight(52)
        import types as _ht
        from PyQt6.QtGui import QPainter as _hP, QLinearGradient as _hG, QColor as _hC, QBrush as _hB, QPen as _hPen
        def _hdr_paint(self_w, event):
            p = _hP(self_w)
            p.setRenderHint(_hP.RenderHint.Antialiasing)
            W, H = self_w.width(), self_w.height()
            g = _hG(0, 0, W, 0)
            g.setColorAt(0, _hC(BG2).lighter(118)); g.setColorAt(0.4, _hC(BG2)); g.setColorAt(1.0, _hC(BG2).lighter(108))
            p.fillRect(0, 0, W, H, _hB(g))
            sep = _hG(0, 0, W, 0)
            sep.setColorAt(0, _hC(BUG_RED).darker(600)); sep.setColorAt(0.15, _hC(BUG_RED).darker(300))
            sep.setColorAt(0.85, _hC(BUG_RED).darker(300)); sep.setColorAt(1.0, _hC(BUG_RED).darker(600))
            p.setPen(_hPen(_hB(sep), 1)); p.drawLine(0, H - 1, W, H - 1); p.end()
        header_w.paintEvent = _ht.MethodType(_hdr_paint, header_w)
        hv = QHBoxLayout(header_w)
        hv.setContentsMargins(28, 0, 28, 0)
        back_btn = QPushButton("← HOME")
        back_btn.setStyleSheet(
            f"background:transparent;border:none;color:{MUTED};"
            f"font-size:9px;letter-spacing:1.5px;padding:4px 0;")
        back_btn.clicked.connect(lambda: self.window()._navigate("dashboard"))
        tl = QLabel("BUG REPORT")
        tl.setStyleSheet(
            f"font-family:{FONT_DISPLAY};font-size:13px;font-weight:bold;"
            f"color:{BUG_RED};letter-spacing:4px;margin-left:14px;")
        hv.addWidget(back_btn); hv.addWidget(tl); hv.addStretch()
        root.addWidget(header_w)

        # ── Body ──────────────────────────────────────────────────────────────
        body = QWidget()
        body.setStyleSheet(f"background:{BG};")
        body_h = QHBoxLayout(body)
        body_h.setContentsMargins(0, 0, 0, 0)
        body_h.setSpacing(0)
        root.addWidget(body, 1)

        # Nav rail
        nav_rail = QWidget()
        nav_rail.setFixedWidth(168)
        nav_rail.setStyleSheet(f"background:#08080f;border-right:1px solid #1a1a28;")
        nav_v = QVBoxLayout(nav_rail)
        nav_v.setContentsMargins(0, 20, 0, 20)
        nav_v.setSpacing(2)
        body_h.addWidget(nav_rail)

        # Stack
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background:{BG};")
        body_h.addWidget(self._stack, 1)

        _nav_base = (
            f"QPushButton{{background:transparent;border:none;"
            f"border-left:2px solid transparent;"
            f"font-family:{FONT_UI};font-size:9px;letter-spacing:2px;"
            f"color:#505068;text-align:left;padding:10px 20px;}}"
            f"QPushButton:hover{{background:#0f0f18;color:#9898b8;"
            f"border-left:2px solid #303048;}}")
        _nav_active = (
            f"QPushButton{{background:#0f0f18;border:none;"
            f"border-left:2px solid {BUG_RED};"
            f"font-family:{FONT_UI};font-size:9px;letter-spacing:2px;"
            f"color:{BUG_RED};text-align:left;padding:10px 20px;}}")
        self._nav_btns = {}
        self._nav_base_ss   = _nav_base
        self._nav_active_ss = _nav_active

        def _nav_btn(label, idx):
            b = QPushButton(label)
            b.setStyleSheet(_nav_active if idx == 0 else _nav_base)
            b.clicked.connect(lambda _, i=idx: self._switch_panel(i))
            nav_v.addWidget(b)
            self._nav_btns[idx] = b

        _nav_btn("REPORT A BUG", 0)
        _nav_btn("VIEW LOGS",    1)
        _nav_btn("SYSTEM INFO",  2)
        nav_v.addStretch()

        # ── Panel 0: Report ───────────────────────────────────────────────────
        p0_scroll, p0 = _panel_scroll()
        p0.addWidget(_section_lbl("DESCRIBE THE ISSUE"))

        p0.addWidget(_field_label("TITLE"))
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("Short summary of the bug...")
        self._title_edit.setStyleSheet(_field_style())
        p0.addWidget(self._title_edit)

        p0.addWidget(_field_label("MODULE"))
        self._module_combo = QComboBox()
        self._module_combo.addItems(["Dashboard", "Legion", "Matrix", "Sage", "Artemis", "Plugins", "Settings", "Other"])
        self._module_combo.setStyleSheet(_field_style())
        p0.addWidget(self._module_combo)

        p0.addWidget(_field_label("WHAT HAPPENED?"))
        self._what_edit = QTextEdit()
        self._what_edit.setPlaceholderText("Describe what you did and what went wrong...")
        self._what_edit.setFixedHeight(100)
        self._what_edit.setStyleSheet(_field_style())
        p0.addWidget(self._what_edit)

        p0.addWidget(_field_label("EXPECTED BEHAVIOUR"))
        self._expected_edit = QTextEdit()
        self._expected_edit.setPlaceholderText("What did you expect to happen?")
        self._expected_edit.setFixedHeight(70)
        self._expected_edit.setStyleSheet(_field_style())
        p0.addWidget(self._expected_edit)

        p0.addWidget(_divider())
        p0.addWidget(_section_lbl("AUTO-COLLECTED INFO  —  ATTACHED AUTOMATICALLY"))

        self._auto_info_lbl = QLabel()
        self._auto_info_lbl.setStyleSheet(
            f"color:#7878a8;font-size:10px;line-height:1.8;"
            f"background:transparent;")
        self._auto_info_lbl.setWordWrap(True)
        p0.addWidget(self._auto_info_lbl)

        submit_btn = QPushButton("⊳  OPEN GITHUB ISSUE")
        submit_btn.setStyleSheet(
            f"QPushButton{{background:{BUG_RED};color:#fff;border:none;"
            f"border-radius:4px;font-family:{FONT_UI};font-size:9px;"
            f"letter-spacing:2px;font-weight:bold;padding:9px 28px;}}"
            f"QPushButton:hover{{background:#f06666;}}"
            f"QPushButton:pressed{{background:#c04444;}}")
        submit_btn.clicked.connect(self._submit)
        p0.addSpacing(4)
        p0.addWidget(submit_btn)
        p0.addStretch()
        self._stack.addWidget(p0_scroll)

        # ── Panel 1: Logs ─────────────────────────────────────────────────────
        p1_scroll, p1 = _panel_scroll()
        p1.addWidget(_section_lbl("RECENT LOGS"))
        self._log_name_lbl = QLabel()
        self._log_name_lbl.setStyleSheet(f"color:{MUTED};font-size:9px;background:transparent;")
        p1.addWidget(self._log_name_lbl)
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setStyleSheet(
            f"QTextEdit{{background:#0a0a10;border:1px solid #1a1a28;"
            f"border-radius:4px;color:#505068;font-size:10px;"
            f"font-family:{FONT_UI};padding:10px;line-height:1.6;}}")
        self._log_view.setMinimumHeight(320)
        p1.addWidget(self._log_view)
        p1.addStretch()
        self._stack.addWidget(p1_scroll)

        # ── Panel 2: System info ──────────────────────────────────────────────
        p2_scroll, p2 = _panel_scroll()
        p2.addWidget(_section_lbl("SYSTEM INFORMATION"))
        self._sysinfo_container = QVBoxLayout()
        self._sysinfo_container.setSpacing(8)
        p2.addLayout(self._sysinfo_container)
        p2.addStretch()
        self._stack.addWidget(p2_scroll)

        # Populate on first build
        self._refresh_sysinfo()
        self._refresh_logs()
        self._refresh_auto_info()

    def _switch_panel(self, index: int):
        self._stack.setCurrentIndex(index)
        for i, b in self._nav_btns.items():
            b.setStyleSheet(self._nav_active_ss if i == index else self._nav_base_ss)

    def _refresh_auto_info(self):
        version = _get_version()
        os_info = _get_os_info()
        _, log_name = _get_log_snippet(1)
        self._auto_info_lbl.setText(
            f"Version: {version}   •   OS: {os_info[:60]}\n"
            f"Log file: {log_name or 'none'}   •   Last 30 lines attached")

    def _refresh_logs(self):
        snippet, name = _get_log_snippet(50)
        self._log_name_lbl.setText(name)
        self._log_view.setPlainText(snippet)
        # Scroll to bottom
        sb = self._log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _refresh_sysinfo(self):
        # Clear existing
        while self._sysinfo_container.count():
            item = self._sysinfo_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        info = _get_sysinfo()
        for key, val in info.items():
            row = QWidget()
            row.setStyleSheet("background:transparent;")
            rv = QVBoxLayout(row)
            rv.setContentsMargins(0, 4, 0, 4)
            rv.setSpacing(2)
            k_lbl = QLabel(key.upper())
            k_lbl.setStyleSheet(
                f"color:#6868a0;font-size:9px;letter-spacing:1px;background:transparent;")
            v_lbl = QLabel(str(val))
            v_lbl.setStyleSheet(f"color:#7878a8;font-size:11px;background:transparent;")
            v_lbl.setWordWrap(True)
            rv.addWidget(k_lbl); rv.addWidget(v_lbl)
            self._sysinfo_container.addWidget(row)

    def _submit(self):
        title = self._title_edit.text().strip()
        if not title:
            self._title_edit.setFocus()
            self._title_edit.setStyleSheet(
                _field_style() + f"QLineEdit{{border-color:{BUG_RED};}}")
            return
        self._title_edit.setStyleSheet(_field_style())

        module   = self._module_combo.currentText()
        what     = self._what_edit.toPlainText().strip()
        expected = self._expected_edit.toPlainText().strip()
        version  = _get_version()
        os_info  = _get_os_info()
        log_snippet, log_name = _get_log_snippet(30)

        body = (
            f"## Bug Report\n\n"
            f"**Module:** {module}\n"
            f"**Great Sage version:** {version}\n"
            f"**OS:** {os_info}\n\n"
            f"## What happened?\n{what or '_Not provided._'}\n\n"
            f"## Expected behaviour\n{expected or '_Not provided._'}\n\n"
            f"## Log snippet ({log_name})\n"
            f"```\n{log_snippet}\n```\n"
        )

        url = (
            "https://github.com/scezian/Great-Sage/issues/new"
            f"?title={urllib.parse.quote('[Bug] ' + title)}"
            f"&body={urllib.parse.quote(body)}"
            f"&labels={urllib.parse.quote('bug')}"
        )

        try:
            subprocess.Popen(["xdg-open", url])
        except Exception:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "Bug Report",
                f"Could not open browser. Copy this URL:\n{url[:200]}...")
