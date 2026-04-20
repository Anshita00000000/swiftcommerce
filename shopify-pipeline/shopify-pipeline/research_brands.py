"""
Brand research script — visits each brand's listing page with Selenium
and inspects the HTML to answer all required questions.
Saves output to: config/brand_research.md
"""

import time, re, json, os, sys
from urllib.parse import urlparse, urljoin
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "config", "brand_research.md")

# ── driver helpers ────────────────────────────────────────────────────────────

def make_driver():
    opts = Options()
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=opts)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"}
    )
    return driver


def load(driver, url, wait=12):
    try:
        driver.get(url)
        time.sleep(wait)
        return True
    except WebDriverException as e:
        return False


def scroll_down(driver, pauses=3, pause_time=2.5):
    last = 0
    for _ in range(pauses):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause_time)
        h = driver.execute_script("return document.body.scrollHeight")
        if h == last:
            break
        last = h


def q(driver, selector):
    """Return list of elements for a CSS selector (empty list if none)."""
    try:
        return driver.find_elements(By.CSS_SELECTOR, selector)
    except Exception:
        return []


def first_text(driver, selector):
    els = q(driver, selector)
    return els[0].text.strip() if els else ""


def first_attr(driver, selector, attr):
    els = q(driver, selector)
    return els[0].get_attribute(attr) if els else ""


def page_source_contains(driver, text):
    return text.lower() in driver.page_source.lower()


def count_links(driver, selector):
    return len(q(driver, selector))


def try_selectors(driver, selectors):
    for sel in selectors:
        els = q(driver, sel)
        if els:
            return sel, els
    return None, []


def detect_shopify(driver):
    src = driver.page_source
    return (
        "Shopify.theme" in src
        or "cdn.shopify.com" in src
        or "/collections/" in driver.current_url
        or "shopify" in src.lower()
    )


def sample_hrefs(driver, selector, n=5):
    els = q(driver, selector)
    hrefs = []
    for el in els[:n]:
        try:
            h = el.get_attribute("href") or ""
            if h:
                hrefs.append(h)
        except Exception:
            pass
    return hrefs


def detect_pagination(driver):
    """Return (type, detail) for pagination."""
    # Infinite scroll / load-more button
    if q(driver, "button.load-more, [class*='load-more'], [data-load-more]"):
        return "next_button", "load-more button present"
    # URL param ?page=N
    if q(driver, "a[href*='?page='], a[href*='page=']"):
        pages = []
        for a in q(driver, "a[href*='page=']")[:20]:
            h = a.get_attribute("href") or ""
            m = re.search(r'page=(\d+)', h)
            if m:
                pages.append(int(m.group(1)))
        mx = max(pages) if pages else "?"
        return "url_param", f"?page=N, max page link seen: {mx}"
    # Next button (rel=next or text "next")
    if q(driver, "a[rel='next'], [aria-label='Next'], .pagination__next, a.next"):
        return "next_button", "rel=next / .pagination__next"
    # Infinite scroll indicators
    if q(driver, "[data-infinite-scroll], [data-infinite], .infinite-scroll"):
        return "scroll", "infinite scroll detected"
    return "unknown", "no pagination indicators found"


def find_sku(driver, link_selector):
    """Try to find SKU data attribute on product cards."""
    sku_attrs = ["data-sku", "data-product-id", "data-product-code",
                 "data-variant-id", "data-id", "data-handle",
                 "data-item-number", "data-reference"]
    for attr in sku_attrs:
        els = q(driver, f"[{attr}]")
        if els:
            val = els[0].get_attribute(attr)
            if val:
                return "data_attribute", attr, val
    # Try product card container
    card_selectors = [
        "li.product-item", ".product-card", ".product-tile",
        ".collection-product-card", "[data-product]",
        "article.product", ".product__item"
    ]
    for sel in card_selectors:
        for attr in sku_attrs:
            els = q(driver, f"{sel}[{attr}]")
            if els:
                val = els[0].get_attribute(attr)
                if val:
                    return "data_attribute", f"{sel}[{attr}]", val
    return "not_found", None, None


def check_availability_listing(driver):
    """Check if availability status is shown on listing page cards."""
    avail_selectors = {
        "button[name='add']": "Add to Cart button",
        "[class*='add-to-cart']": "add-to-cart class",
        "[class*='sold-out']": "sold-out badge",
        "[class*='out-of-stock']": "out-of-stock class",
        "[class*='notify']": "notify badge",
        ".badge--sold-out": "Shopify sold-out badge",
        ".product-badge": "product badge",
        "[data-availability]": "data-availability attribute",
        ".availability": "availability class",
        "[class*='unavailable']": "unavailable class",
        ".product-item__status": "product-item status",
    }
    found = []
    for sel, desc in avail_selectors.items():
        els = q(driver, sel)
        if els:
            texts = list(set(e.text.strip() for e in els[:5] if e.text.strip()))
            found.append((sel, desc, texts))
    return found


