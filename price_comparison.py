"""
price_comparison.py — Compare product prices across retailers.

Public API:
    find_comparable_product(source_url, source_retailer, target_retailer, identity=None)

Env vars (set by caller / environment):
    MATCHING_LLM_PROVIDER: "gemini" | "anthropic" | "groq"
    GEMINI_API_KEY, WALMART_AFFILIATE_ID, etc.
    FIRECRAWL_API_KEY: required for Amazon identity extraction
"""

import os
import re
import logging
import threading
import time

# ---------------------------------------------------------------------------
# Crawl metrics — per-retailer counters for cost tracking and tier-effectiveness analysis.
# Exposed via web_app.py /api/admin/crawl-stats. Counters are in-memory and reset on
# process restart; that's fine for our scale — Railway restarts are observable and the
# numbers are directional, not accounting-grade.
# ---------------------------------------------------------------------------

class _CrawlMetrics:
    """Thread-safe per-retailer counter store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[tuple[str, str], int] = {}
        self._started_at: float = time.time()

    def inc(self, metric: str, retailer: str = "", n: int = 1) -> None:
        retailer = retailer or "unknown"
        with self._lock:
            key = (metric, retailer)
            self._counts[key] = self._counts.get(key, 0) + n

    def snapshot(self) -> dict:
        with self._lock:
            counts_copy = dict(self._counts)
            started_at = self._started_at
        # Group as { metric: { retailer: count } }
        grouped: dict[str, dict[str, int]] = {}
        for (metric, retailer), n in counts_copy.items():
            grouped.setdefault(metric, {})[retailer] = n
        return {
            'started_at': started_at,
            'uptime_seconds': time.time() - started_at,
            'metrics': grouped,
        }

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._started_at = time.time()


_crawl_metrics = _CrawlMetrics()


def get_crawl_metrics() -> dict:
    """Public accessor for web_app.py admin endpoint."""
    return _crawl_metrics.snapshot()


def reset_crawl_metrics() -> None:
    """Public reset for web_app.py admin endpoint."""
    _crawl_metrics.reset()


# ---------------------------------------------------------------------------
# Retailer searcher registry
# Keys are retailer slugs; values are callables (wired in later issues).
# ---------------------------------------------------------------------------
RETAILER_SEARCHERS: dict = {
    "walmart": None,  # wired up after _search_walmart is defined below
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _no_match(reason: str) -> dict:
    """Return a structured no-match sentinel dict."""
    return {"match": None, "reason": reason}


def _empty_identity(search_query: str = "") -> dict:
    return {
        "asin": None,
        "title": None,
        "brand": None,
        "model": None,
        "upc": None,
        "price": None,
        "image_url": None,
        "search_query": search_query,
    }


def _asin_from_url(url: str) -> str | None:
    m = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)', url)
    return m.group(1) if m else None


_CONDITION_LABEL_RE = re.compile(
    r'\b(renewed|refurbished|restored|certified refurbished|open[- ]?box|used)\b',
    re.IGNORECASE,
)

# Generational/variant suffixes that vary across retailers but don't identify different products.
# e.g. "Echo Dot (4th Gen)" vs "Echo Dot" — both refer to the same product family.
_GENERATIONAL_SUFFIX_RE = re.compile(
    r'[\(\[]?\s*(?:'
    r'\d+(?:st|nd|rd|th)\s+gen(?:eration)?'       # 4th gen, 2nd generation
    r'|\b(?:20\d{2}|19\d{2})\b\s*(?:model|release|edition)?'  # 2022, 2022 model, 1999 edition
    r'|gen(?:eration)?\s*\d+'                      # gen 2, generation 3
    r'|v\d+(?:\.\d+)?'                             # v2, v3.1
    r'|mk\s*(?:i{1,3}|iv|v{1,3}|\d+)'             # mk2, mkII
    r')\s*[\)\]]?',
    re.IGNORECASE,
)

def _strip_condition_labels(text: str) -> str:
    """Remove Amazon-specific condition markers that confuse searches on other retailers."""
    return re.sub(r'\s{2,}', ' ', _CONDITION_LABEL_RE.sub('', text)).strip(' ,-–—()')

def _normalize_product_name(text: str) -> str:
    """Strip generational/variant suffixes that differ between retailers for the same product."""
    normalized = _GENERATIONAL_SUFFIX_RE.sub(' ', text)
    normalized = re.sub(r'\s{2,}', ' ', normalized).strip(' ,-–—()')
    return normalized

def _build_search_query(identity: dict) -> str:
    # UPC is useful for matching/verification but Walmart text search returns
    # no results for bare UPC strings — use brand+model or title instead.
    brand = identity.get("brand") or ""
    model = identity.get("model") or ""
    if brand and model:
        return _normalize_product_name(_strip_condition_labels(f"{brand} {model}"))
    title = identity.get("title") or ""
    if title:
        cleaned = _normalize_product_name(_strip_condition_labels(title))
        return " ".join(cleaned.split()[:8])
    asin = identity.get("asin") or ""
    return asin


def _parse_amazon_markdown(markdown: str, html: str) -> dict:
    """Extract product fields from Firecrawl markdown/html of an Amazon PDP."""
    result: dict = {k: None for k in ("title", "brand", "model", "upc", "price", "image_url")}

    # Title: prefer <span id="productTitle"> from HTML (markdown headings are Amazon UI chrome)
    pt = re.search(r'id=["\']productTitle["\'][^>]*>\s*([^<]+)', html)
    if pt:
        result["title"] = pt.group(1).strip()
    else:
        # Fallback: first heading that doesn't look like Amazon accessibility chrome
        for m in re.finditer(r'^#{1,2}\s+(.+)', markdown, re.MULTILINE):
            candidate = m.group(1).strip()
            if 'keyboard shortcut' not in candidate.lower() and 'product summary' not in candidate.lower():
                result["title"] = candidate
                break

    # Price: look for $ amounts
    price_m = re.search(r'\$\s*([\d,]+\.\d{2})', markdown)
    if price_m:
        try:
            result["price"] = float(price_m.group(1).replace(",", ""))
        except ValueError:
            pass

    # Product details table patterns (markdown tables or "Key | Value" lines)
    def _table_val(label: str) -> str | None:
        pattern = rf'(?i)\|\s*{re.escape(label)}\s*\|\s*([^|\n]+)'
        m2 = re.search(pattern, markdown)
        if m2:
            return m2.group(1).strip()
        # Also try "Label: value" format
        m3 = re.search(rf'(?i){re.escape(label)}\s*[:\|]\s*([^\n]+)', markdown)
        if m3:
            return m3.group(1).strip()
        return None

    result["brand"] = _table_val("Brand") or _table_val("Manufacturer")
    result["model"] = _table_val("Model Number") or _table_val("Item model number") or _table_val("Model")
    result["upc"] = _table_val("UPC") or _table_val("EAN")

    # Image URL from og:image in HTML
    img_m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html)
    if not img_m:
        img_m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html)
    if img_m:
        result["image_url"] = img_m.group(1)

    return result


def _init_firecrawl(api_key: str):
    try:
        from firecrawl import Firecrawl
        return Firecrawl(api_key=api_key), 'v2'
    except ImportError:
        pass
    try:
        from firecrawl import FirecrawlApp
        return FirecrawlApp(api_key=api_key), 'v1'
    except ImportError:
        pass
    raise ImportError("firecrawl-py is not installed")


def _scrape_with_jina(url: str) -> str:
    """Last-ditch fallback scraper using Jina AI Reader (r.jina.ai). No API key needed.
    Returns markdown text; HTML is not available via this path.

    NOTE: Empirically unreliable on retail PDPs — never load-bearing. Output must
    pass _jina_quality_ok() before being returned to callers. See docs/11 - Crawl Strategy.md.
    """
    import urllib.request as _ureq
    jina_url = f"https://r.jina.ai/{url}"
    req = _ureq.Request(jina_url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; DealNotify/1.0)",
        "Accept": "text/plain, text/markdown, */*",
        "X-Return-Format": "markdown",
        "X-Timeout": "20",
    })
    try:
        with _ureq.urlopen(req, timeout=25) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except Exception as exc:
        logging.warning("Jina fallback failed for %s: %s", url, exc)
        return ''


def _jina_quality_ok(content: str, is_search_page: bool = False) -> bool:
    """Quality gate for Jina output before returning it to downstream parsers.

    Jina commonly returns near-empty pages, login walls, or anti-bot stubs for retail
    sites. Without this gate, downstream parsers see junk and produce false negatives.

    A response is considered usable if it has either:
      - A clear $X.XX price marker (for PDPs and search results), OR
      - At least one product link to a known retailer (for search pages), AND
      - Minimum 800 chars of text (filters out anti-bot pages and login walls).
    """
    if not content or len(content) < 800:
        return False
    has_price = bool(re.search(r'\$\s*\d+(?:[.,]\d{2,})', content))
    if has_price:
        return True
    if is_search_page:
        # Search pages may not always show prices inline (lazy-loaded), but should have product links
        has_product_link = bool(re.search(
            r'\((https?://[^)\s]*(?:amazon|walmart|target|bestbuy|costco|ebay)\.com/[^)\s]+)\)',
            content,
        ))
        return has_product_link
    return False


_PAYMENT_ERRORS = ("payment required", "insufficient credits", "402", "upgrade your plan")

_SEARCH_URL_PATTERNS = (
    '/s?', '/search?', '/searchpage', 'CatalogSearch', 'searchTerm',
)


def _is_search_url(url: str) -> bool:
    return any(p in url for p in _SEARCH_URL_PATTERNS)


def _try_jina(url: str, retailer: str = "") -> tuple[str, str]:
    """Last-ditch Jina attempt with quality gate. Returns ('','') if output is junk
    so downstream parsers don't see anti-bot pages or login walls."""
    _crawl_metrics.inc('jina_attempts', retailer)
    jina_md = _scrape_with_jina(url)
    if not _jina_quality_ok(jina_md, is_search_page=_is_search_url(url)):
        logging.warning("Jina output failed quality gate for %s (len=%d) — returning empty",
                        url, len(jina_md))
        return '', ''
    _crawl_metrics.inc('jina_successes', retailer)
    return jina_md, ''  # Jina returns markdown only; HTML parsers won't run


