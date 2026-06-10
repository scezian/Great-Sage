"""
gs_episode_tracker.py — Reliable episode number detection for Great Sage
========================================================================
Tries multiple strategies in order, scoped strictly to the immediate
folder containing the file to avoid inflated counts from parent dirs.

Usage
-----
    from gs_episode_tracker import get_episode_number

    ep = get_episode_number("/media/Anime/Chaika/[Exiled-Destiny] Chaika S03E07.mkv")
    # → 7

    ep = get_episode_number("/media/Anime/FogHill/[SubsWhen] Fog Hill 02 (1080p).mkv")
    # → 2

    ep = get_episode_number("/media/Anime/FogHill/[SubsWhen] Fog Hill (1080p).mkv")
    # → folder position (e.g. 2 if it's the 2nd file)
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

VIDEO_EXTS = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v'}


def get_episode_number(file_path: str) -> int:
    """
    Return the episode number for a given video file.
    Returns 0 if all strategies fail.
    """
    filename = os.path.basename(file_path)
    folder   = os.path.dirname(os.path.abspath(file_path))

    # Strategy 1 — Standard SxxExx / sXeX
    ep = _try_sxxexx(filename)
    if ep:
        return ep

    # Strategy 2 — Broader patterns: "- 07 -", "[07]", "Episode 7", "07.mkv"
    ep = _try_broad_patterns(filename)
    if ep:
        return ep

    # Strategy 3 — Immediate folder position (no os.walk, no parent dirs)
    ep = _folder_position(file_path, folder)
    if ep:
        return ep

    return 0


# ── Strategy implementations ──────────────────────────────────────────────────

def _try_sxxexx(filename: str) -> Optional[int]:
    """Match S01E05, s1e5, 1x05 style patterns."""
    name = os.path.splitext(filename)[0]

    # S01E05 / s1e5
    m = re.search(r'[Ss]\d{1,2}[Ee](\d{1,4})', name)
    if m:
        return int(m.group(1))

    # 1x05
    m = re.search(r'\d{1,2}[xX](\d{1,4})', name)
    if m:
        return int(m.group(1))

    return None


def _try_broad_patterns(filename: str) -> Optional[int]:
    """
    Match episode numbers that aren't in SxxExx format.
    Patterns tried in priority order.
    """
    name = os.path.splitext(filename)[0]

    patterns = [
        # "Episode 07" / "Ep.7" / "EP07"
        r'[Ee]p(?:isode)?\.?\s*(\d{1,4})',
        # "- 07 -" / "- 07." / " 07 " surrounded by non-digits
        r'(?:^|[-–\s])\s*(\d{1,3})\s*(?:[-–\s]|$)',
        # "[07]" or "(07)"
        r'[\[\(](\d{1,3})[\]\)]',
        # bare number at very start: "07 - Title"
        r'^(\d{1,3})\s*[-–.]',
        # bare number at very end: "Title - 07"
        r'[-–.]\s*(\d{1,3})\s*$',
    ]

    for pattern in patterns:
        m = re.search(pattern, name)
        if m:
            val = int(m.group(1))
            # Sanity check — episode numbers are rarely > 1000
            if 1 <= val <= 1500:
                return val

    return None


def _folder_position(file_path: str, folder: str) -> Optional[int]:
    """
    Return the 1-based position of file_path among video files
    in its immediate folder, sorted naturally.
    Strictly scoped to the immediate directory — no os.walk.
    """
    try:
        if not os.path.isdir(folder):
            return None

        video_files = sorted(
            f for f in os.listdir(folder)
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS
        )

        filename = os.path.basename(file_path)
        if filename in video_files:
            return video_files.index(filename) + 1

    except Exception:
        pass

    return None


# ── Quick test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_cases = [
        ("[Exiled-Destiny] Chaika The Coffin Princess S03E07.mkv", 7),
        ("[SubsWhen] Fog Hill of Five Elements - 02 (1080p AVC).mkv", 2),
        ("Juni Taisen Zodiac War S01E08.mkv", 8),
        ("Episode 04 - Some Show.mkv", 4),
        ("[Group] Show Name [1080p][07].mkv", 7),
        ("01.mkv", 1),
        ("[SubsWhen] Fog Hill of Five Elements S01 (WEB 1080p AVC AAC).mkv", 0),
    ]

    print("Strategy tests (no filesystem):")
    for fname, expected in test_cases:
        ep = _try_sxxexx(fname) or _try_broad_patterns(fname) or 0
        status = "✓" if ep == expected else f"✗ (got {ep}, expected {expected})"
        print(f"  {status}  {fname}")
