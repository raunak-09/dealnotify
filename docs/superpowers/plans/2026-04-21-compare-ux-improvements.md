# Compare UX Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the compare widget to all users (including unauthenticated), show results progressively as each retailer resolves, fix the "Best price" badge to compare against the current page price, and improve matching accuracy.

**Architecture:** Three coordinated changes: (1) background fans out parallel per-retailer API calls and pushes partials to the content tab; (2) unauthenticated users see a sign-in CTA in the Compare tab that auto-triggers compare on login; (3) the Gemini matching model and prompt are upgraded for higher accuracy.

**Tech Stack:** Chrome Extension MV3 (background service worker, content scripts), Flask/Python backend (price_comparison.py), Gemini REST API

---

## File Map

| File | Change |
|------|--------|
| `chrome-extension/background.js` | Fan out parallel fetches; push COMPARE_RESULT_PARTIAL per retailer; return `{ unauthenticated: true }` when no token |
| `chrome-extension/content.js` | Handle streaming ack + unauth response; add COMPARE_RESULT_PARTIAL/COMPARE_DONE listener; add storage change listener for auto-retry on sign-in |
| `chrome-extension/comparison-panel.js` | Replace `renderComparisonPanel` with `appendComparisonResult` + `finalizeComparisonPanel`; add `renderUnauthPanel` |
| `chrome-extension/comparison-panel.css` | Add `.dealnotify-compare-panel__unauth-*` styles; add `.dealnotify-compare-panel__best-note` |
| `price_comparison.py` | Upgrade Gemini model to `gemini-2.0-flash`; improve `_MATCHING_PROMPT` (brand veto, model-number rule, few-shot examples); improve `_score_with_keywords` (weighted model tokens, brand-mismatch veto, lower threshold) |

---

## Task 1: Progressive Results + Badge/Price Fixes

**Spec:** Section 2 (progressive results), Section 4 (badge + null price)

**Files:**
- Modify: `chrome-extension/background.js` (COMPARE_PRODUCT handler)
- Modify: `chrome-extension/content.js` (response handler + new message listener)
- Modify: `chrome-extension/comparison-panel.js` (replace renderComparisonPanel, add appendComparisonResult + finalizeComparisonPanel, update showComparisonLoadingPanel)

---

- [ ] **Step 1: Update `showComparisonLoadingPanel` to store outOfStock on the panel**

In `chrome-extension/comparison-panel.js`, find the line `document.body.appendChild(panel);` near the end of `showComparisonLoadingPanel` and add `panel.dataset.dnOutOfStock` before it:

Replace this block (near the bottom of `showComparisonLoadingPanel`, before `document.body.appendChild(panel)`):
```js
  panel.appendChild(comparePane);

  // ── Track pane ──
  panel.appendChild(_buildTrackPane(!!outOfStock));

  document.body.appendChild(panel);
}
```
With:
```js
  panel.appendChild(comparePane);

  // ── Track pane ──
  panel.appendChild(_buildTrackPane(!!outOfStock));

  panel.dataset.dnOutOfStock = outOfStock ? '1' : '0';
  document.body.appendChild(panel);
}
```

- [ ] **Step 2: Replace `renderComparisonPanel` with `appendComparisonResult` + `finalizeComparisonPanel`**

In `chrome-extension/comparison-panel.js`, delete the entire `renderComparisonPanel` function (lines starting `// ── Render final comparison results ──` through the closing `}`). Replace it with these two functions:

