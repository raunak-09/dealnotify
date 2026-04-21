"""
DealNotify - Web App (Landing Page + Backend)
Database: PostgreSQL (via DATABASE_URL env var — provisioned by Railway)
Auth: password hashing via werkzeug, email verification, forgot/reset password
"""

from flask import Flask, request, jsonify, send_from_directory
import os
import secrets
import time
import hmac
import threading
import html as html_module
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait as futures_wait, FIRST_COMPLETED
from datetime import datetime, timedelta
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from dotenv import load_dotenv
from price_monitor import scrape_price, scrape_stock_status, extract_stock_status
from price_comparison import find_comparable_product
import pg8000.dbapi as pg8000
from urllib.parse import urlparse
from apscheduler.schedulers.background import BackgroundScheduler
import stripe
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='')


# ─────────────────────────────────────────────
# SECURITY: Rate Limiter (in-memory, per-IP)
# ─────────────────────────────────────────────

class RateLimiter:
    """Simple in-memory rate limiter. Tracks requests per IP per endpoint."""
    def __init__(self):
        self._requests = defaultdict(list)  # key → list of timestamps

    def is_rate_limited(self, key, max_requests, window_seconds):
        """Returns True if the key has exceeded max_requests in window_seconds."""
        now = time.time()
        cutoff = now - window_seconds
        # Clean old entries
        self._requests[key] = [t for t in self._requests[key] if t > cutoff]
        if len(self._requests[key]) >= max_requests:
            return True
        self._requests[key].append(now)
        return False

rate_limiter = RateLimiter()

def get_client_ip():
    """Get real client IP behind Railway's proxy."""
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or 'unknown'


# ─────────────────────────────────────────────
# SECURITY: Token helper — reads from Authorization header or query param
# ─────────────────────────────────────────────

def get_token_from_request():
    """Read auth token from Authorization: Bearer header (preferred) or ?token= query param (fallback)."""
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[7:].strip()
    return request.args.get('token')


# ─────────────────────────────────────────────
# SECURITY: Admin auth helper
# ─────────────────────────────────────────────

def require_admin():
    """Check admin authentication. Returns True if authorized, False otherwise.
    Uses constant-time comparison to prevent timing attacks."""
    admin_password = os.getenv('ADMIN_PASSWORD', '')
    if not admin_password:
        return False

    # Check password from header (preferred) or query param (fallback)
    provided = request.headers.get('X-Admin-Password', '') or request.args.get('password', '')
    if provided and hmac.compare_digest(provided, admin_password):
        return True

    # Check if the request comes from an admin user via token
    token = get_token_from_request()
    if token:
        user, _ = get_user_by_token(token)
        if user:
            admin_emails = [e.strip().lower() for e in os.getenv('ADMIN_EMAILS', '').split(',') if e.strip()]
            if user['email'].lower() in admin_emails:
                return True

    return False


# ── Force HTTPS (Railway terminates SSL at the proxy and sets X-Forwarded-Proto)
@app.before_request
def force_https():
    # X-Forwarded-Proto can be comma-separated when multiple proxies are in the chain
    # e.g. "http, https" — take the first value only
    proto = request.headers.get('X-Forwarded-Proto', '').split(',')[0].strip()

    # Also catch the case where proto isn't set but the Host header reveals
    # we're on the live domain (not localhost/127.0.0.1)
    host = request.headers.get('Host', '')
    is_production = 'dealnotify.co' in host or 'railway.app' in host

    if proto == 'http' or (is_production and proto == ''):
        from flask import redirect
        url = request.url.replace('http://', 'https://', 1)
        return redirect(url, code=301)

    # Redirect bare domain to www
    if host == 'dealnotify.co':
        from flask import redirect
        return redirect(f'https://www.dealnotify.co{request.full_path.rstrip("?")}', code=301)


# ── REMOVED: /api/debug-headers — was exposing internal infrastructure details.
# If you need to debug Railway headers, use `railway logs` or a temporary local route.


# ── CORS: Handle preflight OPTIONS requests for Chrome extension ──
@app.route('/api/<path:path>', methods=['OPTIONS'])
def handle_options(path):
    """Respond to CORS preflight requests from the Chrome extension."""
    response = jsonify({'ok': True})
    return response, 200


# ── Security headers + CORS on every response ──
@app.after_request
def add_security_headers(response):
    # HSTS: tell browsers to always use HTTPS for this domain for 1 year
    response.headers['Strict-Transport-Security'] = \
        'max-age=31536000; includeSubDomains; preload'
    # Prevent MIME-type sniffing
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # Block clickjacking — only allow framing from same origin
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    # Only send the origin as referrer (not full URL) when crossing to another site
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # Content Security Policy — restrict where scripts/styles/images can load from
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://www.googletagmanager.com https://js.stripe.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://www.google-analytics.com https://api.stripe.com; "
        "frame-src https://js.stripe.com; "
        "font-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )
    # Disable browser features the app doesn't need
    response.headers['Permissions-Policy'] = (
        'camera=(), microphone=(), geolocation=(), payment=(self)'
    )
    # Legacy XSS protection (still useful for older browsers)
    response.headers['X-XSS-Protection'] = '1; mode=block'

    # ── CORS for Chrome Extension ──
    # Content scripts send Origin of the host page (e.g. amazon.com), not the extension ID.
    # Allow extension IDs, dealnotify.co, and all retailer/Amazon origins the extension runs on.
    origin = request.headers.get('Origin', '')
    _cors_allowed = (
        origin.startswith('chrome-extension://')
        or origin.endswith('dealnotify.co')
        or origin.endswith('amazon.com')
        or origin.endswith('walmart.com')
        or origin.endswith('target.com')
        or origin.endswith('bestbuy.com')
        or origin.endswith('costco.com')
    )
    if _cors_allowed:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Admin-Key, X-Admin-Password'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE, OPTIONS'
        response.headers['Access-Control-Max-Age'] = '86400'  # Cache preflight for 24h

    return response


# ─────────────────────────────────────────────
# DATABASE HELPERS
# ─────────────────────────────────────────────

def get_db_conn():
    """Get a PostgreSQL connection using pg8000 (pure Python, no libpq needed).
    Prefers DATABASE_PUBLIC_URL (public internet) over DATABASE_URL (internal network)
    so it works without Railway Private Networking being enabled."""
    db_url = (
        os.getenv('DATABASE_PUBLIC_URL') or
        os.getenv('DATABASE_URL')
    )
    if not db_url:
        raise Exception("DATABASE_URL environment variable not set")
    # Normalise scheme so urlparse works
    db_url = db_url.replace('postgres://', 'postgresql://', 1)
    p = urlparse(db_url)
    return pg8000.connect(
        host=p.hostname,
        port=p.port or 5432,
        database=p.path.lstrip('/'),
        user=p.username,
        password=p.password,
        ssl_context=True   # Railway Postgres requires SSL
    )


