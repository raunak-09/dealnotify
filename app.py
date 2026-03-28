"""
Price Drop Alert Bot - Web Interface
A simple web app to manage price alerts
"""

from flask import Flask, render_template, request, jsonify
from price_monitor import add_product, check_all_prices, view_all_products, load_database
import json

app = Flask(__name__)

@app.route('/')
def index():
    """Home page - display all products"""
    db = load_database()
    return jsonify({
        "status": "success",
        "products": db.get("products", [])
    })

@app.route('/add', methods=['POST'])
def add_new_product():
    """Add a new product to monitor"""
    try:
        data = request.json
        
        product = add_product(
            product_name=data.get('product_name'),
            url=data.get('url'),
            target_price=float(data.get('target_price')),
            email=data.get('email')
        )
        
        return jsonify({
            "status": "success",
            "message": f"Added {product['name']} to monitoring",
            "product": product
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 400

@app.route('/check', methods=['GET'])
def check_prices():
    """Check all prices and send alerts"""
    alerts = check_all_prices()
    return jsonify({
        "status": "success",
        "alerts_sent": len(alerts),
        "alerts": alerts
    })

@app.route('/products', methods=['GET'])
def get_products():
    """Get all monitored products"""
    db = load_database()
    return jsonify({
        "status": "success",
        "products": db.get("products", [])
    })

if __name__ == '__main__':
    print("🚀 Price Drop Alert Bot Web Server")
    print("http://localhost:5000")
    app.run(debug=True, port=5000)
