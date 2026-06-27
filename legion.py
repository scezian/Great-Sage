#!/usr/bin/env python3
try:
    import cloudscraper
    CLOUDSCRAPER = True
except ImportError:
    CLOUDSCRAPER = False

import json
import json as _json
import os
import random
import re
import sys
import time
from urllib.parse import urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from source_plugin_base import SourcePlugin, ChapterResult

# ── Logging ────────────────────────────────────────────────────────────────────
try:
    from gs_logger import log as _gs_log
    log = _gs_log.legion
except Exception:
    class _NoopLog:
        def __getattr__(self, name): return lambda *a, **kw: None
    log = _NoopLog()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SCRAPER = cloudscraper.create_scraper() if CLOUDSCRAPER else None

class SourcePluginRegistry:
    def __init__(self):
        self._plugins: list[SourcePlugin] = []

    def register(self, plugin: SourcePlugin):
        self._plugins.append(plugin)

    def for_url(self, url: str) -> SourcePlugin | None:
        for p in self._plugins:
            if p.can_handle(url):
                return p
        return None

    def all_plugins(self) -> list[SourcePlugin]:
        return list(self._plugins)

    def load_user_plugins(self, directory: str):
        """Load .py files from a directory as plugins. Each must define
        a module-level `plugin` variable that is a SourcePlugin instance."""
        import importlib.util, pathlib
        for f in pathlib.Path(directory).glob("*.py"):
            try:
                spec = importlib.util.spec_from_file_location(f.stem, f)
                mod  = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "plugin") and isinstance(mod.plugin, SourcePlugin):
                    self.register(mod.plugin)
            except Exception as e:
                log.error("Failed to load user plugin", file=str(f), error=str(e))

plugin_registry = SourcePluginRegistry()

# Add project dir to sys.path to ensure sources can be imported
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Built-in plugins — one per source; each registers its own can_handle domain check
try:
    from sources.novelbin_plugin import plugin as _p; plugin_registry.register(_p)
except Exception as _e:
    log.error("Failed to load built-in plugin", module="novelbin_plugin", error=str(_e))
try:
    from sources.royalroad_plugin import plugin as _p; plugin_registry.register(_p)
except Exception as _e:
    log.error("Failed to load built-in plugin", module="royalroad_plugin", error=str(_e))
try:
    from sources.novelfire_plugin import plugin as _p; plugin_registry.register(_p)
except Exception as _e:
    log.error("Failed to load built-in plugin", module="novelfire_plugin", error=str(_e))
try:
    from sources.lightnovelpub_plugin import plugin as _p; plugin_registry.register(_p)
except Exception as _e:
    log.error("Failed to load built-in plugin", module="lightnovelpub_plugin", error=str(_e))
try:
    from sources.scribblehub_plugin import plugin as _p; plugin_registry.register(_p)
except Exception as _e:
    log.error("Failed to load built-in plugin", module="scribblehub_plugin", error=str(_e))
try:
    from sources.novelpub_plugin import plugin as _p; plugin_registry.register(_p)
except Exception as _e:
    log.error("Failed to load built-in plugin", module="novelpub_plugin", error=str(_e))
try:
    from sources.novelcool_plugin import plugin as _p; plugin_registry.register(_p)
except Exception as _e:
    log.error("Failed to load built-in plugin", module="novelcool_plugin", error=str(_e))
try:
    from sources.wuxiaworld_plugin import plugin as _p; plugin_registry.register(_p)
except Exception as _e:
    log.error("Failed to load built-in plugin", module="wuxiaworld_plugin", error=str(_e))

# User plugins from ~/.config/great-sage/sources/
_user_plugin_dir = os.path.expanduser("~/.config/great-sage/sources")
os.makedirs(_user_plugin_dir, exist_ok=True)
plugin_registry.load_user_plugins(_user_plugin_dir)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
]

def _generic_build_mirror_urls(url: str) -> list[str]:
    """
    Generates a list of potential mirror URLs for the given URL based on known domains.
    Prioritizes the original URL, then tries known mirror domains.
    Filters out recently failed mirrors.
    """
    parsed_url = urlparse(url)
    original_domain = parsed_url.netloc
    
    # Start with the original URL
    mirror_urls = [url]
    
    # Only add mirrors whose domain is related to the original.
    # Matching on a shared keyword (e.g. "novelbin") prevents unrelated sites
    # (novelfull, novelhall) being tried for sources like novelfire or boxnovel.
    def _domains_related(orig, candidate):
        keywords = ["novelbin", "novelfull", "novelhall", "boxnovel", "novelfire",
                    "royalroad", "wuxia", "lightnovel"]
        for kw in keywords:
            if kw in orig and kw in candidate:
                return True
        return False

    for domain in _DEFAULT_MIRROR_DOMAINS:
        if domain != original_domain and _domains_related(original_domain, domain):
            mirror_url = urlunparse(parsed_url._replace(netloc=domain))
            if mirror_url not in mirror_urls:
                mirror_urls.append(mirror_url)
                
    # Filter out recently failed mirrors and prepare for randomized selection
    now = time.time()
    available_mirrors = []
    
    # Add original URL if not marked as failed
    if original_domain not in _MIRROR_FAILURES or (now - _MIRROR_FAILURES[original_domain] > _MIRROR_FAIL_DURATION):
        available_mirrors.append(url)
    else:
        log.debug(f"Skipping original domain {original_domain} due to recent failure.")

    # Add other mirror URLs if not marked as failed
    for m_url in mirror_urls:
        if m_url == url: continue # Already handled original URL
        m_domain = urlparse(m_url).netloc
        if m_domain not in _MIRROR_FAILURES or (now - _MIRROR_FAILURES[m_domain] > _MIRROR_FAIL_DURATION):
            if m_url not in available_mirrors: # Avoid duplicates
                available_mirrors.append(m_url)
        else:
            log.debug(f"Skipping recently failed mirror: {m_domain} for {url}")

    if not available_mirrors: # If all mirrors are down or filtered, try original anyway as a last resort
        log.warning(f"All preferred and mirror URLs for {url} are currently marked as failed or unavailable. Re-attempting original URL.")
        available_mirrors.append(url)
    
    random.shuffle(available_mirrors) # Shuffle to distribute load and vary retry order
    
    return available_mirrors


def _warm_session(base_url: str):
    try:
        (SCRAPER or SESSION).get(base_url, timeout=10)
        time.sleep(0.5)
    except Exception:
        pass


