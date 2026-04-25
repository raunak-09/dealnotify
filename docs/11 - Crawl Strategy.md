# DealNotify — Crawl Strategy

**Status:** Active. Phase 1 implemented 2026-04-25. Phase 2 pending credentials/keys.
**Owner:** Mishi
**Goal:** Reduce Firecrawl credit consumption by an order of magnitude as user count grows, without slowing down the Compare experience or degrading reliability.

---

## Problem

Firecrawl is the workhorse scraper for two systems:

1. **Price monitor** — every tracked product is re-scraped every 2h (Pro) or 6h (Free trial) by the APScheduler job in `price_monitor.py`. At N users tracking M average products, that's `N × M × (24/interval)` scrapes per day. Linear in users.
2. **Compare feature** — `price_comparison.py` does ~2 Firecrawl calls per request (source identity + per-retailer search), partially shielded by a 7-day DB cache and 15-min in-memory cache.

As users grow, Firecrawl spend grows linearly. Without intervention, scrape costs become the dominant variable cost of the SaaS.

A previous attempt to mitigate this used the Jina AI Reader (`r.jina.ai`) as a free fallback when Firecrawl returned 402 / credit-exhausted. **In production this was unreliable** — for most retail PDPs Jina returned empty or unparseable content, so when Firecrawl ran out of credits the user experience broke. The strategy below treats Jina as last-ditch only.

---

## Principles

1. **The cheapest scrape is the one we never make.** The biggest wins come from caching and deduplication, not from finding a cheaper scraper.
2. **Reliability over cost on the user-facing path.** Compare and price checks must work; degraded results are worse than slightly higher cost.
3. **Free retailer-native APIs are always Tier 1.** When a retailer publishes structured data, scraping is the wrong tool.
4. **Firecrawl stays in the toolbox.** It's the most reliable option for hard pages — we just stop using it for easy ones.
5. **Measure before optimizing further.** Per-retailer counters tell us where to invest next.

---

## Tiered Architecture

Every scrape request flows through these tiers in order. The first tier that succeeds wins.

### Tier 0 — Don't scrape (cache hits)

- **In-memory L1 cache:** 15-min TTL, keyed by `(retailer, identifier, target_retailer)` for Compare; per-product-page for monitor.
- **DB L2 cache (`product_comparisons`, `product_pages`):** 7-day TTL on matches, 1-day TTL on no-match.
- **Identity cache (`product_identities`):** 30-day TTL on `(retailer, canonical_id)` → title/brand/model/UPC. Identity is essentially permanent; price is not. Splitting them lets Compare re-run target searches without re-scraping the source PDP.
- **Cross-user product deduplication:** When 50 users track the same Amazon ASIN, the scheduler scrapes the page **once** per cycle and fans the result out to all user product rows. This is the single biggest reduction at scale — typically 10–100×.

### Tier 1 — Retailer-native APIs (free, structured)

| Retailer | API | Auth | Status |
|---|---|---|---|
| Best Buy | Internal JSON (`/api/v1/json/search`) | None | Live (current) |
| Best Buy | Open API (`api.bestbuy.com`) | `BESTBUY_API_KEY` | Stub — keys pending |
| Target | redsky (`redsky.target.com/redsky_aggregations/v1/web`) | None | Live (Phase 1) |
| eBay | Browse API (`api.ebay.com/buy/browse/v1/item_summary/search`) | `EBAY_APP_ID` | Stub — keys pending |
| Amazon | Product Advertising API 5.0 | `AMAZON_PA_ACCESS_KEY/SECRET` | Stub — Associates approval pending |
| Walmart | Impact Radius affiliate API | `WALMART_AFFILIATE_ID` (have) + token | Future |
| Costco | None published | — | Falls to Tier 2/3 |

### Tier 2 — Cheaper paid scraper (ScraperAPI)

