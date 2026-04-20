"""
product_scraper.py

Visits each product URL and extracts structured data driven entirely by the
adapted config — no brand-specific logic anywhere except the clearly-labelled
seiko_dual spec extractor.

Public API
──────────
scrape_products(driver, urls_with_data, config, ctx=None) -> list[dict]

Per-product output dict keys
─────────────────────────────
  source_url, title, price, sku, description, features (list), color, size,
  images (list), specs (dict), gender, availability, scrape_status, scrape_warnings

scrape_status values
─────────────────────
  "ok"      — title, price, and sku all present
  "partial" — title present but price or sku missing
  "failed"  — title missing (page likely did not load correctly)

Spec extraction types
──────────────────────
  row         — row_selector → key_selector + value_selector
  label_value — parallel label_selector / value_selector lists, zipped
  aria_label  — row_selector elements with label_attr, matched against known_keys
  accordion   — item_selector → label_selector + value_selector (generic)
  icon_map    — item_selector → icon src matched against icon_label_map + text_selector
  seiko_dual  — brand-specific dual-section extractor (see note in function)
"""

import json
import logging
import random
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def scrape_products(
    driver,
    urls_with_data: List[Dict],
    config: dict,
    ctx=None,
) -> List[Dict[str, Any]]:
    """
    Scrape each product URL and return a list of raw product dicts.

    Args:
        driver:         Selenium WebDriver instance.
        urls_with_data: List of {url, sku, gender, availability} dicts
                        (the "new" list from deduplicator.find_new_urls()).
        config:         Adapted config dict.
        ctx:            Optional RunContext — used to write products_raw.json.

    Returns:
        List of raw product dicts (one per successfully attempted URL).
    """
    anti_bot = config.get("anti_bot", {})
    products: List[Dict] = []

    for i, url_entry in enumerate(urls_with_data, 1):
        url = url_entry.get("url", "")
        logger.info("Scraping product %d/%d: %s", i, len(urls_with_data), url)
        try:
            product = _scrape_one(driver, url_entry, config, anti_bot)
            products.append(product)
        except Exception as e:
            logger.error("  Fatal error scraping %s: %s", url, e)
            products.append({
                "source_url":      url,
                "scrape_status":   "failed",
                "scrape_warnings": [f"Fatal: {e}"],
            })

    if ctx:
        ctx.path("products_raw.json").write_text(
            json.dumps(products, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.debug("Wrote products_raw.json (%d products) to run folder.", len(products))

    return products


# ---------------------------------------------------------------------------
# Single-product scraping
# ---------------------------------------------------------------------------

def _scrape_one(
    driver,
    url_entry: Dict,
    config: dict,
    anti_bot: dict,
) -> Dict[str, Any]:
    url              = url_entry.get("url", "")
    gender           = url_entry.get("gender", "")
    first_load_wait  = anti_bot.get("first_load_wait", anti_bot.get("page_delay", 3))

    # Step 1 — Load page
    driver.get(url)
    time.sleep(first_load_wait)

    warnings: List[str] = []

    # Step 2 — Slow scroll (triggers lazy-loaded sections)
    if anti_bot.get("slow_scroll", False):
        _slow_scroll_to_bottom(driver)

    # Step 3 — Pre-scrape actions (failures go to warnings, not raised)
    _run_pre_scrape_actions(driver, config.get("pre_scrape_actions", []), warnings)

    # Step 4 — Parse
    soup = BeautifulSoup(driver.page_source, "lxml")

    selectors     = config.get("selectors", {})
    sku_prefix    = config.get("sku_prefix_to_strip", "")
    images_cfg    = config.get("images", {})
    spec_cfg      = config.get("spec_extraction", {})
    avail_cfg     = config.get("product_availability", {})

    product: Dict[str, Any] = {
        "source_url":      url,
        "gender":          gender,
        "scrape_warnings": warnings,
    }

    # Step 5 — Text fields
    for field in ("title", "price", "description", "color", "size"):
        try:
            sel_list = _as_list(selectors.get(field, []))
            product[field] = _extract_field(soup, sel_list)
        except Exception as e:
            warnings.append(f"[{field}] {e}")
            product[field] = ""

    # SKU — with optional prefix stripping
    try:
        sel_list = _as_list(selectors.get("sku", []))
        product["sku"] = _extract_field(soup, sel_list, strip_prefix=sku_prefix)
    except Exception as e:
        warnings.append(f"[sku] {e}")
        product["sku"] = ""

    # Features — list of strings
    try:
        sel_list = _as_list(selectors.get("features", []))
        product["features"] = _extract_field_list(soup, sel_list)
    except Exception as e:
        warnings.append(f"[features] {e}")
        product["features"] = []

    # Step 6 — Specs
    try:
        product["specs"] = _extract_specs(soup, spec_cfg)
    except Exception as e:
        warnings.append(f"[specs] {e}")
        product["specs"] = {}

    # Step 7 — Images
    try:
        product["images"] = _extract_images(soup, images_cfg, url)
    except Exception as e:
        warnings.append(f"[images] {e}")
        product["images"] = []

    # Step 8 — Product-page availability (recorded only, no filtering)
    try:
        product["availability"] = _check_product_availability(soup, avail_cfg)
    except Exception as e:
        warnings.append(f"[availability] {e}")
        product["availability"] = ""

    # Step 9 — Scrape status
    title = product.get("title", "")
    price = product.get("price", "")
    sku   = product.get("sku", "")
    if not title:
        product["scrape_status"] = "failed"
    elif not price or not sku:
        product["scrape_status"] = "partial"
    else:
        product["scrape_status"] = "ok"

    logger.debug(
        "  %s | title=%r price=%r sku=%r | images=%d specs=%d status=%s",
        url, title[:40] if title else "", price, sku,
        len(product["images"]), len(product["specs"]), product["scrape_status"],
    )
    return product


# ---------------------------------------------------------------------------
# Pre-scrape actions
# ---------------------------------------------------------------------------

def _run_pre_scrape_actions(driver, actions: list, warnings: List[str]) -> None:
    """
    Execute each pre-scrape action in order.  Every failure is recorded in
    warnings — no exception propagates out of this function.
    """
    for action in actions:
        action_type = action.get("type", "")
        selector    = action.get("selector", "")

        try:
            if action_type == "js_click_all":
                if not selector:
                    continue
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    try:
                        driver.execute_script("arguments[0].click();", el)
                    except Exception:
                        pass
                if elements:
                    time.sleep(0.8)
                logger.debug("  [pre_scrape] js_click_all '%s' -> %d clicks",
                             selector, len(elements))

            elif action_type == "click":
                if not selector:
                    continue
                el = driver.find_element(By.CSS_SELECTOR, selector)
                el.click()
                time.sleep(0.5)

            elif action_type == "wait":
                duration = float(action.get("duration", 1))
                time.sleep(duration)

        except Exception as e:
            msg = f"[pre_scrape] '{action_type}' on '{selector}' failed: {e}"
            logger.debug(msg)
            warnings.append(msg)


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------

def _extract_field(
    soup: BeautifulSoup,
    selectors: List[str],
    strip_prefix: str = "",
) -> str:
    """
    Try each CSS selector in order; return the first non-empty result.

    For selectors ending with a bare attribute specifier — e.g. [data-sku] or
    .product[data-sku] — the attribute value is extracted directly rather than
    element text.

    Args:
        selectors:    Ordered list of CSS selectors to try.
        strip_prefix: Remove this prefix from the extracted value (used for sku).
    """
    for sel in selectors:
        if not sel:
            continue
        try:
            # Detect a bare attribute extraction selector: ends with [attr-name]
            # (no = inside the brackets — avoids matching value-filter selectors)
            attr_match = re.search(r'\[([a-zA-Z0-9_:-]+)\]$', sel.strip())
            el = soup.select_one(sel)
            if el:
                if attr_match:
                    val = el.get(attr_match.group(1), "") or ""
                else:
                    val = el.get_text(separator=" ", strip=True)
                val = val.strip()
                if strip_prefix and val.startswith(strip_prefix):
                    val = val[len(strip_prefix):]
                if val:
                    return val
        except Exception:
            pass
    return ""


def _extract_field_list(soup: BeautifulSoup, selectors: List[str]) -> List[str]:
    """
    Try each CSS selector; return the first non-empty list of text values.
    """
    for sel in selectors:
        if not sel:
            continue
        try:
            items = [
                el.get_text(strip=True)
                for el in soup.select(sel)
                if el.get_text(strip=True)
            ]
            if items:
                return items
        except Exception:
            pass
    return []


def _as_list(val: Any) -> List[str]:
    """Wrap a string in a list; return a list unchanged; filter empties."""
    if isinstance(val, str):
        return [val] if val else []
    return [s for s in (val or []) if s]


# ---------------------------------------------------------------------------
# Spec extraction — dispatcher
# ---------------------------------------------------------------------------

def _extract_specs(soup: BeautifulSoup, spec_cfg: dict) -> Dict[str, str]:
    """
    Dispatch to the correct extractor based on spec_cfg["type"].
    Returns {} when spec_cfg is empty or type is unrecognised.
    """
    ext_type = spec_cfg.get("type", "row") if spec_cfg else "row"
    if not spec_cfg:
        return {}

    if ext_type == "row":
        return _specs_row(soup, spec_cfg)
    if ext_type == "label_value":
        return _specs_label_value(soup, spec_cfg)
    if ext_type == "aria_label":
        return _specs_aria_label(soup, spec_cfg)
    if ext_type == "accordion":
        return _specs_accordion(soup, spec_cfg)
    if ext_type == "icon_map":
        return _specs_icon_map(soup, spec_cfg)
    if ext_type == "seiko_dual":
        # NOTE: Seiko-specific extractor. When Seiko's YAML is updated to use
        # type: "row" with its actual selectors, this branch can be removed.
        return _specs_seiko_dual(soup)

    logger.warning("Unknown spec_extraction type: '%s'", ext_type)
    return {}


# ---------------------------------------------------------------------------
# Spec extractors
# ---------------------------------------------------------------------------

def _specs_row(soup: BeautifulSoup, spec_cfg: dict) -> Dict[str, str]:
    """
    Find all row_selector elements; within each find key_selector and
    value_selector and emit key → value.
    """
    row_sel = spec_cfg.get("row_selector", "")
    key_sel = spec_cfg.get("key_selector", "")
    val_sel = spec_cfg.get("value_selector", "")
    specs: Dict[str, str] = {}
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


def _specs_label_value(soup: BeautifulSoup, spec_cfg: dict) -> Dict[str, str]:
    """
    Collect all label_selector elements and all value_selector elements
    independently, then zip them into a dict.
    Trailing colons are stripped from label text.
    """
    label_sel = spec_cfg.get("label_selector", "")
    value_sel = spec_cfg.get("value_selector", "")
    specs: Dict[str, str] = {}
    if not (label_sel and value_sel):
        return specs
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


def _specs_aria_label(soup: BeautifulSoup, spec_cfg: dict) -> Dict[str, str]:
    """
    Find all row_selector elements whose label_attr attribute starts with a
    known key from known_keys.  The value is the remainder of the attribute
    string after stripping the key prefix.

    Unmatched elements are stored keyed by the full attribute string (they will
    typically be ignored downstream).
    """
    row_sel    = spec_cfg.get("row_selector", "")
    label_attr = spec_cfg.get("label_attr", "aria-label")
    known_keys = spec_cfg.get("known_keys", [])
    specs: Dict[str, str] = {}
    if not row_sel:
        return specs
    for el in soup.select(row_sel):
        aria_val = (el.get(label_attr) or "").strip()
        if not aria_val:
            continue
        matched = False
        for key in known_keys:
            if aria_val.lower().startswith(key.lower()):
                specs[key] = aria_val[len(key):].strip()
                matched = True
                break
        if not matched:
            specs[aria_val] = aria_val
    return specs


def _specs_accordion(soup: BeautifulSoup, spec_cfg: dict) -> Dict[str, str]:
    """
    Generic accordion extractor.  Finds all item_selector elements; within
    each, extracts label_selector text as key and value_selector text as value.
    Works for Movado-style and Rado-style accordions without any brand logic.
    """
    item_sel  = spec_cfg.get("item_selector", "")
    label_sel = spec_cfg.get("label_selector", "")
    value_sel = spec_cfg.get("value_selector", "")
    specs: Dict[str, str] = {}
    if not (item_sel and label_sel and value_sel):
        return specs
    for item in soup.select(item_sel):
        try:
            label_el = item.select_one(label_sel)
            value_el = item.select_one(value_sel)
            if label_el and value_el:
                key = label_el.get_text(strip=True)
                val = value_el.get_text(strip=True)
                if key:
                    specs[key] = val
        except Exception:
            pass
    return specs


def _specs_icon_map(soup: BeautifulSoup, spec_cfg: dict) -> Dict[str, str]:
    """
    Find all item_selector elements.  For each:
      - Get the icon's src (or other icon_attribute) via icon_selector.
      - Extract the filename from the src path.
      - Substring-match the filename against icon_label_map keys; use the
        mapped label as the spec key.
      - Get the spec value from text_selector text.
    Only entries with a recognised icon are included.
    """
    item_sel      = spec_cfg.get("item_selector", "")
    icon_sel      = spec_cfg.get("icon_selector", "")
    icon_attr     = spec_cfg.get("icon_attribute", "src")
    text_sel      = spec_cfg.get("text_selector", "")
    icon_label_map: Dict[str, str] = spec_cfg.get("icon_label_map", {})
    specs: Dict[str, str] = {}
    if not (item_sel and icon_sel and text_sel):
        return specs
    for item in soup.select(item_sel):
        try:
            icon_el = item.select_one(icon_sel)
            text_el = item.select_one(text_sel)
            if not (icon_el and text_el):
                continue
            icon_src = (icon_el.get(icon_attr) or "").strip()
            if not icon_src:
                continue
            filename = icon_src.rstrip("/").split("/")[-1].lower()
            matched_label: Optional[str] = None
            for map_key, label in icon_label_map.items():
                if map_key.lower() in filename:
                    matched_label = label
                    break
            if matched_label:
                specs[matched_label] = text_el.get_text(strip=True)
        except Exception:
            pass
    return specs


def _specs_seiko_dual(soup: BeautifulSoup) -> Dict[str, str]:
    """
    NOTE: Seiko-specific extractor — should be replaced when Seiko's brand
    YAML is updated to use type: "row" with explicit selectors.

    Seiko USA has two distinct spec sections on the product page:

    Section 1 — Additional Features (.feature-item blocks):
      <div class="feature-item">
        <div class="feature-label">Case Material</div>
        <div class="feature-value">Two-tone stainless steel case</div>
      </div>

    Section 2 — Technical Data (.techdata-grid sibling divs):
      <div class="techdata-grid techdatacase">
        <div class="label">Diameter</div><div class="value">34 mm</div>
        …
      </div>
      (Movement grid has .value divs without .label — those pairs are skipped.)
    """
    specs: Dict[str, str] = {}

    # Section 1: Additional Features
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

    # Section 2: Technical Data (label + value are sibling divs inside .techdata-grid)
    for grid in soup.select(".techdata-grid"):
        try:
            children = [c for c in grid.children if getattr(c, "name", None) == "div"]
            i = 0
            while i < len(children):
                child = children[i]
                if "label" in child.get("class", []):
                    key = child.get_text(strip=True)
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


# ---------------------------------------------------------------------------
# Product-page availability check
# ---------------------------------------------------------------------------

def _check_product_availability(soup: BeautifulSoup, avail_cfg: dict) -> str:
    """
    Check whether the configured availability indicator element is present on
    the product page.  Returns present_means if found, "" otherwise.
    Recorded only — this function never filters anything.
    """
    if not avail_cfg:
        return ""
    selector     = avail_cfg.get("selector", "")
    present_means = avail_cfg.get("present_means", "sold_out")
    if not selector:
        return ""
    return present_means if soup.select_one(selector) else ""


# ---------------------------------------------------------------------------
# Image extraction
# ---------------------------------------------------------------------------

def _extract_images(soup: BeautifulSoup, image_config: dict, page_url: str) -> List[str]:
    method           = image_config.get("method", "dom_selector")
    exclude_keywords = image_config.get("exclude_keywords", [])
    urls: List[str]  = []

    if method in ("magento_json", "both"):
        urls.extend(_images_magento_json(soup, image_config))

    if method in ("dom_selector", "both") and not urls:
        urls.extend(_images_dom_selector(soup, image_config, page_url))

    if exclude_keywords:
        urls = [u for u in urls
                if not any(kw.lower() in u.lower() for kw in exclude_keywords)]

    return _normalize_and_dedup_images(urls, image_config.get("strip_query_params", False))


def _images_magento_json(soup: BeautifulSoup, image_config: dict) -> List[str]:
    script_pattern  = image_config.get("script_pattern", r'"url":"(https?://[^"]+)"')
    script_contains = image_config.get("script_contains", "")
    urls: List[str] = []
    for script in soup.find_all("script"):
        text = script.string or ""
        if script_contains and script_contains not in text:
            continue
        try:
            urls.extend(re.findall(script_pattern, text))
        except Exception:
            pass
    return urls


def _images_dom_selector(
    soup: BeautifulSoup, image_config: dict, page_url: str
) -> List[str]:
    selectors = image_config.get("selectors", [])
    if not selectors and image_config.get("selector"):
        selectors = [image_config["selector"]]

    attr         = image_config.get("attr", "src")
    fallback_attr = image_config.get("fallback_attr", "data-src")
    parsed       = urlparse(page_url)
    base         = f"{parsed.scheme}://{parsed.netloc}"

    urls: List[str] = []
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
    seen: set = set()
    result: List[str] = []
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slow_scroll_to_bottom(driver, scroll_pause: float = 0.3) -> None:
    """Slow scroll to the page bottom to trigger lazy-loaded sections."""
    last_height = driver.execute_script("return document.body.scrollHeight")
    for offset in range(0, last_height, 400):
        driver.execute_script(f"window.scrollTo(0, {offset});")
        time.sleep(scroll_pause)
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(1)


def _random_delay(anti_bot: dict) -> float:
    d_min = anti_bot.get("random_delay_min")
    d_max = anti_bot.get("random_delay_max")
    if d_min is not None and d_max is not None:
        return random.uniform(float(d_min), float(d_max))
    return float(anti_bot.get("page_delay", 2))
