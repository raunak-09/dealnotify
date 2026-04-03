# PriceGuard — Database Schema

Database: PostgreSQL (Railway plugin)
Driver: pg8000
Initialized by: `init_db()` in `web_app.py` — runs on every app startup, safe to run repeatedly (uses `IF NOT EXISTS` and `ADD COLUMN IF NOT EXISTS`).

---

## Table: `users`

The core user table. One row per registered user.

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `id` | SERIAL | auto | Primary key |
| `name` | TEXT | — | Full name |
| `email` | TEXT | — | Unique, lowercase |
| `token` | TEXT | — | UUID, used as auth token in all API calls |
| `status` | TEXT | `'active'` | Legacy: `'active'` or `'pro'`. **Do not use to gate features — use `is_pro` instead.** |
| `trial_end` | TIMESTAMP | — | 7 days from signup |
| `created_at` | TIMESTAMP | NOW() | |
| `password_hash` | TEXT | NULL | bcrypt hash |
| `email_verified` | BOOLEAN | FALSE | Must be TRUE to log in |
| `verification_token` | TEXT | NULL | UUID sent in verification email |
| `reset_token` | TEXT | NULL | UUID sent in password reset email |
| `reset_token_expiry` | TIMESTAMP | NULL | Reset token expires 1 hour after issue |
| `phone` | TEXT | NULL | Optional, user can add in My Account |
| `newsletter` | BOOLEAN | TRUE | Newsletter opt-in |
| `stripe_customer_id` | TEXT | NULL | Set by Stripe webhook on first purchase |
| `stripe_subscription_id` | TEXT | NULL | Set by Stripe webhook; cleared on cancel |
| `is_pro` | BOOLEAN | FALSE | **Single source of truth for Pro status.** Set TRUE by `checkout.session.completed` webhook. Set FALSE by `customer.subscription.deleted` webhook. |

### Important: `is_pro` vs `status`
- `is_pro = TRUE` → user has active paid subscription → show Pro features, use 2h check interval
- `is_pro = FALSE` → user is on free tier → enforce 3 product limit, use 6h check interval
- `status` column exists for legacy reasons. Back-filled: anyone with `status = 'pro'` got `is_pro = TRUE` during migration.

---

## Table: `products`

One row per tracked product per user.

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `id` | SERIAL | auto | Primary key |
| `user_id` | INTEGER | — | Foreign key → `users.id` |
| `url` | TEXT | — | Full product URL |
| `target_price` | REAL | — | User's desired price |
| `current_price` | REAL | NULL | Last scraped price |
| `store` | TEXT | NULL | Detected from URL: `amazon`, `walmart`, `bestbuy`, `target`, `ebay`, `costco`, `other` |
| `name` | TEXT | NULL | Product name (scraped or extracted from URL) |
| `status` | TEXT | `'active'` | Always `'active'` currently |
| `last_checked` | TIMESTAMP | NULL | When price was last fetched — used to enforce check intervals |
| `created_at` | TIMESTAMP | NOW() | |

---

## Table: `price_history`

One row per price check for each product. Used to render the price history chart.

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `id` | SERIAL | auto | Primary key |
| `product_id` | INTEGER | — | Foreign key → `products.id` |
| `price` | REAL | — | Price at the time of check |
| `checked_at` | TIMESTAMP | NOW() | When this price was recorded |

**Index:** `idx_price_history_product` on `(product_id, checked_at DESC)` for fast per-product history queries.

API returns up to 90 days of history per product.

---

## Schema Creation Code (reference)

```sql
-- users
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name TEXT,
    email TEXT UNIQUE,
    token TEXT,
    status TEXT DEFAULT 'active',
    trial_end TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- products
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    url TEXT,
    target_price REAL,
    current_price REAL,
    store TEXT,
    name TEXT,
    status TEXT DEFAULT 'active',
    last_checked TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- price_history
CREATE TABLE IF NOT EXISTS price_history (
    id SERIAL PRIMARY KEY,
    product_id INTEGER,
    price REAL,
    checked_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_price_history_product
ON price_history(product_id, checked_at DESC);
```

Columns added via migrations (safe to re-run):
```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_token TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expiry TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS newsletter BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_pro BOOLEAN NOT NULL DEFAULT FALSE;
```

---

## Related Notes
- [[01 - Project Overview]]
- [[04 - API Reference]]
- [[06 - Stripe & Payments]]
