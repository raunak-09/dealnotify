"""
Price Drop Alert Bot - Monitor product prices and alert users
"""

import os
import json
from datetime import datetime
from dotenv import load_dotenv
# FirecrawlApp / Firecrawl imported dynamically in _init_firecrawl()
# to support firecrawl-py v0, v1, and v2

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

def clean_url(url):
    """
    Strip tracking/redirect parameters AND URL fragments that can confuse scrapers.
    e.g. Walmart's ?adsRedirect=true, Amazon's ref= tags, Target's #lnk=sametab, etc.
    """
    from urllib.parse import urlparse, urlencode, parse_qs
    STRIP = {
        'adsRedirect', 'ref', 'ref_', 'tag', 'linkCode', 'linkId',
        'pf_rd_p', 'pf_rd_r', 'pd_rd_wg', 'pd_rd_w', 'pd_rd_r',
        'ascsubtag', 'smid', 'asc_refurl', 'asc_campaign',
        # NOTE: 'th' was removed — on Amazon it's the variant selector,
        # stripping it can load a different product variant at a different price.
    }
    try:
        parsed = urlparse(url)
        params = {k: v for k, v in parse_qs(parsed.query).items()
                  if k.lower() not in STRIP}
        # Strip fragment (#...) — it's never needed for scraping
        return parsed._replace(query=urlencode(params, doseq=True), fragment='').geturl()
    except Exception:
        return url


def _extract_jsonld_blocks(html):
    """Pull text from all <script type="application/ld+json"> tags in the HTML."""
    import re
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.IGNORECASE | re.DOTALL
    )
    return '\n'.join(blocks)


