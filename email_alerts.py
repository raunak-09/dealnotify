"""
Email Alert System - Using Gmail SMTP
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

def send_price_drop_email(alert_data):
    """
    Send a price drop alert email using Gmail SMTP
    """
    
    # Gmail credentials
    sender_email = "manisha.jmc@gmail.com"
    sender_password = os.getenv('GMAIL_PASSWORD')
    
    if not sender_password:
        print("❌ Error: GMAIL_PASSWORD not found in .env")
        return False
    
    try:
        # Create email message
        message = MIMEMultipart("alternative")
        message["Subject"] = f"🎉 Price Drop Alert: {alert_data['product']}"
        message["From"] = sender_email
        message["To"] = alert_data['email']
        
        # HTML content
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
                <div style="background-color: white; max-width: 600px; margin: 0 auto; padding: 30px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    
                    <h1 style="color: #27ae60; text-align: center;">🎉 Price Drop Alert!</h1>
                    
                    <div style="border-bottom: 2px solid #f0f0f0; padding: 20px 0;">
                        <h2 style="color: #333; margin: 0 0 10px 0;">{alert_data['product']}</h2>
                        <p style="color: #666; margin: 0;">Your price alert has been triggered!</p>
                    </div>
                    
                    <div style="padding: 20px 0; text-align: center;">
                        <div style="margin: 20px 0;">
                            <p style="color: #999; font-size: 14px; margin: 0;">Previous Price</p>
                            <p style="color: #666; font-size: 18px; margin: 5px 0; text-decoration: line-through;">${alert_data.get('old_price', 'N/A')}</p>
                        </div>
                        
                        <div style="background-color: #27ae60; color: white; padding: 20px; border-radius: 5px; margin: 20px 0;">
                            <p style="color: white; font-size: 14px; margin: 0;">Current Price</p>
                            <h1 style="color: white; margin: 10px 0; font-size: 48px;">${alert_data['current_price']}</h1>
                        </div>
                        
                        <div style="margin: 20px 0;">
                            <p style="color: #27ae60; font-size: 16px; font-weight: bold;">
                                💰 You Save: ${alert_data.get('savings', 0):.2f}
                            </p>
                        </div>
                    </div>
                    
                    <div style="background-color: #f9f9f9; padding: 15px; border-radius: 5px; margin: 20px 0;">
                        <p style="color: #666; font-size: 14px; margin: 0;">
                            <strong>Target Price:</strong> ${alert_data['target_price']}
                        </p>
                    </div>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{alert_data['url']}" style="display: inline-block; background-color: #3498db; color: white; padding: 12px 30px; text-decoration: none; border-radius: 5px; font-weight: bold;">
                            🛒 Buy Now
                        </a>
                    </div>
                    
                    <div style="border-top: 2px solid #f0f0f0; padding-top: 20px; font-size: 12px; color: #999;">
                        <p style="margin: 0;">
                            This is an automated price drop alert from <strong>Price Drop Alert Bot</strong>.
                        </p>
                    </div>
                </div>
            </body>
        </html>
        """
        
        # Plain text version
        text_content = f"""
        🎉 PRICE DROP ALERT!
        
        Product: {alert_data['product']}
        Previous Price: ${alert_data.get('old_price', 'N/A')}
        Current Price: ${alert_data['current_price']}
        
        💰 You Save: ${alert_data.get('savings', 0):.2f}
        Target Price: ${alert_data['target_price']}
        
        Buy Now: {alert_data['url']}
        """
        
        # Attach both plain text and HTML
        part1 = MIMEText(text_content, "plain")
        part2 = MIMEText(html_content, "html")
        message.attach(part1)
        message.attach(part2)
        
        # Send email via Gmail SMTP
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, alert_data['email'], message.as_string())
        
        print(f"✅ Email sent to {alert_data['email']}")
        return True
            
    except Exception as e:
        print(f"❌ Error sending email: {str(e)}")
        return False

def test_email(email_address):
    """Test email alert system"""
    print(f"📧 Sending test email to {email_address}...")
    
    test_alert = {
        'product': 'Nintendo Amiibo - Captain Toad',
        'current_price': 19.99,
        'old_price': 24.99,
        'savings': 5.00,
        'target_price': 19.99,
        'email': email_address,
        'url': 'https://www.bestbuy.com/product/nintendo-amiibo-captain-toad-talking-flower-super-mario-bros-wonder-series-multi/J7GSL5J8J3'
    }
    
    return send_price_drop_email(test_alert)

if __name__ == "__main__":
    test_email("jainr3790@gmail.com")
