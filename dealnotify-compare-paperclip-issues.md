# DealNotify — Compare Feature: Paperclip Issues

**Project:** Cross-Retailer Price Comparison (codename: Compare)
**Repo:** https://github.com/raunak-09/dealnotify
**Deploy:** Push to `main` → Railway auto-deploys
**Read first:** `SKILL.md` (DealNotify project context), `references/db-schema.md`, `references/chrome-extension.md`
**All commit messages:** prefix with `[compare]`

---

## Issue 0 — Environment Setup

**Depends on:** nothing
**Files to touch:** `CLAUDE.md`, Railway environment variables

### Task
Add the following environment variables to Railway before any code is written. They must exist before any deploy that uses them.

```
MATCHING_LLM_PROVIDER=gemini
GEMINI_API_KEY=<get from https://aistudio.google.com/apikey — free, takes 60 seconds>
WALMART_AFFILIATE_ID=        # leave blank for now, code must handle missing gracefully
TARGET_AFFILIATE_ID=         # leave blank
BESTBUY_AFFILIATE_ID=        # leave blank
```

`FIRECRAWL_API_KEY` already exists — do not touch it.

After adding to Railway, append the following section to `CLAUDE.md` in the repo root:

```
## Compare Feature — Env Vars (added [date])
- MATCHING_LLM_PROVIDER: "gemini" | "anthropic" | "groq" — controls which LLM is used for product matching
- GEMINI_API_KEY: Google AI Studio free-tier key
- WALMART_AFFILIATE_ID: Impact Radius affiliate tag for Walmart outbound links
- TARGET_AFFILIATE_ID: (v2 — leave blank for now)
- BESTBUY_AFFILIATE_ID: (v2 — leave blank for now)
```

### Acceptance Criteria
- [ ] All 5 env vars exist in Railway dashboard (blank values are fine for affiliate IDs)
- [ ] `CLAUDE.md` updated in repo with documentation of new vars
- [ ] No secrets committed to git

---

## Issue 1 — Database Migration

**Depends on:** Issue 0
**Files to touch:** `web_app.py` (inside `init_db()` only)

### Task
Add two new tables to `init_db()` using the existing `ALTER TABLE IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS` pattern. These go inside `init_db()` so they are created automatically on Railway deploy.

**Table 1: `product_comparisons`** (comparison cache)

```sql
CREATE TABLE IF NOT EXISTS product_comparisons (
    id SERIAL PRIMARY KEY,
    source_retailer TEXT NOT NULL,
    source_identifier TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_title TEXT,
    source_price NUMERIC(10,2),
    target_retailer TEXT NOT NULL,
    target_url TEXT,
    target_title TEXT,
    target_price NUMERIC(10,2),
    confidence TEXT,
    match_reasoning TEXT,
    cached_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP DEFAULT (NOW() + INTERVAL '48 hours')
);

CREATE INDEX IF NOT EXISTS idx_comparisons_lookup
    ON product_comparisons(source_retailer, source_identifier, target_retailer, expires_at);
```

**Table 2: `comparison_clicks`** (affiliate click tracking)

```sql
CREATE TABLE IF NOT EXISTS comparison_clicks (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    comparison_id INTEGER REFERENCES product_comparisons(id),
    clicked_at TIMESTAMP DEFAULT NOW()
);
```

### Acceptance Criteria
- [ ] `init_db()` is idempotent — safe to run multiple times without error
- [ ] After Railway deploy, both tables exist in production DB
- [ ] Index exists on `product_comparisons`
- [ ] No existing tables or columns were modified

---

## Issue 2 — `price_comparison.py` Module Skeleton

**Depends on:** Issue 1
**Files to touch:** `price_comparison.py` (new file at repo root)

### Task
Create `price_comparison.py` at the repo root alongside `price_monitor.py`. This is the core comparison module. In this issue, build only the skeleton — the public function, the retailer registry, and empty stubs for all private helpers. Do not implement the private helpers yet (those come in Issues 3, 4, and 5).

