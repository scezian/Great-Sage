import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from source_plugin_base import SourcePlugin, ChapterResult, SearchResult

class NovelCoolPlugin(SourcePlugin):
    id = "novelcool"
    name = "NovelCool"
    base_url = "https://www.novelcool.com"
    supports_search = True
    supports_cloudflare = False

    WATERMARK_PATTERNS = [
        r"(?i)novelcool\.com",
        r"(?i)visit\s+\S+\s+for\s+(more|latest|updates?).*",
        r"(?i)this\s+chapter\s+(is|was)\s+(stolen|taken)\s+from.*",
    ]

    def can_handle(self, url: str) -> bool:
        return "novelcool.com" in urlparse(url).netloc

    def fetch_chapter(self, url: str, session, scraper=None) -> ChapterResult:
        try:
            resp = (scraper or session).get(url, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            return ChapterResult("", [], None, None, None, error=str(e))

        soup = BeautifulSoup(resp.text, "html.parser")

        title = "Chapter"
        for tag, attrs in [
            ("h1", {"class": re.compile(r"chapter.title", re.I)}),
            ("h2", {"class": re.compile(r"chapter.title", re.I)}),
            ("h1", {}),
        ]:
            t = soup.find(tag, attrs)
            if t:
                title = t.get_text(strip=True)
                break

        # Primary: div#chapter_content (underscore — not hyphen)
        content_div = soup.find("div", id="chapter_content")
        if not content_div:
            content_div = soup.find("div", id="chapter-content")
        if not content_div:
            content_div = soup.find("div", class_=re.compile(r"chapter.content", re.I))
        if not content_div:
            divs = soup.find_all("div")
            content_div = max(divs, key=lambda d: len(d.find_all("p")), default=None)

        if not content_div:
            return ChapterResult(title, [], None, None, None, error="Could not locate content.")

        for junk in content_div(["script", "style", "iframe", "ins", "noscript"]):
            junk.decompose()

        raw = [p.get_text(separator=" ").strip() for p in content_div.find_all("p") if p.get_text(strip=True)]
        if not raw:
            raw = [l.strip() for l in content_div.get_text(separator="\n").splitlines() if l.strip()]

        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"

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

        # NovelCool: a.next_page / a.prev_page (underscore)
        cls_exact = "next_page" if is_next else "prev_page"
        tag = soup.find("a", class_=cls_exact)
        if tag:
            return abs_href(tag)

        cls = re.compile(r"next.page|btn.next|chapter.next" if is_next
                         else r"prev.page|btn.prev|chapter.prev", re.I)
        tag = soup.find("a", class_=cls)
        if tag:
            return abs_href(tag)

        rel = "next" if is_next else "prev"
        tag = soup.find("a", rel=re.compile(rf"\b{rel}\b", re.I))
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

    def search(self, query: str, session) -> list[SearchResult]:
        from urllib.parse import quote_plus
        url = f"https://www.novelcool.com/search/?name={quote_plus(query)}&page=1"
        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            seen = set()
            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                if "/novel/" not in href:
                    continue
                title = a.get("title", "").strip() or a.get_text(strip=True)
                if not title or len(title) < 3:
                    continue
                if any(kw in title for kw in ["ActionAdventure", "SummaryN/A", "FantasySci"]):
                    continue
                if href in seen:
                    continue
                seen.add(href)
                img = a.find("img")
                cover = (img.get("data-src") or img.get("src", "")) if img else ""
                results.append(SearchResult(title=title, url=href, cover_url=cover))
                if len(results) >= 10:
                    break
            return results
        except Exception:
            return []

    def get_first_chapter_url(self, book_url: str, session):
        """NovelCool chapter URLs contain a numeric ID — fetch the book page to get it."""
        try:
            resp = session.get(book_url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            ch_links = []
            for a in soup.find_all("a", href=True):
                h = a.get("href", "")
                if "/chapter/" in h:
                    m = re.search(r"-Chapter-(\d+)-", h)
                    num = int(m.group(1)) if m else 9999
                    ch_links.append((num, h))
            if not ch_links:
                return None
            ch_links.sort(key=lambda x: x[0])
            return ch_links[0][1]
        except Exception:
            return None

plugin = NovelCoolPlugin()