def _fetchone(cur):
    """Return a single row as a dict, or None."""
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def _fetchall(cur):
    """Return all rows as a list of dicts."""
    rows = cur.fetchall()
    if not rows:
        return []
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def init_db():
    """Create tables and run any pending migrations"""
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                token TEXT UNIQUE NOT NULL,
                signup_date TIMESTAMP NOT NULL DEFAULT NOW(),
                status TEXT NOT NULL DEFAULT 'active',
                trial_days_remaining INTEGER NOT NULL DEFAULT 30
            );
        """)
        # Auth columns migration — safe to run repeatedly
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_token TEXT;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token TEXT;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS reset_token_expiry TIMESTAMP;")
        # Profile / marketing columns
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS newsletter BOOLEAN NOT NULL DEFAULT TRUE;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone TEXT;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                url TEXT NOT NULL,
                target_price NUMERIC(10,2),
                store TEXT,
                added_date TIMESTAMP NOT NULL DEFAULT NOW(),
                status TEXT NOT NULL DEFAULT 'monitoring',
                last_checked TIMESTAMP,
                current_price NUMERIC(10,2),
                alert_sent BOOLEAN NOT NULL DEFAULT FALSE
            );
        """)
        # ── Restock-alert columns ──────────────────────────────────
        cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS track_type TEXT NOT NULL DEFAULT 'price';")
        cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS stock_status TEXT;")           # in_stock / out_of_stock
        cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS last_stock_status TEXT;")      # previous check's status
        cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS stock_detail TEXT;")           # raw signal text
        cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS restock_alert_sent BOOLEAN NOT NULL DEFAULT FALSE;")

        # Stripe migration — add columns if they don't exist yet
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT;")
        # Explicit Pro flag — single source of truth for paid status
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_pro BOOLEAN NOT NULL DEFAULT FALSE;")
        # Back-fill: anyone whose status is already 'pro' gets is_pro = TRUE
        cur.execute("UPDATE users SET is_pro = TRUE WHERE status = 'pro' AND is_pro = FALSE;")
        # Price history — every recorded price check per product
        cur.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id         SERIAL PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                price      NUMERIC(10,2) NOT NULL,
                checked_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_price_history_product
            ON price_history(product_id, checked_at DESC);
        """)
        # Alerts log — every price-drop alert email we send
        cur.execute("""
            CREATE TABLE IF NOT EXISTS alerts_log (
                id         SERIAL PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                product_url TEXT,
                store      TEXT,
                price_at_alert NUMERIC(10,2),
                target_price   NUMERIC(10,2),
                sent_at    TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_alerts_log_sent
            ON alerts_log(sent_at DESC);
        """)
        # alert_type column so alerts_log can record both price-drop and restock alerts
        cur.execute("ALTER TABLE alerts_log ADD COLUMN IF NOT EXISTS alert_type TEXT NOT NULL DEFAULT 'price_drop';")
        # Stock-status history — every recorded stock check
        cur.execute("""
            CREATE TABLE IF NOT EXISTS stock_history (
                id         SERIAL PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                status     TEXT NOT NULL,
                detail     TEXT,
                checked_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_stock_history_product
            ON stock_history(product_id, checked_at DESC);
        """)
        # Compare feature — product comparison cache
        cur.execute("""
            CREATE TABLE IF NOT EXISTS product_comparisons (
                id SERIAL PRIMARY KEY,
                source_retailer TEXT NOT NULL,
                source_identifier TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_title TEXT,
                source_price NUMERIC(10,2),
                target_retailer TEXT NOT NULL,
                target_url TEXT,
                target_title TEXT,
                target_price NUMERIC(10,2),
                confidence TEXT,
                match_reasoning TEXT,
                cached_at TIMESTAMP DEFAULT NOW(),
                expires_at TIMESTAMP DEFAULT (NOW() + INTERVAL '48 hours')
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_comparisons_lookup
            ON product_comparisons(source_retailer, source_identifier, target_retailer, expires_at);
        """)
        # Compare feature — affiliate click tracking
        cur.execute("""
            CREATE TABLE IF NOT EXISTS comparison_clicks (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                comparison_id INTEGER REFERENCES product_comparisons(id),
                clicked_at TIMESTAMP DEFAULT NOW()
            );
        """)
        conn.commit()
        print("✅ Database tables ready")
    except Exception as e:
        conn.rollback()
        print(f"❌ DB init error: {e}")
        raise
    finally:
        cur.close()
        conn.close()


def user_to_dict(user_row, products):
    """Convert a DB user row + product rows into the legacy dict format"""
    return {
        'id': user_row['id'],
        'name': user_row['name'],
        'email': user_row['email'],
        'token': user_row['token'],
        'signup_date': (user_row['signup_date'].isoformat() + 'Z') if hasattr(user_row['signup_date'], 'isoformat') else user_row['signup_date'],
        'status': user_row['status'],
        'trial_days_remaining': user_row['trial_days_remaining'],
        'products': [product_to_dict(p) for p in products]
    }


def product_to_dict(p):
    """Convert a DB product row to dict"""
    return {
        'id': p['id'],
        'url': p['url'],
        'target_price': float(p['target_price']) if p['target_price'] is not None else None,
        'store': p['store'],
        'added_date': (p['added_date'].isoformat() + 'Z') if hasattr(p['added_date'], 'isoformat') else p['added_date'],
        'status': p['status'],
        'last_checked': (p['last_checked'].isoformat() + 'Z') if p['last_checked'] and hasattr(p['last_checked'], 'isoformat') else p['last_checked'],
        'current_price': float(p['current_price']) if p['current_price'] is not None else None,
        'alert_sent': p['alert_sent'],
        'track_type': p.get('track_type', 'price'),
        'stock_status': p.get('stock_status'),
        'stock_detail': p.get('stock_detail'),
        'restock_alert_sent': p.get('restock_alert_sent', False),
    }


def log_price_history(product_id, price):
    """Insert one price record into price_history. Fire-and-forget — never raises."""
    try:
        conn = get_db_conn()
        cur  = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO price_history (product_id, price, checked_at) VALUES (%s, %s, %s)",
                (product_id, price, datetime.now())
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        print(f"⚠️  price_history log error (non-fatal): {e}")


def get_user_by_token(token):
    """Fetch user + their products by token"""
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM users WHERE token = %s", (token,))
        user = _fetchone(cur)
        if not user:
            return None, None
        cur.execute("SELECT * FROM products WHERE user_id = %s ORDER BY added_date ASC", (user['id'],))
        products = _fetchall(cur)
        return user, products
    finally:
        cur.close()
        conn.close()


def get_user_by_email(email):
    """Fetch user by email"""
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        return _fetchone(cur)
    finally:
        cur.close()
        conn.close()


# ─────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────

def get_base_url():
    return os.getenv('BASE_URL', 'https://www.dealnotify.co')


def get_store_name(url):
    if not url:
        return 'Unknown Store'
    url_lower = url.lower()
    if 'amazon' in url_lower:
        return 'Amazon'
    elif 'bestbuy' in url_lower:
        return 'Best Buy'
    elif 'walmart' in url_lower:
        return 'Walmart'
    elif 'target' in url_lower:
        return 'Target'
    elif 'ebay' in url_lower:
        return 'eBay'
    elif 'costco' in url_lower:
        return 'Costco'
    else:
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc.replace('www.', '').split('.')[0].capitalize()
        except:
            return 'Online Store'


# ─────────────────────────────────────────────
# EMAIL FUNCTIONS
# ─────────────────────────────────────────────

def send_welcome_email(name, email, dashboard_url):
    """Send welcome email to new customer via SendGrid"""
    try:
        api_key = os.getenv('SENDGRID_API_KEY')
        from_email = os.getenv('SENDGRID_FROM_EMAIL', 'hello@dealnotify.co')

        if not api_key:
            print("⚠️ Warning: SENDGRID_API_KEY not found - welcome email not sent")
            return False

        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
        <div style="background-color: white; max-width: 600px; margin: 0 auto; padding: 30px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">

        <h1 style="color: #667eea; text-align: center;">🎉 Welcome, {name}!</h1>

        <p style="color: #333; font-size: 16px;">
        Thank you for signing up for <strong>DealNotify</strong>! We're now monitoring prices for you.
        </p>

        <div style="background-color: #f0f7ff; padding: 20px; border-radius: 10px; margin: 25px 0; text-align: center; border: 2px solid #5b67f8;">
        <h2 style="color: #5b67f8; margin-top: 0;">📊 Your Personal Dashboard</h2>
        <p style="color: #333; margin-bottom: 20px;">View and manage all your tracked products in one place. Bookmark this link!</p>
        <a href="{dashboard_url}" style="display: inline-block; background: linear-gradient(135deg, #5b67f8 0%, #6c4fcf 100%); color: white; padding: 15px 40px; text-decoration: none; border-radius: 50px; font-weight: bold; font-size: 16px;">
        👉 View My Dashboard
        </a>
        <p style="color: #999; font-size: 12px; margin-top: 15px;">Keep this link private — it's your personal access link</p>
        </div>

        <div style="background-color: #f9f9f9; padding: 20px; border-radius: 5px; margin: 20px 0;">
        <h2 style="color: #5b67f8; margin-top: 0;">🚀 What happens next?</h2>
        <ol style="color: #333; line-height: 2;">
        <li>We'll start monitoring your product price right away</li>
        <li>When the price drops to your target, you'll get an instant email alert</li>
        <li>You can add more products anytime from your dashboard</li>
        </ol>
        </div>

        <div style="background-color: #f0f7ff; padding: 20px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #5b67f8;">
        <h3 style="color: #5b67f8; margin-top: 0;">💝 Your Free Trial</h3>
        <p style="color: #333;">You have <strong>30 days free</strong> to try all features!</p>
        <p style="color: #666; font-size: 14px;">After that, it's just <strong>$4.99/month</strong> for unlimited monitoring.</p>
        </div>

        <div style="background-color: #fff3cd; padding: 15px; border-radius: 5px; margin: 20px 0;">
        <p style="color: #856404; margin: 0; font-size: 14px;">
        <strong>💡 Pro Tip:</strong> Monitor Best Buy, Amazon, Walmart, Target and more for the best deals!
        </p>
        </div>

        <hr style="border: none; border-top: 2px solid #eee; margin: 30px 0;">
        <p style="color: #333; font-size: 14px;">Best regards,<br>
        <strong>🔔 The DealNotify Team</strong><br>
        <a href="mailto:hello@dealnotify.co" style="color: #5b67f8;">hello@dealnotify.co</a> | <a href="https://www.dealnotify.co" style="color: #5b67f8;">www.dealnotify.co</a><br><br>
        💰 <em>Never miss a price drop again!</em>
        </p>
        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
        <p style="color: #999; font-size: 12px; text-align: center;">© 2026 DealNotify. All rights reserved.</p>
        </div>
        </body>
        </html>
        """

        text_content = f"""
Welcome, {name}!

Thank you for signing up for DealNotify!

YOUR PERSONAL DASHBOARD:
{dashboard_url}
(Keep this link private — it's your personal access link)

WHAT HAPPENS NEXT:
1. We'll start monitoring your product price right away
2. When the price drops to your target, you'll get an instant email alert
3. You can add more products anytime from your dashboard

YOUR FREE TRIAL:
You have 30 days free to try all features!
After that, it's just $4.99/month for unlimited monitoring.

Questions? Reply to this email at hello@dealnotify.co

Best regards,
🔔 The DealNotify Team
hello@dealnotify.co | www.dealnotify.co
💰 Never miss a price drop again!

© 2026 DealNotify. All rights reserved.
        """

        message = Mail(
            from_email=from_email,
            to_emails=email,
            subject='🔔 Welcome to DealNotify! Your dashboard is ready',
            html_content=html_content,
            plain_text_content=text_content
        )

        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        print(f"✅ Welcome email sent to {email} (status: {response.status_code})")
        return True

    except Exception as e:
        print(f"❌ Error sending welcome email: {str(e)}")
        return False


def send_verification_email(name, email, verify_url):
    """Send email address verification link on signup"""
    try:
        api_key    = os.getenv('SENDGRID_API_KEY')
        from_email = os.getenv('SENDGRID_FROM_EMAIL', 'hello@dealnotify.co')
        if not api_key:
            print("⚠️  SENDGRID_API_KEY not set — skipping verification email")
            return False

        html_content = f"""
        <html><body style="font-family:Arial,sans-serif;background:#f5f5f5;padding:20px;">
        <div style="background:white;max-width:600px;margin:0 auto;padding:36px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.08);">
        <h1 style="color:#5b67f8;text-align:center;margin-bottom:6px;">🔔 DealNotify</h1>
        <h2 style="text-align:center;color:#1a1a2e;font-size:22px;">Verify your email, {name}!</h2>
        <p style="color:#555;font-size:15px;text-align:center;margin:12px 0 28px;">
            Click the button below to confirm your email address and activate your account.
        </p>
        <div style="text-align:center;margin:28px 0;">
            <a href="{verify_url}"
               style="display:inline-block;background:#5b67f8;color:white;padding:15px 40px;
                      text-decoration:none;border-radius:50px;font-weight:700;font-size:16px;">
               ✅ Verify My Email
            </a>
        </div>
        <p style="color:#999;font-size:13px;text-align:center;">
            Button not working? Copy and paste this link into your browser:<br>
            <a href="{verify_url}" style="color:#5b67f8;word-break:break-all;">{verify_url}</a>
        </p>
        <p style="color:#bbb;font-size:12px;text-align:center;margin-top:24px;">
            This link expires in 48 hours. If you didn't create a DealNotify account, you can safely ignore this email.
        </p>
        <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
        <p style="color:#999;font-size:12px;text-align:center;">© 2026 DealNotify · <a href="mailto:hello@dealnotify.co" style="color:#5b67f8;">hello@dealnotify.co</a></p>
        </div></body></html>
        """

        message = Mail(
            from_email=from_email,
            to_emails=email,
            subject='✅ Verify your DealNotify email address',
            html_content=html_content
        )
        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        print(f"📧 Verification email sent to {email} (status {response.status_code})")
        return True
    except Exception as e:
        print(f"❌ Verification email error: {e}")
        return False


def send_password_reset_email(name, email, reset_url):
    """Send password reset link"""
    try:
        api_key    = os.getenv('SENDGRID_API_KEY')
        from_email = os.getenv('SENDGRID_FROM_EMAIL', 'hello@dealnotify.co')
        if not api_key:
            print("⚠️  SENDGRID_API_KEY not set — skipping reset email")
            return False

        html_content = f"""
        <html><body style="font-family:Arial,sans-serif;background:#f5f5f5;padding:20px;">
        <div style="background:white;max-width:600px;margin:0 auto;padding:36px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.08);">
        <h1 style="color:#5b67f8;text-align:center;margin-bottom:6px;">🔔 DealNotify</h1>
        <h2 style="text-align:center;color:#1a1a2e;font-size:22px;">Reset your password</h2>
        <p style="color:#555;font-size:15px;text-align:center;margin:12px 0 28px;">
            Hi {name}, we received a request to reset your password.
            Click the button below — this link is valid for <strong>2 hours</strong>.
        </p>
        <div style="text-align:center;margin:28px 0;">
            <a href="{reset_url}"
               style="display:inline-block;background:#5b67f8;color:white;padding:15px 40px;
                      text-decoration:none;border-radius:50px;font-weight:700;font-size:16px;">
               🔑 Reset My Password
            </a>
        </div>
        <p style="color:#999;font-size:13px;text-align:center;">
            Button not working? Copy and paste this link into your browser:<br>
            <a href="{reset_url}" style="color:#5b67f8;word-break:break-all;">{reset_url}</a>
        </p>
        <p style="color:#bbb;font-size:12px;text-align:center;margin-top:24px;">
            If you didn't request a password reset, you can safely ignore this email.
            Your password will not be changed.
        </p>
        <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
        <p style="color:#999;font-size:12px;text-align:center;">© 2026 DealNotify · <a href="mailto:hello@dealnotify.co" style="color:#5b67f8;">hello@dealnotify.co</a></p>
        </div></body></html>
        """

        message = Mail(
            from_email=from_email,
            to_emails=email,
            subject='🔑 Reset your DealNotify password',
            html_content=html_content
        )
        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        print(f"📧 Password reset email sent to {email} (status {response.status_code})")
        return True
    except Exception as e:
        print(f"❌ Password reset email error: {e}")
        return False


def add_affiliate_tag(product_url):
    """Add Amazon affiliate tag to Amazon product URLs for commission revenue"""
    affiliate_tag = os.getenv('AMAZON_AFFILIATE_TAG', '')
    if not affiliate_tag:
        return product_url
    try:
        from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
        parsed = urlparse(product_url)
        # Check if it's an Amazon domain
        if 'amazon.' not in parsed.netloc.lower():
            return product_url
        # Parse existing query params and add/replace tag
        params = parse_qs(parsed.query)
        params['tag'] = [affiliate_tag]
        new_query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))
    except Exception as e:
        print(f"⚠️ Affiliate tag error (non-fatal): {e}")
        return product_url


def get_share_buttons_html(product_url, store, alert_type='price_drop'):
    """Generate viral share / referral buttons HTML for alert emails"""
    import urllib.parse
    if alert_type == 'price_drop':
        share_text = urllib.parse.quote(f"Just got a price drop alert on {store} from DealNotify! 🎉 Check it out:")
        email_subject = urllib.parse.quote(f"Check out this deal on {store}!")
        email_body = urllib.parse.quote(f"Hey! I just got a price drop alert from DealNotify for this product:\n{product_url}\n\nYou can set your own price alerts at https://www.dealnotify.co — it's free to try!")
    else:
        share_text = urllib.parse.quote(f"An item I was waiting for is back in stock on {store}! 📦 DealNotify alerted me:")
        email_subject = urllib.parse.quote(f"This item is back in stock on {store}!")
        email_body = urllib.parse.quote(f"Hey! I just got a restock alert from DealNotify for this product:\n{product_url}\n\nYou can set your own restock alerts at https://www.dealnotify.co — it's free to try!")

    tweet_url = f"https://twitter.com/intent/tweet?text={share_text}&url={urllib.parse.quote(product_url)}"
    email_share_url = f"mailto:?subject={email_subject}&body={email_body}"
    signup_url = "https://www.dealnotify.co"

    return f"""
        <div style="background: #f8f9ff; border-radius: 10px; padding: 20px; margin: 25px 0; text-align: center;">
        <p style="color: #333; font-size: 15px; font-weight: bold; margin-bottom: 15px;">🔥 Share this deal with friends</p>
        <div style="display: inline-block;">
        <a href="{tweet_url}" style="display: inline-block; background: #1DA1F2; color: white; padding: 10px 20px; text-decoration: none; border-radius: 50px; font-size: 13px; font-weight: bold; margin: 4px;">
        🐦 Tweet this deal
        </a>
        <a href="{email_share_url}" style="display: inline-block; background: #5b67f8; color: white; padding: 10px 20px; text-decoration: none; border-radius: 50px; font-size: 13px; font-weight: bold; margin: 4px;">
        ✉️ Email a friend
        </a>
        </div>
        <p style="color: #888; font-size: 12px; margin-top: 12px; margin-bottom: 0;">
        Know someone who'd love this? <a href="{signup_url}" style="color: #5b67f8; font-weight: bold;">Invite them to DealNotify</a> — it's free to try!
        </p>
        </div>
    """


def send_price_drop_alert(name, email, product_url, current_price, target_price, store, dashboard_url, user_timezone=None):
    """Send price drop alert email via SendGrid"""
    try:
        api_key = os.getenv('SENDGRID_API_KEY')
        from_email = os.getenv('SENDGRID_FROM_EMAIL', 'hello@dealnotify.co')

        if not api_key:
            return False

        savings = float(target_price) - float(current_price)

        # Apply affiliate tag for Amazon links
        buy_url = add_affiliate_tag(product_url)

        # Generate share buttons
        share_buttons = get_share_buttons_html(product_url, store, alert_type='price_drop')

        # Format alert timestamp in the user's local timezone if known, else UTC
        from datetime import timezone as _tz
        now_utc = datetime.now(_tz.utc)
        try:
            import zoneinfo
            tz_obj = zoneinfo.ZoneInfo(user_timezone) if user_timezone else _tz.utc
            now_local = now_utc.astimezone(tz_obj)
            tz_label = now_local.strftime('%Z')  # e.g. "CST", "PDT"
            alert_time_str = now_local.strftime(f'%b %d, %Y at %I:%M %p {tz_label}')
        except Exception:
            alert_time_str = now_utc.strftime('%b %d, %Y at %I:%M %p UTC')

        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
        <div style="background-color: white; max-width: 600px; margin: 0 auto; padding: 30px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">

        <h1 style="color: #27ae60; text-align: center;">🎉 Price Drop Alert, {name}!</h1>

        <div style="background-color: #edfaf1; border: 2px solid #27ae60; border-radius: 10px; padding: 25px; margin: 25px 0; text-align: center;">
        <p style="color: #555; font-size: 14px; margin-bottom: 10px;">A product you're tracking just dropped in price!</p>
        <div style="display: flex; justify-content: center; gap: 30px; margin: 15px 0;">
        <div>
            <div style="font-size: 13px; color: #888;">Price When Detected</div>
            <div style="font-size: 32px; font-weight: bold; color: #27ae60;">${float(current_price):.2f}</div>
        </div>
        <div style="font-size: 30px; color: #ccc; padding-top: 15px;">→</div>
        <div>
            <div style="font-size: 13px; color: #888;">Your Target</div>
            <div style="font-size: 32px; font-weight: bold; color: #667eea;">${float(target_price):.2f}</div>
        </div>
        </div>
        <div style="background: #27ae60; color: white; border-radius: 50px; padding: 8px 20px; display: inline-block; font-weight: bold; margin-top: 10px;">
        🎯 You save ${savings:.2f}!
        </div>
        <p style="color: #888; font-size: 12px; margin-top: 14px; margin-bottom: 0;">
        ⏱ Price detected on {alert_time_str}
        </p>
        </div>

        <div style="background: #fff8e1; border-left: 4px solid #f59e0b; border-radius: 6px; padding: 12px 16px; margin: 0 0 20px 0;">
        <p style="margin: 0; font-size: 13px; color: #78350f;">
        <strong>⚡ Act fast!</strong> Online prices — especially on Amazon — can change within minutes.
        The price shown above was detected by our monitor. If the current price on the retailer's page
        looks different, the deal may have ended or a coupon may be required to reach that price.
        </p>
        </div>

        <div style="text-align: center; margin: 25px 0;">
        <a href="{buy_url}" style="display: inline-block; background: linear-gradient(135deg, #27ae60 0%, #2ecc71 100%); color: white; padding: 15px 40px; text-decoration: none; border-radius: 50px; font-weight: bold; font-size: 16px;">
        🛒 Buy Now on {store}
        </a>
        </div>

        <div style="text-align: center; margin: 15px 0;">
        <a href="{dashboard_url}" style="color: #667eea; font-size: 14px;">View your full dashboard →</a>
        </div>

        {share_buttons}

        <hr style="border: none; border-top: 2px solid #eee; margin: 30px 0;">
        <p style="color: #333; font-size: 14px;">Best regards,<br>
        <strong>🔔 The DealNotify Team</strong><br>
        <a href="mailto:hello@dealnotify.co" style="color: #5b67f8;">hello@dealnotify.co</a> | <a href="https://www.dealnotify.co" style="color: #5b67f8;">www.dealnotify.co</a><br><br>
        💰 <em>Never miss a price drop again!</em>
        </p>
        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
        <p style="color: #999; font-size: 12px; text-align: center;">© 2026 DealNotify. All rights reserved.</p>
        </div>
        </body>
        </html>
        """

        message = Mail(
            from_email=from_email,
            to_emails=email,
            subject=f'🎉 Price Drop Alert! ${float(current_price):.2f} on {store} — DealNotify',
            html_content=html_content
        )

        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        print(f"✅ Price alert sent to {email} (status: {response.status_code})")
        return True

    except Exception as e:
        print(f"❌ Error sending price alert: {str(e)}")
        return False


def send_restock_alert(name, email, product_url, store, dashboard_url, user_timezone=None):
    """Send restock alert email via SendGrid when an item comes back in stock"""
    try:
        api_key = os.getenv('SENDGRID_API_KEY')
        from_email = os.getenv('SENDGRID_FROM_EMAIL', 'hello@dealnotify.co')

        if not api_key:
            return False

        # Apply affiliate tag for Amazon links
        buy_url = add_affiliate_tag(product_url)

        # Generate share buttons
        share_buttons = get_share_buttons_html(product_url, store, alert_type='restock')

        from datetime import timezone as _tz
        now_utc = datetime.now(_tz.utc)
        try:
            import zoneinfo
            tz_obj = zoneinfo.ZoneInfo(user_timezone) if user_timezone else _tz.utc
            now_local = now_utc.astimezone(tz_obj)
            tz_label = now_local.strftime('%Z')
            alert_time_str = now_local.strftime(f'%b %d, %Y at %I:%M %p {tz_label}')
        except Exception:
            alert_time_str = now_utc.strftime('%b %d, %Y at %I:%M %p UTC')

        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
        <div style="background-color: white; max-width: 600px; margin: 0 auto; padding: 30px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">

        <h1 style="color: #5b67f8; text-align: center;">📦 Back in Stock, {name}!</h1>

        <div style="background-color: #eff0fe; border: 2px solid #5b67f8; border-radius: 10px; padding: 25px; margin: 25px 0; text-align: center;">
        <p style="color: #555; font-size: 16px; margin-bottom: 10px;">Great news! A product you were waiting for is <strong>back in stock</strong>!</p>
        <div style="font-size: 48px; margin: 15px 0;">✅</div>
        <div style="background: #5b67f8; color: white; border-radius: 50px; padding: 8px 20px; display: inline-block; font-weight: bold; margin-top: 10px;">
        Available Now on {store}
        </div>
        <p style="color: #888; font-size: 12px; margin-top: 14px; margin-bottom: 0;">
        ⏱ Detected on {alert_time_str}
        </p>
        </div>

        <div style="background: #fff8e1; border-left: 4px solid #f59e0b; border-radius: 6px; padding: 12px 16px; margin: 0 0 20px 0;">
        <p style="margin: 0; font-size: 13px; color: #78350f;">
        <strong>⚡ Act fast!</strong> Popular items sell out quickly. Grab yours before it's gone again!
        </p>
        </div>

        <div style="text-align: center; margin: 25px 0;">
        <a href="{buy_url}" style="display: inline-block; background: linear-gradient(135deg, #5b67f8 0%, #818cf8 100%); color: white; padding: 15px 40px; text-decoration: none; border-radius: 50px; font-weight: bold; font-size: 16px;">
        🛒 Buy Now on {store}
        </a>
        </div>

        <div style="text-align: center; margin: 15px 0;">
        <a href="{dashboard_url}" style="color: #667eea; font-size: 14px;">View your full dashboard →</a>
        </div>

        {share_buttons}

        <hr style="border: none; border-top: 2px solid #eee; margin: 30px 0;">
        <p style="color: #333; font-size: 14px;">Best regards,<br>
        <strong>🔔 The DealNotify Team</strong><br>
        <a href="mailto:hello@dealnotify.co" style="color: #5b67f8;">hello@dealnotify.co</a> | <a href="https://www.dealnotify.co" style="color: #5b67f8;">www.dealnotify.co</a><br><br>
        📦 <em>Never miss a restock again!</em>
        </p>
        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
        <p style="color: #999; font-size: 12px; text-align: center;">© 2026 DealNotify. All rights reserved.</p>
        </div>
        </body>
        </html>
        """

        message = Mail(
            from_email=from_email,
            to_emails=email,
            subject=f'📦 Back in Stock on {store}! — DealNotify',
            html_content=html_content
        )

        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        print(f"✅ Restock alert sent to {email} (status: {response.status_code})")
        return True

    except Exception as e:
        print(f"❌ Error sending restock alert: {str(e)}")
        return False


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/dashboard')
def dashboard():
    return send_from_directory('.', 'dashboard.html')


@app.route('/api/signup', methods=['POST'])
def signup():
    """Handle signup form submission — stores hashed password and sends verification email"""
    try:
        # Rate limit: 5 signups per IP per 15 minutes
        ip = get_client_ip()
        if rate_limiter.is_rate_limited(f'signup:{ip}', max_requests=5, window_seconds=900):
            return jsonify({'error': 'Too many signup attempts. Please wait and try again.'}), 429

        data = request.json

        if not data.get('email') or not data.get('name'):
            return jsonify({'error': 'Email and name are required'}), 400

        password = data.get('password', '').strip()
        if password and len(password) < 8:
            return jsonify({'error': 'Password must be at least 8 characters'}), 400

        # Check if email already exists
        existing = get_user_by_email(data['email'])
        if existing:
            return jsonify({'error': 'An account with that email already exists'}), 400

        token              = secrets.token_urlsafe(32)
        verification_token = secrets.token_urlsafe(32)
        password_hash      = generate_password_hash(password) if password else None
        newsletter         = bool(data.get('newsletter', True))
        phone              = (data.get('phone') or '').strip() or None
        dashboard_url      = f"{get_base_url()}/dashboard?token={token}"

        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO users (name, email, token, signup_date, status, trial_days_remaining,
                                   password_hash, email_verified, verification_token, newsletter, phone)
                VALUES (%s, %s, %s, %s, 'active', 7, %s, FALSE, %s, %s, %s)
                RETURNING id
            """, (data['name'], data['email'], token, datetime.now(),
                  password_hash, verification_token, newsletter, phone))
            user_id = _fetchone(cur)['id']

            # Add first product if provided
            if data.get('product_url'):
                cur.execute("""
                    INSERT INTO products (user_id, url, target_price, store, added_date, status, current_price, alert_sent)
                    VALUES (%s, %s, %s, %s, %s, 'monitoring', NULL, FALSE)
                """, (
                    user_id,
                    data['product_url'],
                    data.get('target_price') or None,
                    get_store_name(data['product_url']),
                    datetime.now()
                ))

            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

        print(f"\n✅ NEW SIGNUP! {data['name']} / {data['email']}")

        # Send verification email (primary) + welcome email
        base_url = get_base_url()
        verify_url = f"{base_url}/api/verify-email?token={verification_token}"
        send_verification_email(data['name'], data['email'], verify_url)
        send_welcome_email(data['name'], data['email'], dashboard_url)

        return jsonify({
            'success': True,
            'message': 'Account created! Please check your email to verify your address.',
            'dashboard_url': dashboard_url,
            'token': token
        }), 200

    except Exception as e:
        print(f"❌ Signup error: {str(e)}")
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500


@app.route('/api/verify-email', methods=['GET'])
def verify_email():
    """Verify a user's email address via the link sent on signup"""
    vtok = request.args.get('token')
    if not vtok:
        return '<h2>❌ Missing verification token.</h2>', 400

    conn = get_db_conn()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT id, name, token FROM users WHERE verification_token = %s", (vtok,))
        user = _fetchone(cur)
        if not user:
            return '<h2>❌ This verification link is invalid or has already been used.</h2>', 404

        cur.execute("""
            UPDATE users SET email_verified = TRUE, verification_token = NULL
            WHERE id = %s
        """, (user['id'],))
        conn.commit()
        base_url = get_base_url()
        # Redirect to the homepage with a ?verified=1 flag so we can show a toast
        from flask import redirect
        return redirect(f"{base_url}/?verified=1")
    except Exception as e:
        conn.rollback()
        return f'<h2>❌ Verification error: {e}</h2>', 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/resend-verification', methods=['POST'])
def resend_verification():
    """Resend the email verification link to an unverified user"""
    try:
        data  = request.json
        email = (data.get('email') or '').strip().lower()

        if not email:
            return jsonify({'error': 'Email is required'}), 400

        conn = get_db_conn()
        cur  = conn.cursor()
        try:
            cur.execute("SELECT id, name, email, email_verified FROM users WHERE LOWER(email) = %s", (email,))
            user = _fetchone(cur)

            if not user:
                # Don't reveal whether the email exists
                return jsonify({'success': True}), 200

            if user.get('email_verified'):
                return jsonify({'success': True, 'message': 'Email is already verified'}), 200

            # Generate a fresh verification token
            new_token = secrets.token_urlsafe(32)
            cur.execute("UPDATE users SET verification_token = %s WHERE id = %s", (new_token, user['id']))
            conn.commit()
        finally:
            cur.close()
            conn.close()

        base_url   = get_base_url()
        verify_url = f"{base_url}/api/verify-email?token={new_token}"
        send_verification_email(user['name'], user['email'], verify_url)

        return jsonify({'success': True}), 200

    except Exception as e:
        print(f"❌ Resend verification error: {e}")
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500


@app.route('/api/login', methods=['POST'])
def login():
    """Login with email + password. Returns dashboard URL on success."""
    try:
        # Rate limit: 10 login attempts per IP per 5 minutes
        ip = get_client_ip()
        if rate_limiter.is_rate_limited(f'login:{ip}', max_requests=10, window_seconds=300):
            return jsonify({'error': 'Too many login attempts. Please wait a few minutes and try again.'}), 429

        data     = request.json
        email    = (data.get('email') or '').strip().lower()
        password = data.get('password', '')

        if not email or not password:
            return jsonify({'error': 'Email and password are required'}), 400

        conn = get_db_conn()
        cur  = conn.cursor()
        try:
            cur.execute("SELECT * FROM users WHERE LOWER(email) = %s", (email,))
            user = _fetchone(cur)
        finally:
            cur.close()
            conn.close()

        if not user:
            # Generic message — don't reveal whether email exists
            return jsonify({'error': 'Incorrect email or password'}), 401

        pw_hash = user.get('password_hash')
        if not pw_hash:
            # Legacy account — no password set yet, send a reset link
            return jsonify({
                'error': 'This account has no password set. '
                         'Please use "Forgot password" to create one.'
            }), 401

        if not check_password_hash(pw_hash, password):
            return jsonify({'error': 'Incorrect email or password'}), 401

        # Block login until email is verified
        if not user.get('email_verified'):
            return jsonify({
                'error': 'Please verify your email before logging in. '
                         'Check your inbox for the verification link.',
                'unverified': True
            }), 403

        base_url      = get_base_url()
        dashboard_url = f"{base_url}/dashboard?token={user['token']}"

        print(f"✅ Login: {email}")
        return jsonify({
            'success': True,
            'dashboard_url': dashboard_url,
            'token': user['token']
        }), 200

    except Exception as e:
        print(f"❌ Login error: {e}")
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500


@app.route('/api/forgot-password', methods=['POST'])
def forgot_password():
    """Generate a one-time password reset token and email it to the user"""
    try:
        # Rate limit: 5 password reset attempts per IP per 15 minutes
        ip = get_client_ip()
        if rate_limiter.is_rate_limited(f'forgot:{ip}', max_requests=5, window_seconds=900):
            return jsonify({'error': 'Too many reset attempts. Please wait and try again.'}), 429

        data  = request.json
        email = (data.get('email') or '').strip().lower()

        if not email:
            return jsonify({'error': 'Email is required'}), 400

        # Always respond with the same message for security (don't reveal if email exists)
        generic_ok = jsonify({'success': True,
                              'message': 'If that email is registered you will receive a reset link shortly.'}), 200

        conn = get_db_conn()
        cur  = conn.cursor()
        try:
            cur.execute("SELECT id, name, email FROM users WHERE LOWER(email) = %s", (email,))
            user = _fetchone(cur)
            if not user:
                return generic_ok

            reset_token  = secrets.token_urlsafe(32)
            token_expiry = datetime.now() + timedelta(hours=2)

            cur.execute("""
                UPDATE users SET reset_token = %s, reset_token_expiry = %s WHERE id = %s
            """, (reset_token, token_expiry, user['id']))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

        base_url  = get_base_url()
        reset_url = f"{base_url}/?reset_token={reset_token}"
        send_password_reset_email(user['name'], user['email'], reset_url)

        print(f"📧 Password reset requested: {email}")
        return generic_ok

    except Exception as e:
        print(f"❌ Forgot password error: {e}")
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500


@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    """Consume a reset token and update the user's password"""
    try:
        data     = request.json
        rtok     = data.get('token', '').strip()
        password = data.get('password', '')

        if not rtok or not password:
            return jsonify({'error': 'Token and new password are required'}), 400
        if len(password) < 8:
            return jsonify({'error': 'Password must be at least 8 characters'}), 400

        conn = get_db_conn()
        cur  = conn.cursor()
        try:
            cur.execute("""
                SELECT id, reset_token_expiry FROM users
                WHERE reset_token = %s
            """, (rtok,))
            user = _fetchone(cur)

            if not user:
                return jsonify({'error': 'Invalid or expired reset link. Please request a new one.'}), 400

            expiry = user.get('reset_token_expiry')
            if expiry and datetime.now() > expiry:
                return jsonify({'error': 'This reset link has expired. Please request a new one.'}), 400

            new_hash = generate_password_hash(password)
            cur.execute("""
                UPDATE users SET password_hash = %s, reset_token = NULL, reset_token_expiry = NULL,
                                 email_verified = TRUE
                WHERE id = %s
            """, (new_hash, user['id']))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

        print(f"✅ Password reset successful for user id {user['id']}")
        return jsonify({'success': True, 'message': 'Password updated successfully'}), 200

    except Exception as e:
        print(f"❌ Reset password error: {e}")
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500


@app.route('/api/price-history/<int:product_id>', methods=['GET'])
def get_price_history(product_id):
    """Return price history for a product. Token required — user must own the product."""
    token = get_token_from_request()
    if not token:
        return jsonify({'error': 'Token required'}), 400

    conn = get_db_conn()
    cur  = conn.cursor()
    try:
        # Verify the product belongs to this user
        cur.execute("""
            SELECT p.id FROM products p
            JOIN users u ON u.id = p.user_id
            WHERE p.id = %s AND u.token = %s
        """, (product_id, token))
        if not _fetchone(cur):
            return jsonify({'error': 'Product not found'}), 404

        # Return up to 90 days of history, newest first then reversed for charting
        cur.execute("""
            SELECT price, checked_at
            FROM price_history
            WHERE product_id = %s
              AND checked_at >= NOW() - INTERVAL '90 days'
            ORDER BY checked_at ASC
        """, (product_id,))
        rows = _fetchall(cur)

        history = [
            {
                'price': float(r['price']),
                'checked_at': (r['checked_at'].isoformat() + 'Z') if hasattr(r['checked_at'], 'isoformat') else r['checked_at']
            }
            for r in rows
        ]
        return jsonify({'success': True, 'history': history}), 200

    except Exception as e:
        print(f"❌ price-history error: {e}")
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/dashboard', methods=['GET'])
def get_dashboard():
    """Get dashboard data for a user by token"""
    token = get_token_from_request()
    if not token:
        return jsonify({'error': 'Token required'}), 400

    user, products = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid or expired token'}), 404

    signup_date = user['signup_date']
    if isinstance(signup_date, str):
        signup_date = datetime.fromisoformat(signup_date)
    days_elapsed = (datetime.now() - signup_date).days
    trial_days_remaining = max(0, 30 - days_elapsed)

    is_pro = bool(user.get('is_pro'))

    return jsonify({
        'success': True,
        'user': {
            'name': user['name'],
            'email': user['email'],
            'phone': user.get('phone') or '',
            'newsletter': user.get('newsletter', True),
            'timezone': user.get('timezone') or '',
            'signup_date': signup_date.strftime('%Y-%m-%d'),
            'trial_days_remaining': trial_days_remaining,
            'status': user['status'],
            'is_pro': is_pro          # explicit boolean — use this to gate Pro features
        },
        'products': [product_to_dict(p) for p in products]
    }), 200


@app.route('/api/update-account', methods=['POST'])
def update_account():
    """Update user's profile details: name, phone, newsletter preference"""
    try:
        token = get_token_from_request()
        if not token:
            return jsonify({'error': 'Token required'}), 400

        data = request.json
        name       = (data.get('name') or '').strip()
        phone      = (data.get('phone') or '').strip()
        newsletter = bool(data.get('newsletter', True))

        if not name:
            return jsonify({'error': 'Name cannot be empty'}), 400

        user, _ = get_user_by_token(token)
        if not user:
            return jsonify({'error': 'Invalid token'}), 404

        conn = get_db_conn()
        cur  = conn.cursor()
        try:
            cur.execute("""
                UPDATE users SET name = %s, phone = %s, newsletter = %s WHERE token = %s
            """, (name, phone or None, newsletter, token))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

        print(f"✅ Account updated: {user['email']} — name={name}, newsletter={newsletter}")
        return jsonify({'success': True}), 200

    except Exception as e:
        print(f"❌ Update account error: {e}")
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500


FREE_TIER_PRODUCT_LIMIT = 3   # Free/trial users can track up to this many products

@app.route('/api/add-product', methods=['POST'])
def add_product_to_dashboard():
    """Add a new product to user's tracking list"""
    try:
        token = get_token_from_request()
        if not token:
            return jsonify({'error': 'Token required'}), 400

        data = request.json
        if not data.get('url'):
            return jsonify({'error': 'Product URL is required'}), 400

        user, existing_products = get_user_by_token(token)
        if not user:
            return jsonify({'error': 'Invalid token'}), 404

        # Enforce product limit for free/trial users
        if user.get('status') in ('active', 'trial'):
            if len(existing_products) >= FREE_TIER_PRODUCT_LIMIT:
                return jsonify({
                    'error': 'free_limit_reached',
                    'message': f'Free plan is limited to {FREE_TIER_PRODUCT_LIMIT} products. Upgrade to Pro for unlimited tracking!'
                }), 403

        conn = get_db_conn()
        cur = conn.cursor()
        try:
            track_type = data.get('track_type', 'price')  # 'price' or 'restock'
            if track_type not in ('price', 'restock'):
                track_type = 'price'
            cur.execute("""
                INSERT INTO products (user_id, url, target_price, store, added_date, status, current_price, alert_sent, track_type)
                VALUES (%s, %s, %s, %s, %s, 'monitoring', NULL, FALSE, %s)
                RETURNING *
            """, (
                user['id'],
                data['url'],
                data.get('target_price') or None,
                get_store_name(data['url']),
                datetime.now(),
                track_type
            ))
            new_product = _fetchone(cur)
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

        print(f"✅ Product added for {user['email']}: {data['url']}")

        return jsonify({
            'success': True,
            'message': 'Product added successfully!',
            'product': product_to_dict(new_product)
        }), 200

    except Exception as e:
        print(f"❌ Add product error: {str(e)}")
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500


@app.route('/api/remove-product', methods=['DELETE'])
def remove_product():
    """Remove a product from user's tracking list"""
    try:
        token = get_token_from_request()
        product_id = request.args.get('product_id')

        if not token or not product_id:
            return jsonify({'error': 'Token and product_id required'}), 400

        user, _ = get_user_by_token(token)
        if not user:
            return jsonify({'error': 'Invalid token'}), 404

        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                DELETE FROM products WHERE id = %s AND user_id = %s
            """, (product_id, user['id']))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

        return jsonify({'success': True, 'message': 'Product removed'}), 200

    except Exception as e:
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500


@app.route('/api/update-timezone', methods=['POST'])
def update_timezone():
    """Store the user's browser timezone (e.g. 'America/Chicago') so alert emails show local time."""
    try:
        token = get_token_from_request() or (request.get_json(silent=True) or {}).get('token')
        tz = (request.get_json(silent=True) or {}).get('timezone', '').strip()
        if not token or not tz:
            return jsonify({'error': 'token and timezone required'}), 400
        user, _ = get_user_by_token(token)
        if not user:
            return jsonify({'error': 'invalid token'}), 401
        # Basic validation — IANA timezone strings contain '/' or are 3-letter codes
        import zoneinfo
        try:
            zoneinfo.ZoneInfo(tz)
        except Exception:
            return jsonify({'error': 'invalid timezone'}), 400
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET timezone = %s WHERE id = %s", (tz, user['id']))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        print(f"update_timezone error: {e}")
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500


@app.route('/api/update-target-price', methods=['POST'])
def update_target_price():
    """Update the target price for a specific product"""
    try:
        token = get_token_from_request()
        if not token:
            return jsonify({'error': 'Token required'}), 400

        user, _ = get_user_by_token(token)
        if not user:
            return jsonify({'error': 'Invalid token'}), 401

        data = request.get_json() or {}
        product_id = data.get('product_id')
        new_price = data.get('target_price')

        if not product_id:
            return jsonify({'error': 'product_id required'}), 400
        try:
            new_price = float(new_price)
            if new_price <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            return jsonify({'error': 'Invalid target price'}), 400

        conn = get_db_conn()
        cur = conn.cursor()
        try:
            # Verify the product belongs to this user
            cur.execute("SELECT id FROM products WHERE id = %s AND user_id = %s", (product_id, user['id']))
            if not _fetchone(cur):
                return jsonify({'error': 'Product not found'}), 404

            cur.execute(
                "UPDATE products SET target_price = %s WHERE id = %s AND user_id = %s",
                (new_price, product_id, user['id'])
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

        return jsonify({'success': True, 'target_price': new_price}), 200

    except Exception as e:
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500


@app.route('/api/send-test-alert', methods=['POST', 'GET'])
def send_test_alert():
    """Send a real demo alert email to the authenticated user's inbox.
    Useful for recording demo videos and testing email appearance.

    Usage:
        GET/POST /api/send-test-alert?token=USER_TOKEN&type=price_drop
        GET/POST /api/send-test-alert?token=USER_TOKEN&type=restock

    Optional query params to customise demo data:
        product_url  — URL shown in the email (default: Amazon Sony headphones)
        store        — Store name shown (default: Amazon)
        current_price — Detected price shown (price_drop only, default: 63.99)
        target_price  — User's target price shown (price_drop only, default: 89.00)
    """
    token = get_token_from_request()
    if not token:
        return jsonify({'error': 'Token required'}), 400

    user, _ = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid token'}), 404

    alert_type = request.args.get('type', 'price_drop').strip().lower()
    if alert_type not in ('price_drop', 'restock'):
        return jsonify({'error': 'type must be "price_drop" or "restock"'}), 400

    base_url = get_base_url()
    dashboard_url = f"{base_url}/dashboard?token={token}"

    # Demo product data — override via query params if desired
    product_url   = request.args.get('product_url',   'https://www.amazon.com/dp/B0CH7GXKLT')
    store         = request.args.get('store',          'Amazon')
    current_price = request.args.get('current_price',  '63.99')
    target_price  = request.args.get('target_price',   '89.00')

    try:
        if alert_type == 'price_drop':
            sent = send_price_drop_alert(
                name=user['name'],
                email=user['email'],
                product_url=product_url,
                current_price=float(current_price),
                target_price=float(target_price),
                store=store,
                dashboard_url=dashboard_url,
                user_timezone=user.get('timezone')
            )
            if sent:
                print(f"🧪 Test price-drop alert sent to {user['email']}")
                return jsonify({
                    'success': True,
                    'message': f'Price drop test email sent to {user["email"]}',
                    'details': {
                        'type': 'price_drop',
                        'store': store,
                        'current_price': current_price,
                        'target_price': target_price,
                        'product_url': product_url
                    }
                }), 200
            else:
                return jsonify({'error': 'Failed to send email — check SendGrid config'}), 500

        elif alert_type == 'restock':
            # Default to Walmart PS5 controller for restock demo
            if 'product_url' not in request.args:
                product_url = 'https://www.walmart.com/ip/Sony-PlayStation-5-DualSense-Wireless-Controller/493789019'
            if 'store' not in request.args:
                store = 'Walmart'

            sent = send_restock_alert(
                name=user['name'],
                email=user['email'],
                product_url=product_url,
                store=store,
                dashboard_url=dashboard_url,
                user_timezone=user.get('timezone')
            )
            if sent:
                print(f"🧪 Test restock alert sent to {user['email']}")
                return jsonify({
                    'success': True,
                    'message': f'Restock test email sent to {user["email"]}',
                    'details': {
                        'type': 'restock',
                        'store': store,
                        'product_url': product_url
                    }
                }), 200
            else:
                return jsonify({'error': 'Failed to send email — check SendGrid config'}), 500

    except Exception as e:
        print(f"❌ send-test-alert error: {e}")
        return jsonify({'error': 'An unexpected error occurred'}), 500


@app.route('/api/test-scrape', methods=['GET'])
def test_scrape():
    """Debug endpoint — scrape a single URL and return every price signal found.
       Usage: /api/test-scrape?url=https://...&password=ADMIN_PASSWORD
    """
    import re, io, sys

    # Require admin authentication
    if not require_admin():
        return jsonify({'error': 'Unauthorized'}), 403

    url = request.args.get('url', '').strip()
    if not url:
        return jsonify({'error': 'url parameter required'}), 400

    # SSRF protection: only allow HTTP(S) URLs to known retailer domains
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return jsonify({'error': 'Only HTTP/HTTPS URLs allowed'}), 400
    except Exception:
        return jsonify({'error': 'Invalid URL'}), 400

    from price_monitor import clean_url, _init_firecrawl, _do_scrape, _extract_amazon_price, _extract_meta_price, _extract_jsonld_blocks

    cleaned = clean_url(url)
    result = {
        'original_url': url,
        'cleaned_url': cleaned,
        'is_amazon': bool(re.search(r'amazon\.(com|co\.uk|ca|com\.au|de|fr|it|es)', cleaned, re.IGNORECASE)),
    }

    # Capture all print() output from scraping
    old_stdout = sys.stdout
    sys.stdout = captured = io.StringIO()

    try:
        api_key = os.getenv('FIRECRAWL_API_KEY')
        if not api_key:
            result['error'] = 'FIRECRAWL_API_KEY not set'
            return jsonify(result), 500

        fc, api_version = _init_firecrawl(api_key)
        result['firecrawl_version'] = api_version

        markdown, html = _do_scrape(fc, api_version, cleaned)
        result['html_length'] = len(html)
        result['markdown_length'] = len(markdown)

        # Show what each extraction method finds
        signals = {}

        # Meta tag
        meta_price = _extract_meta_price(html) if html else None
        signals['meta_tag'] = meta_price

        # JSON-LD
        jsonld = _extract_jsonld_blocks(html) if html else ''
        jsonld_prices = []
        for m in re.finditer(r'"priceCurrency"\s*:\s*"USD"', jsonld + (html or ''), re.IGNORECASE):
            window = (jsonld + (html or ''))[max(0, m.start()-600): m.end()+600]
            for pat in (r'"price"\s*:\s*"([\d,]+\.?\d*)"', r'"price"\s*:\s*([\d,]+\.?\d*)'):
                pm = re.search(pat, window)
                if pm:
                    try:
                        p = float(pm.group(1).replace(',', ''))
                        if 0.5 < p < 100000:
                            jsonld_prices.append(p)
                    except: pass
                    break
        signals['jsonld_priceCurrency'] = jsonld_prices if jsonld_prices else None

        # Amazon-specific fields (scan even if not Amazon, for debugging)
        def find_amount(key):
            m = re.search(rf'"{key}"\s*:\s*\{{', html or '')
            if not m: return None
            snippet = html[m.start(): m.start()+400]
            am = re.search(r'"amount"\s*:\s*"?([\d,]+\.?\d*)"?', snippet)
            if am:
                try: return float(am.group(1).replace(',',''))
                except: pass
            return None

        signals['amazon_basisPrice'] = find_amount('basisPrice')
        signals['amazon_listPrice'] = find_amount('listPrice')
        signals['amazon_priceToPay'] = find_amount('priceToPay')

        def find_str(key):
            m = re.search(rf'"{key}"\s*:\s*"\$?([\d,]+\.?\d*)"', html or '')
            if m:
                try: return float(m.group(1).replace(',',''))
                except: pass
            return None

        signals['amazon_priceAmount'] = find_str('priceAmount')
        signals['amazon_displayPrice'] = find_str('displayPrice')
        signals['amazon_ourPrice'] = find_str('ourPrice')
        signals['amazon_buyingPrice'] = find_str('buyingPrice')

        # Coupon detection
        signals['coupon_keywords_found'] = bool(re.search(
            r'coupon|clip\s+coupon|save\s+with\s+coupon|couponBadge|promotionBadge',
            html or '', re.IGNORECASE
        ))

        result['price_signals'] = signals

        # Now run the actual scraper to see what it returns
        from price_monitor import scrape_price
        final_price = scrape_price(url)
        result['final_scrape_price'] = final_price

        # Stock status detection
        stock_result = extract_stock_status(html, markdown, url=cleaned)
        result['stock_status'] = stock_result['status']
        result['stock_detail'] = stock_result['detail']

    except Exception as e:
        result['error'] = str(e)
    finally:
        sys.stdout = old_stdout
        result['scraper_logs'] = captured.getvalue()

    return jsonify(result), 200


@app.route('/api/check-prices', methods=['GET'])
def check_prices_for_user():
    """Check current prices for all of a user's products"""
    token = get_token_from_request()
    if not token:
        return jsonify({'error': 'Token required'}), 400

    user, products = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid token'}), 404

    base_url = get_base_url()
    dashboard_url = f"{base_url}/dashboard?token={token}"

    updated_products = []
    alerts_sent = 0

    conn = get_db_conn()
    cur = conn.cursor()
    try:
        for product in products:
            url = product.get('url')
            if not url:
                updated_products.append(product_to_dict(product))
                continue

            track_type = product.get('track_type', 'price')
            print(f"🔍 Checking [{track_type}] for: {url}")
            scrape_result = scrape_stock_status(url)
            current_price = scrape_result.get('price')
            new_stock_status = scrape_result.get('stock_status', 'unknown')
            stock_detail = scrape_result.get('stock_detail', '')
            old_stock_status = product.get('last_stock_status') or product.get('stock_status')
            print(f"   → price: {current_price}, stock: {new_stock_status}")

            alert_sent = False
            restock_alert_sent = product.get('restock_alert_sent', False)

            # Price-drop alert logic
            if track_type == 'price' and current_price is not None:
                target = product.get('target_price')
                if target and float(current_price) <= float(target):
                    alert_sent = True
                    send_price_drop_alert(
                        name=user['name'],
                        email=user['email'],
                        product_url=url,
                        current_price=current_price,
                        target_price=target,
                        store=product.get('store', 'the store'),
                        dashboard_url=dashboard_url,
                        user_timezone=user.get('timezone')
                    )
                    alerts_sent += 1
                    print(f"🔔 Price alert sent for {user['email']} - ${current_price} <= target ${target}")
                    try:
                        cur.execute("""
                            INSERT INTO alerts_log (user_id, product_id, product_url, store, price_at_alert, target_price, alert_type)
                            VALUES (%s, %s, %s, %s, %s, %s, 'price_drop')
                        """, (user['id'], product['id'], url, product.get('store'), current_price, target))
                    except Exception as log_err:
                        print(f"⚠️ alerts_log insert error (non-fatal): {log_err}")

            # Restock alert logic
            if track_type == 'restock' and new_stock_status == 'in_stock':
                if old_stock_status in ('out_of_stock', None, '') and not restock_alert_sent:
                    restock_alert_sent = True
                    send_restock_alert(
                        name=user['name'],
                        email=user['email'],
                        product_url=url,
                        store=product.get('store', 'the store'),
                        dashboard_url=dashboard_url,
                        user_timezone=user.get('timezone')
                    )
                    alerts_sent += 1
                    print(f"🔔 Restock alert sent for {user['email']} - {old_stock_status} → in_stock!")
                    try:
                        cur.execute("""
                            INSERT INTO alerts_log (user_id, product_id, product_url, store, price_at_alert, target_price, alert_type)
                            VALUES (%s, %s, %s, %s, %s, %s, 'restock')
                        """, (user['id'], product['id'], url, product.get('store'), current_price, product.get('target_price')))
                    except Exception as log_err:
                        print(f"⚠️ alerts_log insert error (non-fatal): {log_err}")

            if track_type == 'restock' and new_stock_status == 'out_of_stock':
                restock_alert_sent = False

            cur.execute("""
                UPDATE products
                SET current_price      = COALESCE(%s, current_price),
                    last_checked       = %s,
                    status             = %s,
                    alert_sent         = %s,
                    stock_status       = %s,
                    last_stock_status  = %s,
                    stock_detail       = %s,
                    restock_alert_sent = %s
                WHERE id = %s AND user_id = %s
            """, (
                current_price,
                datetime.now(),
                'alert_sent' if (alert_sent or restock_alert_sent) else 'monitoring',
                alert_sent,
                new_stock_status,
                product.get('stock_status'),
                stock_detail,
                restock_alert_sent,
                product['id'],
                user['id']
            ))

            product['current_price'] = current_price if current_price is not None else product.get('current_price')
            product['last_checked'] = datetime.now().isoformat()
            product['stock_status'] = new_stock_status
            product['stock_detail'] = stock_detail
            product['restock_alert_sent'] = restock_alert_sent
            if alert_sent:
                product['status'] = 'alert_sent'
                product['alert_sent'] = True

            if current_price is not None:
                log_price_history(product['id'], current_price)

            updated_products.append(product_to_dict(product))

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"❌ check-prices error: {e}")
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500
    finally:
        cur.close()
        conn.close()

    return jsonify({
        'success': True,
        'products': updated_products,
        'alerts_sent': alerts_sent,
        'checked_at': datetime.now().isoformat()
    }), 200


@app.route('/api/contact', methods=['POST'])
def contact():
    """Handle contact form submissions"""
    try:
        # Rate limit: 3 contact form submissions per IP per 15 minutes
        ip = get_client_ip()
        if rate_limiter.is_rate_limited(f'contact:{ip}', max_requests=3, window_seconds=900):
            return jsonify({'error': 'Too many messages. Please wait before sending another.'}), 429

        data = request.json
        name = data.get('name', '').strip()
        email = data.get('email', '').strip()
        message = data.get('message', '').strip()

        if not name or not email or not message:
            return jsonify({'error': 'All fields are required'}), 400

        api_key = os.getenv('SENDGRID_API_KEY')
        from_email = os.getenv('SENDGRID_FROM_EMAIL', 'hello@dealnotify.co')

        if not api_key:
            return jsonify({'error': 'Email service not configured'}), 500

        # HTML-escape user inputs to prevent HTML/script injection in emails
        safe_name    = html_module.escape(name)
        safe_email   = html_module.escape(email)
        safe_message = html_module.escape(message)

        html_content = f"""
        <html><body style="font-family: Arial, sans-serif; padding: 20px;">
        <h2 style="color: #667eea;">📬 New Contact Form Submission</h2>
        <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
        <tr><td style="padding: 10px; font-weight: bold; color: #555;">Name:</td><td style="padding: 10px;">{safe_name}</td></tr>
        <tr style="background:#f9f9f9;"><td style="padding: 10px; font-weight: bold; color: #555;">Email:</td><td style="padding: 10px;"><a href="mailto:{safe_email}">{safe_email}</a></td></tr>
        <tr><td style="padding: 10px; font-weight: bold; color: #555;">Message:</td><td style="padding: 10px;">{safe_message}</td></tr>
        </table>
        <p style="color: #888; font-size: 12px; margin-top: 20px;">Sent from DealNotify Contact Form</p>
        </body></html>
        """

        msg = Mail(
            from_email=from_email,
            to_emails='hello@dealnotify.co',
            subject=f'📬 DealNotify Contact: Message from {name}',
            html_content=html_content
        )

        sg = SendGridAPIClient(api_key)
        sg.send(msg)

        confirm_html = f"""
        <html><body style="font-family: Arial, sans-serif; background: #f5f5f5; padding: 20px;">
        <div style="background: white; max-width: 600px; margin: 0 auto; padding: 30px; border-radius: 10px;">
        <h2 style="color: #667eea;">✅ We got your message, {safe_name}!</h2>
        <p style="color: #333;">Thank you for reaching out. Our team will get back to you within <strong>24 hours</strong>.</p>
        <div style="background: #f9f9f9; padding: 15px; border-radius: 8px; margin: 20px 0;">
        <p style="color: #666; font-size: 14px; margin: 0;"><strong>Your message:</strong><br>{safe_message}</p>
        </div>
        <p style="color: #333; font-size: 14px;">Best regards,<br><strong>🔔 The DealNotify Team</strong><br>
        <a href="mailto:hello@dealnotify.co" style="color: #5b67f8;">hello@dealnotify.co</a> | <a href="https://www.dealnotify.co" style="color: #5b67f8;">www.dealnotify.co</a><br><br>
        💰 <em>Never miss a price drop again!</em></p>
        </div></body></html>
        """

        confirm_msg = Mail(
            from_email=from_email,
            to_emails=email,
            subject='✅ We received your message — DealNotify Support',
            html_content=confirm_html
        )
        sg.send(confirm_msg)

        print(f"📬 Contact form: {name} ({email})")
        return jsonify({'success': True}), 200

    except Exception as e:
        print(f"❌ Contact error: {str(e)}")
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500


@app.route('/api/signups', methods=['GET'])
def get_signups():
    """Get all signups (admin only)"""
    if not require_admin():
        return jsonify({'error': 'Unauthorized'}), 403

    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM users ORDER BY signup_date DESC")
        users = _fetchall(cur)
        result = []
        for u in users:
            cur.execute("SELECT * FROM products WHERE user_id = %s ORDER BY added_date ASC", (u['id'],))
            products = _fetchall(cur)
            result.append(user_to_dict(u, products))
        return jsonify({'signups': result})
    finally:
        cur.close()
        conn.close()


@app.route('/api/alerts-log', methods=['GET'])
def get_alerts_log():
    """Return alert stats and recent alerts (admin endpoint)"""
    if not require_admin():
        return jsonify({'error': 'Unauthorized'}), 403

    conn = get_db_conn()
    cur  = conn.cursor()
    try:
        # Total alerts ever
        cur.execute("SELECT COUNT(*) AS cnt FROM alerts_log")
        total = _fetchone(cur)['cnt']

        # Alerts in last 7 days
        cur.execute("SELECT COUNT(*) AS cnt FROM alerts_log WHERE sent_at >= NOW() - INTERVAL '7 days'")
        last_7d = _fetchone(cur)['cnt']

        # Alerts in last 24 hours
        cur.execute("SELECT COUNT(*) AS cnt FROM alerts_log WHERE sent_at >= NOW() - INTERVAL '1 day'")
        last_24h = _fetchone(cur)['cnt']

        # Unique users alerted
        cur.execute("SELECT COUNT(DISTINCT user_id) AS cnt FROM alerts_log")
        unique_users = _fetchone(cur)['cnt']

        # Recent 50 alerts with user email
        cur.execute("""
            SELECT a.id, a.product_url, a.store, a.price_at_alert, a.target_price,
                   a.sent_at, u.name, u.email
            FROM alerts_log a
            LEFT JOIN users u ON u.id = a.user_id
            ORDER BY a.sent_at DESC
            LIMIT 50
        """)
        recent = _fetchall(cur)
        alerts = [{
            'id': r['id'],
            'user_name': r.get('name', ''),
            'user_email': r.get('email', ''),
            'product_url': r['product_url'],
            'store': r.get('store', ''),
            'price_at_alert': float(r['price_at_alert']) if r['price_at_alert'] else None,
            'target_price': float(r['target_price']) if r['target_price'] else None,
            'sent_at': r['sent_at'].isoformat() if hasattr(r['sent_at'], 'isoformat') else r['sent_at']
        } for r in recent]

        return jsonify({
            'success': True,
            'stats': {
                'total': total,
                'last_7_days': last_7d,
                'last_24_hours': last_24h,
                'unique_users_alerted': unique_users
            },
            'recent_alerts': alerts
        }), 200
    except Exception as e:
        print(f"❌ alerts-log error: {e}")
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/user-check-history', methods=['GET'])
def user_check_history():
    """Admin: return all price check timestamps for a given user email.
    Usage: /api/user-check-history?email=foo@bar.com (admin auth required)
    """
    if not require_admin():
        return jsonify({'error': 'Unauthorized'}), 403

    email = request.args.get('email', '').strip().lower()
    if not email:
        return jsonify({'error': 'email param required'}), 400

    conn = get_db_conn()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT id, name FROM users WHERE LOWER(email) = %s", (email,))
        user = _fetchone(cur)
        if not user:
            return jsonify({'error': f'No user found for {email}'}), 404

        cur.execute("""
            SELECT ph.checked_at, ph.price, p.url, p.store
            FROM price_history ph
            JOIN products p ON p.id = ph.product_id
            WHERE p.user_id = %s
            ORDER BY ph.checked_at DESC
            LIMIT 200
        """, (user['id'],))
        rows = _fetchall(cur)

        return jsonify({
            'user': user['name'],
            'email': email,
            'total_checks': len(rows),
            'checks': [{
                'checked_at': str(r['checked_at']),
                'price': str(r['price']),
                'store': r['store'],
                'url': r['url']
            } for r in rows]
        }), 200
    except Exception as e:
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/admin')
def admin():
    """Admin dashboard — restricted to ADMIN_EMAILS or ADMIN_PASSWORD env var"""
    authorized = False

    # Use the centralized admin auth helper (constant-time comparison, header support)
    authorized = require_admin()

    if not authorized:
        return "<h1>403 Forbidden</h1><p>Access denied.</p>", 403

    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) as cnt FROM users")
        total_users = _fetchone(cur)['cnt']

        cur.execute("SELECT COUNT(*) as cnt FROM products")
        total_products = _fetchone(cur)['cnt']

        # Alert stats
        cur.execute("SELECT COUNT(*) as cnt FROM alerts_log")
        total_alerts = _fetchone(cur)['cnt']

        cur.execute("SELECT COUNT(*) as cnt FROM alerts_log WHERE sent_at >= NOW() - INTERVAL '7 days'")
        alerts_7d = _fetchone(cur)['cnt']

        # Recent 20 alerts
        cur.execute("""
            SELECT a.product_url, a.store, a.price_at_alert, a.target_price,
                   a.sent_at, u.name AS user_name, u.email AS user_email
            FROM alerts_log a
            LEFT JOIN users u ON u.id = a.user_id
            ORDER BY a.sent_at DESC LIMIT 20
        """)
        recent_alerts = _fetchall(cur)

        cur.execute("""
            SELECT u.id, u.name, u.email, u.signup_date, u.status, u.token,
                   COUNT(p.id) as product_count
            FROM users u
            LEFT JOIN products p ON p.user_id = u.id
            GROUP BY u.id
            ORDER BY u.signup_date DESC
        """)
        users = _fetchall(cur)
    finally:
        cur.close()
        conn.close()

    rows_html = "".join([f"""
    <tr>
    <td>{u['id']}</td>
    <td>{u['name']}</td>
    <td>{u['email']}</td>
    <td>{u['product_count']}</td>
    <td>{str(u['signup_date'])[:10]}</td>
    <td>{u['status']}</td>
    <td><a href="/dashboard?token={u.get('token', '')}" target="_blank">View</a></td>
    </tr>
    """ for u in users])

    return f"""
    <html>
    <head>
    <title>Admin Dashboard</title>
    <style>
    body {{ font-family: Arial; margin: 20px; background: #f5f5f5; }}
    .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; }}
    h1 {{ color: #5b67f8; }}
    h2 {{ color: #333; margin-top: 30px; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
    th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; font-size: 13px; }}
    th {{ background-color: #5b67f8; color: white; }}
    tr:nth-child(even) {{ background-color: #f9f9f9; }}
    .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }}
    .stat-card {{ background: linear-gradient(135deg, #5b67f8 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 10px; }}
    .stat-number {{ font-size: 32px; font-weight: bold; }}
    .stat-label {{ font-size: 14px; margin-top: 10px; }}
    a {{ color: #5b67f8; }}
    </style>
    </head>
    <body>
    <div class="container">
    <h1>📊 Admin Dashboard</h1>
    <div class="stats">
    <div class="stat-card">
    <div class="stat-number">{total_users}</div>
    <div class="stat-label">Total Signups</div>
    </div>
    <div class="stat-card">
    <div class="stat-number">{total_products}</div>
    <div class="stat-label">Products Tracked</div>
    </div>
    <div class="stat-card">
    <div class="stat-number">${total_users * 4.99:.2f}</div>
    <div class="stat-label">Potential Monthly Revenue</div>
    </div>
    <div class="stat-card" style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);">
    <div class="stat-number">{total_alerts}</div>
    <div class="stat-label">Total Alerts Sent</div>
    </div>
    <div class="stat-card" style="background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);">
    <div class="stat-number">{alerts_7d}</div>
    <div class="stat-label">Alerts (Last 7 Days)</div>
    </div>
    </div>

    <h2>🔔 Recent Alerts</h2>
    <table>
    <tr>
    <th>User</th><th>Email</th><th>Store</th><th>Price at Alert</th><th>Target</th><th>Sent At</th><th>Product URL</th>
    </tr>
    {"".join([f'<tr><td>{a.get("user_name","N/A")}</td><td>{a.get("user_email","")}</td><td>{a.get("store","")}</td><td>${a.get("price_at_alert","")}</td><td>${a.get("target_price","")}</td><td>{str(a.get("sent_at",""))[:19]}</td><td><a href="{a.get("product_url","")}" target="_blank">View</a></td></tr>' for a in recent_alerts]) if recent_alerts else '<tr><td colspan="7" style="text-align:center;color:#999;">No alerts sent yet</td></tr>'}
    </table>

    <h2>Signup List</h2>
    <table>
    <tr>
    <th>ID</th><th>Name</th><th>Email</th><th>Products</th><th>Signup Date</th><th>Status</th><th>Dashboard</th>
    </tr>
    {rows_html}
    </table>
    </div>
    </body>
    </html>
    """


