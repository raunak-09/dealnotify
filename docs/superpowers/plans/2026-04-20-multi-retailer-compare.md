# Multi-Retailer Compare Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the DealNotify Compare feature from Walmart-only to Walmart + Target + Best Buy + Costco, running searches in parallel and displaying all matches sorted cheapest-first.

**Architecture:** Add three new `_search_<retailer>` functions to `price_comparison.py` following the existing `_search_walmart` pattern. Replace the sequential for-loop in `web_app.py`'s `compare_product` with a parallel `ThreadPoolExecutor` dispatch. Update the Chrome extension to request all four retailers and render a multi-row panel.

**Tech Stack:** Python `concurrent.futures.ThreadPoolExecutor`, Firecrawl (existing), Flask (existing), Chrome Extension MV3 (existing).

---

## File Map

| File | Change |
|------|--------|
| `price_comparison.py` | Add `_parse_target_results`, `_search_target`, `_parse_bestbuy_results`, `_search_bestbuy`, `_parse_costco_results`, `_search_costco`; update `RETAILER_SEARCHERS` |
| `web_app.py` | Replace sequential retailer loop with parallel `ThreadPoolExecutor` dispatch in `compare_product` |
| `chrome-extension/background.js` | Expand `target_retailers` to all four |
| `chrome-extension/comparison-panel.js` | Replace single-match render with sorted multi-match list |
| `chrome-extension/comparison-panel.css` | Add multi-row layout styles, remove single-price styles |

---

## Task 1: Add Target searcher to `price_comparison.py`

**Files:**
- Modify: `price_comparison.py` (after line 257, after `RETAILER_SEARCHERS["walmart"] = _search_walmart`)

- [ ] **Step 1: Add `_parse_target_results` and `_search_target`**

Add the following block immediately after line 257 (`RETAILER_SEARCHERS["walmart"] = _search_walmart`):

```python
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
        markdown, html = _do_scrape(fc, api_version, url)
    except Exception as exc:
        logging.warning("Firecrawl Target search failed: %s", exc)
        return []

    if not markdown and not html:
        return []

    try:
        return _parse_target_results(markdown, html)
    except Exception as exc:
        logging.warning("Failed to parse Target search results: %s", exc)
        return []


RETAILER_SEARCHERS["target"] = _search_target
```

- [ ] **Step 2: Verify syntax**

```bash
cd /Users/ronakclawdbot/Documents/Claude/Projects/DealNotify
python3 -c "import price_comparison; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add price_comparison.py
git commit -m "feat: add Target retailer searcher to price_comparison"
```

---

## Task 2: Add Best Buy searcher to `price_comparison.py`

**Files:**
- Modify: `price_comparison.py` (after `RETAILER_SEARCHERS["target"] = _search_target`)

- [ ] **Step 1: Add `_parse_bestbuy_results` and `_search_bestbuy`**

Add immediately after `RETAILER_SEARCHERS["target"] = _search_target`:

```python
def _parse_bestbuy_results(markdown: str, html: str) -> list:
    candidates = []
    seen_urls: set = set()

    link_pattern = re.compile(
        r'\[([^\]]{10,200})\]\((https://(?:www\.)?bestbuy\.com/site/[^\s\)]+\.p[^\s\)]*)\)'
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


def _search_bestbuy(identity: dict) -> list:
    """Search Best Buy for candidates matching the given product identity."""
    from urllib.parse import quote_plus

    search_query = identity.get("search_query") or ""
    if not search_query:
        return []

    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        logging.warning("FIRECRAWL_API_KEY not set — cannot search Best Buy")
        return []

    url = f"https://www.bestbuy.com/site/searchpage.jsp?st={quote_plus(search_query)}"

    try:
        fc, api_version = _init_firecrawl(api_key)
        markdown, html = _do_scrape(fc, api_version, url)
    except Exception as exc:
        logging.warning("Firecrawl Best Buy search failed: %s", exc)
        return []

    if not markdown and not html:
        return []

    try:
        return _parse_bestbuy_results(markdown, html)
    except Exception as exc:
        logging.warning("Failed to parse Best Buy search results: %s", exc)
        return []


RETAILER_SEARCHERS["bestbuy"] = _search_bestbuy
```

- [ ] **Step 2: Verify syntax**

