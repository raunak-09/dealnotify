# Multi-Retailer Compare — Design Spec
Date: 2026-04-20

## Summary

Extend the DealNotify Compare feature from Walmart-only to four retailers: Walmart, Target, Best Buy, and Costco. The comparison panel will display all retailers that have a matching product, sorted cheapest-first.

## Decisions Made

- **Search strategy:** Sequential (Option A) — extend the current per-retailer loop in `web_app.py`
- **Panel display:** Show ALL matching retailers as a list (not just the cheapest)
- **Costco:** Include with direct links; no affiliate wrapping (no public program)
- **Affiliate programs:** Target (Impact Radius via `TARGET_AFFILIATE_ID`), Best Buy (CJ Affiliate via `BESTBUY_AFFILIATE_ID`)

---

## Backend — `price_comparison.py`

### New searcher functions

Add three functions following the same pattern as `_search_walmart`:

```python
def _search_target(identity: dict) -> list: ...
def _search_bestbuy(identity: dict) -> list: ...
def _search_costco(identity: dict) -> list: ...
```

Each function:
1. Builds a search query from `identity.get("search_query")`
2. Constructs the retailer's search URL:
   - Target: `https://www.target.com/s?searchTerm=<query>`
   - Best Buy: `https://www.bestbuy.com/site/searchpage.jsp?st=<query>`
   - Costco: `https://www.costco.com/CatalogSearch?keyword=<query>`
3. Uses Firecrawl to scrape the search results page (same `_do_scrape` helper)
4. Parses markdown links matching each retailer's product URL pattern:
   - Target: `target.com/p/...`
   - Best Buy: `bestbuy.com/site/...`
   - Costco: `costco.com/...`
5. Returns a list of candidate dicts: `{title, url, price, retailer}`

### Price parsing per retailer

Each retailer's search results page uses different markup. The markdown parser should extract prices using the same `re.search(r'\$[\d,]+\.?\d*', ...)` pattern already used for Walmart.

### `RETAILER_SEARCHERS` update

```python
RETAILER_SEARCHERS = {
    'walmart': _search_walmart,
    'target': _search_target,
    'bestbuy': _search_bestbuy,
    'costco': _search_costco,
}
```

---

## Backend — `web_app.py`

### `wrap_affiliate_link()` update

Add cases for `target` and `bestbuy`:

```python
def wrap_affiliate_link(retailer: str, url: str) -> str:
    if retailer == 'walmart':
        # existing Walmart Impact Radius logic
    elif retailer == 'target':
        tag = os.environ.get('TARGET_AFFILIATE_ID', '')
        return f"{url}{'&' if '?' in url else '?'}afid={tag}" if tag else url
    elif retailer == 'bestbuy':
        tag = os.environ.get('BESTBUY_AFFILIATE_ID', '')
        return f"{url}{'&' if '?' in url else '?'}ref={tag}" if tag else url
    return url  # costco: no wrapping
```

The exact affiliate URL format will depend on the respective programs; use placeholder format above until affiliate accounts are created.

### `compare_product` endpoint — no change needed

The existing for-loop over `target_retailers` already handles multiple retailers. The extension will now pass all four in the request.

### Railway env vars to add (when affiliate accounts are ready)

- `TARGET_AFFILIATE_ID`
- `BESTBUY_AFFILIATE_ID`

Leave blank for now; `wrap_affiliate_link` falls back to the raw URL.

---

## Chrome Extension — `background.js`

Change:
```js
target_retailers: ['walmart'],
```
To:
```js
target_retailers: ['walmart', 'target', 'bestbuy', 'costco'],
```

---

## Chrome Extension — `comparison-panel.js`

Replace single-match rendering with a sorted list of all `exact`/`likely` matches.

### Logic changes

```js
// Before: find first exact/likely match
const match = comparisons.find(c => c.confidence === 'exact' || c.confidence === 'likely');

// After: collect all exact/likely matches, sort cheapest first
const matches = comparisons
  .filter(c => (c.confidence === 'exact' || c.confidence === 'likely') && c.url)
  .sort((a, b) => (a.price ?? Infinity) - (b.price ?? Infinity));

if (!matches.length) return;
```

### Panel structure

```
┌─────────────────────────────────────┐
│ DealNotify                        × │  ← header (unchanged)
├─────────────────────────────────────┤
│ Amazon $279.99                       │  ← source price row
├─────────────────────────────────────┤
│ Walmart      $248.00  Save $31 (11%) │  ← retailer row
│ [View at Walmart →]                  │
├─────────────────────────────────────┤
│ Target       $252.00  Save $27 (9%)  │
│ [View at Target →]                   │
├─────────────────────────────────────┤
│ Best Buy     $259.00  Save $20 (7%)  │
│ [View at Best Buy →]                 │
└─────────────────────────────────────┘
```

Each retailer row:
- Retailer name (bold)
- Price (large)
- Savings badge (green, if cheaper than Amazon)
- CTA button

If only one retailer matches, the panel looks the same as today but with the list structure.

---

## Chrome Extension — `comparison-panel.css`

Add styles for:
- `.dealnotify-compare-panel__source-price` — Amazon price row at top
- `.dealnotify-compare-panel__retailer-row` — one row per matching retailer
- `.dealnotify-compare-panel__divider` — thin separator between rows

Remove the single-price styles that are replaced by the list layout.

---

## Error Handling

- If a retailer search fails (Firecrawl error, parse error), log the error and skip that retailer — do not fail the whole request
- If all retailers return `confidence: none`, return the existing `none` response and show no panel
- Rate limit and caching behavior unchanged — each `(source, retailer)` pair is cached independently

---

## Out of Scope

- Parallel search (can be added later as a performance optimization)
- Best Buy / Target searcher fine-tuning (URL patterns and price parsing may need iteration after testing)
- Costco affiliate program (no public program available)
- Other retailers (eBay, Newegg, etc.)
