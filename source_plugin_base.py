from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class ChapterResult:
    title: str
    paragraphs: list[str]
    next_url: Optional[str]
    prev_url: Optional[str]
    chapter_num: Optional[int]
    error: Optional[str] = None

@dataclass
class SearchResult:
    title: str
    url: str          # canonical book/series page URL
    description: str = ""
    cover_url: str = ""

@dataclass
class BookMetadata:
    title: str = ""
    author: str = ""
    status: str = ""
    genres: list[str] = field(default_factory=list)
    synopsis: str = ""
    cover_url: str = ""
    source_id: str = ""   # e.g. "novelbin"

class SourcePlugin(ABC):
    # --- Identity ---
    id: str          # e.g. "novelbin"  (unique, no spaces)
    name: str        # e.g. "NovelBin"  (display name)
    base_url: str    # e.g. "https://novelbin.com"
    supports_search: bool = False
    supports_cloudflare: bool = False  # True = needs cloudscraper

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Return True if this plugin should handle the given URL."""

    @abstractmethod
    def fetch_chapter(self, url: str, session, scraper=None) -> ChapterResult:
        """Fetch and parse one chapter. session = requests.Session."""

    def search(self, query: str, session) -> list[SearchResult]:
        """Search for novels. Override if supports_search = True."""
        return []

    def fetch_metadata(self, book_url: str, session) -> BookMetadata:
        """Fetch book-level metadata from a series/book page. Optional."""
        return BookMetadata()

    def clean_content(self, paragraphs: list[str]) -> list[str]:
        """Post-process extracted paragraphs. Default: return as-is."""
        return paragraphs
