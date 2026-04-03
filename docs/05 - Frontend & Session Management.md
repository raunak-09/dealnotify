# PriceGuard — Frontend & Session Management

## Files

| File | Purpose |
|------|---------|
| `index.html` | Public landing page, pricing, login, signup, forgot/reset password |
| `dashboard.html` | Authenticated user dashboard (SPA) |
| `upgrade-success.html` | Post-payment confirmation page |

No build system, no framework. Pure HTML + CSS + vanilla JavaScript.

---

## Session Management (localStorage)

### Why localStorage?
Flask sessions reset on each Railway deploy. Using localStorage persists the user's session across deploys and page refreshes.

### Storage Keys

| Key | Value |
|-----|-------|
| `pg_token` | The user's auth token (UUID from DB) |
| `pg_token_expiry` | ISO timestamp — when the session expires |

### Session Lifetime
- **Duration:** 10 minutes of inactivity
- **Rolling expiry:** Any user activity (mouse move, keydown, click, scroll, touch) resets the 10-minute clock
- **Activity throttle:** Expiry is only refreshed at most once every 30 seconds to avoid hammering

### Session Watcher
A `setInterval` runs every 15 seconds and checks if the expiry has passed:
- If expired → clears localStorage → redirects to `/?session_expired=1`
- If within 2 minutes of expiry → shows a yellow warning banner: *"Your session will expire in X minutes. Stay signed in?"*

### Sign In Flow
1. Login API returns `{ token, dashboard_url }`
2. `saveSession(token)` stores token + sets expiry to now + 10 minutes
3. Browser navigates to `dashboard_url`
4. Dashboard JS reads token from URL param first, then localStorage fallback
5. `history.replaceState` removes token from URL bar (clean URL)

### Sign Out
`clearSession()` removes both localStorage keys, then redirects to `/`.

### index.html Session Detection
On `DOMContentLoaded`, index.html checks localStorage for a valid session:
- If valid → swaps nav link to "My Dashboard →" and hero CTA to "Go to Dashboard"
- If `?session_expired=1` in URL → shows a toast: "Your session expired due to inactivity."

---

## Toast Notifications

**Position:** Top-center, 80px from the top (clears the fixed navbar)
**Shape:** Pill / capsule (`border-radius: 50px`)
**Animation:** Fades in + slides down 20px from above
**z-index:** 99999 (on top of everything)
**Duration:** 4 seconds, then fades out

### Types
| Class | Color | Used for |
|-------|-------|---------|
| `.toast.success` | Green `#1e8449` | Product added, prices updated, message sent |
| `.toast.error` | Red `#c0392b` | Validation errors, API failures |
| default | Dark `#222` | Neutral messages |

### Usage in JS
```javascript
showToast('✅ Product added!', 'success');
showToast('Please enter a valid URL', 'error');
showToast('Session expired.');
```

---

## Dashboard Features

### Stats Bar
Shows totals at a glance: products tracked, active alerts, price drops caught.

### Product Cards
Each product card shows:
- Store logo / icon
- Product name
- Current price vs target price
- Last checked timestamp
- "📈 History" button → opens price history modal
- Delete button

### Price History Modal
Opens when user clicks "📈 History":
- Summary stats: current price, lowest ever, highest ever
- SVG line chart (no external libraries):
  - Area fill under the line
  - Dashed horizontal line at target price
  - Red dot markers for days where price was at or below target
  - X-axis: dates (abbreviated)
  - Y-axis: price range

### My Account Section
Accessible from the top-right avatar/menu:
- Edit name, phone number
- Newsletter opt-in toggle
- Plan card:
  - **Pro users:** Gold card — "Pro Plan — Active"
  - **Free users:** Blue card — shows days remaining in trial + "Upgrade" button

### Pro Badge
- Pro users: gold "⚡ Pro" badge next to their name
- Free users: grey "Free" badge

### Upgrade Modal
- Monthly / Annual toggle (switches displayed price)
- "Upgrade Now" button calls `/api/create-checkout-session?billing=monthly|annual`
- Billing selection stored in JS variable `upgradeBilling`

### Refresh Prices Button
Calls `GET /api/check-prices?token=...` and shows a toast with result.

---

## index.html Features

### Pricing Toggle
Pricing section has a Monthly / Annual toggle. Annual shows discounted prices.

### Login / Signup / Forgot Password Modals
All in the same page, shown/hidden via JS. Forms post to `/api/login`, `/api/signup`, `/api/forgot-password`.

### Session Expired Toast
If URL contains `?session_expired=1`, shows: "Your session expired due to inactivity. Please log in again."

---

## Related Notes
- [[04 - API Reference]]
- [[06 - Stripe & Payments]]
- [[10 - Bugs & Fixes]]