```python
"""
price_comparison.py — Cross-retailer product comparison for DealNotify.
Reuses Firecrawl (same API key as price_monitor.py) for scraping.
LLM matching is provider-abstracted via MATCHING_LLM_PROVIDER env var.
"""

import os

# Retailer registry — add Target, Best Buy, Costco here in v2
RETAILER_SEARCHERS = {
    "walmart": "_search_walmart",  # replace string with function reference after implementing
}

def find_comparable_product(
    source_url: str,
    source_retailer: str,
    target_retailer: str,
    identity: dict | None = None,
) -> dict:
    """
    Main public function. Called by /api/compare route.

    Returns:
    {
        "target_retailer": str,
        "target_url": str | None,
        "target_title": str | None,
        "target_price": float | None,
        "confidence": "exact" | "likely" | "possible" | "none",
        "match_reasoning": str,
    }
    """
    try:
        if identity is None:
            identity = _extract_amazon_identity(source_url)

        searcher_name = RETAILER_SEARCHERS.get(target_retailer)
        if not searcher_name:
            return _no_match(f"Unsupported target retailer: {target_retailer}")

        candidates = _search_walmart(identity)  # will be dynamic dispatch in v2
        if not candidates:
            return _no_match("No candidates found")

        result = _score_matches(identity, candidates)
        best_index = result.get("best_index")

        if best_index is None or result.get("confidence") == "none":
            return _no_match(result.get("reasoning", "No match"))

        best = candidates[best_index]
        return {
            "target_retailer": target_retailer,
            "target_url": best.get("url"),
            "target_title": best.get("title"),
            "target_price": best.get("price"),
            "confidence": result.get("confidence", "none"),
            "match_reasoning": result.get("reasoning", ""),
        }

    except Exception as e:
        print(f"⚠️ price_comparison.find_comparable_product failed (non-fatal): {e}")
        return _no_match("internal_error")


def _no_match(reason: str) -> dict:
    return {
        "target_retailer": None,
        "target_url": None,
        "target_title": None,
        "target_price": None,
        "confidence": "none",
        "match_reasoning": reason,
    }


def _extract_amazon_identity(url: str) -> dict:
    raise NotImplementedError("Implement in Issue 3")


def _search_walmart(identity: dict) -> list[dict]:
    raise NotImplementedError("Implement in Issue 4")


def _score_matches(source_identity: dict, candidates: list[dict]) -> dict:
    raise NotImplementedError("Implement in Issue 5")


def _score_with_gemini(source_identity: dict, candidates: list[dict]) -> dict:
    raise NotImplementedError("Implement in Issue 5")


def _score_with_haiku(source_identity: dict, candidates: list[dict]) -> dict:
    """
    Stub for Claude Haiku. To implement:
    - POST to https://api.anthropic.com/v1/messages
    - model: "claude-haiku-4-5-20251001"
    - Use ANTHROPIC_API_KEY from env
    - Same prompt as Gemini, same JSON response shape
    """
    raise NotImplementedError("Set MATCHING_LLM_PROVIDER=gemini, or implement Haiku")


def _score_with_groq(source_identity: dict, candidates: list[dict]) -> dict:
    """
    Stub for Groq. To implement:
    - POST to https://api.groq.com/openai/v1/chat/completions (OpenAI-compatible)
    - model: "llama-3.3-70b-versatile"
    - Use GROQ_API_KEY from env
    - Same prompt as Gemini, same JSON response shape
    """
    raise NotImplementedError("Set MATCHING_LLM_PROVIDER=gemini, or implement Groq")
```

### Acceptance Criteria
- [ ] `price_comparison.py` exists at repo root
- [ ] `from price_comparison import find_comparable_product` works without errors
- [ ] No logic is implemented yet — all private helpers raise `NotImplementedError` with helpful messages
- [ ] `find_comparable_product` with a bad retailer returns a `_no_match()` dict, not an exception
- [ ] Module has no side effects on import

