# PriceGuard — Project Overview

## The Product

PriceGuard monitors product prices on e-commerce sites and emails users when the price drops to their target. It is a subscription SaaS with a free tier and a paid Pro tier.

**Brand name:** PriceGuard
**Domain:** www.dealnotify.co (the GitHub repo and email are still under "dealnotify")
**Owner email:** manisha.jmc@gmail.com

---

## Business Model

| Plan | Price | Products | Check Interval |
|------|-------|----------|---------------|
| Free | $0 | 3 products max | Every 6 hours |
| Pro (Monthly) | $4.99 / month | Unlimited | Every 2 hours |
| Pro (Annual) | $39.99 / year | Unlimited | Every 2 hours |

- Free users get a 7-day trial period (tracked via `trial_end` column).
- Pro status is determined by the `is_pro` boolean column — this is the single source of truth.
- Stripe handles all billing. When a payment completes, a webhook sets `is_pro = TRUE`. When a subscription is cancelled, the webhook sets `is_pro = FALSE`.

---

## Supported Retailers

Amazon, Walmart, Best Buy, Target, eBay, Costco (and most other e-commerce URLs via Firecrawl).

---

## Key Features (as of April 2026)

- User signup with email verification (unverified users cannot log in)
- Login / forgot password / reset password
- Per-user product dashboard (add URL + target price, view current price)
- Automatic price checks on a background scheduler (Pro=2h, Free=6h)
- Email alert when current price ≤ target price
- Price history chart (90 days) with SVG line chart, lowest/highest, alert dots
- "Refresh Prices" button for manual on-demand check
- My Account section (name, phone, newsletter toggle, plan badge)
- Upgrade modal with monthly/annual billing toggle
- Session persistence via localStorage with 10-minute inactivity timeout
- Session expiry warning banner (2 minutes before timeout)
- Toast notifications (top-center, pill-shaped)
- Contact Us form (sends email to hello@dealnotify.co)
- Admin panel at `/admin` showing all users and revenue
- Newsletter subscription opt-in at signup
- HTTPS enforced (Cloudflare + HSTS header)
- Security headers (X-Frame-Options, X-Content-Type-Options, Referrer-Policy)

---

## File Structure

```
dealnotify/                   ← GitHub repo root
├── web_app.py                ← MAIN FILE: all Flask routes, DB init, Stripe, scheduler
├── dashboard.html            ← User dashboard (single-page app, served by Flask)
├── index.html                ← Public landing page + login + pricing
├── upgrade-success.html      ← Post-payment success page
├── email_alerts.py           ← Gmail SMTP email sending functions
├── scraper.py                ← Firecrawl price extraction helper
├── price_monitor.py          ← Older standalone price monitor script (not used in prod)
├── price_monitor_v2.py       ← Older version
├── price_monitor_v3.py       ← Older version
├── app.py                    ← Older API-only backend (superseded by web_app.py)
├── requirements.txt          ← Python dependencies
├── Procfile                  ← Railway start command: web: python web_app.py
├── robots.txt                ← SEO — disallows /admin, /api/*
├── sitemap.xml               ← SEO sitemap
├── test_bestbuy.py           ← Manual test scripts
├── test_bestbuy_v2.py
├── test_email_alert.py
├── BUSINESS_GUIDE.md         ← Business setup notes
├── COMPLETE_SETUP.md         ← Full setup walkthrough
└── EMAIL_SETUP.md            ← Gmail SMTP setup guide
```

> **Note:** `web_app.py` is the only file that matters for production. It includes everything: routing, DB, Stripe, email, and the background scheduler. The older `app.py`, `price_monitor*.py`, and `scraper.py` files are legacy.

---

## Key Architectural Decisions

**Single-file backend** — Everything lives in `web_app.py` for simplicity. No blueprints, no separate modules.

**`is_pro` boolean is the source of truth** — Do not use `status = 'pro'` to gate Pro features. Always check `is_pro`. The `status` column exists for legacy reasons.

**Token-based auth (no server sessions)** — Each user has a `token` UUID in the DB. The frontend stores this in localStorage and sends it as a query param on API calls (`?token=...`). No Flask sessions, no JWT.

**pg8000 (not psycopg2)** — The DB driver is pg8000, a pure-Python PostgreSQL driver. Works on Railway without C dependencies. Queries use `%s` placeholders.

**APScheduler (in-process)** — The price check scheduler runs inside the Flask process using APScheduler's BackgroundScheduler. It runs every hour; per-product interval logic (2h/6h) is inside the job itself.

---

## Related Notes
- [[02 - Tech Stack & Architecture]]
- [[03 - Database Schema]]
- [[04 - API Reference]]
- [[06 - Stripe & Payments]]
- [[07 - Price Monitoring System]]
