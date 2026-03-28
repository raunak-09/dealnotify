# 📧 Email Alert Setup Guide

## Complete Steps to Get Email Alerts Working

### Step 1: Create SendGrid Account (2 minutes)

1. Go to **https://sendgrid.com/free**
2. Sign up with your email
3. Verify your email address
4. You'll be taken to the SendGrid dashboard

### Step 2: Create API Key

1. In SendGrid dashboard, go to **Settings → API Keys**
2. Click **"Create API Key"**
3. Name it: `Price Drop Bot`
4. Select **Full Access** (or at minimum: Mail Send)
5. Click **Create & Copy**
6. Copy the key (starts with `SG.`)

### Step 3: Add API Key to .env File

1. Open your `.env` file:
   ```bash
   nano .env
   ```

2. Add this line (paste your actual API key):
   ```
   SENDGRID_API_KEY=SG.your_actual_key_here
   ```

3. Save the file:
   - Press: `Ctrl + X`
   - Press: `Y`
   - Press: `Enter`

### Step 4: Install SendGrid Library

```bash
cd "/Users/ronakclawdbot/Documents/Claude Projects/firecrawl-scraper"
pip3 install sendgrid
```

### Step 5: Test Email System

```bash
python3 test_email_alert.py
```

You should receive a test email!

### Step 6: Use in Price Monitor

The email system is now integrated! When prices drop:
- ✅ Price is checked automatically
- ✅ Email is sent automatically
- ✅ Beautiful formatted email arrives in inbox

---

## 📊 What the Email Looks Like

When a price drops, customers receive an email with:
- ✅ Product name and image
- ✅ Old price (strikethrough)
- ✅ New price (highlighted green)
- ✅ Amount saved
- ✅ Direct link to buy
- ✅ Professional formatting

---

## 🔧 Troubleshooting

**"Error: SENDGRID_API_KEY not found"**
- Make sure you added it to `.env` file
- Restart Python script after editing `.env`

**"Email failed with status 401"**
- Your API key is wrong or expired
- Generate a new one from SendGrid dashboard

**"Email failed with status 429"**
- You've hit the SendGrid rate limit (100 emails/day on free tier)
- Wait 24 hours or upgrade plan

**Not receiving test email?**
- Check spam folder
- Check email address is correct
- Verify API key is correct

---

## 💡 Free Tier Limits

SendGrid free account includes:
- ✅ 100 emails per day
- ✅ Up to 30 days of email history
- ✅ Full API access
- ✅ Enough to start your business

**Perfect for** first 100 customers or testing!

When you scale, upgrade to paid plan ($19.95/month for unlimited).

---

## 📈 Next Steps

1. ✅ Test email system works
2. ✅ Set up daily price checks (cron job)
3. ✅ Create landing page
4. ✅ Add Stripe payments
5. ✅ Deploy to web
6. ✅ Start marketing
7. ✅ Get paying customers!

You're close! Email alerts are the last piece before you can launch! 🚀
