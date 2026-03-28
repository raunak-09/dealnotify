"""
Test Email Alert System
Send yourself a test email to verify it works
"""

from email_alerts import test_email
import os

print("=" * 70)
print("📧 EMAIL ALERT SYSTEM TEST")
print("=" * 70)

# Get your email
your_email = input("\n📨 Enter your email address: ").strip()

if not your_email or '@' not in your_email:
    print("❌ Invalid email address!")
    exit(1)

print(f"\n📧 Sending test email to: {your_email}")
print("⏳ Please wait...\n")

success = test_email(your_email)

if success:
    print("\n" + "=" * 70)
    print("✅ TEST EMAIL SENT SUCCESSFULLY!")
    print("=" * 70)
    print(f"\n📨 Check your email at {your_email}")
    print("   Look in INBOX or SPAM folder")
    print("\n💡 If you received the email, your system is ready!")
    print("\n🚀 Next step: Run the price monitor with real alerts")
else:
    print("\n" + "=" * 70)
    print("❌ TEST EMAIL FAILED")
    print("=" * 70)
    print("""
Troubleshooting:
1. Make sure SENDGRID_API_KEY is in .env file
2. Restart this script after editing .env
3. Check email address is correct
4. Try generating a new API key from SendGrid
    """)

print("\n" + "=" * 70)
