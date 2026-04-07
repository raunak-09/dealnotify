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

})();
