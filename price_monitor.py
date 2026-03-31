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
    Extract the main product price from page text/markdown.

    Strategy (in order):
    1. JSON-LD structured data — most reliable, sites embed machine-readable price
    2. Lines with strong buying-intent keywords, skipping coupon/savings context
    3. Bold/standalone prices in markdown
    4. Most-frequent price across all non-coupon lines (fallback)
    """
    import re
    from collections import Counter

    if not text:
        return None

    # Words that indicate a savings/coupon amount — NOT the actual product price
    COUPON_WORDS = re.compile(
        r'\b(?:off|save|saving|savings|coupon|clip|discount|you save|applied|reduction|rebate)\b',
        re.IGNORECASE
    )

    def valid_price(s):
        """Parse price string; return float only if in a sensible product range."""
        try:
            p = float(s.replace(',', ''))
            return p if 5.0 < p < 100000 else None
        except Exception:
            return None

    # ── Step 1: JSON-LD / schema.org structured data ────────────────────────
    # E-commerce pages embed machine-readable price in <script type="application/ld+json">
    # Firecrawl's `content` field often preserves this.
    json_ld_patterns = [
        r'"price"\s*:\s*"([\d,]+\.?\d*)"',   # "price": "29.99"
        r'"price"\s*:\s*([\d,]+\.?\d*)',       # "price": 29.99
        r'"lowPrice"\s*:\s*"?([\d,]+\.?\d*)"?',
        r'"highPrice"\s*:\s*"?([\d,]+\.?\d*)"?',
        r'priceAmount["\s:=]+([\d]+\.?\d*)',
    ]
    json_prices = []
    for pat in json_ld_patterns:
        for m in re.findall(pat, text):
            p = valid_price(m)
            if p:
                json_prices.append(p)
    if json_prices:
        best = Counter(json_prices).most_common(1)[0][0]
        print(f"   → JSON-LD prices found: {sorted(set(json_prices))} → using ${best}")
        return best

    # ── Step 2 & 3: Line-by-line scan ───────────────────────────────────────
    lines = text.split('\n')
    intent_prices = []   # prices on lines with buy-intent keywords
    bold_prices   = []   # bold/standalone prices in markdown
    all_prices    = []   # every price not on a coupon line

    for line in lines:
        # Skip lines that are clearly about savings/coupons
        if COUPON_WORDS.search(line):
            continue

        # Collect every $X.XX on this line
        raw = re.findall(r'\$\s*([\d,]+\.\d{2})', line)
        for r_val in raw:
            p = valid_price(r_val)
            if p:
                all_prices.append(p)

                # Buying-intent signals
                if re.search(
                    r'\b(?:price|buy new|buy now|add to cart|in stock|list price|our price|sale price|current price)\b',
                    line, re.IGNORECASE
                ):
                    intent_prices.append(p)

                # Bold price  **$XX.XX**  or price alone on its own line
                if re.search(r'\*\*\$\s*[\d,]+\.\d{2}\*\*', line) or re.match(r'^\s*\$\s*[\d,]+\.\d{2}\s*$', line):
                    bold_prices.append(p)

    # Return the best candidate in priority order
    for bucket in (intent_prices, bold_prices, all_prices):
        if bucket:
            best = Counter(bucket).most_common(1)[0][0]
            return best

    return None


def scrape_price(url):
    """
    Scrape the current price from a product URL using Firecrawl.

    Extraction order:
    1. Schema/extract key (Firecrawl LLM extraction — needs firecrawl-py>=1.0)
    2. JSON-LD + smart regex on combined content + markdown text
    """
    api_key = os.getenv('FIRECRAWL_API_KEY')
    if not api_key:
        print("❌ FIRECRAWL_API_KEY not set")
        return None

    try:
        fc = FirecrawlApp(api_key=api_key)

        price_schema = {
            "type": "object",
            "properties": {
                "price": {
                    "type": "string",
                    "description": "The current purchase price shown on the page (e.g. $29.99). Do NOT include crossed-out original prices or coupon amounts."
                }
            }
        }

        # Newer SDK uses keyword args; older SDK takes a dict — try both
        try:
            result = fc.scrape_url(url, formats=['extract', 'markdown'], extract={'schema': price_schema})
        except TypeError:
            result = fc.scrape_url(url, {
                'formats': ['extract', 'markdown'],
                'extract': {'schema': price_schema}
            })

        print(f"   → Firecrawl keys: {list(result.keys()) if result else 'None'}")

        if not result:
            print("   → Empty result from Firecrawl")
            return None

        # ── Method 1: LLM extract key ────────────────────────────────────
        for key in ('extract', 'data'):
            data = result.get(key)
            if isinstance(data, dict):
                price_val = data.get('price') or data.get('Price') or data.get('current_price')
                if price_val:
                    price_str = str(price_val).replace('$', '').replace(',', '').strip()
                    try:
                        p = float(price_str)
                        if 5.0 < p < 100000:
                            print(f"   ✅ Price from '{key}' extraction: ${p}")
                            return p
                    except Exception:
                        pass

        # ── Method 2: Smart regex on all text content ────────────────────
        # Combine content + markdown so JSON-LD in <script> tags is also searched
        combined = ' '.join(filter(None, [
            result.get('content', ''),
            result.get('markdown', ''),
        ]))

        if combined:
            # Log a snippet to help debug future issues
            snippet = combined[:500].replace('\n', ' ')
            print(f"   → Content snippet: {snippet[:200]}...")

            price = extract_price_from_text(combined[:8000])
            if price:
                print(f"   ✅ Price from text extraction: ${price}")
                return price

        print("   ⚠️ Could not extract price — no matching patterns found")
        return None

    except Exception as e:
        print(f"❌ Scraping error for {url}: {str(e)}")
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
