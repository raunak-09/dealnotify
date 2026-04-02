"""
PriceGuard - Web App (Landing Page + Backend)
Database: PostgreSQL (via DATABASE_URL env var — provisioned by Railway)
Auth: password hashing via werkzeug, email verification, forgot/reset password
"""

from flask import Flask, request, jsonify, send_from_directory
import os
import secrets
from datetime import datetime, timedelta
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from dotenv import load_dotenv
from price_monitor import scrape_price
import pg8000.dbapi as pg8000
from urllib.parse import urlparse
from apscheduler.schedulers.background import BackgroundScheduler
import stripe
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='')


# ── Force HTTPS (Railway terminates SSL at the proxy and sets X-Forwarded-Proto)
@app.before_request
def force_https():
    # X-Forwarded-Proto can be comma-separated when multiple proxies are in the chain
    # e.g. "http, https" — take the first value only
    proto = request.headers.get('X-Forwarded-Proto', '').split(',')[0].strip()
    if proto == 'http':
        from flask import redirect
        url = request.url.replace('http://', 'https://', 1)
        return redirect(url, code=301)


# ── Security headers on every response
@app.after_request
def add_security_headers(response):
    # HSTS: tell browsers to always use HTTPS for this domain for 1 year
    # includeSubDomains covers both dealnotify.co and www.dealnotify.co
    response.headers['Strict-Transport-Security'] = \
        'max-age=31536000; includeSubDomains'
    # Prevent MIME-type sniffing
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # Block clickjacking — only allow framing from same origin
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    # Only send the origin as referrer (not full URL) when crossing to another site
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
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
                trial_days_remaining INTEGER NOT NULL DEFAULT 7
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
        # Stripe migration — add columns if they don't exist yet
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT;")
        # Explicit Pro flag — single source of truth for paid status
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_pro BOOLEAN NOT NULL DEFAULT FALSE;")
        # Back-fill: anyone whose status is already 'pro' gets is_pro = TRUE
        cur.execute("UPDATE users SET is_pro = TRUE WHERE status = 'pro' AND is_pro = FALSE;")
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
        'signup_date': user_row['signup_date'].isoformat() if hasattr(user_row['signup_date'], 'isoformat') else user_row['signup_date'],
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
        'added_date': p['added_date'].isoformat() if hasattr(p['added_date'], 'isoformat') else p['added_date'],
        'status': p['status'],
        'last_checked': p['last_checked'].isoformat() if p['last_checked'] and hasattr(p['last_checked'], 'isoformat') else p['last_checked'],
        'current_price': float(p['current_price']) if p['current_price'] is not None else None,
        'alert_sent': p['alert_sent']
    }


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

        <div style="background-color: #f0f7ff; padding: 20px; border-radius: 10px; margin: 25px 0; text-align: center; border: 2px solid #667eea;">
        <h2 style="color: #667eea; margin-top: 0;">📊 Your Personal Dashboard</h2>
        <p style="color: #333; margin-bottom: 20px;">View and manage all your tracked products in one place. Bookmark this link!</p>
        <a href="{dashboard_url}" style="display: inline-block; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 15px 40px; text-decoration: none; border-radius: 50px; font-weight: bold; font-size: 16px;">
        👉 View My Dashboard
        </a>
        <p style="color: #999; font-size: 12px; margin-top: 15px;">Keep this link private — it's your personal access link</p>
        </div>

        <div style="background-color: #f9f9f9; padding: 20px; border-radius: 5px; margin: 20px 0;">
        <h2 style="color: #667eea; margin-top: 0;">🚀 What happens next?</h2>
        <ol style="color: #333; line-height: 2;">
        <li>We'll start monitoring your product price right away</li>
        <li>When the price drops to your target, you'll get an instant email alert</li>
        <li>You can add more products anytime from your dashboard</li>
        </ol>
        </div>

        <div style="background-color: #f0f7ff; padding: 20px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #667eea;">
        <h3 style="color: #667eea; margin-top: 0;">💝 Your Free Trial</h3>
        <p style="color: #333;">You have <strong>7 days free</strong> to try all features!</p>
        <p style="color: #666; font-size: 14px;">After that, it's just <strong>$4.99/month</strong> for unlimited monitoring.</p>
        </div>

        <div style="background-color: #fff3cd; padding: 15px; border-radius: 5px; margin: 20px 0;">
        <p style="color: #856404; margin: 0; font-size: 14px;">
        <strong>💡 Pro Tip:</strong> Monitor Best Buy, Amazon, Walmart, Target and more for the best deals!
        </p>
        </div>

        <hr style="border: none; border-top: 2px solid #eee; margin: 30px 0;">
        <p style="color: #333; font-size: 14px;">Best regards,<br>
        <strong>The DealNotify Team</strong><br>
        <a href="mailto:hello@dealnotify.co" style="color: #667eea;">hello@dealnotify.co</a> | <a href="https://www.dealnotify.co" style="color: #667eea;">www.dealnotify.co</a><br><br>
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
You have 7 days free to try all features!
After that, it's just $4.99/month for unlimited monitoring.

