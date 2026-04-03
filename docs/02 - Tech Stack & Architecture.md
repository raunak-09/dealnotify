# PriceGuard — Tech Stack & Architecture

## Stack at a Glance

| Layer | Technology | Notes |
|-------|-----------|-------|
| Language | Python 3 | |
| Web framework | Flask | Single `web_app.py` file |
| Database | PostgreSQL | Hosted on Railway |
| DB driver | pg8000 | Pure-Python, no C deps |
| Price scraping | Firecrawl API | `firecrawl-py` SDK |
| Email | Gmail SMTP | App password auth |
| Payments | Stripe | Checkout + Webhooks |
| Scheduler | APScheduler | BackgroundScheduler, in-process |
| Frontend | Vanilla HTML/CSS/JS | No framework, no build step |
| Hosting | Railway | Auto-deploy from GitHub |
| CDN / HTTPS | Cloudflare | DNS proxy, Always Use HTTPS |
| Version control | GitHub | https://github.com/raunak-09/dealnotify |

---

## Architecture Diagram

```
User's Browser
      │
      ▼
  Cloudflare (DNS proxy, HTTPS termination)
      │
      ▼
  Railway (web service)
      │
      ├── Flask app (web_app.py)
      │       ├── Serves index.html and dashboard.html
      │       ├── REST API endpoints (/api/*)
      │       ├── Stripe webhook listener
      │       └── APScheduler (background thread, runs every 1h)
      │               └── Calls Firecrawl API → checks prices → sends Gmail alerts
      │
      └── PostgreSQL (Railway plugin)
              ├── users table
              ├── products table
              └── price_history table
```

---

## Key Dependencies (`requirements.txt`)

```
flask
pg8000
firecrawl-py
stripe
apscheduler
python-dotenv
```

---

## Request Flow: User Checks Dashboard

1. Browser loads `https://www.dealnotify.co/dashboard`
2. Flask serves `dashboard.html` (static template, no server-side rendering)
3. JS reads token from URL param or localStorage
4. JS calls `GET /api/dashboard?token=...`
5. Flask queries PostgreSQL for user + products
6. Returns JSON; JS renders the UI

## Request Flow: Price Check (Scheduled)

1. APScheduler fires every hour
2. Job queries all users where `status IN ('active', 'pro')` and `email_verified = TRUE`
3. For each user's products:
   - Skip if last checked < 2h ago (Pro) or < 6h ago (Free)
   - Call Firecrawl API to scrape the product page
   - Extract price via LLM-powered extraction
   - Log to `price_history` table
   - If current price ≤ target price → send Gmail alert
4. Logs summary to Railway console

## Request Flow: Stripe Upgrade

1. User clicks Upgrade in dashboard
2. JS calls `POST /api/create-checkout-session` with token + billing (monthly/annual)
3. Flask creates a Stripe Checkout session with `client_reference_id = token`
4. Browser redirects to Stripe-hosted checkout page
5. User pays with card
6. Stripe calls `POST /api/stripe-webhook`
7. Flask verifies signature, handles `checkout.session.completed`:
   - Finds user by `client_reference_id` (token)
   - Sets `is_pro = TRUE`, saves Stripe customer/subscription IDs
8. User is redirected to `/upgrade-success`

---

## Related Notes
- [[01 - Project Overview]]
- [[03 - Database Schema]]
- [[06 - Stripe & Payments]]
- [[07 - Price Monitoring System]]
