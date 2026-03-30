"""
Price Drop Alert Bot - Monitor product prices and alert users
"""

import os
import json
from datetime import datetime
from dotenv import load_dotenv
from firecrawl import FirecrawlApp

# Load environment variables
load_dotenv()

# Database file to store products and prices
DB_FILE = "price_data.json"

def load_database():
    """Load the product database from JSON file"""
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    return {"products": []}

def save_database(data):
    """Save the product database to JSON file"""
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def extract_price_from_text(text):
    """
    Extract the main product price from markdown text.
    Prioritises prices near keywords like 'price', 'buy', 'add to cart'.
    Skips coupon/discount amounts and very low prices.
    """
    import re
    if not text:
        return None

    # Step 1: Look for price near strong buying-intent keywords (most reliable)
    priority_patterns = [
        r'(?:current price|sale price|our price|buy new|price)[^\n]{0,30}\$\s*([\d,]+\.\d{2})',
        r'\$\s*([\d,]+\.\d{2})\s*(?:add to cart|buy now|in stock)',
        r'(?:^\s*|\*\*)\$\s*([\d,]+\.\d{2})(?:\*\*|\s*$)',  # bold/prominent price in markdown
    ]
    for pattern in priority_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        for match in matches:
            try:
                price = float(match.replace(',', ''))
                if 5.0 < price < 100000:  # skip prices under $5 (coupons/shipping)
                    return price
            except:
                continue

    # Step 2: Collect ALL prices in the text and return the most frequent / prominent one
    all_prices = re.findall(r'\$\s*([\d,]+\.\d{2})', text)
    candidates = []
    for p in all_prices:
        try:
            price = float(p.replace(',', ''))
            if 5.0 < price < 100000:
                candidates.append(price)
        except:
            continue

    if candidates:
        # Return the most frequently occurring price (most likely the main product price)
        from collections import Counter
        counter = Counter(candidates)
        return counter.most_common(1)[0][0]

    return None


def scrape_price(url):
    """
    Scrape the current price from a URL using Firecrawl.
    Tries schema extraction first, falls back to markdown regex parsing.

    Args:
        url (str): Product URL to scrape

    Returns:
        float: Current price, or None if failed
    """
    api_key = os.getenv('FIRECRAWL_API_KEY')

    if not api_key:
        print("❌ Error: FIRECRAWL_API_KEY not found")
        return None

    try:
        app = FirecrawlApp(api_key=api_key)

        price_schema = {
            "type": "object",
            "properties": {
                "price": {
                    "type": "string",
                    "description": "The current sale price of the product (e.g., $19.99)"
                },
                "product_name": {
                    "type": "string",
                    "description": "The product name or title"
                }
            }
        }

        # Try newer SDK format first, fall back to older format
        try:
            result = app.scrape_url(url, formats=['extract', 'markdown'], extract={'schema': price_schema})
        except TypeError:
            result = app.scrape_url(url, {
                'formats': ['extract', 'markdown'],
                'extract': {'schema': price_schema}
            })

        print(f"   → Firecrawl result keys: {list(result.keys()) if result else 'None'}")

        if not result:
            print("   → No result from Firecrawl")
            return None

        # Method 1: 'extract' key
        if 'extract' in result and result['extract']:
            data = result['extract']
            print(f"   → Extract data: {data}")
            price_val = data.get('price') or data.get('Price') or data.get('current_price')
            if price_val:
                price_str = str(price_val).replace('$', '').replace(',', '').strip()
                try:
                    price = float(price_str)
                    print(f"   ✅ Price from extract: ${price}")
                    return price
                except:
                    pass

        # Method 2: 'data' key (some SDK versions)
        if 'data' in result and isinstance(result['data'], dict):
            data = result['data']
            price_val = data.get('price') or data.get('Price')
            if price_val:
                price_str = str(price_val).replace('$', '').replace(',', '').strip()
                try:
                    price = float(price_str)
                    print(f"   ✅ Price from data: ${price}")
                    return price
                except:
                    pass

        # Method 3: Regex on markdown
        markdown = result.get('markdown') or result.get('content') or ''
        if markdown:
            price = extract_price_from_text(markdown[:3000])
            if price:
                print(f"   ✅ Price from markdown regex: ${price}")
                return price

        print("   ⚠️ Could not extract price from any method")
        return None

    except Exception as e:
        print(f"❌ Error scraping {url}: {str(e)}")
        return None

