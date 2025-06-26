import os
import requests
import logging
import json
from typing import Dict, Any
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Get environment variables and strip any whitespace
SHOP_NAME = os.getenv("SHOP_NAME", "").strip()
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", "").strip()

API_VERSION = "2025-04"
BASE_URL = f"https://{SHOP_NAME}.myshopify.com/admin/api/{API_VERSION}"

def shorten_url(long_url):
    """
    Shortens a URL using the TinyURL API.
    Returns the shortened URL or the original URL if shortening fails.
    """
    try:
        api_url = f"https://tinyurl.com/api-create.php?url={long_url}"
        response = requests.get(api_url, timeout=5)
        if response.status_code == 200:
            return response.text
        return long_url
    except Exception as e:
        logging.error(f"Error shortening URL: {e}")
        return long_url

def fetch_shopify_inventory() -> Dict[str, Any]:
    result = {
        "locations": [],
        "products": [],
        "inventory_items": [],
        "inventory_levels": [],
        "error": None,
        "summary": {
            "total_locations": 0,
            "total_products": 0,
            "total_inventory_items": 0,
            "total_inventory_levels": 0
        }
    }

    # 1) Validate env
    if not SHOP_NAME or not ACCESS_TOKEN:
        result["error"] = "Missing SHOP_NAME or SHOPIFY_ACCESS_TOKEN in environment"
        logging.error(result["error"])
        return result

    headers = {"X-Shopify-Access-Token": ACCESS_TOKEN}

    # 3) Fetch all products with cursor-based pagination
    try:
        logging.info("Fetching all products...")
        products = []
        next_page_info = None

        while True:
            url = f"{BASE_URL}/products.json?limit=250"
            if next_page_info:
                url += f"&page_info={next_page_info}"
            resp = requests.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            page_products = data.get("products", [])
            products.extend(page_products)
            logging.info(f"  - Got {len(page_products)} products")

            # Shopify cursor pagination from the Link header
            link_header = resp.headers.get("Link", "")
            if 'rel="next"' not in link_header:
                break
            # extract page_info=... from the next link
            next_page_info = [
                part.split("page_info=")[1].split(">")[0]
                for part in link_header.split(",")
                if 'rel="next"' in part
            ][0]

        result["products"] = products
        result["summary"]["total_products"] = len(products)
        logging.info(f"✅ Total products fetched: {len(products)}")
    except Exception as e:
        result["error"] = f"Error fetching products: {e}"
        logging.error(result["error"])
        return result

    # 4) Collect inventory_item_ids
    inventory_item_ids = [
        variant["inventory_item_id"]
        for prod in result["products"]
        for variant in prod.get("variants", [])
        if variant.get("inventory_item_id")
    ]

    # 5) Fetch inventory_items in batches
    try:
        logging.info("Fetching inventory items...")
        all_items = []
        for start in range(0, len(inventory_item_ids), 50):
            batch = inventory_item_ids[start:start+50]
            ids_param = ",".join(map(str, batch))
            url = f"{BASE_URL}/inventory_items.json?ids={ids_param}"
            resp = requests.get(url, headers=headers)
            resp.raise_for_status()
            batch_items = resp.json().get("inventory_items", [])
            all_items.extend(batch_items)
            logging.info(f"  - Got {len(batch_items)} items")
        result["inventory_items"] = all_items
        result["summary"]["total_inventory_items"] = len(all_items)
    except Exception as e:
        result["error"] = f"Error fetching inventory items: {e}"
        logging.error(result["error"])
        return result

    logging.info("✅ Inventory fetch complete")
    return result


def save_to_json(data, filename=None):
    """Save data to a JSON file with timestamp in filename"""
    if filename is None:
        filename = f"products.json"
    
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    
    logging.info(f"✅ Data saved to {filename}")
    return filename

import json
import csv
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv
from spreadsheet import write_to_sheet
import os


load_dotenv()

# Your store's base domain (no trailing slash)
STORE_DOMAIN = "https://nashermiles.com"

# Helper to strip HTML tags
def strip_html(raw_html):
    clean = re.sub(r'<[^>]+>', '', raw_html or '')
    return re.sub(r'\s+', ' ', clean).strip()

def process_product(product):
    """Process a single product and return its data"""
    try:
        # Skip products with no stock
        total_stock = sum(v.get('inventory_quantity', 0) for v in product.get('variants', []))
        if total_stock <= 0:
            print(f"Skipping product {product.get('title', 'unknown')} - no stock")
            return None


        # Aggregate variant info
        variants = [
            f"{v.get('title')} - ₹{v.get('price')}"
            for v in product.get('variants', [])
        ]
        
        # Initialize LLM for each thread
        llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash-8b-latest", temperature=0.2)
        prompt = f"""
        compress the product description to max 20 words. sementically don't change the meaning. 
        Product description: {strip_html(product.get('body_html', ''))}
        """
        
        response = llm.invoke([HumanMessage(content=prompt)])
        # Build the full shopping URL and shorten it
        original_url = f"{STORE_DOMAIN}/products/{product.get('handle', '')}"
        short_url = shorten_url(original_url)
        return {
            'title': product.get('title', ''),
            'product_type': product.get('product_type', ''),
            'variants': "; ".join(variants),
            'description': response.content,
            'product link': short_url,
        }
    except Exception as e:
        print(f"Error processing product {product.get('title', 'unknown')}: {str(e)}")
        return None


#step2
def product_to_sheet():
    # Load the Shopify JSON export
    with open('products.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    products = data.get('products', data)

    # Define CSV columns including the shopping link
    fieldnames = [
        'title',
        'product_type',
        'variants',
        'description',
         'product link'    # Built from handle + domain
        
    ]

    # Process products in parallel
    processed_products = []
    # Adjust max_workers based on your system capabilities and API rate limits
    with ThreadPoolExecutor(max_workers=20) as executor:
        # Start the load operations and mark each future with its product
        future_to_product = {executor.submit(process_product, product): product for product in products}
        
        for future in as_completed(future_to_product):
            product = future_to_product[future]
            try:
                result = future.result()
                if result:
                    processed_products.append(result)
            except Exception as e:
                print(f"Error processing product: {e}")

    # Write all results to CSV
    with open('products_user_view.csv', 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(processed_products)

    print(f"CSV file 'products_user_view.csv' created with {len(processed_products)} products.")

    # Write to Google Sheet
    sheet_name = "Products"  # You can change this to your desired sheet name
    write_to_sheet(
        sheet_name=sheet_name,
        data=processed_products,
        fieldnames=fieldnames,
        sheet_id=os.getenv("SHEET_ID")  # Make sure to set this in your .env file
    )

if __name__ == "__main__":
    inventory_data = fetch_shopify_inventory()

    if inventory_data.get("error"):
        print("\n===== ERROR =====\n")
        print(f"Error: {inventory_data['error']}")
        if inventory_data.get("error_details"):
            print("Details:", json.dumps(inventory_data["error_details"], indent=2))
    else:
        # Print summary to console
        print("\n===== SHOPIFY INVENTORY SUMMARY =====\n")
        print(json.dumps(inventory_data["summary"], indent=2))
        
        # Save complete data to JSON file
        saved_file = save_to_json(inventory_data)
        print(f"\nComplete inventory data saved to: {saved_file}")
    product_to_sheet()