# ─────────────────────────────────────────────
# STRIPE PAYMENT ROUTES
# ─────────────────────────────────────────────

@app.route('/api/create-checkout-session', methods=['POST'])
def create_checkout_session():
    """
    Create a Stripe Checkout session for upgrading to Pro.
    The user's token is stored as client_reference_id so the webhook
    can identify them after payment.
    """
    token = get_token_from_request()
    if not token:
        return jsonify({'error': 'Token required'}), 400

    user, _ = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid token'}), 404

    if user.get('is_pro') or user.get('status') == 'pro':
        return jsonify({'error': 'Already on Pro plan'}), 400

    stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

    # Choose price based on billing interval requested by frontend
    billing  = request.args.get('billing', 'monthly')   # 'monthly' or 'annual'
    if billing == 'annual':
        price_id = os.getenv('STRIPE_ANNUAL_PRICE_ID')
        if not price_id:
            # Annual not configured yet — fall back to monthly
            billing  = 'monthly'
            price_id = os.getenv('STRIPE_PRICE_ID')
    else:
        price_id = os.getenv('STRIPE_PRICE_ID')

    if not stripe.api_key or not price_id:
        return jsonify({'error': 'Payment not configured yet'}), 500

    try:
        base_url = get_base_url()
        session = stripe.checkout.Session.create(
            mode='subscription',
            payment_method_types=['card'],
            line_items=[{'price': price_id, 'quantity': 1}],
            customer_email=user['email'],
            client_reference_id=token,          # used in webhook to find the user
            success_url=f"{base_url}/upgrade-success?token={token}",
            cancel_url=f"{base_url}/dashboard?token={token}",
        )
        print(f"✅ Checkout session created ({billing}) for {user['email']}")
        return jsonify({'checkout_url': session.url}), 200

    except Exception as e:
        print(f"❌ Checkout session error: {e}")
        return jsonify({'error': 'An unexpected error occurred. Please try again.'}), 500