```bash
python3 -c "import price_comparison; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add price_comparison.py
git commit -m "feat: add Best Buy retailer searcher to price_comparison"
```

---

## Task 3: Add Costco searcher to `price_comparison.py`

**Files:**
- Modify: `price_comparison.py` (after `RETAILER_SEARCHERS["bestbuy"] = _search_bestbuy`)

- [ ] **Step 1: Add `_parse_costco_results` and `_search_costco`**

Add immediately after `RETAILER_SEARCHERS["bestbuy"] = _search_bestbuy`:

```python
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
        markdown, html = _do_scrape(fc, api_version, url)
    except Exception as exc:
        logging.warning("Firecrawl Costco search failed: %s", exc)
        return []

    if not markdown and not html:
        return []

    try:
        return _parse_costco_results(markdown, html)
    except Exception as exc:
        logging.warning("Failed to parse Costco search results: %s", exc)
        return []


RETAILER_SEARCHERS["costco"] = _search_costco
```

- [ ] **Step 2: Verify all four retailers registered**

```bash
python3 -c "
import price_comparison as pc
print(list(pc.RETAILER_SEARCHERS.keys()))
assert all(v is not None for v in pc.RETAILER_SEARCHERS.values()), 'None searcher found'
print('OK')
"
```
Expected: `['walmart', 'target', 'bestbuy', 'costco']` then `OK`

- [ ] **Step 3: Commit**

```bash
git add price_comparison.py
git commit -m "feat: add Costco retailer searcher to price_comparison"
```

---

## Task 4: Parallel retailer search in `web_app.py`

**Files:**
- Modify: `web_app.py` — `compare_product` function (around line 2889)

The current code loops over `target_retailers` sequentially. We keep the cache-check loop sequential (cheap), then dispatch uncached retailers in parallel.

- [ ] **Step 1: Add `ThreadPoolExecutor` import**

Find the existing imports block at the top of `web_app.py`. Add to the stdlib imports section (the line with `import os`, `import re`, etc.):

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
```

Verify it's not already there:
```bash
grep "ThreadPoolExecutor" web_app.py
```

- [ ] **Step 2: Replace the sequential search loop with parallel dispatch**

Find this block in `compare_product` (around line 2889–2951):

```python
    for retailer in target_retailers:
        cached = None if force_refresh else _get_cached_comparison('amazon', source_identifier, retailer)
        if cached:
            ...
            continue

        caller_identity = None
        if source_title:
            caller_identity = { ... }

        try:
            match = find_comparable_product(source_url, 'amazon', retailer, identity=caller_identity)
        except Exception as e:
            print(f"❌ /api/compare error: {e}")
            match = None

        comparison_id = _save_comparison(...)
        hit_data = match.get('match') if match else None
        ...
        comparisons.append(entry)
```

Replace it with:

```python
    caller_identity = None
    if source_title:
        caller_identity = {
            'asin': asin,
            'title': source_title,
            'brand': source_title.split()[0] if source_title else None,
            'model': None,
            'upc': None,
            'price': source_price,
            'image_url': None,
            'search_query': ' '.join(w.strip(',') for w in source_title.split()[:5]) if source_title else None,
        }

    # Separate cached vs uncached retailers
    uncached_retailers = []
    for retailer in target_retailers:
        cached = None if force_refresh else _get_cached_comparison('amazon', source_identifier, retailer)
        if cached:
            hit = {
                'retailer': retailer,
                'url': cached['target_url'],
                'title': cached['target_title'],
                'price': float(cached['target_price']) if cached['target_price'] else None,
                'savings': None,
                'confidence': cached['confidence'],
                'comparison_id': cached['id'],
                'cached': True,
            }
            if source_price and cached['target_price']:
                hit['savings'] = round(float(source_price) - float(cached['target_price']), 2)
            comparisons.append(hit)
        else:
            uncached_retailers.append(retailer)

    # Search uncached retailers in parallel
    def _search_retailer(retailer):
        try:
            return retailer, find_comparable_product(source_url, 'amazon', retailer, identity=caller_identity)
        except Exception as e:
            print(f"❌ /api/compare error for {retailer}: {e}")
            return retailer, None

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_search_retailer, r): r for r in uncached_retailers}
        retailer_matches = {}
        for future in as_completed(futures):
            retailer, match = future.result()
            retailer_matches[retailer] = match

    # Score, save and build response entries in original order
    for retailer in uncached_retailers:
        match = retailer_matches.get(retailer)
        comparison_id = _save_comparison(
            'amazon', source_identifier, source_url, source_title, source_price,
            retailer, match,
        )

        hit_data = match.get('match') if match else None
        if hit_data and hit_data.get('confidence') in ('exact', 'likely'):
            hit_data['url'] = wrap_affiliate_link(retailer, hit_data.get('url'))

        entry = {
            'retailer': retailer,
            'url': hit_data.get('url') if hit_data else None,
            'title': hit_data.get('title') if hit_data else None,
            'price': float(hit_data['price']) if hit_data and hit_data.get('price') else None,
            'savings': None,
            'confidence': hit_data.get('confidence') if hit_data else 'none',
            'comparison_id': comparison_id,
            'cached': False,
            'debug_reasoning': (hit_data.get('llm_error') or hit_data.get('reasoning')) if hit_data else None,
        }
        if source_price and entry['price']:
            entry['savings'] = round(float(source_price) - entry['price'], 2)
        comparisons.append(entry)
