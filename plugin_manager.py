"""
plugin_manager.py — Great Sage Plugin System
=============================================

Discovers, loads, enables/disables, and hosts plugins.

Plugin folder:  ~/Documents/great sage/plugins/
Plugin config:  ~/.great_sage_plugins.json

A plugin is a single .py file that exposes:

    PLUGIN_NAME    = "My Plugin"       # display name (required)
    PLUGIN_ICON    = "◉"               # single emoji or symbol (required)
    PLUGIN_DESCRIPTION = "..."         # short description (required)
    PLUGIN_VERSION = "1.0"             # version string (optional)
    PLUGIN_AUTHOR  = "..."             # author name (optional)
    PLUGIN_COLOR   = "#4FC4A0"         # accent colour for the card (optional)

    def build_page(parent, api) -> QWidget:
        # Build and return the plugin's full page widget.
        # parent  : the QWidget parent (PluginsPage)
        # api     : PluginAPI instance (see below)
        ...

    def refresh(page):
        # Called whenever the user navigates to this plugin.
        # page: the QWidget returned by build_page
        ...

The api object exposes:
    api.legion_data()               -> dict   (books + progress)
    api.matrix_data()               -> dict   (shows + watchlist)
    api.save_plugin_data(key, val)  -> None   (persist JSON-serialisable value)
    api.load_plugin_data(key)       -> any    (retrieve persisted value, or None)
    api.sage_chat(prompt)           -> str    (ask Sage, returns response string)
    api.colours                     -> dict   (app colour constants)
    api.fonts                       -> dict   (app font constants)
    api.show_status(msg)            -> None   (show message in the app status bar)
"""

from __future__ import annotations

try:
    from gs_logger import log as _log
except Exception:
    class _NoopLog:
        def __getattr__(self, name): return _NoopLog()
        def __call__(self, *a, **kw): return None
    _log = _NoopLog()

import importlib.util
import json
import os

from pathlib import Path

from PyQt6.QtCore    import Qt, QTimer, QSize, pyqtSignal, QThread
from PyQt6.QtGui     import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QStackedWidget, QMessageBox, QSizePolicy,
    QCheckBox,
)

# ── Paths ──────────────────────────────────────────────────────────────────────

def _find_plugins_dir() -> Path:
    here = Path(__file__).resolve().parent
    # 1. Sibling plugins/ next to this file (covers any repo location)
    candidate = here / "plugins"
    if candidate.is_dir():
        return candidate
    # 2. Walk up up to 4 levels
    p = here
    for _ in range(4):
        candidate = p / "plugins"
        if candidate.is_dir():
            return candidate
        p = p.parent
    # 3. Common roots
    for root in ("Projects", "Documents", "dev", "src", "code", ""):
        base = (Path.home() / root) if root else Path.home()
        candidate = base / "Great-Sage" / "plugins"
        if candidate.is_dir():
            return candidate
    # 4. Fallback: create next to this file
    fallback = here / "plugins"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback

PLUGINS_DIR    = _find_plugins_dir()
PLUGINS_CONFIG = Path.home() / ".great_sage_plugins.json"
PLUGIN_DATA    = Path.home() / ".great_sage_plugin_data.json"

# ── Colour / font constants (synced with gs_theme.py) ─────────────────────────
try:
    from gs_theme import (
        BG, BG2, BG3, PANEL, BORDER, BORDER2,
        ACCENT, ACCENT2, NEON, RED, BLUE, PURPLE,
        TEXT, TEXT2, MUTED,
        FONT_UI, FONT_BODY, FONT_DISPLAY,
        accent_btn_style, danger_btn_style, nav_btn_style,
    )
    _COLOURS = {
        "BG": BG, "BG2": BG2, "BG3": BG3, "PANEL": PANEL,
        "BORDER": BORDER, "ACCENT": ACCENT, "ACCENT2": ACCENT2,
        "NEON": NEON, "MUTED": MUTED, "TEXT": TEXT,
        "TEXT2": TEXT2, "RED": RED, "BLUE": BLUE, "PURPLE": PURPLE,
    }
    _FONTS = {"UI": FONT_UI, "BODY": FONT_BODY, "DISPLAY": FONT_DISPLAY}
except ImportError:
    # Standalone fallback if gs_theme is unavailable
    BG, BG2, BG3, PANEL, BORDER, BORDER2 = "#0C0C0E", "#111113", "#141417", "#18181B", "#222225", "#2A2A2E"
    ACCENT, ACCENT2, NEON, RED, BLUE, PURPLE = "#E8C97A", "#4FC4A0", "#00E5CC", "#E05C6A", "#4A9EE0", "#9A70E0"
    TEXT, TEXT2, MUTED = "#D4E4EE", "#7A9BB0", "#4A6070"
    FONT_UI = "JetBrains Mono, Fira Code, Consolas, monospace"
    FONT_BODY = "Palatino Linotype, Palatino, Book Antiqua, Georgia, serif"
    FONT_DISPLAY = "Palatino Linotype, Palatino, Book Antiqua, serif"
    _COLOURS = {
        "BG": BG, "BG2": BG2, "BG3": BG3, "PANEL": PANEL,
        "BORDER": BORDER, "ACCENT": ACCENT, "ACCENT2": ACCENT2,
        "NEON": NEON, "MUTED": MUTED, "TEXT": TEXT,
        "TEXT2": TEXT2, "RED": RED, "BLUE": BLUE, "PURPLE": PURPLE,
    }
    _FONTS = {"UI": FONT_UI, "BODY": FONT_BODY, "DISPLAY": FONT_DISPLAY}

