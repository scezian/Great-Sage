#!/usr/bin/env python3
"""
legion_backfill.py — One-time push of existing Legion library to Supabase.

Run from the Great Sage project directory:
    cd ~/Projects/Great-Sage
    python legion_backfill.py

This reads LEGION_BOOKMARKS and LEGION_PROGRESS and upserts every book
into the Supabase watchlist table as type=webnovel.
Safe to run multiple times — uses upsert (no duplicates).
"""

import sys
import os

# Must be run from the Great Sage project directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def main():
    print("Legion Backfill — pushing library to Supabase...")

    try:
        from gs_sync import GreatSageSync
    except ImportError as e:
        print(f"ERROR: Could not import gs_sync: {e}")
        print("Make sure you're running this from the Great Sage project directory.")
        sys.exit(1)

    try:
        from great_sage_core import get_bookmarks_data, load_json_cached, LEGION_PROGRESS
    except ImportError as e:
        print(f"ERROR: Could not import great_sage_core: {e}")
        sys.exit(1)

    s = GreatSageSync()
    if not s.is_logged_in():
        print("ERROR: Not logged in to Great Sage cloud sync.")
        print("Open Great Sage, go to Settings → Cloud Sync and log in first.")
        sys.exit(1)

    print(f"Logged in as user: {s._user_id}")

    from datetime import datetime, timezone

    lib_data   = get_bookmarks_data()
    prog_data  = load_json_cached(LEGION_PROGRESS, {"books": {}})
    books_prog = prog_data.get("books", {})

    cat_map = {
        "planning":  "plan_to_read",
        "reading":   "reading",
        "dropped":   "dropped",
        "completed": "completed",
    }

    rows    = []
    skipped = 0

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

            prog        = books_prog.get(title, {})
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
            print(f"  [{category}] {title} — ch {chapter_num}, reader_url: {'yes' if reader_url else 'none'}")

    if not rows:
        print("No books found in library — nothing to push.")
        return

    print(f"\nPushing {len(rows)} books to Supabase...")
    try:
        s._upsert("watchlist", rows, on_conflict="user_id,title")
        print(f"Done. {len(rows)} books pushed, {skipped} skipped.")
    except Exception as e:
        print(f"ERROR during upsert: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
