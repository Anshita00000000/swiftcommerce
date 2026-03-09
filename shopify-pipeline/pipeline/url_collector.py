"""
url_collector.py
Visits brand category pages and collects all product URLs.

Two-pass architecture:
  Pass 1 — Load primary_url (or category_urls) once, extracting product URL
            and product code from each card in that single load.
  Pass 2 — Load each gender_url once, extracting URL and code for
            cross-referencing only (not added to master list).
  Cross-reference — pure dict operations, no additional page loads.

Supports pagination types: next_button, load_more, infinite_scroll, url_param, none.
"""

import logging
import random
import re
import time
from datetime import datetime
from typing import Dict, List, Tuple

from urllib.parse import urljoin

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def collect_urls(
    driver, config: dict, limit: int = None, ctx=None
) -> Tuple[List[str], Dict[str, str], Dict[str, str]]:
    """
    Collect all product URLs from the brand's category pages.

    Args:
        driver:  Selenium WebDriver instance.
        config:  Adapted config dict (output of config_adapter.adapt()).
        limit:   Optional max number of URLs to return (applied after all
                 collection and cross-referencing so maps are always complete).
        ctx:     Optional RunContext — used for event logging and saving
                 urls_collected.txt.

    Returns:
        Tuple of:
          urls           — ordered, deduped list of canonical product URLs
          url_gender_map — {normalized_url: "Men"|"Women"|"Unisex"|""}
          url_code_map   — {normalized_url: "SSC929"} ("" if unknown)
    """
    product_link_selector = config.get("selectors", {}).get("product_link", "")
    base_url = config.get("base_url", "")
    anti_bot = config.get("anti_bot", {})
    primary_url = config.get("primary_url", "")
    primary_pagination = config.get("primary_pagination", config.get("pagination", {}))
    category_urls = config.get("category_urls", [])
    gender_urls_cfg = config.get("gender_urls", [])
    code_extraction = config.get("code_extraction", {})
    default_gender = config.get("default_gender", "")

    # ------------------------------------------------------------------
    # PASS 1 — Primary collection
    # Builds master_entries: ordered list of unique product entries.
    # Each entry: {url, normalized_url, code}
    # ------------------------------------------------------------------
    master_entries: List[Dict] = []
    seen_norms: set = set()

    def _add_pairs_to_master(pairs: List[Dict]) -> None:
        for pair in pairs:
            norm = _normalize_url(pair["url"])
            if norm not in seen_norms:
                seen_norms.add(norm)
                master_entries.append({
                    "url": pair["url"],
                    "normalized_url": norm,
                    "code": pair["code"],
                })

    if primary_url:
        logger.info(f"Pass 1: Scraping primary URL: {primary_url}")
        pairs = _scrape_collection(
            driver, primary_url, primary_pagination,
            code_extraction, anti_bot, product_link_selector, base_url,
        )
        _add_pairs_to_master(pairs)
    else:
        # Fallback: treat each category URL as a master-list source
        for cat_url in category_urls:
            logger.info(f"Pass 1: Scraping category URL: {cat_url}")
            pag = config.get("pagination", {})
            pairs = _scrape_collection(
                driver, cat_url, pag,
                code_extraction, anti_bot, product_link_selector, base_url,
            )
            _add_pairs_to_master(pairs)

    n_with_code = sum(1 for e in master_entries if e["code"])
    msg = (
        f"Primary collection: {len(master_entries)} URLs collected, "
        f"{n_with_code} with product codes"
    )
    logger.info(msg)
    if ctx:
        ctx.log_event(msg)

    # ------------------------------------------------------------------
    # PASS 2 — Gender collections
    # Load each gender URL once; results used ONLY for cross-referencing.
    # ------------------------------------------------------------------
    gender_collection_results: List[Dict] = []

    for g_entry in gender_urls_cfg:
        g_url = g_entry.get("url", "")
        g_gender = g_entry.get("gender", "")
        g_pag = g_entry.get("pagination", config.get("pagination", {}))

        if not g_url:
            continue

        logger.info(f"Pass 2: Scraping {g_gender} collection: {g_url}")
        pairs = _scrape_collection(
            driver, g_url, g_pag,
            code_extraction, anti_bot, product_link_selector, base_url,
        )
        gender_collection_results.append({
            "gender": g_gender,
            "url": g_url,
            "pairs": pairs,
        })

        msg = f"{g_gender} collection ({g_url}): {len(pairs)} URLs collected"
        logger.info(msg)
        if ctx:
            ctx.log_event(msg)

    # ------------------------------------------------------------------
    # CROSS-REFERENCE — pure dict operations, no page loads
    # ------------------------------------------------------------------
    url_gender_map, url_code_map, unknown_gender, missing_from_primary = (
        _build_gender_map(master_entries, gender_collection_results, default_gender)
    )

    # If no gender collections were configured, infer from category URL keywords
    # (fallback for brands that have neither primary_url nor gender_urls)
    if not gender_urls_cfg and not primary_url:
        for cat_url in category_urls:
            inferred = _gender_from_category_url(cat_url)
            if inferred:
                for e in master_entries:
                    norm = e["normalized_url"]
                    if norm not in url_gender_map or not url_gender_map[norm]:
                        url_gender_map[norm] = inferred

    # ------------------------------------------------------------------
    # Logging — gender assignment summary
    # ------------------------------------------------------------------
    n_men    = sum(1 for v in url_gender_map.values() if v == "Men")
    n_women  = sum(1 for v in url_gender_map.values() if v == "Women")
    n_unisex = sum(1 for v in url_gender_map.values() if v == "Unisex")
    n_unknown = sum(1 for v in url_gender_map.values() if v == "")

    msg = (
        f"Gender assignment: {n_men} Men, {n_women} Women, "
        f"{n_unisex} Unisex, {n_unknown} unknown"
    )
    logger.info(msg)
    if ctx:
        ctx.log_event(msg)

    if unknown_gender:
        msg = (
            f"WARNING: {len(unknown_gender)} products have unknown gender — "
            f"not found in any gender collection: {unknown_gender[:10]}"
        )
        logger.warning(msg)
        if ctx:
            ctx.log_event(msg)

    if missing_from_primary:
        msg = (
            f"WARNING: {len(missing_from_primary)} products found in gender "
            f"collections but missing from primary collection — may be out of "
            f"stock or unlisted: {missing_from_primary[:10]}"
        )
        logger.warning(msg)
        if ctx:
            ctx.log_event(msg)

    # ------------------------------------------------------------------
    # Save urls_collected.txt via ctx
    # ------------------------------------------------------------------
    all_urls = [e["normalized_url"] for e in master_entries]

    if ctx:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        brand = config.get("brand_name", "")
        header = (
            f"# {timestamp} | {brand} | {len(all_urls)} URLs | "
            f"{n_with_code} codes | {n_men} Men | {n_women} Women | "
            f"{n_unisex} Unisex | {n_unknown} unknown gender"
        )
        lines = [header]
        men_lines: List[str] = []
        women_lines: List[str] = []
        for url in all_urls:
            norm = _normalize_url(url)
            code = url_code_map.get(norm, "")
            gender = url_gender_map.get(norm, "")
            lines.append(
                f"{url}  |  code={code or '?'}  |  gender={gender or 'unknown'}"
            )
            if gender == "Men":
                men_lines.append(url)
            elif gender == "Women":
                women_lines.append(url)
        ctx.path("urls_collected.txt").write_text("\n".join(lines), encoding="utf-8")

        men_header = f"# {timestamp} | {brand} | {len(men_lines)} Men URLs"
        ctx.path("men_urls.txt").write_text(
            "\n".join([men_header] + men_lines), encoding="utf-8"
        )

        women_header = f"# {timestamp} | {brand} | {len(women_lines)} Women URLs"
        ctx.path("women_urls.txt").write_text(
            "\n".join([women_header] + women_lines), encoding="utf-8"
        )

    logger.info(f"Total unique product URLs collected: {len(all_urls)}")

    # Apply limit AFTER all collection and cross-referencing
    if limit:
        all_urls = all_urls[:limit]
        logger.info(f"Limiting to {limit} URLs for testing.")

    return all_urls, url_gender_map, url_code_map


