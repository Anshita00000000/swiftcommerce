# test_scrape.py
import logging
import yaml
from pipeline.config_adapter import adapt
from pipeline.driver_factory import build_driver
from pipeline.product_scraper import scrape_products

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# Your test URLs
test_urls = [
    {"url": "https://www.movado.com/us/en/shop-watches/bold-evolution-2.0-3601382.html", "sku": "3601382", "gender": "Women", "availability": ""},
    {"url": "https://www.movado.com/us/en/shop-watches/bold-evolution-2.0-mini-3601328.html", "sku": "3601328", "gender": "Unisex", "availability": ""},
]

# Load config
with open("config/movado.yaml", encoding="utf-8") as f:
    config = adapt(yaml.safe_load(f))

driver = build_driver(config.get("anti_bot", {}))
try:
    products = scrape_products(driver, test_urls, config)
finally:
    driver.quit()

# Print results
import json
print(json.dumps(products, indent=2))