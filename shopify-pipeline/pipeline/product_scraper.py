"""
product_scraper.py
Visits each product URL and extracts structured data per the adapted brand config.
Per-field try/except isolation: one bad field never crashes a product.
"""

import logging
import random
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By

logger = logging.getLogger(__name__)


def scrape_products(driver, urls: List[str], config: dict) -> List[Dict[str, Any]]:
    anti_bot = config.get("anti_bot", {})
    products = []
    for i, url in enumerate(urls, 1):
        logger.info(f"Scraping product {i}/{len(urls)}: {url}")
        try:
            product = _scrape_one(driver, url, config, anti_bot)
            if product:
                products.append(product)
        except Exception as e:
            logger.error(f"  Fatal error scraping {url}: {e}")
    return products


def _scrape_one(driver, url: str, config: dict, anti_bot: dict) -> Optional[Dict[str, Any]]:
    driver.get(url)
    time.sleep(_random_delay(anti_bot))

    # This scrolls down before doing anything else
    if anti_bot.get("slow_scroll", False):
        _slow_scroll_to_bottom(driver, scroll_pause=0.3)

    _run_pre_scrape_actions(driver, config.get("pre_scrape_actions", []))

    soup = BeautifulSoup(driver.page_source, "lxml")
    fields_selectors = config.get("fields_selectors", {})
    image_config = config.get("images", {})
    spec_extraction = config.get("spec_extraction", {})
    spec_table_cfg = config.get("spec_table", {})

    product = {"source_url": url}

    # Core fields — try each fallback selector
    for field_name, selectors in fields_selectors.items():
        if field_name == "features":
            continue
        try:
            product[field_name] = _extract_text_from_list(soup, selectors)
        except Exception as e:
            logger.warning(f"  [{field_name}] extraction failed: {e}")
            product[field_name] = ""

    # Features list
    try:
        product["features"] = _extract_list_from_selectors(
            soup, fields_selectors.get("features", [])
        )
    except Exception as e:
        logger.warning(f"  [features] extraction failed: {e}")
        product["features"] = []

    # Images
    try:
        product["images"] = _extract_images(soup, image_config, url)
    except Exception as e:
        logger.warning(f"  [images] extraction failed: {e}")
        product["images"] = []

    # Spec key/value pairs
    try:
        product["specs"] = _extract_specs(soup, spec_extraction, spec_table_cfg)
    except Exception as e:
        logger.warning(f"  [specs] extraction failed: {e}")
        product["specs"] = {}

    logger.debug(
        f"  Scraped: {product.get('title', url)} | "
        f"images={len(product['images'])} | specs={len(product['specs'])}"
    )
    return product


# ---------------------------------------------------------------------------
# Pre-scrape actions
# ---------------------------------------------------------------------------

def _run_pre_scrape_actions(driver, actions: list) -> None:
    for action in actions:
        action_type = action.get("type", "")
        selector = action.get("selector", "")
        if not selector:
            continue
        try:
            if action_type == "js_click_all":
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    try:
                        driver.execute_script("arguments[0].click();", el)
                    except Exception:
                        pass
                if elements:
                    time.sleep(0.8)
                    logger.debug(f"  [pre_scrape] js_click_all '{selector}' -> {len(elements)} clicks")
            elif action_type == "click":
                el = driver.find_element(By.CSS_SELECTOR, selector)
                el.click()
                time.sleep(0.5)
        except Exception as e:
            logger.debug(f"  [pre_scrape] '{action_type}' on '{selector}' failed: {e}")


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------

def _extract_text_from_list(soup: BeautifulSoup, selectors: List[str]) -> str:
    for sel in selectors:
        if not sel:
            continue
        try:
            # Handle attribute selectors like [data-pid]
            attr_match = re.match(r'^\[([a-zA-Z0-9_-]+)\]$', sel.strip())
            if attr_match:
                el = soup.select_one(sel)
                if el:
                    val = el.get(attr_match.group(1), "")
                    if val:
                        return val.strip()
            else:
                el = soup.select_one(sel)
                if el:
                    text = el.get_text(separator=" ", strip=True)
                    if text:
                        return text
        except Exception:
            pass
    return ""