# ---------------------------------------------------------------------------
# Cross-reference: build gender and code maps from master + gender results
# ---------------------------------------------------------------------------

def _build_gender_map(
    master_entries: List[Dict],
    gender_collection_results: List[Dict],
    default_gender: str,
) -> Tuple[Dict[str, str], Dict[str, str], List[str], List[str]]:
    """
    Match master entries against gender collection results.

    Matching uses normalized_url equality first; falls back to code equality
    if both the master entry and the gender collection entry have a non-empty code.

    Returns:
        url_gender_map       — {normalized_url: "Men"|"Women"|"Unisex"|""}
        url_code_map         — {normalized_url: code}
        unknown_gender       — list of normalized_urls with no gender match
                               and no default_gender set
        missing_from_primary — list of gender-collection normalized_urls
                               not found in master
    """
    # Build per-gender sets of normalized URLs and codes
    gender_url_sets: Dict[str, set] = {}
    gender_code_sets: Dict[str, set] = {}
    all_gender_norms: set = set()

    for gcr in gender_collection_results:
        gender = gcr["gender"]
        gender_url_sets.setdefault(gender, set())
        gender_code_sets.setdefault(gender, set())
        for pair in gcr["pairs"]:
            norm = _normalize_url(pair["url"])
            gender_url_sets[gender].add(norm)
            all_gender_norms.add(norm)
            if pair["code"]:
                gender_code_sets[gender].add(pair["code"])

    master_norms = {e["normalized_url"] for e in master_entries}

    url_gender_map: Dict[str, str] = {}
    url_code_map: Dict[str, str] = {}
    unknown_gender: List[str] = []

    for entry in master_entries:
        norm = entry["normalized_url"]
        code = entry["code"]
        url_code_map[norm] = code

        matched_genders: set = set()
        for gender, g_url_set in gender_url_sets.items():
            if norm in g_url_set:
                matched_genders.add(gender)
            elif code and code in gender_code_sets.get(gender, set()):
                matched_genders.add(gender)

        if not matched_genders:
            url_gender_map[norm] = default_gender
            if not default_gender:
                unknown_gender.append(norm)
        elif matched_genders == {"Men"}:
            url_gender_map[norm] = "Men"
        elif matched_genders == {"Women"}:
            url_gender_map[norm] = "Women"
        else:
            url_gender_map[norm] = "Unisex"

    missing_from_primary = [u for u in all_gender_norms if u not in master_norms]

    return url_gender_map, url_code_map, unknown_gender, missing_from_primary