def check_availability_product(driver, url):
    """Load a product page and check availability selectors."""
    if not load(driver, url, wait=10):
        return "failed_to_load", []
    scroll_down(driver, pauses=2)

    avail_selectors = [
        "button[name='add']",
        "button[type='submit'][class*='add']",
        ".product-form__submit",
        "[class*='sold-out']",
        "[class*='notify-me']",
        "[class*='out-of-stock']",
        ".availability",
        "[data-add-to-cart]",
        ".btn--add-to-cart",
        ".product__submit",
        "form[action*='/cart/add'] button[type='submit']",
        ".product-purchase__submit",
        "[class*='AddToCart']",
    ]
    results = []
    for sel in avail_selectors:
        els = q(driver, sel)
        if els:
            for el in els[:2]:
                t = el.text.strip()
                if t:
                    results.append((sel, t))
    return "ok", results


# ── per-brand research functions ─────────────────────────────────────────────

def research_brand(driver, brand_config):
    name = brand_config["name"]
    print(f"\n{'='*60}")
    print(f"  RESEARCHING: {name}")
    print(f"{'='*60}")

    result = {
        "name": name,
        "platform": "Unknown",
        "listing_url_confirmed": "not_found",
        "blocks_selenium": "no",
        "product_link_selector": "not_found",
        "product_url_pattern": "unknown",
        "pagination_type": "unknown",
        "pagination_detail": "",
        "approximate_total_products": "unknown",
        "sku_location": "not_found",
        "sku_attribute_name": "not_found",
        "sku_example": "not_found",
        "men_url": brand_config.get("men_url", "not_found"),
        "women_url": brand_config.get("women_url", "not_found"),
        "status_on_listing_page": "no",
        "listing_status_selector": "not_applicable",
        "listing_status_values_seen": [],
        "status_on_product_page": "no",
        "product_status_selector": "not_found",
        "product_status_values_seen": [],
        "status_notes": "",
    }

    # ── Load primary listing URL ──────────────────────────────────────────
    urls_to_try = brand_config["listing_urls"]
    loaded_url = None
    for url in urls_to_try:
        print(f"  Trying: {url}")
        ok = load(driver, url, wait=14)
        if not ok:
            print("    -> WebDriverException")
            continue
        scroll_down(driver, pauses=2, pause_time=2)
        # Check if we got a useful page (not a 403/block)
        title = driver.title
        src_len = len(driver.page_source)
        print(f"    -> title='{title[:60]}', source={src_len} bytes")
        if src_len < 2000:
            result["blocks_selenium"] = "yes"
            continue
        if any(x in title.lower() for x in ["access denied", "403", "robot", "captcha", "blocked"]):
            result["blocks_selenium"] = "yes"
            continue
        loaded_url = url
        break

    if not loaded_url:
        result["blocks_selenium"] = "yes"
        result["status_notes"] = "All listing URLs failed to load or were blocked"
        return result

    result["listing_url_confirmed"] = driver.current_url
    result["platform"] = "Shopify" if detect_shopify(driver) else "Custom"

    # ── Find product card link selector ──────────────────────────────────
    link_selectors_to_try = brand_config.get("link_selectors", [
        "a[href*='/products/']",
        ".product-card a",
        ".product-item a",
        ".product-tile a",
        "li.product-item a",
        "a.product-card__link",
        "[data-product-url]",
        ".collection-grid a[href]",
        "a[href*='/p/']",
        "a[href*='.html']",
        "a[href*='/en-us/']",
        "a[href*='/watches/']",
    ])

    best_selector = None
    best_count = 0
    for sel in link_selectors_to_try:
        n = count_links(driver, sel)
        print(f"    Selector '{sel}': {n} links")
        if n > best_count:
            best_count = n
            best_selector = sel

    if best_selector and best_count > 0:
        result["product_link_selector"] = best_selector
        # Sample URLs to derive pattern
        hrefs = sample_hrefs(driver, best_selector, n=3)
        print(f"    Sample hrefs: {hrefs}")
        if hrefs:
            parsed = urlparse(hrefs[0])
            path = parsed.path
            # Derive pattern from path
            segs = [s for s in path.split("/") if s]
            if segs:
                slug = segs[-1]
                pattern = "/" + "/".join(segs[:-1]) + "/{slug}"
                if slug.endswith(".html"):
                    pattern = "/" + "/".join(segs[:-1]) + "/{slug}.html"
                result["product_url_pattern"] = pattern
        result["approximate_total_products"] = best_count

    # ── Pagination ────────────────────────────────────────────────────────
    ptype, pdetail = detect_pagination(driver)
    result["pagination_type"] = ptype
    result["pagination_detail"] = pdetail

    # ── SKU detection ─────────────────────────────────────────────────────
    sku_loc, sku_attr, sku_ex = find_sku(driver, best_selector or "a")
    result["sku_location"] = sku_loc
    result["sku_attribute_name"] = sku_attr or "not_found"
    result["sku_example"] = sku_ex or "not_found"

    # ── Availability on listing page ──────────────────────────────────────
    avail = check_availability_listing(driver)
    if avail:
        result["status_on_listing_page"] = "yes"
        # Pick the most informative selector
        best_avail = max(avail, key=lambda x: len(x[2]))
        result["listing_status_selector"] = best_avail[0]
        result["listing_status_values_seen"] = best_avail[2]

    # ── Availability on one product page ─────────────────────────────────
    if best_selector and best_count > 0:
        sample_links = sample_hrefs(driver, best_selector, n=5)
        if sample_links:
            prod_url = sample_links[0]
            if not prod_url.startswith("http"):
                base = "{scheme}://{netloc}".format(
                    scheme=urlparse(loaded_url).scheme,
                    netloc=urlparse(loaded_url).netloc
                )
                prod_url = base + prod_url
            print(f"  Checking product page: {prod_url}")
            status_ok, prod_avail = check_availability_product(driver, prod_url)
            if prod_avail:
                result["status_on_product_page"] = "yes"
                result["product_status_selector"] = prod_avail[0][0]
                result["product_status_values_seen"] = list(set(x[1] for x in prod_avail))

    return result


