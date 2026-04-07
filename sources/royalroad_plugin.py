import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from source_plugin_base import SourcePlugin, ChapterResult, SearchResult, BookMetadata

class RoyalRoadPlugin(SourcePlugin):
    id = "royalroad"
    name = "Royal Road"
    base_url = "https://www.royalroad.com"
    supports_search = True
    supports_cloudflare = False # RR often works with standard session if headers are good

    def can_handle(self, url: str) -> bool:
        return "royalroad.com" in urlparse(url).netloc

    def fetch_chapter(self, url: str, session, scraper=None) -> ChapterResult:
        try:
            resp = (scraper or session).get(url, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            return ChapterResult("", [], None, None, None, error=str(e))

        soup = BeautifulSoup(resp.text, "html.parser")

        # Title in <h1 class="font-white"> or <h2>
        title = "Chapter"
        title_tag = soup.find("h1")
        if title_tag:
            title = title_tag.get_text(strip=True)
        else:
            title_tag = soup.find("h2")
            if title_tag:
                title = title_tag.get_text(strip=True)

        content_div = soup.find("div", class_="chapter-content")
        if not content_div:
            return ChapterResult(title, [], None, None, None, error="Could not locate RR chapter content.")

        # Clean junk
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

        # Navigation
        next_url = None
        prev_url = None
        base = "https://www.royalroad.com"
        
        # RR uses buttons with text
        for a in soup.find_all("a", class_="btn-primary"):
            text = a.get_text().lower()
            href = a.get("href")
            if not href: continue
            
            if "next chapter" in text:
                next_url = base + href if href.startswith("/") else href
            elif "previous chapter" in text:
                prev_url = base + href if href.startswith("/") else href

        return ChapterResult(
            title=title,
            paragraphs=raw_paragraphs,
            next_url=next_url,
            prev_url=prev_url,
            chapter_num=None # RR URLs aren't always /chapter-N
        )

    def clean_content(self, paragraphs: list[str]) -> list[str]:
        cleaned = []
        for p in paragraphs:
            # Strip <br> artifacts and lines that are purely whitespace or contain only "---"
            p = p.replace("<br>", "").replace("<br/>", "").strip()
            if not p or p == "---" or p == "...":
                continue
            cleaned.append(p)
        return cleaned

    def search(self, query: str, session) -> list[SearchResult]:
        search_url = f"https://www.royalroad.com/fictions/search?title={query}"
        try:
            resp = session.get(search_url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            
            # fiction cards <div class="fiction-list-item">
            for item in soup.find_all("div", class_="fiction-list-item"):
                title_tag = item.find("h2", class_="fiction-title")
                if title_tag and title_tag.find("a"):
                    a = title_tag.find("a")
                    title = a.get_text(strip=True)
                    url = "https://www.royalroad.com" + a["href"]
                    
                    desc = ""
                    desc_tag = item.find("div", class_="description")
                    if desc_tag:
                        desc = desc_tag.get_text(strip=True)
                    
                    cover_url = ""
                    img_tag = item.find("img")
                    if img_tag:
                        cover_url = img_tag.get("src", "")
                    
                    results.append(SearchResult(title=title, url=url, description=desc, cover_url=cover_url))
                    if len(results) >= 10:
                        break
            return results
        except Exception:
            return []

plugin = RoyalRoadPlugin()