@app.route('/api/stripe-webhook', methods=['POST'])
def stripe_webhook():
    """
    Handle Stripe webhook events to keep the database in sync with
    subscription status. Must be reachable publicly — register it in
    the Stripe dashboard pointing to https://www.dealnotify.co/api/stripe-webhook
    """
    stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
    webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    payload    = request.get_data()
    sig_header = request.headers.get('Stripe-Signature', '')

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        print("❌ Stripe webhook: invalid payload")
        return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError:
        print("❌ Stripe webhook: invalid signature")
        return 'Invalid signature', 400

    event_type = event['type']
    print(f"🔔 Stripe event: {event_type}")

    if event_type == 'checkout.session.completed':
        session         = event['data']['object']
        token           = getattr(session, 'client_reference_id', None)
        customer_id     = getattr(session, 'customer', None)
        subscription_id = getattr(session, 'subscription', None)

        if token:
            conn = get_db_conn()
            cur  = conn.cursor()
            try:
                cur.execute("""
                    UPDATE users
                    SET status = 'pro',
                        is_pro = TRUE,
                        stripe_customer_id = %s,
                        stripe_subscription_id = %s
                    WHERE token = %s
                """, (customer_id, subscription_id, token))
                conn.commit()
                print(f"✅ User upgraded to Pro (token ...{token[-8:]})")
            except Exception as e:
                conn.rollback()
                print(f"❌ Webhook DB error: {e}")
            finally:
                cur.close()
                conn.close()

    elif event_type == 'customer.subscription.deleted':
        subscription_id = event['data']['object']['id']
        conn = get_db_conn()
        cur  = conn.cursor()
        try:
            cur.execute("""
                UPDATE users
                SET status = 'active',
                    is_pro = FALSE,
                    stripe_subscription_id = NULL
                WHERE stripe_subscription_id = %s
            """, (subscription_id,))
            conn.commit()
            print(f"⬇️  User downgraded from Pro (subscription cancelled)")
        except Exception as e:
            conn.rollback()
            print(f"❌ Webhook downgrade DB error: {e}")
        finally:
            cur.close()
            conn.close()

    elif event_type == 'invoice.payment_failed':
        # Log it — optionally send a payment failure email in future
        customer_id = getattr(event['data']['object'], 'customer', None)
        print(f"⚠️  Payment failed for customer {customer_id}")

    return jsonify({'received': True}), 200


