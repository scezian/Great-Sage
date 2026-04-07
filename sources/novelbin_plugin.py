import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import sys
import os

# Import the base class
# Since this will be loaded via importlib.util, we can assume the parent dir is in sys.path
# or we can do relative import if it's a package.
# But legion.py does: sys.path.append(os.path.dirname(__file__)) or similar.
# The loader in legion.py will handle it.
from source_plugin_base import SourcePlugin, ChapterResult, SearchResult, BookMetadata

class NovelBinPlugin(SourcePlugin):
    id = "novelbin"
    name = "NovelBin"
    base_url = "https://novelbin.com"
    supports_search = True
    supports_cloudflare = True

    MIRRORS = ["novelbin.com", "novelbin.me", "novelfull.com", "novelfull.me", "novelfull.net"]

    WATERMARK_PATTERNS = [
        r"(?i)visit\s+\S+\s+for\s+(more|latest|updates?).*",
        r"(?i)read\s+(at|on)\s+\S+\s+for.*",
        r"(?i)support\s+the\s+(author|translator)\s+at\s+\S+.*",
        r"(?i)this\s+chapter\s+(is|was)\s+(stolen|taken)\s+from.*",
        r"(?i)find\s+(this|more)\s+(story|chapter|content)\s+(at|on)\s+\S+.*",
        r"(?i)novelbin\.(me|com|net)\S*",
        r"(?i)\[.*?novelbin.*?\]",
        # NovelBin comment/response junk
        r"(?i)^total\s+responses?\s*:\s*\d+$",
        r"(?i)^responses?\s*:\s*\d+$",
        r"(?i)^\d+\s+comments?$",
        r"(?i)^(load|show)\s+more\s+comments?.*",
        r"(?i)^leave\s+a\s+(reply|comment).*",
        r"(?i)^(sponsored|advertisement|advert)\b.*",
        r"(?i)^your\s+email\s+address\s+will\s+not\s+be\s+published.*",
    ]

    def can_handle(self, url: str) -> bool:
        domain = urlparse(url).netloc
        return any(m in domain for m in self.MIRRORS)

    def _build_mirror_urls(self, url: str) -> list:
        parsed = urlparse(url)
        original_host = parsed.netloc
        urls = [url]
        for mirror in self.MIRRORS:
            if mirror != original_host:
                urls.append(url.replace(original_host, mirror, 1))
        return urls

    def _get_with_retry(self, url: str, session, scraper=None):
        urls = self._build_mirror_urls(url)
        last_error = "Unknown error"
        for attempt_url in urls:
            try:
                # Use scraper if provided, else session
                resp = (scraper or session).get(attempt_url, timeout=15)
                if resp.status_code == 403:
                    last_error = f"403 Forbidden — Cloudflare protected"
                    continue
                resp.raise_for_status()
                return resp, attempt_url
            except Exception as e:
                last_error = str(e)
                continue
        raise Exception(f"All mirrors failed: {last_error}")

    def fetch_chapter(self, url: str, session, scraper=None) -> ChapterResult:
        try:
            resp, actual_url = self._get_with_retry(url, session, scraper)
        except Exception as e:
            return ChapterResult("", [], None, None, None, error=str(e))

        soup = BeautifulSoup(resp.text, "html.parser")

        title = "Chapter"
        for tag_name, attrs in [("span", {"class": "chr-text"}), ("h2", {"class": "chr-title"}), ("h1", {})]:
            tag = soup.find(tag_name, attrs)
            if tag:
                title = tag.get_text(strip=True)
                break

        content_div = None
        for selector in ["chr-content", "chapter-content", "content"]:
            content_div = soup.find("div", id=selector)
            if content_div:
                break
        if not content_div:
            divs = soup.find_all("div")
            content_div = max(divs, key=lambda d: len(d.find_all("p")), default=None)
        
        if not content_div:
            return ChapterResult(title, [], None, None, None, error="Could not locate chapter content.")

        for junk in content_div(["script", "style", "iframe", "ins", "noscript"]):
            junk.decompose()

        raw_paragraphs = [
            p.get_text(separator=" ").strip()
            for p in content_div.find_all("p")
            if p.get_text(strip=True)
        ]
        if not raw_paragraphs:
            raw_text = content_div.get_text(separator="\n").strip()
            raw_paragraphs = [l.strip() for l in raw_text.splitlines() if l.strip()]

        parsed = urlparse(actual_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        
        next_url = self._extract_nav(soup, "next_chap", base)
        prev_url = self._extract_nav(soup, "prev_chap", base)

        url_ch_num = None
        m_url = re.search(r'/chapter-([0-9]+)', actual_url)
        if m_url:
            url_ch_num = int(m_url.group(1))

        if not prev_url and url_ch_num is not None and url_ch_num > 1:
            ch_prefix = actual_url[:actual_url.index(m_url.group(0))]
            prev_url = f"{ch_prefix}/chapter-{url_ch_num - 1}"

        return ChapterResult(
            title=title,
            paragraphs=raw_paragraphs,
            next_url=next_url,
            prev_url=prev_url,
            chapter_num=url_ch_num
        )

    def _extract_nav(self, soup, link_id, base):
        def _make_abs(href):
            if not href or href.startswith("javascript") or href in ("#", "null", "undefined"):
                return None
            return href if href.startswith("http") else base + (href if href.startswith("/") else "/" + href)

        is_next = "next" in link_id
        tag = soup.find("a", id=link_id)
        if tag and tag.get("href"):
            return _make_abs(tag["href"])

        id_variants = (
            ["next_chap","next-chap","next_chapter","next-chapter","nextchapter",
             "next_btn","btn-next","next-page","nextPage","chapter-next"]
            if is_next else
            ["prev_chap","prev-chap","prev_chapter","prev-chapter","prevchapter",
             "prev_btn","btn-prev","prev-page","prevPage","chapter-prev"]
        )
        for vid in id_variants:
            tag = soup.find("a", id=vid)
            if tag and tag.get("href"):
                return _make_abs(tag["href"])

        cls_variants = (
            ["next_chap","next-chap","next_chapter","next-chapter","next-page",
             "nextchapter","btn-next","chapter-next","nav-next","pager-next"]
            if is_next else
            ["prev_chap","prev-chap","prev_chapter","prev-chapter","prev-page",
             "prevchapter","btn-prev","chapter-prev","nav-prev","pager-prev"]
        )
        for cls in cls_variants:
            tag = soup.find("a", class_=re.compile(cls, re.I))
            if tag and tag.get("href"):
                return _make_abs(tag["href"])

        rel = "next" if is_next else "prev"
        tag = soup.find("a", rel=re.compile(rf"\b{rel}\b", re.I))
        if tag and tag.get("href"):
            return _make_abs(tag["href"])
        return None

    def clean_content(self, paragraphs: list[str]) -> list[str]:
        return [
            p for p in paragraphs
            if not any(re.search(pat, p) for pat in self.WATERMARK_PATTERNS)
        ]

    def search(self, query: str, session) -> list[SearchResult]:
        search_url = f"https://novelbin.com/search?keyword={query}"
        try:
            resp = session.get(search_url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            # Based on prompt: book title + URL from <h3 class="novel-title">
            for item in soup.find_all("div", class_="row"):
                title_tag = item.find("h3", class_="novel-title")
                if title_tag and title_tag.find("a"):
                    a = title_tag.find("a")
                    title = a.get_text(strip=True)
                    url = a["href"]
                    if not url.startswith("http"):
                        url = "https://novelbin.com" + url
                    
                    desc = ""
                    # Optional: extract more info if available
                    
                    results.append(SearchResult(title=title, url=url, description=desc))
                    if len(results) >= 10:
                        break
            return results
        except Exception:
            return []

plugin = NovelBinPlugin()