- Provider abstraction in `_scrape()` reads `SCRAPER_PROVIDER` env var: `firecrawl` (default) | `scraperapi` | `scrapingbee`.
- ScraperAPI hobby tier is ~$0.001/req vs Firecrawl ~$0.01–0.04/credit. At 10× volume reduction from Tier 0/1, switching the residual to ScraperAPI is another 5–10× cost cut on remaining traffic.
- Decision rule: route easy pages (static HTML, no anti-bot) to ScraperAPI; reserve Firecrawl for the genuinely hard pages.

### Tier 3 — Firecrawl

- The reliable workhorse. Used when Tier 0/1/2 miss or fail.
- Especially appropriate for: heavy-JS pages, anti-bot-protected pages, pages requiring `wait_for` rendering.

### Tier 4 — Jina AI Reader (last-ditch only)

- Free, no key. Use only when all paid options have failed *and* Jina output passes a quality gate (extractable price, min length).
- Empirically unreliable on retail PDPs — never load-bearing.

### Tier 5 — Async deferral

- If everything fails, return `{ status: "checking", retry_after: 30 }` and queue the work in the background.
- Better UX than a 30-second blocking spinner: tells the user we're working on it instead of burning credits to chase a result.

---

## Multipliers (apply across all tiers)

These reduce *how often* we hit the scrape path at all.

### Cross-user product deduplication

**Problem:** If 100 Pro users track the same Sony WH-1000XM5 ASIN, the scheduler scrapes Amazon 100 times per 2-hour cycle for the same page.

**Solution:** New `product_pages` table, keyed on `(retailer, canonical_id)`:

```
product_pages
  id
  retailer
  canonical_id           -- ASIN, walmart item ID, BB SKU, etc.
  url
  current_price
  stock_status
  last_checked
  stable_streak          -- adaptive scheduling
  next_check_at          -- adaptive scheduling
  payload_json           -- last full scrape result, for fan-out
```

`products` rows now reference `product_pages` via `page_id`. Scheduler iterates over `product_pages` (not `products`), scrapes each unique page once, then fans the result out to every user `products` row pointing at it.

Backfill is lazy: existing `products` rows get a `page_id` on next check.

### Adaptive scheduling

A price stable for 7 days probably won't move tomorrow. Track `stable_streak` per page. After N stable cycles, double the check interval up to a 24h cap. Reset to baseline on any movement.

```
streak 0–2  → check every 2h (Pro baseline)
streak 3–5  → 4h
streak 6–10 → 8h
streak 11+  → 24h (max)
```

Most products are stable most of the time. This typically cuts crawl volume 2–4× on top of dedup.

**Per-retailer caps** (`_RETAILER_MAX_CHECK_HOURS` in `web_app.py`): some retailers' API ToS restrict how long we can display pricing data before refreshing. These caps clamp `next_check_at` regardless of stable_streak.

| Retailer | Max interval (monitor) | Reason |
|---|---|---|
| Best Buy | 1h | Best Buy Open API ToS — pricing data not to be shown more than 1h stale |
| Others | (uncapped — adaptive default applies) | |

Best Buy effectively forfeits the adaptive-scheduling savings for the price monitor. The savings come from (a) cross-user dedup, and (b) once `BESTBUY_API_KEY` is set, scrapes become free Open API calls anyway.

**Compare match cache caps** (`_RETAILER_COMPARE_CACHE_HOURS`): same idea applied to the `product_comparisons` table that powers the Compare panel. For Best Buy specifically the comparison match TTL is clamped to 1h regardless of confidence (overriding the default 7-day-match / 1-day-no-match).

| Retailer | Compare cache TTL | Reason |
|---|---|---|
| Best Buy | 1h | Same ToS — prices shown to users in the Compare panel must be ≤1h stale |
| Others | 7d match / 1d no-match (default) | |

### Identity cache split (Compare)

Today Compare re-extracts source identity (ASIN → title/brand/model) on every cache miss. Identity is essentially permanent. Split it into a separate `product_identities` table with 30-day TTL.

Effect: ~50% reduction in Firecrawl calls per Compare miss (drop the source identity scrape, keep only the target search scrape).

### Pre-warming (Compare)

The Chrome extension already detects supported PDPs. Trigger `/api/compare` on detection with a short debounce; results are warm by the time the user opens the panel. No extra credits — just shifts the latency off the click.

