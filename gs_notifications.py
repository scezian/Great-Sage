"""
gs_notifications.py — Great Sage
=================================
Centralised notification system.

Owns:
  - NotificationStore  (moved from great_sage_core.py)
  - get_notification_store()  singleton accessor
  - push_notification()  one-call helper: dedup + cooldown + bell refresh
  - dismiss_notification()  remove a notification by id + bell refresh

Design rules
------------
1.  NO Qt widget imports at module level — avoids circular import with
    gs_widgets.py / great_sage_gui.py.
2.  Bell refresh is fired via QMetaObject.invokeMethod on the main thread
    so it is always safe to call push_notification() from any background
    thread (sync cycle, token refresh, update checker, etc.).
3.  NotificationStore._save() delegates to great_sage_core.save_json()
    so writes are atomic and fd-safe (same guard as all other JSON saves).
4.  Deduplication: skip if an *unread* notification with the same id
    already exists.  A previously *read* + dismissed one can be re-raised
    (handles mid-session token expiry after the user already dismissed an
    earlier "not logged in" alert).
5.  Cooldown: per-id, don't re-fire within COOLDOWN_SECONDS even if the
    previous one was read.  Prevents notification spam on every 3-min
    sync cycle while the user is logged out.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

# ── Constants ──────────────────────────────────────────────────────────────────
COOLDOWN_SECONDS = 30 * 60   # 30 minutes between repeat notifications (same id)

# ── Lazily resolved paths / helpers ───────────────────────────────────────────
def _notifications_path() -> str:
    from great_sage_core import NOTIFICATIONS_PATH
    return NOTIFICATIONS_PATH

def _save_json(path: str, data) -> bool:
    from great_sage_core import save_json
    return save_json(path, data)

def _log():
    try:
        from gs_logger import log
        return log
    except Exception:
        class _N:
            def __getattr__(self, n): return _N()
            def __call__(self, *a, **kw): return None
        return _N()


# ═══════════════════════════════════════════════════════════════════════════════
# NotificationStore
# ═══════════════════════════════════════════════════════════════════════════════

class NotificationStore:
    """
    Persistent per-user notification list stored at ~/.gs_notifications.json.

    Each notification is a dict:
        id        — unique string, e.g. "update-1.4.2", "rec-<uuid>",
                    "cloud_not_logged_in"
        type      — "update" | "friend_rec" | "warning" | "info"
        title     — short display string
        body      — optional longer description (may be empty string)
        timestamp — ISO 8601 UTC string
        read      — bool
        data      — type-specific payload dict

    Thread-safety: all mutations are protected by a threading.Lock.
    """

    _instance: "NotificationStore | None" = None
    _class_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "NotificationStore":
        if cls._instance is None:
            with cls._class_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._path  = _notifications_path()
        self._items: list[dict] = []
        self._mutex = threading.Lock()
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        """Load from disk. Supports legacy plain-list and new wrapped-dict format."""
        try:
            import os, json
            if os.path.exists(self._path):
                with open(self._path, "r") as f:
                    raw = json.load(f)
                if isinstance(raw, list):
                    self._items = raw
                elif isinstance(raw, dict) and "_notifications" in raw:
                    self._items = raw["_notifications"]
                else:
                    self._items = []
        except Exception as e:
            _log().warning("NotificationStore: failed to load", error=str(e))
            self._items = []

    def _save(self):
        """Atomic write via save_json (same fd-safety as all other JSON paths)."""
        _save_json(self._path, {"_notifications": self._items})

    # ── Public API ────────────────────────────────────────────────────────────

    def add(self,
            notif_type: str,
            title: str,
            data: dict,
            notif_id: str | None = None,
            body: str = "") -> dict:
        """
        Add a new unread notification.

        Deduplication rule: if an *unread* notification with the same id
        already exists, do nothing and return {}.
        If a *read* one exists with the same id it IS replaced (allows
        re-notification after the user dismissed a previous alert).
        """
        nid = notif_id or f"{notif_type}-{datetime.now(timezone.utc).timestamp():.0f}"
        with self._mutex:
            for existing in self._items:
                if existing["id"] == nid and not existing.get("read", False):
                    return {}   # already unread — don't duplicate

            # Remove any old read entry with the same id so the new one is
            # always at the top (newest first).
            self._items = [n for n in self._items if n["id"] != nid]

            item = {
                "id":        nid,
                "type":      notif_type,
                "title":     title,
                "body":      body,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "read":      False,
                "data":      data,
            }
            self._items.insert(0, item)
            self._save()
            _log().info("Notification added", id=nid, type=notif_type)
            return item

    def dismiss(self, notif_id: str):
        """Mark a notification read by id (used for auto-dismiss on login)."""
        with self._mutex:
            for n in self._items:
                if n["id"] == notif_id:
                    n["read"] = True
                    break
            self._save()

    def all_items(self) -> list[dict]:
        with self._mutex:
            return list(self._items)

    def unread_count(self) -> int:
        with self._mutex:
            return sum(1 for n in self._items if not n.get("read", False))

    def mark_read(self, notif_id: str):
        self.dismiss(notif_id)

    def mark_all_read(self):
        with self._mutex:
            for n in self._items:
                n["read"] = True
            self._save()

    def clear_all(self):
        with self._mutex:
            self._items = []
            self._save()


# ── Singleton accessor ─────────────────────────────────────────────────────────

def get_notification_store() -> NotificationStore:
    """Convenience accessor for the singleton NotificationStore."""
    return NotificationStore.instance()


# ═══════════════════════════════════════════════════════════════════════════════
# Cooldown tracker
# ═══════════════════════════════════════════════════════════════════════════════

_last_fired: dict[str, float] = {}   # notif_id → epoch seconds
_cooldown_lock = threading.Lock()

def _check_cooldown(notif_id: str) -> bool:
    """Return True if enough time has passed to re-fire this notification."""
    with _cooldown_lock:
        last = _last_fired.get(notif_id, 0.0)
        now  = time.monotonic()
        if now - last < COOLDOWN_SECONDS:
            return False
        _last_fired[notif_id] = now
        return True

def _reset_cooldown(notif_id: str):
    """Reset cooldown for an id (call after successful login/recovery)."""
    with _cooldown_lock:
        _last_fired.pop(notif_id, None)


# ═══════════════════════════════════════════════════════════════════════════════
# Bell refresh — main-thread safe
# ═══════════════════════════════════════════════════════════════════════════════

def _refresh_bell():
    """
    Refresh the notification bell badge on the main thread.
    Safe to call from any thread — uses QMetaObject.invokeMethod with
    QueuedConnection so the actual widget touch always happens on the
    main thread.
    """
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QMetaObject, Qt

        app = QApplication.instance()
        if app is None:
            return

        for widget in app.topLevelWidgets():
            # MainWindow holds _page_objs["dashboard"]._notif_bell
            page_objs = getattr(widget, "_page_objs", None)
            if page_objs is None:
                continue
            dash = page_objs.get("dashboard")
            if dash is None:
                continue
            bell = getattr(dash, "_notif_bell", None)
            if bell is None:
                continue
            # Queue the call onto the main thread
            QMetaObject.invokeMethod(
                bell,
                "refresh_badge",
                Qt.ConnectionType.QueuedConnection,
            )
            return
    except Exception as e:
        _log().warning("_refresh_bell failed", error=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# Public helpers
# ═══════════════════════════════════════════════════════════════════════════════

def push_notification(
    title: str,
    body: str = "",
    notif_type: str = "info",
    notif_id: str | None = None,
    data: dict | None = None,
    cooldown: bool = True,
) -> bool:
    """
    One-call helper: add a notification + refresh the bell.

    Parameters
    ----------
    title       Short headline shown in the bell panel.
    body        Optional longer description.
    notif_type  "warning" | "info" | "update" | "friend_rec"
    notif_id    Stable string id for deduplication/dismissal.
                Auto-generated if None.
    data        Extra payload dict (passed through to store).
    cooldown    If True (default), respect COOLDOWN_SECONDS per id so
                repeated background failures don't spam notifications.

    Returns True if a new notification was actually added.
    """
    nid = notif_id or f"{notif_type}-{datetime.now(timezone.utc).timestamp():.0f}"

    if cooldown and notif_id is not None:
        # Only apply cooldown for stable ids — auto-generated ids are always unique
        if not _check_cooldown(nid):
            return False

    item = get_notification_store().add(
        notif_type=notif_type,
        title=title,
        body=body,
        data=data or {},
        notif_id=nid,
    )

    if item:
        _refresh_bell()
        return True
    return False


def dismiss_notification(notif_id: str):
    """
    Mark a notification as read by id and refresh the bell.
    Use this for auto-dismiss on recovery (e.g. successful login clears
    the 'not logged in' alert).
    """
    get_notification_store().dismiss(notif_id)
    _reset_cooldown(notif_id)
    _refresh_bell()
