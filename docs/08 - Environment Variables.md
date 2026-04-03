# PriceGuard — Environment Variables

## Where to Set Them

- **Production:** Railway Dashboard → your service → **Variables** tab
- **Local development:** Create a `.env` file in the repo root (never commit this file)

---

## All Variables

### `DATABASE_URL`
- **Purpose:** PostgreSQL connection string
- **Where to get it:** Railway automatically provides this when you add a PostgreSQL plugin. It appears in your service's Variables tab automatically.
- **Format:** `postgresql://user:password@host:port/dbname`
- **Note:** Railway may provide `postgres://` prefix — `web_app.py` converts it to `postgresql://` automatically for pg8000 compatibility.

---

### `FIRECRAWL_API_KEY`
- **Purpose:** Authenticates with Firecrawl API for web scraping product pages
- **Where to get it:** https://firecrawl.dev → Dashboard → API Keys
- **Format:** `fc-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
- **Note:** This is the key for the **live Railway app**. It is separate from any Firecrawl MCP you configure in Claude desktop.

---

### `GMAIL_PASSWORD`
- **Purpose:** Gmail App Password for sending emails via SMTP
- **Sender account:** manisha.jmc@gmail.com
- **Where to get it:**
  1. Go to https://myaccount.google.com/apppasswords
  2. Enable 2-Step Verification first
  3. Create a new App Password (call it "priceguard" or "dealnotify")
  4. Copy the 16-character code shown (remove spaces)
- **Warning:** App Passwords are shown only once. If lost, delete and create a new one.

---

### `STRIPE_SECRET_KEY`
- **Purpose:** Authenticates Stripe API calls (create checkout sessions, verify webhooks)
- **Where to get it:** Stripe Dashboard → Developers → API Keys
- **Format:** `sk_live_...` (production) or `sk_test_...` (testing)
- **Warning:** Never expose this key in frontend code or logs.

---

### `STRIPE_WEBHOOK_SECRET`
- **Purpose:** Verifies that incoming webhook requests are genuinely from Stripe
- **Where to get it:** Stripe Dashboard → Developers → Webhooks → click your endpoint → Signing secret
- **Format:** `whsec_...`
- **Note:** Different from the API key. A new secret is generated each time you create a new webhook endpoint.

---

### `STRIPE_PRICE_ID`
- **Purpose:** Stripe Price ID for the monthly Pro plan
- **Where to get it:** Stripe Dashboard → Products → PriceGuard Pro → Monthly price → copy Price ID
- **Format:** `price_...`

---

### `STRIPE_ANNUAL_PRICE_ID`
- **Purpose:** Stripe Price ID for the annual Pro plan
- **Where to get it:** Stripe Dashboard → Products → PriceGuard Pro → Annual price → copy Price ID
- **Format:** `price_...`
- **Fallback:** If this env var is not set, the checkout will fall back to the monthly price.

---

### `SECRET_KEY`
- **Purpose:** Flask secret key (used for any future session/cookie signing)
- **Recommended value:** A long random string, e.g. generated with `python -c "import secrets; print(secrets.token_hex(32))"`

---

## Local `.env` File (Development)

```env
DATABASE_URL=postgresql://localhost/priceguard_dev
FIRECRAWL_API_KEY=fc-your-key-here
GMAIL_PASSWORD=your16charpassword
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_ID=price_...
STRIPE_ANNUAL_PRICE_ID=price_...
SECRET_KEY=your-random-secret-key
```

Never commit `.env` — it is in `.gitignore`.

---

## Railway Variables Checklist

When setting up Railway from scratch, add all of these:

- [ ] `DATABASE_URL` (auto-added by Railway PostgreSQL plugin)
- [ ] `FIRECRAWL_API_KEY`
- [ ] `GMAIL_PASSWORD`
- [ ] `STRIPE_SECRET_KEY`
- [ ] `STRIPE_WEBHOOK_SECRET`
- [ ] `STRIPE_PRICE_ID`
- [ ] `STRIPE_ANNUAL_PRICE_ID`
- [ ] `SECRET_KEY`

---

## Related Notes
- [[09 - Deployment Guide]]
- [[06 - Stripe & Payments]]
