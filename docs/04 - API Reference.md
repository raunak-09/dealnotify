# PriceGuard — API Reference

Base URL: `https://www.dealnotify.co`

**Auth:** Most user-facing endpoints require `?token=<user_token>` as a query parameter. The token is a UUID stored in the `users` table and in the user's localStorage.

---

## Auth & Account

### `POST /api/signup`
Register a new user.

**Request body (JSON):**
```json
{
  "name": "Jane Doe",
  "email": "jane@example.com",
  "password": "securepassword",
  "newsletter": true
}
```
**Response (success):**
```json
{ "success": true, "message": "Verification email sent." }
```
**Response (error):**
```json
{ "error": "Email already registered." }
```
**Side effect:** Sends verification email with a link containing `verification_token`.

---

### `GET /api/verify-email?token=<verification_token>`
Verify a user's email address. Called when user clicks the link in their verification email.

**Response:** Redirects to `/?verified=1` on success, or returns error JSON.

---

### `POST /api/resend-verification`
Resend the verification email (e.g., if user didn't receive it).

**Request body (JSON):**
```json
{ "email": "jane@example.com" }
```
**Response:** `{ "success": true }` or error JSON.

---

### `POST /api/login`
Log in a verified user.

**Request body (JSON):**
```json
{ "email": "jane@example.com", "password": "securepassword" }
```
**Response (success):**
```json
{ "success": true, "token": "uuid-here", "dashboard_url": "/dashboard?token=..." }
```
**Response (unverified):**
```json
{ "error": "verify_email", "message": "Please verify your email first." }
```

---

### `POST /api/forgot-password`
Trigger a password reset email.

**Request body:** `{ "email": "jane@example.com" }`
**Side effect:** Sends email with reset link containing `reset_token` (expires 1 hour).

---

### `POST /api/reset-password`
Set a new password using a reset token.

**Request body:** `{ "token": "<reset_token>", "password": "newpassword" }`

---

### `POST /api/update-account`
Update user profile fields.

**Query:** `?token=<user_token>`
**Request body:** `{ "name": "...", "phone": "...", "newsletter": true }`

---

## Dashboard & Products

### `GET /api/dashboard`
Get the current user's data and all their products.

**Query:** `?token=<user_token>`
**Response:**
```json
{
  "user": {
    "name": "Jane Doe",
    "email": "jane@example.com",
    "phone": null,
    "newsletter": true,
    "status": "active",
    "is_pro": false,
    "trial_end": "2026-04-10T12:00:00",
    "token": "...",
    "stripe_customer_id": null
  },
  "products": [
    {
      "id": 1,
      "url": "https://amazon.com/...",
      "name": "Sony WH-1000XM5",
      "store": "amazon",
      "target_price": 299.0,
      "current_price": 349.0,
      "last_checked": "2026-04-03T10:00:00",
      "created_at": "2026-03-01T08:00:00"
    }
  ]
}
```

---

### `POST /api/add-product`
Add a product to track.

**Query:** `?token=<user_token>`
**Request body:** `{ "url": "https://...", "target_price": 299.99 }`
**Response (success):** `{ "success": true, "product": { ...product object... } }`
**Response (free limit):** `{ "error": "free_limit_reached", "message": "..." }`

---

### `DELETE /api/remove-product`
Remove a tracked product.

**Query:** `?token=<user_token>&product_id=<id>`

---

### `GET /api/check-prices`
Manually trigger a price check for the current user's products.

**Query:** `?token=<user_token>`
**Response:** `{ "success": true, "alerts_sent": 0, "updated": 3 }`

---

### `GET /api/price-history/<product_id>`
Get 90 days of price history for a product.

**Query:** `?token=<user_token>`
**Response:**
```json
{
  "product_id": 1,
  "history": [
    { "price": 349.0, "checked_at": "2026-04-03T10:00:00" },
    { "price": 329.0, "checked_at": "2026-04-01T10:00:00" }
  ]
}
```

---

## Stripe & Payments

### `POST /api/create-checkout-session`
Start a Stripe Checkout flow.

**Query:** `?token=<user_token>&billing=monthly` (or `billing=annual`)
**Response:** `{ "checkout_url": "https://checkout.stripe.com/..." }`

---

### `POST /api/stripe-webhook`
Stripe webhook endpoint — receives events from Stripe. **Do not call manually.**
Handles:
- `checkout.session.completed` → sets `is_pro = TRUE`
- `customer.subscription.deleted` → sets `is_pro = FALSE`

---

## Other

### `POST /api/contact`
Send a contact form message to hello@dealnotify.co.

**Request body:** `{ "name": "...", "email": "...", "message": "..." }`

---

### `GET /api/signups`
Admin endpoint — list all signups.

---

### `GET /api/check-all-prices`
Admin endpoint — manually trigger the full scheduled price check job for all users.

---

### `GET /api/debug-headers`
Temp diagnostic endpoint — returns request headers. Used to debug HTTPS/proxy issues.

---

## Pages (HTML)

| Route | Serves |
|-------|--------|
| `GET /` | `index.html` — landing page |
| `GET /dashboard` | `dashboard.html` — user dashboard |
| `GET /upgrade-success` | `upgrade-success.html` |
| `GET /admin` | Admin panel (internal) |
| `GET /sitemap.xml` | SEO sitemap |
| `GET /robots.txt` | SEO robots file |

---

## Related Notes
- [[03 - Database Schema]]
- [[06 - Stripe & Payments]]
- [[05 - Frontend & Session Management]]
