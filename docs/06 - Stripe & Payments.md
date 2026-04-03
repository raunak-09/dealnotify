# PriceGuard — Stripe & Payments

## Overview

Stripe handles all billing. The app uses Stripe Checkout (hosted payment page) + webhooks to keep the database in sync.

---

## Plans in Stripe

You need two Price objects set up in the Stripe dashboard:

| Plan | Billing | Stripe Price ID env var | Amount |
|------|---------|------------------------|--------|
| Pro Monthly | Monthly recurring | `STRIPE_PRICE_ID` | $4.99/month |
| Pro Annual | Yearly recurring | `STRIPE_ANNUAL_PRICE_ID` | $39.99/year |

**How to create a Price in Stripe:**
1. Stripe Dashboard → Products → + Add Product
2. Name: "PriceGuard Pro"
3. Add a price → Recurring → set amount and interval
4. Copy the Price ID (starts with `price_...`)
5. Paste into Railway environment variables

---

## Environment Variables Required

| Variable | Value |
|----------|-------|
| `STRIPE_SECRET_KEY` | `sk_live_...` (or `sk_test_...` for testing) |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` (from Stripe webhook settings) |
| `STRIPE_PRICE_ID` | Monthly price ID (`price_...`) |
| `STRIPE_ANNUAL_PRICE_ID` | Annual price ID (`price_...`) |

---

## Checkout Flow

1. User clicks "Upgrade" in the dashboard
2. Selects Monthly or Annual billing
3. Frontend calls:
   ```
   POST /api/create-checkout-session?token=<user_token>&billing=monthly
   ```
   or
   ```
   POST /api/create-checkout-session?token=<user_token>&billing=annual
   ```
4. Backend creates a Stripe Checkout Session:
   - `mode: subscription`
   - `price_id`: monthly or annual depending on `billing` param
   - `client_reference_id`: user's token (this links payment to user in the webhook)
   - `success_url`: `https://www.dealnotify.co/upgrade-success`
   - `cancel_url`: `https://www.dealnotify.co/dashboard?token=...`
5. Backend returns `{ checkout_url: "https://checkout.stripe.com/..." }`
6. Frontend redirects: `window.location.href = data.checkout_url`

---

## Webhook: `checkout.session.completed`

Fired by Stripe immediately after a successful payment.

**What the handler does:**
1. Verifies the webhook signature using `STRIPE_WEBHOOK_SECRET`
2. Reads `client_reference_id` from the session → this is the user's token
3. Finds the user in the DB by token
4. Sets:
   ```sql
   UPDATE users SET
     is_pro = TRUE,
     status = 'pro',
     stripe_customer_id = <customer_id>,
     stripe_subscription_id = <subscription_id>
   WHERE token = <token>
   ```

**Critical implementation note:**
Stripe's Python SDK returns a `StripeObject` (not a plain dict). You **must** use `getattr(session, 'field', None)` — NOT `session.get('field')`. `.get()` raises `AttributeError` on `StripeObject`.

```python
# ✅ Correct
token = getattr(session, 'client_reference_id', None)
customer_id = getattr(session, 'customer', None)

# ❌ Wrong — crashes with AttributeError
token = session.get('client_reference_id')
```

---

## Webhook: `customer.subscription.deleted`

Fired when a user cancels or their subscription lapses.

**What the handler does:**
```sql
UPDATE users SET
  is_pro = FALSE,
  status = 'active',
  stripe_subscription_id = NULL
WHERE stripe_subscription_id = <subscription_id>
```

---

## Stripe Webhook Configuration

In Stripe Dashboard → Developers → Webhooks:
- **Endpoint URL:** `https://www.dealnotify.co/api/stripe-webhook`
- **Events to listen for:**
  - `checkout.session.completed`
  - `customer.subscription.deleted`
- Copy the **Signing Secret** (`whsec_...`) → paste as `STRIPE_WEBHOOK_SECRET` in Railway

---

## Testing Stripe Locally

Use Stripe CLI:
```bash
stripe listen --forward-to localhost:5000/api/stripe-webhook
```

Use test card: `4242 4242 4242 4242` (any future expiry, any CVC)

---

## How `is_pro` Gets Set

```
User pays → Stripe fires webhook → handler sets is_pro = TRUE → user sees Pro plan in dashboard
User cancels → Stripe fires webhook → handler sets is_pro = FALSE → user reverts to Free
```

`is_pro` is the **only** field you should check in the frontend and backend for gating features. Do not check `status`.

---

## Related Notes
- [[03 - Database Schema]]
- [[08 - Environment Variables]]
- [[10 - Bugs & Fixes]]
