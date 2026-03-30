"""
Price Drop Alert Bot - Web App (Landing Page + Backend)
Database: PostgreSQL (via DATABASE_URL env var — provisioned by Railway)
"""

from flask import Flask, request, jsonify, send_from_directory
import os
import secrets
from datetime import datetime
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from dotenv import load_dotenv
from price_monitor import scrape_price
import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='')


# ─────────────────────────────────────────────
# DATABASE HELPERS
# ─────────────────────────────────────────────

def get_db_conn():
    """Get a PostgreSQL connection from DATABASE_URL"""
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        raise Exception("DATABASE_URL environment variable not set")
    # Railway sometimes gives postgres:// but psycopg2 needs postgresql://
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)


def init_db():
    """Create tables if they don't exist"""
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
        user = cur.fetchone()
        if not user:
            return None, None
        cur.execute("SELECT * FROM products WHERE user_id = %s ORDER BY added_date ASC", (user['id'],))
        products = cur.fetchall()
        return dict(user), [dict(p) for p in products]
    finally:
        cur.close()
        conn.close()


def get_user_by_email(email):
    """Fetch user by email"""
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        return dict(user) if user else None
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
    """Handle signup form submission"""
    try:
        data = request.json

        if not data.get('email') or not data.get('name'):
            return jsonify({'error': 'Email and name are required'}), 400

        # Check if email already exists
        existing = get_user_by_email(data['email'])
        if existing:
            return jsonify({'error': 'Email already registered'}), 400

        token = secrets.token_urlsafe(32)
        dashboard_url = f"{get_base_url()}/dashboard?token={token}"

        conn = get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO users (name, email, token, signup_date, status, trial_days_remaining)
                VALUES (%s, %s, %s, %s, 'active', 7)
                RETURNING id
            """, (data['name'], data['email'], token, datetime.now()))
            user_id = cur.fetchone()['id']

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

        print(f"\n✅ NEW SIGNUP!")
        print(f"   Name: {data['name']}")
        print(f"   Email: {data['email']}")
        print(f"   Product: {data.get('product_url', 'None')}")
        print(f"   Dashboard: {dashboard_url}")

        send_welcome_email(data['name'], data['email'], dashboard_url)

        return jsonify({
            'success': True,
            'message': 'Signup successful! Check your email for your dashboard link.',
            'dashboard_url': dashboard_url
        }), 200

    except Exception as e:
        print(f"❌ Signup error: {str(e)}")
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

    return jsonify({
        'success': True,
        'user': {
            'name': user['name'],
            'email': user['email'],
            'signup_date': signup_date.strftime('%Y-%m-%d'),
            'trial_days_remaining': trial_days_remaining,
            'status': user['status']
        },
        'products': [product_to_dict(p) for p in products]
    }), 200


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

        user, _ = get_user_by_token(token)
        if not user:
            return jsonify({'error': 'Invalid token'}), 404

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
            new_product = dict(cur.fetchone())
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
        users = [dict(u) for u in cur.fetchall()]
        result = []
        for u in users:
            cur.execute("SELECT * FROM products WHERE user_id = %s ORDER BY added_date ASC", (u['id'],))
            products = [dict(p) for p in cur.fetchall()]
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
        total_users = cur.fetchone()['cnt']

        cur.execute("SELECT COUNT(*) as cnt FROM products")
        total_products = cur.fetchone()['cnt']

        cur.execute("""
            SELECT u.id, u.name, u.email, u.signup_date, u.status, u.token,
                   COUNT(p.id) as product_count
            FROM users u
            LEFT JOIN products p ON p.user_id = u.id
            GROUP BY u.id
            ORDER BY u.signup_date DESC
        """)
        users = [dict(row) for row in cur.fetchall()]
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

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
