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


def _build_search_query(identity: dict) -> str:
    # UPC is useful for matching/verification but Walmart text search returns
    # no results for bare UPC strings — use brand+model or title instead.
    brand = identity.get("brand") or ""
    model = identity.get("model") or ""
    if brand and model:
        return f"{brand} {model}"
    title = identity.get("title") or ""
    if title:
        return " ".join(title.split()[:8])
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


def _do_scrape(fc, api_version: str, url: str, formats: list | None = None, wait_for_ms: int = 0) -> tuple[str, str]:
    fmts = formats or ['markdown', 'html']
    if api_version == 'v2':
        kwargs = {'formats': fmts}
        if wait_for_ms:
            kwargs['wait_for'] = wait_for_ms
        resp = fc.scrape(url, **kwargs)
        markdown = getattr(resp, 'markdown', None) or ''
        html = getattr(resp, 'html', None) or getattr(resp, 'content', None) or ''
        return markdown, html
    scrape_opts: dict = {'formats': fmts}
    if wait_for_ms:
        scrape_opts['wait_for'] = wait_for_ms
    try:
        result = fc.scrape_url(url, scrape_opts)
    except TypeError:
        result = fc.scrape_url(url, fmts)
    if not isinstance(result, dict):
        return '', ''
    return result.get('markdown') or '', result.get('html') or ''


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

    api_key = (os.getenv("FIRECRAWL_API_KEY") or "").strip()
    if not api_key:
        logging.warning("FIRECRAWL_API_KEY not set — returning partial identity")
        identity["search_query"] = _build_search_query(identity)
        return identity

    try:
        fc, api_version = _init_firecrawl(api_key)
        markdown, html = _do_scrape(fc, api_version, source_url)

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

    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        logging.warning("FIRECRAWL_API_KEY not set — cannot search Walmart")
        return []

    url = f"https://www.walmart.com/search?q={quote_plus(search_query)}"

    try:
        fc, api_version = _init_firecrawl(api_key)
        markdown, html = _do_scrape(fc, api_version, url)
    except Exception as exc:
        logging.warning("Firecrawl Walmart search failed: %s", exc)
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


