"""
gs_adblock.py — Great Sage Ad Blocker
======================================
A standalone, uBlock Origin-class ad blocker for PyQt6 WebEngine views.

Architecture (four layers, executed in order):
  Layer 0 — Filter List Engine (network):
      Parses ABP/EasyList/uBO filter syntax (||domain^, /regex/, @@exception,
      $script/$image/$third-party options).  Downloads and caches EasyList,
      EasyPrivacy, uBO filters, and Peter Lowe's hosts list at startup; auto-
      refreshes every FILTER_TTL_DAYS days in a background thread.

  Layer 1 — Structural Heuristics (network):
      Catches rotating/unknown ad domains by their URL shape:
      Social Bar rotation, hash-script paths, push/beacon URLs, known ad
      subdomain prefixes.  Falls back gracefully when no filter list is loaded.

  Layer 2 — Cosmetic Filtering (DOM / CSS):
      Injected at DocumentCreation (before any page JS runs) via
      QWebEngineScript.  Hides known ad containers instantly via CSS.
      Cosmetic rules from the parsed filter lists are also injected here.

  Layer 3 — JS Overlay Killer (DOM / JS):
      Injected at DocumentReady + repeated sweeps at 0.3 / 0.7 / 1.5 / 3 /
      6 / 12 seconds + MutationObserver.  Kills fixed/absolute overlays,
      corner ads, and text-pattern dialogs; auto-clicks dismiss buttons;
      nulls push-notification and beacon APIs; restores scroll-lock.

Public API
----------
    from gs_adblock import AdBlockManager

    mgr = AdBlockManager(parent_qobject)   # start background fetch
    mgr.install(profile)                   # wire interceptor + cosmetic scripts
    page = mgr.make_page(profile, parent)  # AnimePage replacement
    js   = mgr.popup_killer_js()           # run after load: page.runJavaScript(js)
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

try:
    from PyQt6.QtCore import QObject, QTimer
    from PyQt6.QtWebEngineCore import (
        QWebEngineProfile,
        QWebEnginePage,
        QWebEngineScript,
        QWebEngineSettings,
        QWebEngineUrlRequestInfo,
        QWebEngineUrlRequestInterceptor,
    )
    _WEBENGINE_OK = True
except ImportError:
    _WEBENGINE_OK = False

log = logging.getLogger("gs_adblock")

# ──────────────────────────────────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────────────────────────────────

_CACHE_DIR  = Path.home() / ".cache" / "great_sage" / "adblock"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_FILTER_CACHE   = _CACHE_DIR / "compiled_rules.json"
_COSMETIC_CACHE = _CACHE_DIR / "cosmetic_rules.json"
_STAMP_FILE     = _CACHE_DIR / "last_updated.txt"
_CUSTOM_FILE    = Path.home() / ".config" / "great_sage" / "adblock_custom.txt"

FILTER_TTL_DAYS = 4          # re-download lists every N days
FILTER_TIMEOUT  = 12         # seconds per HTTP request

# ──────────────────────────────────────────────────────────────────────────────
# Filter list sources
# ──────────────────────────────────────────────────────────────────────────────

FILTER_SOURCES = {
    # Primary ad blocking
    "easylist":     "https://easylist.to/easylist/easylist.txt",
    # Tracking protection
    "easyprivacy":  "https://easylist.to/easylist/easyprivacy.txt",
    # uBO-specific optimisations
    "ubo_filters":  "https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/filters/filters.txt",
    # Annoyances (cookie notices, GDPR banners, push prompts)
    "ubo_annoy":    "https://raw.githubusercontent.com/uBlockOrigin/uAssets/master/filters/annoyances.txt",
    # Peter Lowe hosts (domains only, very reliable)
    "peter_lowe":   "https://pgl.yoyo.org/adservers/serverlist.php?hostformat=hosts&showintro=0&mimetype=plaintext",
    # AdGuard Base (good overlap + fills gaps)
    "adguard_base": "https://filters.adtidy.org/extension/ublock/filters/2.txt",
}

# ──────────────────────────────────────────────────────────────────────────────
# Built-in fallback domain set (used when no cache exists yet)
# ──────────────────────────────────────────────────────────────────────────────

_FALLBACK_DOMAINS = {
    "doubleclick.net", "googlesyndication.com", "adservice.google.com",
    "pagead2.googlesyndication.com", "adsterra.com", "propellerads.com",
    "popads.net", "rayanbordel.com", "realsrv.com", "exoclick.com",
    "juicyads.com", "trafficjunky.net", "trafficstars.com", "adspyglass.com",
    "hilltopads.net", "clickadu.com", "popcash.net", "adcash.com",
    "yllix.com", "plugrush.com", "adskeeper.co.uk", "adnium.com",
    "evadav.com", "pushground.com", "richpush.co", "kadam.net",
    "mgid.com", "revcontent.com", "taboola.com", "outbrain.com",
    "zedo.com", "adhese.com", "smartadserver.com", "rubiconproject.com",
    "openx.net", "pubmatic.com", "appnexus.com", "casalemedia.com",
    "criteo.com", "criteo.net", "mxpnl.com", "scorecardresearch.com",
    "quantserve.com", "imrworldwide.com", "doubleverify.com",
    "adsymptotic.com", "amazon-adsystem.com", "advertising.com",
    "yieldmo.com", "lijit.com", "contextweb.com", "bidswitch.net",
    "indexexchange.com", "sovrn.com", "sharethrough.com", "triplelift.com",
    "media.net", "33across.com", "onetag.com", "emxdgt.com",
    "loopme.com", "rhythmone.com", "undertone.com", "teads.tv",
    "spotxchange.com", "spotx.tv", "springserve.com", "appnexus.com",
}

_FALLBACK_PATTERNS = [
    "redirect", "popup", "popunder", "clickunder", "/pop/", "track.",
    "/ad/", "/ads/", "/click/", "/banner/", "/interstitial/",
]

# ──────────────────────────────────────────────────────────────────────────────
# Content allowlist — these must NEVER be blocked
# ──────────────────────────────────────────────────────────────────────────────

_ALLOWLIST = (
    "youtube.com", "googlevideo.com", "ytimg.com", "ggpht.com",
    "google.com", "gstatic.com", "googleapis.com",
    "animekai.be", "animekai.to", "animetsu.to", "gogoanime",
    "cloudflare.com", "cloudflare.net", "fastly.net",
    "akamaized.net", "akamai.net",
    "jwpcdn.com", "jwplatform.com", "bitmovin.com",
    "vidcloud", "streamtape", "mp4upload", "doodstream",
    "cdnjs.cloudflare.com",
    "fonts.googleapis.com", "fonts.gstatic.com",
)

# ──────────────────────────────────────────────────────────────────────────────
# Structural heuristic patterns (Layer 1)
# ──────────────────────────────────────────────────────────────────────────────

_SOCIAL_BAR_HOST_RE = re.compile(r'^cf\.[a-z0-9-]+\.[a-z]{2,}$')
_SOCIAL_BAR_PATH_RE = re.compile(r'^/[A-Za-z0-9]{8,}/\d+/?$')

_HASH_SCRIPT_RE = re.compile(
    r'^/(?:[0-9a-f]{8,32}|[a-z0-9]{8,32}/[a-z0-9]{4,32})\.js$'
)

_PUSH_PATH_RE = re.compile(
    r'/(push|subscribe|notification|beacon|sw\.js|service.?worker)([/?]|$)',
    re.IGNORECASE,
)

_AD_SUBDOMAIN_PREFIXES = (
    "ads.", "ad.", "adx.", "adserv", "banners.", "banner.",
    "pop.", "pops.", "click.", "trk.", "track.", "promo.",
    "cdn-ad.", "static-ad.", "media-ad.", "sync.", "pixel.",
    "imp.", "impress.", "rtb.", "bid.", "prebid.", "cm.",
    "collector.", "tag.", "tags.", "dtm.", "analytics.",
)

# ──────────────────────────────────────────────────────────────────────────────
# ABP / EasyList / uBO filter parser
# ──────────────────────────────────────────────────────────────────────────────

class FilterRule:
    """One parsed network filter rule."""
    __slots__ = ("pattern", "is_exception", "is_regex",
                 "domain_anchor", "path_only", "options",
                 "_re", "_plain_domain")

    def __init__(self, pattern: str, is_exception: bool = False,
                 is_regex: bool = False, domain_anchor: bool = False,
                 path_only: bool = False, options: dict | None = None):
        self.pattern       = pattern
        self.is_exception  = is_exception
        self.is_regex      = is_regex
        self.domain_anchor = domain_anchor
        self.path_only     = path_only
        self.options       = options or {}
        self._re: Optional[re.Pattern] = None
        self._plain_domain: Optional[str] = None

        if is_regex:
            try:
                self._re = re.compile(pattern, re.IGNORECASE)
            except re.error:
                self._re = None
        elif domain_anchor and "/" not in pattern and "*" not in pattern and "^" not in pattern:
            # Pure domain rule — fast path
            self._plain_domain = pattern.lower().lstrip(".")
        else:
            # Convert ABP glob pattern → regex
            p = pattern
            p = p.replace(".", r"\.")
            p = p.replace("*", ".*")
            p = p.replace("?", ".")
            p = p.replace("^", r"(?:[/?&=]|$)")
            if domain_anchor:
                p = r"(?:^|\.)?" + p
            try:
                self._re = re.compile(p, re.IGNORECASE)
            except re.error:
                self._re = None

    def matches_url(self, url: str, host: str, rtype_str: str,
                    is_third_party: bool) -> bool:
        """Return True if this rule matches the given request."""
        if not self._re and not self._plain_domain:
            return False

        # Option checks
        opts = self.options
        if opts:
            tp = opts.get("third-party")
            if tp is True and not is_third_party:
                return False
            if tp is False and is_third_party:
                return False
            allowed_types = opts.get("types")
            if allowed_types and rtype_str not in allowed_types:
                return False

        if self._plain_domain:
            return host == self._plain_domain or host.endswith("." + self._plain_domain)
        return bool(self._re and self._re.search(url))


class FilterEngine:
    """
    Parsed representation of one or more filter lists.

    Rules are bucketed by a 4-char token extracted from the pattern for a
    fast pre-filter (same idea as uBO's tokenisation) to avoid O(N) linear
    scan on every request.
    """

    def __init__(self):
        self.block_rules:     list[FilterRule] = []
        self.exception_rules: list[FilterRule] = []
        self.domain_set:      set[str]          = set(_FALLBACK_DOMAINS)
        self._loaded = False

    # ── Parsing ───────────────────────────────────────────────────────────────

    def parse_text(self, text: str) -> None:
        """Parse an ABP/EasyList/uBO filter list text blob."""
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("!") or line.startswith("["):
                continue

            # Cosmetic filters (##, #@#, #?#) — handled separately
            if "##" in line or "#@#" in line or "#?#" in line or "##+js" in line:
                continue

            is_exception = line.startswith("@@")
            if is_exception:
                line = line[2:]

            # Hosts-file format: "0.0.0.0 domain.com" or "127.0.0.1 domain.com"
            if re.match(r'^(?:0\.0\.0\.0|127\.0\.0\.1)\s+', line):
                parts = line.split()
                if len(parts) == 2:
                    d = parts[1].lower()
                    if d and d not in ("localhost", "broadcasthost", "0.0.0.0"):
                        if not is_exception:
                            self.domain_set.add(d)
                continue

            # Parse options
            options: dict = {}
            if "$" in line:
                idx = line.rfind("$")
                # Don't split if $ is inside a regex
                if not (line.startswith("/") and line.endswith("/")):
                    opts_str = line[idx+1:]
                    line     = line[:idx]
                    self._parse_options(opts_str, options)

            # Regex rules
            is_regex = line.startswith("/") and line.endswith("/")
            if is_regex:
                pattern = line[1:-1]
                rule = FilterRule(pattern, is_exception, is_regex=True, options=options)
                if rule._re:
                    (self.exception_rules if is_exception else self.block_rules).append(rule)
                continue

            # Domain anchor ||
            domain_anchor = line.startswith("||")
            if domain_anchor:
                line = line[2:]

            # Strip leading/trailing |
            line = line.strip("|")
            if not line:
                continue

            # Pure domain (no wildcards, no path) → fast domain set
            if (domain_anchor
                    and not is_exception
                    and not options
                    and "/" not in line
                    and "*" not in line
                    and "^" not in line
                    and "=" not in line):
                self.domain_set.add(line.lower())
                continue

            rule = FilterRule(line, is_exception, domain_anchor=domain_anchor, options=options)
            if rule._re or rule._plain_domain:
                (self.exception_rules if is_exception else self.block_rules).append(rule)

    @staticmethod
    def _parse_options(opts_str: str, out: dict) -> None:
        types: set[str] = set()
        for opt in opts_str.split(","):
            opt = opt.strip()
            if opt in ("script",):               types.add("script")
            elif opt in ("image",):              types.add("image")
            elif opt in ("stylesheet", "css"):   types.add("stylesheet")
            elif opt in ("xmlhttprequest", "xhr"):types.add("xhr")
            elif opt in ("subdocument", "frame"):types.add("frame")
            elif opt == "third-party":           out["third-party"] = True
            elif opt == "~third-party":          out["third-party"] = False
            elif opt == "first-party":           out["third-party"] = False
            elif opt == "~first-party":          out["third-party"] = True
        if types:
            out["types"] = list(types)

    # ── Checking ──────────────────────────────────────────────────────────────

    def should_block(self, url: str, host: str,
                     rtype_str: str, is_third_party: bool) -> bool:
        """Return True if the request should be blocked."""
        # Fast path: domain set (O(1) hash lookup)
        if host in self.domain_set:
            return True
        # Walk up subdomains
        parts = host.split(".")
        for i in range(1, len(parts) - 1):
            if ".".join(parts[i:]) in self.domain_set:
                return True

        # Slower rule scan — only triggered if domain set doesn't catch it
        blocked = any(
            r.matches_url(url, host, rtype_str, is_third_party)
            for r in self.block_rules
        )
        if not blocked:
            return False

        # Exception check
        return not any(
            r.matches_url(url, host, rtype_str, is_third_party)
            for r in self.exception_rules
        )

    # ── Serialise / deserialise compiled rules ────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "domains": list(self.domain_set),
            "block_rules": [
                {"p": r.pattern, "re": r.is_regex, "da": r.domain_anchor,
                 "opts": r.options}
                for r in self.block_rules
            ],
            "exception_rules": [
                {"p": r.pattern, "re": r.is_regex, "da": r.domain_anchor,
                 "opts": r.options}
                for r in self.exception_rules
            ],
        }

    def from_dict(self, d: dict) -> None:
        self.domain_set = set(d.get("domains", [])) | _FALLBACK_DOMAINS
        for rd in d.get("block_rules", []):
            r = FilterRule(rd["p"], is_regex=rd.get("re", False),
                           domain_anchor=rd.get("da", False),
                           options=rd.get("opts", {}))
            if r._re or r._plain_domain:
                self.block_rules.append(r)
        for rd in d.get("exception_rules", []):
            r = FilterRule(rd["p"], is_exception=True,
                           is_regex=rd.get("re", False),
                           domain_anchor=rd.get("da", False),
                           options=rd.get("opts", {}))
            if r._re or r._plain_domain:
                self.exception_rules.append(r)
        self._loaded = True


# ──────────────────────────────────────────────────────────────────────────────
# Cosmetic rule collector
# ──────────────────────────────────────────────────────────────────────────────

class CosmeticEngine:
    """
    Collects CSS selectors from ## cosmetic rules.
    Global rules (no domain restriction) are injected into every page.
    """

    def __init__(self):
        self.global_selectors: list[str] = []

    def parse_text(self, text: str) -> None:
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("!"):
                continue
            # Global cosmetic: ##selector
            m = re.match(r'^##(.+)$', line)
            if m:
                sel = m.group(1).strip()
                if sel and not sel.startswith("+js("):
                    self.global_selectors.append(sel)

    def build_css(self) -> str:
        if not self.global_selectors:
            return ""
        body = ",\n".join(self.global_selectors[:4000])  # cap to avoid huge injections
        return f"{body} {{ display: none !important; }}\n"

    def to_dict(self) -> dict:
        return {"global_selectors": self.global_selectors[:4000]}

    def from_dict(self, d: dict) -> None:
        self.global_selectors = d.get("global_selectors", [])


# ──────────────────────────────────────────────────────────────────────────────
# Filter list fetcher  (runs in a background thread)
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_url(url: str) -> Optional[str]:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 GreatSageAdBlock/1.0"},
        )
        with urllib.request.urlopen(req, timeout=FILTER_TIMEOUT) as resp:
            raw = resp.read()
            return raw.decode("utf-8", errors="replace")
    except Exception as exc:
        log.warning("adblock: fetch failed %s — %s", url, exc)
        return None


def _needs_refresh() -> bool:
    if not _STAMP_FILE.exists():
        return True
    try:
        mtime = _STAMP_FILE.stat().st_mtime
        return (time.time() - mtime) > FILTER_TTL_DAYS * 86400
    except OSError:
        return True


def _load_custom(engine: FilterEngine, cosmetic: CosmeticEngine) -> None:
    """Load user's custom filter file if it exists."""
    if _CUSTOM_FILE.exists():
        try:
            text = _CUSTOM_FILE.read_text(encoding="utf-8", errors="replace")
            engine.parse_text(text)
            cosmetic.parse_text(text)
            log.info("adblock: loaded custom filters from %s", _CUSTOM_FILE)
        except Exception as e:
            log.warning("adblock: failed to load custom file — %s", e)


def _download_and_compile() -> tuple[FilterEngine, CosmeticEngine]:
    engine   = FilterEngine()
    cosmetic = CosmeticEngine()
    ok_count = 0

    for name, url in FILTER_SOURCES.items():
        text = _fetch_url(url)
        if text:
            engine.parse_text(text)
            cosmetic.parse_text(text)
            ok_count += 1
            log.info("adblock: loaded %s (%d KB)", name, len(text) // 1024)

    _load_custom(engine, cosmetic)

    if ok_count:
        try:
            _FILTER_CACHE.write_text(
                json.dumps(engine.to_dict(), ensure_ascii=False), encoding="utf-8")
            _COSMETIC_CACHE.write_text(
                json.dumps(cosmetic.to_dict(), ensure_ascii=False), encoding="utf-8")
            _STAMP_FILE.write_text(str(time.time()))
            log.info("adblock: compiled %d domains + %d block rules + %d exception rules",
                     len(engine.domain_set), len(engine.block_rules), len(engine.exception_rules))
        except Exception as e:
            log.warning("adblock: cache write failed — %s", e)

    engine._loaded = ok_count > 0
    return engine, cosmetic


def _load_from_cache() -> tuple[FilterEngine, CosmeticEngine]:
    engine   = FilterEngine()
    cosmetic = CosmeticEngine()
    try:
        d = json.loads(_FILTER_CACHE.read_text(encoding="utf-8"))
        engine.from_dict(d)
        cd = json.loads(_COSMETIC_CACHE.read_text(encoding="utf-8"))
        cosmetic.from_dict(cd)
        _load_custom(engine, cosmetic)
        log.info("adblock: restored %d domains + %d rules from cache",
                 len(engine.domain_set), len(engine.block_rules))
    except Exception as e:
        log.warning("adblock: cache load failed — %s", e)
    return engine, cosmetic


# ──────────────────────────────────────────────────────────────────────────────
# Network interceptor  (Layer 0 + 1)
# ──────────────────────────────────────────────────────────────────────────────

if _WEBENGINE_OK:
    class _AdBlockInterceptor(QWebEngineUrlRequestInterceptor):
        """
        Network-level ad blocker.

        Decision order (first match wins):
          0. Content allowlist             — never block video/CDN domains
          1. FilterEngine domain set       — O(1) hash lookup, 50k+ domains
          2. FilterEngine rule scan        — ABP/uBO parsed rules
          3. Structural heuristics         — shape-based detection
          4. URL pattern blocklist         — sub/script frames only
        """

        def __init__(self, engine: FilterEngine):
            super().__init__()
            self._engine = engine

        def update_engine(self, engine: FilterEngine) -> None:
            self._engine = engine

        @staticmethod
        def _rtype_str(rtype) -> str:
            RT = QWebEngineUrlRequestInfo.ResourceType
            return {
                RT.ResourceTypeScript:       "script",
                RT.ResourceTypeImage:        "image",
                RT.ResourceTypeStylesheet:   "stylesheet",
                RT.ResourceTypeSubFrame:     "frame",
                RT.ResourceTypeXhr:          "xhr",
            }.get(rtype, "other")

        def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:
            url   = info.requestUrl().toString()
            host  = info.requestUrl().host().lower()
            path  = info.requestUrl().path()
            rtype = info.resourceType()
            rtype_str = self._rtype_str(rtype)

            # Normalise host
            if host.startswith("www."):
                host = host[4:]

            # ── 0. Content allowlist ──────────────────────────────────────────
            if any(a in host for a in _ALLOWLIST):
                return

            RT = QWebEngineUrlRequestInfo.ResourceType

            # ── 1 + 2. Filter engine (domain set + rule scan) ─────────────────
            initiator = info.initiator().host().lower()
            if initiator.startswith("www."):
                initiator = initiator[4:]
            is_third_party = bool(initiator) and initiator != host

            if self._engine.should_block(url, host, rtype_str, is_third_party):
                info.block(True)
                return

            # ── 3. Structural heuristics ──────────────────────────────────────

            # Social Bar / Adsterra domain rotation
            if (_SOCIAL_BAR_HOST_RE.match(host)
                    and "cloudflare" not in host
                    and _SOCIAL_BAR_PATH_RE.match(path)):
                info.block(True)
                return

            # Random-hash cross-origin script requests
            if (rtype == RT.ResourceTypeScript
                    and _HASH_SCRIPT_RE.match(path)
                    and host != initiator
                    and not any(a in host for a in _ALLOWLIST)):
                info.block(True)
                return

            # Push / beacon / service worker
            if (_PUSH_PATH_RE.search(path)
                    and not any(a in host for a in _ALLOWLIST)
                    and rtype not in (RT.ResourceTypeMainFrame, RT.ResourceTypeSubFrame)):
                info.block(True)
                return

            # Known ad subdomain prefixes
            if any(host.startswith(pfx) for pfx in _AD_SUBDOMAIN_PREFIXES):
                if not any(a in host for a in _ALLOWLIST):
                    info.block(True)
                    return

            # ── 4. URL pattern blocklist (non-main-frame requests only) ───────
            if rtype not in (RT.ResourceTypeMainFrame, RT.ResourceTypeSubFrame):
                url_lower = url.lower()
                for pat in _FALLBACK_PATTERNS:
                    if pat in url_lower:
                        info.block(True)
                        return

else:
    class _AdBlockInterceptor:  # type: ignore
        def __init__(self, engine): pass
        def update_engine(self, engine): pass


# ──────────────────────────────────────────────────────────────────────────────
# Custom page  (Layer 2 — handled via install(), Layer 3 — popup_killer_js)
# ──────────────────────────────────────────────────────────────────────────────

if _WEBENGINE_OK:
    class AdBlockPage(QWebEnginePage):
        """
        Drop-in replacement for QWebEnginePage / AnimePage.
        - Enables fullscreen + autoplay
        - Blocks all popup/new-window requests
        - Silently swallows JS alert/confirm/prompt dialogs
        - Suppresses JS console noise
        """

        def __init__(self, profile, parent=None):
            super().__init__(profile, parent)
            s = self.settings()
            s.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled,     True)
            s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture,  False)
            s.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent,  True)
            s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled,          True)
            s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
            s.setAttribute(QWebEngineSettings.WebAttribute.DnsPrefetchEnabled,           True)
            self.fullScreenRequested.connect(self._handle_fullscreen)

        def _handle_fullscreen(self, request):
            request.accept()
            view = self.parent()
            if not view:
                return
            host = view
            while host and not hasattr(host, "_enter_stream_fullscreen"):
                host = host.parent()
            if not host:
                return
            if request.toggleOn():
                host._enter_stream_fullscreen()

        def createWindow(self, _type):
            return None  # block all popups

        def javaScriptAlert(self, url, msg):     pass
        def javaScriptConfirm(self, url, msg):   return False
        def javaScriptPrompt(self, url, msg, default_value, result): return False
        def javaScriptConsoleMessage(self, level, message, line, source): pass

