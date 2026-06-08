"""
library_validator.py
────────────────────
Runs automatically at startup (called from AutoSyncWorker before any sync
decisions are made). Scans every book in the library, detects corrupt files
(nav-menu garbage, bot-challenge pages, near-empty files), deletes them, and
resets the book's download_state so the sync worker re-queues a clean download.

No user action required — fully automatic.
"""

import os
import re
import shutil

# ── Garbage signatures ────────────────────────────────────────────────────────
# These strings appear in nav-menu garbage but never in real chapter text.
# Threshold: if NAV_THRESHOLD or more are found in the first sample, it's corrupt.

NAV_GARBAGE_STRINGS = [
    "Novel Bin", "Novel List", "Latest Release", "Hot Novel",
    "Completed Novel", "Most Popular", "Light gray", "Light blue",
    "Light yellow", "Wood grain", "Palatino Linotype", "Bookerly",
    "Font family", "Font size", "Line height", "Full frame",
    "Login/Signup", "Show menu", "Read light novel",
    "korean novel and chinese novel",
]
NAV_THRESHOLD = 4          # hits needed to call it corrupt
SAMPLE_CHARS  = 8000       # how many chars from the start of the file to sample
MIN_VALID_BYTES = 500      # files smaller than this are always corrupt


def _is_corrupt(filepath: str) -> tuple[bool, str]:
    """
    Return (is_corrupt, reason).
    Reads only the first SAMPLE_CHARS bytes — fast even on large files.
    """
    try:
        size = os.path.getsize(filepath)
        if size < MIN_VALID_BYTES:
            return True, f"File too small ({size} bytes)"

        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            sample = f.read(SAMPLE_CHARS)

        hits = sum(1 for s in NAV_GARBAGE_STRINGS if s in sample)
        if hits >= NAV_THRESHOLD:
            return True, f"Nav-menu garbage detected ({hits} signature matches in first {SAMPLE_CHARS} chars)"

        # Also catch JS-challenge pages that somehow got saved
        if re.search(r"window\.location\.replace\s*\(", sample):
            return True, "JS bot-challenge page saved as chapter content"

        return False, ""

    except Exception as e:
        return False, ""   # can't read — leave it alone


def _reset_download_state(book: dict) -> dict:
    """Return a clean idle download_state dict."""
    return {
        "status":                     "idle",
        "total_chapters_downloaded":  0,
        "last_downloaded_chapter":    None,
        "last_downloaded_chapter_num": None,
        "last_error":                 None,
        "failed_chapters":            [],
        "download_path":              None,
    }


def validate_library(legion_data: dict, library_dir: str, log=None) -> tuple[dict, list[str]]:
    """
    Scan all books in legion_data against their library files.
    Deletes corrupt files and resets download_state for affected books.

    Returns:
        (updated_legion_data, list_of_cleaned_book_names)
    """
    cleaned = []
    books = legion_data.get("books", {})

    for book_name, book in books.items():
        safe = re.sub(r'[^\w\-_\. ]', '_', book_name)
        book_dir  = os.path.join(library_dir, safe)
        book_file = os.path.join(book_dir, safe + ".txt")

        dl_state = book.get("download_state", {})
        status   = dl_state.get("status", "idle")
        chapters = dl_state.get("total_chapters_downloaded", 0)

        # Skip books currently downloading — don't interfere
        if status in ("downloading", "queued"):
            continue

        # Skip books with no file on disk — nothing to validate
        if not os.path.exists(book_file):
            # But if JSON thinks there are chapters, reconcile it
            if chapters > 0:
                book["download_state"] = _reset_download_state(book)
                cleaned.append(book_name)
                if log:
                    log.warning("Library validator: JSON claims chapters but no file — reset",
                                book=book_name, json_chapters=chapters)
            continue

        corrupt, reason = _is_corrupt(book_file)
        if not corrupt:
            continue

        # Delete the corrupt library folder
        try:
            shutil.rmtree(book_dir)
            if log:
                log.warning("Library validator: deleted corrupt library folder",
                            book=book_name, reason=reason, path=book_dir)
        except Exception as e:
            if log:
                log.error("Library validator: failed to delete corrupt folder",
                          book=book_name, error=str(e))
            continue

        # Reset download state so sync re-queues it
        book["download_state"] = _reset_download_state(book)
        # Also clear the Catalogue entry if it exists
        catalogue_dir = os.path.join(os.path.dirname(library_dir), "Catalogue", safe)
        if os.path.exists(catalogue_dir):
            try:
                shutil.rmtree(catalogue_dir)
            except Exception:
                pass

        cleaned.append(book_name)

    return legion_data, cleaned