---

## Issue 3 — Amazon Identity Extraction

**Depends on:** Issue 2
**Files to touch:** `price_comparison.py`

### Task
Implement `_extract_amazon_identity(url: str) -> dict` in `price_comparison.py`.

Use Firecrawl to scrape the Amazon product page (same API key and pattern as `price_monitor.py`). Return:

```python
{
    "asin": str | None,       # extract from URL: /dp/<ASIN>/ or /gp/product/<ASIN>/
    "title": str | None,
    "brand": str | None,
    "model": str | None,      # from product details table on page
    "upc": str | None,        # from product details table on page; often absent
    "price": float | None,
    "image_url": str | None,
}
```

**ASIN extraction logic:** Use a regex on the URL before firing Firecrawl — if the ASIN can be extracted from the URL, do that first (it's instant and free). Only use Firecrawl if additional identity fields are needed.

**Query construction for Walmart search (used in Issue 4):** After extracting identity, build a search query string in this priority order:
1. If UPC exists: use UPC directly (most precise)
2. If model + brand exist: `"{brand} {model}"`
3. Fallback: first 8 words of title

Attach the query string to the returned dict as `"search_query": str`.

Wrap everything in try/except. On failure, return a dict with all fields set to `None` and `"search_query"` set to a basic string extracted from the URL. Never raise.

### Acceptance Criteria
- [ ] Given an Amazon URL with `/dp/B0XXXXXX/`, ASIN is extracted without firing Firecrawl
- [ ] Given a known Amazon PDP URL, returns a dict with at least `title` and `price` populated
- [ ] Function never raises — all exceptions caught and logged as non-fatal
- [ ] Returned dict always has all keys present (values may be None)

---

## Issue 4 — Walmart Product Search

**Depends on:** Issue 3
**Files to touch:** `price_comparison.py`

### Task
Implement `_search_walmart(identity: dict) -> list[dict]` in `price_comparison.py`.

Use Firecrawl to scrape Walmart's search results page:

```
https://www.walmart.com/search?q={identity["search_query"]}
```

Return up to 5 candidate products as a list of dicts:

```python
[
    {
        "title": str,
        "price": float | None,
        "url": str,            # full Walmart product URL
        "image_url": str | None,
    },
    ...
]
```

**Implementation notes:**
- URL-encode the search query before inserting into the URL
- Extract results from the Firecrawl response — Walmart's search page returns product cards; parse title, price, and URL from each card
- If Firecrawl returns no usable results, return an empty list `[]` (do not raise)
- Cap results at 5 candidates — the LLM doesn't need more

**Error handling:** Wrap in try/except. On Firecrawl failure or parse failure, log as non-fatal and return `[]`.

### Acceptance Criteria
- [ ] Given a search query like `"Sony WH-1000XM5"`, returns at least 1 candidate dict
- [ ] Each candidate dict has all four keys (`title`, `price`, `url`, `image_url`)
- [ ] Returns `[]` (not an exception) when Walmart returns no results or Firecrawl fails
- [ ] Results are capped at 5 items

---

## Issue 5 — LLM Matching with Provider Abstraction

**Depends on:** Issue 4
**Files to touch:** `price_comparison.py`

### Task
Implement `_score_matches`, `_score_with_gemini`, and the provider dispatch in `price_comparison.py`.

**Provider dispatch (implement exactly as shown):**

```python
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
```

**Shared prompt (use verbatim for all providers):**

```python
MATCHING_PROMPT = """You are a product-matching assistant for a retail price comparison tool.

Given a SOURCE product from Amazon and a list of CANDIDATE products from Walmart,
identify which candidate (if any) is the same or near-equivalent product.

Scoring rules:
- "exact": same brand, same model number / UPC, same size/color/variant
- "likely": same brand and product, minor variant differences (e.g., 2-pack vs 3-pack, color)
- "possible": similar product but unclear if actually the same (e.g., generic vs branded)
- "none": no candidate is a real match

Respond with ONLY valid JSON, no prose:
{"best_index": <int or null>, "confidence": "<exact|likely|possible|none>", "reasoning": "<one sentence>"}"""
```

**Gemini implementation:**

```python
def _score_with_gemini(source_identity: dict, candidates: list[dict]) -> dict:
    import requests, json

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("⚠️ GEMINI_API_KEY not set (non-fatal)")
        return {"best_index": None, "confidence": "none", "reasoning": "no_api_key"}

    user_message = f"SOURCE: {json.dumps(source_identity)}\nCANDIDATES: {json.dumps(candidates)}"

    payload = {
        "contents": [{"parts": [{"text": MATCHING_PROMPT + "\n\n" + user_message}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0,
            "maxOutputTokens": 200,
        },
    }

    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}",
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
        # Validate
        if result.get("confidence") not in ("exact", "likely", "possible", "none"):
            result["confidence"] = "none"
        return result
    except Exception as e:
        print(f"⚠️ Gemini matching failed (non-fatal): {e}")
        return {"best_index": None, "confidence": "none", "reasoning": "gemini_error"}
```

Leave `_score_with_haiku` and `_score_with_groq` as `NotImplementedError` stubs (already in place from Issue 2) — do not implement them.

### Acceptance Criteria
- [ ] `MATCHING_LLM_PROVIDER=gemini` with a valid `GEMINI_API_KEY` returns a valid result dict
- [ ] Result always has `best_index`, `confidence`, and `reasoning` keys
- [ ] `confidence` is always one of the four valid values
- [ ] Missing API key returns `{"confidence": "none"}` — does not crash
- [ ] Malformed JSON from Gemini returns `{"confidence": "none"}` — does not crash
- [ ] Spot-check 10 source/candidate pairs manually; results are sensible

---

## Issue 6 — API Endpoint: `POST /api/compare`

**Depends on:** Issue 5
**Files to touch:** `web_app.py`

### Task
Add the `/api/compare` endpoint to `web_app.py`. Follow all existing patterns (auth, rate limiting, DB connection, error handling).

**Route:**

```python
@app.route("/api/compare", methods=["POST"])
def compare_product():
    token = get_token_from_request()
    user = get_user_by_token(token)
    if not user:
        return jsonify({"error": "unauthorized"}), 401

    if rate_limiter.is_rate_limited(f"compare:{user['id']}", max_requests=30, window_seconds=3600):
        return jsonify({"error": "rate_limited"}), 429

    data = request.get_json() or {}
    source_url = data.get("source_url", "").strip()
    if not source_url or "amazon.com" not in source_url:
        return jsonify({"error": "invalid_source_url"}), 400

    asin = data.get("asin") or _extract_asin_from_url(source_url)
    source_identifier = asin or source_url
    target_retailers = data.get("target_retailers", ["walmart"])

    comparisons = []
    for retailer in target_retailers:
        cached = _get_cached_comparison("amazon", source_identifier, retailer)
        if cached:
            comparisons.append({**cached, "cached": True})
            continue

        identity = {
            "asin": asin,
            "title": data.get("title"),
            "price": data.get("price"),
            "search_query": data.get("title", ""),
        }

        from price_comparison import find_comparable_product
        result = find_comparable_product(
            source_url=source_url,
            source_retailer="amazon",
            target_retailer=retailer,
            identity=identity if identity["title"] else None,
        )

        comparison_id = _save_comparison("amazon", source_identifier, source_url,
                                         data.get("title"), data.get("price"),
                                         retailer, result)

        if result["confidence"] in ("exact", "likely"):
            result["target_url"] = wrap_affiliate_link(retailer, result["target_url"])

        comparisons.append({
            "retailer": retailer,
            "url": result["target_url"],
            "title": result["target_title"],
            "price": result["target_price"],
            "savings": round((data.get("price") or 0) - (result["target_price"] or 0), 2),
            "confidence": result["confidence"],
            "comparison_id": comparison_id,
            "cached": False,
        })

    return jsonify({
        "source": {
            "asin": asin,
            "url": source_url,
            "title": data.get("title"),
            "price": data.get("price"),
        },
        "comparisons": comparisons,
    }), 200
```

Also add these helper functions (private, in `web_app.py`):

- `_extract_asin_from_url(url)` — regex extract ASIN from `/dp/<ASIN>/` or `/gp/product/<ASIN>/`
- `_get_cached_comparison(source_retailer, source_identifier, target_retailer)` — DB lookup, returns dict or None
- `_save_comparison(...)` — DB insert into `product_comparisons`, returns `id`

### Acceptance Criteria
- [ ] `POST /api/compare` with valid token + valid Amazon URL returns 200 with `comparisons` array
- [ ] Missing/invalid token returns 401
- [ ] Non-Amazon URL returns 400
- [ ] 31st request in one hour returns 429
- [ ] Cache miss writes a row to `product_comparisons`
- [ ] Second identical request within 48h returns `"cached": true` and is fast (<200ms)
- [ ] "no match" results are also cached (prevents re-scraping known misses)

---

## Issue 7 — Affiliate Link Wrapping

**Depends on:** Issue 6
**Files to touch:** `web_app.py`

### Task
Add `wrap_affiliate_link(retailer: str, url: str) -> str` to `web_app.py`.

```python
def wrap_affiliate_link(retailer: str, url: str | None) -> str | None:
    if not url:
        return None

    affiliate_ids = {
        "walmart": os.environ.get("WALMART_AFFILIATE_ID"),
        "target": os.environ.get("TARGET_AFFILIATE_ID"),
        "bestbuy": os.environ.get("BESTBUY_AFFILIATE_ID"),
    }

    affiliate_id = affiliate_ids.get(retailer)
    if not affiliate_id:
        print(f"⚠️ No affiliate ID configured for {retailer} (non-fatal) — returning unwrapped URL")
        return url

    # Impact Radius tag format (used by Walmart, Target, Best Buy)
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}affid={affiliate_id}"
```

If `WALMART_AFFILIATE_ID` is blank, the function returns the unwrapped URL. This must not crash. Affiliate tags can be added to Railway env later without a code change.

### Acceptance Criteria
- [ ] When `WALMART_AFFILIATE_ID` is set, returned URL contains `affid=<id>` param
- [ ] When `WALMART_AFFILIATE_ID` is blank, returns original URL unchanged
- [ ] `None` input returns `None` (no crash)
- [ ] Works correctly regardless of whether source URL already has query params

---

## Issue 8 — Click Tracking Endpoint

**Depends on:** Issue 7
**Files to touch:** `web_app.py`

### Task
Add `POST /api/compare/click` to `web_app.py`.

```python
@app.route("/api/compare/click", methods=["POST"])
def track_comparison_click():
    token = get_token_from_request()
    user = get_user_by_token(token)
    if not user:
        return "", 204  # silent fail — don't block the click

    data = request.get_json() or {}
    comparison_id = data.get("comparison_id")

    if comparison_id:
        try:
            conn = get_db_conn()
            cur = conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO comparison_clicks (user_id, comparison_id) VALUES (%s, %s)",
                    (user["id"], comparison_id),
                )
                conn.commit()
            finally:
                cur.close()
                conn.close()
        except Exception as e:
            print(f"⚠️ Failed to log comparison click (non-fatal): {e}")

    return "", 204
```

Always returns 204. Click tracking should never block the user from navigating to the retailer.

### Acceptance Criteria
- [ ] `POST /api/compare/click` with valid `comparison_id` inserts a row in `comparison_clicks`
- [ ] Always returns 204 regardless of auth status or DB errors
- [ ] Row includes `user_id` and `comparison_id`

---

## Issue 9 — Chrome Extension Integration

**Depends on:** Issue 8
**Files to touch:** `chrome-extension/manifest.json`, `chrome-extension/content.js`, `chrome-extension/background.js`, `chrome-extension/comparison-panel.js` (new), `chrome-extension/comparison-panel.css` (new)

### Task
Wire the Chrome extension to detect Amazon PDPs and surface comparison results.

**Step 1 — `manifest.json`:** Ensure `host_permissions` and `content_scripts[*].matches` include `"https://*.amazon.com/*"`. Add `comparison-panel.js` and `comparison-panel.css` to the content scripts for Amazon URLs.

**Step 2 — `content.js`:** Add Amazon PDP detection:

```javascript
// Detect Amazon PDP
const isAmazonPDP = /\/dp\/[A-Z0-9]+/i.test(window.location.pathname) ||
                    /\/gp\/product\/[A-Z0-9]+/i.test(window.location.pathname);

if (isAmazonPDP) {
  const asin = window.location.pathname.match(/\/(?:dp|product)\/([A-Z0-9]+)/i)?.[1];
  const title = document.querySelector("#productTitle")?.innerText?.trim();
  const priceText = document.querySelector(".a-price .a-offscreen")?.innerText;
  const price = priceText ? parseFloat(priceText.replace(/[^0-9.]/g, "")) : null;

  chrome.runtime.sendMessage({
    type: "COMPARE_PRODUCT",
    payload: { source_url: window.location.href, asin, title, price, target_retailers: ["walmart"] }
  });
}
```

Listen for the response and call `renderComparisonPanel(data)` from `comparison-panel.js`.

**Step 3 — `background.js`:** Handle `COMPARE_PRODUCT` message. Fetch `/api/compare` with the user's auth token. Forward the response back to the content script. Fail silently — if the request fails or returns an error, send back `null`.

**Step 4 — `comparison-panel.js` (new):** `renderComparisonPanel(comparison)` injects a panel into the DOM only when `comparison.confidence` is `"exact"` or `"likely"`. If confidence is `"possible"` or `"none"`, render nothing.

Panel design:
- Position: floating bottom-right of viewport, `position: fixed`, `z-index: 9999`
- Width: 300px max
- Brand colors: purple `#5b67f8` header bar, white body, `font-family: -apple-system, sans-serif`
- Header: "DealNotify" logo text + "×" close button
- Body: retailer name, product title (truncated to 2 lines), price, savings badge (green `#27ae60`)
- CTA button: "View at Walmart →" in purple `#5b67f8`, full width
- Confidence badge: "Exact match" (dark) or "Likely match" (grey) — small, below title
- On CTA click: fire `POST /api/compare/click` then open `comparison.url` in a new tab
- On "×" click: remove panel from DOM (dismissed for this page session only)

**Step 5 — `comparison-panel.css` (new):** All panel styles scoped under `.dealnotify-compare-panel` class to avoid Amazon CSS conflicts. No `!important` unless absolutely necessary.

### Acceptance Criteria
- [ ] Extension detects Amazon PDPs and fires exactly one `/api/compare` call per page load
- [ ] Panel renders on a known Amazon product that has a Walmart match (confidence exact/likely)
- [ ] Panel does NOT render when confidence is `"possible"` or `"none"` — silent
- [ ] "View at Walmart →" opens affiliate-tagged URL in a new tab
- [ ] "View at Walmart →" click sends `POST /api/compare/click` before navigating
- [ ] "×" closes and removes the panel
- [ ] Panel does not render on non-PDP Amazon pages (search results, homepage, etc.)
- [ ] Extension does not break existing Amazon page functionality
- [ ] No console errors on Amazon PDPs

---

## Issue 10 — Admin Stats Endpoint

**Depends on:** Issue 9
**Files to touch:** `web_app.py`

### Task
Add `GET /api/admin/compare-stats` to `web_app.py`. Use the existing `ADMIN_KEY` auth pattern.

```python
@app.route("/api/admin/compare-stats", methods=["GET"])
def admin_compare_stats():
    admin_key = request.headers.get("X-Admin-Key")
    if admin_key != os.environ.get("ADMIN_KEY"):
        return jsonify({"error": "unauthorized"}), 401

    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM product_comparisons")
        total_comparisons = _fetchone(cur)["count"]

        cur.execute("SELECT COUNT(*) FROM comparison_clicks")
        total_clicks = _fetchone(cur)["count"]

        cur.execute("""
            SELECT confidence, COUNT(*) as count
            FROM product_comparisons
            GROUP BY confidence
        """)
        by_confidence = {row["confidence"]: row["count"] for row in _fetchall(cur)}

        cur.execute("""
            SELECT source_title, COUNT(*) as lookups
            FROM product_comparisons
            WHERE source_title IS NOT NULL
            GROUP BY source_title
            ORDER BY lookups DESC
            LIMIT 10
        """)
        top_products = _fetchall(cur)

        ctr = round(total_clicks / total_comparisons, 4) if total_comparisons > 0 else 0

        return jsonify({
            "total_comparisons": total_comparisons,
            "total_clicks": total_clicks,
            "click_through_rate": ctr,
            "matches_by_confidence": by_confidence,
            "top_source_products": top_products,
        }), 200
    finally:
        cur.close()
        conn.close()
```

### Acceptance Criteria
- [ ] Valid `X-Admin-Key` header returns 200 with all five fields
- [ ] Missing or wrong key returns 401
- [ ] `click_through_rate` is `total_clicks / total_comparisons` (or 0 if no comparisons)
- [ ] `matches_by_confidence` covers all four confidence values that exist in the DB

---

## Issue 11 — Final QA Checklist

**Depends on:** Issues 0–10 complete and deployed to Railway

### Task
Run through all of the following manually and verify before marking this feature done. Document any failures as new issues, do not close this issue until all checks pass.

**Backend:**
- [ ] Known ASIN with Walmart equivalent (e.g. Sony WH-1000XM5, Levi's 501, Instant Pot Duo) → returns `"likely"` or `"exact"` match with valid Walmart URL
- [ ] Amazon-exclusive or obscure ASIN → returns `"confidence": "none"`, `url: null`
- [ ] Cache miss round trip completes in < 8 seconds
- [ ] Cache hit returns in < 200ms
- [ ] Invalid/non-Amazon URL → 400
- [ ] No/wrong auth token → 401
- [ ] 31st request in one hour → 429
- [ ] Admin endpoint returns real numbers after QA session
- [ ] `comparison_clicks` table has rows after clicking through on test products

**Affiliate:**
- [ ] Outbound Walmart URL includes affiliate tag when `WALMART_AFFILIATE_ID` is set
- [ ] If `WALMART_AFFILIATE_ID` is blank, URL is still valid (no crash)

**Chrome Extension:**
- [ ] Panel renders correctly on 5 different Amazon PDPs with known Walmart matches
- [ ] Panel is absent on 3 Amazon-exclusive product pages
- [ ] Panel is absent on Amazon search results page
- [ ] "×" dismisses panel correctly
- [ ] CTA click opens correct Walmart URL with affiliate tag
- [ ] No console errors during any of the above

**Update `CLAUDE.md`:** Add a paragraph summarising what the Compare feature added, which files were created/modified, and what's queued for v2.

---

## Explicitly Out of Scope for This Sprint

Do not build any of the following. If they feel urgent, file a new issue for v2 rather than building now.

- Target, Best Buy, Costco integrations
- Web app / dashboard comparison view
- Email alerts on competitor price drops
- Background comparison of all tracked products
- Price history charts for competitor prices
- Comparison UI inside `dashboard.html`
- Any A/B testing
- Groq or Claude Haiku provider implementations (stubs only)
- Automated tests or CI setup
- Mobile extension