def _extract_meta_price(html):
    """
    Extract price from <meta> tags used by many retailers.
    Handles: product:price:amount, og:price:amount, itemprop="price"
    """
    import re
    patterns = [
        r'<meta[^>]+property=["\'](?:product|og):price:amount["\'][^>]+content=["\']([0-9.,]+)["\']',
        r'<meta[^>]+content=["\']([0-9.,]+)["\'][^>]+property=["\'](?:product|og):price:amount["\']',
        r'<meta[^>]+itemprop=["\']price["\'][^>]+content=["\']([0-9.,]+)["\']',
        r'<meta[^>]+content=["\']([0-9.,]+)["\'][^>]+itemprop=["\']price["\']',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            try:
                p = float(m.group(1).replace(',', ''))
                if 0.5 < p < 100000:
                    return p
            except Exception:
                pass
    return None


def _extract_amazon_price(html):
    """
    Amazon-specific price extraction.

    Priority order (most → least reliable):
      1. basisPrice  — the "real" shelf price before any coupon is applied
      2. listPrice   — MSRP / regular price, also pre-coupon
      3. priceToPay  — what the customer pays at checkout; MAY be post-coupon
      4. priceAmount / ourPrice / displayPrice — fallbacks

    We use a two-step window search for each key so we aren't tripped up by
    nested JSON objects: find the key, then scan the next N chars for "amount".
    """
    import re

    def valid_price(s):
        try:
            p = float(str(s).replace(',', '').replace('$', '').strip())
            return p if 0.5 < p < 100000 else None
        except Exception:
            return None

    def find_amount_after_key(key, text, window=400):
        """Find the first 'amount' value within `window` chars after `key`."""
        m = re.search(rf'"{key}"\s*:\s*\{{', text)
        if not m:
            return None
        snippet = text[m.start(): m.start() + window]
        am = re.search(r'"amount"\s*:\s*"?([\d,]+\.?\d*)"?', snippet)
        if am:
            return valid_price(am.group(1))
        return None

    def find_string_value(key, text):
        m = re.search(rf'"{key}"\s*:\s*"\$?([\d,]+\.?\d*)"', text)
        return valid_price(m.group(1)) if m else None

    # 1. basisPrice — real shelf price, ignore this only if it looks unreasonably high
    basis = find_amount_after_key('basisPrice', html)
    if basis:
        print(f"   → Amazon basisPrice: ${basis}")
        return basis

    # 2. listPrice
    list_p = find_amount_after_key('listPrice', html)
    if list_p:
        print(f"   → Amazon listPrice: ${list_p}")
        return list_p

    # 3. priceToPay — potentially post-coupon; use only when nothing better found
    pay = find_amount_after_key('priceToPay', html)
    print(f"   → Amazon priceToPay: ${pay}" if pay else "   → Amazon priceToPay: (none)")

    # Fallbacks
    fallbacks = [
        find_string_value('priceAmount', html),
        find_string_value('ourPrice', html),
        find_string_value('displayPrice', html),
        find_string_value('buyingPrice', html),
    ]
    for fb in fallbacks:
        if fb:
            print(f"   → Amazon fallback price: ${fb}")
            # If priceToPay and a fallback both exist, pick the HIGHER one
            # (coupon pages: priceToPay is lower; non-coupon: they should match)
            if pay and abs(pay - fb) > 0.5:
                best = max(pay, fb)
                print(f"   → Coupon likely — using higher price: ${best}")
                return best
            return fb

    # Nothing else found — return priceToPay as last resort
    if pay:
        return pay

    return None


def extract_price_from_text(html, markdown='', url=''):
    """
    Extract the main product price from page HTML and/or markdown.

    Strategy:
    • Amazon URLs → skip JSON-LD (which Amazon populates with post-coupon prices)
                    and go straight to basisPrice-first extraction.
    • All other URLs (in priority order):
      1. <meta> property price tags
      2. JSON-LD <script> blocks (priceCurrency proximity)
      3. Target-specific JSON fields
      4. Best Buy / generic patterns
      5. Full-text priceCurrency scan
      6. Markdown line-by-line scan
    """
    import re
    from collections import Counter

    def valid_price(s):
        try:
            p = float(str(s).replace(',', '').replace('$', '').strip())
            return p if 0.5 < p < 100000 else None
        except Exception:
            return None

    COUPON_WORDS = re.compile(
        r'\b(?:off|save|saving|savings|coupon|clip|discount|you save|applied|reduction|rebate|was)\b',
        re.IGNORECASE
    )

    is_amazon = bool(re.search(r'amazon\.(com|co\.uk|ca|com\.au|de|fr|it|es)', url or '', re.IGNORECASE))

    # ── Amazon fast-path ─────────────────────────────────────────────────────
    # Skip Steps 1 & 2 for Amazon: JSON-LD on Amazon often contains the
    # post-coupon price (priceToPay), not the shelf price. Go directly to the
    # basisPrice-first extractor which is tuned for Amazon's JS data structures.
    if is_amazon and html:
        print(f"   → Amazon URL detected — using basisPrice-first extraction")
        price = _extract_amazon_price(html)
        if price:
            return price
        print(f"   → Amazon extractor found nothing, falling through to generic steps")

    # ── Step 1: Meta tag price (most reliable for non-Amazon retailers) ───────
    if not is_amazon and html:
        p = _extract_meta_price(html)
        if p:
            print(f"   → Meta tag price: ${p}")
            return p

    # ── Step 2: JSON-LD blocks + priceCurrency proximity (skip for Amazon) ───
    jsonld = _extract_jsonld_blocks(html) if html else ''
    if not is_amazon:
        search_targets = [jsonld, html or '', markdown or '']
        for corpus in search_targets:
            if not corpus:
                continue
            offer_prices = []
            for m in re.finditer(r'"priceCurrency"\s*:\s*"USD"', corpus, re.IGNORECASE):
                window = corpus[max(0, m.start() - 600): m.end() + 600]
                for pat in (r'"price"\s*:\s*"([\d,]+\.?\d*)"',
                            r'"price"\s*:\s*([\d,]+\.?\d*)'):
                    pm = re.search(pat, window)
                    if pm:
                        p = valid_price(pm.group(1))
                        if p:
                            offer_prices.append(p)
                            break
            if offer_prices:
                best = Counter(offer_prices).most_common(1)[0][0]
                print(f"   → priceCurrency prices: {sorted(set(offer_prices))} → ${best}")
                return best

    # ── Step 3: Target-specific JSON patterns ────────────────────────────────
    if html:
        target_patterns = [
            r'"formatted_current_price"\s*:\s*"\$?([\d,]+\.?\d*)"',
            r'"current_retail"\s*:\s*([\d,]+\.?\d*)',
            r'"currentRetailPrice"\s*:\s*([\d,]+\.?\d*)',
            r'"reg_retail"\s*:\s*([\d,]+\.?\d*)',
            r'"price"\s*:\s*\{"value"\s*:\s*([\d,]+\.?\d*)',
        ]
        for pat in target_patterns:
            m = re.search(pat, html)
            if m:
                p = valid_price(m.group(1))
                if p:
                    print(f"   → Target-specific price pattern: ${p}")
                    return p

    # ── Step 4: Generic retailer JSON patterns ───────────────────────────────
    if html:
        generic_patterns = [
            r'"salePrice"\s*:\s*"?\$?([\d,]+\.?\d*)"?',
            r'"regularPrice"\s*:\s*"?\$?([\d,]+\.?\d*)"?',
            r'"currentPrice"\s*:\s*"?\$?([\d,]+\.?\d*)"?',
            r'"finalPrice"\s*:\s*"?\$?([\d,]+\.?\d*)"?',
            r'"sellingPrice"\s*:\s*"?\$?([\d,]+\.?\d*)"?',
            r'"specialPrice"\s*:\s*"?\$?([\d,]+\.?\d*)"?',
        ]
        for pat in generic_patterns:
            m = re.search(pat, html)
            if m:
                p = valid_price(m.group(1))
                if p:
                    print(f"   → Generic price pattern: ${p}")
                    return p

    # ── Step 5: Broader JSON "price" keys in JSON-LD ─────────────────────────
    for corpus in (jsonld, html or ''):
        if not corpus:
            continue
        json_prices = []
        for pat in (r'"price"\s*:\s*"([\d,]+\.\d{2})"',
                    r'"price"\s*:\s*([\d,]+\.\d{2})',
                    r'"lowPrice"\s*:\s*"?([\d,]+\.\d{2})"?'):
            for val in re.findall(pat, corpus):
                p = valid_price(val)
                if p:
                    json_prices.append(p)
        if json_prices:
            # Use the most common price (avoids outliers from related products)
            best = Counter(json_prices).most_common(1)[0][0]
            print(f"   → Broad JSON prices: {sorted(set(json_prices[:20]))} → ${best}")
            return best

    # ── Step 6: Markdown line-by-line scan ───────────────────────────────────
    if not markdown:
        return None

    lines = markdown.split('\n')
    intent_prices, bold_prices, all_prices = [], [], []

    for line in lines:
        if COUPON_WORDS.search(line):
            continue
        for r_val in re.findall(r'\$\s*([\d,]+\.\d{2})', line):
            p = valid_price(r_val)
            if not p:
                continue
            all_prices.append(p)
            if re.search(
                r'\b(?:price|buy new|buy now|add to cart|in stock|list price|our price|sale price|current price|now)\b',
                line, re.IGNORECASE
            ):
                intent_prices.append(p)
            if re.search(r'\*\*\$\s*[\d,]+\.\d{2}\*\*', line) or \
               re.match(r'^\s*\$\s*[\d,]+\.\d{2}\s*$', line):
                bold_prices.append(p)

    for bucket in (intent_prices, bold_prices):
        if bucket:
            best = Counter(bucket).most_common(1)[0][0]
            print(f"   → Markdown intent/bold price: ${best}")
            return best

    if all_prices:
        best = Counter(all_prices).most_common(1)[0][0]
        print(f"   → Markdown most-common price: ${best}")
        return best

    return None


def _init_firecrawl(api_key):
    """
    Return a (client, api_version) tuple.
    firecrawl-py 2.x  → class Firecrawl,    method .scrape()
    firecrawl-py 1.x  → class FirecrawlApp, method .scrape_url()
    firecrawl-py 0.x  → class FirecrawlApp, method .scrape_url() with dict arg
    """
    try:
        from firecrawl import Firecrawl
        return Firecrawl(api_key=api_key), 'v2'
    except ImportError:
        pass
    try:
        from firecrawl import FirecrawlApp
        return FirecrawlApp(api_key=api_key), 'v1'
    except ImportError:
        pass
    raise ImportError("firecrawl-py is not installed or has an unexpected API")


def _do_scrape(fc, api_version, url):
    """
    Call the correct Firecrawl scrape method for the detected API version.
    Returns a (markdown_text, content_text) tuple — both may be empty strings.
    """
    if api_version == 'v2':
        # firecrawl-py 2.x: fc.scrape(url, formats=[...]) → ScrapeResponse object
        resp = fc.scrape(url, formats=['markdown', 'html'])
        markdown = getattr(resp, 'markdown', None) or ''
        content  = getattr(resp, 'html', None) or getattr(resp, 'content', None) or ''
        return markdown, content
    else:
        # firecrawl-py 0.x / 1.x: fc.scrape_url(url, ...) → dict
        try:
            result = fc.scrape_url(url, formats=['markdown', 'html'])
        except TypeError:
            result = fc.scrape_url(url, {'formats': ['markdown', 'html']})
        if not isinstance(result, dict):
            return '', ''
        markdown = result.get('markdown') or result.get('content') or ''
        content  = result.get('html') or ''
        return markdown, content


def scrape_price(url):
    """
    Scrape the current price from a product URL using Firecrawl.
    Handles firecrawl-py v0, v1, and v2 API automatically.
    Strips tracking parameters before scraping to avoid redirect pages.
    """
    api_key = os.getenv('FIRECRAWL_API_KEY')
    if not api_key:
        print("❌ FIRECRAWL_API_KEY not set")
        return None

    # Strip tracking params (e.g. ?adsRedirect=true on Walmart URLs)
    clean = clean_url(url)
    if clean != url:
        print(f"   → Cleaned URL: {clean}")

    try:
        fc, api_version = _init_firecrawl(api_key)
        print(f"   → Using Firecrawl API {api_version}")

        markdown, html = _do_scrape(fc, api_version, clean)

        if not html and not markdown:
            print("   → Empty result from Firecrawl")
            return None

        snippet = (markdown or html or '')[:300].replace('\n', ' ')
        print(f"   → Content snippet: {snippet}...")
        print(f"   → HTML length: {len(html)} chars, Markdown length: {len(markdown)} chars")

        price = extract_price_from_text(html, markdown, url=clean)
        if price:
            print(f"   ✅ Price: ${price}")
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