@app.route('/upgrade-success')
def upgrade_success():
    return send_from_directory('.', 'upgrade-success.html')


# ── Blog routes ───────────────────────────────
@app.route('/blog')
def blog_index():
    return send_from_directory('.', 'blog.html')

@app.route('/blog/amazon-dynamic-pricing-algorithm')
def blog_post_1():
    return send_from_directory('.', 'blog-dynamic-pricing.html')

@app.route('/blog/restock-alerts-back-in-stock-notifications')
def blog_post_2():
    return send_from_directory('.', 'blog-restock-alerts.html')

@app.route('/blog/free-price-tracker-chrome-extension')
def blog_post_3():
    return send_from_directory('.', 'blog-free-price-tracker-chrome-extension.html')


@app.route('/sitemap.xml')
def sitemap():
    from flask import Response
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://www.dealnotify.co/</loc>
    <lastmod>2026-04-05</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://www.dealnotify.co/blog</loc>
    <lastmod>2026-04-05</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://www.dealnotify.co/blog/amazon-dynamic-pricing-algorithm</loc>
    <lastmod>2026-04-05</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://www.dealnotify.co/blog/restock-alerts-back-in-stock-notifications</loc>
    <lastmod>2026-04-06</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://www.dealnotify.co/blog/free-price-tracker-chrome-extension</loc>
    <lastmod>2026-04-14</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://www.dealnotify.co/terms.html</loc>
    <lastmod>2026-04-01</lastmod>
    <changefreq>yearly</changefreq>
    <priority>0.3</priority>
  </url>
  <url>
    <loc>https://www.dealnotify.co/contact.html</loc>
    <lastmod>2026-04-01</lastmod>
    <changefreq>yearly</changefreq>
    <priority>0.4</priority>
  </url>
