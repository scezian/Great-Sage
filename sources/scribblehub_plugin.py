import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from source_plugin_base import SourcePlugin, ChapterResult, SearchResult

class ScribbleHubPlugin(SourcePlugin):
    id = "scribblehub"
    name = "ScribbleHub"
    base_url = "https://www.scribblehub.com"
    supports_search = True
    supports_cloudflare = False

    WATERMARK_PATTERNS = [
        r"(?i)scribblehub\.com",
        r"(?i)visit\s+\S+\s+for\s+(more|latest|updates?).*",
        r"(?i)this\s+chapter\s+(is|was)\s+(stolen|taken)\s+from.*",
    ]

    def can_handle(self, url: str) -> bool:
        return "scribblehub.com" in urlparse(url).netloc

    def fetch_chapter(self, url: str, session, scraper=None) -> ChapterResult:
        try:
            resp = (scraper or session).get(url, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            return ChapterResult("", [], None, None, None, error=str(e))

        soup = BeautifulSoup(resp.text, "html.parser")

        # Title: div.chapter-title or h1
        title = "Chapter"
        for tag, attrs in [
            ("div", {"class": "chapter-title"}),
            ("h1",  {"class": re.compile(r"chapter.title", re.I)}),
            ("h1",  {}),
        ]:
            t = soup.find(tag, attrs)
            if t:
                title = t.get_text(strip=True)
                break

        # Content: div#chp_raw — ScribbleHub's unique id
        content_div = soup.find("div", id="chp_raw")
        if not content_div:
            content_div = soup.find("div", class_=re.compile(r"chapter.content|reading.content", re.I))
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

        base = "https://www.scribblehub.com"

        # Nav: a#btn-read-next / a#btn-read-prev
        next_url = self._nav(soup, True, base)
        prev_url = self._nav(soup, False, base)

        # ScribbleHub URL pattern: /read/{series_id}/chapter/{chapter_num}/
        ch_num = None
        m = re.search(r"/chapter/(\d+)", url)
        if m:
            ch_num = int(m.group(1))
        else:
            # fallback: /chapter-N style
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

        id_key = "btn-read-next" if is_next else "btn-read-prev"
        tag = soup.find("a", id=id_key)
        if tag:
            return abs_href(tag)

        # class fallback
        cls = re.compile(r"btn-next|next-chap" if is_next else r"btn-prev|prev-chap", re.I)
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
        url = f"https://www.scribblehub.com/?s={query}&post_type=fictionposts"
        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for item in soup.select(".search-li, .novel-item"):
                a = item.select_one(".search-title a, h3 a")
                if not a:
                    continue
                title = a.get_text(strip=True)
                href = a.get("href", "")
                img = item.select_one("img")
                cover = (img.get("src") or "") if img else ""
                desc_el = item.select_one(".search-content, .novel-excerpt")
                desc = desc_el.get_text(strip=True) if desc_el else ""
                results.append(SearchResult(title=title, url=href, description=desc, cover_url=cover))
                if len(results) >= 10:
                    break
            return results
        except Exception:
            return []

plugin = ScribbleHubPlugin()