```

- [ ] **Step 3: Verify Flask app imports without error**

```bash
cd /Users/ronakclawdbot/Documents/Claude/Projects/DealNotify
python3 -c "import web_app; print('OK')"
```
Expected: `OK` (Flask app loads, no syntax errors)

- [ ] **Step 4: Commit**

```bash
git add web_app.py
git commit -m "feat: parallel retailer search in compare_product using ThreadPoolExecutor"
```

---

## Task 5: Update `background.js` to request all four retailers

**Files:**
- Modify: `chrome-extension/background.js`

- [ ] **Step 1: Change `target_retailers` list**

Find:
```js
          target_retailers: ['walmart'],
```

Replace with:
```js
          target_retailers: ['walmart', 'target', 'bestbuy', 'costco'],
```

- [ ] **Step 2: Verify no other `target_retailers` references**

```bash
grep -n "target_retailers" chrome-extension/background.js
```
Expected: one line, showing `['walmart', 'target', 'bestbuy', 'costco']`

- [ ] **Step 3: Commit**

```bash
git add chrome-extension/background.js
git commit -m "feat: request all four retailers in compare message"
```

---

## Task 6: Multi-match panel in `comparison-panel.js`

**Files:**
- Modify: `chrome-extension/comparison-panel.js`

Replace the entire file content with the multi-match implementation:

- [ ] **Step 1: Rewrite `renderComparisonPanel`**

Replace the full file with:

```js
/**
 * DealNotify Chrome Extension — Comparison Panel
 * Renders a floating panel listing all retailer price matches found on Amazon PDPs.
 */

const DN_COMPARE_API_BASE = 'https://www.dealnotify.co';