</urlset>"""
    return Response(xml, mimetype='application/xml')


@app.route('/robots.txt')
def robots():
    from flask import Response
    txt = "User-agent: *\nAllow: /\n\nSitemap: https://www.dealnotify.co/sitemap.xml\n"
    return Response(txt, mimetype='text/plain')


# ─────────────────────────────────────────────
# AUTOMATED HOURLY PRICE CHECK JOB
# ─────────────────────────────────────────────

def check_all_prices_job():
    """
    Hourly background job: check prices for every tracked product across
    all users. Interval is based on plan:
      - Pro  users → check if last_checked > 2 hours ago  (or never checked)
      - Free users → check if last_checked > 6 hours ago  (or never checked)
    The scheduler still runs every hour; per-product interval logic is handled here.
    """
    PRO_INTERVAL_HOURS  = 2
    FREE_INTERVAL_HOURS = 6

    started_at = datetime.now().isoformat()
    print(f"\n⏰ === Price check job started at {started_at} ===")

    total_checked = 0
    total_skipped = 0
    total_alerts  = 0
    total_errors  = 0
    now           = datetime.now()

    try:
        # Fetch ALL users regardless of plan (active + pro)
        conn = get_db_conn()
        cur  = conn.cursor()
        try:
            cur.execute("SELECT * FROM users WHERE status IN ('active', 'pro')")
            users = _fetchall(cur)
        finally:
            cur.close()
            conn.close()

        print(f"   → {len(users)} user(s) to process")

        for user in users:
            user_id       = user['id']
            token         = user['token']
            is_pro        = bool(user.get('is_pro'))
            interval_hrs  = PRO_INTERVAL_HOURS if is_pro else FREE_INTERVAL_HOURS
            dashboard_url = f"{get_base_url()}/dashboard?token={token}"

            # Fetch this user's products
            conn = get_db_conn()
            cur  = conn.cursor()
            try:
                cur.execute(
                    "SELECT * FROM products WHERE user_id = %s ORDER BY added_date ASC",
                    (user_id,)
                )
                products = _fetchall(cur)
            finally:
                cur.close()
                conn.close()

            for product in products:
                url = product.get('url')
                if not url:
                    continue

                # ── Interval gate ──────────────────────────────────────────
                last_checked = product.get('last_checked')
                if last_checked:
                    if isinstance(last_checked, str):
                        last_checked = datetime.fromisoformat(last_checked)
                    hours_since = (now - last_checked).total_seconds() / 3600
                    if hours_since < interval_hrs:
                        total_skipped += 1
                        continue   # not due yet
                # ───────────────────────────────────────────────────────────

                track_type = product.get('track_type', 'price')
                plan_label = '⭐Pro' if is_pro else 'Free'
                type_label = '📦Restock' if track_type == 'restock' else '💰Price'
                print(f"🔍 [{plan_label}] [{type_label}] [{user['email']}] {url[:60]}")

                # ── Scrape: use unified scraper that returns price + stock ──
                try:
                    scrape_result = scrape_stock_status(url)
                    total_checked += 1
                except Exception as scrape_err:
                    print(f"   ❌ Scrape error: {scrape_err}")
                    total_errors += 1
                    continue

                current_price = scrape_result.get('price')
                new_stock_status = scrape_result.get('stock_status', 'unknown')
                stock_detail = scrape_result.get('stock_detail', '')
                old_stock_status = product.get('last_stock_status') or product.get('stock_status')

                if current_price is None and track_type == 'price':
                    print(f"   ⚠️  Price not found")
                    total_errors += 1
                    conn = get_db_conn()
                    cur  = conn.cursor()
                    try:
                        cur.execute(
                            "UPDATE products SET last_checked = %s WHERE id = %s AND user_id = %s",
                            (datetime.now(), product['id'], user_id)
                        )
                        conn.commit()
                    except Exception:
                        conn.rollback()
                    finally:
                        cur.close()
                        conn.close()
                    continue

                alert_sent = False
                restock_alert_sent = product.get('restock_alert_sent', False)

                # ── Price-drop alert logic (for track_type = 'price') ──────
                if track_type == 'price' and current_price is not None:
                    target = product.get('target_price')
                    if target and float(current_price) <= float(target):
                        alert_sent = True
                        send_price_drop_alert(
                            name=user['name'],
                            email=user['email'],
                            product_url=url,
                            current_price=current_price,
                            target_price=target,
                            store=product.get('store', 'the store'),
                            dashboard_url=dashboard_url,
                            user_timezone=user.get('timezone')
                        )
                        total_alerts += 1
                        print(f"   🔔 Price alert sent — ${current_price} <= target ${target}")
                        try:
                            aconn = get_db_conn()
                            acur  = aconn.cursor()
                            acur.execute("""
                                INSERT INTO alerts_log (user_id, product_id, product_url, store, price_at_alert, target_price, alert_type)
                                VALUES (%s, %s, %s, %s, %s, %s, 'price_drop')
                            """, (user_id, product['id'], url, product.get('store'), current_price, target))
                            aconn.commit()
                            acur.close()
                            aconn.close()
                        except Exception as log_err:
                            print(f"⚠️ alerts_log insert error (non-fatal): {log_err}")

                # ── Restock alert logic (for track_type = 'restock') ───────
                if track_type == 'restock' and new_stock_status == 'in_stock':
                    # Transition: was out_of_stock (or unknown) → now in_stock
                    if old_stock_status in ('out_of_stock', None, '') and not restock_alert_sent:
                        restock_alert_sent = True
                        send_restock_alert(
                            name=user['name'],
                            email=user['email'],
                            product_url=url,
                            store=product.get('store', 'the store'),
                            dashboard_url=dashboard_url,
                            user_timezone=user.get('timezone')
                        )
                        total_alerts += 1
                        print(f"   🔔 Restock alert sent — {old_stock_status} → in_stock!")
                        try:
                            aconn = get_db_conn()
                            acur  = aconn.cursor()
                            acur.execute("""
                                INSERT INTO alerts_log (user_id, product_id, product_url, store, price_at_alert, target_price, alert_type)
                                VALUES (%s, %s, %s, %s, %s, %s, 'restock')
                            """, (user_id, product['id'], url, product.get('store'), current_price, product.get('target_price')))
                            aconn.commit()
                            acur.close()
                            aconn.close()
                        except Exception as log_err:
                            print(f"⚠️ alerts_log insert error (non-fatal): {log_err}")

                # Reset restock_alert_sent when item goes out of stock again
                if track_type == 'restock' and new_stock_status == 'out_of_stock':
                    restock_alert_sent = False

                # ── Persist updated state to DB ───────────────────────────
                conn = get_db_conn()
                cur  = conn.cursor()
                try:
                    cur.execute("""
                        UPDATE products
                        SET current_price      = COALESCE(%s, current_price),
                            last_checked       = %s,
                            status             = %s,
                            alert_sent         = %s,
                            stock_status       = %s,
                            last_stock_status  = %s,
                            stock_detail       = %s,
                            restock_alert_sent = %s
                        WHERE id = %s AND user_id = %s
                    """, (
                        current_price,
                        datetime.now(),
                        'alert_sent' if (alert_sent or restock_alert_sent) else 'monitoring',
                        alert_sent,
                        new_stock_status,
                        product.get('stock_status'),   # old status becomes last_stock_status
                        stock_detail,
                        restock_alert_sent,
                        product['id'],
                        user_id
                    ))
                    conn.commit()
                except Exception as db_err:
                    conn.rollback()
                    print(f"   ❌ DB update error: {db_err}")
                finally:
                    cur.close()
                    conn.close()

                # Record in price history (non-blocking)
                if current_price is not None:
                    log_price_history(product['id'], current_price)

                # Record in stock history (non-blocking)
                if new_stock_status != 'unknown':
                    try:
                        sconn = get_db_conn()
                        scur  = sconn.cursor()
                        scur.execute("""
                            INSERT INTO stock_history (product_id, status, detail)
                            VALUES (%s, %s, %s)
                        """, (product['id'], new_stock_status, stock_detail))
                        sconn.commit()
                        scur.close()
                        sconn.close()
                    except Exception as sh_err:
                        print(f"⚠️ stock_history insert error (non-fatal): {sh_err}")

    except Exception as e:
        print(f"❌ Hourly job fatal error: {e}")

    print(
        f"✅ Check done — "
        f"{total_checked} checked, {total_skipped} skipped (not due), "
        f"{total_alerts} alerts sent (price + restock), {total_errors} errors\n"
    )
    return {
        'checked': total_checked,
        'skipped': total_skipped,
        'alerts':  total_alerts,
        'errors':  total_errors
    }


@app.route('/api/check-all-prices', methods=['GET'])
def check_all_prices_admin():
    """
    Admin endpoint to manually trigger a full price check for all users.
    Protected by ADMIN_KEY env var — pass as ?key=<ADMIN_KEY> or X-Admin-Key header.
    """
    admin_key = request.args.get('key') or request.headers.get('X-Admin-Key', '')
    expected  = os.getenv('ADMIN_KEY', '')

    if not expected or not hmac.compare_digest(admin_key, expected):
        return jsonify({'error': 'Unauthorized'}), 401

    # Run synchronously so the caller gets the result
    result = check_all_prices_job()
    return jsonify({'success': True, **result, 'triggered_at': datetime.now().isoformat()}), 200


# ─────────────────────────────────────────────
# Compare Feature
# ─────────────────────────────────────────────

def wrap_affiliate_link(retailer: str, url: str | None) -> str | None:
    if not url:
        return None
    amazon_tag = os.environ.get("AMAZON_AFFILIATE_TAG")
    if retailer == "amazon":
        if not amazon_tag:
            return url
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}tag={amazon_tag}"
    affiliate_ids = {
        "walmart": os.environ.get("WALMART_AFFILIATE_ID"),
        "target": os.environ.get("TARGET_AFFILIATE_ID"),
        "bestbuy": os.environ.get("BESTBUY_AFFILIATE_ID"),
        "costco": os.environ.get("COSTCO_AFFILIATE_ID"),
    }
    affiliate_id = affiliate_ids.get(retailer)
    if not affiliate_id:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}affid={affiliate_id}"


def _extract_asin_from_url(url: str) -> str | None:
    import re
    m = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)', url)
    return m.group(1) if m else None


# In-memory comparison cache — 15-minute TTL, sits in front of DB cache to avoid
# Firecrawl + DB calls on rapid repeat page loads or tab refreshes
_mem_compare_cache: dict = {}
_mem_cache_lock = threading.Lock()
_MEM_CACHE_TTL = 900  # 15 minutes


def _mem_cache_get(source_retailer, source_identifier, target_retailer):
    key = (source_retailer, source_identifier, target_retailer)
    with _mem_cache_lock:
        entry = _mem_compare_cache.get(key)
        if not entry:
            return None
        result, ts = entry
        if time.time() - ts > _MEM_CACHE_TTL:
            del _mem_compare_cache[key]
            return None
        return result


def _mem_cache_set(source_retailer, source_identifier, target_retailer, value):
    key = (source_retailer, source_identifier, target_retailer)
    with _mem_cache_lock:
        _mem_compare_cache[key] = (value, time.time())
        # Evict expired entries if the dict grows large
        if len(_mem_compare_cache) > 500:
            now = time.time()
            stale = [k for k, (_, ts) in _mem_compare_cache.items() if now - ts > _MEM_CACHE_TTL]
            for k in stale:
                del _mem_compare_cache[k]


def _get_cached_comparison(source_retailer, source_identifier, target_retailer):
    # Check in-memory cache first (fastest path)
    mem_hit = _mem_cache_get(source_retailer, source_identifier, target_retailer)
    if mem_hit is not None:
        return mem_hit

    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                """SELECT * FROM product_comparisons
                   WHERE source_retailer = %s AND source_identifier = %s
                     AND target_retailer = %s AND expires_at > NOW()
                   ORDER BY cached_at DESC LIMIT 1""",
                (source_retailer, source_identifier, target_retailer),
            )
            row = _fetchone(cur)
            if row:
                # Warm the in-memory cache so subsequent requests within 15 min skip the DB
                _mem_cache_set(source_retailer, source_identifier, target_retailer, row)
            return row
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        print(f"⚠️ Cache lookup failed (non-fatal): {e}")
        return None


def _save_comparison(source_retailer, source_identifier, source_url, source_title,
                     source_price, target_retailer, match):
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        try:
            hit = match.get("match") if match else None
            confidence = hit.get("confidence") if hit else "none"
            # Matches: 7-day TTL (prices are relatively stable).
            # No-match: 30-day TTL (if a retailer doesn't carry the product, that's unlikely to change soon).
            ttl_days = 7 if confidence in ("exact", "likely") else 30
            expires_at = datetime.now() + timedelta(days=ttl_days)
            cur.execute(
                """INSERT INTO product_comparisons
                   (source_retailer, source_identifier, source_url, source_title, source_price,
                    target_retailer, target_url, target_title, target_price, confidence, match_reasoning,
                    expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (
                    source_retailer, source_identifier, source_url, source_title, source_price,
                    target_retailer,
                    hit.get("url") if hit else None,
                    hit.get("title") if hit else None,
                    hit.get("price") if hit else None,
                    confidence,
                    hit.get("reasoning") if hit else match.get("reason") if match else None,
                    expires_at,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            comparison_id = row[0] if row else None
            # Warm in-memory cache with the freshly saved comparison so repeated requests
            # within 15 min hit memory instead of DB + Firecrawl
            if comparison_id:
                _mem_cache_set(source_retailer, source_identifier, target_retailer, {
                    'id': comparison_id,
                    'target_url': hit.get("url") if hit else None,
                    'target_title': hit.get("title") if hit else None,
                    'target_price': hit.get("price") if hit else None,
                    'confidence': confidence,
                })
            return comparison_id
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        print(f"⚠️ Failed to save comparison (non-fatal): {e}")
        return None


@app.route('/api/compare', methods=['POST'])
def compare_product():
    token = get_token_from_request()
    user, _ = get_user_by_token(token) if token else (None, None)
    if not user:
        return jsonify({'error': 'Token required'}), 401

    data = request.get_json() or {}
    source_url = data.get('source_url', '')
    if not source_url:
        return jsonify({'error': 'source_url required'}), 400

    source_retailer = data.get('source_retailer', 'amazon')
    asin = data.get('asin') or _extract_asin_from_url(source_url)
    source_title = data.get('title')
    _raw_price = data.get('price')
    source_price = float(_raw_price.strip().lstrip('$').replace(',', '')) if _raw_price else None
    target_retailers = data.get('target_retailers', ['walmart'])
    force_refresh = data.get('force_refresh', False)

    source_identifier = asin or source_url
    comparisons = []

    caller_identity = None
    if source_title:
        caller_identity = {
            'asin': asin,
            'title': source_title,
            'brand': source_title.split()[0] if source_title else None,
            'model': None,
            'upc': None,
            'price': source_price,
            'image_url': None,
            'search_query': ' '.join(w.strip(',-–—') for w in source_title.split()[:5]).strip(',-–— ') if source_title else None,
        }

    # Separate cached vs uncached retailers
    uncached_retailers = []
    for retailer in target_retailers:
        cached = None if force_refresh else _get_cached_comparison(source_retailer, source_identifier, retailer)
        if cached:
            hit = {
                'retailer': retailer,
                'url': cached['target_url'],
                'title': cached['target_title'],
                'price': float(cached['target_price']) if cached['target_price'] else None,
                'savings': None,
                'confidence': cached['confidence'],
                'comparison_id': cached['id'],
                'cached': True,
            }
            if source_price and cached['target_price']:
                hit['savings'] = round(float(source_price) - float(cached['target_price']), 2)
            comparisons.append(hit)
        else:
            uncached_retailers.append(retailer)

    # Rate-limit only when real API calls are needed; cache hits are always free
    if uncached_retailers and rate_limiter.is_rate_limited(
        f'compare:{user["id"]}', max_requests=200, window_seconds=3600
    ):
        return jsonify({'error': 'Rate limit exceeded — 200 API requests per hour'}), 429

    # Search uncached retailers in parallel
    def _search_retailer(retailer):
        try:
            return retailer, find_comparable_product(source_url, source_retailer, retailer, identity=caller_identity)
        except Exception as e:
            print(f"❌ /api/compare error for {retailer}: {e}")
            return retailer, None

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_search_retailer, r): r for r in uncached_retailers}
        retailer_matches = {r: None for r in uncached_retailers}
        done, timed_out = futures_wait(futures, timeout=11)
        for future in done:
            try:
                retailer, match = future.result()
                retailer_matches[retailer] = match
            except Exception as e:
                print(f"❌ /api/compare future error: {e}")
        for future in timed_out:
            retailer = futures[future]
            print(f"⏱ /api/compare: {retailer} timed out, skipping")
            future.cancel()

    # Score, save and build response entries in original order
    for retailer in uncached_retailers:
        match = retailer_matches.get(retailer)
        comparison_id = _save_comparison(
            source_retailer, source_identifier, source_url, source_title, source_price,
            retailer, match,
        )

        hit_data = match.get('match') if match else None
        if hit_data and hit_data.get('confidence') in ('exact', 'likely'):
            hit_data['url'] = wrap_affiliate_link(retailer, hit_data.get('url'))

        entry = {
            'retailer': retailer,
            'url': hit_data.get('url') if hit_data else None,
            'title': hit_data.get('title') if hit_data else None,
            'price': float(hit_data['price']) if hit_data and hit_data.get('price') else None,
            'savings': None,
            'confidence': hit_data.get('confidence') if hit_data else 'none',
            'comparison_id': comparison_id,
            'cached': False,
            'debug_reasoning': (hit_data.get('llm_error') or hit_data.get('reasoning')) if hit_data else None,
        }
        if source_price and entry['price']:
            entry['savings'] = round(float(source_price) - entry['price'], 2)
        comparisons.append(entry)

    return jsonify({
        'source': {'retailer': source_retailer, 'asin': asin, 'url': source_url, 'title': source_title, 'price': source_price},
        'comparisons': comparisons,
    }), 200


