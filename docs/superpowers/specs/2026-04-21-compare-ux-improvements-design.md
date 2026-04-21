# Compare UX Improvements — Design Spec
Date: 2026-04-21

## Overview

Three coordinated improvements to the DealNotify Chrome extension compare feature:
1. **Unauthenticated UX** — show compare widget to all users; gate API call behind sign-in; auto-run compare on sign-in
2. **Progressive results** — render each retailer row as its API call resolves instead of waiting for all retailers
3. **Matching accuracy** — upgrade Gemini model and strengthen the prompt + keyword fallback

---

## Section 1: Unauthenticated UX

### Goal
Every user who visits a supported retailer PDP sees the tabbed Compare + Track card immediately, regardless of login state. Compare is the primary tab. Unauthenticated users see a sign-in CTA. Once they sign in (without leaving the page), compare runs automatically and results appear in the same card.

### Flow
1. User lands on PDP (e.g. Walmart product page)
2. `detectAndCompare()` fires as before — shows shimmer loading panel, sends `COMPARE_PRODUCT` to background
3. Background checks `dn_token`:
   - **If token present**: existing flow — fetch `/api/compare`, return data
   - **If no token**: return `{ unauthenticated: true }` (instead of `null`)
4. Content script receives response:
   - **`{ unauthenticated: true }`**: call `renderUnauthPanel(sourcePrice, sourceRetailer, outOfStock)`
   - **`null` or error**: remove panel (existing error handling, unchanged)
5. Unauthenticated panel shows Compare tab (active) with sign-in CTA + Track tab
6. User clicks "Sign in to Compare →" → `chrome.runtime.sendMessage({ action: 'openPopup' })` opens the extension popup
7. User signs in → `dn_token` written to `chrome.storage.local`
8. Content script `chrome.storage.onChanged` listener fires → detects token change from absent to present → resets `_compareDispatched = false`, removes current panel, calls `detectAndCompare()` → full compare flow runs, shimmer shows, then results

### Files changed

**`background.js`**
- In `COMPARE_PRODUCT` handler: replace `sendResponse(null); return;` with `sendResponse({ unauthenticated: true }); return;`

**`content.js`**
- In `COMPARE_PRODUCT` response callback:
  - Add: `if (response && response.unauthenticated) { renderUnauthPanel(sourcePrice, sourceRetailer, outOfStock); return; }`
  - Existing null check stays for actual errors
- Add `chrome.storage.onChanged` listener (runs once at content script load):
  ```js
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

**`comparison-panel.js`**
- Add `renderUnauthPanel(sourcePrice, sourceRetailer, outOfStock)`:
  - Uses `_createBaseCard()` and `_buildTabBar()` (existing helpers)
  - Compare pane (active): icon + "Find the best price across Walmart, Target, Best Buy & more" + "Sign in to Compare →" button
  - Track pane: same `_buildTrackPane(outOfStock)` output as today (no change)

**`comparison-panel.css`**
- Add `.dealnotify-compare-panel__unauth-content`: centered flex column, padding `20px 14px`
- Add `.dealnotify-compare-panel__unauth-msg`: font-size 13px, color `#5c5c7a`, margin-bottom 12px, line-height 1.45
- Add `.dealnotify-compare-panel__unauth-cta`: same pill style as `.dealnotify-compare-panel__track-cta`

---

## Section 2: Progressive Results

### Goal
Each retailer's result appears in the compare panel as soon as its individual API call resolves, rather than waiting for all retailers. The user sees the first result within ~2–3s of page load instead of waiting up to 11s.

### Approach
Background fires **N parallel fetch calls** (one per target retailer) instead of one combined call. Each resolves independently. As each completes, background pushes a `COMPARE_RESULT_PARTIAL` message to the tab. Content script appends the result row to the existing panel. When all settle, background sends `COMPARE_DONE`.

The existing `/api/compare` endpoint is called with `target_retailers: [singleRetailer]` per request — no backend changes needed. Per-retailer DB cache means cold misses cost ~3–6s each; cache hits return in <500ms.

### Message protocol

| Message | Direction | Payload |
|---|---|---|
| `COMPARE_PRODUCT` | content → background | existing fields |
| `{ streaming: true }` | background → content (sendResponse) | immediate ack; closes port |
| `COMPARE_RESULT_PARTIAL` | background → content (tabs.sendMessage) | `{ data: { comparisons, source } }` |
| `COMPARE_DONE` | background → content (tabs.sendMessage) | `{}` |

