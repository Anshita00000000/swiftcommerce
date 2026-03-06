"""
url_collector.py
Visits brand category pages and collects all product URLs.
Supports multiple pagination types: next_button, load_more, infinite_scroll, none.
"""

import logging
import time
from typing import List
from urllib.parse import urljoin

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)


def collect_urls(driver, config: dict, limit: int = None) -> List[str]:
    """
    Collect all product URLs from the brand's category pages.

    Args:
        driver: Selenium WebDriver instance.
        config: Parsed YAML config dict.
        limit: Optional max number of URLs to return (for testing).

    Returns:
        Deduplicated list of product page URLs.
    """
    pagination = config.get("pagination", {})
    pagination_type = pagination.get("type", "none")
    category_urls = config.get("category_urls", [])
    selectors = config.get("selectors", {})
    product_link_selector = selectors.get("product_link")
    base_url = config.get("base_url", "")
    delay = config.get("anti_bot", {}).get("page_delay", 2)

    all_urls = []

    for cat_url in category_urls:
        logger.info(f"Collecting URLs from category: {cat_url}")
        try:
            urls = _collect_from_category(
                driver, cat_url, pagination_type, pagination,
                product_link_selector, base_url, delay
            )
            logger.info(f"  Found {len(urls)} URLs in {cat_url}")
            all_urls.extend(urls)
        except Exception as e:
            logger.error(f"  Failed to collect from {cat_url}: {e}")

    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for url in all_urls:
        if url not in seen:
            seen.add(url)
            deduped.append(url)

    logger.info(f"Total unique product URLs collected: {len(deduped)}")

    if limit:
        deduped = deduped[:limit]
        logger.info(f"Limiting to {limit} URLs for testing.")

    return deduped


def _collect_from_category(
    driver, cat_url: str, pagination_type: str,
    pagination: dict, product_link_selector: str,
    base_url: str, delay: float
) -> List[str]:
    """Handle a single category page with the configured pagination type."""

    driver.get(cat_url)
    time.sleep(delay)

    if pagination_type == "next_button":
        return _paginate_next_button(
            driver, pagination, product_link_selector, base_url, delay
        )
    elif pagination_type == "load_more":
        return _paginate_load_more(
            driver, pagination, product_link_selector, base_url, delay
        )
    elif pagination_type == "infinite_scroll":
        return _paginate_infinite_scroll(
            driver, pagination, product_link_selector, base_url, delay
        )
    else:
        # "none" — single page
        return _extract_links(driver, product_link_selector, base_url)


def _paginate_next_button(driver, pagination, selector, base_url, delay) -> List[str]:
    next_selector = pagination.get("next_button_selector")
    max_pages = pagination.get("max_pages", 100)
    urls = []

    for page_num in range(1, max_pages + 1):
        links = _extract_links(driver, selector, base_url)
        urls.extend(links)
        logger.debug(f"    Page {page_num}: {len(links)} links")

        try:
            next_btn = driver.find_element(By.CSS_SELECTOR, next_selector)
            if not next_btn.is_displayed() or not next_btn.is_enabled():
                break
            next_btn.click()
            time.sleep(delay)
        except NoSuchElementException:
            logger.debug("    No next button found — end of pagination.")
            break

    return urls


def _paginate_load_more(driver, pagination, selector, base_url, delay) -> List[str]:
    load_more_selector = pagination.get("load_more_selector")
    max_clicks = pagination.get("max_clicks", 50)

    for click_num in range(max_clicks):
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, load_more_selector))
            )
            driver.execute_script("arguments[0].click();", btn)
            logger.debug(f"    Load more click #{click_num + 1}")
            time.sleep(delay)
        except (NoSuchElementException, TimeoutException):
            logger.debug("    Load more button gone — all products loaded.")
            break

    return _extract_links(driver, selector, base_url)


def _paginate_infinite_scroll(driver, pagination, selector, base_url, delay) -> List[str]:
    max_scrolls = pagination.get("max_scrolls", 50)
    scroll_pause = pagination.get("scroll_pause", 2)

    last_height = driver.execute_script("return document.body.scrollHeight")

    for scroll_num in range(max_scrolls):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(scroll_pause)
        new_height = driver.execute_script("return document.body.scrollHeight")
        logger.debug(f"    Scroll #{scroll_num + 1}: height {last_height} -> {new_height}")
        if new_height == last_height:
            logger.debug("    Page height unchanged — end of scroll.")
            break
        last_height = new_height

    return _extract_links(driver, selector, base_url)


def _extract_links(driver, css_selector: str, base_url: str) -> List[str]:
    """Extract href values from all matching elements, resolving relative URLs."""
    try:
        elements = driver.find_elements(By.CSS_SELECTOR, css_selector)
    except Exception as e:
        logger.warning(f"    Failed to find elements with selector '{css_selector}': {e}")
        return []

    urls = []
    for el in elements:
        try:
            href = el.get_attribute("href") or ""
            if href:
                full_url = urljoin(base_url, href)
                urls.append(full_url)
        except Exception:
            pass

    return urls