def _do_scrape(fc, api_version: str, url: str, formats: list | None = None, wait_for_ms: int = 0,
               retailer: str = "") -> tuple[str, str]:
    fmts = formats or ['markdown', 'html']
    try:
        _crawl_metrics.inc('firecrawl_calls', retailer)
        if api_version == 'v2':
            kwargs = {'formats': fmts}
            if wait_for_ms:
                kwargs['wait_for'] = wait_for_ms
            resp = fc.scrape(url, **kwargs)
            markdown = getattr(resp, 'markdown', None) or ''
            html = getattr(resp, 'html', None) or getattr(resp, 'content', None) or ''
        else:
            scrape_opts: dict = {'formats': fmts}
            if wait_for_ms:
                scrape_opts['wait_for'] = wait_for_ms
            try:
                result = fc.scrape_url(url, scrape_opts)
            except TypeError:
                result = fc.scrape_url(url, fmts)
            if not isinstance(result, dict):
                return _try_jina(url, retailer)
            markdown = result.get('markdown') or ''
            html = result.get('html') or ''

        if markdown or html:
            _crawl_metrics.inc('firecrawl_successes', retailer)
            return markdown, html

        # Empty response — fall through to Jina
        raise RuntimeError("Firecrawl returned empty content")

    except Exception as exc:
        exc_str = str(exc).lower()
        if any(e in exc_str for e in _PAYMENT_ERRORS):
            _crawl_metrics.inc('firecrawl_credit_exhausted', retailer)
            logging.warning("Firecrawl credits exhausted — last-ditch Jina for %s", url)
        else:
            _crawl_metrics.inc('firecrawl_failures', retailer)
            logging.warning("Firecrawl scrape failed (%s) — last-ditch Jina for %s", exc, url)

        return _try_jina(url, retailer)


def _scrape_via_scraperapi(url: str, render_js: bool = False, retailer: str = "") -> tuple[str, str]:
    """Tier-2 paid scraper. Cheaper than Firecrawl (~$0.001/req vs ~$0.01-0.04).
    Returns (markdown_or_html, html). For now returns html as both, since ScraperAPI
    returns raw HTML — downstream parsers in this module also accept HTML.

    Requires SCRAPER_API_KEY env var. If not set, returns ('','') so caller can fall through.
    """
    api_key = (os.getenv("SCRAPER_API_KEY") or "").strip()
    if not api_key:
        return '', ''

    import urllib.request as _ureq
    from urllib.parse import urlencode

    params = {
        'api_key': api_key,
        'url': url,
        'country_code': 'us',
    }
    if render_js:
        params['render'] = 'true'

    api_url = f"https://api.scraperapi.com/?{urlencode(params)}"
    req = _ureq.Request(api_url, headers={'Accept': 'text/html, */*'})

    try:
        _crawl_metrics.inc('scraperapi_calls', retailer)
        with _ureq.urlopen(req, timeout=30) as resp:
            html = resp.read().decode('utf-8', errors='replace')
        if html and len(html) > 800:
            _crawl_metrics.inc('scraperapi_successes', retailer)
            # Most parsers in this module read either markdown or html. We don't have
            # a markdown converter here, so pass html through both slots — markdown-only
            # parsers will fail and fall through to html parsing where present.
            return html, html
        return '', ''
    except Exception as exc:
        _crawl_metrics.inc('scraperapi_failures', retailer)
        logging.warning("ScraperAPI failed for %s: %s", url, exc)
        return '', ''