Questions? Reply to this email at hello@dealnotify.co

Best regards,
The DealNotify Team
hello@dealnotify.co | www.dealnotify.co
💰 Never miss a price drop again!

© 2026 DealNotify. All rights reserved.
        """

        message = Mail(
            from_email=from_email,
            to_emails=email,
            subject='🎉 Welcome to DealNotify! Here is your dashboard link',
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
        <h1 style="color:#5b67f8;text-align:center;margin-bottom:6px;">🛡️ PriceGuard</h1>
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
            This link expires in 48 hours. If you didn't create a PriceGuard account, you can safely ignore this email.
        </p>
        <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
        <p style="color:#999;font-size:12px;text-align:center;">© 2026 PriceGuard · <a href="mailto:hello@dealnotify.co" style="color:#5b67f8;">hello@dealnotify.co</a></p>
        </div></body></html>
        """

        message = Mail(
            from_email=from_email,
            to_emails=email,
            subject='✅ Verify your PriceGuard email address',
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
        <h1 style="color:#5b67f8;text-align:center;margin-bottom:6px;">🛡️ PriceGuard</h1>
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
        <p style="color:#999;font-size:12px;text-align:center;">© 2026 PriceGuard · <a href="mailto:hello@dealnotify.co" style="color:#5b67f8;">hello@dealnotify.co</a></p>
        </div></body></html>
        """

        message = Mail(
            from_email=from_email,
            to_emails=email,
            subject='🔑 Reset your PriceGuard password',
            html_content=html_content
        )
        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        print(f"📧 Password reset email sent to {email} (status {response.status_code})")
        return True
    except Exception as e:
        print(f"❌ Password reset email error: {e}")
        return False


def send_price_drop_alert(name, email, product_url, current_price, target_price, store, dashboard_url):
    """Send price drop alert email via SendGrid"""
    try:
        api_key = os.getenv('SENDGRID_API_KEY')
        from_email = os.getenv('SENDGRID_FROM_EMAIL', 'hello@dealnotify.co')

        if not api_key:
            return False

        savings = float(target_price) - float(current_price)

        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
        <div style="background-color: white; max-width: 600px; margin: 0 auto; padding: 30px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">

        <h1 style="color: #27ae60; text-align: center;">🎉 Price Drop Alert, {name}!</h1>

        <div style="background-color: #edfaf1; border: 2px solid #27ae60; border-radius: 10px; padding: 25px; margin: 25px 0; text-align: center;">
        <p style="color: #555; font-size: 14px; margin-bottom: 10px;">A product you're tracking just dropped in price!</p>
        <div style="display: flex; justify-content: center; gap: 30px; margin: 15px 0;">
        <div>
            <div style="font-size: 13px; color: #888;">Current Price</div>
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
        </div>

        <div style="text-align: center; margin: 25px 0;">
        <a href="{product_url}" style="display: inline-block; background: linear-gradient(135deg, #27ae60 0%, #2ecc71 100%); color: white; padding: 15px 40px; text-decoration: none; border-radius: 50px; font-weight: bold; font-size: 16px;">
        🛒 Buy Now on {store}
        </a>
        </div>

        <div style="text-align: center; margin: 15px 0;">
        <a href="{dashboard_url}" style="color: #667eea; font-size: 14px;">View your full dashboard →</a>
        </div>

        <hr style="border: none; border-top: 2px solid #eee; margin: 30px 0;">
        <p style="color: #333; font-size: 14px;">Best regards,<br>
        <strong>The DealNotify Team</strong><br>
        <a href="mailto:hello@dealnotify.co" style="color: #667eea;">hello@dealnotify.co</a> | <a href="https://www.dealnotify.co" style="color: #667eea;">www.dealnotify.co</a><br><br>
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
            subject=f'🎉 Price Drop Alert! ${float(current_price):.2f} on {store}',
            html_content=html_content
        )

        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        print(f"✅ Price alert sent to {email} (status: {response.status_code})")
        return True

    except Exception as e:
        print(f"❌ Error sending price alert: {str(e)}")
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
        dashboard_url      = f"{get_base_url()}/dashboard?token={token}"

        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO users (name, email, token, signup_date, status, trial_days_remaining,
                                   password_hash, email_verified, verification_token, newsletter)
                VALUES (%s, %s, %s, %s, 'active', 7, %s, FALSE, %s, %s)
                RETURNING id
            """, (data['name'], data['email'], token, datetime.now(),
                  password_hash, verification_token, newsletter))
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
        return jsonify({'error': str(e)}), 500


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
        return jsonify({'error': str(e)}), 500


