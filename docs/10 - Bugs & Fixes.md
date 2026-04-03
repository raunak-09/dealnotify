# PriceGuard — Bugs & Fixes

A running log of every significant bug encountered, its root cause, and the fix applied. Useful context for future debugging.

---

## Bug 1: Stripe Webhook Crash — `AttributeError: get`

**Date:** April 2026
**Symptom:** Railway logs showed `AttributeError: 'StripeObject' object has no attribute 'get'` every time a user completed payment. `is_pro` was never set to TRUE.
**Root cause:** Newer Stripe Python SDK versions return a `StripeObject` (a custom class), not a plain Python dict. `StripeObject` does not have a `.get()` method. The code was calling `session.get('client_reference_id')`.
**Fix:**
```python
# Before (broken):
token = session.get('client_reference_id')

# After (fixed):
token = getattr(session, 'client_reference_id', None)
```
Apply `getattr(obj, 'field', None)` to all StripeObject field reads in the webhook handler.

---

## Bug 2: Pro Users Never Getting Price Checks

**Date:** April 2026
**Symptom:** Pro users' products were never being checked by the background scheduler, even though the job was running.
**Root cause:** The scheduler job queried `WHERE status = 'active'`, which excluded users with `status = 'pro'`.
**Fix:**
```sql
-- Before:
WHERE status = 'active'

-- After:
WHERE status IN ('active', 'pro')
```

---

## Bug 3: Scheduled Job Running Once Per Day Instead of Hourly

**Date:** April 2026
**Symptom:** Price checks were only happening once a day (around 6am), not every hour.
**Root cause:** The APScheduler cron expression was `'0 6 * * *'` (daily at 6am) instead of `'0 * * * *'` (every hour).
**Fix:** Changed APScheduler job to:
```python
scheduler.add_job(
    check_all_prices_job,
    'cron',
    minute='0'  # top of every hour
)
```

---

## Bug 4: Pro Badge / Trial Banner Showing Wrong Status

**Date:** April 2026
**Symptom:** After paying, the dashboard still showed the free trial countdown instead of the Pro badge and plan card.
**Root cause:** Frontend was checking `userData.status === 'pro'` which was not being reliably set. The new `is_pro` boolean field was not yet in use everywhere.
**Fix:** Added `is_pro BOOLEAN DEFAULT FALSE` column. Webhook sets it to TRUE on purchase. Frontend checks `userData.is_pro` (boolean) everywhere instead of `userData.status`. This is the single source of truth.

---

## Bug 5: Session Lost When Clicking Logo

**Date:** April 2026
**Symptom:** Clicking the PriceGuard logo (which links to `/`) would navigate to the homepage, losing the user's session token from the URL.
**Root cause:** The auth token was only being stored in the URL query param (`/dashboard?token=...`). Navigating away removed it.
**Fix:** Implemented localStorage session persistence:
- On login, token is saved to `localStorage` under key `pg_token`
- Dashboard reads token from URL first, then falls back to localStorage
- `history.replaceState` cleans the token from the URL bar
- index.html detects a valid localStorage session and swaps the nav link to "My Dashboard →"

---

## Bug 6: Site Showing "Not Secure" in Browser

**Date:** April 2026
**Symptom:** Chrome showed "Not Secure" warning on `dealnotify.co` even after the app was on Railway.
**Root cause (1):** No HSTS header — browsers showed "Not Secure" on the first HTTP visit before the redirect happened.
**Root cause (2):** Railway only supports one custom domain; `dealnotify.co` (non-www) was not being redirected to `www.dealnotify.co`.
**Fix:**
1. Added Cloudflare as DNS provider for both domains
2. Set Cloudflare "Always Use HTTPS" ON
3. Added Cloudflare redirect rule: `dealnotify.co` → `www.dealnotify.co`
4. Added HSTS header in Flask `after_request`:
   ```python
   response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
   ```

---

## Bug 7: Annual Billing Not Routing to Correct Stripe Price

**Date:** April 2026
**Symptom:** Clicking "Upgrade - Annual" in the dashboard was starting a monthly checkout instead of annual.
**Root cause:** The billing toggle was updating the UI but not passing the `billing` parameter through to the API call.
**Fix:** End-to-end wiring:
1. Toggle stores selection in JS variable `upgradeBilling`
2. `startCheckout()` passes `&billing=${upgradeBilling}` in the fetch URL
3. Backend reads `request.args.get('billing', 'monthly')`
4. Selects `STRIPE_ANNUAL_PRICE_ID` if `billing == 'annual'`, else `STRIPE_PRICE_ID`

---

## Bug 8: Toast Notifications Invisible

**Date:** April 2026
**Symptom:** Users couldn't see any toast notifications after they were moved to the top of the screen.
**Root cause:** Toast was positioned at `top: 24px` which placed it inside the fixed navbar (height ~60px). Even though `z-index: 9999` was set, the toast text was obscured by/blending into the navbar background.
**Fix:** Changed to `top: 80px` to position the toast clearly below the navbar. Also increased `z-index` to `99999`.

---

## Bug 9: Firecrawl MCP "API Key Not Provided" Errors

**Date:** April 2026
**Symptom:** Claude desktop logs showed `Either FIRECRAWL_API_KEY or FIRECRAWL_API_URL must be provided` and the MCP server kept crashing.
**Root cause:** This was the **local** Firecrawl MCP server (used to give Claude Firecrawl access in conversations), not the production app. The local `firecrawl-mcp` npx package was missing the API key in Claude desktop's MCP config.
**Fix:** Add to Claude desktop MCP configuration:
```json
{
  "mcpServers": {
    "firecrawl-mcp": {
      "command": "npx",
      "args": ["-y", "firecrawl-mcp"],
      "env": {
        "FIRECRAWL_API_KEY": "fc-your-key-here"
      }
    }
  }
}
```
**Note:** The production Railway app's Firecrawl scraping is unaffected — it uses its own `FIRECRAWL_API_KEY` environment variable and was working correctly.

---

## Related Notes
- [[06 - Stripe & Payments]]
- [[07 - Price Monitoring System]]
- [[09 - Deployment Guide]]
- [[05 - Frontend & Session Management]]
