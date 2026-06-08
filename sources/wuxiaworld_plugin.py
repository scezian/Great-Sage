import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from source_plugin_base import SourcePlugin, ChapterResult, SearchResult

class WuxiaWorldPlugin(SourcePlugin):
    id = "wuxiaworld"
    name = "WuxiaWorld"
    base_url = "https://www.wuxiaworld.com"
    supports_search = False  # search is API/AJAX
    supports_cloudflare = False

    WATERMARK_PATTERNS = [
        r"(?i)wuxiaworld\.com",
        r"(?i)volarenovels?\.com",
        r"(?i)visit\s+\S+\s+for\s+(more|latest|updates?).*",
        r"(?i)read\s+(at|on)\s+\S+\s+for.*",
        r"(?i)this\s+chapter\s+(is|was)\s+(stolen|taken)\s+from.*",
        r"(?i)^(sponsored\s+by|ad|advertisement)\b.*",
        r"(?i)^\s*(join|support)\s+(our\s+)?(discord|patreon)\b.*",
    ]

    def can_handle(self, url: str) -> bool:
        return "wuxiaworld.com" in urlparse(url).netloc

    def fetch_chapter(self, url: str, session, scraper=None) -> ChapterResult:
        try:
            resp = (scraper or session).get(url, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            return ChapterResult("", [], None, None, None, error=str(e))

        soup = BeautifulSoup(resp.text, "html.parser")

        # Title: h4.panel-title, h2, or h1
        title = "Chapter"
        for tag, attrs in [
            ("h4", {"class": "panel-title"}),
            ("h2", {"class": re.compile(r"chapter.title", re.I)}),
            ("h1", {}),
            ("h2", {}),
        ]:
            t = soup.find(tag, attrs)
            if t:
                title = t.get_text(strip=True)
                break

        # Content: div.chapter-content (class)
        content_div = soup.find("div", class_="chapter-content")
        if not content_div:
            # WW sometimes uses div#chapter-content
            content_div = soup.find("div", id="chapter-content")
        if not content_div:
            # Try any div with reading/text content classes
            content_div = soup.find("div", class_=re.compile(r"reading.content|chapter.text", re.I))
        if not content_div:
            divs = soup.find_all("div")
            content_div = max(divs, key=lambda d: len(d.find_all("p")), default=None)

        if not content_div:
            return ChapterResult(title, [], None, None, None, error="Could not locate content.")

        for junk in content_div(["script", "style", "iframe", "ins", "noscript", "aside"]):
            junk.decompose()

        raw = [p.get_text(separator=" ").strip() for p in content_div.find_all("p") if p.get_text(strip=True)]
        if not raw:
            raw = [l.strip() for l in content_div.get_text(separator="\n").splitlines() if l.strip()]

        base = "https://www.wuxiaworld.com"

        next_url = self._nav(soup, True, base)
        prev_url = self._nav(soup, False, base)

        ch_num = None
        m = re.search(r"/chapter-(\d+)", url)
        if m:
            ch_num = int(m.group(1))

        return ChapterResult(title=title, paragraphs=raw,
                             next_url=next_url, prev_url=prev_url, chapter_num=ch_num)

    def _nav(self, soup, is_next: bool, base: str):
        def abs_href(a):
            h = a.get("href", "")
            if not h or h.startswith("javascript") or h in ("#", "null"):
                return None
            return h if h.startswith("http") else base + (h if h.startswith("/") else "/" + h)

        # rel=next/prev — WW honours this
        rel = "next" if is_next else "prev"
        tag = soup.find("a", rel=re.compile(rf"\b{rel}\b", re.I))
        if tag:
            return abs_href(tag)

        # class: chapter-nav-right / chapter-nav-left
        cls = re.compile(r"chapter.nav.right|next.chapter" if is_next
                         else r"chapter.nav.left|prev.chapter", re.I)
        tag = soup.find("a", class_=cls)
        if tag:
            return abs_href(tag)

        patterns = ([r"next\s*chapter", r"^next$"] if is_next
                    else [r"prev\s*chapter", r"previous\s*chapter", r"^prev$"])
        for a in soup.find_all("a", href=True):
            if any(re.search(p, a.get_text(strip=True), re.I) for p in patterns):
                h = abs_href(a)
                if h:
                    return h
        return None

    def clean_content(self, paragraphs: list[str]) -> list[str]:
        return [p for p in paragraphs
                if not any(re.search(pat, p) for pat in self.WATERMARK_PATTERNS)]

plugin = WuxiaWorldPlugin()
