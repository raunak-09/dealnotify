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

  panel.dataset.dnOutOfStock = outOfStock ? '1' : '0';
  document.body.appendChild(panel);
}


// ── Progressive: add one retailer row as its result arrives ──

function appendComparisonResult(match, source) {
  const panel = document.querySelector('.dealnotify-compare-panel');
  if (!panel) return;
  const comparePane = panel.querySelector('[data-dn-pane="compare"]');
  if (!comparePane) return;

  const sourcePrice = source && typeof source.price === 'number' ? source.price : null;
  const price = match.price != null ? parseFloat(match.price) : null;
  const hasPrice = price != null && !isNaN(price);
  const retailerLabel = DN_RETAILER_LABELS[match.retailer] || (match.retailer
    ? match.retailer.charAt(0).toUpperCase() + match.retailer.slice(1)
    : 'Retailer');

  const savingsAmt = hasPrice && sourcePrice != null && price < sourcePrice
    ? parseFloat((sourcePrice - price).toFixed(2))
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
  priceEl.textContent = hasPrice ? `$${price.toFixed(2)}` : 'See price';

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
  cta.addEventListener('click', () => {
    // Open first to preserve user gesture (window.open must be synchronous)
    window.open(match.url, '_blank', 'noopener');
    // Track click through background worker (keeps token out of content-script memory)
    if (match.comparison_id) {
      chrome.runtime.sendMessage({
        action: 'TRACK_COMPARE_CLICK',
        comparison_id: match.comparison_id,
      }).catch(() => {});
    }
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

  // Track the best priced match for badge/Track pane (only rows with a known price qualify)
  if (hasPrice) {
    const currentBestPrice = panel.dataset.dnBestPrice ? parseFloat(panel.dataset.dnBestPrice) : Infinity;
    if (price < currentBestPrice) {
      panel.dataset.dnBestPrice = String(price);
      panel.dataset.dnBestRetailer = match.retailer;
      panel.dataset.dnBestUrl = match.url || '';
      panel.dataset.dnBestComparisonId = match.comparison_id || '';
      panel.dataset.dnSourcePrice = sourcePrice != null ? String(sourcePrice) : '';
    }
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
      if (!isNaN(p) && Math.abs(p - bestPrice) < 0.01) {
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


// ── Track-only card for non-PDP retailer pages ──

function showTrackOnlyCard(outOfStock) {
  if (document.querySelector('.dealnotify-compare-panel')) return;

  const panel = _createBaseCard();

  const pane = _buildTrackPane(!!outOfStock);
  pane.classList.add('dealnotify-compare-panel__pane--active');
  panel.appendChild(pane);

  document.body.appendChild(panel);
}


// ── Unauthenticated state: Compare tab shows sign-in CTA ──

function renderUnauthPanel(sourcePrice, sourceRetailer, outOfStock) {
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

  // Track pane — unauthenticated users can still set price/restock alerts
  panel.appendChild(_buildTrackPane(!!outOfStock));

  document.body.appendChild(panel);
}
