"""
url_collector.py

Visits brand category pages and collects all product URLs.

Three-pass architecture:
  Pass 1 — Load primary_url with primary_pagination. Extract product link,
            product code, and listing availability from each card.
            This is the master list.
  Pass 2 — Load each gender_url. Extract link + code only (not added to
            master list — used only for gender cross-reference).
  Pass 3 — Call gender_mapper.assign_genders() — pure dict operations,
            no additional page loads.

Return value
────────────
  (all_urls, url_data_map)

  all_urls     — deduped, ordered list of normalized URL strings
  url_data_map — {normalized_url: {url, sku, gender, availability}}
                   sku          : product code from listing page, or ""
                   gender       : "Men"|"Women"|"Unisex"|""
                   availability : "available"|"sold_out"|"" (recorded only,
                                  never used to filter)

Output files written via ctx.path()
────────────────────────────────────
  url_collection.json  — meta block + full URL array
  gender_map.json      — {normalized_url: gender}

Pagination types supported: infinite_scroll, next_button, load_more,
                             url_param, none.
"""

import json
import logging
import random
import re
import time
from typing import Dict, List, Optional, Tuple

from urllib.parse import urljoin

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from pipeline import gender_mapper

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def collect_urls(
    driver,
    config: dict,
    limit: Optional[int] = None,
    ctx=None,
) -> Tuple[List[str], Dict[str, Dict]]:
    """
    Collect all product URLs from the brand's category pages.

    Args:
        driver:  Selenium WebDriver instance.
        config:  Adapted config dict (output of config_adapter.adapt()).
        limit:   Optional max number of URLs returned.  Applied AFTER all
                 collection and cross-referencing so maps are always complete.
        ctx:     Optional RunContext — used for event logging and JSON output.

    Returns:
        Tuple of:
          all_urls     — ordered, deduped list of normalized URL strings
          url_data_map — {normalized_url: {url, sku, gender, availability}}
    """
    product_link_selector = config.get("selectors", {}).get("product_link", "")
    base_url              = config.get("base_url", "")
    anti_bot              = config.get("anti_bot", {})
    primary_url           = config.get("primary_url", "")
    primary_pagination    = config.get("primary_pagination", config.get("pagination", {}))
    category_urls         = config.get("category_urls", [])
    gender_urls_cfg       = config.get("gender_urls", [])
    code_extraction       = config.get("code_extraction", {})
    listing_avail_cfg     = config.get("listing_availability", {})
    default_gender        = config.get("default_gender", "")

    # ------------------------------------------------------------------
    # PASS 1 — Primary collection
    # Builds master_entries: ordered list of unique product entries.
    # Each entry: {url, normalized_url, code, availability}
    # ------------------------------------------------------------------
    master_entries: List[Dict] = []
    seen_norms: set = set()

    def _add_pairs_to_master(pairs: List[Dict]) -> None:
        for pair in pairs:
            norm = _normalize_url(pair["url"])
            if norm not in seen_norms:
                seen_norms.add(norm)
                master_entries.append({
                    "url":            pair["url"],
                    "normalized_url": norm,
                    "code":           pair.get("code", ""),
                    "availability":   pair.get("availability", ""),
                })

    if primary_url:
        logger.info("Pass 1: Scraping primary URL: %s", primary_url)
        pairs = _scrape_collection(
            driver, primary_url, primary_pagination,
            code_extraction, listing_avail_cfg, anti_bot,
            product_link_selector, base_url,
        )
        _add_pairs_to_master(pairs)
    else:
        # Fallback: treat each category URL as a master-list source
        for cat_url in category_urls:
            logger.info("Pass 1: Scraping category URL: %s", cat_url)
            pairs = _scrape_collection(
                driver, cat_url, config.get("pagination", {}),
                code_extraction, listing_avail_cfg, anti_bot,
                product_link_selector, base_url,
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
        g_url    = g_entry.get("url", "")
        g_gender = g_entry.get("gender", "")
        g_pag    = g_entry.get("pagination", config.get("pagination", {}))

        if not g_url:
            continue

        logger.info("Pass 2: Scraping %s collection: %s", g_gender, g_url)
        pairs = _scrape_collection(
            driver, g_url, g_pag,
            code_extraction, {}, anti_bot,   # no availability needed for gender refs
            product_link_selector, base_url,
        )
        raw_count = len(pairs)

        # Dedup within the gender collection for accurate unique-count logging
        seen_g: set = set()
        unique_pairs: List[Dict] = []
        for p in pairs:
            g_norm = _normalize_url(p["url"])
            if g_norm not in seen_g:
                seen_g.add(g_norm)
                unique_pairs.append(p)
        dedup_count = len(unique_pairs)
        scroll_dups = raw_count - dedup_count

        gender_collection_results.append({
            "gender": g_gender,
            "url":    g_url,
            "pairs":  unique_pairs,
        })

        if scroll_dups > 0:
            msg = (
                f"{g_gender} collection ({g_url}): "
                f"{dedup_count} unique URLs "
                f"({raw_count} raw, {scroll_dups} scroll duplicates removed)"
            )
        else:
            msg = f"{g_gender} collection ({g_url}): {dedup_count} unique URLs"
        logger.info(msg)
        if ctx:
            ctx.log_event(msg)

    # ------------------------------------------------------------------
    # PASS 3 — Gender assignment (pure dict operations, no page loads)
    # ------------------------------------------------------------------
    url_gender_map: Dict[str, str] = gender_mapper.assign_genders(
        master_entries, gender_collection_results, default_gender
    )

    # Fallback: if no gender collections configured AND no primary_url,
    # infer gender from the category URL keywords themselves.
    if not gender_urls_cfg and not primary_url:
        for cat_url in category_urls:
            inferred = gender_mapper.infer_gender_from_url(cat_url)
            if inferred:
                for e in master_entries:
                    norm = e["normalized_url"]
                    if not url_gender_map.get(norm):
                        url_gender_map[norm] = inferred

    # ------------------------------------------------------------------
    # Gender summary logging
    # ------------------------------------------------------------------
    n_men     = sum(1 for v in url_gender_map.values() if v == "Men")
    n_women   = sum(1 for v in url_gender_map.values() if v == "Women")
    n_unisex  = sum(1 for v in url_gender_map.values() if v == "Unisex")
    n_unknown = sum(1 for v in url_gender_map.values() if v == "")

    msg = (
        f"Gender assignment: {n_men} Men, {n_women} Women, "
        f"{n_unisex} Unisex, {n_unknown} unknown"
    )
    logger.info(msg)
    if ctx:
        ctx.log_event(msg)

    unknown_urls = gender_mapper.get_unknown_urls(url_gender_map)
    if unknown_urls:
        msg = (
            f"WARNING: {len(unknown_urls)} products have unknown gender "
            f"(not found in any gender collection): {unknown_urls[:10]}"
        )
        logger.warning(msg)
        if ctx:
            ctx.log_event(msg)

    # ------------------------------------------------------------------
    # Build all_urls and url_data_map
    # ------------------------------------------------------------------
    all_urls: List[str] = [e["normalized_url"] for e in master_entries]

    url_data_map: Dict[str, Dict] = {
        e["normalized_url"]: {
            "url":          e["url"],
            "sku":          e["code"],
            "gender":       url_gender_map.get(e["normalized_url"], ""),
            "availability": e["availability"],
        }
        for e in master_entries
    }

    # ------------------------------------------------------------------
    # Write output files via ctx
    # ------------------------------------------------------------------
    if ctx:
        _write_json_outputs(ctx, config, all_urls, url_data_map, url_gender_map,
                            n_men, n_women, n_unisex, n_unknown, n_with_code)

    logger.info("Total unique product URLs collected: %d", len(all_urls))

    # Record total found BEFORE limit so run_summary can show real count
    if ctx:
        ctx._urls_collected_total = len(all_urls)
        ctx._urls_after_limit = min(limit, len(all_urls)) if limit else len(all_urls)

    # Apply limit AFTER all collection and cross-referencing
    if limit:
        all_urls = all_urls[:limit]
        logger.info("Limiting to %d URLs for testing.", limit)

    return all_urls, url_data_map


# ---------------------------------------------------------------------------
# JSON output helpers
# ---------------------------------------------------------------------------

def _write_json_outputs(
    ctx,
    config: dict,
    all_urls: List[str],
    url_data_map: Dict[str, Dict],
    url_gender_map: Dict[str, str],
    n_men: int,
    n_women: int,
    n_unisex: int,
    n_unknown: int,
    n_with_code: int,
) -> None:
    """Write url_collection.json and gender_map.json to the run folder."""
    brand = config.get("brand_name", "")

    # url_collection.json
    url_collection = {
        "meta": {
            "brand":          brand,
            "timestamp":      ctx.timestamp,
            "total_urls":     len(all_urls),
            "with_sku":       n_with_code,
            "men":            n_men,
            "women":          n_women,
            "unisex":         n_unisex,
            "unknown_gender": n_unknown,
        },
        "urls": [
            {
                "url":          url_data_map[u]["url"],
                "sku":          url_data_map[u]["sku"],
                "gender":       url_data_map[u]["gender"],
                "availability": url_data_map[u]["availability"],
            }
            for u in all_urls
        ],
    }
    ctx.path("url_collection.json").write_text(
        json.dumps(url_collection, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # gender_map.json
    ctx.path("gender_map.json").write_text(
        json.dumps(url_gender_map, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    logger.debug("Wrote url_collection.json and gender_map.json to run folder.")


# ---------------------------------------------------------------------------
# Pass 1 / Pass 2 scraping — single entry point per collection URL
# ---------------------------------------------------------------------------

def _scrape_collection(
    driver,
    url: str,
    pagination_config: dict,
    code_extraction: dict,
    listing_avail_cfg: dict,
    anti_bot: dict,
    product_link_selector: str,
    base_url: str,
) -> List[Dict]:
    """
    Load a collection URL with the configured pagination strategy.
    Extracts product URL, product code, and listing availability simultaneously.

    Returns List[Dict] of {"url": ..., "code": ..., "availability": ...}.
    """
    pag_type       = pagination_config.get("type", "none")
    first_load_wait = anti_bot.get("first_load_wait", anti_bot.get("page_delay", 3))

    driver.get(url)
    time.sleep(first_load_wait)

    kwargs = dict(
        pagination=pagination_config,
        selector=product_link_selector,
        base_url=base_url,
        anti_bot=anti_bot,
        code_extraction=code_extraction,
        listing_avail_cfg=listing_avail_cfg,
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
            driver, product_link_selector, base_url,
            code_extraction, listing_avail_cfg,
        )
        n_codes = sum(1 for p in pairs if p["code"])
        logger.debug("  Page 1: found %d links, %d with codes", len(pairs), n_codes)
        return pairs


# ---------------------------------------------------------------------------
# Pagination strategies — each returns List[Dict] of {url, code, availability}
# ---------------------------------------------------------------------------

def _paginate_next_button(
    driver, pagination, selector, base_url, anti_bot,
    code_extraction, listing_avail_cfg,
) -> List[Dict]:
    next_selector = pagination.get("next_button_selector", "")
    max_pages     = pagination.get("max_pages", 100)
    pairs: List[Dict] = []

    for page_num in range(1, max_pages + 1):
        page_pairs = _extract_links_with_codes(
            driver, selector, base_url, code_extraction, listing_avail_cfg
        )
        n_codes = sum(1 for p in page_pairs if p["code"])
        logger.debug("  Page %d: found %d links, %d with codes",
                     page_num, len(page_pairs), n_codes)
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
    driver, pagination, selector, base_url, anti_bot,
    code_extraction, listing_avail_cfg,
) -> List[Dict]:
    load_more_selector = pagination.get("load_more_selector", "")
    max_clicks         = pagination.get("max_clicks", 50)

    for click_num in range(max_clicks):
        # Scroll slowly toward the bottom in 300 px steps, checking for the
        # button after each step — click it the moment it becomes visible.
        btn = None
        while True:
            try:
                candidate = driver.find_element(By.CSS_SELECTOR, load_more_selector)
                if candidate.is_displayed() and candidate.is_enabled():
                    btn = candidate
                    break
            except NoSuchElementException:
                pass

            current_bottom = driver.execute_script(
                "return window.pageYOffset + window.innerHeight"
            )
            page_height = driver.execute_script("return document.body.scrollHeight")
            if current_bottom >= page_height:
                break  # reached the bottom — button is genuinely gone

            driver.execute_script("window.scrollBy(0, 300);")
            time.sleep(0.3)

        if btn is None:
            logger.debug("  Load more button not found after full scroll — all products loaded.")
            break

        driver.execute_script("arguments[0].click();", btn)
        logger.debug("  Load more click #%d", click_num + 1)
        time.sleep(_random_delay(anti_bot))

    pairs = _extract_links_with_codes(
        driver, selector, base_url, code_extraction, listing_avail_cfg
    )
    n_codes = sum(1 for p in pairs if p["code"])
    logger.debug("  Found %d links, %d with codes (after load-more)", len(pairs), n_codes)
    return pairs


def _paginate_infinite_scroll(
    driver, pagination, selector, base_url, anti_bot,
    code_extraction, listing_avail_cfg,
) -> List[Dict]:
    max_scrolls  = pagination.get("max_scrolls", 60)
    scroll_pause = pagination.get("scroll_pause", anti_bot.get("scroll_pause", 3.0))
    slow_scroll  = anti_bot.get("slow_scroll", False)

    last_height = driver.execute_script("return document.body.scrollHeight")

    for scroll_num in range(max_scrolls):
        if slow_scroll:
            current   = driver.execute_script("return window.pageYOffset")
            remaining = last_height - current
            step      = max(300, remaining // 5)
            for _ in range(5):
                driver.execute_script(f"window.scrollBy(0, {step});")
                time.sleep(0.3)
        else:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

        time.sleep(scroll_pause)
        new_height = driver.execute_script("return document.body.scrollHeight")
        logger.debug("  Scroll #%d: height %d -> %d", scroll_num + 1, last_height, new_height)

        if new_height == last_height:
            logger.debug("  Page height unchanged — end of scroll.")
            break
        last_height = new_height

    pairs = _extract_links_with_codes(
        driver, selector, base_url, code_extraction, listing_avail_cfg
    )
    n_codes = sum(1 for p in pairs if p["code"])
    logger.debug("  Found %d links, %d with codes (after scroll)", len(pairs), n_codes)
    return pairs


def _paginate_url_param(
    driver, base_cat_url: str, pagination: dict,
    selector: str, base_url: str, anti_bot: dict,
    code_extraction: dict, listing_avail_cfg: dict,
) -> List[Dict]:
    """
    Append ?page=1, ?page=2 … to the category URL.
    Stop when a page yields no new links.
    """
    param_template = pagination.get("param_template", "?page={}")
    max_pages      = pagination.get("max_pages", 20)
    base_cat_url   = base_cat_url.split("?")[0]   # strip any existing query string

    all_pairs: List[Dict] = []
    seen_norms: set = set()

    for page_num in range(1, max_pages + 1):
        param    = param_template.replace("{}", str(page_num))
        page_url = (
            base_cat_url + "&" + param.lstrip("?&")
            if "?" in base_cat_url
            else base_cat_url + param
        )

        logger.debug("  url_param page %d: %s", page_num, page_url)
        driver.get(page_url)
        time.sleep(_random_delay(anti_bot))

        pairs     = _extract_links_with_codes(
            driver, selector, base_url, code_extraction, listing_avail_cfg
        )
        new_pairs = [p for p in pairs if _normalize_url(p["url"]) not in seen_norms]

        if not new_pairs:
            logger.debug("  No new links on page %d — end of pagination.", page_num)
            break

        n_codes = sum(1 for p in new_pairs if p["code"])
        logger.debug("  Page %d: found %d links, %d with codes",
                     page_num, len(new_pairs), n_codes)

        for p in new_pairs:
            seen_norms.add(_normalize_url(p["url"]))
        all_pairs.extend(new_pairs)

    return all_pairs


# ---------------------------------------------------------------------------
# Link + code + availability extraction — one DOM pass per page
# ---------------------------------------------------------------------------

def _extract_links_with_codes(
    driver,
    css_selector: str,
    base_url: str,
    code_extraction: dict,
    listing_avail_cfg: dict,
) -> List[Dict]:
    """
    Find all product link elements matching css_selector.
    For each element, extract href, code, and listing availability in one pass.

    Returns List[Dict] of {"url": ..., "code": ..., "availability": ...}.
    """
    if not css_selector:
        logger.warning("  No product_link selector configured.")
        return []

    try:
        elements = driver.find_elements(By.CSS_SELECTOR, css_selector)
    except Exception as e:
        logger.warning("  Failed to find elements with selector '%s': %s",
                       css_selector, e)
        return []

    pairs: List[Dict] = []
    for el in elements:
        try:
            href = el.get_attribute("href") or ""
            if not href:
                continue
            full_url     = urljoin(base_url, href)
            code         = _extract_code_from_element(driver, el, href, code_extraction)
            availability = _extract_availability(driver, el, listing_avail_cfg)
            pairs.append({"url": full_url, "code": code, "availability": availability})
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
      Supported pattern: "last_segment_after_final_hyphen"
    """
    if not code_extraction:
        return ""

    source           = code_extraction.get("source", "")
    normalize_method = code_extraction.get("normalize", "")

    if source == "attribute":
        attribute = code_extraction.get("attribute", "")
        if not attribute:
            return ""

        current = el
        for _ in range(5):   # <a> itself + up to 4 ancestors
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
            parts   = segment.split("-")
            code    = parts[-1] if len(parts) > 1 else segment
            return _normalize_code(code, normalize_method)
        return ""

    return ""


def _extract_availability(driver, el, listing_avail_cfg: dict) -> str:
    """
    Infer listing availability from the product card containing the link element.

    Walks up from the link element through up to 5 ancestors, trying
    find_elements(selector) at each level.  If the selector is found,
    returns present_means ("sold_out" or "available").  If never found,
    returns "".

    This is best-effort — no filtering is done on the result.
    """
    if not listing_avail_cfg:
        return ""
    selector    = listing_avail_cfg.get("selector", "")
    present_means = listing_avail_cfg.get("present_means", "sold_out")
    if not selector:
        return ""

    current = el
    for _ in range(6):   # el itself + up to 5 ancestors
        try:
            found = current.find_elements(By.CSS_SELECTOR, selector)
            if found:
                return present_means
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