```js
// ── Progressive: add one retailer row as its result arrives ──

function appendComparisonResult(match, source) {
  const panel = document.querySelector('.dealnotify-compare-panel');
  if (!panel) return;
  const comparePane = panel.querySelector('[data-dn-pane="compare"]');
  if (!comparePane) return;

  const sourcePrice = source && typeof source.price === 'number' ? source.price : null;
  const retailerLabel = DN_RETAILER_LABELS[match.retailer] || (match.retailer
    ? match.retailer.charAt(0).toUpperCase() + match.retailer.slice(1)
    : 'Retailer');

  const savingsAmt = sourcePrice != null && match.price < sourcePrice
    ? parseFloat((sourcePrice - match.price).toFixed(2))
    : (match.savings != null ? match.savings : null);
  const savingsPct = (savingsAmt && sourcePrice)
    ? Math.round((savingsAmt / sourcePrice) * 100)
    : null;

  // Build retailer row
  const row = document.createElement('div');
  row.className = 'dealnotify-compare-panel__retailer-row';

  const topLine = document.createElement('div');
  topLine.className = 'dealnotify-compare-panel__row-top';

  const nameLine = document.createElement('div');
  nameLine.className = 'dealnotify-compare-panel__name-line';

  const nameEl = document.createElement('span');
  nameEl.className = 'dealnotify-compare-panel__retailer-name';
  nameEl.textContent = retailerLabel;
  nameLine.appendChild(nameEl);

  const priceEl = document.createElement('span');
  priceEl.className = 'dealnotify-compare-panel__retailer-price';
  priceEl.textContent = `$${match.price.toFixed(2)}`;

  topLine.appendChild(nameLine);
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

  // Place row: replace first shimmer row, or append before loading hint
  const shimmerRow = comparePane.querySelector('.dealnotify-compare-panel__shimmer-row');
  const hint = comparePane.querySelector('.dealnotify-compare-panel__loading-hint');

  if (shimmerRow) {
    // Replace shimmer in-place; the divider before the next shimmer becomes the row separator
    comparePane.insertBefore(row, shimmerRow);
    shimmerRow.remove();
  } else {
    // All shimmer rows already replaced — insert before hint with a separator
    const existingRows = comparePane.querySelectorAll('.dealnotify-compare-panel__retailer-row');
    if (existingRows.length > 0) {
      const divider = document.createElement('div');
      divider.className = 'dealnotify-compare-panel__divider';
      comparePane.insertBefore(divider, hint || null);
    }
    comparePane.insertBefore(row, hint || null);
  }

  // Track the best match seen so far on the panel element for use in finalizeComparisonPanel
  const currentBestPrice = panel.dataset.dnBestPrice ? parseFloat(panel.dataset.dnBestPrice) : Infinity;
  if (match.price < currentBestPrice) {
    panel.dataset.dnBestPrice = String(match.price);
    panel.dataset.dnBestRetailer = match.retailer;
    panel.dataset.dnBestUrl = match.url || '';
    panel.dataset.dnBestComparisonId = match.comparison_id || '';
    panel.dataset.dnSourcePrice = sourcePrice != null ? String(sourcePrice) : '';
  }
}


// ── Progressive: finalize panel once all retailer calls have settled ──

function finalizeComparisonPanel() {
  const panel = document.querySelector('.dealnotify-compare-panel');
  if (!panel) return;
  const comparePane = panel.querySelector('[data-dn-pane="compare"]');
  if (!comparePane) return;

  // Remove any remaining shimmer rows and their preceding dividers
  comparePane.querySelectorAll('.dealnotify-compare-panel__shimmer-row').forEach(shimmer => {
    const prev = shimmer.previousElementSibling;
    if (prev && prev.classList.contains('dealnotify-compare-panel__divider')) {
      prev.remove();
    }
    shimmer.remove();
  });

  // Remove loading hint
  const hint = comparePane.querySelector('.dealnotify-compare-panel__loading-hint');
  if (hint) hint.remove();

  const retailerRows = comparePane.querySelectorAll('.dealnotify-compare-panel__retailer-row');

  if (!retailerRows.length) {
    comparePane.innerHTML = '';
    const noResults = document.createElement('div');
    noResults.className = 'dealnotify-compare-panel__no-results';
    noResults.textContent = 'No better prices found at other retailers.';
    comparePane.appendChild(noResults);
    _activateTab(panel, 'track');
    return;
  }

  // Apply "Best price" badge to cheapest row if it beats the source price
  const sourcePrice = panel.dataset.dnSourcePrice ? parseFloat(panel.dataset.dnSourcePrice) : null;
  const bestPrice = panel.dataset.dnBestPrice ? parseFloat(panel.dataset.dnBestPrice) : null;

  if (bestPrice != null && (sourcePrice == null || bestPrice < sourcePrice)) {
    // Find the row whose price matches bestPrice and badge it
    retailerRows.forEach(row => {
      const priceEl = row.querySelector('.dealnotify-compare-panel__retailer-price');
      if (!priceEl) return;
      const p = parseFloat(priceEl.textContent.replace(/[^0-9.]/g, ''));
      if (Math.abs(p - bestPrice) < 0.01) {
        const nameLine = row.querySelector('.dealnotify-compare-panel__name-line');
        if (nameLine && !nameLine.querySelector('.dealnotify-compare-panel__best-badge')) {
          const badge = document.createElement('span');
          badge.className = 'dealnotify-compare-panel__best-badge';
          badge.textContent = 'Best price';
          nameLine.appendChild(badge);
        }
      }
    });
  } else if (sourcePrice != null) {
    // All competitors are more expensive — source is best
    const sourceRow = comparePane.querySelector('.dealnotify-compare-panel__source-row');
    const sourceLabel = sourceRow
      ? (sourceRow.querySelector('.dealnotify-compare-panel__source-label')?.textContent || 'current retailer')
      : 'current retailer';
    const note = document.createElement('div');
    note.className = 'dealnotify-compare-panel__best-note';
    note.textContent = `You're already at the best price on ${sourceLabel}.`;
    // Insert right after source row (or at top of compare pane)
    if (sourceRow && sourceRow.nextElementSibling) {
      comparePane.insertBefore(note, sourceRow.nextElementSibling);
    } else {
      comparePane.prepend(note);
    }
  }

  // Update Track pane with best competitor price (only if cheaper than source)
  const outOfStock = panel.dataset.dnOutOfStock === '1';
  const bestForTrack = (bestPrice != null && (sourcePrice == null || bestPrice < sourcePrice))
    ? {
        price: bestPrice,
        retailer: panel.dataset.dnBestRetailer,
        savings: sourcePrice != null ? parseFloat((sourcePrice - bestPrice).toFixed(2)) : null,
      }
    : null;

  const trackPane = panel.querySelector('[data-dn-pane="track"]');
  if (trackPane) {
    const newTrack = _buildTrackPane(outOfStock, bestForTrack);
    const wasActive = trackPane.classList.contains('dealnotify-compare-panel__pane--active');
    newTrack.dataset.dnPane = 'track';
    if (wasActive) newTrack.classList.add('dealnotify-compare-panel__pane--active');
    trackPane.replaceWith(newTrack);
  }
}
```

- [ ] **Step 3: Update `background.js` COMPARE_PRODUCT handler to fan out parallel fetches**

Replace the entire `if (message.action === 'COMPARE_PRODUCT')` block in `chrome-extension/background.js`:

```js
  if (message.action === 'COMPARE_PRODUCT') {
    chrome.storage.local.get(['dn_token'], (stored) => {
      const token = stored.dn_token;
      if (!token) { sendResponse(null); return; }

      const ALL_COMPARE_RETAILERS = ['amazon', 'walmart', 'target', 'bestbuy', 'costco'];
      const sourceRetailer = message.source_retailer || 'amazon';
      const targetRetailers = ALL_COMPARE_RETAILERS.filter(r => r !== sourceRetailer);
      const tabId = sender && sender.tab && sender.tab.id;
      const basePayload = {
        source_url:      message.source_url,
        source_retailer: sourceRetailer,
        asin:            message.asin,
        title:           message.title,
        price:           message.price,
      };

      // Acknowledge immediately so the message port can close
      sendResponse({ streaming: true });

      const promises = targetRetailers.map(async (retailer) => {
        try {
          const res = await fetch(`${API_BASE}/api/compare`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${token}`,
            },
            body: JSON.stringify({ ...basePayload, target_retailers: [retailer] }),
          });
          if (!res.ok) return;
          const data = await res.json();
          const match = data.comparisons && data.comparisons.find(c =>
            (c.confidence === 'exact' || c.confidence === 'likely') &&
            c.url && c.price != null
          );
          if (match && tabId) {
            chrome.tabs.sendMessage(tabId, {
              action: 'COMPARE_RESULT_PARTIAL',
              match,
              source: data.source,
            }).catch(() => {});
          }
        } catch (e) {}
      });

      Promise.allSettled(promises).then(() => {
        if (tabId) {
          chrome.tabs.sendMessage(tabId, { action: 'COMPARE_DONE' }).catch(() => {});
        }
      });
    });
    return true; // keep message port open until sendResponse is called
  }
```

- [ ] **Step 4: Update `content.js` response handler and add COMPARE_RESULT_PARTIAL listener**

In `chrome-extension/content.js`, inside `detectAndCompare()`, find the `chrome.runtime.sendMessage` callback and replace it:

Old callback (inside the `chrome.runtime.sendMessage({action: 'COMPARE_PRODUCT', ...}, (response) => { ... })` block):
```js
    (response) => {
      if (chrome.runtime.lastError) {
        const p = document.querySelector('.dealnotify-compare-panel');
        if (p) p.remove();
        return;
      }
      if (response) renderComparisonPanel(response);
      else {
        const p = document.querySelector('.dealnotify-compare-panel');
        if (p) p.remove();
      }
    }
```

New callback:
```js
    (response) => {
      if (chrome.runtime.lastError) {
        const p = document.querySelector('.dealnotify-compare-panel');
        if (p) p.remove();
        return;
      }
      if (!response) {
        const p = document.querySelector('.dealnotify-compare-panel');
        if (p) p.remove();
        return;
      }
      // response.streaming === true — results arrive via COMPARE_RESULT_PARTIAL
    }
```

Then find the existing `chrome.runtime.onMessage.addListener` (the one handling `getProductInfo`) and add the new cases inside it so only one listener is registered:

Replace the existing listener:
```js
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.action === 'getProductInfo') {
      // ... existing extraction code ...
    }
    return true; // keep the message channel open for async response
  });
```

With:
```js
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.action === 'getProductInfo') {
      const domain = getStoreDomain();
      if (!domain || !extractors[domain]) {
        sendResponse(null);
        return;
      }
      try {
        const info = extractors[domain]();
        sendResponse({
          title:      sanitizeText(info.title || document.title, MAX_TITLE_LENGTH),
          price:      cleanPrice(info.price),
          outOfStock: !!info.outOfStock,
          url:        window.location.href
        });
      } catch (e) {
        sendResponse({
          title:      sanitizeText(document.title, MAX_TITLE_LENGTH),
          price:      '',
          outOfStock: false,
          url:        window.location.href
        });
      }
    }

    if (message.action === 'COMPARE_RESULT_PARTIAL') {
      appendComparisonResult(message.match, message.source);
    }

    if (message.action === 'COMPARE_DONE') {
      finalizeComparisonPanel();
    }

    return true;
  });
```

- [ ] **Step 5: Verify Task 1 manually**

Load the unpacked extension in Chrome (`chrome://extensions` → Load unpacked → select `chrome-extension/`). Navigate to an Amazon product page (e.g. `https://www.amazon.com/dp/B0BDHWDR12`). Expected:
- Compare panel appears with shimmer rows
- Within 3–6s, individual retailer rows start appearing (shimmer rows replaced one by one)
- After ~10s: all shimmer rows gone, loading hint removed
- "Best price" badge appears only on a row cheaper than the Amazon price shown
- If Amazon is cheapest, a green "You're already at the best price on Amazon" note appears
- No null-price rows appear (any retailer without a parseable price is silently excluded)

- [ ] **Step 6: Commit**

```bash
git add chrome-extension/background.js chrome-extension/content.js chrome-extension/comparison-panel.js
git commit -m "feat: progressive per-retailer compare results + best price badge fix"
```

---

## Task 2: Unauthenticated UX

**Spec:** Section 1

**Files:**
- Modify: `chrome-extension/background.js` (return unauthenticated signal)
- Modify: `chrome-extension/content.js` (handle unauth response; add storage change listener)
- Modify: `chrome-extension/comparison-panel.js` (add `renderUnauthPanel`)
- Modify: `chrome-extension/comparison-panel.css` (add unauth styles + best-note style)

---

- [ ] **Step 1: Update `background.js` to return `{ unauthenticated: true }` when no token**

In `chrome-extension/background.js`, inside the `COMPARE_PRODUCT` handler, find:
```js
      if (!token) { sendResponse(null); return; }
```
Replace with:
```js
      if (!token) { sendResponse({ unauthenticated: true }); return; }
```

- [ ] **Step 2: Add unauth handling + storage change listener to `content.js`**

In `chrome-extension/content.js`, inside `detectAndCompare()`, in the `COMPARE_PRODUCT` response callback, find:
```js
      // response.streaming === true — results arrive via COMPARE_RESULT_PARTIAL
```
Add the unauthenticated check before that comment:
```js
      if (response.unauthenticated) {
        renderUnauthPanel(isNaN(priceNum) ? null : priceNum, sourceRetailer, !!outOfStock);
        return;
      }
      // response.streaming === true — results arrive via COMPARE_RESULT_PARTIAL
```

Then, at the bottom of the IIFE (after `detectAndCompare();` is called), add the storage change listener:
```js
  // Auto-retry compare when user signs in while on a PDP
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    if (changes.dn_token && !changes.dn_token.oldValue && changes.dn_token.newValue) {
      _compareDispatched = false;
      const panel = document.querySelector('.dealnotify-compare-panel');
      if (panel) panel.remove();
      detectAndCompare();
    }
  });
```

- [ ] **Step 3: Add `renderUnauthPanel` to `comparison-panel.js`**

At the end of `chrome-extension/comparison-panel.js` (before the closing of any module wrapper, after `showTrackOnlyCard`), add:

```js
// ── Unauthenticated state: Compare tab shows sign-in CTA ──

function renderUnauthPanel(sourcePrice, sourceRetailer, outOfStock) {
  const existing = document.querySelector('.dealnotify-compare-panel');
  if (existing) existing.remove();

  const panel = _createBaseCard();

  _buildTabBar(panel, [
    { id: 'compare', label: '📊 Compare' },
    { id: 'track',   label: '🔔 Track'   },
  ], 'compare');

  // Compare pane — sign-in CTA
  const comparePane = document.createElement('div');
  comparePane.className = 'dealnotify-compare-panel__pane dealnotify-compare-panel__pane--active';
  comparePane.dataset.dnPane = 'compare';

  if (sourcePrice != null) {
    const sourceRow = document.createElement('div');
    sourceRow.className = 'dealnotify-compare-panel__source-row';
    const sourceLabelEl = document.createElement('span');
    sourceLabelEl.className = 'dealnotify-compare-panel__source-label';
    sourceLabelEl.textContent = DN_RETAILER_LABELS[sourceRetailer] || 'Current price';
    const sourceAmt = document.createElement('span');
    sourceAmt.className = 'dealnotify-compare-panel__source-price';
    sourceAmt.textContent = `$${sourcePrice.toFixed(2)}`;
    sourceRow.appendChild(sourceLabelEl);
    sourceRow.appendChild(sourceAmt);
    comparePane.appendChild(sourceRow);
  }

  const unauthContent = document.createElement('div');
  unauthContent.className = 'dealnotify-compare-panel__unauth-content';

  const msg = document.createElement('p');
  msg.className = 'dealnotify-compare-panel__unauth-msg';
  msg.textContent = 'Find the best price across Walmart, Target, Best Buy & more — for free.';

  const cta = document.createElement('button');
  cta.className = 'dealnotify-compare-panel__unauth-cta';
  cta.textContent = 'Sign in to Compare →';
  cta.addEventListener('click', () => {
    chrome.runtime.sendMessage({ action: 'openPopup' });
  });

  unauthContent.appendChild(msg);
  unauthContent.appendChild(cta);
  comparePane.appendChild(unauthContent);
  panel.appendChild(comparePane);

  // Track pane (unchanged — still lets unauthenticated users set restock/price alerts)
  panel.appendChild(_buildTrackPane(!!outOfStock));

  document.body.appendChild(panel);
}
```

- [ ] **Step 4: Add CSS for unauthenticated state and best-note**

At the end of `chrome-extension/comparison-panel.css`, add:

```css
/* ── Unauthenticated compare state ── */
.dealnotify-compare-panel__unauth-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 20px 14px 16px;
  text-align: center;
  gap: 12px;
}

.dealnotify-compare-panel__unauth-msg {
  font-size: 13px;
  color: #5c5c7a;
  margin: 0;
  line-height: 1.45;
}

.dealnotify-compare-panel__unauth-cta {
  display: block;
  width: 100%;
  padding: 10px;
  background: #5b67f8;
  color: #ffffff;
  font-size: 13px;
  font-weight: 600;
  text-align: center;
  border: none;
  border-radius: 50px;
  cursor: pointer;
  transition: background 0.15s ease, transform 0.15s ease, box-shadow 0.15s ease;
}

.dealnotify-compare-panel__unauth-cta:hover {
  background: #4351d8;
  transform: translateY(-1px);
  box-shadow: 0 4px 16px rgba(91, 103, 248, 0.35);
}

/* ── "Already at best price" note in compare pane ── */
.dealnotify-compare-panel__best-note {
  font-size: 12px;
  color: #16a34a;
  font-weight: 600;
  text-align: center;
  padding: 8px 14px;
  background: rgba(34, 197, 94, 0.08);
  border-bottom: 1px solid rgba(34, 197, 94, 0.2);
}
```

- [ ] **Step 5: Verify Task 2 manually**

Sign out of the extension (popup → sign out, or clear `dn_token` from `chrome://extensions` → service worker → Application → Local Storage). Navigate to a product page. Expected:
- Compare tab is shown and active (not Track-only)
- Compare tab shows current price (source row) and "Find the best price across Walmart, Target, Best Buy & more — for free."
- "Sign in to Compare →" button is visible
- Clicking it opens the extension popup
- Sign in through the popup; without refreshing the page, the panel switches to the shimmer loading state → progressive results appear

- [ ] **Step 6: Commit**

```bash
git add chrome-extension/background.js chrome-extension/content.js chrome-extension/comparison-panel.js chrome-extension/comparison-panel.css
git commit -m "feat: show compare widget to unauthenticated users with sign-in CTA and auto-retry on login"
```

---

## Task 3: Matching Accuracy

**Spec:** Section 3

**Files:**
- Modify: `price_comparison.py` (upgrade Gemini model, improve `_MATCHING_PROMPT`, improve `_score_with_keywords`)

---

- [ ] **Step 1: Upgrade Gemini model from `gemini-2.0-flash-lite` to `gemini-2.0-flash`**

In `price_comparison.py`, find:
```python
        f"gemini-2.0-flash-lite:generateContent?key={api_key}"
```
Replace with:
```python
        f"gemini-2.0-flash:generateContent?key={api_key}"
```
Also update the comment on the line above:
```python
    # Use gemini-2.0-flash — better accuracy, no thinking-token overhead
```

- [ ] **Step 2: Replace `_MATCHING_PROMPT` with improved version**

In `price_comparison.py`, replace the entire `_MATCHING_PROMPT` constant (starting `_MATCHING_PROMPT = """\` and ending `}\"""`) with:

```python
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
```

- [ ] **Step 3: Improve `_score_with_keywords` with model-token weighting and brand-mismatch veto**

In `price_comparison.py`, replace the entire `_score_with_keywords` function with:

```python
def _score_with_keywords(source_identity: dict, candidates: list[dict], retailer: str = "") -> dict:
    """Fallback scorer using weighted token-overlap when LLM APIs are unavailable."""
    _stopwords = {'the', 'a', 'an', 'and', 'or', 'with', 'for', 'in', 'on', 'at', 'of',
                  'to', 'by', 'from', 'is', 'it', 'as', 'pack', 'count', 'oz'}
    source_title = (source_identity.get('title') or '').lower()
    source_words = set(re.findall(r'\b[a-z0-9]+\b', source_title)) - _stopwords
    # Alphanumeric model tokens (e.g. "1000xm5", "b09xs7jwhh") are strong identity signals
    source_model_tokens = {w for w in source_words if re.search(r'[0-9]', w) and len(w) >= 4}
    source_brand = (source_identity.get('brand') or '').lower().strip()

    if not source_words:
        return {"confidence": "none", "best_index": None, "reasoning": "No source words to match"}

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
        if source_brand and source_brand not in cand_raw:
            continue

        overlap = source_words & cand_words
        weighted_overlap = _weighted_size(overlap)
        score = weighted_overlap / source_weighted_total if source_weighted_total else 0.0

        # Boost: brand + at least one model token both appear → floor at likely
        model_hit = source_model_tokens & cand_words
        if source_brand and source_brand in cand_raw and model_hit:
            score = max(score, 0.6)

        if score > best_score:
            best_score = score
            best_idx = i

    if best_score >= 0.50:
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
```

- [ ] **Step 4: Quick sanity check — run keyword scorer directly**

From the project root, run a quick Python sanity check:

```bash
python3 - <<'EOF'
import sys
sys.path.insert(0, '.')
from price_comparison import _score_with_keywords

# Should match (same brand + model token "xm5")
source = {'title': 'Sony WH-1000XM5 Wireless Headphones', 'brand': 'sony'}
candidates = [
  {'title': 'Sony WH-1000XM5 Headphones Silver', 'price': 280},
  {'title': 'Sony WH-1000XM4 Wireless Headphones', 'price': 250},
]
r = _score_with_keywords(source, candidates)
assert r['confidence'] == 'likely' and r['best_index'] == 0, f"FAIL: {r}"
print("PASS: correct match", r['confidence'], r['best_index'])

# Brand veto — Bose should score 0 against Sony
source2 = {'title': 'Sony WH-1000XM5 Wireless Headphones', 'brand': 'sony'}
candidates2 = [{'title': 'Bose QuietComfort 45 Headphones', 'price': 279}]
r2 = _score_with_keywords(source2, candidates2)
assert r2['confidence'] == 'none', f"FAIL brand veto: {r2}"
print("PASS: brand veto", r2['confidence'])
EOF
```

Expected output:
```
PASS: correct match likely 0
PASS: brand veto none
```

- [ ] **Step 5: Commit**

```bash
git add price_comparison.py
git commit -m "feat: upgrade Gemini to flash, improve matching prompt + keyword fallback accuracy"
```

- [ ] **Step 6: Deploy and validate in Railway logs**

Push to remote and confirm Railway auto-deploys:
```bash
git push origin main
```

After deploy, navigate to a product page in the extension and check Railway logs (`railway logs`) for:
- `gemini-2.0-flash:generateContent` in the URL (confirms model upgrade)
- Matching log lines showing `confidence=exact` or `confidence=likely` for known-good products
- No `LLM scoring unavailable` lines (confirms Gemini is responding)

---

## Self-Review Checklist

- **Section 1 (Unauth UX):** Covered in Task 2 — `renderUnauthPanel`, `{ unauthenticated: true }`, `storage.onChanged` auto-retry. ✓
- **Section 2 (Progressive results):** Covered in Task 1 — fan-out fetches, `appendComparisonResult`, `finalizeComparisonPanel`. ✓
- **Section 3 (Matching accuracy):** Covered in Task 3 — model upgrade, prompt, keyword fallback. ✓
- **Section 4 (Badge + null price):** Covered in Task 1 — null prices excluded in `background.js` filter (`c.price != null`); badge only shown when cheaper than source; "best note" when source is cheapest. ✓
- **`showTrackOnlyCard`** — still referenced in `content.js` for non-PDP pages. Not touched by any task; no change needed. ✓
- **SPA navigation** — `_onSpaNavigate` removes `.dealnotify-compare-panel` and resets `_compareDispatched`; progressive partials no-op if panel is gone (`if (!panel) return`). ✓
- **Type consistency** — `appendComparisonResult(match, source)`, `finalizeComparisonPanel()`, `renderUnauthPanel(sourcePrice, sourceRetailer, outOfStock)` — names used consistently across Task 1 steps. ✓
