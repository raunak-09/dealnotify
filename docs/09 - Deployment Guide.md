# PriceGuard — Deployment Guide

## Platform: Railway

**URL:** https://railway.app
**Project:** dealnotify
**Auto-deploys:** Yes — every push to `main` branch triggers a rebuild

---

## How to Deploy a Change

```bash
git add <files>
git commit -m "describe your change"
git push origin main
```

Railway detects the push and rebuilds automatically. Takes ~1-2 minutes. Monitor in Railway → Deployments tab.

---

## First-Time Setup (from scratch)

### 1. Create Railway Service
1. Go to https://railway.app → New Project → Deploy from GitHub
2. Connect your GitHub account and select `raunak-09/dealnotify`
3. Railway auto-detects the `Procfile` and runs `python web_app.py`

### 2. Add PostgreSQL
1. In your Railway project → click **+ New** → **Database** → **Add PostgreSQL**
2. Railway provisions a Postgres instance and automatically adds `DATABASE_URL` to your service's environment
3. On the next deploy, `init_db()` runs and creates all tables automatically

### 3. Add Environment Variables
Go to your Railway service → **Variables** tab and add all variables from [[08 - Environment Variables]].

### 4. Add Custom Domain
1. Railway service → **Settings** → **Networking** → **Add Custom Domain**
2. Enter `www.dealnotify.co`
3. Railway gives you a CNAME target (e.g. `xyz.up.railway.app`)
4. In Cloudflare, create a CNAME record: `www` → the Railway target, **Proxied (orange cloud)**

> Note: Railway only allows one custom domain per service. `dealnotify.co` (non-www) is handled via Cloudflare redirect rule — see HTTPS section below.

---

## Cloudflare Setup (HTTPS & Dual Domain)

Both `www.dealnotify.co` and `dealnotify.co` must be HTTPS. Railway only handles one domain, so Cloudflare handles the dual-domain routing.

### DNS Records in Cloudflare
| Type | Name | Target | Proxy |
|------|------|--------|-------|
| CNAME | `www` | `<your-railway-target>.up.railway.app` | ✅ Proxied |
| A | `@` (root) | `192.0.2.1` (placeholder) | ✅ Proxied |

### Cloudflare Rules
1. **Always Use HTTPS** (SSL/TLS → Edge Certificates → Always Use HTTPS: ON)
2. **Redirect Rule:** non-www → www
   - Match: `dealnotify.co/*`
   - Action: Redirect to `https://www.dealnotify.co/$1` (301)

### SSL/TLS Mode
Set to **Full** in Cloudflare SSL/TLS settings.

### Why this works
- User visits `http://dealnotify.co` → Cloudflare redirects to `https://www.dealnotify.co`
- User visits `https://www.dealnotify.co` → Cloudflare proxies to Railway → Flask app
- HSTS header in Flask ensures browsers always use HTTPS after first visit

---

## HTTPS in the Flask App

`web_app.py` has two HTTPS-related mechanisms:

### 1. `force_https()` before_request
Redirects HTTP → HTTPS in production:
```python
proto = request.headers.get('X-Forwarded-Proto', '')
# Handles comma-separated values (Railway proxy quirk)
if ',' in proto:
    proto = proto.split(',')[0].strip()
host = request.headers.get('Host', '')
is_production = 'dealnotify.co' in host or 'railway.app' in host
if proto == 'http' and is_production:
    return redirect(request.url.replace('http://', 'https://'), 301)
```

### 2. `add_security_headers()` after_request
Adds security headers to every response:
```python
response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
response.headers['X-Content-Type-Options'] = 'nosniff'
response.headers['X-Frame-Options'] = 'SAMEORIGIN'
response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
```

---

## Railway Procfile

```
web: python web_app.py
```

`web_app.py` must bind to `0.0.0.0` and use Railway's `PORT` env var:
```python
app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
```

---

## Checking Logs

Railway Dashboard → your service → **Deployments** → click latest → **Deploy Logs**

Healthy startup log:
```
🚀 Starting PriceGuard web app...
⏰ Price check scheduler started (runs hourly; Pro=2h interval, Free=6h interval)
 * Running on all addresses (0.0.0.0)
 * Running on http://127.0.0.1:5000
```

---

## Common Errors & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `Application failed to respond` | App binding to localhost | Use `host="0.0.0.0"` |
| `AttributeError: get` in Stripe webhook | Stripe SDK returns StripeObject not dict | Use `getattr(session, 'field', None)` — see [[10 - Bugs & Fixes]] |
| Site shows "Not Secure" | Missing HSTS header or Cloudflare not set up | Ensure Cloudflare is proxying + HSTS header is added |
| `postgres://` connection error | pg8000 needs `postgresql://` prefix | `web_app.py` auto-converts this |
| Scheduled job not running | Was set to once/day (daily 6am cron) | Fixed to `minute='0'` (every hour) |

---

## Related Notes
- [[08 - Environment Variables]]
- [[10 - Bugs & Fixes]]
- [[02 - Tech Stack & Architecture]]
