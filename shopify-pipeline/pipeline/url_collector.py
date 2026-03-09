"""
url_collector.py
Visits brand category pages and collects all product URLs.
Supports pagination types: next_button, load_more, infinite_scroll, url_param, none.
"""

import logging
import random
import time
from typing import List
from urllib.parse import urljoin

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)


def _gender_from_category_url(cat_url: str) -> str:
    """
    Infer gender from category URL keywords.
    Returns 'Men', 'Women', or '' (unknown).
    """
    url_lower = cat_url.lower()
    is_men   = any(kw in url_lower for kw in ["/mens", "/men/", "/men-", "men's", "gents"])
    is_women = any(kw in url_lower for kw in ["/womens", "/women/", "/women-", "women's", "ladies"])
    if is_men and not is_women:
        return "Men"
    if is_women and not is_men:
        return "Women"
    return ""


def collect_urls(driver, config: dict, limit: int = None):
    """
    Collect all product URLs from the brand's category pages.

    Args:
        driver: Selenium WebDriver instance.
        config: Adapted config dict (output of config_adapter.adapt()).
        limit: Optional max number of URLs to return (for testing).

    Returns:
        Tuple of:
          - deduped list of product URLs
          - url_gender_map: {normalized_url: 'Men'|'Women'|'Unisex'|''}
    """
    pagination = config.get("pagination", {})
    pagination_type = pagination.get("type", "none")
    category_urls = config.get("category_urls", [])
    selectors = config.get("selectors", {})
    product_link_selector = selectors.get("product_link", "")
    base_url = config.get("base_url", "")
    anti_bot = config.get("anti_bot", {})

    # Track which collections each URL appears in
    url_to_genders: dict = {}  # normalized_url -> set of genders

    for cat_url in category_urls:
        cat_gender = _gender_from_category_url(cat_url)
        logger.info(f"Collecting URLs from category: {cat_url} (gender={cat_gender or 'unknown'})")
        try:
            urls = _collect_from_category(
                driver, cat_url, pagination_type, pagination,
                product_link_selector, base_url, anti_bot
            )
            logger.info(f"  Found {len(urls)} URLs in {cat_url}")
            for url in urls:
                norm = _normalize_url(url)
                if norm not in url_to_genders:
                    url_to_genders[norm] = set()
                if cat_gender:
                    url_to_genders[norm].add(cat_gender)
        except Exception as e:
            logger.error(f"  Failed to collect from {cat_url}: {e}")

    # Resolve gender per URL
    url_gender_map = {}
    for norm_url, genders in url_to_genders.items():
        if len(genders) == 0:
            url_gender_map[norm_url] = ""
        elif genders == {"Men"}:
            url_gender_map[norm_url] = "Men"
        elif genders == {"Women"}:
            url_gender_map[norm_url] = "Women"
        else:
            url_gender_map[norm_url] = "Unisex"  # appeared in both men + women collections

    # Deduplicate URLs while preserving order
    seen = set()
    deduped = []
    for norm_url in url_to_genders:
        if norm_url not in seen:
            seen.add(norm_url)
            # Restore full https URL
            deduped.append(norm_url if norm_url.startswith("http") else "https:" + norm_url)

    logger.info(f"Total unique product URLs collected: {len(deduped)}")

    if limit:
        deduped = deduped[:limit]
        logger.info(f"Limiting to {limit} URLs for testing.")

    return deduped, url_gender_map


def _collect_from_category(
    driver, cat_url: str, pagination_type: str,
    pagination: dict, product_link_selector: str,
    base_url: str, anti_bot: dict
) -> List[str]:
    """Handle a single category page with the configured pagination type."""
    first_load_wait = anti_bot.get("first_load_wait", anti_bot.get("page_delay", 3))

    driver.get(cat_url)
    time.sleep(first_load_wait)

    if pagination_type == "next_button":
        return _paginate_next_button(
            driver, pagination, product_link_selector, base_url, anti_bot
        )
    elif pagination_type == "load_more":
        return _paginate_load_more(
            driver, pagination, product_link_selector, base_url, anti_bot
        )
    elif pagination_type == "infinite_scroll":
        return _paginate_infinite_scroll(
            driver, pagination, product_link_selector, base_url, anti_bot
        )
    elif pagination_type == "url_param":
        return _paginate_url_param(
            driver, cat_url, pagination, product_link_selector, base_url, anti_bot
        )
    else:
        # "none" — single page
        return _extract_links(driver, product_link_selector, base_url)