def _extract_list_from_selectors(soup: BeautifulSoup, selectors: List[str]) -> List[str]:
    for sel in selectors:
        if not sel:
            continue
        try:
            els = soup.select(sel)
            items = [el.get_text(strip=True) for el in els if el.get_text(strip=True)]
            if items:
                return items
        except Exception:
            pass
    return []


# ---------------------------------------------------------------------------
# Spec extraction
# ---------------------------------------------------------------------------

def _extract_specs(soup: BeautifulSoup, spec_extraction: dict, spec_table_cfg: dict) -> Dict[str, str]:
    ext_type = spec_extraction.get("type", "row")
    if ext_type == "label_value":
        return _specs_label_value(soup, spec_extraction)
    elif ext_type == "aria_label":
        return _specs_aria_label(soup, spec_extraction, spec_table_cfg)
    elif ext_type == "seiko_dual":
        return _specs_seiko_dual(soup)
    else:
        return _specs_row(soup, spec_extraction)


def _specs_row(soup: BeautifulSoup, spec_extraction: dict) -> Dict[str, str]:
    row_sel = spec_extraction.get("row_selector", "")
    key_sel = spec_extraction.get("key_selector", "")
    val_sel = spec_extraction.get("value_selector", "")
    specs = {}
    if not (row_sel and key_sel and val_sel):
        return specs
    for row in soup.select(row_sel):
        try:
            key_el = row.select_one(key_sel)
            val_el = row.select_one(val_sel)
            if key_el and val_el:
                key = key_el.get_text(strip=True)
                val = val_el.get_text(strip=True)
                if key:
                    specs[key] = val
        except Exception:
            pass
    return specs


def _specs_seiko_dual(soup: BeautifulSoup) -> Dict[str, str]:
    """
    Seiko USA has two distinct spec sections on the product page:

    1) Additional Features (.feature-item blocks):
       <div class="feature-item">
         <div class="feature-label">Case Material</div>
         <div class="feature-value">Two-tone stainless steel case</div>
       </div>

    2) Technical Data (.techdata-grid sibling divs):
       <div class="techdata-grid techdatacase">
         <div class="label">Diameter</div><div class="value">34 mm</div>
         <div class="label">Thickness</div><div class="value">10.7 mm</div>
       </div>
       Note: the Movement grid only has .value divs (no .label) — those are skipped.
    """
    specs = {}

    # --- Section 1: Additional Features ---
    for item in soup.select("div.feature-item"):
        try:
            label_el = item.select_one(".feature-label")
            value_el = item.select_one(".feature-value")
            if label_el and value_el:
                key = label_el.get_text(strip=True)
                val = value_el.get_text(strip=True)
                if key and val:
                    specs[key] = val
        except Exception:
            pass

    # --- Section 2: Technical Data (label+value are siblings inside .techdata-grid) ---
    for grid in soup.select(".techdata-grid"):
        try:
            children = [c for c in grid.children if getattr(c, "name", None) == "div"]
            # Walk through children in pairs: if div has class "label", next div is "value"
            i = 0
            while i < len(children):
                child = children[i]
                classes = child.get("class", [])
                if "label" in classes:
                    key = child.get_text(strip=True)
                    # Next sibling should be the value
                    if i + 1 < len(children):
                        val = children[i + 1].get_text(strip=True)
                        if key and val:
                            specs[key] = val
                        i += 2
                        continue
                i += 1
        except Exception:
            pass

    return specs
    """Hamilton: parallel .attribute-label / .attribute-value elements (zipped)."""
    label_sel = spec_extraction.get("label_selector", ".attribute-label")
    value_sel = spec_extraction.get("value_selector", ".attribute-value")
    specs = {}
    labels = soup.select(label_sel)
    values = soup.select(value_sel)
    for label_el, value_el in zip(labels, values):
        try:
            key = label_el.get_text(strip=True).rstrip(":")
            val = value_el.get_text(strip=True)
            if key:
                specs[key] = val
        except Exception:
            pass
    return specs


