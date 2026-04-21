/**
 * DealNotify Chrome Extension — Unified Card
 * Renders a floating tabbed card with Compare + Track tabs on product pages.
 * Also shows a Track-only card on non-PDP retailer pages.
 */

const DN_COMPARE_API_BASE = 'https://www.dealnotify.co';

const DN_RETAILER_LABELS = {
  amazon:  'Amazon',
  walmart: 'Walmart',
  target:  'Target',
  bestbuy: 'Best Buy',
  costco:  'Costco',
};


// ── Shared helpers ──

function _createBaseCard() {
  const existing = document.querySelector('.dealnotify-compare-panel');
  if (existing) existing.remove();

  const panel = document.createElement('div');
  panel.className = 'dealnotify-compare-panel';

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

  return panel;
}

function _buildTabBar(panel, tabs, defaultTab) {
  const tabBar = document.createElement('div');
  tabBar.className = 'dealnotify-compare-panel__tabs';

  tabs.forEach(({ id, label }) => {
    const btn = document.createElement('button');
    btn.className = 'dealnotify-compare-panel__tab' +
      (id === defaultTab ? ' dealnotify-compare-panel__tab--active' : '');
    btn.textContent = label;
    btn.dataset.dnTab = id;
    tabBar.appendChild(btn);
  });

  tabBar.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-dn-tab]');
    if (!btn) return;
    _activateTab(panel, btn.dataset.dnTab);
  });

  panel.appendChild(tabBar);
}

function _activateTab(panel, tabId) {
  panel.querySelectorAll('[data-dn-tab]').forEach(t =>
    t.classList.toggle('dealnotify-compare-panel__tab--active', t.dataset.dnTab === tabId));
  panel.querySelectorAll('[data-dn-pane]').forEach(p =>
    p.classList.toggle('dealnotify-compare-panel__pane--active', p.dataset.dnPane === tabId));
}

function _buildTrackPane(outOfStock, bestMatch) {
  const pane = document.createElement('div');
  pane.className = 'dealnotify-compare-panel__pane';
  pane.dataset.dnPane = 'track';

  const content = document.createElement('div');
  content.className = 'dealnotify-compare-panel__track-content';

  // Best competitor price callout (shown when compare results exist)
  if (bestMatch && bestMatch.price != null) {
    const bestLabel = DN_RETAILER_LABELS[bestMatch.retailer] || bestMatch.retailer;
    const priceCallout = document.createElement('div');
    priceCallout.className = 'dealnotify-compare-panel__track-best-price';
    priceCallout.innerHTML =
      `<span class="dealnotify-compare-panel__track-best-label">Best price found</span>` +
      `<span class="dealnotify-compare-panel__track-best-retailer">${bestLabel}</span>` +
      `<span class="dealnotify-compare-panel__track-best-amount">$${bestMatch.price.toFixed(2)}</span>`;
    if (bestMatch.savings && bestMatch.savings > 0) {
      const badge = document.createElement('span');
      badge.className = 'dealnotify-compare-panel__savings';
      badge.textContent = `Save $${bestMatch.savings.toFixed(2)}`;
      priceCallout.appendChild(badge);
    }
    content.appendChild(priceCallout);
  }

  const msg = document.createElement('p');
  msg.className = 'dealnotify-compare-panel__track-msg';
  msg.textContent = outOfStock
    ? 'This item is out of stock. Get notified when it\'s back.'
    : 'Get notified when the price drops further.';

  const cta = document.createElement('button');
  cta.className = 'dealnotify-compare-panel__track-cta';
  cta.textContent = outOfStock ? '📦 Set Restock Alert' : '🔔 Set Price Alert';
  cta.addEventListener('click', () => {
    chrome.runtime.sendMessage({ action: 'openPopup' });
  });

  content.appendChild(msg);
  content.appendChild(cta);
  pane.appendChild(content);
  return pane;
}


// ── Shimmer loading panel (shown immediately while compare API runs) ──

function showComparisonLoadingPanel(sourcePrice, sourceRetailer, outOfStock) {
  const panel = _createBaseCard();

  _buildTabBar(panel, [
    { id: 'compare', label: '📊 Compare' },
    { id: 'track',   label: '🔔 Track'   },
  ], 'compare');

  // ── Compare pane ──
  const comparePane = document.createElement('div');
  comparePane.className = 'dealnotify-compare-panel__pane dealnotify-compare-panel__pane--active';
  comparePane.dataset.dnPane = 'compare';

  if (sourcePrice != null) {
    const sourceRow = document.createElement('div');
    sourceRow.className = 'dealnotify-compare-panel__source-row';
    const sourceLabel = document.createElement('span');
    sourceLabel.className = 'dealnotify-compare-panel__source-label';
    sourceLabel.textContent = DN_RETAILER_LABELS[sourceRetailer] || 'Current price';
    const sourceAmt = document.createElement('span');
    sourceAmt.className = 'dealnotify-compare-panel__source-price';
    sourceAmt.textContent = `$${sourcePrice.toFixed(2)}`;
    sourceRow.appendChild(sourceLabel);
    sourceRow.appendChild(sourceAmt);
    comparePane.appendChild(sourceRow);
  }

  for (let i = 0; i < 2; i++) {
    if (i > 0) {
      const div = document.createElement('div');
      div.className = 'dealnotify-compare-panel__divider';
      comparePane.appendChild(div);
    }
    const row = document.createElement('div');
    row.className = 'dealnotify-compare-panel__shimmer-row';

    const topLine = document.createElement('div');
    topLine.className = 'dealnotify-compare-panel__shimmer-top';
    const nameShimmer = document.createElement('div');
    nameShimmer.className = 'dealnotify-compare-panel__shimmer-line dealnotify-compare-panel__shimmer-name';
    const priceShimmer = document.createElement('div');
    priceShimmer.className = 'dealnotify-compare-panel__shimmer-line dealnotify-compare-panel__shimmer-price';
    topLine.appendChild(nameShimmer);
    topLine.appendChild(priceShimmer);

    const ctaShimmer = document.createElement('div');
    ctaShimmer.className = 'dealnotify-compare-panel__shimmer-line dealnotify-compare-panel__shimmer-cta';

    row.appendChild(topLine);
    row.appendChild(ctaShimmer);
    comparePane.appendChild(row);
  }

  const hint = document.createElement('div');
  hint.className = 'dealnotify-compare-panel__loading-hint';
  hint.textContent = 'Comparing prices across retailers…';
  comparePane.appendChild(hint);

  panel.appendChild(comparePane);

  // ── Track pane ──
  panel.appendChild(_buildTrackPane(!!outOfStock));

  document.body.appendChild(panel);
}