# Import shared painting utility
try:
    from gs_theme import paint_topbar
except ImportError:
    paint_topbar = None


# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        if PLUGINS_CONFIG.exists():
            return json.loads(PLUGINS_CONFIG.read_text())
    except Exception:
        pass
    return {}


def _save_config(cfg: dict):
    tmp = str(PLUGINS_CONFIG) + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, str(PLUGINS_CONFIG))
    except Exception:
        pass


def _load_plugin_data() -> dict:
    try:
        if PLUGIN_DATA.exists():
            return json.loads(PLUGIN_DATA.read_text())
    except Exception:
        pass
    return {}


def _save_plugin_data(data: dict):
    tmp = str(PLUGIN_DATA) + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, str(PLUGIN_DATA))
    except Exception:
        pass


# ── Plugin API ─────────────────────────────────────────────────────────────────

class PluginAPI:
    """
    Passed to every plugin's build_page().
    Gives read access to app data, isolated storage, and optional Sage access.
    """

    def __init__(self, plugin_name: str, status_callback=None):
        self._name     = plugin_name
        self._status_cb = status_callback
        self.colours   = _COLOURS
        self.fonts     = _FONTS

    # ── Core data ──────────────────────────────────────────────────────────────

    def legion_data(self) -> dict:
        """Return the current Legion progress data (books, chapters, etc.)."""
        try:
            path = Path.home() / ".great_sage_legion.json"
            if path.exists():
                return json.loads(path.read_text())
        except Exception:
            pass
        return {"books": {}}

    def matrix_data(self) -> dict:
        """Return the current Matrix data (watching, watchlist, settings)."""
        try:
            path = Path.home() / ".config" / "matrix" / "progress.json"
            if path.exists():
                return json.loads(path.read_text())
        except Exception:
            pass
        return {"watchlist": {}, "watching": {}, "completed": {}}

    # ── Plugin-isolated storage ────────────────────────────────────────────────

    def save_plugin_data(self, key: str, value) -> None:
        """
        Persist any JSON-serialisable value under a namespaced key.
        key is automatically namespaced to this plugin so plugins can't clash.
        """
        data = _load_plugin_data()
        ns   = f"{self._name}::{key}"
        data[ns] = value
        _save_plugin_data(data)

    def load_plugin_data(self, key: str):
        """Retrieve a previously saved value, or None if not found."""
        data = _load_plugin_data()
        return data.get(f"{self._name}::{key}")

    def delete_plugin_data(self, key: str) -> None:
        """Delete a stored value."""
        data = _load_plugin_data()
        data.pop(f"{self._name}::{key}", None)
        _save_plugin_data(data)

    # ── Sage ───────────────────────────────────────────────────────────────────

    def sage_chat(self, prompt: str) -> str:
        """
        Send a prompt to Sage and return the response string.
        Blocks the calling thread — call from a QThread, not the GUI thread.
        Returns an empty string on error.
        """
        try:
            import sage as _sage
            matrix_data = self.matrix_data()
            api_key = matrix_data.get("settings", {}).get("groq_api_key", "")
            if api_key:
                _sage.GROQ_API_KEY = api_key
            groq_model = matrix_data.get("settings", {}).get("groq_model", "")
            if groq_model:
                _sage.GROQ_MODEL = groq_model
            response, error = _sage.groq_chat(prompt)
            return response or ""
        except Exception:
            return ""
    # ── UI helpers ─────────────────────────────────────────────────────────────

    def show_status(self, msg: str) -> None:
        """Show a message in the app's status bar (if available)."""
        if self._status_cb:
            try:
                self._status_cb(msg)
            except Exception:
                pass

    def make_label(self, text: str, color: str = None,
                   size: int = 13, bold: bool = False) -> QLabel:
        """Convenience — create a styled QLabel matching app style."""
        w = QLabel(text)
        c = color or TEXT
        s = f"color:{c};font-size:{size}px;font-family:{FONT_UI};"
        if bold:
            s += "font-weight:bold;"
        w.setStyleSheet(s)
        return w

    def make_button(self, text: str, accent: bool = False) -> QPushButton:
        """Convenience — create a styled QPushButton matching app style."""
        b = QPushButton(text)
        if accent:
            b.setStyleSheet(
                f"QPushButton {{ background:{ACCENT}; color:{BG}; border:none; "
                f"font-weight:bold; font-size:12px; letter-spacing:1px; "
                f"padding:8px 18px; border-radius:4px; }}"
                f"QPushButton:hover {{ background:#F0D98A; }}")
        else:
            b.setStyleSheet(
                f"QPushButton {{ background:transparent; color:{TEXT2}; "
                f"border:1px solid {BORDER}; font-size:12px; padding:7px 16px; "
                f"border-radius:4px; }}"
                f"QPushButton:hover {{ border-color:{ACCENT}; color:{ACCENT}; }}")
        return b

    # ── Slot injection ─────────────────────────────────────────────────────────

    # Available slot names:
    #   "dashboard_top"         — full-width strip above the dashboard title
    #   "dashboard_below_cards" — full-width area below the 4 main cards
    #   "reader_sidebar"        — extra panel in the Legion reader
    #   "matrix_header"         — strip above the Matrix tab bar
    #   "sage_below_output"     — area below Sage's response text

    def register_slot(self, slot_name: str, widget: "QWidget",
                      label: str = "") -> bool:
        """
        Inject a widget into a named slot anywhere in the main app.
        Call from build_page() on the GUI thread.
        Returns True if the slot name is valid.
        """
        return SlotRegistry.instance().register(
            slot_name, self._name, widget, label)

    def unregister_slot(self, slot_name: str) -> None:
        """Remove this plugin's widget from a slot."""
        SlotRegistry.instance().unregister(slot_name, self._name)