### Files changed

**`background.js`** — replace the single-fetch block with:
```js
// Fan out one fetch per target retailer
const retailers = ALL_COMPARE_RETAILERS.filter(r => r !== sourceRetailer);
const tabId = sender.tab.id;
const basePayload = { source_url, source_retailer, asin, title, price };

sendResponse({ streaming: true }); // close port immediately

const promises = retailers.map(async (retailer) => {
  try {
    const res = await fetch(`${API_BASE}/api/compare`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({ ...basePayload, target_retailers: [retailer] }),
    });
    if (!res.ok) return;
    const data = await res.json();
    const match = data.comparisons?.find(c =>
      (c.confidence === 'exact' || c.confidence === 'likely') && c.url && c.price != null
    );
    if (match) {
      chrome.tabs.sendMessage(tabId, { action: 'COMPARE_RESULT_PARTIAL', match, source: data.source });
    }
  } catch (e) {}
});

Promise.allSettled(promises).then(() => {
  chrome.tabs.sendMessage(tabId, { action: 'COMPARE_DONE' });
});
```

**`content.js`**
- Remove the existing `renderComparisonPanel(response)` call from the `COMPARE_PRODUCT` response callback (only used to handle the old single-response)
- `showComparisonLoadingPanel()` still called immediately (shimmer stays)
- Add message listener for `COMPARE_RESULT_PARTIAL` and `COMPARE_DONE`:
  ```js
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.action === 'COMPARE_RESULT_PARTIAL') {
      appendComparisonResult(message.match, message.source);
    }
    if (message.action === 'COMPARE_DONE') {
      finalizeComparisonPanel();
    }
  });
  ```

**`comparison-panel.js`**
- Add `appendComparisonResult(match, source)`:
  - If no `.dealnotify-compare-panel` exists (panel was closed), do nothing
  - Replace one shimmer row with a real retailer row; insert in price-sorted position
  - If no shimmer rows remain, add at end
  - Show savings vs source price if available
- Add `finalizeComparisonPanel()`:
  - Remove any remaining shimmer rows
  - If compare pane has no retailer rows (all retailers returned no match), show "No better prices found" note and switch to Track tab
  - Update Track pane's best-price callout with cheapest visible row

### Edge cases
- User closes panel before results arrive: `appendComparisonResult` checks for panel existence and no-ops
- All retailers return no match: `finalizeComparisonPanel` handles empty state
- Panel replaced by SPA navigation before `COMPARE_DONE`: `_onSpaNavigate` removes panel; subsequent partials no-op

---

## Section 3: Matching Accuracy

### Goal
Reduce false positives (wrong product matched) and false negatives (same product not matched). Primary levers: better model, stronger prompt, smarter keyword fallback.

### Changes (`price_comparison.py`)

**Model upgrade**
- Change `gemini-2.0-flash-lite` → `gemini-2.0-flash` in `_match_with_gemini()`
- Same free-tier quota; better reasoning on product attributes

**Prompt improvements** (replace `_MATCHING_PROMPT`):
- Add brand-mismatch veto rule: "If the source and candidate have different brands, return `none` immediately — do not consider name similarity"
- Add model-number rule: "An exact model number match (e.g. 'WH-1000XM5', 'OLED55C3') is near-certain `exact`. A model number present in source but absent or different in candidate is near-certain `none`"
- Add 2 few-shot examples:
  - Correct match: Sony WH-1000XM5 (Amazon) → Sony WH-1000XM5 (Walmart) = `exact`
  - Correct rejection: Sony WH-1000XM5 (Amazon) → Sony WH-1000XM4 (Walmart) = `none` (different model number)

**Keyword fallback improvements** (`_score_with_keywords`):
- Extract model-number tokens: alphanumeric strings ≥4 chars containing at least one digit (e.g. "XM5", "C3PU", "1000XM5")
- Weight model-number tokens 3× vs regular word tokens in recall calculation
- Brand-mismatch veto: extract first capitalized word from source title as presumed brand; if not found anywhere in candidate title → return `{ confidence: 'none', best_index: None }`
- Lower `likely` threshold from 55% → 50% (weighted recall), keep `possible` at 35%

---

## Out of Scope
- Target and Best Buy affiliate ID wiring (v2 queue, per CLAUDE.md)
- Costco Akamai blocking — accept as known limitation
- Backend SSE streaming (per-retailer parallel calls achieves the same UX without server changes)
- Guest (unauthenticated) API access to `/api/compare`
