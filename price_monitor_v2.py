"""
Price Drop Alert Bot - Improved Version 2
Better scraping strategy for different websites
"""

import os
import json
import re
from datetime import datetime
from dotenv import load_dotenv
from firecrawl import FirecrawlApp

load_dotenv()
DB_FILE = "price_data.json"

def load_database():
    """Load the product database"""
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    return {"products": []}

def save_database(data):
    """Save the product database"""
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def extract_price_from_text(text):
    """
    Extract price from text using regex
    Looks for patterns like $19.99, $1,299.99, etc.
    """
    if not text:
        return None
    
    # Find price patterns like $19.99 or $1,299.99
    price_pattern = r'\$[\d,]+\.?\d*'
    matches = re.findall(price_pattern, text)
    
    if matches:
        # Get the first (usually the main product price)
        price_str = matches[0].replace('$', '').replace(',', '')
        try:
            return float(price_str)
        except:
            return None
    return None

def scrape_price_improved(url):
    """
    Improved price scraping with multiple strategies
    """
    api_key = os.getenv('FIRECRAWL_API_KEY')
    
    if not api_key:
        print("❌ Error: FIRECRAWL_API_KEY not found")
        return None
    
    try:
        app = FirecrawlApp(api_key=api_key)
        
        print(f"   🔄 Scraping {url[:50]}...")
        
        # Strategy 1: Get markdown (simplified content)
        result = app.scrape_url(url, {
            'formats': ['markdown', 'extract'],
            'extract': {
                'schema': {
                    "type": "object",
                    "properties": {
                        "price": {
                            "type": "string",
                            "description": "Product price"
                        }
                    }
                }
            }
        })
        
        if result:
            # Try extracted data first
            if 'extract' in result and 'price' in result['extract']:
                price = result['extract'].get('price')
                if price:
                    extracted_price = extract_price_from_text(price)
                    if extracted_price:
                        return extracted_price
            
            # Try markdown content
            if 'markdown' in result:
                text = result['markdown']
                price = extract_price_from_text(text)
                if price:
                    return price
        
        return None
        
    except Exception as e:
        print(f"   ❌ Error: {str(e)}")
        return None

def add_product(product_name, url, target_price, email):
    """Add a product to monitor"""
    db = load_database()
    
    print(f"\n📦 Adding: {product_name}")
    
    # Try to get current price
    current_price = scrape_price_improved(url)
    
    if current_price is None:
        print(f"   ⚠️ Could not scrape price. Using target price as current.")
        current_price = target_price
    else:
        print(f"   ✅ Scraped price: ${current_price}")
    
    product = {
        "id": len(db["products"]) + 1,
        "name": product_name,
        "url": url,
        "target_price": target_price,
        "current_price": current_price,
        "email": email,
        "added_date": datetime.now().isoformat(),
        "price_history": [{
            "price": current_price,
            "date": datetime.now().isoformat()
        }],
        "alert_sent": False
    }
    
    db["products"].append(product)
    save_database(db)
    
    print(f"   💾 Saved to database")
    return product

def check_all_prices():
    """Check prices for all products"""
    db = load_database()
    alerts = []
    
    print("\n🔍 Checking prices...")
    print("=" * 70)
    
    for product in db["products"]:
        print(f"\n📦 {product['name']}")
        current_price = scrape_price_improved(product['url'])
        
        if current_price is None:
            print(f"   ⚠️ Could not scrape (retaining last known price: ${product['current_price']})")
            continue
        
        old_price = product['current_price']
        product['current_price'] = current_price
        product['price_history'].append({
            "price": current_price,
            "date": datetime.now().isoformat()
        })
        
        price_change = old_price - current_price
        
        print(f"   Old: ${old_price} → New: ${current_price}")
        
        if price_change > 0:
            print(f"   📉 Price DROPPED ${price_change:.2f}")
        elif price_change < 0:
            print(f"   📈 Price INCREASED ${abs(price_change):.2f}")
        else:
            print(f"   ➡️ Price UNCHANGED")
        
        # Check if below target
        if current_price <= product['target_price']:
            print(f"   🎉 ALERT! Price is at or below target (${product['target_price']})")
            alerts.append({
                "product": product['name'],
                "current_price": current_price,
                "target_price": product['target_price'],
                "email": product['email'],
                "url": product['url'],
                "savings": old_price - current_price
            })
    
    save_database(db)
    return alerts

def view_all_products():
    """Display all products"""
    db = load_database()
    
    if not db["products"]:
        print("\n📭 No products being monitored.")
        return
    
    print("\n" + "=" * 70)
    print("📊 MONITORED PRODUCTS")
    print("=" * 70)
    
    for product in db["products"]:
        status = "✅" if product['current_price'] > product['target_price'] else "🎉"
        print(f"\n{status} {product['name']}")
        print(f"   Current:  ${product['current_price']}")
        print(f"   Target:   ${product['target_price']}")
        print(f"   Email:    {product['email']}")

if __name__ == "__main__":
    print("Testing improved price scraper...")