# ---------------------------------------------------------------------------
# Pass 1 / Pass 2 scraping — single entry point per collection URL
# ---------------------------------------------------------------------------

def _scrape_collection(
    driver,
    url: str,
    pagination_config: dict,
    code_extraction: dict,
    anti_bot: dict,
    product_link_selector: str,
    base_url: str,
) -> List[Dict]:
    """
    Load a collection URL with the configured pagination strategy.
    Extracts product URL and product code simultaneously from each card.

    Returns List[Dict] of {"url": ..., "code": ...} pairs.
    """
    pag_type = pagination_config.get("type", "none")
    first_load_wait = anti_bot.get("first_load_wait", anti_bot.get("page_delay", 3))

    driver.get(url)
    time.sleep(first_load_wait)

    kwargs = dict(
        pagination=pagination_config,
        selector=product_link_selector,
        base_url=base_url,
        anti_bot=anti_bot,
        code_extraction=code_extraction,
    )

    if pag_type == "next_button":
        return _paginate_next_button(driver, **kwargs)
    elif pag_type == "load_more":
        return _paginate_load_more(driver, **kwargs)
    elif pag_type == "infinite_scroll":
        return _paginate_infinite_scroll(driver, **kwargs)
    elif pag_type == "url_param":
        return _paginate_url_param(driver, base_cat_url=url, **kwargs)
    else:
        # "none" — single page
        pairs = _extract_links_with_codes(
            driver, product_link_selector, base_url, code_extraction
        )
        n_codes = sum(1 for p in pairs if p["code"])
        logger.debug(f"  Page 1: found {len(pairs)} links, {n_codes} with codes")
        return pairs