def _paginate_next_button(driver, pagination, selector, base_url, anti_bot) -> List[str]:
    next_selector = pagination.get("next_button_selector", "")
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
            time.sleep(_random_delay(anti_bot))
        except NoSuchElementException:
            logger.debug("    No next button found — end of pagination.")
            break

    return urls


def _paginate_load_more(driver, pagination, selector, base_url, anti_bot) -> List[str]:
    load_more_selector = pagination.get("load_more_selector", "")
    max_clicks = pagination.get("max_clicks", 50)

    for click_num in range(max_clicks):
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, load_more_selector))
            )
            driver.execute_script("arguments[0].click();", btn)
            logger.debug(f"    Load more click #{click_num + 1}")
            time.sleep(_random_delay(anti_bot))
        except (NoSuchElementException, TimeoutException):
            logger.debug("    Load more button gone — all products loaded.")
            break

    return _extract_links(driver, selector, base_url)


def _paginate_infinite_scroll(driver, pagination, selector, base_url, anti_bot) -> List[str]:
    max_scrolls = pagination.get("max_scrolls", 60)
    scroll_pause = pagination.get("scroll_pause", anti_bot.get("scroll_pause", 3.0))
    slow_scroll = anti_bot.get("slow_scroll", False)

    last_height = driver.execute_script("return document.body.scrollHeight")

    for scroll_num in range(max_scrolls):
        if slow_scroll:
            # Scroll in small steps to mimic human behavior
            current = driver.execute_script("return window.pageYOffset")
            remaining = last_height - current
            step = max(300, remaining // 5)
            for _ in range(5):
                driver.execute_script(f"window.scrollBy(0, {step});")
                time.sleep(0.3)
        else:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

        time.sleep(scroll_pause)
        new_height = driver.execute_script("return document.body.scrollHeight")
        logger.debug(f"    Scroll #{scroll_num + 1}: height {last_height} -> {new_height}")

        if new_height == last_height:
            logger.debug("    Page height unchanged — end of scroll.")
            break
        last_height = new_height

    return _extract_links(driver, selector, base_url)


def _paginate_url_param(
    driver, base_cat_url: str, pagination: dict,
    selector: str, base_url: str, anti_bot: dict
) -> List[str]:
    """
    Tissot-style: append ?page=1, ?page=2 ... to the category URL.
    Stop when a page yields no new links or returns to same page.
    """
    param_template = pagination.get("param_template", "?page={}")
    max_pages = pagination.get("max_pages", 20)
    # Strip existing query string to avoid duplication
    base_cat_url = base_cat_url.split("?")[0]

    all_urls = []
    seen_norms = set()

    for page_num in range(1, max_pages + 1):
        param = param_template.replace("{}", str(page_num))
        if "?" in base_cat_url:
            page_url = base_cat_url + "&" + param.lstrip("?&")
        else:
            page_url = base_cat_url + param

        logger.debug(f"    url_param page {page_num}: {page_url}")
        driver.get(page_url)
        time.sleep(_random_delay(anti_bot))

        links = _extract_links(driver, selector, base_url)
        new_links = [u for u in links if _normalize_url(u) not in seen_norms]

        if not new_links:
            logger.debug(f"    No new links on page {page_num} — end of pagination.")
            break

        for u in new_links:
            seen_norms.add(_normalize_url(u))
        all_urls.extend(new_links)
        logger.debug(f"    Page {page_num}: {len(new_links)} new links")

    return all_urls


def _extract_links(driver, css_selector: str, base_url: str) -> List[str]:
    """Extract href values from all matching elements, resolving relative URLs."""
    if not css_selector:
        logger.warning("  No product_link selector configured.")
        return []

    try:
        elements = driver.find_elements(By.CSS_SELECTOR, css_selector)
    except Exception as e:
        logger.warning(f"  Failed to find elements with selector '{css_selector}': {e}")
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


def _random_delay(anti_bot: dict) -> float:
    """Return a random delay between configured min/max, or fall back to page_delay."""
    d_min = anti_bot.get("random_delay_min")
    d_max = anti_bot.get("random_delay_max")
    if d_min is not None and d_max is not None:
        return random.uniform(float(d_min), float(d_max))
    return float(anti_bot.get("page_delay", 2))


def _normalize_url(url: str) -> str:
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[7:]
    return url.rstrip("/")
