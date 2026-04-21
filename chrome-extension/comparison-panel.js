/**
 * DealNotify Chrome Extension — Comparison Panel
 * Renders a floating panel listing all retailer price matches found on Amazon PDPs.
 */

const DN_COMPARE_API_BASE = 'https://www.dealnotify.co';

const DN_RETAILER_LABELS = {
  walmart: 'Walmart',
  target: 'Target',
  bestbuy: 'Best Buy',
  costco: 'Costco',
};

function showComparisonLoadingPanel(sourcePrice) {
  const existing = document.querySelector('.dealnotify-compare-panel');
  if (existing) existing.remove();

  const panel = document.createElement('div');
  panel.className = 'dealnotify-compare-panel';

  // Header
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

  // Amazon source row (real price if available)
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

  // Shimmer rows
  for (let i = 0; i < 2; i++) {
    if (i > 0) {
      const div = document.createElement('div');
      div.className = 'dealnotify-compare-panel__divider';
      panel.appendChild(div);
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
    panel.appendChild(row);
  }

  // Hint text
  const hint = document.createElement('div');
  hint.className = 'dealnotify-compare-panel__loading-hint';
  hint.textContent = 'Comparing prices across retailers…';
  panel.appendChild(hint);

  document.body.appendChild(panel);
}

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

    // Top line: name + price + savings badge
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