def _specs_aria_label(soup: BeautifulSoup, spec_extraction: dict, spec_table_cfg: dict) -> Dict[str, str]:
    """Tissot: aria-label='KEY VALUE' — parse by known keys from spec_table rows."""
    row_sel = spec_extraction.get("row_selector", "")
    label_attr = spec_extraction.get("label_attr", "aria-label")
    specs = {}
    if not row_sel:
        return specs

    # Build list of known keys from spec_table rows
    known_keys = [
        row["aria_key"]
        for row in spec_table_cfg.get("rows", [])
        if row.get("type") == "aria_label" and row.get("aria_key")
    ]

    for el in soup.select(row_sel):
        aria_val = el.get(label_attr, "").strip()
        if not aria_val:
            continue
        matched = False
        for key in known_keys:
            if aria_val.lower().startswith(key.lower()):
                value = aria_val[len(key):].strip()
                specs[key] = value
                matched = True
                break
        if not matched:
            # Store full string keyed by itself (will be skipped in spec table)
            specs[aria_val] = aria_val
    return specs


# ---------------------------------------------------------------------------
# Image extraction
# ---------------------------------------------------------------------------

def _extract_images(soup: BeautifulSoup, image_config: dict, page_url: str) -> List[str]:
    method = image_config.get("method", "dom_selector")
    exclude_keywords = image_config.get("exclude_keywords", [])
    urls = []

    if method in ("magento_json", "both"):
        urls.extend(_images_magento_json(soup, image_config))

    if method in ("dom_selector", "both") and not urls:
        urls.extend(_images_dom_selector(soup, image_config, page_url))

    if exclude_keywords:
        urls = [u for u in urls if not any(kw.lower() in u.lower() for kw in exclude_keywords)]

    return _normalize_and_dedup_images(urls, image_config.get("strip_query_params", False))


def _images_magento_json(soup: BeautifulSoup, image_config: dict) -> List[str]:
    script_pattern = image_config.get("script_pattern", r'"url":"(https?://[^"]+)"')
    script_contains = image_config.get("script_contains", "")
    urls = []
    for script in soup.find_all("script"):
        text = script.string or ""
        if script_contains and script_contains not in text:
            continue
        try:
            urls.extend(re.findall(script_pattern, text))
        except Exception:
            pass
    return urls


def _images_dom_selector(soup: BeautifulSoup, image_config: dict, page_url: str) -> List[str]:
    selectors = image_config.get("selectors", [])
    if not selectors and image_config.get("selector"):
        selectors = [image_config["selector"]]

    attr = image_config.get("attr", "src")
    fallback_attr = image_config.get("fallback_attr", "data-src")
    parsed = urlparse(page_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    urls = []
    for selector in selectors:
        if not selector:
            continue
        for el in soup.select(selector):
            url = el.get(attr) or el.get(fallback_attr) or ""
            if not url:
                img = el.find("img")
                if img:
                    url = img.get(attr) or img.get(fallback_attr) or ""
            if url and not url.startswith("data:"):
                if url.startswith("//"):
                    url = "https:" + url
                elif url.startswith("/"):
                    url = base + url
                urls.append(url)
    return urls


def _normalize_and_dedup_images(urls: List[str], strip_query: bool = False) -> List[str]:
    seen = set()
    result = []
    for url in urls:
        url = url.strip()
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("http://"):
            url = "https://" + url[7:]
        if strip_query and "?" in url:
            url = url.split("?")[0]
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _random_delay(anti_bot: dict) -> float:
    d_min = anti_bot.get("random_delay_min")
    d_max = anti_bot.get("random_delay_max")
    if d_min is not None and d_max is not None:
        return random.uniform(float(d_min), float(d_max))
    return float(anti_bot.get("page_delay", 2))

def _slow_scroll_to_bottom(driver, scroll_pause=0.5):
    """Slow scroll to the bottom of the page to trigger lazy-loaded sections."""
    last_height = driver.execute_script("return document.body.scrollHeight")
    
    # We don't need to scroll the ENTIRE page usually, 
    # just enough to hit the footer where the specs are.
    # Let's scroll in 400px steps.
    for i in range(0, last_height, 400):
        driver.execute_script(f"window.scrollTo(0, {i});")
        time.sleep(scroll_pause)
        
    # One final scroll to the very bottom
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(1) # Final wait for JS to render