def _scrape(url: str, formats: list | None = None, wait_for_ms: int = 0,
            retailer: str = "") -> tuple[str, str]:
    """Unified scrape helper. Provider order driven by SCRAPER_PROVIDER env var:
        - 'firecrawl' (default): Firecrawl primary, Jina last-ditch with quality gate
        - 'scraperapi': ScraperAPI primary, Firecrawl fallback, Jina last-ditch
        - 'firecrawl-then-scraperapi': Try Firecrawl first; on credit exhaustion or empty,
          escalate to ScraperAPI (preserves Firecrawl quality but caps spend at credits)

    Tier flow per call: Tier-2 paid (if configured) → Tier-3 Firecrawl → Tier-4 Jina (gated).
    See docs/11 - Crawl Strategy.md for the full strategy.
    """
    provider = (os.getenv("SCRAPER_PROVIDER") or "firecrawl").strip().lower()
    fc_api_key = (os.getenv("FIRECRAWL_API_KEY") or "").strip()
    sa_api_key = (os.getenv("SCRAPER_API_KEY") or "").strip()

    # ScraperAPI primary
    if provider == 'scraperapi' and sa_api_key:
        md, html = _scrape_via_scraperapi(url, render_js=bool(wait_for_ms), retailer=retailer)
        if md or html:
            return md, html
        # fall through to Firecrawl

    # Firecrawl path (default)
    if fc_api_key:
        try:
            fc, api_version = _init_firecrawl(fc_api_key)
            md, html = _do_scrape(fc, api_version, url, formats=formats,
                                  wait_for_ms=wait_for_ms, retailer=retailer)
            if md or html:
                return md, html
        except Exception as exc:
            logging.warning("Firecrawl init failed: %s", exc)

    # firecrawl-then-scraperapi escalation: try ScraperAPI after Firecrawl miss/fail
    if provider == 'firecrawl-then-scraperapi' and sa_api_key:
        md, html = _scrape_via_scraperapi(url, render_js=bool(wait_for_ms), retailer=retailer)
        if md or html:
            return md, html

    # Last-ditch: Jina (rarely useful in practice — see strategy doc)
    logging.warning("All paid scrapers missed for %s — last-ditch Jina", url)
    return _try_jina(url, retailer)


def _parse_walmart_product_page(markdown: str, html: str) -> dict:
    """Extract product fields from a scraped Walmart product page."""
    result: dict = {k: None for k in ("title", "brand", "model", "upc", "price", "image_url")}

    # Title: prefer og:title or itemprop="name" from HTML
    title_m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html)
    if not title_m:
        title_m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', html)
    if title_m:
        result["title"] = title_m.group(1).strip()
    else:
        # Fallback: first non-trivial markdown heading
        for m in re.finditer(r'^#{1,2}\s+(.+)', markdown, re.MULTILINE):
            candidate = m.group(1).strip()
            if len(candidate) > 10 and 'walmart' not in candidate.lower():
                result["title"] = candidate
                break

    # Price: look for $ amounts
    price_m = re.search(r'\$\s*([\d,]+\.\d{2})', markdown)
    if price_m:
        try:
            result["price"] = float(price_m.group(1).replace(",", ""))
        except ValueError:
            pass

    # Brand and model from product detail sections
    def _find_field(label: str) -> str | None:
        m2 = re.search(rf'(?i){re.escape(label)}\s*[:\|]\s*([^\n|<]+)', markdown)
        return m2.group(1).strip() if m2 else None

    result["brand"] = _find_field("Brand") or _find_field("Manufacturer")
    result["model"] = _find_field("Model") or _find_field("Model Number") or _find_field("Part Number")
    result["upc"] = _find_field("UPC") or _find_field("GTIN")

    # Image
    img_m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html)
    if not img_m:
        img_m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html)
    if img_m:
        result["image_url"] = img_m.group(1)

    return result


def _walmart_item_id_from_url(url: str) -> str | None:
    """Extract Walmart item ID from a walmart.com/ip/... URL."""
    m = re.search(r'/ip/(?:[^/]+/)?(\d+)', url)
    return m.group(1) if m else None


def _extract_walmart_identity(source_url: str) -> dict:
    """Extract product identity from a Walmart product URL.

    Scrapes the Walmart PDP to get title, brand, model, and price so that
    cross-retailer searches use real product data rather than URL slugs.
    Never raises — returns a dict with all keys always present.
    """
    item_id = _walmart_item_id_from_url(source_url)

    # Start with a URL-slug fallback query
    slug_query = item_id or re.sub(r'[^a-zA-Z0-9 ]', ' ', source_url).strip()[:60]
    identity = _empty_identity(slug_query)

    try:
        markdown, html = _scrape(source_url, retailer="walmart")
        if markdown or html:
            parsed = _parse_walmart_product_page(markdown, html)
            identity.update(parsed)
    except Exception as exc:
        logging.warning("Walmart identity extraction failed: %s", exc)

    identity["search_query"] = _build_search_query(identity)
    return identity


def _extract_amazon_identity(source_url: str) -> dict:
    """Extract product identity from an Amazon product URL.

    ASIN extraction is done via regex on the URL (free, instant). Firecrawl is
    only called when additional fields (title, brand, price, etc.) are needed.
    Never raises — returns a dict with all keys always present.
    """
    asin = _asin_from_url(source_url)

    # Fallback query from URL slug if everything else fails
    slug_query = asin or re.sub(r'[^a-zA-Z0-9 ]', ' ', source_url).strip()[:60]

    identity = _empty_identity(slug_query)
    identity["asin"] = asin

    try:
        markdown, html = _scrape(source_url, retailer="amazon")
        if markdown or html:
            parsed = _parse_amazon_markdown(markdown, html)
            identity.update(parsed)
    except Exception as exc:
        logging.warning("Amazon identity extraction failed: %s", exc)

    identity["search_query"] = _build_search_query(identity)
    return identity


def _parse_walmart_search_results(markdown: str, html: str) -> list:
    candidates = []
    seen_urls: set = set()

    link_pattern = re.compile(
        r'\[([^\]]{10,200})\]\((https://(?:www\.)?walmart\.com/ip/[^\s\)]+)\)'
    )

    for m in link_pattern.finditer(markdown):
        if len(candidates) >= 5:
            break

        title = m.group(1).strip()
        raw_url = m.group(2).strip()
        clean_url = re.sub(r'\?.*$', '', raw_url)
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)

        affiliate_id = os.getenv("WALMART_AFFILIATE_ID")
        url = f"{clean_url}?affiliates={affiliate_id}" if affiliate_id else clean_url

        price = None
        end = min(len(markdown), m.end() + 300)
        price_m = re.search(r'\$\s*([\d,]+\.\d{2})', markdown[m.end():end])
        if price_m:
            try:
                price = float(price_m.group(1).replace(",", ""))
            except ValueError:
                pass

        candidates.append({"title": title, "price": price, "url": url, "image_url": None})

    if html and candidates:
        img_urls = re.findall(
            r'<img[^>]+src=["\']([^"\']+i5\.walmartimages\.com[^"\']+)["\']', html
        )
        for i, img_url in enumerate(img_urls[:len(candidates)]):
            candidates[i]["image_url"] = img_url

    return candidates


def _search_walmart(identity: dict) -> list:
    """Search Walmart for candidates matching the given product identity."""
    from urllib.parse import quote_plus

    search_query = identity.get("search_query") or ""
    if not search_query:
        return []

    url = f"https://www.walmart.com/search?q={quote_plus(search_query)}"

    try:
        markdown, html = _scrape(url, retailer="walmart")
    except Exception as exc:
        logging.warning("Walmart search scrape failed: %s", exc)
        return []

    if not markdown and not html:
        return []

    try:
        candidates = _parse_walmart_search_results(markdown, html)
        return candidates
    except Exception as exc:
        logging.warning("Failed to parse Walmart search results: %s", exc)
        return []


RETAILER_SEARCHERS["walmart"] = _search_walmart


def _parse_target_results(markdown: str, html: str) -> list:
    candidates = []
    seen_urls: set = set()

    link_pattern = re.compile(
        r'\[([^\]]{10,200})\]\((https://(?:www\.)?target\.com/p/[^\s\)]+)\)'
    )

    for m in link_pattern.finditer(markdown):
        if len(candidates) >= 5:
            break

        title = m.group(1).strip()
        raw_url = m.group(2).strip()
        clean_url = re.sub(r'\?.*$', '', raw_url)
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)

        price = None
        end = min(len(markdown), m.end() + 300)
        price_m = re.search(r'\$\s*([\d,]+\.\d{2})', markdown[m.end():end])
        if price_m:
            try:
                price = float(price_m.group(1).replace(",", ""))
            except ValueError:
                pass

        candidates.append({"title": title, "price": price, "url": clean_url, "image_url": None})

    return candidates