@app.route('/api/compare/click', methods=['POST'])
def track_comparison_click():
    token = get_token_from_request()
    user, _ = get_user_by_token(token) if token else (None, None)
    if not user:
        return '', 204

    data = request.get_json() or {}
    comparison_id = data.get('comparison_id')

    if comparison_id:
        try:
            conn = get_db_conn()
            cur = conn.cursor()
            try:
                cur.execute(
                    'INSERT INTO comparison_clicks (user_id, comparison_id) VALUES (%s, %s)',
                    (user['id'], comparison_id),
                )
                conn.commit()
            finally:
                cur.close()
                conn.close()
        except Exception as e:
            print(f"⚠️ Failed to log comparison click (non-fatal): {e}")

    return '', 204


@app.route('/api/admin/compare-cache/clear', methods=['POST'])
def admin_clear_compare_cache():
    admin_key = request.headers.get('X-Admin-Key')
    if admin_key != os.environ.get('ADMIN_KEY'):
        return jsonify({'error': 'unauthorized'}), 401

    data = request.get_json() or {}
    asin = data.get('asin')

    conn = get_db_conn()
    cur = conn.cursor()
    try:
        if asin:
            cur.execute(
                """DELETE FROM comparison_clicks
                   WHERE comparison_id IN (
                       SELECT id FROM product_comparisons WHERE source_identifier = %s
                   )""",
                (asin,)
            )
            cur.execute(
                "DELETE FROM product_comparisons WHERE source_identifier = %s",
                (asin,)
            )
        else:
            cur.execute("DELETE FROM comparison_clicks")
            cur.execute("DELETE FROM product_comparisons")
        deleted = cur.rowcount
        conn.commit()
        return jsonify({'deleted': deleted, 'asin': asin or 'all'})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cur.close()
        conn.close()


