/**
 * DealNotify Chrome Extension — Comparison Panel
 * Renders a floating panel when a Walmart price match is found on Amazon PDPs.
 */

const DN_COMPARE_API_BASE = 'https://www.dealnotify.co';

function renderComparisonPanel(response) {
  const comparisons = response && response.comparisons;
  if (!Array.isArray(comparisons)) return;

  const match = comparisons.find(c => c.confidence === 'exact' || c.confidence === 'likely');
  if (!match || !match.url) return;

  // Remove any existing panel
  const existing = document.querySelector('.dealnotify-compare-panel');
  if (existing) existing.remove();

  const sourcePrice = response.source && response.source.price;
  const savingsAmt = match.savings;
  const savingsPct = (sourcePrice && match.price != null && sourcePrice > match.price)
    ? Math.round(((sourcePrice - match.price) / sourcePrice) * 100)
    : null;
  const retailerLabel = match.retailer
    ? match.retailer.charAt(0).toUpperCase() + match.retailer.slice(1)
    : 'Walmart';

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

  // ── Body ──
  const body = document.createElement('div');
  body.className = 'dealnotify-compare-panel__body';

  const retailerEl = document.createElement('div');
  retailerEl.className = 'dealnotify-compare-panel__retailer';
  retailerEl.textContent = retailerLabel;

  const titleEl = document.createElement('div');
  titleEl.className = 'dealnotify-compare-panel__title';
  titleEl.textContent = match.title || '';

  const confidenceBadge = document.createElement('span');
  confidenceBadge.className = 'dealnotify-compare-panel__confidence';
  confidenceBadge.textContent = match.confidence === 'exact' ? 'Exact match' : 'Likely match';

  const priceRow = document.createElement('div');
  priceRow.className = 'dealnotify-compare-panel__price-row';

  if (sourcePrice != null) {
    const amazonEl = document.createElement('span');
    amazonEl.className = 'dealnotify-compare-panel__price-source';
    amazonEl.textContent = `Amazon $${sourcePrice.toFixed(2)}`;
    priceRow.appendChild(amazonEl);

    const arrowEl = document.createElement('span');
    arrowEl.className = 'dealnotify-compare-panel__price-arrow';
    arrowEl.textContent = '→';
    priceRow.appendChild(arrowEl);
  }

  const walmartEl = document.createElement('span');
  walmartEl.className = 'dealnotify-compare-panel__price';
  walmartEl.textContent = match.price != null ? `${retailerLabel} $${match.price.toFixed(2)}` : '';
  priceRow.appendChild(walmartEl);

  body.appendChild(retailerEl);
  body.appendChild(titleEl);
  body.appendChild(confidenceBadge);
  body.appendChild(priceRow);

  if (savingsAmt != null && savingsAmt > 0) {
    const savingsBadge = document.createElement('span');
    savingsBadge.className = 'dealnotify-compare-panel__savings';
    savingsBadge.textContent = savingsPct
      ? `Save $${savingsAmt.toFixed(2)} (${savingsPct}%)`
      : `Save $${savingsAmt.toFixed(2)}`;
    body.appendChild(savingsBadge);
  }

  // ── CTA ──
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

  // ── Assemble ──
  panel.appendChild(header);
  panel.appendChild(body);
  panel.appendChild(cta);
  document.body.appendChild(panel);
}