_TARGET_REDSKY_KEYS = [
    # Public web client keys observed in target.com's JS bundle. These rotate occasionally
    # so we try several. If none work, fall through to scraping.
    "9f36aeafbe60771e321a7cc95a78140772ab3e96",
    "ff457966e64d5e877fdbad070f276d18ecec4a01",
]

_TARGET_REDSKY_URL = (
    "https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v2"
    "?key={key}&keyword={q}&channel=WEB&page=/s/{q}&count=12&offset=0"
    "&pricing_store_id=3991&platform=desktop&visitor_id=DEALNOTIFY"
)


def _search_target_redsky(search_query: str) -> list:
    """Tier-1 fast path: Target's internal redsky JSON API. No API key required, free,
    structured. Modeled on _search_bestbuy_json — tries known keys and parses defensively.
    """
    import urllib.request
    import json as _json
    from urllib.parse import quote

    encoded = quote(search_query)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.target.com",
        "Referer": "https://www.target.com/",
    }

    _crawl_metrics.inc('native_api_calls', 'target')
    for key in _TARGET_REDSKY_KEYS:
        url = _TARGET_REDSKY_URL.format(key=key, q=encoded)
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status != 200:
                    continue
                data = _json.loads(resp.read())
        except Exception as exc:
            logging.warning("Target redsky %s failed: %s", key[:8], exc)
            continue

        # Response shape: data → search_response → products → list of product dicts
        products = (
            (data.get("data") or {})
            .get("search", {})
            .get("products")
            or (data.get("data") or {}).get("search_response", {}).get("products")
            or []
        )
        if not products:
            continue

        candidates = []
        for p in products[:5]:
            item = p.get("item") or {}
            name = (
                (item.get("product_description") or {}).get("title")
                or item.get("name")
                or p.get("name")
                or ""
            )
            if not name:
                continue
            price_obj = p.get("price") or {}
            price_raw = (
                price_obj.get("current_retail")
                or price_obj.get("reg_retail")
                or price_obj.get("formatted_current_price")
            )
            try:
                if isinstance(price_raw, str):
                    price_raw = price_raw.replace("$", "").replace(",", "").strip()
                price = float(price_raw) if price_raw is not None else None
            except (TypeError, ValueError):
                price = None
            tcin = item.get("tcin") or p.get("tcin") or ""
            slug = (item.get("enrichment") or {}).get("buy_url") or ""
            if slug and not slug.startswith("http"):
                slug = "https://www.target.com" + slug
            if not slug and tcin:
                slug = f"https://www.target.com/p/-/A-{tcin}"

            image_url = None
            primary_img = (item.get("enrichment") or {}).get("images") or {}
            if primary_img:
                image_url = primary_img.get("primary_image_url") or None

            candidates.append({"title": name, "price": price, "url": slug, "image_url": image_url})

        if candidates:
            _crawl_metrics.inc('native_api_successes', 'target')
            logging.warning("Target redsky API: found %d candidates: %s",
                            len(candidates),
                            [(c['title'][:50], c['price']) for c in candidates[:3]])
            return candidates

    logging.warning("Target redsky: no candidates returned for %r", search_query)
    return []


def _search_target(identity: dict) -> list:
    """Search Target for candidates matching the given product identity."""
    from urllib.parse import quote_plus

    search_query = identity.get("search_query") or ""
    if not search_query:
        return []

    # Fast path: redsky JSON API (no Firecrawl, no anti-bot issues)
    candidates = _search_target_redsky(search_query)
    if candidates:
        return candidates

    url = f"https://www.target.com/s?searchTerm={quote_plus(search_query)}"

    try:
        markdown, html = _scrape(url, formats=['markdown'], wait_for_ms=2000, retailer="target")
    except Exception as exc:
        logging.warning("Target search scrape failed: %s", exc)
        return []

    if not markdown and not html:
        logging.warning("Target search: returned empty content (blocked or JS-only page)")
        return []

    logging.warning("Target search: markdown_len=%d preview=%r", len(markdown), markdown[:300])
    try:
        results = _parse_target_results(markdown, html)
        logging.warning("Target search: found %d candidates: %s", len(results),
                        [(c['title'][:50], c['price']) for c in results[:3]])
        return results
    except Exception as exc:
        logging.warning("Failed to parse Target search results: %s", exc)
        return []


RETAILER_SEARCHERS["target"] = _search_target


def _parse_bestbuy_results(markdown: str, html: str) -> list:
    candidates = []
    seen_urls: set = set()

    # Firecrawl often emits relative URLs (/site/...) rather than absolute ones
    md_link_pattern = re.compile(
        r'\[([^\]]{10,200})\]\(((?:https://(?:www\.)?bestbuy\.com)?/site/[^\s\)]+\.p[^\s\)]*)\)'
    )
    for m in md_link_pattern.finditer(markdown):
        if len(candidates) >= 5:
            break
        title = m.group(1).strip()
        raw_url = m.group(2).strip()
        if not raw_url.startswith('http'):
            raw_url = 'https://www.bestbuy.com' + raw_url
        clean_url = re.sub(r'\?.*$', '', raw_url)
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)
        price = None
        end = min(len(markdown), m.end() + 300)
        price_m = re.search(r'\$\s*([\d,]+\.\d{2})', markdown[m.end():end])
        if price_m:
            try:
                price = float(price_m.group(1).replace(",", ""))
            except ValueError:
                pass
        candidates.append({"title": title, "price": price, "url": clean_url, "image_url": None})

    # HTML fallback: BestBuy product <a> tags wrap nested elements, so we can't match
    # text directly inside <a>...</a>. Instead: collect all product hrefs, then find
    # the nearest aria-label or <h4> heading within the following 600 chars of HTML.
    if not candidates and html:
        href_pattern = re.compile(
            r'href=["\']([^"\']*(?:bestbuy\.com)?/site/[^"\']+\.p\b[^"\']*)["\']',
            re.IGNORECASE,
        )
        heading_pattern = re.compile(
            r'(?:aria-label|title)=["\']([^"\']{10,200})["\']'
            r'|<(?:h[1-6])[^>]*>\s*(?:<[^>]+>)*([^<]{10,200})',
            re.IGNORECASE,
        )
        for m in href_pattern.finditer(html):
            if len(candidates) >= 5:
                break
            raw_url = m.group(1).strip()
            if not raw_url.startswith('http'):
                raw_url = 'https://www.bestbuy.com' + raw_url
            clean_url = re.sub(r'\?.*$', '', raw_url)
            if clean_url in seen_urls:
                continue
            # Look for a product title in the surrounding HTML context
            end = min(len(html), m.end() + 600)
            hm = heading_pattern.search(html[m.start():end])
            title = ''
            if hm:
                title = (hm.group(1) or hm.group(2) or '').strip()
            if not title:
                continue
            seen_urls.add(clean_url)
            candidates.append({"title": title, "price": None, "url": clean_url, "image_url": None})

    return candidates