(Already partially in place via the extension-side compare cache. Verify and tighten timing.)

---

## Implementation Phases

### Phase 1 — Code-only, ship now (this commit)

1. Strategy doc (this file).
2. Demote Jina to last-ditch with quality gate.
3. Per-retailer instrumentation counters.
4. Identity cache split (`product_identities` table).
5. Target redsky native JSON fast path.
6. Cross-user product deduplication (`product_pages` + scheduler refactor + lazy backfill).
7. Adaptive scheduling (`stable_streak`, `next_check_at`).
8. Provider abstraction (`SCRAPER_PROVIDER` env var) with ScraperAPI driver stub.
9. Stub env-var support for eBay / Best Buy Open API / Amazon PA-API.

### Phase 2 — Pending external action (Mishi)

1. Sign up for ScraperAPI (recommend hobby plan to start) — set `SCRAPER_API_KEY`.
2. Get eBay developer key — set `EBAY_APP_ID`.
3. Get Best Buy Open API key — set `BESTBUY_API_KEY`.
4. Apply for Amazon Product Advertising API via Associates — set `AMAZON_PA_ACCESS_KEY` + `AMAZON_PA_SECRET_KEY` + `AMAZON_PA_PARTNER_TAG`.
5. Implement the actual API call bodies behind each stub once keys are in place.

### Phase 3 — Measure & tune

After 1 week of production data with Phase 1 deployed:

- Read `/api/admin/crawl-stats` to identify the highest-spend retailer.
- Decide which Phase 2 integrations to prioritize.
- Tune adaptive-scheduling thresholds based on real stability distributions.
- Decide whether ScraperAPI is replacing Firecrawl entirely for some retailers, or just the easy fraction.

---

## Metrics

Exposed at `/api/admin/crawl-stats` (admin-auth gated):

- `firecrawl_calls` per retailer per day
- `native_api_hits` per retailer per day
- `jina_attempts` and `jina_successes`
- `cache_hits` / `cache_misses` (memory L1, DB L2)
- `dedup_factor` (unique pages scraped vs user-products tracked)
- `avg_check_interval` per page tier (adaptive scheduling effectiveness)

Targets after Phase 1 fully deployed:

- 60–80% reduction in `firecrawl_calls` from price monitor (driven by dedup + adaptive)
- 40–60% reduction in `firecrawl_calls` from Compare (driven by identity cache)
- 100% Tier-1 routing for Target queries (was scraping search page; now redsky JSON)

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Cross-user dedup migration breaks existing tracking | Lazy backfill: existing rows keep working until next check repopulates. New table is additive. |
| Adaptive scheduling delays a real price drop | Cap at 24h. Reset streak on any movement (including stock). For Pro plan emphasizes "every 2h" — keep first 24h on baseline regardless. |
| Target redsky endpoint changes/rate-limits | Falls through to existing scraping path. Same defensive shape as Best Buy internal JSON. |
| ScraperAPI quality regresses vs Firecrawl on some pages | Provider abstraction lets us route per-retailer. Start ScraperAPI on Costco/Amazon search; keep Firecrawl on Walmart until measured. |
| Identity cache returns stale data after a product relaunch | 30-day TTL puts an outer bound. Add invalidate-on-Compare-miss-after-N-tries logic if needed. |

---

## Out of scope

- Replacing Firecrawl entirely. It's the right tool for the hard 10–20% of pages.
- Self-hosted Playwright on Railway. Maintenance + IP-block costs exceed savings.
- Realtime price scraping on user dashboard view. Stays cache-served.

---

## Code locations after Phase 1

- `price_comparison.py` — provider abstraction, identity cache, instrumentation, Target redsky, demoted Jina.
- `price_monitor.py` — uses `product_pages`, adaptive scheduling, fan-out to user `products`.
- `web_app.py` — schema additions (`product_pages`, `product_identities`), `/api/admin/crawl-stats` endpoint.
- `docs/11 - Crawl Strategy.md` — this file.