# ── format output ─────────────────────────────────────────────────────────────

def format_result(r):
    lines = [
        f"=== {r['name']} ===",
        f"platform: {r['platform']}",
        f"listing_url_confirmed: {r['listing_url_confirmed']}",
        f"blocks_selenium: {r['blocks_selenium']}",
        "",
        "-- URL COLLECTION --",
        f"product_link_selector: {r['product_link_selector']}",
        f"product_url_pattern: {r['product_url_pattern']}",
        f"pagination_type: {r['pagination_type']}",
        f"pagination_detail: {r['pagination_detail']}",
        f"approximate_total_products: {r['approximate_total_products']}",
        f"sku_location: {r['sku_location']}",
        f"sku_attribute_name: {r['sku_attribute_name']}",
        f"sku_example: {r['sku_example']}",
        f"men_url: {r['men_url']}",
        f"women_url: {r['women_url']}",
        "",
        "-- AVAILABILITY STATUS --",
        f"status_on_listing_page: {r['status_on_listing_page']}",
        f"listing_status_selector: {r['listing_status_selector']}",
        f"listing_status_values_seen: {r['listing_status_values_seen']}",
        f"status_on_product_page: {r['status_on_product_page']}",
        f"product_status_selector: {r['product_status_selector']}",
        f"product_status_values_seen: {r['product_status_values_seen']}",
        f"status_notes: {r['status_notes']}",
        "",
    ]
    return "\n".join(lines)


# ── brand configs ─────────────────────────────────────────────────────────────

