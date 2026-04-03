# PriceGuard — Price Monitoring System

## Overview

Prices are checked by a background job that runs inside the Flask process. It uses Firecrawl to scrape product pages and sends Gmail alerts when a price drop is detected.

---

## Scheduler

**Library:** APScheduler (`BackgroundScheduler`)
**Schedule:** Every hour (`0 * * * *` cron expression)
**Started:** At Flask app startup (when `__name__ == '__main__'`)

```python
scheduler = BackgroundScheduler()
scheduler.add_job(
    check_all_prices_job,
    'cron',
    minute='0'   # top of every hour
)
scheduler.start()
```

The scheduler runs as a background thread inside the Railway process. It does not require an external cron service.

---

## Check Intervals (Tiered)

The job runs every hour, but individual products are skipped based on when they were last checked:

| Plan | Min time between checks |
|------|------------------------|
| Pro (`is_pro = TRUE`) | 2 hours |
| Free (`is_pro = FALSE`) | 6 hours |

**Logic inside the job:**
```python
min_interval = timedelta(hours=2) if user_is_pro else timedelta(hours=6)
if last_checked and (now - last_checked) < min_interval:
    skip this product
```

This means a Pro user's products are checked at most 12 times/day, and a Free user's at most 4 times/day.

---

## Job: `check_all_prices_job()`

**Location:** `web_app.py`, around line 1574

### What it does, step by step:

1. Queries all users where `email_verified = TRUE` and `status IN ('active', 'pro')`
2. For each user, queries their active products
3. For each product:
   a. Checks if enough time has passed since `last_checked` (interval gate)
   b. Calls `scrape_price(url)` via Firecrawl
   c. If price successfully extracted:
      - Updates `current_price` and `last_checked` in DB
      - Calls `log_price_history(product_id, price)` (non-blocking)
      - If `current_price <= target_price` → calls `send_price_drop_alert(...)`
4. Logs a summary to Railway console:
   ```
   ✅ Price check complete: 12 checked, 3 skipped (interval), 1 alert sent
   ```

---

## Price Scraping (Firecrawl)

**API:** Firecrawl (`firecrawl-py` SDK)
**Key:** Set in `FIRECRAWL_API_KEY` Railway env var

Firecrawl uses LLM-powered extraction to pull structured data (product name, price) from any product page. This works across Amazon, Walmart, Best Buy, Target, eBay, Costco without custom parsers per retailer.

**Important:** The Firecrawl key in Railway env vars is separate from any Firecrawl MCP you may have configured locally in Claude desktop. If the Railway app is scraping correctly, the Railway key is fine regardless of local MCP errors.

---

## Email Alerts

**Provider:** Gmail SMTP
**Sender:** manisha.jmc@gmail.com
**Auth:** Gmail App Password (`GMAIL_PASSWORD` env var)

### When an alert fires:
- `current_price <= target_price`
- Alert is sent once per trigger (not rate-limited per user currently — if price stays below target, alert fires every check cycle)

### Alert email includes:
- Product name + store
- Current price vs target price
- Direct link to product
- Link to their dashboard

---

## Price History Logging

Every successful price fetch is logged to `price_history`:
```python
def log_price_history(product_id, price):
    INSERT INTO price_history (product_id, price, checked_at) VALUES (...)
```

This is fire-and-forget — if it fails it logs a warning but does not interrupt the main check job.

The history is surfaced in the dashboard as a 90-day SVG line chart.

---

## Store Detection

When a product is added, the store is detected from the URL:

| URL contains | Store label |
|-------------|------------|
| `amazon` | `amazon` |
| `walmart` | `walmart` |
| `bestbuy` | `bestbuy` |
| `target` | `target` |
| `ebay` | `ebay` |
| `costco` | `costco` |
| anything else | `other` |

---

## Manual Trigger

An admin can manually run the full job via:
```
GET /api/check-all-prices
```

A user can trigger a check for just their own products via:
```
GET /api/check-prices?token=<user_token>
```

---

## Related Notes
- [[02 - Tech Stack & Architecture]]
- [[03 - Database Schema]]
- [[08 - Environment Variables]]
