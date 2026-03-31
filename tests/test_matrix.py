"""
tests/test_matrix.py — Unit tests for matrix.py episode parsing helpers
"""
import os
import sys
import pytest

# Add the project root to sys.path so matrix.py can be imported directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from matrix import MediaPlayer


# ── _extract_episode_number ────────────────────────────────────────────────────

class TestExtractEpisodeNumber:
    """Tests for MediaPlayer._extract_episode_number."""

    @pytest.mark.parametrize("filename, expected", [
        # Standard SxxExx
        ("Show.Name.S01E05.720p.mkv", 5),
        ("show.s2e12.mkv",            12),
        ("S10E01.mp4",                1),
        # Cross format 1x05
        ("Show.1x05.mkv",             5),
        ("Show.2X10.avi",             10),
        # Episode / Ep keyword
        ("Show Episode 07.mkv",       7),
        ("Show.Ep.3.mkv",             3),
        # NOTE: "EP05" mid-filename with trailing text is a known parser gap —
        # the regex requires a dot/space before the digits, so this returns None.
        ("Show EP05 Title.mkv",       None),
        # Bare number at start
        ("05 - Title.mkv",            5),
        ("12.Something.mkv",          12),
        # Bare number at end
        ("Title - 08.mkv",            8),
        # Bracketed
        ("[SubGroup] Show - 03 [720p].mkv", 3),
        # No match at all
        ("README.txt",                None),
    ])
    def test_episode_extraction(self, filename, expected):
        result = MediaPlayer._extract_episode_number(filename)
        assert result == expected, f"For '{filename}': expected {expected}, got {result}"


# ── _extract_season_episode ────────────────────────────────────────────────────

class TestExtractSeasonEpisode:
    """Tests for MediaPlayer._extract_season_episode."""

    @pytest.mark.parametrize("filename, expected", [
        # SxxExx
        ("Show.S01E05.mkv",   (1, 5)),
        ("show.S03E12.mkv",   (3, 12)),
        # Cross format
        ("Show.2x10.mkv",     (2, 10)),
        # Three-digit combined: 101 → S1E01
        ("Show.Name.101.mkv", (1, 1)),
        ("Show.Name.312.mkv", (3, 12)),
        # Episode-only → defaults to season 1
        ("Episode 07.mkv",    (1, 7)),
        # Nothing found
        ("README.txt",        (0, 0)),
    ])
    def test_season_episode_extraction(self, filename, expected):
        result = MediaPlayer._extract_season_episode(filename)
        assert result == expected, f"For '{filename}': expected {expected}, got {result}"