BRANDS = [
    {
        "name": "Luminox",
        "listing_urls": ["https://luminox.com/collections/all-watches"],
        "link_selectors": [
            "a[href*='/products/']",
            ".product-card a",
            ".product-card__link",
            "li.grid__item a[href*='/products/']",
            ".card__heading a",
        ],
        "men_url": "https://luminox.com/collections/mens-watches",
        "women_url": "https://luminox.com/collections/womens-watches",
    },
    {
        "name": "G-Shock",
        "listing_urls": [
            "https://www.casio.com/us/watches/gshock/",
            "https://www.casio.com/us/watches/gshock.filter.K84vKnGqtE1KLS5R0youyU@ODi5JLCkttgUA/",
        ],
        "link_selectors": [
            "a[href*='/gshock/']",
            ".product-tile a",
            ".product-card a",
            ".item-card a",
            "[class*='product'] a[href*='/watches/']",
            "a.product-tile__link",
            ".search-result-items a",
        ],
        "men_url": "https://www.casio.com/us/watches/gshock/",
        "women_url": "not_found",
    },
    {
        "name": "Hamilton",
        "listing_urls": [
            "https://www.hamiltonwatch.com/en-us/filter-by.html",
            "https://www.hamiltonwatch.com/en-us/",
        ],
        "link_selectors": [
            "a[href*='/en-us/']",
            ".product-card a",
            ".card-product a",
            "[class*='product-tile'] a",
            ".product-list-item a",
            "a[href*='.html']",
        ],
        "men_url": "https://www.hamiltonwatch.com/en-us/filter-by/mens-watches.html",
        "women_url": "https://www.hamiltonwatch.com/en-us/filter-by/womens-watches.html",
    },
    {
        "name": "Tissot",
        "listing_urls": [
            "https://www.tissotwatches.com/en-us/collection.html",
        ],
        "link_selectors": [
            "a[href*='/en-us/']",
            ".product-card a",
            ".item-card a",
            "[class*='product'] a",
            "a[href*='.html']",
        ],
        "men_url": "https://www.tissotwatches.com/en-us/men.html",
        "women_url": "https://www.tissotwatches.com/en-us/women.html",
    },
    {
        "name": "Rado",
        "listing_urls": [
            "https://www.rado.com/en_us/watches/all-watches/",
        ],
        "link_selectors": [
            "a[href*='/watches/']",
            ".product-card a",
            ".card-product a",
            "[class*='product-tile'] a",
            "a[href*='/en_us/']",
        ],
        "men_url": "https://www.rado.com/en_us/watches/all-watches/men-watches.html",
        "women_url": "https://www.rado.com/en_us/watches/all-watches/women-watches.html",
    },
    {
        "name": "Victorinox",
        "listing_urls": [
            "https://www.victorinox.com/en-US/Products/Watches/c/TP_AllProducts/",
        ],
        "link_selectors": [
            "a[href*='/Products/']",
            ".product-tile a",
            ".product-card a",
            "[class*='product'] a",
            "a[href*='/Watches/']",
        ],
        "men_url": "https://www.victorinox.com/en-US/Products/Watches/Men%27s-Watches/c/TP-mens-watches/",
        "women_url": "https://www.victorinox.com/en-US/Products/Watches/Women's-Watches/c/TP_WomenWatches/",
    },
    {
        "name": "Movado",
        "listing_urls": [
            "https://www.movado.com/us/en/shop-watches/shop-all-watches",
        ],
        "link_selectors": [
            "a[href*='/us/en/']",
            ".product-tile a",
            ".product-card a",
            "[class*='product-tile'] a",
            "a[href*='/watches']",
        ],
        "men_url": "https://www.movado.com/us/en/mens-designs",
        "women_url": "https://www.movado.com/us/en/womens-designs",
    },
    {
        "name": "Michele",
        "listing_urls": [
            "https://www.michele.com/en-us/watches/",
        ],
        "link_selectors": [
            "a[href*='/products/']",
            "a[href*='/watches/']",
            ".product-card a",
            ".product-tile a",
            "li.product a",
        ],
        "men_url": "not_applicable",
        "women_url": "not_applicable",
    },
    {
        "name": "Seiko",
        "listing_urls": [
            "https://seikousa.com/collections/all",
        ],
        "link_selectors": [
            "#product-grid a[href*='/products/']",
            "a[href*='/products/']",
            ".product-card a",
            ".grid__item a[href*='/products/']",
        ],
        "men_url": "https://seikousa.com/collections/mens",
        "women_url": "https://seikousa.com/collections/womens",
    },
]


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    driver = make_driver()
    all_results = []

    try:
        for brand_cfg in BRANDS:
            try:
                result = research_brand(driver, brand_cfg)
                all_results.append(result)
                print(f"\n  RESULT for {brand_cfg['name']}:")
                print(format_result(result))
            except Exception as e:
                print(f"  ERROR researching {brand_cfg['name']}: {e}")
                all_results.append({
                    "name": brand_cfg["name"],
                    "error": str(e),
                    "men_url": brand_cfg.get("men_url", "not_found"),
                    "women_url": brand_cfg.get("women_url", "not_found"),
                })
    finally:
        driver.quit()

    # ── Write markdown output ─────────────────────────────────────────────
    lines = [
        "# Brand Research Report",
        "Generated: 2026-03-09",
        "Method: Selenium (Chrome, headful, undetected mode)",
        "",
        "---",
        "",
    ]
    for r in all_results:
        if "error" in r:
            lines.append(f"=== {r['name']} ===")
            lines.append(f"ERROR: {r['error']}")
            lines.append("")
        else:
            lines.append(format_result(r))
        lines.append("---")
        lines.append("")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n\nSaved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