def _search_target(identity: dict) -> list:
    """Search Target for candidates matching the given product identity."""
    from urllib.parse import quote_plus

    search_query = identity.get("search_query") or ""
    if not search_query:
        return []

    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        logging.warning("FIRECRAWL_API_KEY not set — cannot search Target")
        return []

    url = f"https://www.target.com/s?searchTerm={quote_plus(search_query)}"

    try:
        fc, api_version = _init_firecrawl(api_key)
        markdown, html = _do_scrape(fc, api_version, url, formats=['markdown'], wait_for_ms=2000)
    except Exception as exc:
        logging.warning("Firecrawl Target search failed: %s", exc)
        return []

    if not markdown and not html:
        logging.warning("Target search: Firecrawl returned empty content (blocked or JS-only page)")
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

    md_link_pattern = re.compile(
        r'\[([^\]]{10,200})\]\((https://(?:www\.)?bestbuy\.com/site/[^\s\)]+\.p[^\s\)]*)\)'
    )
    for m in md_link_pattern.finditer(markdown):
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

    # Also try HTML if markdown yielded nothing — Best Buy SPAs sometimes render product
    # links in HTML even when JS template literals are unresolved in the markdown output
    if not candidates and html:
        html_link_pattern = re.compile(
            r'<a[^>]+href=["\']([^"\']*bestbuy\.com/site/[^"\']*\.p\b[^"\']*)["\'][^>]*>'
            r'([^<]{10,200})</a>',
            re.IGNORECASE,
        )
        for m in html_link_pattern.finditer(html):
            if len(candidates) >= 5:
                break
            raw_url = m.group(1).strip()
            title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            if not title:
                continue
            clean_url = re.sub(r'\?.*$', '', raw_url)
            if not clean_url.startswith('http'):
                clean_url = 'https://www.bestbuy.com' + clean_url
            if clean_url in seen_urls:
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

    for url_template in _BB_JSON_URLS:
        url = url_template.format(q=encoded)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
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

    # Fallback: Firecrawl scrape (may be blocked by anti-bot)
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        logging.warning("FIRECRAWL_API_KEY not set — cannot search Best Buy via Firecrawl")
        return []

    url = f"https://www.bestbuy.com/site/searchpage.jsp?st={quote_plus(search_query)}"

    try:
        fc, api_version = _init_firecrawl(api_key)
        markdown, html = _do_scrape(fc, api_version, url, formats=['markdown', 'html'], wait_for_ms=1500)
    except Exception as exc:
        logging.warning("Firecrawl Best Buy search failed: %s", exc)
        return []

    if not markdown and not html:
        logging.warning("Best Buy search: Firecrawl returned empty content (blocked or JS-only page)")
        return []

    logging.warning("Best Buy search (Firecrawl fallback): markdown_len=%d html_len=%d preview=%r",
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

    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        logging.warning("FIRECRAWL_API_KEY not set — cannot search Costco")
        return []

    url = f"https://www.costco.com/CatalogSearch?keyword={quote_plus(search_query)}"

    try:
        fc, api_version = _init_firecrawl(api_key)
        markdown, html = _do_scrape(fc, api_version, url, formats=['markdown'], wait_for_ms=3000)
    except Exception as exc:
        logging.warning("Firecrawl Costco search failed: %s", exc)
        return []

    if not markdown and not html:
        logging.warning("Costco search: Firecrawl returned empty content (blocked or JS-only page)")
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

    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        logging.warning("FIRECRAWL_API_KEY not set — cannot search Amazon")
        return []

    url = f"https://www.amazon.com/s?k={quote_plus(search_query)}"

    try:
        fc, api_version = _init_firecrawl(api_key)
        # Amazon search pages are server-rendered — no JS wait needed
        markdown, html = _do_scrape(fc, api_version, url)
    except Exception as exc:
        logging.warning("Firecrawl Amazon search failed: %s", exc)
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


_VALID_CONFIDENCES = {"exact", "likely", "possible", "none"}

_MATCHING_PROMPT = """\
You are a product-matching assistant for a retail price comparison tool.
Given a SOURCE product and a list of CANDIDATE products from another retailer,
identify which candidate (if any) is the same or near-equivalent product.

Scoring rules:
- "exact": same brand, same model number / UPC, same size/color/variant
- "likely": same brand and product, minor variant differences (e.g., 2-pack vs 3-pack, color)
- "possible": similar product but unclear if actually the same
- "none": no candidate is a real match

Respond with ONLY valid JSON, no prose:
{"best_index": <int or null>, "confidence": "<exact|likely|possible|none>", "reasoning": "<one sentence>"}\
"""


def _score_with_keywords(source_identity: dict, candidates: list[dict], retailer: str = "") -> dict:
    """Fallback scorer using token-overlap when LLM APIs are unavailable."""
    _stopwords = {'the', 'a', 'an', 'and', 'or', 'with', 'for', 'in', 'on', 'at', 'of',
                  'to', 'by', 'from', 'is', 'it', 'as', 'pack', 'count', 'oz'}
    source_title = (source_identity.get('title') or '').lower()
    source_words = set(re.findall(r'\b[a-z0-9]+\b', source_title)) - _stopwords
    # Alphanumeric model tokens (e.g. "1000xm5", "b09xs7jwhh") are strong identity signals
    source_model_tokens = {w for w in source_words if re.search(r'[0-9]', w) and len(w) >= 4}
    source_brand = (source_identity.get('brand') or '').lower().strip()

    if not source_words:
        return {"confidence": "none", "best_index": None, "reasoning": "No source words to match"}

    best_score = 0.0
    best_idx = None
    for i, c in enumerate(candidates):
        cand_raw = (c.get('title') or '').lower()
        # Strip markdown bold markers that Walmart markdown contains
        cand_raw = re.sub(r'\*+', '', cand_raw)
        cand_words = set(re.findall(r'\b[a-z0-9]+\b', cand_raw)) - _stopwords
        if not cand_words:
            continue
        overlap = len(source_words & cand_words)
        # Score by recall (how many source terms appear in candidate) since
        # candidate titles contain extra marketing text that inflates denominators
        score = overlap / len(source_words) if source_words else 0.0
        # Boost: if brand + at least one model token both appear in candidate,
        # that's a strong signal — treat as likely regardless of raw recall score
        model_hit = source_model_tokens & cand_words
        if source_brand and source_brand in cand_raw and model_hit:
            score = max(score, 0.6)
        if score > best_score:
            best_score = score
            best_idx = i

    if best_score >= 0.55:
        confidence = "likely"
    elif best_score >= 0.35:
        confidence = "possible"
    else:
        logging.warning("%s keyword fallback: no match (best_score=%.2f, source_words=%s)",
                        retailer or "?", best_score, source_words)
        return {"confidence": "none", "best_index": None, "reasoning": "Low keyword overlap"}

    logging.warning("%s keyword fallback: confidence=%s best_score=%.2f best_idx=%s best_title=%r",
                    retailer or "?", confidence, best_score, best_idx,
                    candidates[best_idx].get('title', '')[:60] if best_idx is not None else '')
    return {
        "confidence": confidence,
        "best_index": best_idx,
        "reasoning": f"Keyword overlap {best_score:.0%} (LLM unavailable)"
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

    # Use gemini-2.0-flash-lite — reliable JSON mode, no thinking-token overhead
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash-lite:generateContent?key={api_key}"
    )

    try:
        req = urllib.request.Request(url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())

        text = body["candidates"][0]["content"]["parts"][0]["text"]
        # Extract JSON even when Gemini adds prose preamble
        json_match = re.search(r'\{[^{}]*"confidence"[^{}]*\}', text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(0))
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
        else:
            # For non-Amazon sources without a pre-built identity, construct a minimal one
            # from the URL slug so searches still have some query to work with
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