# ---------------------------------------------------------------------------
# Pagination strategies — each returns List[Dict] of {url, code}
# ---------------------------------------------------------------------------

def _paginate_next_button(
    driver, pagination, selector, base_url, anti_bot, code_extraction
) -> List[Dict]:
    next_selector = pagination.get("next_button_selector", "")
    max_pages = pagination.get("max_pages", 100)
    pairs: List[Dict] = []

    for page_num in range(1, max_pages + 1):
        page_pairs = _extract_links_with_codes(driver, selector, base_url, code_extraction)
        n_codes = sum(1 for p in page_pairs if p["code"])
        logger.debug(f"  Page {page_num}: found {len(page_pairs)} links, {n_codes} with codes")
        pairs.extend(page_pairs)

        try:
            next_btn = driver.find_element(By.CSS_SELECTOR, next_selector)
            if not next_btn.is_displayed() or not next_btn.is_enabled():
                break
            next_btn.click()
            time.sleep(_random_delay(anti_bot))
        except NoSuchElementException:
            logger.debug("  No next button found — end of pagination.")
            break

    return pairs


def _paginate_load_more(
    driver, pagination, selector, base_url, anti_bot, code_extraction
) -> List[Dict]:
    load_more_selector = pagination.get("load_more_selector", "")
    max_clicks = pagination.get("max_clicks", 50)

    for click_num in range(max_clicks):
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, load_more_selector))
            )
            driver.execute_script("arguments[0].click();", btn)
            logger.debug(f"  Load more click #{click_num + 1}")
            time.sleep(_random_delay(anti_bot))
        except (NoSuchElementException, TimeoutException):
            logger.debug("  Load more button gone — all products loaded.")
            break

    pairs = _extract_links_with_codes(driver, selector, base_url, code_extraction)
    n_codes = sum(1 for p in pairs if p["code"])
    logger.debug(f"  Page 1: found {len(pairs)} links, {n_codes} with codes (after load-more)")
    return pairs


def _paginate_infinite_scroll(
    driver, pagination, selector, base_url, anti_bot, code_extraction
) -> List[Dict]:
    max_scrolls = pagination.get("max_scrolls", 60)
    scroll_pause = pagination.get("scroll_pause", anti_bot.get("scroll_pause", 3.0))
    slow_scroll = anti_bot.get("slow_scroll", False)

    last_height = driver.execute_script("return document.body.scrollHeight")

    for scroll_num in range(max_scrolls):
        if slow_scroll:
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
        logger.debug(f"  Scroll #{scroll_num + 1}: height {last_height} -> {new_height}")

        if new_height == last_height:
            logger.debug("  Page height unchanged — end of scroll.")
            break
        last_height = new_height

    pairs = _extract_links_with_codes(driver, selector, base_url, code_extraction)
    n_codes = sum(1 for p in pairs if p["code"])
    logger.debug(f"  Page 1: found {len(pairs)} links, {n_codes} with codes (after scroll)")
    return pairs


def _paginate_url_param(
    driver, base_cat_url: str, pagination: dict,
    selector: str, base_url: str, anti_bot: dict, code_extraction: dict,
) -> List[Dict]:
    """
    Tissot-style: append ?page=1, ?page=2 ... to the category URL.
    Stop when a page yields no new links.
    """
    param_template = pagination.get("param_template", "?page={}")
    max_pages = pagination.get("max_pages", 20)
    base_cat_url = base_cat_url.split("?")[0]  # strip any existing query string

    all_pairs: List[Dict] = []
    seen_norms: set = set()

    for page_num in range(1, max_pages + 1):
        param = param_template.replace("{}", str(page_num))
        if "?" in base_cat_url:
            page_url = base_cat_url + "&" + param.lstrip("?&")
        else:
            page_url = base_cat_url + param

        logger.debug(f"  url_param page {page_num}: {page_url}")
        driver.get(page_url)
        time.sleep(_random_delay(anti_bot))

        pairs = _extract_links_with_codes(driver, selector, base_url, code_extraction)
        new_pairs = [p for p in pairs if _normalize_url(p["url"]) not in seen_norms]

        if not new_pairs:
            logger.debug(f"  No new links on page {page_num} — end of pagination.")
            break

        n_codes = sum(1 for p in new_pairs if p["code"])
        logger.debug(
            f"  Page {page_num}: found {len(new_pairs)} links, {n_codes} with codes"
        )

        for p in new_pairs:
            seen_norms.add(_normalize_url(p["url"]))
        all_pairs.extend(new_pairs)

    return all_pairs


