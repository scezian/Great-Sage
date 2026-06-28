"""
gs_legion_sync.py — Legion ↔ Supabase sync
============================================
Handles push (backup) and pull (restore) of Legion library data.

Push flow
---------
Called from gs_legion_ui.py whenever:
  - A book is added/moved to a library category  (library_add)
  - A book is removed from the library           (library_remove)
  - Reader saves reading position                (ReaderPanel._save_progress)

Pull / restore flow
-------------------
Called once on fresh install from gs_settings_ui or on demand.
For each webnovel entry pulled from Supabase:
  1. Search Discovery (RR / FWN / Gutenberg) by exact title + source
  2. Call library_add(book, category)
  3. If category == "reading": call jump_in_add(book)
  4. Seed reader_url + last_downloaded_url into LEGION_PROGRESS so the
     download worker resumes from the saved chapter, not chapter 1.

Supabase schema used
--------------------
  watchlist table:
    title, type="webnovel", status, cover_url, progress (chapter num),
    metadata JSONB = {
        "source":      "royalroad"|"libread"|"gutenberg",
        "reader_url":  "https://freewebnovel.com/novel/slug/chapter-47",
        "book_url":    "https://freewebnovel.com/novel/slug",  (landing page)
    }
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("great_sage.legion_sync")

# ── Legion category ↔ Supabase status ────────────────────────────────────────

_LEGION_TO_SUPA_STATUS = {
    "planning":  "plan_to_read",
    "reading":   "reading",
    "dropped":   "dropped",
    "completed": "completed",
}

_SUPA_TO_LEGION_CAT = {
    "plan_to_read": "planning",
    "reading":      "reading",
    "dropped":      "dropped",
    "completed":    "completed",
    # fallbacks for items that may have been set via the website
    "plan_to_watch": "planning",
    "watching":      "reading",
}


# ── Push helpers ──────────────────────────────────────────────────────────────

def push_book(title: str, category: str, cover_url: str = "",
              book_url: str = "", source: str = "",
              reader_url: str = "", chapter_num: int = 0) -> None:
    """
    Push a single Legion book to Supabase in a daemon thread.
    Safe to call from any thread — never blocks the UI.
    """
    def _do():
        try:
            from gs_sync import GreatSageSync
            s = GreatSageSync()
            if not s.is_logged_in():
                return

            supa_status = _LEGION_TO_SUPA_STATUS.get(category, "plan_to_read")
            metadata    = {
                "source":     source,
                "reader_url": reader_url,
                "book_url":   book_url,
            }

            # Use _upsert directly so we can include the metadata column,
            # which push_single doesn't expose.
            s._upsert("watchlist", [{
                "user_id":    s._user_id,
                "title":      title,
                "type":       "webnovel",
                "status":     supa_status,
                "cover_url":  cover_url,
                "progress":   chapter_num,
                "notes":      "",
                "rating":     None,
                "metadata":   metadata,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }], on_conflict="user_id,title")

            log.info(f"[legion_sync] pushed '{title}' → {supa_status}")
        except Exception as e:
            log.warning(f"[legion_sync] push_book failed for '{title}': {e}")

    threading.Thread(target=_do, daemon=True, name="legion_sync_push").start()


def delete_book(title: str) -> None:
    """Delete a book from the cloud watchlist in a daemon thread."""
    def _do():
        try:
            from gs_sync import GreatSageSync
            s = GreatSageSync()
            if not s.is_logged_in():
                return
            s.delete_item(title)
            log.info(f"[legion_sync] deleted '{title}'")
        except Exception as e:
            log.warning(f"[legion_sync] delete_book failed for '{title}': {e}")

    threading.Thread(target=_do, daemon=True, name="legion_sync_delete").start()


def push_reader_progress(title: str, reader_url: str,
                          book_url: str = "", source: str = "",
                          cover_url: str = "", chapter_num: int = 0) -> None:
    """
    Update reader_url in Supabase metadata when reading position changes.
    Only updates metadata + progress — does not change status or category.
    """
    def _do():
        try:
            from gs_sync import GreatSageSync, SUPABASE_URL, SUPABASE_ANON
            s = GreatSageSync()
            if not s.is_logged_in():
                return

            # Fetch existing row so we don't wipe status/category
            rows = s._get(
                "watchlist",
                f"user_id=eq.{s._user_id}&title=eq.{title}",
                select="status,metadata",
            )

            existing_meta = {}
            existing_status = "reading"
            if rows:
                existing_status = rows[0].get("status", "reading")
                existing_meta   = rows[0].get("metadata") or {}

            existing_meta.update({
                "reader_url": reader_url,
                "book_url":   book_url or existing_meta.get("book_url", ""),
                "source":     source   or existing_meta.get("source", ""),
            })

            s._upsert("watchlist", [{
                "user_id":    s._user_id,
                "title":      title,
                "type":       "webnovel",
                "status":     existing_status,
                "cover_url":  cover_url,
                "progress":   chapter_num,
                "metadata":   existing_meta,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }], on_conflict="user_id,title")

            log.info(f"[legion_sync] progress updated '{title}' → ch {chapter_num}")
        except Exception as e:
            log.warning(f"[legion_sync] push_reader_progress failed for '{title}': {e}")

    threading.Thread(target=_do, daemon=True, name="legion_sync_progress").start()


# ── Pull / restore ────────────────────────────────────────────────────────────

def legion_restore_to_disk() -> bool:
    """
    Pull webnovel entries from Supabase and merge into LEGION_BOOKMARKS +
    LEGION_PROGRESS using last-write-wins per title — exactly like
    GreatSageSync.restore_to_disk() does for Matrix.

    Safe to call every 3 minutes in the sync cycle:
    - Books already in local library are never re-added blindly
    - Cloud-only books are added to LEGION_BOOKMARKS directly (no Discovery search)
    - If cloud timestamp is newer, local entry is updated
    - For newly added reading books, LEGION_PROGRESS is seeded with reader_url
      and last_downloaded_url so the download worker resumes from the right chapter

    Returns True on success.
    """
    try:
        from gs_sync import GreatSageSync
        from great_sage_core import (
            get_bookmarks_data, load_json_cached, save_json,
            LEGION_BOOKMARKS, LEGION_PROGRESS,
        )

        s = GreatSageSync()
        if not s.is_logged_in():
            return False

        # Pull webnovel rows from Supabase
        rows = s._get(
            "watchlist",
            f"user_id=eq.{s._user_id}&type=eq.webnovel",
            select="title,status,cover_url,progress,metadata,updated_at",
        )

        if not rows:
            return True

        # Build cloud index: title.lower() → (category, entry dict, metadata)
        cloud_index: dict[str, tuple[str, dict, dict]] = {}
        for row in rows:
            title = row.get("title", "").strip()
            if not title:
                continue
            status   = row.get("status", "plan_to_read")
            category = _SUPA_TO_LEGION_CAT.get(status, "planning")
            meta     = row.get("metadata") or {}
            entry    = {
                "title":      title,
                "author":     "",
                "cover_url":  row.get("cover_url", ""),
                "url":        meta.get("book_url", ""),
                "source":     meta.get("source", ""),
                "synopsis":   "",
                "updated_at": row.get("updated_at", ""),
            }
            cloud_index[title.lower()] = (category, entry, meta)

        # Build local index: title.lower() → (category, entry dict)
        lib_data = get_bookmarks_data()
        local_index: dict[str, tuple[str, dict]] = {}
        for cat in ("planning", "reading", "dropped", "completed"):
            for e in lib_data.get(cat, []):
                if not isinstance(e, dict):
                    continue
                t = e.get("title", "").strip().lower()
                if t:
                    local_index[t] = (cat, e)

        # Last-write-wins merge into LEGION_BOOKMARKS
        merged: dict[str, list] = {
            "planning": [], "reading": [], "dropped": [], "completed": []
        }
        all_titles = set(local_index) | set(cloud_index)
        newly_reading: list[tuple[str, dict, dict]] = []  # (title, entry, meta)

        for title_key in all_titles:
            in_local = title_key in local_index
            in_cloud = title_key in cloud_index

            if in_local and not in_cloud:
                # Local only — keep as-is
                cat, entry = local_index[title_key]
                merged.setdefault(cat, []).append(entry)

            elif in_cloud and not in_local:
                # Cloud only — add to library
                cat, entry, meta = cloud_index[title_key]
                merged.setdefault(cat, []).append(entry)
                if cat == "reading":
                    newly_reading.append((entry["title"], entry, meta))
                log.info(f"[legion_restore] added from cloud: '{entry['title']}' → {cat}")

            else:
                # Both — last-write-wins by updated_at
                local_cat, local_entry = local_index[title_key]
                cloud_cat, cloud_entry, meta = cloud_index[title_key]
                local_ts = local_entry.get("updated_at", "")
                cloud_ts = cloud_entry.get("updated_at", "")

                if cloud_ts and cloud_ts > local_ts:
                    # Cloud is newer — merge, preserving local-only fields
                    merged_entry = {**local_entry, **cloud_entry}
                    merged.setdefault(cloud_cat, []).append(merged_entry)
                    if cloud_cat == "reading" and local_cat != "reading":
                        newly_reading.append((merged_entry["title"], merged_entry, meta))
                else:
                    # Local is newer or equal — keep local
                    merged.setdefault(local_cat, []).append(local_entry)

        # Write merged bookmarks to disk
        save_json(LEGION_BOOKMARKS, merged)

        # Seed LEGION_PROGRESS for newly added reading books
        if newly_reading:
            prog_data = load_json_cached(LEGION_PROGRESS, {"books": {}})
            prog_books = prog_data.setdefault("books", {})
            changed = False
            for title, entry, meta in newly_reading:
                reader_url  = meta.get("reader_url", "")
                book_url    = meta.get("book_url", "") or entry.get("url", "")
                source      = meta.get("source", "") or entry.get("source", "")
                import re as _re
                _m = _re.search(r"/chapter-(\d+)", reader_url)
                chapter_num = int(_m.group(1)) if _m else 0

                if title not in prog_books:
                    # Fresh entry — create full progress record
                    prog_books[title] = {
                        "title":                   title,
                        "author":                  entry.get("author", ""),
                        "cover_url":               entry.get("cover_url", ""),
                        "url":                     book_url,
                        "source":                  source,
                        "synopsis":                entry.get("synopsis", ""),
                        "chapters_read":           chapter_num,
                        "current_chapter":         chapter_num,
                        "last_read":               0,
                        "last_downloaded_chapter": chapter_num,
                        "last_downloaded_url":     reader_url,
                        "reader_url":              reader_url,
                        "reader_chapter":          f"Chapter {chapter_num}" if chapter_num else "",
                        "download_state": {
                            "status":                      "idle",
                            "total_chapters_downloaded":   chapter_num,
                            "last_downloaded_chapter":     None,
                            "last_downloaded_chapter_num": chapter_num,
                            "download_path":               None,
                            "failed_chapters":             [],
                            "timestamp":                   0,
                            "pause_requested":             False,
                        },
                    }
                    changed = True
                    log.info(f"[legion_restore] seeded progress for '{title}' → ch {chapter_num}")
                else:
                    # Entry exists — only update reader_url if cloud has one and local doesn't
                    existing = prog_books[title]
                    if reader_url and not existing.get("reader_url"):
                        existing["reader_url"]           = reader_url
                        existing["last_downloaded_url"]  = reader_url
                        existing["last_downloaded_chapter"] = chapter_num
                        changed = True

            if changed:
                save_json(LEGION_PROGRESS, prog_data)

        added   = sum(1 for t in cloud_index if t not in local_index)
        updated = sum(
            1 for t in cloud_index
            if t in local_index
            and cloud_index[t][1].get("updated_at", "") > local_index[t][1].get("updated_at", "")
        )
        log.info(f"[legion_restore] done — {added} added, {updated} updated from cloud")
        return True

    except Exception as e:
        log.error(f"[legion_restore] failed: {e}")
        return False


class LegionRestoreWorker:
    """Runs legion_restore_to_disk() in a daemon thread. Drop-in for the old worker."""

    def __init__(self, on_progress=None, on_done=None, on_error=None):
        self._on_progress = on_progress or (lambda msg: None)
        self._on_done     = on_done     or (lambda restored, skipped: None)
        self._on_error    = on_error    or (lambda msg: None)

    def start(self):
        def _run():
            ok = legion_restore_to_disk()
            if ok:
                self._on_done(0, 0)
            else:
                self._on_error("Legion restore failed — check logs.")
        threading.Thread(target=_run, daemon=True, name="legion_restore").start()


# ── One-time backfill ─────────────────────────────────────────────────────────

def backfill_library(on_progress=None, on_done=None, on_error=None):
    """
    Push all books currently in LEGION_BOOKMARKS to Supabase.
    Reads reader_url and last_downloaded_chapter from LEGION_PROGRESS per book.
    Safe to call multiple times — uses upsert.

    on_progress(msg: str)         — called for each book pushed
    on_done(pushed, skipped: int) — called on completion
    on_error(msg: str)            — called on fatal error
    """
    _on_progress = on_progress or (lambda msg: None)
    _on_done     = on_done     or (lambda p, s: None)
    _on_error    = on_error    or (lambda msg: None)

    def _run():
        try:
            from gs_sync import GreatSageSync
            from great_sage_core import get_bookmarks_data, load_json_cached, LEGION_PROGRESS
            from datetime import datetime, timezone

            s = GreatSageSync()
            if not s.is_logged_in():
                _on_error("Not logged in — cannot backfill.")
                return

            lib_data  = get_bookmarks_data()
            prog_data = load_json_cached(LEGION_PROGRESS, {"books": {}})
            books_prog = prog_data.get("books", {})

            pushed  = 0
            skipped = 0
            rows    = []

            cat_map = {
                "planning":  "plan_to_read",
                "reading":   "reading",
                "dropped":   "dropped",
                "completed": "completed",
            }

            for category, entries in lib_data.items():
                if category not in cat_map:
                    continue
                supa_status = cat_map[category]
                for entry in entries:
                    if not isinstance(entry, dict):
                        skipped += 1
                        continue
                    title = entry.get("title", "").strip()
                    if not title:
                        skipped += 1
                        continue

                    prog    = books_prog.get(title, {})
                    reader_url  = prog.get("reader_url", "")
                    book_url    = prog.get("url", "") or entry.get("url", "")
                    source      = prog.get("source", "") or entry.get("source", "")
                    cover_url   = entry.get("cover_url", "")
                    import re as _re
                    _m = _re.search(r"/chapter-(\d+)", reader_url)
                    chapter_num = int(_m.group(1)) if _m else 0

                    metadata = {
                        "source":     source,
                        "reader_url": reader_url,
                        "book_url":   book_url,
                    }

                    rows.append({
                        "user_id":    s._user_id,
                        "title":      title,
                        "type":       "webnovel",
                        "status":     supa_status,
                        "cover_url":  cover_url,
                        "progress":   chapter_num,
                        "notes":      "",
                        "rating":     None,
                        "metadata":   metadata,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })
                    _on_progress(f"Queued '{title}' ({category})")
                    pushed += 1

            if rows:
                s._upsert("watchlist", rows, on_conflict="user_id,title")
                log.info(f"[legion_sync] backfill complete — {pushed} books pushed")
            _on_done(pushed, skipped)

        except Exception as e:
            log.error(f"[legion_sync] backfill failed: {e}")
            _on_error(str(e))

    threading.Thread(target=_run, daemon=True, name="legion_sync_backfill").start()
