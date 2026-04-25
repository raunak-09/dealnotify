## Compare Feature — Env Vars (added 2026-04-17)
- MATCHING_LLM_PROVIDER: "gemini" | "anthropic" | "groq" — controls which LLM is used for product matching
- GEMINI_API_KEY: Google AI Studio free-tier key
- WALMART_AFFILIATE_ID: Impact Radius affiliate tag for Walmart outbound links
- TARGET_AFFILIATE_ID: (v2 — leave blank for now)
- BESTBUY_AFFILIATE_ID: (v2 — leave blank for now)

## Crawl Strategy — Env Vars (added 2026-04-25)
See docs/11 - Crawl Strategy.md for the full plan.
- SCRAPER_PROVIDER: "firecrawl" (default) | "scraperapi" | "firecrawl-then-scraperapi"
- SCRAPER_API_KEY: ScraperAPI key — Tier-2 paid alternative to Firecrawl (~10× cheaper). Sign up at https://www.scraperapi.com/
- EBAY_APP_ID + EBAY_CERT_ID: eBay App ID (Client ID) and Cert ID (Client Secret) for the Browse API. Tier-1 free 5000/day. The app uses these to mint a 2-hour Application access token via OAuth2 client_credentials grant — refreshed automatically by `_get_ebay_app_token()`. Apply at https://developer.ebay.com/my/keys
- BESTBUY_API_KEY: Best Buy Open API key — Tier-1 free 5000/day. Apply at https://developer.bestbuy.com/
- AMAZON_PA_ACCESS_KEY / AMAZON_PA_SECRET_KEY / AMAZON_PA_PARTNER_TAG: Amazon Product Advertising API 5.0. Requires Amazon Associates approval. PA-API stub present; implementation deferred.

When any of these keys is unset the corresponding tier no-ops and the existing scraping path is used. Set the keys to activate the new tier without code changes.

## Compare Feature — Summary (added 2026-04-17)

The Compare feature lets users see if an Amazon product they are viewing is available cheaper at Walmart. It runs automatically on Amazon PDPs via the Chrome extension and shows a panel with the Walmart price, savings, and a CTA.

### New files created
- `price_comparison.py` — core comparison module: Amazon identity extraction (via Firecrawl), Walmart search (via Firecrawl), LLM matching (Gemini/Anthropic/Groq provider abstraction)

### Files modified
- `web_app.py` — added `product_comparisons` + `comparison_clicks` DB tables, `POST /api/compare` (auth + rate-limit + cache + affiliate wrapping), `POST /api/compare/click` (click tracker), `GET /api/admin/compare-stats`, `wrap_affiliate_link()` helper
- `chrome-extension/manifest.json` — added `https://*.amazon.com/*` to host_permissions; added second content_scripts entry for Amazon PDPs loading comparison-panel.js/css
- `chrome-extension/background.js` — handles `COMPARE_PRODUCT` message, fetches `/api/compare`, forwards `COMPARE_RESULT` to tab
- `chrome-extension/content.js` — `detectAndCompareAmazonPDP()` with `_compareDispatched` guard; sends `COMPARE_PRODUCT` to background; renders panel on `COMPARE_RESULT`

### New extension files
- `chrome-extension/comparison-panel.js` — renders price comparison panel (only for exact/likely confidence); DOM-only, no innerHTML
- `chrome-extension/comparison-panel.css` — styles scoped under `.dealnotify-compare-panel`

### QA bugs fixed (DEA-18, 2026-04-17)
- `FIRECRAWL_API_KEY` and `GEMINI_API_KEY` had trailing `\n` in Railway env vars → added `.strip()` to both
- `gemini-2.5-flash` thinking model consumed `maxOutputTokens: 200` budget before emitting any JSON → switched to `gemini-2.0-flash-lite` (no thinking), bumped to `maxOutputTokens: 512`, added JSON extraction regex for prose-wrapped responses
- Added keyword-based fallback scorer (`_score_with_keywords`) that activates when Gemini API is rate-limited or quota-exhausted — uses recall-based token overlap (≥55% → likely, ≥35% → possible)
- `get_user_by_token()` returns tuple `(user, products)` — both compare endpoints had `user, _ =` unpacking added

### v2 items completed (as of 2026-04-21)
- ✅ Target, Best Buy, Costco retailer integration (`RETAILER_SEARCHERS` fully wired)
- ✅ Amazon as compare target from non-Amazon PDPs
- ✅ Multi-retailer progressive results (one API call per retailer in parallel)
- ✅ Extension-side compare cache (30-min TTL, keyed by ASIN or URL)
- ✅ Unauthenticated user sign-in CTA with auto-retry on login
- ✅ Best-price badge across all retailers
- ✅ Rate limit raised to 200/hr

### v3 queue
- `TARGET_AFFILIATE_ID` and `BESTBUY_AFFILIATE_ID` env vars (env vars exist, leave blank for now)
- Push notifications for price improvements on tracked products
- Upgrade Gemini API to paid tier to avoid daily quota exhaustion
- eBay PDP compare support (extractors exist; PDP detector + retailer mapping not yet wired)

## Crawl Strategy Phase 1 (added 2026-04-25)

Goal: cut Firecrawl spend as users grow without slowing Compare. See docs/11 - Crawl Strategy.md.

Shipped this commit:
- ✅ Strategy doc at docs/11 - Crawl Strategy.md
- ✅ Jina demoted to last-ditch with `_jina_quality_ok()` quality gate (was unreliable in production)
- ✅ Per-retailer crawl metrics (`_crawl_metrics`) exposed at `GET /api/admin/crawl-stats?key=…`
- ✅ Identity cache split — new `product_identities` table, 30-day TTL, hoisted out of per-retailer loop in `/api/compare`. Cuts ~50% of Firecrawl calls per Compare miss.
- ✅ Target redsky JSON fast path (`_search_target_redsky`) — Tier-1 free, no key required.
- ✅ Provider abstraction: `SCRAPER_PROVIDER` env var (`firecrawl` | `scraperapi` | `firecrawl-then-scraperapi`). ScraperAPI driver implemented.
- ✅ Stub native-API integrations for eBay (Browse), Best Buy (Open API), Amazon (PA-API). Activated by env var.
- ✅ Schema additions: `product_pages` (cross-user dedup), `products.page_id` column.

Pending in this Phase 1 commit (still in progress):
- 🔄 `price_monitor.py` refactor to use `product_pages` for cross-user dedup + adaptive scheduling.

Phase 2 (pending external action):
- ⏳ Sign up for ScraperAPI; set `SCRAPER_API_KEY` and `SCRAPER_PROVIDER=firecrawl-then-scraperapi`.
- ⏳ Get eBay Browse API key; set `EBAY_APP_ID`.
- ⏳ Get Best Buy Open API key; set `BESTBUY_API_KEY`.
- ⏳ Apply for Amazon Product Advertising API; fill in `_search_amazon_paapi` body.

Phase 3 (after 1 week of production data with Phase 1 deployed):
- ⏳ Read `/api/admin/crawl-stats`, identify highest-spend retailers, prioritize Phase 2 work accordingly.