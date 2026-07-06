"""
gs_sync_scheduler.py
─────────────────────
Fix for the root cause behind Great Sage sync "not sticking": there used to
be TWO independent autosync loops running at once —

  1. gs_matrix_ui.py's MatrixPage spawned its own daemon thread on init that
     called sync.restore_to_disk() immediately, then every 300s forever.
  2. gs_settings_ui.py started a QTimer every 180s calling _sync_cycle(),
     which did restore_to_disk() -> legion_restore_to_disk() ->
     drain_pending_pushes() -> push().

Neither loop knew the other existed. Even with every individual file write
correctly locked, two independent read-merge-write cycles racing on the same
cloud table and the same local files is a classic lost-update: loop A reads
state X and starts merging; loop B reads the same state X before A writes;
whichever writes last silently discards the other's merge decision.

This module is the ONE place that owns the periodic sync cycle. Every page
that wants to react to a completed pull (to refresh its view) registers a
callback instead of running its own loop.

Usage:
    from gs_sync_scheduler import sync_scheduler
    sync_scheduler.register_pull_listener(self._on_cloud_pull_complete)
    sync_scheduler.start()   # safe to call from multiple pages — starts once
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, List

logger = logging.getLogger("great_sage.sync_scheduler")

# How often the full sync cycle runs. Matches the old settings-page interval
# (3 min) since that was the tighter of the two previous loops.
SYNC_INTERVAL_SECONDS = 3 * 60


class SyncScheduler:
    """
    Single shared owner of the periodic Matrix + Legion sync cycle.
    Thread-safe singleton — use the module-level `sync_scheduler` instance.
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._listeners: List[Callable[[], None]] = []
        self._listeners_lock = threading.Lock()
        self._started = False
        self._start_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # Fix: a single lock around "run one sync cycle" ensures that even if
        # something calls run_once() manually (e.g. a "Sync now" button) at
        # the same moment the background loop is about to tick, the two
        # don't overlap and race each other.
        self._cycle_lock = threading.Lock()

    @classmethod
    def get(cls) -> "SyncScheduler":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def register_pull_listener(self, callback: Callable[[], None]):
        """
        Register a callback to be invoked (on whatever thread the sync ran
        on — callers should marshal to the Qt main thread themselves, e.g.
        via QTimer.singleShot(0, callback)) after each completed sync cycle.
        """
        with self._listeners_lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def unregister_pull_listener(self, callback: Callable[[], None]):
        with self._listeners_lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def start(self):
        """
        Start the background loop. Idempotent — safe for every page that
        wants sync to be running to call this in its __init__; only the
        first call actually spawns the thread.
        """
        with self._start_lock:
            if self._started:
                return
            self._started = True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="gs_sync_scheduler"
            )
            self._thread.start()
            logger.info("[gs_sync_scheduler] Started single shared sync loop")

    def stop(self):
        self._stop_event.set()

    def _loop(self):
        # Run once immediately on start (equivalent to the old "pull on
        # launch" behavior), then on a fixed interval.
        self.run_once()
        while not self._stop_event.wait(SYNC_INTERVAL_SECONDS):
            self.run_once()

    def run_once(self):
        """Run exactly one sync cycle. Safe to call from a UI 'Sync now' action."""
        if not self._cycle_lock.acquire(blocking=False):
            logger.info("[gs_sync_scheduler] Cycle already running — skipping")
            return
        try:
            self._do_cycle()
        finally:
            self._cycle_lock.release()

    def _do_cycle(self):
        try:
            from gs_sync import GreatSageSync
            sync = GreatSageSync.get()
            if not sync.is_logged_in():
                return

            sync.restore_to_disk()

            try:
                from gs_legion_sync import legion_restore_to_disk, drain_pending_pushes
                legion_restore_to_disk()
                drain_pending_pushes()
            except Exception as e:
                logger.warning(f"[gs_sync_scheduler] Legion sync step failed: {e}")

            sync.push()
        except Exception as e:
            logger.error(f"[gs_sync_scheduler] Sync cycle failed: {e}")
            return

        with self._listeners_lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb()
            except Exception as e:
                logger.warning(f"[gs_sync_scheduler] Listener callback failed: {e}")


# Module-level singleton — this is what every page should import and use.
sync_scheduler = SyncScheduler.get()
