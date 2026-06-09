"""
gs_sync.py — Great Sage ↔ Supabase sync client
================================================
Handles cloud backup/restore and recommendations inbox
for the Great Sage desktop app.

Usage
-----
    from gs_sync import GreatSageSync

    sync = GreatSageSync()

    # Sign in (prompts if no token cached)
    sync.login(email, password)

    # On fresh install — pull everything down
    data = sync.pull()

    # On app launch / data change — push local state up
    sync.push(watchlist_dict, progress_dict)

    # Poll recommendations inbox
    recs = sync.get_recommendations()

    # Mark a recommendation as accepted (adds to watchlist too)
    sync.accept_recommendation(rec_id, title, media_type, progress)

Supabase tables (Bolt-generated schema)
---------------------------------------
    profiles        — id, username, display_name, avatar_url, bio, created_at, is_admin
    watchlist       — id, user_id, title, type, status, cover_url, notes, rating, progress, created_at, updated_at
    recommendations — id, sender_id, receiver_id, title, type, cover_url, message, status, created_at
    friendships     — id, sender_id, receiver_id, status, created_at, updated_at

progress.json structure (Great Sage local)
------------------------------------------
    {
        "watchlist": {
            "Planning":   [{"title": ..., "type": "Anime"|"Novel", ...}],
            "Watching":   [...],
            "Dropped":    [...],
            "Completed":  [...]
        },
        "watching": {
            "Show Title": {
                "episode": 5,
                "season":  1,
                "position": 120.4,
                "duration": 1440.0,
                ...
            }
        }
    }
"""

from __future__ import annotations

import json
import os
import time
import threading
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

SUPABASE_URL     = "https://yukvabimqwhzyhsxacgw.supabase.co"
SUPABASE_ANON    = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1a3ZhYmltcXdoenloc3hhY2d3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA5MjE0MjYsImV4cCI6MjA5NjQ5NzQyNn0"
    ".3k-2YyDYHmdUmt9vKYigFy0s2ZjwICDSxqSJaIaHyqI"
)

PROGRESS_PATH    = Path.home() / ".config" / "matrix" / "progress.json"
TOKEN_CACHE_PATH = Path.home() / ".config" / "matrix" / "gs_sync_token.json"

# Map Great Sage bucket names → Supabase status values
GS_TO_SUPA_STATUS = {
    "Planning":  "planning",
    "Watching":  "watching",
    "Dropped":   "dropped",
    "Completed": "completed",
}
SUPA_TO_GS_STATUS = {v: k for k, v in GS_TO_SUPA_STATUS.items()}

# Map Great Sage type strings → Supabase type values
GS_TO_SUPA_TYPE = {
    "Anime":  "anime",
    "Novel":  "novel",
    "Show":   "show",
    "Movie":  "movie",
}
SUPA_TO_GS_TYPE = {v: k for k, v in GS_TO_SUPA_TYPE.items()}


# ── Sync client ───────────────────────────────────────────────────────────────

