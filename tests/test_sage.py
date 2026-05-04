"""
tests/test_sage.py — Unit tests for sage.py data helpers
"""
import os
import sys
import json
import tempfile
import pytest

# Add the project root to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestFindBookTxt:
    """Tests for sage.find_book_txt with a controlled temporary directory."""

    def setup_method(self):
        """Create a temporary directory with a few fake book .txt files."""
        self._tmpdir = tempfile.mkdtemp()
        # Create fake book files
        for name in [
            "Sage Of Humanity.txt",
            "The Golem Mage.txt",
            "Devouring Evolution I Reborn As An Arctic Wolf.txt",
        ]:
            with open(os.path.join(self._tmpdir, name), "w") as f:
                f.write("placeholder")

    def teardown_method(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_exact_match(self):
        """find_book_txt should locate a file with an exact name match."""
        import sage
        # Temporarily override search dirs to use only our temp dir
        original = sage.LEGION_BOOK_SEARCH_DIRS
        sage.LEGION_BOOK_SEARCH_DIRS = [self._tmpdir]
        sage._book_path_cache.clear()
        try:
            result = sage.find_book_txt("Sage Of Humanity")
            assert result is not None
            assert result.endswith("Sage Of Humanity.txt")
        finally:
            sage.LEGION_BOOK_SEARCH_DIRS = original

    def test_fuzzy_match(self):
        """find_book_txt should locate a file via fuzzy normalised match."""
        import sage
        original = sage.LEGION_BOOK_SEARCH_DIRS
        sage.LEGION_BOOK_SEARCH_DIRS = [self._tmpdir]
        sage._book_path_cache.clear()
        try:
            result = sage.find_book_txt("The Golem Mage")
            assert result is not None
            assert "Golem" in result
        finally:
            sage.LEGION_BOOK_SEARCH_DIRS = original

    def test_cache_hit(self):
        """After a successful lookup, a second call should return from cache."""
        import sage
        original = sage.LEGION_BOOK_SEARCH_DIRS
        sage.LEGION_BOOK_SEARCH_DIRS = [self._tmpdir]
        sage._book_path_cache.clear()
        try:
            result1 = sage.find_book_txt("The Golem Mage")
            assert result1 is not None
            # Cache should now contain this entry
            assert "The Golem Mage" in sage._book_path_cache
            # Second call — even with empty search dirs, cache returns the result
            sage.LEGION_BOOK_SEARCH_DIRS = []
            result2 = sage.find_book_txt("The Golem Mage")
            assert result2 == result1
        finally:
            sage.LEGION_BOOK_SEARCH_DIRS = original

    def test_not_found(self):
        """find_book_txt should return None for a non-existent title."""
        import sage
        original = sage.LEGION_BOOK_SEARCH_DIRS
        sage.LEGION_BOOK_SEARCH_DIRS = [self._tmpdir]
        sage._book_path_cache.clear()
        try:
            result = sage.find_book_txt("Definitely Not A Real Book Title 999")
            assert result is None
        finally:
            sage.LEGION_BOOK_SEARCH_DIRS = original


class TestProfileToText:
    """Smoke test for sage.profile_to_text — ensure it produces a string."""

    @staticmethod
    def test_empty_profile():
        import sage
        profile = {
            "novels": [],
            "watching": [],
            "watchlist": [],
            "completed": [],
            "stats": {
                "total_chapters_read": 0,
                "total_reading_hours": 0,
                "shows_completed": 0,
                "books_bookmarked": 0,
            },
            "bookmarks": {},
            "external": {},
        }
        text = sage.profile_to_text(profile)
        assert isinstance(text, str)
        assert "STATS" in text