def _generic_get_with_retry(url: str) -> tuple[requests.Response | None, str]:
    """
    Attempts to fetch a URL with retries, exponential backoff, jitter,
    User-Agent rotation, and mirror rotation.
    Returns (response, actual_url) on success, or (None, original_url) on final failure.
    """
    last_error_message = "Unknown error"
    retries = 0
    max_retries = 4 # Total 4 attempts (0, 1, 2, 3)
    original_url = url # Store original URL for final failure return

    while retries < max_retries:
        current_attempt_urls = _generic_build_mirror_urls(original_url)
        current_attempt_url = current_attempt_urls[0] # Take the first available after shuffling and filtering

        # 3. User-Agent Rotation
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": random.choice(["en-US,en;q=0.9", "es-ES,es;q=0.8", "fr-FR,fr;q=0.7", "en;q=0.9"]), # Rotate occasionally
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        }

        log.debug(f"Attempt {retries + 1}/{max_retries} for URL: {current_attempt_url} (original: {original_url})")
        log.debug(f"  Using User-Agent: {headers['User-Agent']}")

        try:
            # Existing _warm_session logic
            parsed = urlparse(current_attempt_url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            _warm_session(base)
            time.sleep(0.8) # Initial small delay before request

            resp = (SCRAPER or SESSION).get(current_attempt_url, timeout=15, headers=headers)
            
            # 1. Retry logic - Specific Status codes
            retry_status_codes = [429, 502, 503, 504, 408]
            if resp.status_code in retry_status_codes:
                last_error_message = f"HTTP {resp.status_code} - Server/Rate Limit Error"
                log.warning(f"{last_error_message} for {current_attempt_url}. Retrying...")
                _MIRROR_FAILURES[urlparse(current_attempt_url).netloc] = time.time() # Mark current mirror as failed
                
                retries += 1
                if retries >= max_retries: break # Exit if max retries reached

                # 2. Exponential backoff with jitter
                base_wait = 2 ** retries
                jitter = random.uniform(0, base_wait * 0.3)
                actual_wait = base_wait + jitter
                log.debug(f"Waiting for {actual_wait:.2f}s before next retry.")
                time.sleep(actual_wait)
                continue # Go to next retry attempt
            
            # 403 handling — detect Cloudflare challenge and fail fast
            if resp.status_code == 403:
                domain = urlparse(current_attempt_url).netloc
                _MIRROR_FAILURES[domain] = time.time()

                # Detect Cloudflare: CF-Ray header or challenge page body markers
                is_cloudflare = (
                    "cf-ray" in resp.headers or
                    "cloudflare" in resp.headers.get("server", "").lower() or
                    "Just a moment" in resp.text or
                    "cf-browser-verification" in resp.text or
                    "_cf_chl" in resp.text
                )

                if is_cloudflare:
                    # Try cloudscraper — one attempt only, it either works or it doesn't
                    cf_bypassed = False
                    if CLOUDSCRAPER:
                        scraper_instance = SCRAPER if SCRAPER else cloudscraper.create_scraper()
                        try:
                            resp2 = scraper_instance.get(current_attempt_url, timeout=30, headers=headers)
                            if resp2.status_code == 200:
                                log.debug(f"Cloudscraper bypassed Cloudflare for {current_attempt_url}")
                                return resp2, current_attempt_url
                            last_error_message = f"403 Forbidden — Cloudflare protected (cloudscraper status: {resp2.status_code})"
                        except Exception as cs_e:
                            last_error_message = f"403 Forbidden — Cloudflare protected (cloudscraper: {type(cs_e).__name__})"
                    else:
                        last_error_message = "403 Forbidden — Cloudflare protected (no cloudscraper)"

                    # Cloudflare blocks the whole domain — mark ALL known mirrors failed
                    # so we don't waste time trying them. Extend failure window to 30 min.
                    _cf_expire = time.time() + 1800  # 30 minutes from now
                    for mirror_domain in _DEFAULT_MIRROR_DOMAINS + [domain]:
                        _MIRROR_FAILURES[mirror_domain] = _cf_expire - _MIRROR_FAIL_DURATION
                    log.error(
                        f"All mirrors failed: 403 Forbidden — Cloudflare protected for {original_url}"
                    )
                    # Fail immediately — retrying other mirrors will also get 403
                    break

                else:
                    # Plain 403 (not Cloudflare) — retry with backoff as normal
                    last_error_message = f"403 Forbidden (non-Cloudflare) for {current_attempt_url}"
                    log.warning(f"{last_error_message}. Retrying...")
                    retries += 1
                    if retries >= max_retries: break
                    base_wait = 2 ** retries
                    jitter = random.uniform(0, base_wait * 0.3)
                    time.sleep(base_wait + jitter)
                    continue
            
            resp.raise_for_status() # Raises HTTPError for other bad responses (4xx or 5xx)
            log.info(f"Successfully fetched {current_attempt_url}")
            return resp, current_attempt_url

        # 1. Retry logic - Requests exceptions
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error_message = f"Network error ({type(e).__name__}): {str(e)}"
            log.warning(f"{last_error_message} for {current_attempt_url}. Retrying...")
            _MIRROR_FAILURES[urlparse(current_attempt_url).netloc] = time.time() # Mark domain as failed
            
            retries += 1
            if retries >= max_retries: break # Exit if max retries reached

            # 2. Exponential backoff with jitter
            base_wait = 2 ** retries
            jitter = random.uniform(0, base_wait * 0.3)
            actual_wait = base_wait + jitter
            log.debug(f"Waiting for {actual_wait:.2f}s before next retry.")
            time.sleep(actual_wait)
            continue # Go to next retry attempt
        except requests.exceptions.RequestException as e: # Catch other request-related errors (e.g., HTTPError from raise_for_status for non-retryable codes)
            last_error_message = f"Request failed ({type(e).__name__}): {str(e)}"
            log.warning(f"{last_error_message} for {current_attempt_url}. Retrying...")
            _MIRROR_FAILURES[urlparse(current_attempt_url).netloc] = time.time() # Mark domain as failed
            
            retries += 1
            if retries >= max_retries: break # Exit if max retries reached

            base_wait = 2 ** retries
            jitter = random.uniform(0, base_wait * 0.3)
            actual_wait = base_wait + jitter
            log.debug(f"Waiting for {actual_wait:.2f}s before next retry.")
            time.sleep(actual_wait)
            continue # Go to next retry attempt
        except Exception as e: # Catch any other unexpected errors
            last_error_message = f"Unexpected error ({type(e).__name__}): {str(e)}"
            log.error(f"{last_error_message} for {current_attempt_url}. Retrying...")
            _MIRROR_FAILURES[urlparse(current_attempt_url).netloc] = time.time() # Mark domain as failed
            
            retries += 1
            if retries >= max_retries: break # Exit if max retries reached

            base_wait = 2 ** retries
            jitter = random.uniform(0, base_wait * 0.3)
            actual_wait = base_wait + jitter
            log.debug(f"Waiting for {actual_wait:.2f}s before next retry.")
            time.sleep(actual_wait)
            continue # Go to next retry attempt
    
    log.error(f"Final failure for {original_url} after {retries} retries. Last error: {last_error_message}")
    return None, original_url # Return None and original URL on final failure



# ── Content validation ─────────────────────────────────────────────────────────

_JS_CHALLENGE_PATTERNS = [
    r"window\.location\.replace\s*\(",
    r"<title>\s*Loading\.\.\.\s*</title>",
    r"Checking your browser",
    r"DDoS protection by",
    r"Please wait while we verify",
]

_NAV_GARBAGE_STRINGS = [
    "Novel Bin", "Novel List", "Latest Release", "Hot Novel",
    "Completed Novel", "Most Popular", "Light gray", "Light blue",
    "Light yellow", "Wood grain", "Palatino Linotype", "Bookerly",
    "Font family", "Font size", "Line height", "Full frame",
    "Login/Signup",
]

def _is_js_challenge(html: str) -> bool:
    if len(html) < 2000:
        for pat in _JS_CHALLENGE_PATTERNS:
            if re.search(pat, html, re.I):
                return True
    return False

def _is_nav_garbage(paragraphs: list) -> bool:
    if not paragraphs:
        return False
    sample = " ".join(paragraphs[:20])
    hits = sum(1 for s in _NAV_GARBAGE_STRINGS if s in sample)
    return hits >= 4

def _delete_book_library(book_name: str):
    try:
        safe = re.sub(r'[^\w\-_\. ]', '_', book_name)
        book_dir = os.path.join(LIBRARY_DIR, safe)
        if os.path.exists(book_dir):
            import shutil as _shutil
            _shutil.rmtree(book_dir)
            log.warning("Deleted corrupt library folder", book=book_name, path=book_dir)
    except Exception as e:
        log.error("Failed to delete corrupt library folder", book=book_name, error=str(e))

# ── Source mirror finder ───────────────────────────────────────────────────────

_MIRROR_SEARCH_SOURCES = [
    "novelfire",
    "lightnovelpub",
    "novelpub",
    "scribblehub",
    "novelcool",
]

def _find_mirror_source(book_name: str, current_url: str):
    """
    When the primary source is blocking scrapers, search other sources for
    the same book. Returns a working chapter-1 URL or None.
    """
    import difflib
    title_query = book_name.strip()
    log.info("Searching for mirror source", book=book_name, trying=_MIRROR_SEARCH_SOURCES)

    for src_id in _MIRROR_SEARCH_SOURCES:
        if src_id in current_url:
            continue
        plugin = None
        for p in plugin_registry.all_plugins():
            if p.id == src_id:
                plugin = p
                break
        if not plugin or not getattr(plugin, "supports_search", False):
            continue
        try:
            results = plugin.search(title_query, SESSION)
            if not results:
                continue
            best = max(results, key=lambda r: difflib.SequenceMatcher(
                None, r.title.lower(), title_query.lower()).ratio())
            ratio = difflib.SequenceMatcher(
                None, best.title.lower(), title_query.lower()).ratio()
            if ratio < 0.6:
                log.debug("Mirror search: weak match", source=src_id,
                          matched=best.title, ratio=round(ratio, 2))
                continue
            log.info("Mirror candidate found", source=src_id,
                     matched=best.title, ratio=round(ratio, 2), url=best.url)
            # Use plugin's get_first_chapter_url if available (needed for sites
            # like NovelCool where chapter URLs contain an opaque numeric ID).
            # Fall back to appending /chapter-1 for simpler URL schemes.
            if hasattr(plugin, "get_first_chapter_url"):
                ch1_url = plugin.get_first_chapter_url(best.url, SESSION)
                if not ch1_url:
                    log.debug("get_first_chapter_url returned None", source=src_id)
                    continue
            else:
                ch1_url = best.url.rstrip('/') + '/chapter-1'
            try:
                test_result = fetch_chapter(ch1_url)
                t_paras = test_result[1]
                if t_paras and not _is_nav_garbage(t_paras) and len(t_paras) > 3:
                    log.info("Mirror source verified", source=src_id,
                             chapter_url=ch1_url, paragraphs=len(t_paras))
                    return ch1_url
            except Exception as e:
                log.debug("Mirror chapter fetch failed", source=src_id, error=str(e))
        except Exception as e:
            log.warning("Mirror search failed", source=src_id, error=str(e))

    log.warning("No working mirror source found", book=book_name)
    return None


def _fetch_chapter_generic(url: str):
    # Hardcoded mirrors for the generic fallback (original NovelBin mirrors)
    GENERIC_MIRRORS = ["novelbin.com"]
    GENERIC_WATERMARKS = [
        r"(?i)visit\s+\S+\s+for\s+(more|latest|updates?).*",
        r"(?i)read\s+(at|on)\s+\S+\s+for.*",
        r"(?i)support\s+the\s+(author|translator)\s+at\s+\S+.*",
        r"(?i)this\s+chapter\s+(is|was)\s+(stolen|taken)\s+from.*",
        r"(?i)find\s+(this|more)\s+(story|chapter|content)\s+(at|on)\s+\S+.*",
        r"(?i)novelbin\.(me|com|net)\S*",
        r"(?i)\[.*?novelbin.*?\]",
        r"(?i)^total\s+responses?\s*:\s*\d+$",
        r"(?i)^responses?\s*:\s*\d+$",
        r"(?i)^\d+\s+comments?$",
        r"(?i)^(load|show)\s+more\s+comments?.*",
        r"(?i)^leave\s+a\s+(reply|comment).*",
        r"(?i)^(sponsored|advertisement|advert)\b.*",
        r"(?i)^your\s+email\s+address\s+will\s+not\s+be\s+published.*",
    ]

    def _local_build_mirrors(u):
        parsed = urlparse(u)
        host = parsed.netloc
        urls = [u]
        for m in GENERIC_MIRRORS:
            if m != host:
                urls.append(u.replace(host, m, 1))
        return urls

    def _local_get_with_retry(u):
        urls = _local_build_mirrors(u)
        last_err = "Unknown error"
        for att_url in urls:
            parsed = urlparse(att_url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            _warm_session(base)
            time.sleep(0.8)
            try:
                resp = (SCRAPER or SESSION).get(att_url, timeout=15)
                if resp.status_code == 403:
                    if CLOUDSCRAPER and not SCRAPER:
                        try:
                            cs = cloudscraper.create_scraper()
                            resp2 = cs.get(att_url, timeout=20)
                            if resp2.status_code == 200: return resp2, att_url
                        except Exception: pass
                    elif SCRAPER:
                        try:
                            time.sleep(2)
                            resp2 = SCRAPER.get(att_url, timeout=30)
                            if resp2.status_code == 200: return resp2, att_url
                        except Exception: pass
                    last_err = f"403 Forbidden — Cloudflare protected"
                    continue
                resp.raise_for_status()
                return resp, att_url
            except Exception as e:
                last_err = str(e)
                continue
        raise requests.RequestException(f"{last_err} — All mirrors failed.")

    try:
        resp, actual_url = _local_get_with_retry(url)
    except Exception as e:
        log.error("_fetch_chapter_generic failed", url=url, error=str(e))
        return None, [], None, None, str(e), None

    if _is_js_challenge(resp.text):
        return "Chapter", [], None, None, "Site returned a bot-challenge page — cannot scrape without a browser.", None

    soup = BeautifulSoup(resp.text, "html.parser")
    title = "Chapter"
    for tag_name, attrs in [
        ("span", {"class": "chr-text"}),
        ("h2",   {"class": "chr-title"}),
        ("h1",   {"class": "chapter-title"}),
        ("h4",   {"class": "panel-title"}),   # wuxiaworld
        ("h1",   {}),
        ("h2",   {}),
    ]:
        tag = soup.find(tag_name, attrs)
        if tag:
            title = tag.get_text(strip=True)
            break

    content_div = None
    # Try by id first (exact matches for known sources)
    for id_sel in ["chr-content", "chapter-content", "chp_raw", "chapter_content", "content"]:
        content_div = soup.find("div", id=id_sel)
        if content_div:
            break
    # Try by class (novelfire, novelpub, wuxiaworld all use div.chapter-content)
    if not content_div:
        for cls_sel in ["chapter-content", "chapter-inner", "reading-content", "text-left"]:
            content_div = soup.find("div", class_=cls_sel)
            if content_div:
                break
    # Last resort: largest div by paragraph count
    if not content_div:
        divs = soup.find_all("div")
        content_div = max(divs, key=lambda d: len(d.find_all("p")), default=None)
    
    if not content_div:
        return title, [], None, None, "Could not locate content.", None

    for junk in content_div(["script", "style", "iframe", "ins", "noscript"]):
        junk.decompose()

    raw_paragraphs = [p.get_text(separator=" ").strip() for p in content_div.find_all("p") if p.get_text(strip=True)]
    if not raw_paragraphs:
        raw_text = content_div.get_text(separator="\n").strip()
        raw_paragraphs = [l.strip() for l in raw_text.splitlines() if l.strip()]

    paragraphs = [p for p in raw_paragraphs if not any(re.search(pat, p) for pat in GENERIC_WATERMARKS)]

    parsed = urlparse(actual_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    next_url = _extract_nav(soup, "next_chap", base)
    prev_url = _extract_nav(soup, "prev_chap", base)

    url_ch_num = None
    m_url = re.search(r'/chapter-([0-9]+)', actual_url)
    if m_url:
        url_ch_num = int(m_url.group(1))

    # Arithmetic fallback for sites that render nav via JS (e.g. NovelBin).
    # If _extract_nav returned None but we know the chapter number from the URL,
    # construct next/prev by incrementing/decrementing the slug.
    if url_ch_num is not None:
        ch_prefix = actual_url[:actual_url.index(m_url.group(0))]
        if not next_url:
            candidate = f"{ch_prefix}/chapter-{url_ch_num + 1}"
            next_url = candidate  # optimistic — download loop will 404 and stop naturally
        if not prev_url and url_ch_num > 1:
            prev_url = f"{ch_prefix}/chapter-{url_ch_num - 1}"

    return title, paragraphs, next_url, prev_url, None, url_ch_num

def fetch_chapter(url: str, book_name: str = ""):
    plugin = plugin_registry.for_url(url)
    if plugin:
        scraper = SCRAPER if plugin.supports_cloudflare else None
        result  = plugin.fetch_chapter(url, SESSION, scraper)
        if not result.error:
            result.paragraphs = plugin.clean_content(result.paragraphs)
            return result.title, result.paragraphs, result.next_url, result.prev_url, None, result.chapter_num
        # Plugin failed — try mirror source if we have a book name
        log.error("Plugin fetch failed, trying mirror source",
                  url=url, plugin=plugin.id, error=result.error)
        if book_name:
            mirror_url = _find_mirror_source(book_name, url)
            if mirror_url:
                mirror_plugin = plugin_registry.for_url(mirror_url)
                if mirror_plugin:
                    scraper2 = SCRAPER if mirror_plugin.supports_cloudflare else None
                    r2 = mirror_plugin.fetch_chapter(mirror_url, SESSION, scraper2)
                    if not r2.error:
                        r2.paragraphs = mirror_plugin.clean_content(r2.paragraphs)
                        log.info("Mirror source fetch succeeded",
                                 mirror_url=mirror_url, plugin=mirror_plugin.id)
                        return r2.title, r2.paragraphs, r2.next_url, r2.prev_url, None, r2.chapter_num
                else:
                    # Mirror URL not covered by any plugin — fall back to generic
                    log.info("Mirror URL has no plugin, using generic fetch", mirror_url=mirror_url)
                    return _fetch_chapter_generic(mirror_url)
        return "", [], None, None, result.error, None
    else:
        return _fetch_chapter_generic(url)


def truncate_text(text: str, max_length: int = 60) -> str:
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[:max_length-3] + "..."


# ── Metadata ──────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()


def _is_noise(text: str) -> bool:
    noise = re.compile(
        r'more from|follow|bookmark|add to|all novel|read more|'
        r'latest chapter|chapter list|table of content|^genres?$|^tags?$|'
        r'^author$|^status$|^rating$|^views?$',
        re.I
    )
    return bool(noise.search(text)) or len(text) > 120


def _extract_json_ld(soup) -> dict:
    meta = {}
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(tag.string or '')
            if not isinstance(data, dict):
                continue
            if data.get('@type') in ('Book', 'CreativeWork', 'WebPage', 'Article'):
                if data.get('name') and not meta.get('title'):
                    meta['title'] = _clean(data['name'])
                if data.get('author'):
                    author = data['author']
                    if isinstance(author, dict):
                        author = author.get('name', '')
                    elif isinstance(author, list):
                        author = ', '.join(a.get('name', '') for a in author if isinstance(a, dict))
                    if author and not _is_noise(str(author)):
                        meta['author'] = _clean(str(author))
                if data.get('genre') and not meta.get('genres'):
                    g = data['genre']
                    if isinstance(g, list):
                        g = ', '.join(g)
                    meta['genres'] = _clean(str(g))
                if data.get('description') and not meta.get('synopsis'):
                    meta['synopsis'] = _clean(data['description'])
        except Exception:
            pass
    return meta


def _extract_og_meta(soup) -> dict:
    meta = {}
    for tag in soup.find_all('meta'):
        prop = tag.get('property', '') or tag.get('name', '')
        content = _clean(tag.get('content', ''))
        if not content:
            continue
        if prop in ('og:title', 'twitter:title') and not meta.get('title'):
            meta['title'] = content
        if prop in ('og:description', 'description', 'twitter:description') and not meta.get('synopsis'):
            meta['synopsis'] = content
    return meta


LABEL_MAP = {
    'author': 'author', 'writer': 'author',
    'genre': 'genres', 'genres': 'genres',
    'category': 'genres', 'categories': 'genres',
    'status': 'status',
    'alternative': 'alternative_names', 'other name': 'alternative_names',
    'other names': 'alternative_names', 'alt name': 'alternative_names',
    'tag': 'tags', 'tags': 'tags',
    'source': 'source', 'translator': 'translator',
    'year': 'year', 'release': 'year',
    'type': 'novel_type',
}


def _scrape_info_box(soup) -> dict:
    meta = {}
    for li in soup.find_all('li'):
        label_tag = li.find(['label', 'h3', 'h4', 'strong', 'span'], recursive=False)
        if not label_tag:
            continue
        label_text = _clean(label_tag.get_text()).rstrip(':').lower()
        key = LABEL_MAP.get(label_text)
        if not key or meta.get(key):
            continue
        value_parts = []
        for child in li.children:
            if child is label_tag:
                continue
            if hasattr(child, 'get_text'):
                t = _clean(child.get_text(separator=', '))
                if t and not _is_noise(t):
                    value_parts.append(t)
            else:
                t = _clean(str(child))
                if t and not _is_noise(t):
                    value_parts.append(t)
        value = ', '.join(v for v in value_parts if v).strip(', ')
        if value:
            meta[key] = value
    if meta:
        return meta
    for elem in soup.find_all(['div', 'p', 'span', 'td']):
        text = _clean(elem.get_text(separator=': '))
        for label_pat, key in LABEL_MAP.items():
            if meta.get(key):
                continue
            m = re.match(rf'^{re.escape(label_pat)}\s*:\s*(.+)$', text, re.I)
            if m:
                value = _clean(m.group(1))
                if value and not _is_noise(value) and len(value) < 300:
                    meta[key] = value
                    break
    return meta


_SYNOPSIS_SELECTORS = [
    ('div', {'id': 'novel-desc'}),
    ('div', {'id': 'synopsis'}),
    ('div', {'id': 'description'}),
    ('div', {'class': 'desc-text'}),
    ('div', {'class': 'summary__content'}),
    ('div', {'class': 'synopsis'}),
    ('div', {'class': 'description'}),
    ('div', {'class': 'summary'}),
    ('div', {'class': 'novel-desc'}),
    ('div', {'class': 'book-desc'}),
    ('div', {'class': 'detail-desc'}),
    ('div', {'class': 'desc'}),
]


def _extract_synopsis(soup):
    for tag_name, attrs in _SYNOPSIS_SELECTORS:
        tag = soup.find(tag_name, attrs)
        if tag:
            for junk in tag(['script', 'style', 'button', 'iframe', 'ins', 'noscript']):
                junk.decompose()
            for span in tag.find_all(['span', 'a']):
                if re.search(r'read more|show (less|more)|collapse', _clean(span.get_text()), re.I):
                    span.decompose()
            text = _clean(tag.get_text(separator=' '))
            text = re.sub(r'^(synopsis|description|summary|about)\s*[:\-]?\s*', '', text, flags=re.I)
            if len(text) > 60:
                return text[:2000]
    candidates = []
    for div in soup.find_all('div'):
        ps = div.find_all('p', recursive=False)
        if len(ps) < 2:
            continue
        text = _clean(div.get_text(separator=' '))
        if 80 < len(text) < 3000:
            candidates.append(text)
    if candidates:
        candidates.sort(key=len)
        return candidates[0][:2000]
    return None


def fetch_book_metadata(chapter_url: str) -> dict:
    plugin = plugin_registry.for_url(chapter_url)
    if plugin:
        try:
            metadata = plugin.fetch_metadata(chapter_url, SESSION)
            if metadata and metadata.title:
                return {
                    "title": metadata.title,
                    "author": metadata.author,
                    "status": metadata.status,
                    "genres": ", ".join(metadata.genres),
                    "synopsis": metadata.synopsis,
                    "cover_url": metadata.cover_url
                }
        except Exception as e:
            log.error("Plugin fetch_metadata failed", plugin=plugin.id, error=str(e))

    try:
        parsed = urlparse(chapter_url)
        path_parts = parsed.path.strip('/').split('/')
        candidates = []
        if len(path_parts) >= 2:
            candidates.append(f"{parsed.scheme}://{parsed.netloc}/{path_parts[0]}/{path_parts[1]}")
            candidates.append(f"{parsed.scheme}://{parsed.netloc}/{path_parts[0]}/{path_parts[1]}/")
        if len(path_parts) >= 1:
            candidates.append(f"{parsed.scheme}://{parsed.netloc}/{path_parts[0]}")
            candidates.append(f"{parsed.scheme}://{parsed.netloc}/{path_parts[0]}/")
            slug = path_parts[0].rstrip('.html')
            candidates.append(f"{parsed.scheme}://{parsed.netloc}/{slug}.html")
        resp = None
        for url in dict.fromkeys(candidates):
            try:
                r, _ = _generic_get_with_retry(url)
                if r and r.status_code == 200 and len(r.text) > 2000:
                    resp = r
                    break
            except Exception:
                continue
        if not resp:
            return {}
        soup = BeautifulSoup(resp.text, 'html.parser')
        meta = {}
        meta.update(_extract_json_ld(soup))
        for k, v in _extract_og_meta(soup).items():
            if k not in meta:
                meta[k] = v
        for k, v in _scrape_info_box(soup).items():
            if not meta.get(k):
                meta[k] = v
        syn = _extract_synopsis(soup)
        if syn:
            current_synopsis = meta.get('synopsis', '')
            if len(syn) > len(current_synopsis):
                meta['synopsis'] = syn
        for k in list(meta.keys()):
            if isinstance(meta[k], str):
                meta[k] = re.sub(r'&[a-z]+;', ' ', meta[k]).strip()
                if not meta[k]:
                    del meta[k]
        return meta
    except Exception as e:
        log.error("fetch_book_metadata failed", url=chapter_url, error=str(e))
        return {}


def _resolve_novelbin_first_chapter(book_page_url: str) -> str | None:
    """
    NovelBin loads its chapter list via JavaScript (AJAX).
    The server-side endpoint is POST /ajax/chapter-archive with the novel ID
    embedded in a data-novel-id attribute on the book landing page.
    Falls back to constructing /b/{slug}/chapter-1 if the AJAX call fails.
    """
    try:
        resp, actual_url = _generic_get_with_retry(book_page_url)
        soup = BeautifulSoup(resp.text, "html.parser")
        parsed = urlparse(actual_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # Try to find the novel ID for the AJAX endpoint
        novel_id = None
        # Common selectors where NovelBin embeds the novel ID
        for sel in [
            "[data-novel-id]",
            "#rating[data-novel-id]",
            ".rating[data-novel-id]",
            "#chapter-list-page[data-novel-id]",
            "div[data-novel-id]",
        ]:
            el = soup.select_one(sel)
            if el and el.get("data-novel-id"):
                novel_id = el["data-novel-id"]
                break

        if novel_id:
            ajax_url = f"{base}/ajax/chapter-archive"
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": actual_url,
                }
                r = SESSION.post(ajax_url, data={"novel_id": novel_id}, headers=headers, timeout=15)
                if r.ok:
                    archive_soup = BeautifulSoup(r.text, "html.parser")
                    # Chapter links are in <a href="/b/slug/chapter-N"> — get sorted list
                    links = archive_soup.find_all("a", href=re.compile(r"/chapter-\d+", re.I))
                    if links:
                        def _ch_num(a):
                            m = re.search(r"chapter-(\d+)", a["href"], re.I)
                            return int(m.group(1)) if m else 999999
                        first = min(links, key=_ch_num)
                        href = first["href"]
                        return href if href.startswith("http") else base + href
            except Exception as e:
                log.warning("NovelBin AJAX chapter-archive failed", novel_id=novel_id, error=str(e))

        # Fallback: construct chapter-1 URL from the slug
        # novelbin.com/b/book-slug → novelbin.com/b/book-slug/chapter-1
        m = re.search(r"/b/([^/?#]+)", actual_url)
        if m:
            slug = m.group(1)
            # Strip any trailing chapter segment already in the URL
            slug = re.sub(r"/chapter-\d+.*$", "", slug)
            candidate = f"{base}/b/{slug}/chapter-1"
            try:
                test_resp, _ = _generic_get_with_retry(candidate)
                if test_resp.status_code < 400:
                    return candidate
            except Exception:
                pass

        return None
    except Exception as e:
        log.warning("_resolve_novelbin_first_chapter failed", url=book_page_url, error=str(e))
        return None


def resolve_first_chapter_url(book_page_url: str) -> str | None:
    """
    Given a book landing page URL, resolve the first chapter URL.
    This scrapes the book page and looks for 'Read' or 'Start Reading' links.
    Returns the first chapter URL or None if not found.
    """
    try:
        # Check if plugin can handle this URL
        plugin = plugin_registry.for_url(book_page_url)
        if plugin and hasattr(plugin, 'resolve_first_chapter'):
            try:
                return plugin.resolve_first_chapter(book_page_url, SESSION)
            except Exception as e:
                log.warning("Plugin resolve_first_chapter failed", url=book_page_url, error=str(e))

        # NovelBin-specific: chapter list is AJAX-loaded, use the chapter-archive endpoint
        if re.search(r"novelbin\.(com|net|me)", book_page_url, re.I):
            first = _resolve_novelbin_first_chapter(book_page_url)
            if first:
                return first
            # Fall through to generic if AJAX endpoint fails

        # NovelFull-specific: append ?page=1&per-page=50 to force sequential catalog
        # from Chapter 1 instead of the default trending/latest feed layout.
        if re.search(r"novelfull\.(net|com)", book_page_url, re.I):
            try:
                # Strip any existing query string from the book page URL first
                nf_base_url = book_page_url.split("?")[0]
                nf_index_url = f"{nf_base_url}?page=1&per-page=50"
                resp_nf = (SCRAPER or SESSION).get(nf_index_url, timeout=10)
                if resp_nf.status_code == 200:
                    soup_nf = BeautifulSoup(resp_nf.text, "html.parser")
                    parsed_nf = urlparse(book_page_url)
                    nf_domain = f"{parsed_nf.scheme}://{parsed_nf.netloc}"
                    # ul.list-chapter holds the ordered chapter index
                    chapters_nf = soup_nf.select("ul.list-chapter li a[href]")
                    if chapters_nf:
                        # Sort numerically by chapter number to guarantee Chapter 1
                        def _nf_ch_num(a):
                            m = re.search(r"chapter[-/]?(\d+)", a.get("href", ""), re.I)
                            return int(m.group(1)) if m else 999999
                        chapters_nf.sort(key=_nf_ch_num)
                        href = chapters_nf[0]["href"]
                        return href if href.startswith("http") else nf_domain + href
            except Exception as _nf_e:
                log.warning("NovelFull chapter-1 resolve failed, falling back to generic",
                            url=book_page_url, error=str(_nf_e))
            # Fall through to generic if the above fails

        # Generic fallback: scrape the book page for first chapter link
        resp, actual_url = _generic_get_with_retry(book_page_url)
        soup = BeautifulSoup(resp.text, "html.parser")
        parsed = urlparse(actual_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # Look for common "Read" or "Start Reading" button patterns
        read_patterns = [
            r"read\s+now",
            r"start\s+reading",
            r"read\s+first\s+chapter",
            r"begin\s+reading",
            r"^read$",
            r"^start$",
        ]

        # 1. Look for buttons/links with read patterns
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            for pat in read_patterns:
                if re.search(pat, text, re.I):
                    href = a["href"]
                    if href:
                        # Make absolute URL
                        if href.startswith("http"):
                            return href
                        elif href.startswith("/"):
                            return base + href
                        else:
                            return base + "/" + href

        # 2. Look for chapter list and get first chapter
        chapter_selectors = [
            ("a", {"class": re.compile(r"chapter|chap", re.I)}),
            ("a", {"href": re.compile(r"/chapter[-/]?", re.I)}),
            ("a", {"href": re.compile(r"chapter-\d+", re.I)}),
        ]

        for tag_name, attrs in chapter_selectors:
            links = soup.find_all(tag_name, attrs)
            if links:
                # Sort by href to get chapter 1 or earliest
                def chapter_sort_key(a):
                    href = a.get("href", "")
                    m = re.search(r"chapter[-/]?(\d+)", href, re.I)
                    if m:
                        return int(m.group(1))
                    return 999999

                sorted_links = sorted(links, key=chapter_sort_key)
                if sorted_links:
                    first_ch = sorted_links[0]["href"]
                    if first_ch.startswith("http"):
                        return first_ch
                    elif first_ch.startswith("/"):
                        return base + first_ch
                    else:
                        return base + "/" + first_ch

        # 3. Look for table of contents links
        toc_selectors = [
            ("a", {"href": re.compile(r"toc|contents|chapters|list", re.I)}),
            ("a", {"class": re.compile(r"toc|contents|chapters|list", re.I)}),
        ]

        for tag_name, attrs in toc_selectors:
            toc_link = soup.find(tag_name, attrs)
            if toc_link:
                href = toc_link["href"]
                if href:
                    toc_url = href if href.startswith("http") else (base + href if href.startswith("/") else base + "/" + href)
                    # Try to get first chapter from TOC page
                    try:
                        toc_resp, _ = _generic_get_with_retry(toc_url)
                        toc_soup = BeautifulSoup(toc_resp.text, "html.parser")
                        for sel in chapter_selectors:
                            links = toc_soup.find_all(*sel)
                            if links:
                                sorted_links = sorted(links, key=lambda a: int(re.search(r"chapter[-/]?(\d+)", a.get("href", ""), re.I).group(1)) if re.search(r"chapter[-/]?(\d+)", a.get("href", ""), re.I) else 999999)
                                if sorted_links:
                                    first_ch = sorted_links[0]["href"]
                                    if first_ch.startswith("http"):
                                        return first_ch
                                    elif first_ch.startswith("/"):
                                        return base + first_ch
                                    else:
                                        return base + "/" + first_ch
                    except Exception:
                        pass

        return None
    except Exception as e:
        log.error("resolve_first_chapter_url failed", url=book_page_url, error=str(e))
        return None


def parse_novelbin(soup): return _scrape_info_box(soup)
def parse_novelfull(soup): return _scrape_info_box(soup)
def parse_wuxiaworld(soup): return _scrape_info_box(soup)
def parse_royalroad(soup): return _scrape_info_box(soup)
def parse_generic(soup): return _scrape_info_box(soup)
def extract_synopsis_fallback(soup): return _extract_synopsis(soup)


def extract_book_title_from_chapter(url: str) -> str:
    try:
        resp, _ = _generic_get_with_retry(url)
        soup = BeautifulSoup(resp.text, "html.parser")
        breadcrumb = soup.find("ol", class_="breadcrumb")
        if breadcrumb:
            for li in breadcrumb.find_all("li"):
                a = li.find("a")
                if a and "novel" in a.get("href", ""):
                    return a.get_text(strip=True)
        for tag in soup.find_all(["h1", "h2", "h3"]):
            text = tag.get_text(strip=True)
            if text and len(text) < 100 and "chapter" not in text.lower():
                return text
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
            for sep in [" – ", " - ", " | ", " — "]:
                if sep in title:
                    parts = title.split(sep)
                    return parts[-1].strip()
            return title
    except Exception:
        pass
    return None


def _extract_nav(soup, link_id, base):
    def _make_abs(href):
        if not href or href.startswith("javascript") or href in ("#", "null", "undefined"):
            return None
        return href if href.startswith("http") else base + (href if href.startswith("/") else "/" + href)

    is_next = "next" in link_id

    # 1. Exact id match (original behaviour)
    tag = soup.find("a", id=link_id)
    if tag and tag.get("href"):
        return _make_abs(tag["href"])

    # 2. Common id variants
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

    # 3. Common class variants
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

    # 4. rel="next" / rel="prev"
    rel = "next" if is_next else "prev"
    tag = soup.find("a", rel=re.compile(rf"\b{rel}\b", re.I))
    if tag and tag.get("href"):
        return _make_abs(tag["href"])

    # 5. Link text matching
    text_patterns = (
        [r"next\s*chapter", r"^next$", r"next\s*>", r">>", r"next\s*ep"]
        if is_next else
        [r"prev\s*chapter", r"previous\s*chapter", r"^prev$", r"^previous$", r"<\s*prev", r"<<"]
    )
    for a in soup.find_all("a", href=True):
        txt = a.get_text(strip=True)
        for pat in text_patterns:
            if re.search(pat, txt, re.I):
                href = _make_abs(a["href"])
                if href: return href

    # 6. aria-label
    label = "next" if is_next else "prev"
    for a in soup.find_all("a", href=True):
        aria = (a.get("aria-label","") or a.get("title","")).lower()
        if label in aria and ("chapter" in aria or "page" in aria or aria == label):
            href = _make_abs(a["href"])
            if href: return href

    return None


def get_download_status_text(book):
    state = book.get('download_state', {})
    status = state.get('status', 'idle')
    if status == 'downloading':
        downloaded = state.get('total_chapters_downloaded', 0)
        return f"[cyan]⏳ Downloading... ({downloaded} chapters)[/cyan]"
    elif status == 'completed':
        downloaded = state.get('total_chapters_downloaded', 0)
        path = state.get('download_path', '')
        filename = os.path.basename(path) if path else get_book_filename(book.get('book_title', 'Unknown'))
        return f"[green]✅ Downloaded ({downloaded} chapters) - {filename}[/green]"
    elif status == 'paused':
        downloaded = state.get('total_chapters_downloaded', 0)
        return f"[yellow]⏸️ Paused ({downloaded} chapters downloaded)[/yellow]"
    elif status == 'queued':
        return "[blue]⏳ Queued for download...[/blue]"
    elif status == 'failed':
        failed = len(state.get('failed_chapters', []))
        return f"[red]❌ Download failed ({failed} chapters failed)[/red]"
    elif status == 'cancelled':
        return "[red]❌ Download cancelled[/red]"
    return None


def get_book_filename(book_name):
    return re.sub(r'[^\w\-_\. ]', '_', book_name) + ".txt"

LIBRARY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library")

def get_book_path(book_name):
    """Return the full path to a book's .txt file under library/{name}/{name}.txt"""
    safe = re.sub(r'[^\w\-_\. ]', '_', book_name)
    book_dir = os.path.join(LIBRARY_DIR, safe)
    os.makedirs(book_dir, exist_ok=True)
    return os.path.join(book_dir, safe + ".txt")


def append_chapter_to_file(book_name, chapter_num, title, paragraphs):
    save_path = get_book_path(book_name)
    real_num = chapter_num
    m = re.search(r'chapter[\s\-_]*(\d+)', title, re.IGNORECASE)
    if m:
        real_num = int(m.group(1))
    paragraphs = [p for p in paragraphs if not _is_junk_paragraph(p)]
    if not paragraphs:
        log.debug("append_chapter_to_file: skipped — no real content after junk filter",
                  book=book_name, chapter=real_num)
        return
    try:
        with open(save_path, 'a', encoding='utf-8') as f:
            f.write(f"\n\n{'='*60}\n")
            f.write(f"Chapter {real_num}: {title}\n")
            f.write(f"{'='*60}\n\n")
            for p in paragraphs:
                f.write(p + "\n\n")
        log.debug("Chapter appended to file", book=book_name, chapter=real_num)
    except Exception as e:
        log.error("append_chapter_to_file failed", book=book_name, chapter=real_num, error=str(e))


def get_chapter_from_file(book_name: str, chapter_num: int):
    """
    Try to load a chapter from the local .txt file.
    Returns (title, paragraphs) or (None, None) if not found.
    """
    try:
        save_path = get_book_path(book_name)

        if not os.path.exists(save_path):
            return None, None

        with open(save_path, 'r', encoding='utf-8') as f:
            raw = f.read()

        # Split into chapter blocks on the === separator
        blocks = re.split(r'={50,}', raw)
        for i in range(len(blocks) - 1):
            header = blocks[i].strip()
            body   = blocks[i + 1].strip() if i + 1 < len(blocks) else ""

            # Header line: "Chapter 5: Some Title"
            m = re.match(r'Chapter\s+(\d+)\s*[:\-]?\s*(.*)', header, re.IGNORECASE)
            if m and int(m.group(1)) == chapter_num:
                title      = m.group(2).strip() or f"Chapter {chapter_num}"
                paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
                if paragraphs:
                    return title, paragraphs

    except Exception as e:
        log.error("get_chapter_from_file failed", book=book_name, chapter=chapter_num, error=str(e))
    return None, None


def read_chapters_around(book_name: str, chapter_num: int, n: int = 5) -> str:
    """
    Read n chapters around chapter_num from the local .txt file.
    Returns concatenated text of those chapters, or empty string if not found.
    """
    try:
        save_path  = get_book_path(book_name)
        if not os.path.exists(save_path):
            return ""
        with open(save_path, 'r', encoding='utf-8') as f:
            raw = f.read()
        blocks = re.split(r'={50,}', raw)
        chapters = []
        for i in range(len(blocks) - 1):
            header = blocks[i].strip()
            body   = blocks[i + 1].strip() if i + 1 < len(blocks) else ""
            m = re.match(r'Chapter\s+(\d+)\s*[:\-]?\s*(.*)', header, re.IGNORECASE)
            if m:
                chapters.append((int(m.group(1)), m.group(2).strip(), body))
        if not chapters:
            return ""
        # Find chapters around chapter_num
        start = max(0, chapter_num - n)
        end   = chapter_num
        result = []
        for ch_num, title, body in chapters:
            if start <= ch_num <= end:
                result.append(f"Chapter {ch_num}: {title}\n\n{body[:3000]}")
        return "\n\n" + ("=" * 40) + "\n\n".join(result) if result else ""
    except Exception as e:
        log.error("read_chapters_around failed", book=book_name, chapter=chapter_num, error=str(e))
        return ""


def read_last_n_chapters(book_name: str, n: int = 5) -> str:
    """
    Read the last n chapters from the local .txt file.
    Returns concatenated text, or empty string if not found.
    """
    try:
        save_path  = get_book_path(book_name)
        if not os.path.exists(save_path):
            return ""
        with open(save_path, 'r', encoding='utf-8') as f:
            raw = f.read()
        blocks = re.split(r'={50,}', raw)
        chapters = []
        for i in range(len(blocks) - 1):
            header = blocks[i].strip()
            body   = blocks[i + 1].strip() if i + 1 < len(blocks) else ""
            m = re.match(r'Chapter\s+(\d+)\s*[:\-]?\s*(.*)', header, re.IGNORECASE)
            if m:
                chapters.append((int(m.group(1)), m.group(2).strip(), body))
        if not chapters:
            return ""
        last_n = chapters[-n:]
        result = []
        for ch_num, title, body in last_n:
            result.append(f"Chapter {ch_num}: {title}\n\n{body[:3000]}")
        return "\n\n" + ("=" * 40) + "\n\n".join(result) if result else ""
    except Exception as e:
        log.error("read_last_n_chapters failed", book=book_name, n=n, error=str(e))
        return ""


def find_next_chapter(url: str):
    """Fetch url and extract the next chapter link using all available methods."""
    plugin = plugin_registry.for_url(url)
    if plugin:
        scraper = SCRAPER if plugin.supports_cloudflare else None
        try:
            result = plugin.fetch_chapter(url, SESSION, scraper)
            return result.next_url
        except Exception as e:
            log.warning("Plugin find_next_chapter failed", url=url, plugin=plugin.id, error=str(e))

    try:
        resp, actual_url = _generic_get_with_retry(url)
        if actual_url != url:
            log.warning(
                "find_next_chapter: URL redirected — nav scraping may fail on unfamiliar layout",
                original=url, redirected_to=actual_url,
            )
        soup = BeautifulSoup(resp.text, "html.parser")
        parsed = urlparse(actual_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        return _extract_nav(soup, "next_chap", base)
    except Exception as e:
        log.warning("find_next_chapter failed", url=url, error=str(e))
    return None


def _get_chapter_list_from_file(book_name: str) -> list:
    """
    Return a list of (chapter_num, title) tuples from the local .txt file.
    Used for the chapter list picker.
    """
    try:
        save_path  = get_book_path(book_name)
        if not os.path.exists(save_path):
            return []
        with open(save_path, 'r', encoding='utf-8') as f:
            raw = f.read()
        # File format: ====header====  body  ====header====  body ...
        # After splitting on ===, blocks alternate: [junk, header, body, header, body, ...]
        # Only odd-indexed blocks are chapter headers — skip even (body) blocks
        # to avoid matching chapter references inside story text.
        blocks = re.split(r"={50,}", raw)
        seen   = {}
        i = 1
        while i < len(blocks) - 1:
            header = blocks[i].strip()
            i += 2
            m = re.match(r"Chapter\s+(\d+)\s*[:\-]?\s*(.*)", header, re.IGNORECASE)
            if m:
                num = int(m.group(1))
                if num not in seen:
                    seen[num] = m.group(2).strip() or f"Chapter {num}"
        return sorted(seen.items(), key=lambda x: x[0])
    except Exception:
        return []



# ── Junk paragraph detection ──────────────────────────────────────────────────

_JUNK_PATTERNS = [
    re.compile(r"use arrow keys", re.IGNORECASE),
    re.compile(r"prev/next chapter", re.IGNORECASE),
    re.compile(r"^\s*please\s+(click|tap|support)", re.IGNORECASE),
    re.compile(r"^\s*translator['']?s?\s+note[s]?\s*[:：]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*\[.*?\]\s*$"),
]

def _is_junk_paragraph(p: str) -> bool:
    p = p.strip()
    if not p or len(p) < 5:
        return True
    for pat in _JUNK_PATTERNS:
        if pat.search(p):
            return True
    return False

def _chapter_has_real_content(paragraphs: list, min_real: int = 3, min_total_chars: int = 150) -> bool:
    real = [p for p in paragraphs if not _is_junk_paragraph(p)]
    if len(real) < min_real:
        return False
    if sum(len(p) for p in real) < min_total_chars:
        return False
    return True


def clean_junk_chapters(book_name: str) -> dict:
    """
    Scan the downloaded .txt file and:
      1. Strip junk paragraphs (nav text etc.) from within each chapter.
      2. Remove duplicate chapter numbers — keep first occurrence only.
      3. Remove chapters with no real content after stripping.
    Rewrites the file in place with sequential numbering.

    Returns:
        {
            "removed":        int,   # whole chapters deleted (empty or duplicate)
            "duplicates":     int,   # duplicate chapters removed
            "paras_stripped": int,   # junk paragraphs stripped
            "kept":           int,   # chapters kept
            "new_last":       int,   # final chapter count
            "error":          str | None,
        }
    """
    result = {"removed": 0, "duplicates": 0, "paras_stripped": 0,
              "kept": 0, "new_last": 0, "error": None}
    save_path = get_book_path(book_name)
    if not os.path.exists(save_path):
        result["error"] = "Book file not found"
        return result

    try:
        with open(save_path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()

        blocks = re.split(r"={50,}", raw)
        chapters = []   # (chapter_num, title, clean_paragraphs)
        seen_nums = set()

        i = 1
        while i < len(blocks) - 1:
            header_block = blocks[i].strip()
            body_block   = blocks[i + 1] if i + 1 < len(blocks) else ""
            i += 2

            m = re.match(r"Chapter\s+(\d+)\s*[:\-]?\s*(.*)", header_block, re.IGNORECASE)
            if not m:
                continue

            chapter_num = int(m.group(1))
            title       = m.group(2).strip() or f"Chapter {chapter_num}"
            all_paras   = [p.strip() for p in body_block.split("\n\n") if p.strip()]
            clean_paras = [p for p in all_paras if not _is_junk_paragraph(p)]
            result["paras_stripped"] += len(all_paras) - len(clean_paras)

            # Deduplicate — skip if we've already seen this chapter number
            if chapter_num in seen_nums:
                result["duplicates"] += 1
                continue
            seen_nums.add(chapter_num)
            chapters.append((chapter_num, title, clean_paras))

        # Drop chapters with no real content after stripping
        kept    = [(n, t, p) for n, t, p in chapters if _chapter_has_real_content(p)]
        removed = len(chapters) - len(kept)
        result["removed"] = removed

        total_changed = removed + result["duplicates"] + result["paras_stripped"]
        if total_changed == 0:
            result["kept"]     = len(kept)
            result["new_last"] = len(kept)
            return result

        # Rewrite with sequential numbering
        lines = []
        for new_num, (_, title, paras) in enumerate(kept, start=1):
            lines.append(f"\n\n{'='*60}")
            lines.append(f"Chapter {new_num}: {title}")
            lines.append(f"{'='*60}\n")
            for p in paras:
                lines.append(p + "\n")

        with open(save_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        result["kept"]     = len(kept)
        result["new_last"] = len(kept)
        log.info("clean_junk_chapters complete", book=book_name,
                 removed=removed, duplicates=result["duplicates"],
                 paras_stripped=result["paras_stripped"], kept=len(kept))
        return result

    except Exception as e:
        result["error"] = str(e)
        log.error("clean_junk_chapters failed", book=book_name, error=str(e))
        return result