# ── Slot registry (singleton) ──────────────────────────────────────────────────

VALID_SLOTS = {
    "dashboard_top",
    "dashboard_below_cards",
    "dashboard_background",   # full-bleed background layer behind dashboard content
    "reader_sidebar",
    "matrix_header",
    "sage_below_output",
}


class SlotRegistry:
    """
    Central registry: slot_name → list of (plugin_name, widget, label).
    SlotHost widgets subscribe here and rebuild when entries change.
    """
    _instance: "SlotRegistry | None" = None

    @classmethod
    def instance(cls) -> "SlotRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._slots:     dict[str, list[dict]] = {s: [] for s in VALID_SLOTS}
        self._listeners: dict[str, list]       = {s: [] for s in VALID_SLOTS}

    def register(self, slot_name: str, plugin_name: str,
                 widget, label: str = "") -> bool:
        if slot_name not in VALID_SLOTS:
            return False
        self._slots[slot_name] = [
            e for e in self._slots[slot_name] if e["plugin"] != plugin_name
        ]
        self._slots[slot_name].append(
            {"plugin": plugin_name, "widget": widget, "label": label})
        self._notify(slot_name)
        return True

    def unregister(self, slot_name: str, plugin_name: str):
        if slot_name not in VALID_SLOTS:
            return
        self._slots[slot_name] = [
            e for e in self._slots[slot_name] if e["plugin"] != plugin_name
        ]
        self._notify(slot_name)

    def entries(self, slot_name: str) -> list:
        return list(self._slots.get(slot_name, []))

    def add_listener(self, slot_name: str, callback):
        if slot_name in self._listeners:
            self._listeners[slot_name].append(callback)

    def _notify(self, slot_name: str):
        for cb in self._listeners.get(slot_name, []):
            try:
                cb()
            except Exception:
                pass


# ── SlotHost widget ────────────────────────────────────────────────────────────

class SlotHost(QWidget):
    """
    Drop this into any layout in the main app at a named slot position.
    Auto-shows/hides based on whether any plugin has registered content.

    Usage in great_sage_gui.py:
        from plugin_manager import SlotHost
        slot = SlotHost("dashboard_top")
        layout.addWidget(slot)
    """

    def __init__(self, slot_name: str, parent=None):
        super().__init__(parent)
        self._slot_name = slot_name
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self.setVisible(False)
        SlotRegistry.instance().add_listener(slot_name, self._rebuild)
        self._rebuild()  # Initial rebuild to pick up pre-registered widgets

    def _rebuild(self):
        # Detach existing widgets without destroying them
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)

        entries = SlotRegistry.instance().entries(self._slot_name)
        if not entries:
            self.setVisible(False)
            self.setMaximumHeight(0)
            return

        self.setMaximumHeight(16777215)  # restore unlimited

        for entry in entries:
            if entry["label"]:
                lbl = QLabel(entry["label"])
                lbl.setStyleSheet(
                    f"color:{MUTED};font-size:9px;letter-spacing:2px;padding:2px 0 0 2px;")
                self._layout.addWidget(lbl)
            w = entry["widget"]
            w.setParent(self)
            self._layout.addWidget(w)
            w.show()

        self.setVisible(True)
        self._resize_children()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_children()

    def _resize_children(self):
        """Make children fill this widget's full area (used by background slots)."""
        for i in range(self._layout.count()):
            item = self._layout.itemAt(i)
            if not item: continue
            w = item.widget()
            if w and hasattr(w, "_fill_parent"):
                w._fill_parent()


