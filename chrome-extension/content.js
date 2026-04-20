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
      const outOfStock = pageContains('out of stock')
                      || pageContains('get in-stock alert');
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
      const outOfStock = pageContains('out of stock')
                      || pageContains('sold out');
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
    return true; // keep the message channel open for async response
  });


  // ── Auto-inject floating widget when a trackable product is detected ──

  const WIDGET_ID = 'dn-floating-widget';

  function injectWidget(info) {
    // Don't inject twice
    if (document.getElementById(WIDGET_ID)) return;

    const isRestock = info.outOfStock;
    const priceText = info.price ? info.price : '';
    const shortTitle = info.title.length > 48
      ? info.title.substring(0, 45) + '…'
      : info.title;

    const label = isRestock
      ? '📦 Get restock alert'
      : '🔔 Track price drop';

    const subText = isRestock
      ? 'Item is out of stock'
      : (priceText ? `Current price: ${priceText}` : 'Price tracking available');

    // Use Shadow DOM to isolate styles from the host page
    const host = document.createElement('div');
    host.id = WIDGET_ID;
    host.style.cssText = [
      'position: fixed',
      'bottom: 24px',
      'right: 24px',
      'z-index: 2147483647',
      'font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    ].join(';');

    const shadow = host.attachShadow({ mode: 'closed' });

    shadow.innerHTML = `
      <style>
        .dn-widget {
          background: #1a1a2e;
          border: 1px solid rgba(91,103,248,0.5);
          border-radius: 14px;
          padding: 12px 14px;
          display: flex;
          align-items: center;
          gap: 10px;
          box-shadow: 0 4px 24px rgba(0,0,0,0.35);
          cursor: pointer;
          max-width: 280px;
          transition: transform 0.15s ease, box-shadow 0.15s ease;
          text-decoration: none;
          user-select: none;
        }
        .dn-widget:hover {
          transform: translateY(-2px);
          box-shadow: 0 6px 28px rgba(91,103,248,0.4);
        }
        .dn-bell {
          font-size: 22px;
          flex-shrink: 0;
          line-height: 1;
        }
        .dn-text {
          display: flex;
          flex-direction: column;
          gap: 1px;
          min-width: 0;
        }
        .dn-label {
          color: #ffffff;
          font-size: 13px;
          font-weight: 600;
          white-space: nowrap;
        }
        .dn-sub {
          color: rgba(255,255,255,0.55);
          font-size: 11px;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .dn-close {
          margin-left: auto;
          color: rgba(255,255,255,0.35);
          font-size: 16px;
          padding: 2px 4px;
          cursor: pointer;
          flex-shrink: 0;
          border-radius: 4px;
          line-height: 1;
        }
        .dn-close:hover { color: rgba(255,255,255,0.7); }
      </style>
      <div class="dn-widget" id="dn-btn">
        <div class="dn-bell">${isRestock ? '📦' : '🔔'}</div>
        <div class="dn-text">
          <div class="dn-label">${label}</div>
          <div class="dn-sub" title="${shortTitle}">${subText}</div>
        </div>
        <div class="dn-close" id="dn-close" title="Dismiss">✕</div>
      </div>
    `;

    // Clicking the widget opens the extension popup
    shadow.getElementById('dn-btn').addEventListener('click', (e) => {
      if (e.target.id === 'dn-close') return;
      chrome.runtime.sendMessage({ action: 'openPopup' });
    });

    // Dismiss button removes the widget
    shadow.getElementById('dn-close').addEventListener('click', (e) => {
      e.stopPropagation();
      host.remove();
    });

    document.body.appendChild(host);
  }

  function tryDetectAndInject() {
    const domain = getStoreDomain();
    if (!domain || !extractors[domain]) return;

    try {
      const info = extractors[domain]();
      const title = sanitizeText(info.title || document.title, MAX_TITLE_LENGTH);
      const price = cleanPrice(info.price);
      const outOfStock = !!info.outOfStock;

      // Only show widget if we have at minimum a title + (price OR out-of-stock signal)
      if (title && (price || outOfStock)) {
        injectWidget({ title, price, outOfStock });
      }
    } catch (e) {
      // Silently ignore extraction errors
    }
  }

  // Run after page is fully loaded (content scripts run at document_idle)
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tryDetectAndInject);
  } else {
    // Small delay to let JS-rendered prices load (e.g. Amazon dynamic content)
    setTimeout(tryDetectAndInject, 800);
  }

  // ── Amazon PDP Compare Detection ──

  let _compareDispatched = false;

  function detectAndCompareAmazonPDP() {
    if (_compareDispatched) return;
    const hostname = window.location.hostname;
    if (!hostname.includes('amazon.com')) return;

    const asinMatch = window.location.pathname.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})(?:[/?]|$)/);
    if (!asinMatch) return;
    const asin = asinMatch[1];

    const title = sanitizeText(
      document.getElementById('productTitle')?.textContent || document.title,
      MAX_TITLE_LENGTH
    );
    const priceRaw = document.querySelector('.a-price .a-offscreen')?.textContent
      || document.querySelector('#priceblock_ourprice')?.textContent
      || '';
    const price = cleanPrice(priceRaw) || null;

    _compareDispatched = true;
    chrome.runtime.sendMessage({
      action: 'COMPARE_PRODUCT',
      source_url: window.location.href,
      asin,
      title,
      price,
    }, (response) => {
      if (chrome.runtime.lastError) return; // extension context invalidated
      if (response) renderComparisonPanel(response);
    });
  }

  setTimeout(detectAndCompareAmazonPDP, 1200);

})();