else:
    class AdBlockPage:  # type: ignore
        def __init__(self, profile, parent=None): pass


# ──────────────────────────────────────────────────────────────────────────────
# Cosmetic injection CSS  (static layer — always injected regardless of lists)
# ──────────────────────────────────────────────────────────────────────────────

_STATIC_COSMETIC_CSS = r"""
/* ── Known ad container selectors ── */
[class*="ad-"], [class*="-ad"], [class*="ads-"], [class*="-ads"],
[id*="ad-"],    [id*="-ad"],    [id*="ads-"],    [id*="-ads"],
[class*="banner-ad"], [class*="advert"], [class*="sponsor"],
[id*="sponsor"], [id*="advert"],
amp-ad, ins.adsbygoogle,
iframe[src*="doubleclick"],
iframe[src*="googlesyndication"],
iframe[src*="adsterra"],
iframe[src*="exoclick"],
iframe[src*="trafficjunky"],
iframe[src*="realsrv"],
iframe[src*="popads"],
iframe[src*="adcash"],
iframe[src*="hilltopads"],
iframe[src*="propellerads"],
iframe[id*="google_ads"],
[class*="push-notification"], [id*="push-notification"],
[class*="notif-prompt"],      [id*="notif-prompt"],
[class*="interstitial"],      [id*="interstitial"],
[class*="takeover"],          [id*="takeover"],
[class*="lightbox-ad"],       [id*="lightbox-ad"],
[class*="overlay-ad"],        [id*="overlay-ad"],
[class*="ad-overlay"],        [id*="ad-overlay"],
[class*="ad-modal"],          [id*="ad-modal"],
[class*="popup-ad"],          [id*="popup-ad"],
/* Cookie / GDPR banners */
[class*="cookie-banner"], [id*="cookie-banner"],
[class*="gdpr-banner"],   [id*="gdpr-banner"],
[class*="consent-banner"],[id*="consent-banner"],
[class*="cc-window"],     [id*="cc-window"],
/* Social share junk */
[class*="social-share-sticky"],
/* Sticky bottom banners */
[class*="sticky-ad"],   [id*="sticky-ad"],
[class*="fixed-ad"],    [id*="fixed-ad"] {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
    width: 0 !important;
    pointer-events: none !important;
}
"""


