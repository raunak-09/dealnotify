"""
Test Improved Price Monitor - Nintendo Amiibo from Best Buy
"""

from price_monitor_v2 import add_product, check_all_prices, view_all_products

print("=" * 70)
print("🎮 IMPROVED PRICE MONITOR TEST")
print("Nintendo Amiibo - Captain Toad (Best Buy)")
print("=" * 70)

# Clear old data (fresh start)
import os
if os.path.exists("price_data.json"):
    os.remove("price_data.json")
    print("🗑️  Cleared old data\n")

# Add the Nintendo Amiibo
product = add_product(
    product_name="Nintendo Amiibo - Captain Toad (Best Buy)",
    url="https://www.bestbuy.com/product/nintendo-amiibo-captain-toad-talking-flower-super-mario-bros-wonder-series-multi/J7GSL5J8J3",
    target_price=19.99,  # Alert if price drops below $19.99
    email="your-email@example.com"
)

# View the product
view_all_products()

# Check the price
print("\n" + "=" * 70)
print("TESTING PRICE CHECK")
print("=" * 70)
alerts = check_all_prices()

print("\n" + "=" * 70)
if alerts:
    print(f"🎉 ALERTS: {len(alerts)} price drop(s) detected!")
    for alert in alerts:
        print(f"   - {alert['product']}: ${alert['current_price']} (Target: ${alert['target_price']})")
else:
    print("✓ No price drops detected")

print("\n📝 Actual Best Buy price: $24.99")
print("If the scraper found the correct price, great!")
print("If not, we can adjust the scraping strategy.")