@app.route('/api/admin/compare-stats', methods=['GET'])
def admin_compare_stats():
    admin_key = request.headers.get('X-Admin-Key')
    if admin_key != os.environ.get('ADMIN_KEY'):
        return jsonify({'error': 'unauthorized'}), 401

    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute('SELECT COUNT(*) as count FROM product_comparisons')
        total_comparisons = _fetchone(cur)['count']
        cur.execute('SELECT COUNT(*) as count FROM comparison_clicks')
        total_clicks = _fetchone(cur)['count']
        cur.execute("""
            SELECT confidence, COUNT(*) as count
            FROM product_comparisons GROUP BY confidence
        """)
        by_confidence = {row['confidence']: row['count'] for row in _fetchall(cur)}
        cur.execute("""
            SELECT source_title, COUNT(*) as lookups
            FROM product_comparisons
            WHERE source_title IS NOT NULL
            GROUP BY source_title ORDER BY lookups DESC LIMIT 10
        """)
        top_products = _fetchall(cur)
        ctr = round(total_clicks / total_comparisons, 4) if total_comparisons > 0 else 0
        return jsonify({
            'total_comparisons': total_comparisons,
            'total_clicks': total_clicks,
            'click_through_rate': ctr,
            'matches_by_confidence': by_confidence,
            'top_source_products': top_products,
        }), 200
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    print("=" * 70)
    print("🚀 DEALNOTIFY - WEB APP")
    print("=" * 70)
    print("\n📱 Landing Page: http://localhost:5000")
    print("📊 Admin Panel:  http://localhost:5000/admin")
    print("\n💡 Press Ctrl+C to stop\n")

    # Initialize database tables on startup
    try:
        init_db()
    except Exception as e:
        print(f"⚠️  Could not init DB: {e}")

    # Scheduler runs every hour; per-product interval logic inside the job:
    # Pro users checked every 2h, Free users every 6h
    try:
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            check_all_prices_job,
            trigger='interval',
            hours=1,
            id='hourly_price_check',
            max_instances=1,       # never run two at once
            misfire_grace_time=300 # if delayed up to 5 min, still run it
        )
        scheduler.start()
        print("⏰ Price check scheduler started (runs hourly; Pro=2h interval, Free=6h interval)\n")
    except Exception as e:
        print(f"⚠️  Scheduler could not start: {e}\n")

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