function renderComparisonPanel(response) {
  const comparisons = response && response.comparisons;
  if (!Array.isArray(comparisons)) return;

  // Collect all exact/likely matches with valid URLs, sorted cheapest first
  const matches = comparisons
    .filter(c => (c.confidence === 'exact' || c.confidence === 'likely') && c.url)
    .sort((a, b) => (a.price != null ? a.price : Infinity) - (b.price != null ? b.price : Infinity));

  if (!matches.length) return;

  // Remove any existing panel
  const existing = document.querySelector('.dealnotify-compare-panel');
  if (existing) existing.remove();

  const sourcePrice = response.source && response.source.price;

  // ── Panel container ──
  const panel = document.createElement('div');
  panel.className = 'dealnotify-compare-panel';

  // ── Header ──
  const header = document.createElement('div');
  header.className = 'dealnotify-compare-panel__header';

  const logo = document.createElement('span');
  logo.className = 'dealnotify-compare-panel__logo';
  logo.textContent = 'DealNotify';

  const closeBtn = document.createElement('button');
  closeBtn.className = 'dealnotify-compare-panel__close';
  closeBtn.textContent = '×';
  closeBtn.setAttribute('aria-label', 'Close');
  closeBtn.addEventListener('click', () => panel.remove());

  header.appendChild(logo);
  header.appendChild(closeBtn);
  panel.appendChild(header);

  // ── Amazon source price row ──
  if (sourcePrice != null) {
    const sourceRow = document.createElement('div');
    sourceRow.className = 'dealnotify-compare-panel__source-row';

    const sourceLabel = document.createElement('span');
    sourceLabel.className = 'dealnotify-compare-panel__source-label';
    sourceLabel.textContent = 'Amazon';

    const sourceAmt = document.createElement('span');
    sourceAmt.className = 'dealnotify-compare-panel__source-price';
    sourceAmt.textContent = `$${sourcePrice.toFixed(2)}`;

    sourceRow.appendChild(sourceLabel);
    sourceRow.appendChild(sourceAmt);
    panel.appendChild(sourceRow);
  }

  // ── One row per matching retailer ──
  matches.forEach((match, idx) => {
    if (idx > 0) {
      const divider = document.createElement('div');
      divider.className = 'dealnotify-compare-panel__divider';
      panel.appendChild(divider);
    }

    const retailerLabel = match.retailer
      ? match.retailer.charAt(0).toUpperCase() + match.retailer.slice(1)
      : 'Retailer';

    const savingsAmt = match.savings;
    const savingsPct = (sourcePrice && match.price != null && sourcePrice > match.price)
      ? Math.round(((sourcePrice - match.price) / sourcePrice) * 100)
      : null;

    const row = document.createElement('div');
    row.className = 'dealnotify-compare-panel__retailer-row';

    // Top line: name + price + savings badge
    const topLine = document.createElement('div');
    topLine.className = 'dealnotify-compare-panel__row-top';

    const nameEl = document.createElement('span');
    nameEl.className = 'dealnotify-compare-panel__retailer-name';
    nameEl.textContent = retailerLabel;

    const priceEl = document.createElement('span');
    priceEl.className = 'dealnotify-compare-panel__retailer-price';
    priceEl.textContent = match.price != null ? `$${match.price.toFixed(2)}` : '';

    topLine.appendChild(nameEl);
    topLine.appendChild(priceEl);

    if (savingsAmt != null && savingsAmt > 0) {
      const savingsBadge = document.createElement('span');
      savingsBadge.className = 'dealnotify-compare-panel__savings';
      savingsBadge.textContent = savingsPct
        ? `Save $${savingsAmt.toFixed(2)} (${savingsPct}%)`
        : `Save $${savingsAmt.toFixed(2)}`;
      topLine.appendChild(savingsBadge);
    }

    row.appendChild(topLine);

    // CTA button
    const cta = document.createElement('button');
    cta.className = 'dealnotify-compare-panel__cta';
    cta.textContent = `View at ${retailerLabel} →`;
    cta.addEventListener('click', async () => {
      try {
        const stored = await new Promise(resolve =>
          chrome.storage.local.get(['dn_token'], resolve)
        );
        const token = stored.dn_token;
        if (token && match.comparison_id) {
          fetch(`${DN_COMPARE_API_BASE}/api/compare/click`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${token}`,
            },
            body: JSON.stringify({ comparison_id: match.comparison_id }),
          }).catch(() => {});
        }
      } catch (_) {}
      window.open(match.url, '_blank', 'noopener');
    });

    row.appendChild(cta);
    panel.appendChild(row);
  });

  document.body.appendChild(panel);
}
```

- [ ] **Step 2: Verify no syntax errors**

```bash
node --check chrome-extension/comparison-panel.js && echo "OK"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add chrome-extension/comparison-panel.js
git commit -m "feat: render all retailer matches as sorted list in comparison panel"
```

---

## Task 7: Update `comparison-panel.css` for multi-row layout

**Files:**
- Modify: `chrome-extension/comparison-panel.css`

- [ ] **Step 1: Replace the full CSS file**

```css
.dealnotify-compare-panel {
  position: fixed;
  bottom: 100px;
  right: 20px;
  z-index: 2147483647;
  width: 320px;
  max-width: calc(100vw - 40px);
  background: #ffffff;
  border-radius: 10px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.18);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  font-size: 14px;
  color: #1a1a2e;
  overflow: hidden;
}

/* ── Header ── */
.dealnotify-compare-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #5b67f8;
  padding: 10px 14px;
}

.dealnotify-compare-panel__logo {
  color: #ffffff;
  font-weight: 700;
  font-size: 14px;
  letter-spacing: 0.3px;
}

.dealnotify-compare-panel__close {
  background: none;
  border: none;
  color: rgba(255, 255, 255, 0.85);
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  padding: 0 2px;
  margin: 0;
}

.dealnotify-compare-panel__close:hover {
  color: #ffffff;
}

/* ── Amazon source price row ── */
.dealnotify-compare-panel__source-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  background: #f8f8fb;
  border-bottom: 1px solid #eee;
}

.dealnotify-compare-panel__source-label {
  font-size: 12px;
  font-weight: 600;
  color: #888;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.dealnotify-compare-panel__source-price {
  font-size: 14px;
  font-weight: 600;
  color: #888;
  text-decoration: line-through;
}

/* ── Retailer rows ── */
.dealnotify-compare-panel__retailer-row {
  padding: 10px 14px 0;
}

.dealnotify-compare-panel__row-top {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}

.dealnotify-compare-panel__retailer-name {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #555;
  min-width: 60px;
}

.dealnotify-compare-panel__retailer-price {
  font-size: 18px;
  font-weight: 700;
  color: #1a1a2e;
}

.dealnotify-compare-panel__savings {
  display: inline-block;
  font-size: 11px;
  font-weight: 600;
  color: #ffffff;
  background: #27ae60;
  border-radius: 4px;
  padding: 2px 6px;
}

/* ── CTA button ── */
.dealnotify-compare-panel__cta {
  display: block;
  width: 100%;
  padding: 8px 10px;
  margin-top: 0;
  margin-bottom: 10px;
  background: #5b67f8;
  color: #ffffff;
  font-size: 13px;
  font-weight: 600;
  text-align: center;
  border: none;
  border-radius: 6px;
  cursor: pointer;
}

.dealnotify-compare-panel__cta:hover {
  background: #4a56e8;
}

/* ── Divider between retailer rows ── */
.dealnotify-compare-panel__divider {
  height: 1px;
  background: #eee;
  margin: 0 14px;
}
```

- [ ] **Step 2: Commit**

```bash
git add chrome-extension/comparison-panel.css
git commit -m "feat: multi-row panel CSS for multi-retailer compare"
```

---

## Task 8: Push and test

- [ ] **Step 1: Push all commits**

```bash
git push origin main
```

- [ ] **Step 2: Wait for Railway to redeploy**

Railway auto-deploys on push. Check logs:
```bash
railway logs --tail 20
```
Wait for: `Serving Flask app 'web_app'`

- [ ] **Step 3: Reload extension in Chrome**

1. Go to `chrome://extensions`
2. Find DealNotify → click ↺ reload

- [ ] **Step 4: Test on Sony WH-1000XM5**

Open `https://www.amazon.com/dp/B09XS7JWHH` and wait ~10 seconds.

Expected: panel appears with multiple retailer rows (Walmart confirmed, Target/Best Buy/Costco will appear if they carry the product). Each row has price + "View at X →" button.

If only Walmart appears, that's still correct — the other retailers may not carry this specific SKU.

- [ ] **Step 5: Check Railway logs for parallel search timing**

```bash
railway logs --tail 50 | grep -E "compare|target|bestbuy|costco|walmart"
```

Each retailer search should fire concurrently. Total request time should be ~2s or less (check Railway response time in logs).

---

## Notes for Implementer

- **URL patterns may need tuning.** The regex patterns for Target (`.../p/...`), Best Buy (`.../site/...`), and Costco (`.../...product...`) are initial guesses based on known URL structures. After the first test run, check what Firecrawl actually returns in the markdown and adjust the `link_pattern` regex in each `_parse_*` function accordingly.
- **Costco has no affiliate program.** `wrap_affiliate_link` already returns the raw URL for any retailer without an env var — Costco links will just be direct.
- **Target/Best Buy affiliate format TBD.** The `wrap_affiliate_link` function appends `?affid=<id>` — exact parameter names for Impact Radius (Target) and CJ Affiliate (Best Buy) may differ. Update when affiliate accounts are created.
- **Firecrawl quota.** We're now making up to 4× more calls per compare. The free tier quota will exhaust faster. Monitor Railway logs for 429 errors.
