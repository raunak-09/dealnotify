"""
Test Price Monitor with Nintendo Amiibo from Best Buy
"""

from price_monitor import add_product, check_all_prices, view_all_products
import json

print("=" * 70)
print("🎮 TESTING PRICE MONITOR WITH NINTENDO AMIIBO (BEST BUY)")
print("=" * 70)

# Add the Nintendo Amiibo to monitor
print("\n📦 Adding Nintendo Amiibo to monitor...")
print("URL: https://www.bestbuy.com/product/nintendo-amiibo-captain-toad...")

product = add_product(
    product_name="Nintendo Amiibo - Captain Toad (Best Buy)",
    url="https://www.bestbuy.com/product/nintendo-amiibo-captain-toad-talking-flower-super-mario-bros-wonder-series-multi/J7GSL5J8J3",
    target_price=12.99,  # Alert if price drops below $12.99
    email="your-email@example.com"  # Change this to your email
)

print("\n" + "=" * 70)
print("✅ PRODUCT ADDED!")
print("=" * 70)

# Show all monitored products
print("\n📊 Currently Monitoring:\n")
view_all_products()

# Check prices now
print("\n🔍 Checking current price...")
print("=" * 70)
alerts = check_all_prices()

if alerts:
    print(f"\n🎉 PRICE DROP DETECTED! {len(alerts)} alert(s) to send")
else:
    print("\n✓ Price is stable (no alerts yet)")

print("\n" + "=" * 70)
print("📝 NEXT STEPS:")
print("=" * 70)
print("""
1. Check if the price was scraped correctly above
2. Change the target_price to test alerts
3. Run this script again to check for price drops
4. Set up a schedule to run this daily:
   - On Mac: Use 'crontab -e' to schedule daily runs
   - Or use GitHub Actions for cloud scheduling
5. Add more products to monitor!

To modify the target price, edit test_bestbuy.py and change:
    target_price=12.99  <- Change this number

To add your real email, change:
    email="your-email@example.com"  <- Add your email
""")
