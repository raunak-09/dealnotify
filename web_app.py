"""
Price Drop Alert Bot - Web App (Landing Page + Backend)
"""

from flask import Flask, request, jsonify, send_from_directory
import json
import os
import secrets
from datetime import datetime
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='.', static_url_path='')

SIGNUPS_FILE = "signups.json"

def load_signups():
    """Load signups from file"""
    if os.path.exists(SIGNUPS_FILE):
        with open(SIGNUPS_FILE, 'r') as f:
            return json.load(f)
    return {"signups": []}

def save_signups(data):
    """Save signups to file"""
    with open(SIGNUPS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def get_base_url():
    """Get base URL for links"""
    return os.getenv('BASE_URL', 'https://www.dealnotify.co')

def get_store_name(url):
    """Extract store name from URL"""
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

def get_store_emoji(url):
    """Get emoji for store"""
    if not url:
        return '🛒'
    url_lower = url.lower()
    if 'amazon' in url_lower:
        return '📦'
    elif 'bestbuy' in url_lower:
        return '💻'
    elif 'walmart' in url_lower:
        return '🛒'
    elif 'target' in url_lower:
        return '🎯'
    elif 'ebay' in url_lower:
        return '🏷️'
    else:
        return '🛍️'

def send_welcome_email(name, email, dashboard_url):
    """Send welcome email to new customer via SendGrid"""
    try:
        api_key = os.getenv('SENDGRID_API_KEY')
        from_email = os.getenv('SENDGRID_FROM_EMAIL', 'manisha.jmc@gmail.com')

        if not api_key:
            print("⚠️ Warning: SENDGRID_API_KEY not found - welcome email not sent")
            return False

        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
        <div style="background-color: white; max-width: 600px; margin: 0 auto; padding: 30px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">

        <h1 style="color: #667eea; text-align: center;">🎉 Welcome, {name}!</h1>

        <p style="color: #333; font-size: 16px;">
        Thank you for signing up for <strong>Price Drop Alert Bot</strong>! We're now monitoring prices for you.
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
        <p style="color: #666; font-size: 12px; text-align: center;">If you have any questions, reply to this email.</p>
        <p style="color: #999; font-size: 12px; text-align: center;">© 2026 Price Drop Alert Bot. All rights reserved.</p>
        </div>
        </body>
        </html>
        """

        text_content = f"""
Welcome, {name}!

Thank you for signing up for Price Drop Alert Bot!

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

Questions? Reply to this email!

© 2026 Price Drop Alert Bot
        """

        message = Mail(
            from_email=from_email,
            to_emails=email,
            subject='🎉 Welcome to Price Drop Alert Bot! Here is your dashboard link',
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


@app.route('/')
def index():
    """Serve landing page"""
    return send_from_directory('.', 'index.html')


@app.route('/dashboard')
def dashboard():
    """Serve customer dashboard"""
    return send_from_directory('.', 'dashboard.html')


@app.route('/api/signup', methods=['POST'])
def signup():
    """Handle signup form submission"""
    try:
        data = request.json

        if not data.get('email') or not data.get('name'):
            return jsonify({'error': 'Email and name are required'}), 400

        signups = load_signups()

        for s in signups['signups']:
            if s['email'] == data.get('email'):
                return jsonify({'error': 'Email already registered'}), 400

        # Generate unique dashboard token
        token = secrets.token_urlsafe(32)
        dashboard_url = f"{get_base_url()}/dashboard?token={token}"

        new_signup = {
            'id': len(signups['signups']) + 1,
            'name': data.get('name'),
            'email': data.get('email'),
            'token': token,
            'products': [],
            'signup_date': datetime.now().isoformat(),
            'status': 'active',
            'trial_days_remaining': 7
        }

        # Add first product if provided
        if data.get('product_url'):
            new_signup['products'].append({
                'id': 1,
                'url': data.get('product_url', ''),
                'target_price': data.get('target_price', ''),
                'store': get_store_name(data.get('product_url', '')),
                'added_date': datetime.now().isoformat(),
                'status': 'monitoring',
                'last_checked': None,
                'current_price': None,
                'alert_sent': False
            })

        signups['signups'].append(new_signup)
        save_signups(signups)

        print(f"\n✅ NEW SIGNUP!")
        print(f"   Name: {data.get('name')}")
        print(f"   Email: {data.get('email')}")
        print(f"   Product: {data.get('product_url', 'None')}")
        print(f"   Dashboard: {dashboard_url}")

        send_welcome_email(data.get('name'), data.get('email'), dashboard_url)

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

    signups = load_signups()
    user = None
    for s in signups['signups']:
        if s.get('token') == token:
            user = s
            break

    if not user:
        return jsonify({'error': 'Invalid or expired token'}), 404

    # Calculate trial days remaining
    signup_date = datetime.fromisoformat(user['signup_date'])
    days_elapsed = (datetime.now() - signup_date).days
    trial_days_remaining = max(0, 7 - days_elapsed)

    return jsonify({
        'success': True,
        'user': {
            'name': user['name'],
            'email': user['email'],
            'signup_date': user['signup_date'][:10],
            'trial_days_remaining': trial_days_remaining,
            'status': user['status']
        },
        'products': user.get('products', [])
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

        signups = load_signups()
        user = None
        user_index = None
        for i, s in enumerate(signups['signups']):
            if s.get('token') == token:
                user = s
                user_index = i
                break

        if not user:
            return jsonify({'error': 'Invalid token'}), 404

        products = user.get('products', [])
        new_product = {
            'id': len(products) + 1,
            'url': data.get('url'),
            'target_price': data.get('target_price', ''),
            'store': get_store_name(data.get('url', '')),
            'added_date': datetime.now().isoformat(),
            'status': 'monitoring',
            'last_checked': None,
            'current_price': None,
            'alert_sent': False
        }

        products.append(new_product)
        signups['signups'][user_index]['products'] = products
        save_signups(signups)

        print(f"✅ Product added for {user['email']}: {data.get('url')}")

        return jsonify({
            'success': True,
            'message': 'Product added successfully!',
            'product': new_product
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

        signups = load_signups()
        user_index = None
        for i, s in enumerate(signups['signups']):
            if s.get('token') == token:
                user_index = i
                break

        if user_index is None:
            return jsonify({'error': 'Invalid token'}), 404

        products = signups['signups'][user_index].get('products', [])
        products = [p for p in products if str(p['id']) != str(product_id)]
        signups['signups'][user_index]['products'] = products
        save_signups(signups)

        return jsonify({'success': True, 'message': 'Product removed'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/signups', methods=['GET'])
def get_signups():
    """Get all signups (admin only)"""
    signups = load_signups()
    return jsonify(signups)


@app.route('/admin')
def admin():
    """Simple admin dashboard"""
    signups = load_signups()
    total_products = sum(len(s.get('products', [])) for s in signups['signups'])
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
    <div class="stat-number">{len(signups['signups'])}</div>
    <div class="stat-label">Total Signups</div>
    </div>
    <div class="stat-card">
    <div class="stat-number">{total_products}</div>
    <div class="stat-label">Products Tracked</div>
    </div>
    <div class="stat-card">
    <div class="stat-number">${len(signups['signups']) * 4.99:.2f}</div>
    <div class="stat-label">Potential Monthly Revenue</div>
    </div>
    </div>
    <h2>Signup List</h2>
    <table>
    <tr>
    <th>ID</th><th>Name</th><th>Email</th><th>Products</th><th>Signup Date</th><th>Status</th><th>Dashboard</th>
    </tr>
    """ + "".join([f"""
    <tr>
    <td>{s['id']}</td>
    <td>{s['name']}</td>
    <td>{s['email']}</td>
    <td>{len(s.get('products', []))}</td>
    <td>{s['signup_date'][:10]}</td>
    <td>{s['status']}</td>
    <td><a href="/dashboard?token={s.get('token', '')}" target="_blank">View</a></td>
    </tr>
    """ for s in signups['signups']]) + """
    </table>
    </div>
    </body>
    </html>
    """


if __name__ == '__main__':
    print("=" * 70)
    print("🚀 PRICE DROP ALERT BOT - WEB APP")
    print("=" * 70)
    print("\n📱 Landing Page: http://localhost:5000")
    print("📊 Admin Panel: http://localhost:5000/admin")
    print("\n💡 Press Ctrl+C to stop\n")

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