_BB_JSON_URLS = [
    # Try known Best Buy internal search endpoint patterns
    "https://www.bestbuy.com/api/2.0/json/search?format=json&q={q}&context=product&pageSize=5",
    "https://www.bestbuy.com/api/3.0/json/search?format=json&q={q}&type=product&pageSize=5",
    "https://www.bestbuy.com/api/v1/json/search?format=json&q={q}&type=product&pageSize=5",
]


def _search_bestbuy_json(search_query: str) -> list:
    """Fast path: try Best Buy's internal search JSON endpoints (no API key required)."""
    import urllib.request
    import json as _json
    from urllib.parse import quote_plus

    encoded = quote_plus(search_query)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.bestbuy.com/",
    }

    _crawl_metrics.inc('native_api_calls', 'bestbuy')
    for url_template in _BB_JSON_URLS:
        url = url_template.format(q=encoded)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status != 200:
                    continue
                data = _json.loads(resp.read())
        except Exception as exc:
            logging.warning("Best Buy JSON API %s failed: %s", url, exc)
            continue

        # Parse whichever response shape we got
        products = (
            data.get("products")
            or data.get("items")
            or data.get("searchResults")
            or (data.get("data") or {}).get("products")
            or []
        )
        if not products:
            continue

        candidates = []
        for p in products[:5]:
            name = p.get("name") or p.get("longDescription") or p.get("title") or ""
            if not name:
                continue
            price_raw = p.get("salePrice") or p.get("regularPrice") or p.get("price")
            try:
                price = float(price_raw) if price_raw is not None else None
            except (TypeError, ValueError):
                price = None
            raw_url = p.get("url") or p.get("pdpUrl") or ""
            if raw_url and not raw_url.startswith("http"):
                raw_url = "https://www.bestbuy.com" + raw_url
            sku = str(p.get("sku") or p.get("skuId") or "")
            if not raw_url and sku:
                raw_url = f"https://www.bestbuy.com/site/{sku}.p"
            candidates.append({"title": name, "price": price, "url": raw_url, "image_url": None})

        if candidates:
            _crawl_metrics.inc('native_api_successes', 'bestbuy')
            logging.warning("Best Buy JSON API (%s): found %d candidates: %s",
                            url, len(candidates),
                            [(c['title'][:50], c['price']) for c in candidates[:3]])
            return candidates

    logging.warning("Best Buy JSON API: all endpoints returned no products for %r", search_query)
    return []


def _search_bestbuy(identity: dict) -> list:
    """Search Best Buy for candidates matching the given product identity."""
    from urllib.parse import quote_plus

    search_query = identity.get("search_query") or ""
    if not search_query:
        return []

    # Fast path: internal JSON API (no Firecrawl needed, no anti-bot issues)
    candidates = _search_bestbuy_json(search_query)
    if candidates:
        return candidates

    # Fallback: scrape search page (Firecrawl if available, else Jina)
    url = f"https://www.bestbuy.com/site/searchpage.jsp?st={quote_plus(search_query)}"

    try:
        markdown, html = _scrape(url, formats=['markdown', 'html'], wait_for_ms=3000, retailer="bestbuy")
    except Exception as exc:
        logging.warning("Best Buy search scrape failed: %s", exc)
        return []

    if not markdown and not html:
        logging.warning("Best Buy search: returned empty content (blocked or JS-only page)")
        return []

    logging.warning("Best Buy search (scrape fallback): markdown_len=%d html_len=%d preview=%r",
                    len(markdown), len(html), markdown[:300])
    try:
        results = _parse_bestbuy_results(markdown, html)
        logging.warning("Best Buy search: found %d candidates: %s", len(results),
                        [(c['title'][:50], c['price']) for c in results[:3]])
        return results
    except Exception as exc:
        logging.warning("Failed to parse Best Buy search results: %s", exc)
        return []


RETAILER_SEARCHERS["bestbuy"] = _search_bestbuy


def _parse_costco_results(markdown: str, html: str) -> list:
    candidates = []
    seen_urls: set = set()

    link_pattern = re.compile(
        r'\[([^\]]{10,200})\]\((https://(?:www\.)?costco\.com/[^\s\)]*\.product\.[^\s\)]+)\)'
    )

    for m in link_pattern.finditer(markdown):
        if len(candidates) >= 5:
            break

        title = m.group(1).strip()
        raw_url = m.group(2).strip()
        clean_url = re.sub(r'\?.*$', '', raw_url)
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)

        price = None
        end = min(len(markdown), m.end() + 300)
        price_m = re.search(r'\$\s*([\d,]+\.\d{2})', markdown[m.end():end])
        if price_m:
            try:
                price = float(price_m.group(1).replace(",", ""))
            except ValueError:
                pass

        candidates.append({"title": title, "price": price, "url": clean_url, "image_url": None})

    return candidates


def _search_costco(identity: dict) -> list:
    """Search Costco for candidates matching the given product identity."""
    from urllib.parse import quote_plus

    search_query = identity.get("search_query") or ""
    if not search_query:
        return []

    url = f"https://www.costco.com/CatalogSearch?keyword={quote_plus(search_query)}"

    try:
        markdown, html = _scrape(url, formats=['markdown'], wait_for_ms=3000, retailer="costco")
    except Exception as exc:
        logging.warning("Costco search scrape failed: %s", exc)
        return []

    if not markdown and not html:
        logging.warning("Costco search: returned empty content (blocked or JS-only page)")
        return []

    logging.warning("Costco search: markdown_len=%d preview=%r", len(markdown), markdown[:300])
    try:
        results = _parse_costco_results(markdown, html)
        logging.warning("Costco search: found %d candidates", len(results))
        return results
    except Exception as exc:
        logging.warning("Failed to parse Costco search results: %s", exc)
        return []


RETAILER_SEARCHERS["costco"] = _search_costco


def _parse_amazon_search_results(markdown: str, html: str) -> list:
    candidates = []
    seen_asins: set = set()

    # Amazon search result links contain /dp/ASIN in the URL
    link_pattern = re.compile(
        r'\[([^\]]{10,200})\]\((https://(?:www\.)?amazon\.com/[^\s\)]*?/dp/([A-Z0-9]{10})[^\s\)]*)\)'
    )

    for m in link_pattern.finditer(markdown):
        if len(candidates) >= 5:
            break

        title = m.group(1).strip()
        asin = m.group(3)
        if asin in seen_asins:
            continue
        seen_asins.add(asin)

        clean_url = f"https://www.amazon.com/dp/{asin}"

        price = None
        end = min(len(markdown), m.end() + 300)
        price_m = re.search(r'\$\s*([\d,]+\.\d{2})', markdown[m.end():end])
        if price_m:
            try:
                price = float(price_m.group(1).replace(",", ""))
            except ValueError:
                pass

        candidates.append({"title": title, "price": price, "url": clean_url, "image_url": None})

    return candidates


def _search_amazon(identity: dict) -> list:
    """Search Amazon for candidates matching the given product identity."""
    from urllib.parse import quote_plus

    search_query = identity.get("search_query") or ""
    if not search_query:
        return []

    url = f"https://www.amazon.com/s?k={quote_plus(search_query)}"

    try:
        markdown, html = _scrape(url, retailer="amazon")
    except Exception as exc:
        logging.warning("Amazon search scrape failed: %s", exc)
        return []

    if not markdown and not html:
        logging.warning("Amazon search: Firecrawl returned empty content")
        return []

    logging.warning("Amazon search: markdown_len=%d preview=%r", len(markdown), markdown[:200])
    try:
        results = _parse_amazon_search_results(markdown, html)
        logging.warning("Amazon search: found %d candidates: %s", len(results),
                        [(c['title'][:50], c['price']) for c in results[:3]])
        return results
    except Exception as exc:
        logging.warning("Failed to parse Amazon search results: %s", exc)
        return []