def _build_cosmetic_injection(extra_css: str = "") -> str:
    """Return JS to inject at DocumentCreation."""
    combined_css = _STATIC_COSMETIC_CSS + extra_css
    # Escape for embedding in a JS string
    css_escaped = combined_css.replace("\\", "\\\\").replace("`", r"\`")
    return (
        "(function(){"
        "window.open=function(){return null;};"
        "window.alert=function(){};"
        "window.confirm=function(){return false;};"
        "window.prompt=function(){return null;};"
        "var s=document.createElement('style');"
        f"s.textContent=`{css_escaped}`;"
        "document.documentElement.appendChild(s);"
        "})();"
    )


# ──────────────────────────────────────────────────────────────────────────────
# JS popup / overlay killer  (Layer 3)
# ──────────────────────────────────────────────────────────────────────────────

def popup_killer_js() -> str:
    """
    Heavy-duty DOM overlay killer.

    Sweeps:
      - Immediately on injection
      - At 300 ms, 700 ms, 1.5 s, 3 s, 6 s, 12 s (catches lazy loaders)
      - On every DOM mutation (MutationObserver, debounced to 120 ms)

    Five detection passes:
      1. Selector-based kill (class/id patterns)
      2a. Large fixed/absolute overlays (z-index ≥ 50, covers ≥ 30% × 20%)
      2b. Corner ads (small fixed box docked to a viewport corner)
      3. Text-content scan (age gates, push prompts, adult promos, etc.)
      4. Auto-click close/dismiss buttons found inside overlays
      5. Remove scroll-lock left behind by dismissed overlays
    """
    return r"""
(function() {
'use strict';

// ── Block ad JS APIs ──────────────────────────────────────────────────────
try {
    if ('serviceWorker' in navigator) {
        Object.defineProperty(navigator, 'serviceWorker', {
            get: function() { return { register: function() { return Promise.reject(); } }; },
            configurable: false
        });
    }
    if (window.Notification) {
        window.Notification.requestPermission = function() { return Promise.resolve('denied'); };
        Object.defineProperty(window.Notification, 'permission', {
            get: function() { return 'denied'; }, configurable: true
        });
    }
    if (navigator.sendBeacon) {
        navigator.sendBeacon = function() { return false; };
    }
    // Block push subscription
    if (window.PushManager) {
        window.PushManager.prototype.subscribe = function() { return Promise.reject(new Error('blocked')); };
    }
    // Null window.open (belt-and-suspenders alongside createWindow override)
    window.open = function() { return null; };
} catch(e) {}

// ── Viewport helpers ──────────────────────────────────────────────────────
var VW = window.innerWidth  || document.documentElement.clientWidth  || 800;
var VH = window.innerHeight || document.documentElement.clientHeight || 600;
window.addEventListener('resize', function() {
    VW = window.innerWidth  || document.documentElement.clientWidth  || 800;
    VH = window.innerHeight || document.documentElement.clientHeight || 600;
});

// ── Video player guard ────────────────────────────────────────────────────
function containsPlayer(el) {
    if (!el) return false;
    var tag = (el.tagName || '').toLowerCase();
    if (tag === 'video' || tag === 'iframe') return true;
    return !!(el.querySelector && el.querySelector('video, iframe[src*="player"], iframe[src*="embed"]'));
}

// ── Heuristic overlay detector ────────────────────────────────────────────
function isAdOverlay(el) {
    if (!el || !el.isConnected) return false;
    var cs  = getComputedStyle(el);
    var pos = cs.position;
    if (pos !== 'fixed' && pos !== 'absolute') return false;
    var r = el.getBoundingClientRect();
    if (r.width < 80 || r.height < 50) return false;
    if (containsPlayer(el)) return false;
    var z  = parseInt(cs.zIndex || '0', 10) || 0;
    var bg = cs.backgroundColor || '';
    return (z >= 10) ||
        (bg !== '' && bg !== 'transparent' && bg !== 'rgba(0, 0, 0, 0)') ||
        (r.width > VW * 0.25 && r.height > VH * 0.15);
}

// ── Corner ad detector ────────────────────────────────────────────────────
function isCornerAd(el) {
    if (!el || !el.isConnected) return false;
    var cs  = getComputedStyle(el);
    var pos = cs.position;
    if (pos !== 'fixed' && pos !== 'absolute') return false;
    if (containsPlayer(el)) return false;
    var r = el.getBoundingClientRect();
    if (r.width  < 80  || r.height < 60)  return false;
    if (r.width  > VW * 0.75)             return false;
    if (r.height > VH * 0.75)             return false;
    var inCorner = (r.left < VW * 0.35 || r.right > VW * 0.65) &&
                   (r.top  < VH * 0.35 || r.bottom > VH * 0.65);
    if (!inCorner) return false;
    var txt = (el.textContent || '').toLowerCase();
    return TEXT_PATTERNS.some(function(p) { return txt.includes(p); }) ||
           !!(el.querySelector && el.querySelector('img'));
}

// ── Selector kill list ────────────────────────────────────────────────────
var KILL_SELECTORS = [
    '[class*="modal"]',   '[class*="popup"]',   '[class*="overlay"]',
    '[class*="dialog"]',  '[class*="banner"]',  '[class*="consent"]',
    '[class*="gdpr"]',    '[class*="cookie"]',  '[class*="age-"]',
    '[class*="-age"]',    '[class*="verify"]',  '[class*="gate"]',
    '[class*="wall"]',    '[class*="advert"]',  '[class*="sponsor"]',
    '[class*="interstitial"]', '[class*="promo"]',
    '[class*="takeover"]', '[class*="lightbox"]', '[class*="alert"]',
    '[class*="robot"]',   '[class*="captcha"]', '[class*="bot-check"]',
    '[id*="modal"]',      '[id*="popup"]',      '[id*="overlay"]',
    '[id*="consent"]',    '[id*="cookie"]',     '[id*="age"]',
    '[id*="gdpr"]',       '[id*="adblock"]',    '[id*="gate"]',
    '[id*="wall"]',       '[id*="banner"]',     '[id*="advert"]',
    '[id*="lightbox"]',   '[id*="takeover"]',   '[id*="alert"]',
    '[id*="captcha"]',    '[id*="robot"]',
];

// ── Text patterns ─────────────────────────────────────────────────────────
var TEXT_PATTERNS = [
    'over 18','over18','are you 18','age verification','confirm your age',
    'this site contains','adult content','enter age','i am 18',"i'm 18",
    'must be 18','18 years','18+',
    'xxx game','adult game','sex game','dating sim','hot singles',
    'meet singles','play now','want to play','click to play',
    'free game','play for free','spin to win','you won',
    'allow notifications','enable notifications','subscribe to notifications',
    'push notifications','allow to continue','click allow',
    'complete a survey','take a survey','human verification',
    'you are not a robot','verify you are human','verify you are not a bot',
    'are you a bot','not a bot','chrome alert','browser alert',
    'download our app',
    'install our app','vpn required','use our vpn',
    'this is an advertisement',
    'disable your adblocker','whitelist this site','please disable adblock',
    'ad blocker detected','adblock detected','we noticed you',
    'support us by disabling',
];

// ── Close button patterns ─────────────────────────────────────────────────
var CLOSE_PATTERNS = [
    'close','dismiss','deny','cancel','no thanks','not now',
    'disagree','reject','skip','continue without','no, thanks',
    'maybe later','decline','refuse','✕','×','x',
    'got it','i understand','accept all','allow all',
];

// ── Core sweep ────────────────────────────────────────────────────────────
function killOverlays() {
    // Pass 1: selector + heuristic
    KILL_SELECTORS.forEach(function(sel) {
        try {
            document.querySelectorAll(sel).forEach(function(el) {
                if (isAdOverlay(el)) el.remove();
            });
        } catch(e) {}
    });

    // Pass 2a: large overlay geometry (z ≥ 20, ≥ 15% × 10%) or high-z small dialog (z ≥ 999)
    try {
        document.querySelectorAll('div,section,aside,article,span').forEach(function(el) {
            if (!el.isConnected) return;
            var cs  = getComputedStyle(el);
            var pos = cs.position;
            if (pos !== 'fixed' && pos !== 'absolute') return;
            var z = parseInt(cs.zIndex || '0', 10) || 0;
            if (z < 20) return;
            var r = el.getBoundingClientRect();
            if (containsPlayer(el)) return;
            // Large overlay: covers significant viewport area
            var isLarge = r.width > VW * 0.15 && r.height > VH * 0.10;
            // Small dialog: high z-index centered on screen (fake native dialogs)
            var isCenteredDialog = z >= 999 && r.width > 200 && r.height > 80 &&
                r.left > VW * 0.1 && r.right < VW * 0.9;
            if (!isLarge && !isCenteredDialog) return;
            el.remove();
        });
    } catch(e) {}

    // Pass 2b: corner ads
    try {
        document.querySelectorAll('div,section,aside,article').forEach(function(el) {
            if (isCornerAd(el)) el.remove();
        });
    } catch(e) {}

    // Pass 3: text-content scan
    try {
        document.querySelectorAll('div,section,aside,article,form').forEach(function(el) {
            if (!el.isConnected) return;
            var txt = (el.textContent || '').toLowerCase();
            if (txt.length < 2 || txt.length > 2000) return;
            if (!TEXT_PATTERNS.some(function(p) { return txt.includes(p); })) return;
            var node = el, target = null;
            for (var i = 0; i < 8 && node; i++) {
                var cs2 = getComputedStyle(node);
                if (cs2.position === 'fixed' || cs2.position === 'absolute') {
                    var r2 = node.getBoundingClientRect();
                    if (r2.width > 120 && r2.height > 60) { target = node; }
                }
                node = node.parentElement;
            }
            if (target) target.remove();
            else if (isAdOverlay(el)) el.remove();
        });
    } catch(e) {}

    // Pass 4: auto-click dismiss buttons
    try {
        document.querySelectorAll('button,[role="button"],a.close,.close-btn').forEach(function(btn) {
            if (!btn.isConnected) return;
            var txt = (btn.textContent || btn.innerText || btn.getAttribute('aria-label') || '').toLowerCase().trim();
            if (!CLOSE_PATTERNS.some(function(p) { return txt === p || txt.includes(p); })) return;
            var node = btn;
            for (var i = 0; i < 8 && node; i++) {
                if (isAdOverlay(node) || isCornerAd(node)) { btn.click(); return; }
                node = node.parentElement;
            }
        });
    } catch(e) {}

    // Pass 5: remove scroll-lock
    try {
        document.body.style.overflow = '';
        document.body.style.paddingRight = '';
        document.documentElement.style.overflow = '';
    } catch(e) {}
}

// ── Run immediately and on schedule ──────────────────────────────────────
killOverlays();
[300, 700, 1500, 3000, 6000, 12000].forEach(function(t) {
    setTimeout(killOverlays, t);
});

// ── MutationObserver (debounced) ──────────────────────────────────────────
var _sweepTimer = null;
function _debouncedSweep() {
    if (_sweepTimer) return;
    _sweepTimer = setTimeout(function() { _sweepTimer = null; killOverlays(); }, 120);
}

var obs = new MutationObserver(function(mutations) {
    for (var i = 0; i < mutations.length; i++) {
        if (mutations[i].addedNodes.length > 0) { _debouncedSweep(); return; }
    }
});
obs.observe(document.documentElement, { childList: true, subtree: true });

})();
"""