def add_product(product_name, url, target_price, email):
    """
    Add a product to monitor
    
    Args:
        product_name (str): Name of the product
        url (str): Product URL
        target_price (float): Price alert threshold
        email (str): User's email for alerts
    """
    db = load_database()
    
    # Get current price
    current_price = scrape_price(url)
    
    if current_price is None:
        print(f"⚠️ Warning: Could not scrape initial price from {url}")
        current_price = target_price
    
    product = {
        "id": len(db["products"]) + 1,
        "name": product_name,
        "url": url,
        "target_price": target_price,
        "current_price": current_price,
        "email": email,
        "added_date": datetime.now().isoformat(),
        "price_history": [
            {
                "price": current_price,
                "date": datetime.now().isoformat()
            }
        ],
        "alert_sent": False
    }
    
    db["products"].append(product)
    save_database(db)
    
    print(f"✅ Added: {product_name}")
    print(f"   Current Price: ${current_price}")
    print(f"   Target Price: ${target_price}")
    print(f"   Alert Email: {email}")
    return product

def check_all_prices():
    """
    Check prices for all tracked products and send alerts if needed
    """
    db = load_database()
    alerts_to_send = []
    
    print("\n🔍 Checking prices for all products...")
    print("=" * 60)
    
    for product in db["products"]:
        print(f"\nChecking: {product['name']}")
        current_price = scrape_price(product['url'])
        
        if current_price is None:
            print(f"⚠️ Could not scrape current price")
            continue
        
        old_price = product['current_price']
        product['current_price'] = current_price
        product['price_history'].append({
            "price": current_price,
            "date": datetime.now().isoformat()
        })
        
        print(f"   Old Price: ${old_price}")
        print(f"   New Price: ${current_price}")
        
        # Check if price dropped below target
        if current_price <= product['target_price']:
            print(f"   🎉 ALERT! Price dropped below target!")
            alerts_to_send.append({
                "product": product['name'],
                "current_price": current_price,
                "target_price": product['target_price'],
                "email": product['email'],
                "url": product['url'],
                "savings": old_price - current_price
            })
    
    save_database(db)
    
    # Send alerts
    if alerts_to_send:
        print(f"\n📧 Sending {len(alerts_to_send)} price drop alerts...")
        for alert in alerts_to_send:
            send_alert(alert)
    
    return alerts_to_send

def send_alert(alert):
    """
    Send a price drop alert (currently prints to console)
    
    TODO: Integrate email or Telegram notification
    """
    print(f"""
    ═══════════════════════════════════════════════════════════
    📧 PRICE DROP ALERT FOR: {alert['product']}
    ═══════════════════════════════════════════════════════════
    
    New Price:    ${alert['current_price']}
    Target Price: ${alert['target_price']}
    You Save:     ${alert['savings']:.2f}
    
    Buy Now: {alert['url']}
    
    Sent to: {alert['email']}
    ═══════════════════════════════════════════════════════════
    """)

def view_all_products():
    """Display all monitored products"""
    db = load_database()
    
    if not db["products"]:
        print("No products being monitored yet.")
        return
    
    print("\n📊 All Monitored Products:")
    print("=" * 80)
    
    for product in db["products"]:
        status = "✅ Active" if product['current_price'] > product['target_price'] else "🎉 Alert!"
        print(f"""
ID: {product['id']} - {status}
Product: {product['name']}
Current Price: ${product['current_price']}
Target Price: ${product['target_price']}
Email: {product['email']}
URL: {product['url']}
""")

def demo():
    """Run a demo with sample products"""
    print("\n🎬 DEMO MODE - Adding sample products...\n")
    
    # Example products
    add_product(
        product_name="Apple AirPods Pro",
        url="https://www.amazon.com/Apple-AirPods-Latest-Model/dp/B09JQMJHXY",
        target_price=150.00,
        email="user@example.com"
    )
    
    add_product(
        product_name="Sony WH-1000XM5 Headphones",
        url="https://www.amazon.com/Sony-WH-1000XM5-Canceling-Headphones-Phone-Call/dp/B09RMTD726",
        target_price=300.00,
        email="user@example.com"
    )
    
    print("\n✅ Demo products added!")
    print("Run check_all_prices() to monitor them")

if __name__ == "__main__":
    # Run demo
    demo()
    
    # View products
    view_all_products()
    
    # Check prices
    check_all_prices()