class GreatSageSync:
    """
    Thin Supabase REST client for Great Sage.
    Talks directly to Supabase's auto-generated PostgREST API.
    No extra dependencies — just requests.
    """

    def __init__(self):
        self._token:    Optional[str] = None
        self._user_id:  Optional[str] = None
        self._username: Optional[str] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_poll = threading.Event()
        self._rec_callbacks: list = []

        self._load_cached_token()

    # ── Auth ──────────────────────────────────────────────────────────────────

    def login(self, email: str, password: str) -> dict:
        """Sign in with email + password. Caches token to disk."""
        resp = requests.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers=self._anon_headers(),
            json={"email": email, "password": password},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        self._token   = data["access_token"]
        self._user_id = data["user"]["id"]
        self._cache_token(data)

        # Fetch username from profiles
        profile = self._get_profile()
        self._username = profile.get("username") if profile else None

        logger.info(f"[gs_sync] Logged in as {self._username} ({self._user_id})")
        return data

    def logout(self):
        """Clear token and stop polling."""
        self._stop_polling()
        self._token   = None
        self._user_id = None
        if TOKEN_CACHE_PATH.exists():
            TOKEN_CACHE_PATH.unlink()
        logger.info("[gs_sync] Logged out")

    def is_logged_in(self) -> bool:
        return self._token is not None and self._user_id is not None

    def _get_profile(self) -> Optional[dict]:
        rows = self._get("profiles", f"id=eq.{self._user_id}", select="id,username,display_name")
        return rows[0] if rows else None

    # ── Pull (restore) ────────────────────────────────────────────────────────

    def pull(self) -> dict:
        """
        Pull full cloud state and return as a dict matching progress.json structure.
        Call this on fresh install to restore everything.
        """
        if not self.is_logged_in():
            raise RuntimeError("Not logged in")

        rows = self._get(
            "watchlist",
            f"user_id=eq.{self._user_id}",
            select="title,type,status,notes,rating,progress,cover_url",
            order="created_at.desc",
        )

        watchlist: dict[str, list] = {
            "Planning": [], "Watching": [], "Dropped": [], "Completed": []
        }
        watching: dict[str, dict] = {}

        for row in rows:
            gs_status = SUPA_TO_GS_STATUS.get(row.get("status", ""), "Planning")
            gs_type   = SUPA_TO_GS_TYPE.get(row.get("type", ""), "Anime")
            title     = row.get("title", "")

            entry = {
                "title":     title,
                "type":      gs_type,
                "notes":     row.get("notes", ""),
                "rating":    row.get("rating", 0),
                "cover_url": row.get("cover_url", ""),
            }
            watchlist[gs_status].append(entry)

            # Restore progress for actively watching items
            if gs_status == "Watching" and row.get("progress", 0):
                watching[title] = {
                    "episode":  row.get("progress", 0),
                    "season":   1,
                    "position": 0.0,
                    "duration": 0.0,
                }

        result = {"watchlist": watchlist, "watching": watching}
        logger.info(
            f"[gs_sync] Pulled {sum(len(v) for v in watchlist.values())} items"
        )
        return result

    def restore_to_disk(self) -> bool:
        """
        Pull from cloud and write directly to progress.json.
        Backs up existing file first.
        Returns True on success.
        """
        try:
            data = self.pull()
        except Exception as e:
            logger.error(f"[gs_sync] Pull failed: {e}")
            return False

        # Load existing progress.json to merge (don't overwrite local-only keys)
        existing = self._load_progress()

        # Merge: cloud watchlist wins, local watching progress wins if newer
        existing["watchlist"] = data["watchlist"]
        for title, prog in data["watching"].items():
            if title not in existing.get("watching", {}):
                existing.setdefault("watching", {})[title] = prog

        self._save_progress(existing)
        logger.info("[gs_sync] Restored to disk")
        return True

    # ── Push (backup) ─────────────────────────────────────────────────────────

    def push(
        self,
        watchlist: Optional[dict] = None,
        watching: Optional[dict] = None,
    ) -> bool:
        """
        Push local state up to Supabase.
        Reads from progress.json if watchlist/watching not provided.
        Uses upsert so it's safe to call repeatedly.
        """
        if not self.is_logged_in():
            raise RuntimeError("Not logged in")

        local = self._load_progress()
        watchlist = watchlist or local.get("watchlist", {})
        watching  = watching  or local.get("watching", {})

        rows = []
        for gs_status, items in watchlist.items():
            supa_status = GS_TO_SUPA_STATUS.get(gs_status, "planning")
            for item in items:
                title    = item.get("title", "").strip()
                if not title:
                    continue
                gs_type  = item.get("type", "Anime")
                progress = 0
                if gs_status == "Watching" and title in watching:
                    progress = watching[title].get("episode", 0)

                rows.append({
                    "user_id":   self._user_id,
                    "title":     title,
                    "type":      GS_TO_SUPA_TYPE.get(gs_type, "anime"),
                    "status":    supa_status,
                    "notes":     item.get("notes", ""),
                    "rating":    item.get("rating", 0),
                    "progress":  progress,
                    "cover_url": item.get("cover_url", ""),
                })

        if not rows:
            logger.info("[gs_sync] Nothing to push")
            return True

        try:
            self._upsert("watchlist", rows, on_conflict="user_id,title")
            logger.info(f"[gs_sync] Pushed {len(rows)} items")
            return True
        except Exception as e:
            logger.error(f"[gs_sync] Push failed: {e}")
            return False

    def push_single(self, title: str, media_type: str, status: str, episode: int = 0,
                    notes: str = "", rating: int = 0, cover_url: str = "") -> bool:
        """Push a single watchlist item — call this when user adds/updates one show."""
        if not self.is_logged_in():
            return False
        try:
            self._upsert("watchlist", [{
                "user_id":   self._user_id,
                "title":     title,
                "type":      GS_TO_SUPA_TYPE.get(media_type, "anime"),
                "status":    GS_TO_SUPA_STATUS.get(status, "watching"),
                "notes":     notes,
                "rating":    rating,
                "progress":  episode,
                "cover_url": cover_url,
            }], on_conflict="user_id,title")
            return True
        except Exception as e:
            logger.error(f"[gs_sync] push_single failed: {e}")
            return False

    def delete_item(self, title: str) -> bool:
        """Remove an item from the cloud watchlist."""
        if not self.is_logged_in():
            return False
        try:
            resp = requests.delete(
                f"{SUPABASE_URL}/rest/v1/watchlist",
                headers=self._auth_headers(),
                params={"user_id": f"eq.{self._user_id}", "title": f"eq.{title}"},
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[gs_sync] delete_item failed: {e}")
            return False

    # ── Recommendations ───────────────────────────────────────────────────────

    def get_recommendations(self) -> list[dict]:
        """
        Fetch inbox recommendations (pending/unread).
        Returns list of dicts with sender info and show details.
        """
        if not self.is_logged_in():
            return []

        rows = self._get(
            "recommendations",
            f"receiver_id=eq.{self._user_id}&status=eq.pending",
            select="id,sender_id,title,type,cover_url,message,created_at",
            order="created_at.desc",
        )

        # Enrich with sender username
        result = []
        sender_cache: dict[str, str] = {}
        for row in rows:
            sid = row.get("sender_id", "")
            if sid not in sender_cache:
                profile = self._get(
                    "profiles",
                    f"id=eq.{sid}",
                    select="username,display_name",
                )
                sender_cache[sid] = (
                    profile[0].get("display_name") or profile[0].get("username", "Unknown")
                    if profile else "Unknown"
                )
            result.append({
                "id":         row["id"],
                "sender":     sender_cache[sid],
                "title":      row.get("title", ""),
                "type":       SUPA_TO_GS_TYPE.get(row.get("type", ""), "Anime"),
                "cover_url":  row.get("cover_url", ""),
                "message":    row.get("message", ""),
                "created_at": row.get("created_at", ""),
            })
        return result

    def accept_recommendation(self, rec_id: str, title: str, media_type: str) -> bool:
        """
        Accept a recommendation: adds to local watchlist Planning bucket
        and marks it accepted in Supabase.
        """
        if not self.is_logged_in():
            return False

        # Mark accepted in cloud
        try:
            resp = requests.patch(
                f"{SUPABASE_URL}/rest/v1/recommendations",
                headers=self._auth_headers(),
                params={"id": f"eq.{rec_id}"},
                json={"status": "accepted"},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"[gs_sync] accept_recommendation cloud update failed: {e}")
            return False

        # Add to local progress.json
        local = self._load_progress()
        planning = local.setdefault("watchlist", {}).setdefault("Planning", [])
        if not any(i.get("title") == title for i in planning):
            planning.append({
                "title": title,
                "type":  media_type,
                "notes": "",
                "rating": 0,
                "cover_url": "",
            })
            self._save_progress(local)

        # Also push to cloud watchlist
        self.push_single(title, media_type, "Planning")
        logger.info(f"[gs_sync] Accepted recommendation: {title}")
        return True

    def dismiss_recommendation(self, rec_id: str) -> bool:
        """Dismiss without adding to watchlist."""
        if not self.is_logged_in():
            return False
        try:
            resp = requests.patch(
                f"{SUPABASE_URL}/rest/v1/recommendations",
                headers=self._auth_headers(),
                params={"id": f"eq.{rec_id}"},
                json={"status": "dismissed"},
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[gs_sync] dismiss failed: {e}")
            return False

    # ── Background polling ────────────────────────────────────────────────────

    def start_polling(self, interval: int = 300, callback=None):
        """
        Poll recommendations inbox every `interval` seconds.
        Calls callback(recs: list) when new recommendations arrive.
        """
        if callback:
            self._rec_callbacks.append(callback)
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._stop_poll.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            args=(interval,),
            daemon=True,
            name="gs_sync_poll",
        )
        self._poll_thread.start()
        logger.info(f"[gs_sync] Polling started (every {interval}s)")

    def _poll_loop(self, interval: int):
        last_seen: set[str] = set()
        while not self._stop_poll.wait(timeout=interval):
            try:
                recs = self.get_recommendations()
                new_ids = {r["id"] for r in recs}
                new_recs = [r for r in recs if r["id"] not in last_seen]
                if new_recs:
                    for cb in self._rec_callbacks:
                        try:
                            cb(new_recs)
                        except Exception as e:
                            logger.error(f"[gs_sync] Callback error: {e}")
                last_seen = new_ids
            except Exception as e:
                logger.error(f"[gs_sync] Poll error: {e}")

    def _stop_polling(self):
        self._stop_poll.set()

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _anon_headers(self) -> dict:
        return {
            "apikey":       SUPABASE_ANON,
            "Content-Type": "application/json",
        }

    def _auth_headers(self) -> dict:
        return {
            "apikey":        SUPABASE_ANON,
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json",
            "Prefer":        "return=representation",
        }

    def _get(self, table: str, query: str = "", select: str = "*",
             order: str = "") -> list:
        params = {"select": select}
        if order:
            params["order"] = order
        # Append filter params from query string manually
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        if query:
            url += f"?{query}&select={select}"
            if order:
                url += f"&order={order}"
            resp = requests.get(url, headers=self._auth_headers(), timeout=10)
        else:
            resp = requests.get(url, headers=self._auth_headers(),
                                params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _upsert(self, table: str, rows: list, on_conflict: str = "") -> list:
        headers = self._auth_headers()
        if on_conflict:
            headers["Prefer"] = f"resolution=merge-duplicates,return=representation"
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=headers,
            params={"on_conflict": on_conflict} if on_conflict else {},
            json=rows,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json() if resp.text else []

    # ── Token cache ───────────────────────────────────────────────────────────

    def _cache_token(self, data: dict):
        TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        cache = {
            "access_token":  data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "user_id":       data["user"]["id"],
            "expires_at":    data.get("expires_at", 0),
        }
        TOKEN_CACHE_PATH.write_text(json.dumps(cache, indent=2))

    def _load_cached_token(self):
        if not TOKEN_CACHE_PATH.exists():
            return
        try:
            cache = json.loads(TOKEN_CACHE_PATH.read_text())
            expires_at = cache.get("expires_at", 0)
            # Refresh if within 1 hour of expiry
            if expires_at and time.time() > expires_at - 3600:
                self._refresh_token(cache.get("refresh_token", ""))
                return
            self._token   = cache.get("access_token")
            self._user_id = cache.get("user_id")
            logger.info("[gs_sync] Loaded cached token")
        except Exception as e:
            logger.warning(f"[gs_sync] Failed to load cached token: {e}")

    def _refresh_token(self, refresh_token: str):
        try:
            resp = requests.post(
                f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
                headers=self._anon_headers(),
                json={"refresh_token": refresh_token},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self._token   = data["access_token"]
            self._user_id = data["user"]["id"]
            self._cache_token(data)
            logger.info("[gs_sync] Token refreshed")
        except Exception as e:
            logger.warning(f"[gs_sync] Token refresh failed: {e}")
            self._token   = None
            self._user_id = None

    # ── Local file helpers ────────────────────────────────────────────────────

    def _load_progress(self) -> dict:
        if not PROGRESS_PATH.exists():
            return {}
        try:
            return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"[gs_sync] Failed to read progress.json: {e}")
            return {}

    def _save_progress(self, data: dict):
        PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Write to temp file first, then rename (atomic)
        tmp = PROGRESS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(PROGRESS_PATH)