@app.route('/api/login', methods=['POST'])
def login():
    """Login with email + password. Returns dashboard URL on success."""
    try:
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
        return jsonify({'error': str(e)}), 500


@app.route('/api/forgot-password', methods=['POST'])
def forgot_password():
    """Generate a one-time password reset token and email it to the user"""
    try:
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
        return jsonify({'error': str(e)}), 500


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
        return jsonify({'error': str(e)}), 500


@app.route('/api/dashboard', methods=['GET'])
def get_dashboard():
    """Get dashboard data for a user by token"""
    token = request.args.get('token')
    if not token:
        return jsonify({'error': 'Token required'}), 400

    user, products = get_user_by_token(token)
    if not user:
        return jsonify({'error': 'Invalid or expired token'}), 404

    signup_date = user['signup_date']
    if isinstance(signup_date, str):
        signup_date = datetime.fromisoformat(signup_date)
    days_elapsed = (datetime.now() - signup_date).days
    trial_days_remaining = max(0, 7 - days_elapsed)

    is_pro = bool(user.get('is_pro'))

    return jsonify({
        'success': True,
        'user': {
            'name': user['name'],
            'email': user['email'],
            'phone': user.get('phone') or '',
            'newsletter': user.get('newsletter', True),
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
        token = request.args.get('token')
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
        return jsonify({'error': str(e)}), 500


FREE_TIER_PRODUCT_LIMIT = 3   # Free/trial users can track up to this many products

@app.route('/api/add-product', methods=['POST'])
def add_product_to_dashboard():
    """Add a new product to user's tracking list"""
    try:
        token = request.args.get('token')
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
            cur.execute("""
                INSERT INTO products (user_id, url, target_price, store, added_date, status, current_price, alert_sent)
                VALUES (%s, %s, %s, %s, %s, 'monitoring', NULL, FALSE)
                RETURNING *
            """, (
                user['id'],
                data['url'],
                data.get('target_price') or None,
                get_store_name(data['url']),
                datetime.now()
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
        return jsonify({'error': str(e)}), 500


@app.route('/api/remove-product', methods=['DELETE'])
def remove_product():
    """Remove a product from user's tracking list"""
    try:
        token = request.args.get('token')
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
        return jsonify({'error': str(e)}), 500


@app.route('/api/check-prices', methods=['GET'])
def check_prices_for_user():
    """Check current prices for all of a user's products"""
    token = request.args.get('token')
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

            print(f"🔍 Checking price for: {url}")
            current_price = scrape_price(url)
            print(f"   → scrape_price returned: {current_price}")

            if current_price is not None:
                target = product.get('target_price')
                alert_sent = False

                if target and float(current_price) <= float(target):
                    alert_sent = True
                    send_price_drop_alert(
                        name=user['name'],
                        email=user['email'],
                        product_url=url,
                        current_price=current_price,
                        target_price=target,
                        store=product.get('store', 'the store'),
                        dashboard_url=dashboard_url
                    )
                    alerts_sent += 1
                    print(f"🔔 Alert sent for {user['email']} - price ${current_price} <= target ${target}")

                cur.execute("""
                    UPDATE products
                    SET current_price = %s,
                        last_checked = %s,
                        status = %s,
                        alert_sent = %s
                    WHERE id = %s AND user_id = %s
                """, (
                    current_price,
                    datetime.now(),
                    'alert_sent' if alert_sent else 'monitoring',
                    alert_sent,
                    product['id'],
                    user['id']
                ))

                product['current_price'] = current_price
                product['last_checked'] = datetime.now().isoformat()
                if alert_sent:
                    product['status'] = 'alert_sent'
                    product['alert_sent'] = True

            updated_products.append(product_to_dict(product))

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"❌ check-prices error: {e}")
        return jsonify({'error': str(e)}), 500
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

        html_content = f"""
        <html><body style="font-family: Arial, sans-serif; padding: 20px;">
        <h2 style="color: #667eea;">📬 New Contact Form Submission</h2>
        <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
        <tr><td style="padding: 10px; font-weight: bold; color: #555;">Name:</td><td style="padding: 10px;">{name}</td></tr>
        <tr style="background:#f9f9f9;"><td style="padding: 10px; font-weight: bold; color: #555;">Email:</td><td style="padding: 10px;"><a href="mailto:{email}">{email}</a></td></tr>
        <tr><td style="padding: 10px; font-weight: bold; color: #555;">Message:</td><td style="padding: 10px;">{message}</td></tr>
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
        <h2 style="color: #667eea;">✅ We got your message, {name}!</h2>
        <p style="color: #333;">Thank you for reaching out. Our team will get back to you within <strong>24 hours</strong>.</p>
        <div style="background: #f9f9f9; padding: 15px; border-radius: 8px; margin: 20px 0;">
        <p style="color: #666; font-size: 14px; margin: 0;"><strong>Your message:</strong><br>{message}</p>
        </div>
        <p style="color: #333; font-size: 14px;">Best regards,<br><strong>The DealNotify Team</strong><br>
        <a href="mailto:hello@dealnotify.co" style="color: #667eea;">hello@dealnotify.co</a> | <a href="https://www.dealnotify.co" style="color: #667eea;">www.dealnotify.co</a><br><br>
        💰 <em>Never miss a price drop again!</em></p>
        </div></body></html>
        """

        confirm_msg = Mail(
            from_email=from_email,
            to_emails=email,
            subject='✅ We received your message - DealNotify Support',
            html_content=confirm_html
        )
        sg.send(confirm_msg)

        print(f"📬 Contact form: {name} ({email})")
        return jsonify({'success': True}), 200

    except Exception as e:
        print(f"❌ Contact error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/signups', methods=['GET'])
def get_signups():
    """Get all signups (admin only)"""
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


@app.route('/admin')
def admin():
    """Admin dashboard"""
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) as cnt FROM users")
        total_users = _fetchone(cur)['cnt']

        cur.execute("SELECT COUNT(*) as cnt FROM products")
        total_products = _fetchone(cur)['cnt']

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
    h1 {{ color: #667eea; }}
    h2 {{ color: #333; margin-top: 30px; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
    th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; font-size: 13px; }}
    th {{ background-color: #667eea; color: white; }}
    tr:nth-child(even) {{ background-color: #f9f9f9; }}
    .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }}
    .stat-card {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 10px; }}
    .stat-number {{ font-size: 32px; font-weight: bold; }}
    .stat-label {{ font-size: 14px; margin-top: 10px; }}
    a {{ color: #667eea; }}
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
    </div>
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
    token = request.args.get('token')
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
        return jsonify({'error': str(e)}), 500


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


@app.route('/sitemap.xml')
def sitemap():
    from flask import Response
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://www.dealnotify.co/</loc>
    <lastmod>2026-03-31</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
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
    all active users and send email alerts whenever a price drops to target.
    """
    started_at = datetime.now().isoformat()
    print(f"\n⏰ === Hourly price check started at {started_at} ===")

    total_checked = 0
    total_alerts  = 0
    total_errors  = 0

    try:
        # Fetch all active users
        conn = get_db_conn()
        cur  = conn.cursor()
        try:
            cur.execute("SELECT * FROM users WHERE status = 'active'")
            users = _fetchall(cur)
        finally:
            cur.close()
            conn.close()

        print(f"   → {len(users)} active user(s) to check")

        for user in users:
            user_id      = user['id']
            token        = user['token']
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

                print(f"🔍 [{user['email']}] {url[:70]}")
                try:
                    current_price = scrape_price(url)
                    total_checked += 1
                except Exception as scrape_err:
                    print(f"   ❌ Scrape error: {scrape_err}")
                    total_errors += 1
                    continue

                if current_price is None:
                    print(f"   ⚠️  Price not found")
                    total_errors += 1
                    # Still record the check time even if price wasn't found
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

                # Check if price meets target
                target     = product.get('target_price')
                alert_sent = False
                if target and float(current_price) <= float(target):
                    alert_sent = True
                    send_price_drop_alert(
                        name=user['name'],
                        email=user['email'],
                        product_url=url,
                        current_price=current_price,
                        target_price=target,
                        store=product.get('store', 'the store'),
                        dashboard_url=dashboard_url
                    )
                    total_alerts += 1
                    print(f"   🔔 Alert sent — ${current_price} <= target ${target}")

                # Persist updated price to DB
                conn = get_db_conn()
                cur  = conn.cursor()
                try:
                    cur.execute("""
                        UPDATE products
                        SET current_price = %s,
                            last_checked  = %s,
                            status        = %s,
                            alert_sent    = %s
                        WHERE id = %s AND user_id = %s
                    """, (
                        current_price,
                        datetime.now(),
                        'alert_sent' if alert_sent else 'monitoring',
                        alert_sent,
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

    except Exception as e:
        print(f"❌ Hourly job fatal error: {e}")

    print(
        f"✅ Hourly check done — "
        f"{total_checked} checked, {total_alerts} alerts sent, {total_errors} errors\n"
    )
    return {'checked': total_checked, 'alerts': total_alerts, 'errors': total_errors}


@app.route('/api/check-all-prices', methods=['GET'])
def check_all_prices_admin():
    """
    Admin endpoint to manually trigger a full price check for all users.
    Protected by ADMIN_KEY env var — pass as ?key=<ADMIN_KEY> or X-Admin-Key header.
    """
    admin_key = request.args.get('key') or request.headers.get('X-Admin-Key', '')
    expected  = os.getenv('ADMIN_KEY', '')

    if not expected or admin_key != expected:
        return jsonify({'error': 'Unauthorized'}), 401

    # Run synchronously so the caller gets the result
    result = check_all_prices_job()
    return jsonify({'success': True, **result, 'triggered_at': datetime.now().isoformat()}), 200


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

    # Start hourly background price check scheduler
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
        print("⏰ Hourly price check scheduler started\n")
    except Exception as e:
        print(f"⚠️  Scheduler could not start: {e}\n")

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
