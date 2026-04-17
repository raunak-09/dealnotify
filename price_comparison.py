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
    if identity.get("upc"):
        return identity["upc"]
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

    # Title: first level-1 or level-2 heading
    m = re.search(r'^#{1,2}\s+(.+)', markdown, re.MULTILINE)
    if m:
        result["title"] = m.group(1).strip()

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


def _do_scrape(fc, api_version: str, url: str) -> tuple[str, str]:
    if api_version == 'v2':
        resp = fc.scrape(url, formats=['markdown', 'html'])
        markdown = getattr(resp, 'markdown', None) or ''
        html = getattr(resp, 'html', None) or getattr(resp, 'content', None) or ''
        return markdown, html
    try:
        result = fc.scrape_url(url, formats=['markdown', 'html'])
    except TypeError:
        result = fc.scrape_url(url, {'formats': ['markdown', 'html']})
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

    print(f"🔍 Walmart scrape for '{search_query}': markdown={len(markdown)}ch html={len(html)}ch")
    if markdown:
        print(f"🔍 Markdown snippet: {markdown[:500]}")

    if not markdown and not html:
        return []

    try:
        candidates = _parse_walmart_search_results(markdown, html)
        print(f"🔍 Parsed {len(candidates)} candidates: {[c.get('title','')[:40] for c in candidates]}")
        return candidates
    except Exception as exc:
        logging.warning("Failed to parse Walmart search results: %s", exc)
        return []


RETAILER_SEARCHERS["walmart"] = _search_walmart


_VALID_CONFIDENCES = {"exact", "likely", "possible", "none"}

_MATCHING_PROMPT = """\
You are a product-matching assistant for a retail price comparison tool.
Given a SOURCE product from Amazon and a list of CANDIDATE products from Walmart,
identify which candidate (if any) is the same or near-equivalent product.

Scoring rules:
- "exact": same brand, same model number / UPC, same size/color/variant
- "likely": same brand and product, minor variant differences (e.g., 2-pack vs 3-pack, color)
- "possible": similar product but unclear if actually the same
- "none": no candidate is a real match

Respond with ONLY valid JSON, no prose:
{"best_index": <int or null>, "confidence": "<exact|likely|possible|none>", "reasoning": "<one sentence>"}\
"""


def _score_matches(source_identity: dict, candidates: list[dict]) -> dict:
    provider = os.environ.get("MATCHING_LLM_PROVIDER", "gemini").lower()
    if provider == "gemini":
        return _score_with_gemini(source_identity, candidates)
    elif provider == "anthropic":
        return _score_with_haiku(source_identity, candidates)
    elif provider == "groq":
        return _score_with_groq(source_identity, candidates)
    else:
        print(f"⚠️ Unknown MATCHING_LLM_PROVIDER={provider} (non-fatal), falling back to gemini")
        return _score_with_gemini(source_identity, candidates)


def _score_with_gemini(source_identity: dict, candidates: list[dict]) -> dict:
    import json
    import urllib.request

    api_key = os.getenv("GEMINI_API_KEY")
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
            "maxOutputTokens": 200,
        },
    }).encode()

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={api_key}"
    )

    try:
        req = urllib.request.Request(url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())

        text = body["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
    except Exception as exc:
        logging.warning("Gemini scoring failed: %s", exc)
        return {"confidence": "none", "best_index": None, "reasoning": "Scoring error"}

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
        identity = _extract_amazon_identity(source_url)

    candidates = RETAILER_SEARCHERS[target_retailer](identity)
    if not candidates:
        return _no_match("no candidates found")

    result = _score_matches(identity, candidates)
    if result.get("confidence") == "none" or result.get("best_index") is None:
        return _no_match("no matching candidate found")

    idx = result["best_index"]
    if not (0 <= idx < len(candidates)):
        return _no_match("scorer returned out-of-range index")

    match = dict(candidates[idx])
    match["confidence"] = result["confidence"]
    match["reasoning"] = result.get("reasoning", "")
    return {"match": match, "reason": None}