# ── Plugin loader ──────────────────────────────────────────────────────────────

class PluginRecord:
    """Everything the manager knows about one plugin."""
    def __init__(self, path: Path):
        self.path        = path
        self.filename    = path.name
        self.name        = path.stem
        self.icon        = "◉"
        self.description = ""
        self.version     = ""
        self.author      = ""
        self.color       = ACCENT2
        self.enabled     = False
        self.loaded      = False
        self.error       = ""
        self._mod        = None
        self._page       = None   # built QWidget, if any

    def read_metadata(self):
        """Read PLUGIN_* constants from the file using AST for safe parsing."""
        try:
            import ast
            source = self.path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            if not isinstance(node.value, ast.Constant):
                                continue
                            val = node.value.value
                            if target.id == "PLUGIN_NAME":
                                self.name = val
                            elif target.id == "PLUGIN_ICON":
                                self.icon = val
                            elif target.id == "PLUGIN_DESCRIPTION":
                                self.description = val
                            elif target.id == "PLUGIN_VERSION":
                                self.version = val
                            elif target.id == "PLUGIN_AUTHOR":
                                self.author = val
                            elif target.id == "PLUGIN_COLOR":
                                self.color = val
        except Exception as e:
            self.error = str(e)

    def load_module(self) -> bool:
        """Fully import the plugin module. Returns True on success."""
        if self._mod:
            return True
        try:
            spec = importlib.util.spec_from_file_location(
                f"gs_plugin_{self.path.stem}", str(self.path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._mod   = mod
            self.loaded = True
            self.error  = ""
            _log.plugin.info("Plugin loaded", plugin=self.name, file=self.path.name)
            return True
        except Exception as e:
            self.error  = str(e)
            self.loaded = False
            _log.plugin.error("Plugin load failed", plugin=self.name,
                              file=self.path.name, error=str(e))
            return False

    def build_page(self, parent: QWidget, api: PluginAPI) -> QWidget | None:
        """Call the plugin's build_page() and cache the result."""
        if not self.loaded and not self.load_module():
            return None
        if self._page:
            return self._page
        try:
            page = self._mod.build_page(parent, api)
            self._page = page
            return page
        except Exception as e:
            self.error = str(e)
            return None

    def call_refresh(self):
        """Call the plugin's refresh() if defined."""
        if not self.loaded or not self._mod:
            return
        if hasattr(self._mod, "refresh") and self._page:
            try:
                self._mod.refresh(self._page)
            except Exception:
                pass


# ── Plugin engine ──────────────────────────────────────────────────────────────

class PluginEngine:
    """
    Singleton-style manager.  Discovers plugins, tracks enabled state,
    and provides the registry to the UI.
    """

    def __init__(self):
        self._plugins: dict[str, PluginRecord] = {}   # filename → record
        self._config:  dict                    = {}
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        self.reload()

    def reload(self):
        """Re-scan the plugins folder and merge with saved config."""
        self._config = _load_config()
        seen = set()

        for path in sorted(PLUGINS_DIR.glob("*.py")):
            if path.name.startswith("_"):
                continue
            key = path.name
            seen.add(key)
            if key not in self._plugins:
                rec = PluginRecord(path)
                rec.read_metadata()
                self._plugins[key] = rec
            else:
                # Refresh metadata in case the file changed
                self._plugins[key].read_metadata()

            # Default new plugins to disabled — user must explicitly enable them
            self._plugins[key].enabled = self._config.get(key, {}).get("enabled", False)

        # Remove records for deleted files
        for key in list(self._plugins.keys()):
            if key not in seen:
                del self._plugins[key]

    def all_plugins(self) -> list[PluginRecord]:
        return list(self._plugins.values())

    def enabled_plugins(self) -> list[PluginRecord]:
        return [p for p in self._plugins.values() if p.enabled]

    def set_enabled(self, filename: str, enabled: bool):
        rec = self._plugins.get(filename)
        if not rec:
            return
        rec.enabled = enabled
        cfg = self._config.setdefault(filename, {})
        cfg["enabled"] = enabled
        _save_config(self._config)

    def get(self, filename: str) -> PluginRecord | None:
        return self._plugins.get(filename)

    @property
    def plugins_dir(self) -> Path:
        return PLUGINS_DIR


# ── Plugins Hub page (shown inside the main app) ──────────────────────────────

class PluginsPage(QWidget):
    """
    The full-page UI for the Plugins section.
    Left sidebar: list of enabled plugins.
    Right area: plugin content (stacked) + manager panel.
    """

    navigate_home = pyqtSignal()   # emitted when user clicks ← HOME

    def __init__(self, engine: PluginEngine, status_cb=None):
        super().__init__()
        self._engine    = engine
        self._status_cb = status_cb
        self._api_cache: dict[str, PluginAPI]  = {}
        self._page_stack: QStackedWidget | None = None
        self._sidebar_btns: dict[str, QPushButton] = {}
        self._current_plugin: str | None = None
        self._build()

    def _api(self, plugin: PluginRecord) -> PluginAPI:
        if plugin.filename not in self._api_cache:
            self._api_cache[plugin.filename] = PluginAPI(
                plugin.name, status_callback=self._status_cb)
        return self._api_cache[plugin.filename]

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        import types as _pt0
        self._back_bar = QWidget()
        self._back_bar.setFixedHeight(44)
        if paint_topbar:
            self._back_bar.paintEvent = _pt0.MethodType(paint_topbar, self._back_bar)
        bbl0 = QHBoxLayout(self._back_bar)
        bbl0.setContentsMargins(20, 0, 20, 0)
        bbl0.setSpacing(0)
        back_b0 = QPushButton("← All Plugins")
        back_b0.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;color:{MUTED};"
            f"font-size:9px;letter-spacing:1.5px;padding:4px 0;}}"
            f"QPushButton:hover{{color:{ACCENT};}}")
        back_b0.clicked.connect(self._show_manager)
        self._back_bar_title = QLabel("")
        self._back_bar_title.setStyleSheet(
            f"font-family:{FONT_DISPLAY};font-size:12px;font-weight:bold;"
            f"color:{ACCENT};letter-spacing:3px;margin-left:16px;background:transparent;")
        bbl0.addWidget(back_b0)
        bbl0.addWidget(self._back_bar_title)
        bbl0.addStretch()
        self._back_bar.hide()
        root.addWidget(self._back_bar)

        # ── Single stacked content area — no sidebar ───────────────────────────
        self._page_stack = QStackedWidget()

        # Index 0 — manager panel (always present)
        self._manager_panel = self._build_manager()
        self._page_stack.addWidget(self._manager_panel)

        # Index 1+ — plugin pages (added dynamically)
        self._plugin_page_indices: dict[str, int] = {}

        root.addWidget(self._page_stack, 1)

        # Stub nav attributes so existing code doesn't break
        self._nav_container = QWidget()
        self._nav_layout    = QVBoxLayout(self._nav_container)
        self._sidebar_btns: dict[str, QPushButton] = {}

    def _build_manager(self) -> QWidget:
        """Build the plugin manager panel."""
        w = QWidget()
        w.setStyleSheet(f"background:{BG};")
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Top header bar (replaces sidebar header)
        import types as _pt
        top_bar = QWidget()
        top_bar.setFixedHeight(52)
        if paint_topbar:
            top_bar.paintEvent = _pt.MethodType(paint_topbar, top_bar)
        tb = QHBoxLayout(top_bar)
        tb.setContentsMargins(24, 0, 20, 0)
        tb.setSpacing(0)
        back_btn = QPushButton("← HOME")
        back_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;color:{MUTED};"
            f"font-size:9px;letter-spacing:1.5px;padding:4px 0;}}"
            f"QPushButton:hover{{color:{ACCENT};}}")
        back_btn.clicked.connect(self.navigate_home.emit)
        tl = QLabel("PLUGINS")
        tl.setStyleSheet(
            f"font-family:{FONT_DISPLAY};font-size:13px;font-weight:bold;"
            f"color:{ACCENT};letter-spacing:3px;margin-left:14px;background:transparent;")
        self._reload_btn = QPushButton("↻  SCAN FOR PLUGINS")
        self._reload_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:1px solid {BORDER};color:{MUTED};"
            f"font-size:9px;letter-spacing:1.5px;padding:6px 14px;border-radius:3px;}}"
            f"QPushButton:hover{{border-color:{ACCENT};color:{ACCENT};}}")
        self._reload_btn.clicked.connect(self._do_reload)
        tb.addWidget(back_btn)
        tb.addWidget(tl)
        tb.addStretch()
        tb.addWidget(self._reload_btn)
        v.addWidget(top_bar)

        body = QWidget()
        body.setStyleSheet(f"background:{BG};")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(28, 16, 28, 20)
        bv.setSpacing(16)
        v.addWidget(body, 1)
        v = bv  # redirect remaining adds to body layout

        # Plugin folder hint
        hint = QLabel(f"Plugin folder:  {self._engine.plugins_dir}")
        hint.setStyleSheet(f"color:{MUTED};font-size:11px;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        self._hline = QFrame()
        self._hline.setFrameShape(QFrame.Shape.HLine)
        self._hline.setStyleSheet(f"color:{BORDER};")
        v.addWidget(self._hline)

        # Scrollable plugin list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea{{border:none;background:transparent;}}"
            f"QScrollBar:vertical{{background:{BG};width:8px;border:none;}}"
            f"QScrollBar::handle:vertical{{background:{BORDER};border-radius:4px;min-height:30px;}}")
        self._mgr_container = QWidget()
        self._mgr_container.setStyleSheet("background:transparent;")
        self._mgr_layout = QVBoxLayout(self._mgr_container)
        self._mgr_layout.setContentsMargins(0, 0, 0, 0)
        self._mgr_layout.setSpacing(10)
        self._mgr_layout.addStretch()
        scroll.setWidget(self._mgr_container)
        v.addWidget(scroll, 1)

        # Open folder button
        open_btn = QPushButton("📂  Open Plugins Folder")
        open_btn.setStyleSheet(
            f"background:transparent;border:1px solid {BORDER};color:{MUTED};"
            f"font-size:11px;padding:8px 16px;border-radius:4px;")
        open_btn.clicked.connect(self._open_plugins_folder)
        v.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignLeft)

        return w

    def _build_plugin_card(self, rec: PluginRecord) -> QWidget:
        """Build one plugin row in the manager list."""
        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:{BG2};border:1px solid {BORDER};border-radius:6px;}}"
            f"QFrame:hover{{border-color:{rec.color}44;}}")

        cl = QHBoxLayout(card); cl.setContentsMargins(16, 12, 16, 12); cl.setSpacing(14)

        # Icon + name + meta
        ico = QLabel(rec.icon)
        ico.setStyleSheet(f"color:{rec.color};font-size:28px;background:transparent;")
        ico.setFixedWidth(36)
        cl.addWidget(ico)

        info = QVBoxLayout(); info.setSpacing(2)
        name_lbl = QLabel(rec.name)
        name_lbl.setStyleSheet(
            f"color:{TEXT};font-size:13px;font-weight:bold;font-family:{FONT_UI};background:transparent;")
        info.addWidget(name_lbl)
        if rec.description:
            desc_lbl = QLabel(rec.description)
            desc_lbl.setStyleSheet(f"color:{TEXT2};font-size:11px;background:transparent;")
            desc_lbl.setWordWrap(True)
            info.addWidget(desc_lbl)
        meta_parts = []
        if rec.version: meta_parts.append(f"v{rec.version}")
        if rec.author:  meta_parts.append(rec.author)
        if rec.error:   meta_parts.append(f"⚠ {rec.error[:60]}")
        if meta_parts:
            meta_lbl = QLabel("  ·  ".join(meta_parts))
            meta_lbl.setStyleSheet(
                f"color:{'#E05C6A' if rec.error else MUTED};font-size:10px;background:transparent;")
            info.addWidget(meta_lbl)

        cl.addLayout(info, 1)

        # Settings button (only for enabled plugins)
        if rec.enabled:
            settings_btn = QPushButton("⚙ Settings")
            settings_btn.setStyleSheet(
                f"QPushButton{{background:transparent;border:1px solid {BORDER};"
                f"color:{MUTED};font-size:9px;letter-spacing:1px;"
                f"padding:5px 10px;border-radius:3px;}}"
                f"QPushButton:hover{{border-color:{rec.color};color:{rec.color};}}")
            settings_btn.clicked.connect(lambda _, r=rec: self._open_plugin(r))
            cl.addWidget(settings_btn)

        # Enable/disable toggle
        toggle = QCheckBox()
        toggle.setChecked(rec.enabled)
        toggle.setStyleSheet(
            f"QCheckBox::indicator{{width:36px;height:20px;border-radius:10px;"
            f"border:1px solid {BORDER};}}"
            f"QCheckBox::indicator:checked{{background:{ACCENT2};border-color:{ACCENT2};}}"
            f"QCheckBox::indicator:unchecked{{background:{BG3};}}")
        toggle.toggled.connect(lambda checked, r=rec: self._toggle_plugin(r, checked))
        cl.addWidget(toggle)

        # Delete button
        del_btn = QPushButton("✕")
        del_btn.setFixedSize(28, 28)
        del_btn.setToolTip("Delete plugin file")
        del_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:1px solid {BORDER};"
            f"color:{MUTED};font-size:11px;border-radius:3px;}}"
            f"QPushButton:hover{{background:#2A0E14;border-color:{RED};color:{RED};}}")
        del_btn.clicked.connect(lambda _, r=rec: self._delete_plugin(r))
        cl.addWidget(del_btn)

        return card

    # ── Actions ────────────────────────────────────────────────────────────────

    def _delete_plugin(self, rec: PluginRecord):
        """Permanently delete a plugin file after confirmation."""
        reply = QMessageBox.question(
            self,
            "Delete Plugin",
            f"Permanently delete '{rec.name}'?\n\n{rec.filename} will be removed from disk.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Unregister slots and navigate away if active
        if self._current_plugin == rec.filename:
            self._show_manager()
        try:
            from plugin_manager import SlotRegistry
            for slot in ("dashboard_top", "dashboard_below_cards",
                         "reader_sidebar", "matrix_header", "sage_below_output"):
                SlotRegistry.instance().unregister(slot, rec.name)
        except Exception:
            pass

        # Remove from config
        self._engine._config.pop(rec.filename, None)
        from plugin_manager import _save_config
        _save_config(self._engine._config)

        # Delete the file
        try:
            rec.path.unlink()
        except Exception as e:
            QMessageBox.warning(self, "Delete Failed", f"Could not delete file:\n{e}")
            return

        # Reload engine and refresh UI
        self._do_reload()

    def _toggle_plugin(self, rec: PluginRecord, enabled: bool):
        self._engine.set_enabled(rec.filename, enabled)
        # Rebuild sidebar nav
        self._rebuild_nav()
        if not enabled:
            # If currently viewing this plugin, go back to manager
            if self._current_plugin == rec.filename:
                self._show_manager()
            # Unregister any slots it claimed
            try:
                from plugin_manager import SlotRegistry
                for slot in ("dashboard_top", "dashboard_below_cards",
                             "reader_sidebar", "matrix_header", "sage_below_output"):
                    SlotRegistry.instance().unregister(slot, rec.name)
            except Exception:
                pass
        else:
            # Plugin just enabled — build its page immediately so slots register
            if rec.filename not in self._plugin_page_indices:
                self._open_plugin(rec)
                # Stay on manager after enabling
                self._show_manager()

    def _do_reload(self):
        self._engine.reload()
        self._plugin_page_indices.clear()  # force rebuild with back button wrappers
        self.refresh()

    def _open_plugins_folder(self):
        import subprocess
        try:
            subprocess.Popen(["xdg-open", str(self._engine.plugins_dir)])
        except Exception:
            pass

    def _show_manager(self):
        self._current_plugin = None
        self._page_stack.setCurrentIndex(0)
        self._update_nav_active(None)
        self._back_bar.hide()

    def _open_plugin(self, rec: PluginRecord):
        """Navigate to a plugin's page, building it on first open."""
        if rec.filename not in self._plugin_page_indices:
            api  = self._api(rec)
            page = rec.build_page(self, api)
            if page is None:
                QMessageBox.critical(
                    self, f"Plugin error — {rec.name}",
                    f"Failed to load plugin:\n\n{rec.error or 'Unknown error'}\n\n"
                    f"Check the plugin file for syntax errors.")
                return
            idx = self._page_stack.count()
            self._page_stack.addWidget(page)
            self._plugin_page_indices[rec.filename] = idx

        self._current_plugin = rec.filename
        self._page_stack.setCurrentIndex(self._plugin_page_indices[rec.filename])
        self._update_nav_active(rec.filename)
        # Show back bar with plugin name
        self._back_bar_title.setText(f"{rec.icon}  {rec.name.upper()}")
        self._back_bar_title.setStyleSheet(
            f"font-family:{FONT_DISPLAY};font-size:12px;font-weight:bold;"
            f"color:{rec.color};letter-spacing:3px;margin-left:16px;background:transparent;")
        self._back_bar.show()
        rec.call_refresh()

    def _update_nav_active(self, active_filename: str | None):
        for fname, btn in self._sidebar_btns.items():
            btn.setProperty("active", fname == active_filename)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _rebuild_nav(self):
        """Rebuild the sidebar plugin navigation buttons."""
        # Clear existing
        while self._nav_layout.count():
            item = self._nav_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._sidebar_btns.clear()

        enabled = self._engine.enabled_plugins()
        for rec in enabled:
            label = f"{rec.icon}  {rec.name}"
            btn = QPushButton(label)
            btn.setStyleSheet(nav_btn_style())
            btn.clicked.connect(lambda _, r=rec: self._open_plugin(r))
            self._nav_layout.addWidget(btn)
            self._sidebar_btns[rec.filename] = btn

        if not enabled:
            hint = QLabel("No plugins enabled")
            hint.setStyleSheet(f"color:{MUTED};font-size:10px;padding:8px 18px;letter-spacing:1px;")
            self._nav_layout.addWidget(hint)

    def _rebuild_manager_list(self):
        """Rebuild the plugin cards in the manager panel."""
        while self._mgr_layout.count() > 1:   # keep trailing stretch
            item = self._mgr_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        plugins = self._engine.all_plugins()
        if not plugins:
            empty = QLabel(
                f"No plugins found.\n\n"
                f"Drop a .py plugin file into:\n{self._engine.plugins_dir}")
            empty.setStyleSheet(f"color:{MUTED};font-size:12px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            self._mgr_layout.insertWidget(0, empty)
        else:
            for i, rec in enumerate(plugins):
                card = self._build_plugin_card(rec)
                self._mgr_layout.insertWidget(i, card)

    # ── Public ─────────────────────────────────────────────────────────────────

    def refresh(self):
        """Called when the user navigates to the Plugins page."""
        self._rebuild_nav()
        self._rebuild_manager_list()
        if self._current_plugin:
            rec = self._engine.get(self._current_plugin)
            if rec and rec.enabled:
                rec.call_refresh()
            else:
                self._show_manager()


# ── Example plugin (written to the plugins folder on first run) ────────────────

_EXAMPLE_PLUGIN_SRC = '''\
"""
hello_sage.py — Example Great Sage Plugin
==========================================
This is a minimal example showing how to build a plugin.
Drop any .py file into the plugins folder to add new features.
"""

PLUGIN_NAME        = "Hello Sage"
PLUGIN_ICON        = "👋"
PLUGIN_DESCRIPTION = "Example plugin — shows your reading & watching stats"
PLUGIN_VERSION     = "1.0"
PLUGIN_AUTHOR      = "Great Sage"
PLUGIN_COLOR       = "#4FC4A0"

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame
from PyQt6.QtCore    import Qt


def build_page(parent, api):
    """Build and return the plugin\'s page widget."""
    colours = api.colours

    w = QWidget(parent)
    w.setStyleSheet(f"background:{colours[\'BG\']};")
    v = QVBoxLayout(w); v.setContentsMargins(40, 32, 40, 32); v.setSpacing(20)

    title = api.make_label("👋  HELLO SAGE", colours["ACCENT"], 18, bold=True)
    v.addWidget(title)

    sub = api.make_label(
        "This is an example plugin. Replace this file to build your own feature.",
        colours["TEXT2"], 13)
    sub.setWordWrap(True)
    v.addWidget(sub)

    # Show some live data from the app
    ld = api.legion_data()
    md = api.matrix_data()

    books    = ld.get("books", {})
    watching = md.get("watching", {})

    stats_frame = QFrame()
    stats_frame.setStyleSheet(
        f"background:{colours[\'BG2\']};border:1px solid {colours[\'BORDER\']};"
        f"border-radius:8px;padding:16px;")
    sf = QVBoxLayout(stats_frame); sf.setSpacing(10)

    sf.addWidget(api.make_label("YOUR STATS", colours["MUTED"], 9, bold=True))

    total_words = sum(b.get("words_read", 0) for b in books.values())
    total_ch    = sum(b.get("chapters_read", 0) for b in books.values())
    sf.addWidget(api.make_label(
        f"📚  {len(books)} book(s) in library  ·  {total_ch} chapters read  ·  {total_words:,} words",
        colours["TEXT"], 13))
    sf.addWidget(api.make_label(
        f"📺  {len(watching)} show(s) in continue watching",
        colours["TEXT"], 13))

    v.addWidget(stats_frame)
    v.addStretch()

    hint = api.make_label(
        f"Plugin file: plugins/hello_sage.py  —  edit it to build your own feature.",
        colours["MUTED"], 10)
    hint.setWordWrap(True)
    v.addWidget(hint)

    return w


def refresh(page):
    """Called when user navigates to this plugin — rebuild live data if needed."""
    pass  # For a stateless display like this, nothing to do
'''


def _write_example_plugin():
    """Write the example plugin only on very first run (no plugins exist at all)."""
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    # Only write if the plugins folder is completely empty
    existing = list(PLUGINS_DIR.glob("*.py"))
    if existing:
        return   # user already has plugins, don't interfere
    dest = PLUGINS_DIR / "hello_sage.py"
    try:
        dest.write_text(_EXAMPLE_PLUGIN_SRC, encoding="utf-8")
    except Exception:
        pass


# ── Module entry point (called by great_sage_gui.py) ──────────────────────────

def create_plugins_page(status_cb=None) -> tuple[PluginEngine, PluginsPage]:
    """
    Create and return plugin engine + page widget.
    Call once from MainWindow.__init__. Pass status bar callback.

    Example usage in great_sage_gui.py:
        from plugin_manager import create_plugins_page
        self._plugin_engine, plugins_page = create_plugins_page(
            status_cb=lambda m: self._status.showMessage(f"  {m}"))
        self._pages.addWidget(plugins_page)
        self._page_objs["plugins"] = plugins_page
        plugins_page.navigate_home.connect(lambda: self._navigate("dashboard"))
    """
    _write_example_plugin()
    
    engine = PluginEngine()
    page = PluginsPage(engine, status_cb)
    
    # Auto-activate background plugins that need to run immediately
    _activate_background_plugins(engine, status_cb)
    
    return engine, page


def _activate_background_plugins(engine: PluginEngine, status_cb=None):
    """
    Automatically activate plugins that register background slots at startup.
    These plugins need to be active immediately to provide their functionality.
    """
    for plugin in engine.enabled_plugins():
        try:
            if plugin.load_module():
                mod = plugin._mod
                
                # Check if plugin has background registration functions
                if hasattr(mod, 'on_enable') and hasattr(mod, 'build_page'):
                    api = PluginAPI(plugin.name, status_cb)
                    
                    # Call build_page first to initialize the plugin
                    try:
                        mod.build_page(None, api)
                        _log.plugin.info("Auto-activated background plugin", plugin=plugin.name)
                    except Exception as e:
                        _log.plugin.warning("Failed to auto-activate plugin", 
                                          plugin=plugin.name, error=str(e))
                        
        except Exception as e:
            _log.plugin.warning("Failed to load plugin for auto-activation", 
                              plugin=plugin.name, error=str(e))