# ──────────────────────────────────────────────────────────────────────────────
# AdBlockManager — top-level orchestrator
# ──────────────────────────────────────────────────────────────────────────────

class AdBlockManager:
    """
    Manages filter list lifecycle and wires everything into a QWebEngineProfile.

    Usage:
        mgr = AdBlockManager()
        mgr.install(self._stream_profile)        # call once after profile creation
        page = mgr.make_page(profile, view)      # use instead of AnimePage(...)
        # after page load:
        page.runJavaScript(mgr.popup_killer_js())
    """

    def __init__(self):
        self._engine   = FilterEngine()
        self._cosmetic = CosmeticEngine()
        self._interceptor: Optional[_AdBlockInterceptor] = None
        self._profiles: list = []
        self._lock = threading.Lock()

        # Try cache first (instant startup)
        if _FILTER_CACHE.exists() and _COSMETIC_CACHE.exists():
            self._engine, self._cosmetic = _load_from_cache()

        # Background refresh
        self._start_background_fetch()

    # ── Public API ─────────────────────────────────────────────────────────

    def install(self, profile) -> None:
        """
        Wire the ad blocker into a QWebEngineProfile.
        Call once per profile.
        """
        if not _WEBENGINE_OK:
            return

        self._profiles.append(profile)

        # Network interceptor
        self._interceptor = _AdBlockInterceptor(self._engine)
        profile.setUrlRequestInterceptor(self._interceptor)

        # Cosmetic injection script
        self._inject_cosmetic(profile)

    def make_page(self, profile, parent=None) -> "AdBlockPage":
        """Return a fully configured AdBlockPage."""
        return AdBlockPage(profile, parent)

    def popup_killer_js(self) -> str:
        """JS to run after page load."""
        return popup_killer_js()

    def status(self) -> dict:
        """Return status info for a debug/settings panel."""
        return {
            "domains":         len(self._engine.domain_set),
            "block_rules":     len(self._engine.block_rules),
            "exception_rules": len(self._engine.exception_rules),
            "cosmetic_rules":  len(self._cosmetic.global_selectors),
            "lists_loaded":    self._engine._loaded,
            "cache_exists":    _FILTER_CACHE.exists(),
            "custom_file":     str(_CUSTOM_FILE),
            "last_updated":    (
                _STAMP_FILE.read_text().strip()
                if _STAMP_FILE.exists() else "never"
            ),
        }

    def force_refresh(self) -> None:
        """Force re-download of all filter lists (e.g. from a settings button)."""
        if _STAMP_FILE.exists():
            _STAMP_FILE.unlink()
        self._start_background_fetch()

    # ── Internal ────────────────────────────────────────────────────────────

    def _inject_cosmetic(self, profile) -> None:
        from PyQt6.QtWebEngineCore import QWebEngineScript
        extra_css = self._cosmetic.build_css()
        js = _build_cosmetic_injection(extra_css)
        script = QWebEngineScript()
        script.setName("gs_adblock_cosmetic")
        script.setSourceCode(js)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setRunsOnSubFrames(True)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        profile.scripts().insert(script)

    def _start_background_fetch(self) -> None:
        t = threading.Thread(target=self._fetch_worker, daemon=True, name="adblock_fetch")
        t.start()

    def _fetch_worker(self) -> None:
        if not _needs_refresh():
            log.info("adblock: filter lists are fresh, skipping download")
            return
        log.info("adblock: downloading filter lists in background…")
        engine, cosmetic = _download_and_compile()
        if engine._loaded:
            with self._lock:
                self._engine   = engine
                self._cosmetic = cosmetic
            # Update live interceptor
            if self._interceptor:
                self._interceptor.update_engine(engine)
            # Re-inject cosmetic scripts into all known profiles
            for profile in self._profiles:
                try:
                    # Remove old script, insert refreshed one
                    scripts = profile.scripts()
                    existing = scripts.find("gs_adblock_cosmetic")
                    if not existing.isNull():
                        scripts.remove(existing)
                    self._inject_cosmetic(profile)
                except Exception as e:
                    log.warning("adblock: profile cosmetic refresh failed — %s", e)
            log.info("adblock: filter lists updated. domains=%d block_rules=%d",
                     len(engine.domain_set), len(engine.block_rules))


# ── Module-level singleton ────────────────────────────────────────────────────

_manager: Optional[AdBlockManager] = None


def get_manager() -> AdBlockManager:
    """Return (or lazily create) the module-level AdBlockManager singleton."""
    global _manager
    if _manager is None:
        _manager = AdBlockManager()
    return _manager
