"""
product_scraper.py
Visits each product URL and extracts structured data per the brand YAML config.
Per-field try/except isolation: one bad field never crashes a product.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By

logger = logging.getLogger(__name__)


def scrape_products(driver, urls: List[str], config: dict) -> List[Dict[str, Any]]:
    """
    Scrape each URL and return a list of raw product dicts.

    Args:
        driver: Selenium WebDriver instance.
        urls: Product page URLs to scrape.
        config: Parsed YAML config dict.

    Returns:
        List of raw product data dicts (pre-normalization).
    """
    delay = config.get("anti_bot", {}).get("page_delay", 2)
    products = []

    for i, url in enumerate(urls, 1):
        logger.info(f"Scraping product {i}/{len(urls)}: {url}")
        try:
            product = _scrape_one(driver, url, config, delay)
            if product:
                products.append(product)
        except Exception as e:
            logger.error(f"  Fatal error scraping {url}: {e}")

    return products


def _scrape_one(driver, url: str, config: dict, delay: float) -> Optional[Dict[str, Any]]:
    """Scrape a single product page."""
    driver.get(url)
    time.sleep(delay)

    soup = BeautifulSoup(driver.page_source, "lxml")
    selectors = config.get("selectors", {})
    fields = config.get("fields", {})
    image_config = config.get("images", {})
    spec_config = config.get("spec_table", {})

    product = {"source_url": url}

    # --- Core fields (each isolated) ---
    for field_name, selector in fields.items():
        if not selector:
            continue
        try:
            product[field_name] = _extract_text(soup, selector)
        except Exception as e:
            logger.warning(f"  [{field_name}] extraction failed: {e}")
            product[field_name] = ""

    # --- Images ---
    try:
        product["images"] = _extract_images(driver, soup, image_config, url)
    except Exception as e:
        logger.warning(f"  [images] extraction failed: {e}")
        product["images"] = []

    # --- Spec table key/value pairs ---
    try:
        product["specs"] = _extract_specs(soup, spec_config)
    except Exception as e:
        logger.warning(f"  [specs] extraction failed: {e}")
        product["specs"] = {}

    # --- Features list ---
    try:
        features_selector = selectors.get("features_list")
        if features_selector:
            product["features"] = _extract_list(soup, features_selector)
        else:
            product["features"] = []
    except Exception as e:
        logger.warning(f"  [features] extraction failed: {e}")
        product["features"] = []

    logger.debug(f"  Scraped: {product.get('title', url)}")
    return product


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _extract_text(soup: BeautifulSoup, selector: str) -> str:
    """Extract and clean text from the first matching CSS selector."""
    el = soup.select_one(selector)
    if el is None:
        return ""
    return el.get_text(separator=" ", strip=True)


def _extract_list(soup: BeautifulSoup, selector: str) -> List[str]:
    """Extract text from all matching elements as a list."""
    els = soup.select(selector)
    return [el.get_text(strip=True) for el in els if el.get_text(strip=True)]


def _extract_images(driver, soup: BeautifulSoup, image_config: dict, page_url: str) -> List[str]:
    """
    Extract product images. Supports two methods:
      - magento_json: parse a JSON blob embedded in a <script> tag
      - dom_selector: CSS selector(s) pointing to <img> or elements with data-src/src
    Normalizes all URLs to https:// and deduplicates.
    """
    method = image_config.get("method", "dom_selector")

    if method == "magento_json":
        return _images_magento_json(soup, image_config)
    else:
        return _images_dom_selector(soup, image_config)


def _images_magento_json(soup: BeautifulSoup, image_config: dict) -> List[str]:
    """Extract images from Magento-style JSON embedded in a script tag."""
    script_pattern = image_config.get("script_pattern", r"\"url\":\"(https?://[^\"]+)\"")
    scripts = soup.find_all("script", type=lambda t: t != "application/ld+json")
    urls = []

    for script in scripts:
        text = script.string or ""
        # Look for the gallery data object or any JSON with image urls
        if image_config.get("script_contains") and image_config["script_contains"] not in text:
            continue
        try:
            found = re.findall(script_pattern, text)
            urls.extend(found)
        except Exception:
            pass

    return _normalize_and_dedup_images(urls)


def _images_dom_selector(soup: BeautifulSoup, image_config: dict) -> List[str]:
    """Extract image URLs from DOM elements using CSS selectors."""
    selector = image_config.get("selector", "")
    attr = image_config.get("attr", "src")
    fallback_attr = image_config.get("fallback_attr", "data-src")

    if not selector:
        return []

    urls = []
    for el in soup.select(selector):
        url = el.get(attr) or el.get(fallback_attr) or ""
        if url and not url.startswith("data:"):
            urls.append(url)

    return _normalize_and_dedup_images(urls)


def _normalize_and_dedup_images(urls: List[str]) -> List[str]:
    """Normalize all image URLs to https:// and remove duplicates."""
    seen = set()
    result = []
    for url in urls:
        url = url.strip()
        # Normalize protocol-relative URLs
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("http://"):
            url = "https://" + url[7:]
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _extract_specs(soup: BeautifulSoup, spec_config: dict) -> Dict[str, str]:
    """
    Extract key/value spec pairs from a spec table.
    Supports row_selector + key_selector + value_selector pattern,
    and aria-label attribute extraction.
    """
    specs = {}

    row_selector = spec_config.get("row_selector")
    key_selector = spec_config.get("key_selector")
    value_selector = spec_config.get("value_selector")
    aria_selector = spec_config.get("aria_label_selector")

    if row_selector and key_selector and value_selector:
        for row in soup.select(row_selector):
            try:
                key_el = row.select_one(key_selector)
                val_el = row.select_one(value_selector)
                if key_el and val_el:
                    key = key_el.get_text(strip=True)
                    val = val_el.get_text(strip=True)
                    if key:
                        specs[key] = val
            except Exception:
                pass

    if aria_selector:
        for el in soup.select(aria_selector):
            try:
                label = el.get("aria-label", "").strip()
                text = el.get_text(strip=True)
                if label:
                    specs[label] = text
            except Exception:
                pass

    return specs