# ---------------------------------------------------------------------------
# Link + code extraction — one pass over the DOM elements
# ---------------------------------------------------------------------------

def _extract_links_with_codes(
    driver, css_selector: str, base_url: str, code_extraction: dict
) -> List[Dict]:
    """
    Find all product link elements matching css_selector.
    For each element, extract the href AND attempt code extraction in the
    same pass (no second page load).

    Returns List[Dict] of {"url": ..., "code": ...}.
    """
    if not css_selector:
        logger.warning("  No product_link selector configured.")
        return []

    try:
        elements = driver.find_elements(By.CSS_SELECTOR, css_selector)
    except Exception as e:
        logger.warning(f"  Failed to find elements with selector '{css_selector}': {e}")
        return []

    pairs: List[Dict] = []
    for el in elements:
        try:
            href = el.get_attribute("href") or ""
            if not href:
                continue
            full_url = urljoin(base_url, href)
            code = _extract_code_from_element(driver, el, href, code_extraction)
            pairs.append({"url": full_url, "code": code})
        except Exception:
            pass

    return pairs


def _extract_code_from_element(
    driver, el, href: str, code_extraction: dict
) -> str:
    """
    Extract product code from the link element (or its ancestors) or from href.

    source == "attribute":
      Walk up from the <a> element through up to 4 ancestors.
      At each level call get_attribute(attribute). Return first non-empty match.

    source == "url":
      Apply pattern to the href string.
    """
    if not code_extraction:
        return ""

    source = code_extraction.get("source", "")
    normalize_method = code_extraction.get("normalize", "")

    if source == "attribute":
        attribute = code_extraction.get("attribute", "")
        if not attribute:
            return ""

        current = el
        for _ in range(5):  # <a> itself + up to 4 ancestors
            try:
                val = current.get_attribute(attribute)
                if val:
                    return _normalize_code(val, normalize_method)
            except Exception:
                pass
            try:
                current = driver.execute_script(
                    "return arguments[0].parentElement", current
                )
                if current is None:
                    break
            except Exception:
                break
        return ""

    elif source == "url":
        pattern = code_extraction.get("pattern", "")
        if pattern == "last_segment_after_final_hyphen":
            segment = href.rstrip("/").split("/")[-1]
            segment = re.sub(r"\.[a-z]{2,4}$", "", segment)  # strip extension
            parts = segment.split("-")
            code = parts[-1] if len(parts) > 1 else segment
            return _normalize_code(code, normalize_method)
        return ""

    return ""


def _normalize_code(code: str, method: str) -> str:
    """Apply normalization to an extracted code string."""
    if not code:
        return ""
    code = code.strip()
    if method == "uppercase":
        return code.upper()
    if method == "lowercase":
        return code.lower()
    return code


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gender_from_category_url(cat_url: str) -> str:
    """
    Infer gender from category URL keywords.
    Returns 'Men', 'Women', or '' (unknown).
    Used only when gender_urls is empty and primary_url is not set.
    """
    url_lower = cat_url.lower()
    is_men   = any(kw in url_lower for kw in ["/mens", "/men/", "/men-", "men's", "gents"])
    is_women = any(kw in url_lower for kw in ["/womens", "/women/", "/women-", "women's", "ladies"])
    if is_men and not is_women:
        return "Men"
    if is_women and not is_men:
        return "Women"
    return ""


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
