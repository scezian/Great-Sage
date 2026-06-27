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

import base64
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
# Map Great Sage bucket names → Supabase status values (website schema)
# Website statuses: watching, reading, completed, on_hold, dropped, plan_to_watch, plan_to_read
GS_TO_SUPA_STATUS = {
    "Watching":  "watching",
    "Dropped":   "dropped",
    "Completed": "completed",
    "Planning":  "plan_to_watch",
}
SUPA_TO_GS_STATUS = {
    "watching":      "watching",
    "reading":       "watching",
    "completed":     "completed",
    "on_hold":       "watching",
    "dropped":       "dropped",
    "plan_to_watch": "planning",
    "plan_to_read":  "planning",
}

# Map Great Sage type strings → Supabase type values (website schema)
# Website types: show, webnovel
GS_TO_SUPA_TYPE = {
    "Anime":    "show",
    "Show":     "show",
    "Movie":    "show",
    "Novel":    "webnovel",
    "Webnovel": "webnovel",
}
SUPA_TO_GS_TYPE = {
    "show":    "Anime",
    "webnovel":"Novel",
}


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

        # Shared session — reuses the connection pool so we never leak SSL
        # sockets. One session per GreatSageSync instance (singleton in practice).
        self._session = requests.Session()
        # In-memory token expiry — avoids reading TOKEN_CACHE_PATH on every request
        self._token_expires_at: float = 0.0

        self._load_cached_token()

    # ── Auth ──────────────────────────────────────────────────────────────────

    def login(self, email: str, password: str) -> dict:
        """Sign in with email + password. Caches token to disk."""
        resp = self._session.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers=self._anon_headers(),
            json={"email": email, "password": password},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        self._token   = data["access_token"]
        self._user_id = data["user"]["id"]
        self._cache_token(data, email=email, password=password)

        # Fetch username from profiles
        profile = self._get_profile()
        self._username = profile.get("username") if profile else None

        logger.info(f"[gs_sync] Logged in as {self._username} ({self._user_id})")
        return data

    def logout(self):
        """Clear token and stop polling."""
        self._stop_polling()
        self._token            = None
        self._user_id          = None
        self._token_expires_at = 0.0
        if TOKEN_CACHE_PATH.exists():
            TOKEN_CACHE_PATH.unlink()
        logger.info("[gs_sync] Logged out")

    def close(self):
        """Close the shared HTTP session. Call on app exit."""
        self._stop_polling()
        try:
            self._session.close()
        except Exception:
            pass

    def is_logged_in(self) -> bool:
        return self._token is not None and self._user_id is not None

    def _get_profile(self) -> Optional[dict]:
        try:
            rows = self._get("profiles", f"id=eq.{self._user_id}", select="id,username,display_name")
            return rows[0] if rows else None
        except Exception as e:
            logger.warning(f"[gs_sync] _get_profile failed: {e}")
            return None

    # ── Pull (restore) ────────────────────────────────────────────────────────

    def pull(self) -> dict:
        """
        Pull full cloud state and return as a dict matching progress.json structure.
        Includes updated_at per entry so restore_to_disk can do last-write-wins merging.
        """
        if not self.is_logged_in():
            raise RuntimeError("Not logged in")

        rows = self._get(
            "watchlist",
            f"user_id=eq.{self._user_id}",
            select="title,type,status,notes,rating,progress,cover_url,updated_at",
            order="created_at.desc",
        )

        # Keys must be lowercase — great_sage_core.get_matrix_data() only
        # recognises lowercase bucket names ("planning", "watching", etc.).
        watchlist: dict[str, list] = {
            "planning": [], "watching": [], "dropped": [], "completed": []
        }
        watching: dict[str, dict] = {}

        for row in rows:
            gs_status = SUPA_TO_GS_STATUS.get(row.get("status", ""), "planning")
            gs_type   = SUPA_TO_GS_TYPE.get(row.get("type", ""), "Anime")
            title     = row.get("title", "")

            entry = {
                "title":      title,
                "type":       gs_type,
                "notes":      row.get("notes", ""),
                "rating":     row.get("rating", 0),
                "cover_url":  row.get("cover_url", ""),
                "updated_at": row.get("updated_at", ""),
            }
            watchlist[gs_status].append(entry)

            # Restore progress for actively watching items
            if gs_status == "watching" and row.get("progress", 0):
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
        Merge cloud state into local progress.json using last-write-wins per title.

        For each title, whichever side has the more recent updated_at timestamp
        wins — both the bucket (status) and the entry data. Titles that only
        exist on one side are always included. This means:
        - Items added on the website appear in the app on the next sync cycle.
        - Items moved to a different status on the website update in the app.
        - Local changes are never silently discarded if they're newer.

        Returns True on success.
        """
        try:
            data = self.pull()
        except Exception as e:
            logger.error(f"[gs_sync] Pull failed: {e}")
            return False

        existing = self._load_progress()
        local_wl  = existing.get("watchlist", {})

        # Build a flat index of local entries: title.lower() → (bucket, entry)
        local_index: dict[str, tuple[str, dict]] = {}
        for bucket, items in local_wl.items():
            for item in items:
                if not isinstance(item, dict):
                    continue
                t = item.get("title", "").strip().lower()
                if t:
                    local_index[t] = (bucket, item)

        # Build a flat index of cloud entries: title.lower() → (bucket, entry)
        cloud_wl = data.get("watchlist", {})
        cloud_index: dict[str, tuple[str, dict]] = {}
        for bucket, items in cloud_wl.items():
            for item in items:
                if not isinstance(item, dict):
                    continue
                t = item.get("title", "").strip().lower()
                if t:
                    cloud_index[t] = (bucket, item)

        # Last-write-wins merge
        merged: dict[str, list] = {
            "planning": [], "watching": [], "dropped": [], "completed": []
        }
        all_titles = set(local_index) | set(cloud_index)

        for title_key in all_titles:
            in_local = title_key in local_index
            in_cloud = title_key in cloud_index

            if in_local and not in_cloud:
                # Local only — always keep
                bucket, entry = local_index[title_key]
                merged.setdefault(bucket, []).append(entry)

            elif in_cloud and not in_local:
                # Cloud only — always add (this is the website-add case)
                bucket, entry = cloud_index[title_key]
                merged.setdefault(bucket, []).append(entry)

            else:
                # Both sides have it — last-write-wins by updated_at
                local_bucket, local_entry = local_index[title_key]
                cloud_bucket, cloud_entry = cloud_index[title_key]
                local_ts = local_entry.get("updated_at", "")
                cloud_ts = cloud_entry.get("updated_at", "")

                if cloud_ts and cloud_ts > local_ts:
                    # Cloud is newer — use cloud bucket and data, but preserve
                    # local-only fields (file_path, is_anime, added timestamp)
                    merged_entry = {**local_entry, **cloud_entry}
                    merged.setdefault(cloud_bucket, []).append(merged_entry)
                else:
                    # Local is newer or equal — keep local
                    merged.setdefault(local_bucket, []).append(local_entry)

        existing["watchlist"] = merged

        # Merge watching progress: local wins (has position/duration), cloud fills gaps
        for title, prog in data.get("watching", {}).items():
            if title not in existing.get("watching", {}):
                existing.setdefault("watching", {})[title] = prog

        self._save_progress(existing)
        added   = len(set(cloud_index) - set(local_index))
        updated = sum(
            1 for t in set(cloud_index) & set(local_index)
            if cloud_index[t][1].get("updated_at", "") > local_index[t][1].get("updated_at", "")
        )
        logger.info(
            f"[gs_sync] Sync complete — {added} new from cloud, {updated} updated from cloud"
        )
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
        for gs_status_raw, items in watchlist.items():
            # Normalise to lowercase so push works regardless of whether the
            # user's progress.json has capitalised or lowercase bucket names.
            gs_status = gs_status_raw.lower().capitalize()  # "planning" → "Planning", "Planning" → "Planning"
            for item in items:
                title    = item.get("title", "").strip()
                if not title:
                    continue
                gs_type   = item.get("type", "Anime")
                supa_type = GS_TO_SUPA_TYPE.get(gs_type, "show")
                is_novel  = supa_type == "webnovel"

                # Resolve status with type awareness
                if gs_status == "Watching":
                    supa_status = "reading" if is_novel else "watching"
                elif gs_status == "Planning":
                    supa_status = "plan_to_read" if is_novel else "plan_to_watch"
                else:
                    supa_status = GS_TO_SUPA_STATUS.get(gs_status, "plan_to_watch")

                progress = 0
                if gs_status == "Watching" and title in watching:
                    prog_info = watching[title]
                    progress = prog_info.get("current_episode") or prog_info.get("episode") or 0

                from datetime import datetime, timezone as _tz
                rows.append({
                    "user_id":    self._user_id,
                    "title":      title,
                    "type":       supa_type,
                    "status":     supa_status,
                    "notes":      item.get("notes", ""),
                    "rating":     item.get("rating") or None,
                    "progress":   progress,
                    "cover_url":  item.get("cover_url", ""),
                    "updated_at": datetime.now(_tz.utc).isoformat(),
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
                "rating":    rating or None,
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
            resp = self._session.delete(
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
            resp = self._session.patch(
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
        planning = local.setdefault("watchlist", {}).setdefault("planning", [])
        if not any(i.get("title") == title for i in planning):
            planning.append({
                "title":      title,
                "type":       media_type,
                "notes":      "",
                "rating":     0,
                "cover_url":  "",
                "updated_at": datetime.now(timezone.utc).isoformat(),
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
            resp = self._session.patch(
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

    def start_polling(self, interval: int = 300, callback=None,
                      watchlist_callback=None):
        """
        Poll recommendations inbox AND pull watchlist every `interval` seconds.

        - callback(recs)           called when new recommendations arrive
        - watchlist_callback()     called after each successful restore_to_disk()
                                   so the UI can refresh itself

        Fires an immediate first check so pending recs and any TrackFlix
        additions surface on launch rather than waiting the full interval.
        """
        if callback and callback not in self._rec_callbacks:
            self._rec_callbacks.append(callback)
        self._watchlist_callback = watchlist_callback
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

        def _check():
            # ── Pull watchlist from cloud (TrackFlix → Great Sage) ──────────
            try:
                ok = self.restore_to_disk()
                if ok:
                    cb = getattr(self, "_watchlist_callback", None)
                    if cb:
                        try:
                            cb()
                        except Exception as e:
                            logger.error(f"[gs_sync] Watchlist callback error: {e}")
            except Exception as e:
                logger.error(f"[gs_sync] Watchlist pull error: {e}")

            # ── Poll recommendations inbox ───────────────────────────────────
            try:
                recs = self.get_recommendations()
                new_ids = {r["id"] for r in recs}
                new_recs = [r for r in recs if r["id"] not in last_seen]
                if new_recs:
                    for cb in self._rec_callbacks:
                        try:
                            cb(new_recs)
                        except Exception as e:
                            logger.error(f"[gs_sync] Rec callback error: {e}")
                last_seen.clear()
                last_seen.update(new_ids)
            except Exception as e:
                logger.error(f"[gs_sync] Rec poll error: {e}")

        # Immediate first check — don't make the user wait a full interval
        # before pending recommendations or TrackFlix additions surface.
        _check()

        while not self._stop_poll.wait(timeout=interval):
            _check()

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
        self._ensure_fresh_token()
        params = {"select": select}
        if order:
            params["order"] = order
        # Append filter params from query string manually
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        if query:
            url += f"?{query}&select={select}"
            if order:
                url += f"&order={order}"
            resp = self._session.get(url, headers=self._auth_headers(), timeout=10)
        else:
            resp = self._session.get(url, headers=self._auth_headers(),
                                params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _upsert(self, table: str, rows: list, on_conflict: str = "") -> list:
        """
        Upsert rows using PostgREST's native merge-duplicates resolution.

        Previously this did a DELETE then INSERT which was destructive: if the
        INSERT failed (e.g. 409 conflict, network drop) the table was left empty.
        Now we use a single POST with Prefer: resolution=merge-duplicates so the
        operation is atomic and safe to retry.
        """
        self._ensure_fresh_token()
        if not rows:
            return []

        headers = self._auth_headers()
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"

        params = {}
        if on_conflict:
            params["on_conflict"] = on_conflict

        resp = self._session.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=headers,
            params=params,
            json=rows,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json() if resp.text else []

    # ── Token cache ───────────────────────────────────────────────────────────

    def _cache_token(self, data: dict, email: str = "", password: str = ""):
        TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if TOKEN_CACHE_PATH.exists():
            try:
                existing = json.loads(TOKEN_CACHE_PATH.read_text())
            except Exception:
                pass
        cache = {
            "access_token":  data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "user_id":       data["user"]["id"],
            "expires_at":    data.get("expires_at", 0),
            # Preserve stored credentials if not provided
            "email":         email or existing.get("email", ""),
            "password":      base64.b64encode(password.encode()).decode() if password
                             else existing.get("password", ""),
        }
        TOKEN_CACHE_PATH.write_text(json.dumps(cache, indent=2))
        # Keep in-memory expiry in sync so _ensure_fresh_token avoids disk reads
        self._token_expires_at = float(data.get("expires_at", 0))

    def _load_cached_token(self):
        if not TOKEN_CACHE_PATH.exists():
            return
        try:
            cache = json.loads(TOKEN_CACHE_PATH.read_text())
            # Always load cached token first so user is never silently logged out
            self._token            = cache.get("access_token")
            self._user_id          = cache.get("user_id")
            self._token_expires_at = float(cache.get("expires_at", 0))
            # Try to refresh if expired or within 5 minutes of expiry
            if self._token_expires_at and time.time() > self._token_expires_at - 300:
                self._refresh_token(cache.get("refresh_token", ""))
            else:
                logger.info("[gs_sync] Loaded cached token")
        except Exception as e:
            logger.warning(f"[gs_sync] Failed to load cached token: {e}")

    def _ensure_fresh_token(self):
        """
        Called before every HTTP request. Refreshes the access token if it has
        expired or is within 5 minutes of expiry. Uses in-memory expiry so no
        file is opened on every call.
        """
        if not self._token_expires_at:
            return
        if time.time() > self._token_expires_at - 300:
            logger.info("[gs_sync] Token near/past expiry — refreshing mid-session")
            try:
                cache = json.loads(TOKEN_CACHE_PATH.read_text())
                self._refresh_token(cache.get("refresh_token", ""))
            except Exception as e:
                logger.warning(f"[gs_sync] _ensure_fresh_token error: {e}")

    def _refresh_token(self, refresh_token: str):
        # Pre-load stored credentials before anything else so they're always
        # available in both the success and failure branches below.
        email = ""
        password = ""
        try:
            cache = json.loads(TOKEN_CACHE_PATH.read_text())
            email    = cache.get("email", "")
            raw_pw   = cache.get("password", "")
            password = base64.b64decode(raw_pw).decode() if raw_pw else ""
        except Exception:
            pass

        try:
            resp = self._session.post(
                f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
                headers=self._anon_headers(),
                json={"refresh_token": refresh_token},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self._token   = data["access_token"]
            self._user_id = data["user"]["id"]
            self._cache_token(data, email=email, password=password)
            logger.info("[gs_sync] Token refreshed")
        except Exception as e:
            logger.warning(f"[gs_sync] Token refresh failed: {e}")
            # Try silent re-login with stored credentials
            if email and password:
                try:
                    self.login(email, password)
                    logger.info("[gs_sync] Re-logged in silently with stored credentials")
                    return
                except Exception as re_err:
                    logger.warning(f"[gs_sync] Silent re-login failed: {re_err}")
            # Give up — clear cache and require manual sign-in
            self._token            = None
            self._user_id          = None
            self._token_expires_at = 0.0
            try:
                TOKEN_CACHE_PATH.unlink(missing_ok=True)
            except Exception:
                pass

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