RETAILER_SEARCHERS["amazon"] = _search_amazon


# ---------------------------------------------------------------------------
# Tier-1 retailer-native API stubs — wired when env vars are set.
# These functions return [] when their key is missing, so the existing
# scraping path remains the active route until keys are configured.
# See docs/11 - Crawl Strategy.md → Phase 2 for activation steps.
# ---------------------------------------------------------------------------

# eBay OAuth Application access token cache.
# Tokens live for 2 hours (7200s). We refresh ~5 minutes before expiry to avoid
# in-flight expiry races. Token is shared process-wide, refreshed under a lock.
_ebay_token_lock = threading.Lock()
_ebay_token_state: dict = {"token": None, "expires_at": 0.0}


def _get_ebay_app_token() -> str | None:
    """Fetch an Application access token via OAuth2 client_credentials grant.
    Caches the token in-process and refreshes ~5 minutes before expiry.

    Requires EBAY_APP_ID (Client ID) and EBAY_CERT_ID (Client Secret) env vars.
    Returns None if either is missing or the token request fails.
    """
    import base64
    import urllib.request
    import urllib.parse
    import json as _json

    app_id  = (os.getenv("EBAY_APP_ID")  or "").strip()
    cert_id = (os.getenv("EBAY_CERT_ID") or "").strip()
    if not (app_id and cert_id):
        return None

    now = time.time()
    with _ebay_token_lock:
        cached = _ebay_token_state.get("token")
        expires_at = _ebay_token_state.get("expires_at", 0.0)
        if cached and now < expires_at - 300:  # 5-min safety buffer
            return cached

        # Fetch new token
        creds = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
        body  = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        }).encode()
        req = urllib.request.Request(
            "https://api.ebay.com/identity/v1/oauth2/token",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type":  "application/x-www-form-urlencoded",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                payload = _json.loads(resp.read())
            token   = payload.get("access_token")
            ttl_s   = int(payload.get("expires_in", 7200))
            if not token:
                logging.warning("eBay OAuth: no access_token in response: %s", payload)
                return None
            _ebay_token_state["token"]      = token
            _ebay_token_state["expires_at"] = now + ttl_s
            _crawl_metrics.inc('ebay_token_refreshes', 'ebay')
            logging.warning("eBay OAuth: minted new app token (ttl=%ds)", ttl_s)
            return token
        except Exception as exc:
            logging.warning("eBay OAuth token fetch failed: %s", exc)
            _crawl_metrics.inc('ebay_token_failures', 'ebay')
            return None


def _search_ebay_browse_api(search_query: str) -> list:
    """Tier-1 fast path: eBay Browse API (5000 free calls/day).
    Activated when EBAY_APP_ID + EBAY_CERT_ID env vars are set. Returns [] otherwise.
    Endpoint: https://api.ebay.com/buy/browse/v1/item_summary/search

    Uses an OAuth2 Application access token (client_credentials grant), refreshed
    automatically every ~2h via _get_ebay_app_token().
    """
    token = _get_ebay_app_token()
    if not token:
        return []

    import urllib.request
    import json as _json
    from urllib.parse import quote_plus

    _crawl_metrics.inc('native_api_calls', 'ebay')
    url = f"https://api.ebay.com/buy/browse/v1/item_summary/search?q={quote_plus(search_query)}&limit=5"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
    except Exception as exc:
        logging.warning("eBay Browse API failed: %s", exc)
        return []

    items = data.get("itemSummaries") or []
    candidates = []
    for it in items[:5]:
        title = it.get("title") or ""
        price_obj = it.get("price") or {}
        try:
            price = float(price_obj.get("value")) if price_obj.get("value") else None
        except (TypeError, ValueError):
            price = None
        url_ = it.get("itemWebUrl") or ""
        img = (it.get("image") or {}).get("imageUrl")
        if title and url_:
            candidates.append({"title": title, "price": price, "url": url_, "image_url": img})

    if candidates:
        _crawl_metrics.inc('native_api_successes', 'ebay')
    return candidates


def _search_bestbuy_open_api(search_query: str) -> list:
    """Tier-1 fast path: Best Buy Open API (5000 free calls/day).
    Activated when BESTBUY_API_KEY env var is set. Returns [] otherwise.
    More reliable than the internal JSON path (_search_bestbuy_json) which can
    break without notice. When this returns results, the internal path is skipped.
    """
    api_key = (os.getenv("BESTBUY_API_KEY") or "").strip()
    if not api_key:
        return []

    import urllib.request
    import json as _json
    from urllib.parse import quote

    _crawl_metrics.inc('native_api_calls', 'bestbuy_open')
    # Best Buy Open API multi-term syntax: (search=word1&search=word2&...) — each
    # `search=` is an AND'd filter. Cap at 6 terms to keep the URL short and avoid
    # the API rejecting overly specific queries that would return zero results.
    tokens = [quote(w) for w in search_query.split()[:6] if len(w) >= 2]
    if not tokens:
        return []
    keyword_filter = "&".join(f"search={t}" for t in tokens)
    url = (
        f"https://api.bestbuy.com/v1/products({keyword_filter})"
        f"?apiKey={api_key}&format=json&pageSize=5&show=sku,name,salePrice,regularPrice,url,image"
    )

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
    except Exception as exc:
        logging.warning("Best Buy Open API failed: %s", exc)
        return []

    products = data.get("products") or []
    candidates = []
    for p in products[:5]:
        title = p.get("name") or ""
        price = p.get("salePrice") or p.get("regularPrice")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        url_ = p.get("url") or ""
        if url_ and not url_.startswith("http"):
            url_ = "https://www.bestbuy.com" + url_
        img = p.get("image")
        if title and url_:
            candidates.append({"title": title, "price": price, "url": url_, "image_url": img})

    if candidates:
        _crawl_metrics.inc('native_api_successes', 'bestbuy_open')
    return candidates


def _search_amazon_paapi(search_query: str) -> list:
    """Tier-1 fast path: Amazon Product Advertising API 5.0.
    Activated when AMAZON_PA_ACCESS_KEY, AMAZON_PA_SECRET_KEY, and AMAZON_PA_PARTNER_TAG
    are all set. Returns [] otherwise.

    NOTE: PA-API requires the `python-amazon-paapi` package and AWS Signature v4 signing.
    This stub returns [] until both env vars and the dependency are present, at which
    point the implementation can be filled in (or use the official SDK).
    See: https://webservices.amazon.com/paapi5/documentation/
    """
    access_key = (os.getenv("AMAZON_PA_ACCESS_KEY") or "").strip()
    secret_key = (os.getenv("AMAZON_PA_SECRET_KEY") or "").strip()
    partner_tag = (os.getenv("AMAZON_PA_PARTNER_TAG") or "").strip()
    if not (access_key and secret_key and partner_tag):
        return []

    # Implementation deferred to Phase 2 (see docs/11 - Crawl Strategy.md).
    # Once paapi5-python-sdk is installed and Associates approval is in place, fill in:
    #   1. Construct SearchItemsRequest with Keywords=search_query, ItemCount=5,
    #      Resources=['ItemInfo.Title', 'Offers.Listings.Price', 'Images.Primary.Large']
    #   2. Sign the request with AWS4 (the SDK does this).
    #   3. POST to https://webservices.amazon.com/paapi5/searchitems
    #   4. Map response.SearchResult.Items to candidate dicts.
    logging.warning("Amazon PA-API: keys present but implementation deferred to Phase 2")
    _crawl_metrics.inc('native_api_calls', 'amazon_paapi_stub')
    return []


# Wire native-API fast paths into existing searchers — they no-op when keys absent.
# Order: native API first, then existing scraping path (already handles its own fallbacks).

_original_search_ebay = RETAILER_SEARCHERS.get("ebay")  # not currently registered; placeholder

def _search_ebay(identity: dict) -> list:
    """eBay searcher. Tier-1 (Browse API) → falls through to scraping path if not configured.
    eBay PDPs aren't currently wired as a Compare source (see CLAUDE.md v3 queue), but the
    target-side searcher is registered so users on Amazon/Walmart can compare against eBay.
    """
    search_query = identity.get("search_query") or ""
    if not search_query:
        return []
    # Tier 1: Browse API
    candidates = _search_ebay_browse_api(search_query)
    if candidates:
        return candidates
    # No scraping fallback for eBay yet — would be added if Browse API quota becomes a concern
    return []


RETAILER_SEARCHERS["ebay"] = _search_ebay


# Override Best Buy searcher to try Open API before internal JSON
_search_bestbuy_internal_jsonscrape = _search_bestbuy

def _search_bestbuy_with_native_api(identity: dict) -> list:
    search_query = identity.get("search_query") or ""
    if search_query:
        candidates = _search_bestbuy_open_api(search_query)
        if candidates:
            return candidates
    # Fall through to existing internal JSON + scraping path
    return _search_bestbuy_internal_jsonscrape(identity)


RETAILER_SEARCHERS["bestbuy"] = _search_bestbuy_with_native_api


# Override Amazon searcher to try PA-API before scraping
_search_amazon_scrape = _search_amazon

def _search_amazon_with_native_api(identity: dict) -> list:
    search_query = identity.get("search_query") or ""
    if search_query:
        candidates = _search_amazon_paapi(search_query)
        if candidates:
            return candidates
    return _search_amazon_scrape(identity)


RETAILER_SEARCHERS["amazon"] = _search_amazon_with_native_api


_VALID_CONFIDENCES = {"exact", "likely", "possible", "none"}

_MATCHING_PROMPT = """\
You are a product-matching assistant for a retail price comparison tool.
Given a SOURCE product and a list of CANDIDATE products from another retailer,
identify which candidate (if any) is the same or near-equivalent product.

RULES (apply strictly in order):
1. Brand veto: If the source and a candidate have clearly different brand names, that candidate
   is "none" — do not consider name similarity. A different brand is a different product.
2. Model number rule: If the source title contains a model number (e.g. "WH-1000XM5", "OLED55C3",
   "iPad Pro 11-inch M4"), a candidate with a different or absent model number is "none".
   An exact model number match is near-certain "exact".
3. Variant leniency: Same brand + same model but different color, pack size, or minor spec
   variant → "likely". Do not penalise if one listing omits color while the other specifies it.
4. Use "possible" only when brand or model genuinely cannot be determined from the titles.

Scoring:
- "exact": same brand + same model number + same size/color/variant
- "likely": same brand + same model, minor variant differences OR one listing lacks variant detail
- "possible": similar product but brand/model unclear from the titles given
- "none": different brand, different model number, or clearly a different product

Examples:
SOURCE: Sony WH-1000XM5 Wireless Noise Canceling Headphones, Black
CANDIDATES:
0. Sony WH-1000XM5 Headphones Wireless, Silver
1. Sony WH-1000XM4 Wireless Headphones, Black
→ {"best_index": 0, "confidence": "exact", "reasoning": "Same brand and model WH-1000XM5, only color differs"}

SOURCE: Sony WH-1000XM5 Wireless Noise Canceling Headphones
CANDIDATES:
0. Bose QuietComfort 45 Bluetooth Headphones
→ {"best_index": null, "confidence": "none", "reasoning": "Different brand (Bose vs Sony)"}

Respond with ONLY valid JSON, no prose:
{"best_index": <int or null>, "confidence": "<exact|likely|possible|none>", "reasoning": "<one sentence>"}\
"""


def _price_tier(price: float | None) -> str:
    """Classify a price into budget / mid / premium tier."""
    if price is None:
        return "mid"
    if price >= 300:
        return "premium"
    if price >= 50:
        return "mid"
    return "budget"

# Confidence thresholds per price tier: (likely_min, possible_min)
# Premium products have wider acceptable price variance, so we accept lower keyword overlap.
_TIER_THRESHOLDS: dict[str, tuple[float, float]] = {
    "budget": (0.50, 0.35),
    "mid":    (0.45, 0.30),
    "premium": (0.38, 0.25),
}


def _score_with_keywords(source_identity: dict, candidates: list[dict], retailer: str = "") -> dict:
    """Fallback scorer using weighted token-overlap when LLM APIs are unavailable."""
    _stopwords = {'the', 'a', 'an', 'and', 'or', 'with', 'for', 'in', 'on', 'at', 'of',
                  'to', 'by', 'from', 'is', 'it', 'as', 'pack', 'count', 'oz'}
    # Use search_query for scoring when available — it's already capped to core product words
    # and excludes bundle accessories that inflate the denominator and lower overlap scores.
    source_title = (source_identity.get('search_query') or source_identity.get('title') or '').lower()
    source_words = set(re.findall(r'\b[a-z0-9]+\b', source_title)) - _stopwords
    # Alphanumeric model tokens (e.g. "1000xm5", "b09xs7jwhh") are strong identity signals
    source_model_tokens = {w for w in source_words if re.search(r'[0-9]', w) and len(w) >= 4}
    source_brand = (source_identity.get('brand') or '').lower().strip()
    _brand_pat = re.compile(rf'\b{re.escape(source_brand)}\b') if source_brand else None

    if not source_words:
        return {"confidence": "none", "best_index": None, "reasoning": "No source words to match"}

    # Select confidence thresholds based on source product price tier
    tier = _price_tier(source_identity.get("price"))
    likely_min, possible_min = _TIER_THRESHOLDS[tier]

    # Build weighted source token set: model tokens count 3×, others 1×
    def _weighted_size(token_set: set) -> float:
        return sum(3.0 if t in source_model_tokens else 1.0 for t in token_set)

    source_weighted_total = _weighted_size(source_words)

    best_score = 0.0
    best_idx = None
    for i, c in enumerate(candidates):
        cand_raw = (c.get('title') or '').lower()
        cand_raw = re.sub(r'\*+', '', cand_raw)  # strip Walmart markdown bold markers
        cand_words = set(re.findall(r'\b[a-z0-9]+\b', cand_raw)) - _stopwords
        if not cand_words:
            continue

        # Brand-mismatch veto: if source brand is known and absent from candidate → skip
        if source_brand and not _brand_pat.search(cand_raw):
            continue

        overlap = source_words & cand_words
        weighted_overlap = _weighted_size(overlap)
        score = weighted_overlap / source_weighted_total if source_weighted_total else 0.0

        # Boost: brand + at least one model token both appear → floor at likely_min
        model_hit = source_model_tokens & cand_words
        if source_brand and bool(_brand_pat.search(cand_raw)) and model_hit:
            score = max(score, likely_min + 0.15)

        if score > best_score:
            best_score = score
            best_idx = i

    if best_score >= likely_min:
        confidence = "likely"
    elif best_score >= possible_min:
        confidence = "possible"
    else:
        logging.warning("%s keyword fallback: no match (best_score=%.2f tier=%s source_words=%s)",
                        retailer or "?", best_score, tier, source_words)
        return {"confidence": "none", "best_index": None, "reasoning": "Low keyword overlap"}

    logging.warning("%s keyword fallback: confidence=%s best_score=%.2f tier=%s best_idx=%s best_title=%r",
                    retailer or "?", confidence, best_score, tier, best_idx,
                    candidates[best_idx].get('title', '')[:60] if best_idx is not None else '')
    return {
        "confidence": confidence,
        "best_index": best_idx,
        "reasoning": f"Keyword overlap {best_score:.0%} tier={tier} (LLM unavailable)"
    }


def _score_matches(source_identity: dict, candidates: list[dict], retailer: str = "") -> dict:
    # Keyword scorer is instant — try it first to avoid LLM latency on clear matches
    kw_result = _score_with_keywords(source_identity, candidates, retailer=retailer)
    if kw_result.get("confidence") in ("exact", "likely"):
        return kw_result

    # Keyword score is ambiguous — call LLM for better accuracy on hard cases
    provider = os.environ.get("MATCHING_LLM_PROVIDER", "gemini").lower()
    if provider == "gemini":
        result = _score_with_gemini(source_identity, candidates)
    elif provider == "anthropic":
        result = _score_with_haiku(source_identity, candidates)
    elif provider == "groq":
        result = _score_with_groq(source_identity, candidates)
    else:
        logging.warning("Unknown MATCHING_LLM_PROVIDER=%s — falling back to gemini", provider)
        result = _score_with_gemini(source_identity, candidates)

    # If LLM errored (rate limit, quota, etc.), use keyword result as-is
    if result.get("reasoning") in ("No API key configured",) or \
            str(result.get("reasoning", "")).startswith("Gemini error:"):
        logging.warning("LLM scoring unavailable (%s) — using keyword fallback", result.get('reasoning'))
        kw_result["llm_error"] = result.get("reasoning")
        return kw_result

    return result


def _score_with_gemini(source_identity: dict, candidates: list[dict]) -> dict:
    import json
    import urllib.request

    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        logging.warning("GEMINI_API_KEY not set — returning no-match")
        return {"confidence": "none", "best_index": None, "reasoning": "No API key configured"}

    candidate_lines = "\n".join(
        f"{i}. {c.get('title', 'Unknown')} — ${c.get('price', 'N/A')}"
        for i, c in enumerate(candidates)
    )
    user_content = (
        f"SOURCE: {source_identity.get('title', 'Unknown')} "
        f"(brand={source_identity.get('brand')}, model={source_identity.get('model')}, "
        f"upc={source_identity.get('upc')})\n\n"
        f"CANDIDATES:\n{candidate_lines}"
    )

    payload = json.dumps({
        "contents": [{"parts": [{"text": _MATCHING_PROMPT + "\n\n" + user_content}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0,
            "maxOutputTokens": 512,
        },
    }).encode()

    # Use gemini-2.0-flash — better accuracy, no thinking-token overhead
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={api_key}"
    )

    try:
        req = urllib.request.Request(url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())

        text = body["candidates"][0]["content"]["parts"][0]["text"]
        # Extract JSON even when Gemini adds prose preamble
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            m_text = text[start:end+1]
        else:
            m_text = None
        if m_text:
            result = json.loads(m_text)
        else:
            result = json.loads(text)
    except Exception as exc:
        logging.warning("Gemini scoring failed: %s", exc)
        return {"confidence": "none", "best_index": None, "reasoning": f"Gemini error: {exc}"}

    if result.get("confidence") not in _VALID_CONFIDENCES:
        result["confidence"] = "none"
    if "best_index" not in result:
        result["best_index"] = None
    if "reasoning" not in result:
        result["reasoning"] = ""
    return result


def _score_with_haiku(source_identity: dict, candidates: list[dict]) -> dict:
    raise NotImplementedError("_score_with_haiku: Anthropic provider not yet implemented")


def _score_with_groq(source_identity: dict, candidates: list[dict]) -> dict:
    raise NotImplementedError("_score_with_groq: Groq provider not yet implemented")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_identity(source_url: str, source_retailer: str) -> dict:
    """Public identity extractor. Web layer calls this once, caches the result, and
    passes the identity to find_comparable_product() for every target retailer in the
    same Compare request — saving N-1 redundant source-PDP scrapes.

    Returns _empty_identity() for unsupported retailers so callers always get a dict.
    Never raises.
    """
    if source_retailer == 'amazon':
        return _extract_amazon_identity(source_url)
    if source_retailer == 'walmart':
        return _extract_walmart_identity(source_url)
    # Other source retailers fall back to a slug-only identity (no scrape)
    slug_query = re.sub(r'[^a-zA-Z0-9 ]', ' ', source_url).strip()[:60]
    identity = _empty_identity(slug_query)
    return identity


def canonical_id_from_url(source_url: str, source_retailer: str) -> str | None:
    """Extract a stable per-retailer canonical ID from a PDP URL.
    Used as the cache key for identity and page-dedup tables.
    """
    if source_retailer == 'amazon':
        return _asin_from_url(source_url)
    if source_retailer == 'walmart':
        return _walmart_item_id_from_url(source_url)
    # For retailers without a regex extractor yet, fall back to None (callers will
    # use source_url as the identifier instead).
    return None


def find_comparable_product(
    source_url: str,
    source_retailer: str,
    target_retailer: str,
    identity: dict | None = None,
) -> dict:
    """Find a comparable product on target_retailer for a given source product.

    Returns a match dict on success, or _no_match(...) on failure.
    Raises nothing — callers can always inspect the returned dict.
    """
    if target_retailer not in RETAILER_SEARCHERS:
        return _no_match(f"unsupported target retailer: {target_retailer!r}")

    if RETAILER_SEARCHERS[target_retailer] is None:
        return _no_match(f"searcher for {target_retailer!r} not yet implemented")

    if identity is None:
        if source_retailer == 'amazon':
            identity = _extract_amazon_identity(source_url)
        elif source_retailer == 'walmart':
            identity = _extract_walmart_identity(source_url)
        else:
            # For other sources without a dedicated extractor, construct a minimal identity
            # from the URL slug so searches still have some query to work with.
            slug_query = re.sub(r'[^a-zA-Z0-9 ]', ' ', source_url).strip()[:60]
            identity = _empty_identity(slug_query)

    candidates = RETAILER_SEARCHERS[target_retailer](identity)
    if not candidates:
        return _no_match("no candidates found")

    result = _score_matches(identity, candidates, retailer=target_retailer)
    logging.warning("%s scoring final: confidence=%s best_index=%s reasoning=%r",
                    target_retailer, result.get('confidence'), result.get('best_index'),
                    result.get('reasoning', '')[:80])
    if result.get("confidence") == "none" or result.get("best_index") is None:
        return _no_match("no matching candidate found")

    idx = result["best_index"]
    if not (0 <= idx < len(candidates)):
        return _no_match("scorer returned out-of-range index")

    match = dict(candidates[idx])
    match["confidence"] = result["confidence"]
    match["reasoning"] = result.get("reasoning", "")
    match["llm_error"] = result.get("llm_error")
    return {"match": match, "reason": None}