// ── Render final comparison results ──

function renderComparisonPanel(response) {
  const comparisons = response && response.comparisons;
  if (!Array.isArray(comparisons)) return;

  const matches = comparisons
    .filter(c => (c.confidence === 'exact' || c.confidence === 'likely') && c.url)
    .sort((a, b) => (a.price != null ? a.price : Infinity) - (b.price != null ? b.price : Infinity));

  const panel = document.querySelector('.dealnotify-compare-panel');

  if (!matches.length) {
    // No results — switch to Track tab, show a note in Compare tab
    if (panel) {
      const comparePane = panel.querySelector('[data-dn-pane="compare"]');
      if (comparePane) {
        comparePane.innerHTML = '';
        const noResults = document.createElement('div');
        noResults.className = 'dealnotify-compare-panel__no-results';
        noResults.textContent = 'No better prices found at other retailers.';
        comparePane.appendChild(noResults);
      }
      _activateTab(panel, 'track');
    }
    return;
  }

  // Rebuild card with full results
  if (panel) panel.remove();

  const sourcePrice = response.source && response.source.price;
  const sourceRetailer = (response.source && response.source.retailer) || 'amazon';
  const sourceLabel = DN_RETAILER_LABELS[sourceRetailer] || 'Current price';
  const outOfStock = response.source && response.source.out_of_stock;

  const newPanel = _createBaseCard();

  _buildTabBar(newPanel, [
    { id: 'compare', label: '📊 Compare' },
    { id: 'track',   label: '🔔 Track'   },
  ], 'compare');

  // ── Compare pane with results ──
  const comparePane = document.createElement('div');
  comparePane.className = 'dealnotify-compare-panel__pane dealnotify-compare-panel__pane--active';
  comparePane.dataset.dnPane = 'compare';

  if (sourcePrice != null) {
    const sourceRow = document.createElement('div');
    sourceRow.className = 'dealnotify-compare-panel__source-row';
    const sourceLabelEl = document.createElement('span');
    sourceLabelEl.className = 'dealnotify-compare-panel__source-label';
    sourceLabelEl.textContent = sourceLabel;
    const sourceAmt = document.createElement('span');
    sourceAmt.className = 'dealnotify-compare-panel__source-price';
    sourceAmt.textContent = `$${sourcePrice.toFixed(2)}`;
    sourceRow.appendChild(sourceLabelEl);
    sourceRow.appendChild(sourceAmt);
    comparePane.appendChild(sourceRow);
  }

  matches.forEach((match, idx) => {
    if (idx > 0) {
      const divider = document.createElement('div');
      divider.className = 'dealnotify-compare-panel__divider';
      comparePane.appendChild(divider);
    }

    const retailerLabel = DN_RETAILER_LABELS[match.retailer] || (match.retailer
      ? match.retailer.charAt(0).toUpperCase() + match.retailer.slice(1)
      : 'Retailer');
    const isCheapest = idx === 0;
    const savingsAmt = match.savings;
    const savingsPct = (sourcePrice && match.price != null && sourcePrice > match.price)
      ? Math.round(((sourcePrice - match.price) / sourcePrice) * 100)
      : null;

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

    if (isCheapest && matches.length > 1) {
      const bestBadge = document.createElement('span');
      bestBadge.className = 'dealnotify-compare-panel__best-badge';
      bestBadge.textContent = 'Best price';
      nameLine.appendChild(bestBadge);
    }

    const priceEl = document.createElement('span');
    priceEl.className = 'dealnotify-compare-panel__retailer-price';
    priceEl.textContent = match.price != null ? `$${match.price.toFixed(2)}` : '';

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
    comparePane.appendChild(row);
  });

  newPanel.appendChild(comparePane);

  // ── Track pane — inject best match price so it's visible from the Track tab too ──
  newPanel.appendChild(_buildTrackPane(!!outOfStock, matches[0]));

  document.body.appendChild(newPanel);
}


// ── Track-only card for non-PDP retailer pages ──

function showTrackOnlyCard(outOfStock) {
  if (document.querySelector('.dealnotify-compare-panel')) return;

  const panel = _createBaseCard();

  const pane = _buildTrackPane(!!outOfStock);
  pane.classList.add('dealnotify-compare-panel__pane--active');
  panel.appendChild(pane);

  document.body.appendChild(panel);
}
