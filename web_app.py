"""
Price Drop Alert Bot - Web App (Landing Page + Backend)
"""

from flask import Flask, render_template, request, jsonify, send_from_directory
import json
import os
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

def send_welcome_email(name, email):
    """Send welcome email to new customer via SendGrid"""
    try:
        api_key = os.getenv('SENDGRID_API_KEY')
        from_email = os.getenv('SENDGRID_FROM_EMAIL', 'manisha.jmc@gmail.com')

        if not api_key:
            print("⚠️ Warning: SENDGRID_API_KEY not found - welcome email not sent")
            return False

        # HTML content
        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
        <div style="background-color: white; max-width: 600px; margin: 0 auto; padding: 30px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">

        <h1 style="color: #667eea; text-align: center;">🎉 Welcome, {name}!</h1>

        <p style="color: #333; font-size: 16px;">
        Thank you for signing up for <strong>Price Drop Alert Bot</strong>!
        </p>

        <div style="background-color: #f9f9f9; padding: 20px; border-radius: 5px; margin: 20px 0;">
        <h2 style="color: #667eea; margin-top: 0;">🚀 Getting Started</h2>
        <ol style="color: #333;">
        <li><strong>Add Products</strong> - Tell us which products to monitor</li>
        <li><strong>Set Target Prices</strong> - Choose your desired price points</li>
        <li><strong>Get Alerts</strong> - Receive instant email notifications when prices drop</li>
        </ol>
        </div>

        <div style="background-color: #f0f7ff; padding: 20px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #667eea;">
        <h3 style="color: #667eea; margin-top: 0;">💝 Your Free Trial</h3>
        <p style="color: #333;">
        You have <strong>7 days free</strong> to try all features!
        </p>
        <p style="color: #666; font-size: 14px;">
        After that, it's just <strong>$4.99/month</strong> for unlimited monitoring.
        </p>
        </div>

        <div style="background-color: #fff3cd; padding: 15px; border-radius: 5px; margin: 20px 0;">
        <p style="color: #856404; margin: 0; font-size: 14px;">
        <strong>💡 Pro Tip:</strong> Monitor Best Buy, Amazon, Walmart, Target and more for the best deals!
        </p>
        </div>

        <hr style="border: none; border-top: 2px solid #eee; margin: 30px 0;">

        <p style="color: #666; font-size: 12px; text-align: center;">
        If you have any questions, reply to this email or visit our website.
        </p>
        <p style="color: #999; font-size: 12px; text-align: center;">
        © 2026 Price Drop Alert Bot. All rights reserved.
        </p>
        </div>
        </body>
        </html>
        """

        # Plain text version
        text_content = f"""
        Welcome, {name}!

        Thank you for signing up for Price Drop Alert Bot!

        GETTING STARTED:
        1. Add Products - Tell us which products to monitor
        2. Set Target Prices - Choose your desired price points
        3. Get Alerts - Receive instant email notifications when prices drop

        YOUR FREE TRIAL:
        You have 7 days free to try all features!
        After that, it's just $4.99/month for unlimited monitoring.

        Pro Tip: Monitor Best Buy, Amazon, Walmart, Target and more for the best deals!

        Questions? Reply to this email!

        © 2026 Price Drop Alert Bot
        """

        message = Mail(
            from_email=from_email,
            to_emails=email,
            subject='🎉 Welcome to Price Drop Alert Bot!',
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

@app.route('/api/signup', methods=['POST'])
def signup():
    """Handle signup form submission"""
    try:
        data = request.json

        # Validate data
        if not data.get('email') or not data.get('name'):
            return jsonify({'error': 'Email and name are required'}), 400

        # Load signups
        signups = load_signups()

        # Check if email already exists
        for signup in signups['signups']:
            if signup['email'] == data.get('email'):
                return jsonify({'error': 'Email already registered'}), 400

        # Create new signup
        new_signup = {
            'id': len(signups['signups']) + 1,
            'name': data.get('name'),
            'email': data.get('email'),
            'product_url': data.get('product_url', ''),
            'target_price': data.get('target_price', ''),
            'signup_date': datetime.now().isoformat(),
            'status': 'active',
            'trial_days_remaining': 7
        }

        # Add to signups
        signups['signups'].append(new_signup)
        save_signups(signups)

        print(f"\n✅ NEW SIGNUP!")
        print(f" Name: {data.get('name')}")
        print(f" Email: {data.get('email')}")
        print(f" Product: {data.get('product_url', 'None')}")

        # SEND WELCOME EMAIL
        send_welcome_email(data.get('name'), data.get('email'))

        return jsonify({
            'success': True,
            'message': 'Signup successful! Check your email for next steps.'
        }), 200

    except Exception as e:
        print(f"❌ Signup error: {str(e)}")
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
    th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
    th {{ background-color: #667eea; color: white; }}
    tr:nth-child(even) {{ background-color: #f9f9f9; }}
    .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }}
    .stat-card {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 10px; }}
    .stat-number {{ font-size: 32px; font-weight: bold; }}
    .stat-label {{ font-size: 14px; margin-top: 10px; }}
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
    <div class="stat-number">${len(signups['signups']) * 4.99:.2f}</div>
    <div class="stat-label">Monthly Revenue (if all convert)</div>
    </div>
    </div>

    <h2>Signup List</h2>
    <table>
    <tr>
    <th>ID</th>
    <th>Name</th>
    <th>Email</th>
    <th>Product URL</th>
    <th>Target Price</th>
    <th>Signup Date</th>
    <th>Status</th>
    </tr>
    """ + "".join([f"""
    <tr>
    <td>{s['id']}</td>
    <td>{s['name']}</td>
    <td>{s['email']}</td>
    <td>{s.get('product_url', '-')}</td>
    <td>${s.get('target_price', '-')}</td>
    <td>{s['signup_date'][:10]}</td>
    <td>{s['status']}</td>
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
