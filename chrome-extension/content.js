/**
 * DealNotify Chrome Extension — Content Script
 * Detects product information on supported retailer pages.
 * Runs on: Amazon, Walmart, Best Buy, Target, eBay, Costco
 *
 * Security notes:
 *  - Only reads textContent (never innerHTML) to avoid XSS
 *  - Only responds to messages from our own extension (chrome.runtime.onMessage)
 *  - Sanitizes all output strings (truncation, strip tags)
 *  - Never injects DOM elements or modifies the page
 */

(() => {
  'use strict';

  // ── Security: sanitize extracted text ──

  const MAX_TITLE_LENGTH = 300;
  const MAX_PRICE_LENGTH = 30;

  function sanitizeText(str, maxLength) {
    if (!str || typeof str !== 'string') return '';
    // Strip any accidental HTML tags (textContent shouldn't have them, but just in case)
    const clean = str.replace(/<[^>]*>/g, '').trim();
    return clean.substring(0, maxLength);
  }


  // ── Product info extraction by store ──

  const extractors = {

    // ────────────── AMAZON ──────────────
    'amazon.com': () => {
      const title = getText('#productTitle') || getText('#title');
      const price = getText('.a-price .a-offscreen')
                 || getText('#priceblock_ourprice')
                 || getText('#priceblock_dealprice')
                 || getText('.a-price-whole');
      const outOfStock = !!document.querySelector('#outOfStock')
                      || !!document.querySelector('#availabilityInsideBuyBox_feature_div .a-color-price');
      return { title, price, outOfStock };
    },

    // ────────────── WALMART ──────────────
    'walmart.com': () => {
      const title = getText('[data-testid="product-title"]')
                 || getText('h1[itemprop="name"]')
                 || getText('h1.prod-ProductTitle');
      const price = getText('[itemprop="price"]')
                 || getText('[data-testid="price-wrap"] .f2')
                 || getText('.price-characteristic');
      // Scope OOS check to the buy box only — full-page scan picks up
      // "out of stock" from reviews and related products, causing false positives.
      const buyBox = document.querySelector('[data-testid="add-to-cart-section"]')
                  || document.querySelector('[data-testid="buy-box"]')
                  || document.querySelector('.prod-blitz-copy');
      const buyBoxText = buyBox ? buyBox.innerText.toLowerCase() : '';
      const outOfStock = !!document.querySelector('[data-testid="get-in-stock-alert"]')
                      || buyBoxText.includes('out of stock')
                      || buyBoxText.includes('get in-stock alert');
      return { title, price, outOfStock };
    },

    // ────────────── BEST BUY ──────────────
    'bestbuy.com': () => {
      const title = getText('.sku-title h1')
                 || getText('h1.heading-5');
      const price = getText('.priceView-hero-price span[aria-hidden="true"]')
                 || getText('.priceView-customer-price span');
      const outOfStock = !!document.querySelector('.fulfillment-add-to-cart-button .btn-disabled')
                      || pageContains('sold out');
      return { title, price, outOfStock };
    },

    // ────────────── TARGET ──────────────
    'target.com': () => {
      const title = getText('[data-test="product-title"]')
                 || getText('h1[data-test="product-title"]');
      const price = getText('[data-test="product-price"]')
                 || getText('.styles__CurrentPriceFontSize');
      // Scope to fulfillment/buy-box area to avoid false positives from reviews
      const fulfillment = document.querySelector('[data-test="fulfillment-cell"]')
                       || document.querySelector('[data-test="add-to-cart-button"]')?.closest('section');
      const fulfillmentText = fulfillment ? fulfillment.innerText.toLowerCase() : '';
      const outOfStock = !!document.querySelector('[data-test="oos-header"]')
                      || fulfillmentText.includes('out of stock')
                      || fulfillmentText.includes('sold out');
      return { title, price, outOfStock };
    },

    // ────────────── EBAY ──────────────
    'ebay.com': () => {
      const title = getText('.x-item-title__mainTitle span')
                 || getText('#itemTitle');
      const price = getText('.x-price-primary span')
                 || getText('#prcIsum');
      const outOfStock = pageContains('this listing has ended')
                      || pageContains('no longer available');
      return { title, price, outOfStock };
    },

    // ────────────── COSTCO ──────────────
    'costco.com': () => {
      const title = getText('h1[itemprop="name"]')
                 || getText('.product-title');
      const price = getText('#pull-right-price .value')
                 || getText('[automation-id="productPrice"]');
      const outOfStock = pageContains('out of stock')
                      || pageContains('not available');
      return { title, price, outOfStock };
    }
  };


  // ── Helper functions ──

  function getText(selector) {
    try {
      const el = document.querySelector(selector);
      if (!el) return '';
      // Always use textContent (safe) — never innerHTML
      return el.textContent.trim();
    } catch (e) {
      return '';
    }
  }

  function pageContains(text) {
    try {
      const body = document.body?.innerText?.toLowerCase() || '';
      return body.includes(text.toLowerCase());
    } catch (e) {
      return false;
    }
  }

  function getStoreDomain() {
    const hostname = window.location.hostname.replace('www.', '');
    for (const domain of Object.keys(extractors)) {
      if (hostname.includes(domain)) return domain;
    }
    return null;
  }

  function cleanPrice(priceStr) {
    if (!priceStr) return '';
    // Extract only the first valid price-like pattern — blocks injection
    const match = priceStr.match(/\$[\d,]+\.?\d*/);
    return match ? sanitizeText(match[0], MAX_PRICE_LENGTH) : '';
  }


  // ── Message listener ──
  // Only responds to messages from our own extension (chrome.runtime.onMessage)

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


  // ── Track-only card for non-PDP pages (uses showTrackOnlyCard from comparison-panel.js) ──

  function tryShowTrackCard() {
    const domain = getStoreDomain();
    if (!domain || !extractors[domain]) return;

    try {
      const info = extractors[domain]();
      const title = sanitizeText(info.title || document.title, MAX_TITLE_LENGTH);
      const price = cleanPrice(info.price);
      const outOfStock = !!info.outOfStock;

      if (title && (price || outOfStock)) {
        showTrackOnlyCard(outOfStock);
      }
    } catch (e) {
      // Silently ignore extraction errors
    }
  }

  // After JS-rendered content loads, show the track card if compare hasn't shown one
  setTimeout(tryShowTrackCard, 800);

  // ── Multi-Retailer PDP Compare Detection ──

  // Maps domain → function that returns true when the current URL is a product page
  const PDP_DETECTORS = {
    'amazon.com':  (path) => /\/(?:dp|gp\/product)\/[A-Z0-9]{10}/.test(path),
    'walmart.com': (path) => /\/ip\//.test(path),
    'target.com':  (path) => /\/p\/[^/]/.test(path),
    'bestbuy.com': (path) => /\/site\/.+\.p(\?|\/|$)/.test(path + '/'),
    'costco.com':  (path) => /\.product\./.test(path),
  };

  const DOMAIN_TO_RETAILER = {
    'amazon.com':  'amazon',
    'walmart.com': 'walmart',
    'target.com':  'target',
    'bestbuy.com': 'bestbuy',
    'costco.com':  'costco',
  };

  let _compareDispatched = false;
  let _compareGeneration = 0;
  let _lastHref = window.location.href;

  function _onSpaNavigate() {
    const href = window.location.href;
    if (href === _lastHref) return;
    _lastHref = href;

    // Reset compare state so it re-runs for the new product page
    _compareDispatched = false;
    const panel = document.querySelector('.dealnotify-compare-panel');
    if (panel) panel.remove();

    // Delay to let the SPA render new product content before extracting
    setTimeout(detectAndCompare, 1200);
    setTimeout(tryShowTrackCard, 800);
  }

  // Poll for URL changes (React SPAs don't fire popstate on pushState)
  setInterval(_onSpaNavigate, 500);
  // Also catch back/forward navigation
  window.addEventListener('popstate', () => setTimeout(_onSpaNavigate, 100));

  function detectAndCompare() {
    if (_compareDispatched) return;

    const domain = getStoreDomain();
    if (!domain) return;

    const pdpDetector = PDP_DETECTORS[domain];
    const sourceRetailer = DOMAIN_TO_RETAILER[domain];
    if (!pdpDetector || !sourceRetailer) return;
    if (!pdpDetector(window.location.pathname)) return;

    // Use existing extractors to get title/price/outOfStock for this retailer
    let title, price, outOfStock, asin;
    try {
      const info = extractors[domain]();
      title = sanitizeText(info.title || document.title, MAX_TITLE_LENGTH);
      price = cleanPrice(info.price) || null;
      outOfStock = !!info.outOfStock;
    } catch (e) {
      title = sanitizeText(document.title, MAX_TITLE_LENGTH);
      price = null;
      outOfStock = false;
    }
    if (!title) return;

    // For Amazon PDPs also extract the ASIN (used as stable cache key)
    if (domain === 'amazon.com') {
      const m = window.location.pathname.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})(?:[/?]|$)/);
      asin = m ? m[1] : null;
    }

    _compareDispatched = true;
    const generation = ++_compareGeneration;

    const priceNum = price ? parseFloat(price.replace(/[^0-9.]/g, '')) : null;
    showComparisonLoadingPanel(isNaN(priceNum) ? null : priceNum, sourceRetailer, outOfStock);

    chrome.runtime.sendMessage({
      action: 'COMPARE_PRODUCT',
      source_url:     window.location.href,
      source_retailer: sourceRetailer,
      asin:           asin || null,
      title,
      price,
    }, (response) => {
      if (generation !== _compareGeneration) return; // stale response from previous navigation
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
      if (response.unauthenticated) {
        renderUnauthPanel(isNaN(priceNum) ? null : priceNum, sourceRetailer, !!outOfStock);
        return;
      }
      // response.streaming === true — results arrive via COMPARE_RESULT_PARTIAL
    });
  }

  // Fire at document_idle — no artificial delay for compare (widget uses its own 800ms)
  detectAndCompare();

  // Auto-retry compare when user signs in while the unauth panel is showing.
  // Checking for the unauth panel (not token oldValue) is the correct condition:
  // it fires whenever the user signs in from the CTA, regardless of prior token state.
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    if (!changes.dn_token || !changes.dn_token.newValue) return;
    if (!document.querySelector('.dealnotify-compare-panel__unauth-content')) return;
    const domain = getStoreDomain();
    const pdpDetector = domain && PDP_DETECTORS[domain];
    if (!pdpDetector || !pdpDetector(window.location.pathname)) return;
    _compareDispatched = false;
    const panel = document.querySelector('.dealnotify-compare-panel');
    if (panel) panel.remove();
    detectAndCompare();
  });

})();
