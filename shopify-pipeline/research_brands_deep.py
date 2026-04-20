"""
Deep-dive research for brands that need more investigation:
- G-Shock (try alternate approaches)
- Hamilton (find true product card selector)
- Tissot (find true product card selector + check individual product page)
- Rado (find true product card selector + check product page)
- Victorinox (find true product card selector + check product page)
- Movado (find true product card selector + check product page)
- Michele (Cloudflare - wait longer)
"""

import time, re, json, os
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException

RESULTS_PATH = os.path.join(os.path.dirname(__file__), "config", "brand_research_deep.txt")


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


def q(driver, sel):
    try:
        return driver.find_elements(By.CSS_SELECTOR, sel)
    except Exception:
        return []


def load(driver, url, wait=15):
    try:
        driver.get(url)
        time.sleep(wait)
        return True
    except WebDriverException:
        return False


def scroll(driver, n=4, pause=3):
    for _ in range(n):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)


def attrs_on(driver, selector, *attrs):
    """Print all data-* attributes on first 3 matching elements."""
    els = q(driver, selector)
    out = []
    for el in els[:3]:
        row = {}
        for a in attrs:
            row[a] = el.get_attribute(a) or ""
        # Also dump all data-* via JS
        try:
            data_attrs = driver.execute_script(
                "var m={}; var ds=arguments[0].dataset; for(var k in ds){m[k]=ds[k];} return m;",
                el
            )
            row["data_attrs"] = data_attrs
        except Exception:
            row["data_attrs"] = {}
        out.append(row)
    return out


def count_unique_hrefs(driver, selector):
    els = q(driver, selector)
    hrefs = set()
    for el in els:
        try:
            h = el.get_attribute("href") or ""
            if h:
                hrefs.add(h)
        except Exception:
            pass
    return len(hrefs), list(hrefs)[:5]


def inspect_page(driver, label, candidate_selectors):
    """Try a list of candidate selectors and report counts + sample hrefs + data-attrs."""
    print(f"\n  -- {label} --")
    print(f"  URL: {driver.current_url}")
    print(f"  Title: {driver.title}")
    print(f"  Source: {len(driver.page_source)} bytes")

    # Dump all unique href patterns
    all_links = q(driver, "a[href]")
    href_patterns = {}
    for a in all_links:
        try:
            h = a.get_attribute("href") or ""
            p = urlparse(h)
            # Get first 3 path segments
            segs = [s for s in p.path.split("/") if s]
            pat = "/" + "/".join(segs[:3]) if segs else "/"
            href_patterns[pat] = href_patterns.get(pat, 0) + 1
        except Exception:
            pass
    sorted_pats = sorted(href_patterns.items(), key=lambda x: -x[1])
    print(f"  Top href path patterns:")
    for pat, cnt in sorted_pats[:15]:
        print(f"    {cnt:4d}x  {pat}")

    print(f"\n  Candidate selectors:")
    for sel in candidate_selectors:
        n, hrefs = count_unique_hrefs(driver, sel)
        total = len(q(driver, sel))
        print(f"    [{total:3d} total / {n:3d} unique] {sel}")
        if n > 0:
            for h in hrefs[:3]:
                print(f"              -> {h}")
        # Check data-attrs on first element
        if total > 0:
            data = attrs_on(driver, sel, "href", "data-sku", "data-product-id",
                           "data-product-code", "data-item-number", "data-reference",
                           "data-pid", "data-handle", "data-variant-id")
            for d in data[:1]:
                da = d.get("data_attrs", {})
                if da:
                    print(f"              data-* attrs: {da}")


def check_product_page(driver, url, label):
    print(f"\n  -- Product Page: {label} --")
    print(f"  Loading: {url}")
    load(driver, url, wait=12)
    scroll(driver, n=2, pause=2)
    print(f"  Title: {driver.title}")
    print(f"  URL: {driver.current_url}")
    print(f"  Source: {len(driver.page_source)} bytes")

    # Check add-to-cart / availability selectors
    avail_selectors = [
        "button[name='add']",
        "button[type='submit']",
        "form[action*='cart'] button",
        "[class*='add-to-cart']",
        "[class*='AddToCart']",
        "[class*='addtocart']",
        "[class*='sold-out']",
        "[class*='out-of-stock']",
        "[class*='notify']",
        ".product-form__submit",
        ".btn-add-to-cart",
        ".add-to-bag",
        "[data-action='add-to-cart']",
        "[data-add-to-cart]",
        ".product__submit button",
        ".product-info__cart-submit",
        ".product-purchase__submit",
        ".js-add-to-cart",
        "#add-to-cart",
        ".btn--add-to-cart",
    ]
    print(f"\n  Availability selectors found:")
    for sel in avail_selectors:
        els = q(driver, sel)
        if els:
            texts = []
            for el in els[:3]:
                t = el.text.strip()
                if t:
                    texts.append(repr(t))
            attr_disabled = [el.get_attribute("disabled") for el in els[:2]]
            print(f"    {sel}: {len(els)} els, texts={texts}, disabled={attr_disabled}")

    # SKU on product page
    sku_selectors = [
        ".product-sku", ".sku", "[class*='sku']", "[class*='reference']",
        "[class*='model-number']", ".product-number", ".pdp-sku",
        "[itemprop='sku']", "[class*='item-number']",
    ]
    print(f"\n  SKU selectors found:")
    for sel in sku_selectors:
        els = q(driver, sel)
        if els:
            for el in els[:2]:
                print(f"    {sel}: '{el.text.strip()}'")


output_lines = []


def log(msg=""):
    print(msg)
    output_lines.append(msg)


def research_hamilton(driver):
    log("\n" + "="*60)
    log("DEEP DIVE: Hamilton")
    log("="*60)
    url = "https://www.hamiltonwatch.com/en-us/filter-by.html"
    load(driver, url, wait=16)
    scroll(driver, n=3, pause=3)

    candidates = [
        "a.product-list-item__link",
        ".product-list-item a",
        ".product-grid-item a",
        ".product-tile a[href*='.html']",
        "article a[href*='/en-us/']",
        ".js-product-tile a",
        "[class*='product-item'] a",
        "[class*='product-card'] a",
        "li a[href*='/en-us/']",
        "a[href*='/en-us/'][href*='.html']:not([href*='filter-by'])",
        # Magento-style
        ".products.list.items a.product-item-link",
        ".product-item-info a",
        # Try data attributes on cards
        "[data-sku] a",
        "[data-product-id] a",
    ]

    log(f"  URL: {url}")
    log(f"  Title: {driver.title}")
    log(f"  Source: {len(driver.page_source)} bytes")

    all_links = q(driver, "a[href]")
    href_pats = {}
    for a in all_links:
        try:
            h = a.get_attribute("href") or ""
            p = urlparse(h)
            segs = [s for s in p.path.split("/") if s]
            pat = "/" + "/".join(segs[:4]) if segs else "/"
            href_pats[pat] = href_pats.get(pat, 0) + 1
        except Exception:
            pass
    log("  Top href patterns:")
    for pat, cnt in sorted(href_pats.items(), key=lambda x: -x[1])[:20]:
        log(f"    {cnt:4d}x  {pat}")

    log("  Candidate selectors:")
    for sel in candidates:
        n, hrefs = count_unique_hrefs(driver, sel)
        total = len(q(driver, sel))
        log(f"    [{total:3d}/{n}u] {sel}")
        for h in hrefs[:2]:
            log(f"            {h}")
        # data attrs
        if total > 0:
            da = attrs_on(driver, sel, "href")
            for d in da[:1]:
                if d.get("data_attrs"):
                    log(f"            data-*: {d['data_attrs']}")

    # Try JS to find product data
    try:
        product_data = driver.execute_script("""
            var cards = document.querySelectorAll('[data-sku],[data-product-id],[data-pid],[data-item-number]');
            var results = [];
            cards.forEach(function(c) {
                var ds = {};
                for(var k in c.dataset) ds[k] = c.dataset[k];
                results.push({tag: c.tagName, cls: c.className.substr(0,60), data: ds});
            });
            return results.slice(0,10);
        """)
        log(f"\n  JS data-attr cards found: {len(product_data)}")
        for r in product_data[:5]:
            log(f"    {r}")
    except Exception as e:
        log(f"  JS query error: {e}")

    # Check page for specific product URLs pattern
    src = driver.page_source
    # Look for product URLs in source
    prod_urls = re.findall(r'href="(/en-us/[^"]+\.html)"', src)
    unique_prods = list(set(prod_urls))
    log(f"\n  Product URLs from source (first 10): {unique_prods[:10]}")
    log(f"  Total unique .html hrefs: {len(unique_prods)}")

    # Filter to actual product pages (not filter-by, not mens-watches, not womens-watches etc.)
    actual_products = [u for u in unique_prods if
                      '/en-us/' in u and u.endswith('.html') and
                      'filter-by' not in u and 'mens-watches' not in u and
                      'womens-watches' not in u and 'collections' not in u and
                      len(u.split('/')) >= 4]
    log(f"  Likely product page URLs: {actual_products[:5]}")

    # Check one product page
    if actual_products:
        prod_url = "https://www.hamiltonwatch.com" + actual_products[0]
        check_product_page(driver, prod_url, "Hamilton product page")

    # Check men's page
    log("\n  Checking men's page...")
    load(driver, "https://www.hamiltonwatch.com/en-us/filter-by/mens-watches.html", wait=12)
    scroll(driver, n=2, pause=2)
    for sel in ["a[href*='/en-us/'][href$='.html']", "[data-sku]", "[data-product-id]"]:
        n, hrefs = count_unique_hrefs(driver, sel)
        log(f"    [{n}] {sel}: {hrefs[:2]}")


def research_tissot(driver):
    log("\n" + "="*60)
    log("DEEP DIVE: Tissot")
    log("="*60)

    # First check the collection page structure
    url = "https://www.tissotwatches.com/en-us/tissot-t-touch-expert-solar-ii.html"
    log(f"\n  Checking known product URL: {url}")
    load(driver, url, wait=12)
    log(f"  Title: {driver.title}")
    log(f"  URL: {driver.current_url}")
    src = driver.page_source
    # SKU in source
    sku_match = re.search(r'T\d{3}\.\d{3}\.\d{2}\.\d{3}\.\d{2}', src)
    if sku_match:
        log(f"  SKU found in source: {sku_match.group()}")

    check_product_page(driver, url, "Tissot product page")

    # Back to listing
    log("\n  Loading Tissot collection listing...")
    load(driver, "https://www.tissotwatches.com/en-us/collection.html", wait=15)
    scroll(driver, n=3, pause=3)
    log(f"  Source: {len(driver.page_source)} bytes")

    # Find actual product cards
    candidates = [
        ".product-item a",
        ".product-tile a",
        "[class*='product-item'] a[href*='.html']",
        ".item-box a",
        ".catalog-item a",
        "a[href*='/en-us/tissot-']",
        ".product-list a[href*='.html']",
        ".product a.product-link",
        ".list-item a",
    ]
    log("  Candidate selectors for product cards:")
    for sel in candidates:
        n, hrefs = count_unique_hrefs(driver, sel)
        log(f"    [{n}u] {sel}: {hrefs[:2]}")

    # Extract product URLs from source
    src = driver.page_source
    prod_matches = re.findall(r'"(/en-us/tissot-[^"]+\.html)"', src)
    unique = list(set(prod_matches))
    log(f"\n  Tissot product URLs from source ({len(unique)} unique): {unique[:5]}")

    # Check pagination
    log("\n  Pagination elements:")
    pag_sels = ["a[rel='next']", ".pagination a", ".pager a",
                "a[href*='page=']", "button[class*='load']",
                ".show-more", "[data-page]"]
    for sel in pag_sels:
        els = q(driver, sel)
        if els:
            log(f"    {sel}: {len(els)} elements")
            for el in els[:2]:
                log(f"      text='{el.text.strip()}' href='{el.get_attribute('href')}'")

    # Check data attributes on product items
    try:
        items = driver.execute_script("""
            var all = document.querySelectorAll('a');
            var prods = [];
            all.forEach(function(a) {
                if(a.href && a.href.indexOf('/en-us/tissot-') > -1) {
                    var ds = {};
                    for(var k in a.dataset) ds[k] = a.dataset[k];
                    var parent = a.parentElement;
                    var pds = {};
                    if(parent) for(var k in parent.dataset) pds[k] = parent.dataset[k];
                    prods.push({href: a.href, adata: ds, pdata: pds, pcls: parent ? parent.className.substr(0,60) : ''});
                }
            });
            return prods.slice(0,5);
        """)
        log(f"\n  Tissot product link details: {items}")
    except Exception as e:
        log(f"  JS query error: {e}")


def research_rado(driver):
    log("\n" + "="*60)
    log("DEEP DIVE: Rado")
    log("="*60)
    url = "https://www.rado.com/en_us/watches/all-watches/"
    load(driver, url, wait=16)
    scroll(driver, n=4, pause=3)
    log(f"  Title: {driver.title}")
    log(f"  URL: {driver.current_url}")
    log(f"  Source: {len(driver.page_source)} bytes")

    candidates = [
        "a[href*='/en_us/watches/']",
        ".product-tile a",
        ".product-card a",
        "[class*='product'] a[href*='/en_us/']",
        ".catalog-grid a",
        ".product-item a",
        "a[href*='/watches/'][href$='.html']",
        "a[href*='/en_us/'][href$='.html']",
    ]
    log("  Candidate selectors:")
    for sel in candidates:
        n, hrefs = count_unique_hrefs(driver, sel)
        log(f"    [{n}u] {sel}: {hrefs[:2]}")

    # Extract from source
    src = driver.page_source
    prods = re.findall(r'"(/en_us/watches/[^"]+\.html)"', src)
    unique = list(set(prods))
    log(f"\n  Product URLs from source ({len(unique)}): {unique[:5]}")

    # JS inspect
    try:
        items = driver.execute_script("""
            var cards = document.querySelectorAll('[class*="product"],[class*="tile"],[class*="item"]');
            var result = [];
            cards.forEach(function(c) {
                var a = c.querySelector('a');
                if(!a) return;
                var ds = {};
                for(var k in c.dataset) ds[k] = c.dataset[k];
                var ads = {};
                for(var k in a.dataset) ads[k] = a.dataset[k];
                if(Object.keys(ds).length || Object.keys(ads).length) {
                    result.push({cls: c.className.substr(0,60), href: a.href, data: ds, adata: ads});
                }
            });
            return result.slice(0,10);
        """)
        log(f"\n  Product cards with data attrs:")
        for it in items[:5]:
            log(f"    {it}")
    except Exception as e:
        log(f"  JS error: {e}")

    # Pagination
    log("\n  Pagination:")
    pag_sels = ["a[rel='next']", ".pagination a", "a[href*='page']",
                "button[class*='more']", "button[class*='load']",
                "[data-infinite]", ".load-more"]
    for sel in pag_sels:
        els = q(driver, sel)
        if els:
            log(f"    {sel}: {len(els)}")
            for el in els[:2]:
                log(f"      '{el.text.strip()}' href='{el.get_attribute('href')}'")

    # Check a product page
    if unique:
        prod_url = "https://www.rado.com" + unique[0]
        check_product_page(driver, prod_url, "Rado product page")


def research_victorinox(driver):
    log("\n" + "="*60)
    log("DEEP DIVE: Victorinox")
    log("="*60)
    url = "https://www.victorinox.com/en-US/Products/Watches/c/TP_AllProducts/"
    load(driver, url, wait=16)
    scroll(driver, n=3, pause=3)
    log(f"  Title: {driver.title}")
    log(f"  URL: {driver.current_url}")
    log(f"  Source: {len(driver.page_source)} bytes")

    candidates = [
        "a[href*='/Products/Watches/'][href*='/p/']",
        ".product-tile a[href*='/p/']",
        ".product-tile__image-wrapper a",
        ".product-tile__name a",
        "a[href*='/Watches/'][href*='/p/']",
        "a.product-name-link",
        ".product-list-item a",
        "[class*='product-item'] a[href*='/p/']",
        # Hybris platform
        ".thumb-image a",
        ".product-lister-item a.thumb-image",
    ]
    log("  Candidate selectors:")
    for sel in candidates:
        n, hrefs = count_unique_hrefs(driver, sel)
        log(f"    [{n}u] {sel}: {hrefs[:2]}")

    # Extract from source
    src = driver.page_source
    prods = re.findall(r'"(/en-US/Products/Watches/[^"]*?/p/[^"]+)"', src)
    unique = list(set(prods))
    log(f"\n  Product URLs from source (/p/ pattern, {len(unique)}): {unique[:5]}")

    # Also try other product URL patterns
    prods2 = re.findall(r'"(/en-US/Products/Watches/[A-Z][^"]{10,})"', src)
    unique2 = list(set(prods2))
    log(f"  Product URLs (broader, {len(unique2)}): {unique2[:5]}")

    # JS inspect
    try:
        items = driver.execute_script("""
            var tiles = document.querySelectorAll('.product-tile,.product-lister-item,[class*="product-item"]');
            var result = [];
            tiles.forEach(function(t) {
                var a = t.querySelector('a');
                var ds = {};
                for(var k in t.dataset) ds[k] = t.dataset[k];
                var pcode = t.getAttribute('data-product-code') || t.getAttribute('data-sku') || '';
                result.push({
                    cls: t.className.substr(0,60),
                    href: a ? a.href : '',
                    data: ds,
                    pcode: pcode
                });
            });
            return result.slice(0,5);
        """)
        log(f"\n  Product tiles:")
        for it in items:
            log(f"    {it}")
    except Exception as e:
        log(f"  JS error: {e}")

    # Check a product page if found
    if unique:
        prod_url = "https://www.victorinox.com" + unique[0]
        check_product_page(driver, prod_url, "Victorinox product page")


def research_movado(driver):
    log("\n" + "="*60)
    log("DEEP DIVE: Movado")
    log("="*60)
    url = "https://www.movado.com/us/en/shop-watches/shop-all-watches"
    load(driver, url, wait=18)
    scroll(driver, n=4, pause=3)
    log(f"  Title: {driver.title}")
    log(f"  URL: {driver.current_url}")
    log(f"  Source: {len(driver.page_source)} bytes")

    candidates = [
        ".product-card a[href*='/us/en/']",
        ".product-card a",
        ".product-tile a",
        "a[href*='/us/en/'][href$='.html']",
        "a.product-card__link",
        ".product-grid-item a",
        ".plp-product a",
        "[class*='product-card'] a",
        "a[href*='/watches/']",
        ".product-image a",
        "a[data-pid]",
        "a[data-product-id]",
    ]
    log("  Candidate selectors:")
    for sel in candidates:
        n, hrefs = count_unique_hrefs(driver, sel)
        log(f"    [{n}u] {sel}: {hrefs[:2]}")

    # Extract from source
    src = driver.page_source
    prods = re.findall(r'"(/us/en/[^"]{5,}\.html)"', src)
    unique = list(set(prods))
    log(f"\n  Product URLs from source (.html, {len(unique)}): {unique[:5]}")

    # Also look at all /us/en/ links
    all_en = re.findall(r'"(/us/en/[^"?#]{5,})"', src)
    unique_en = list(set(all_en))
    log(f"  All /us/en/ URLs ({len(unique_en)}): {unique_en[:10]}")

    # JS inspect
    try:
        items = driver.execute_script("""
            var cards = document.querySelectorAll('.product-card,.product-tile,[class*="product-card"]');
            var result = [];
            cards.forEach(function(c) {
                var a = c.querySelector('a');
                var ds = {};
                for(var k in c.dataset) ds[k] = c.dataset[k];
                var ads = {};
                if(a) for(var k in a.dataset) ads[k] = a.dataset[k];
                result.push({
                    cls: c.className.substr(0,80),
                    href: a ? a.href : '',
                    data: ds,
                    adata: ads
                });
            });
            return result.slice(0,10);
        """)
        log(f"\n  Product cards:")
        for it in items[:5]:
            log(f"    {it}")
    except Exception as e:
        log(f"  JS error: {e}")

    # Pagination
    log("\n  Pagination:")
    pag_sels = ["a[rel='next']", ".pagination a", "a[href*='page']",
                "button[class*='more']", ".load-more", "[data-infinite]",
                "button[class*='load']", ".show-more-btn"]
    for sel in pag_sels:
        els = q(driver, sel)
        if els:
            log(f"    {sel}: {len(els)}")
            for el in els[:2]:
                log(f"      '{el.text.strip()}' href='{el.get_attribute('href')}'")

    # Check first product
    prod_links = q(driver, ".product-card a")
    valid = []
    for el in prod_links:
        h = el.get_attribute("href") or ""
        if "/us/en/" in h and h != url and "shop-all-watches" not in h:
            valid.append(h)
    if valid:
        check_product_page(driver, valid[0], "Movado product page")


def research_michele(driver):
    log("\n" + "="*60)
    log("DEEP DIVE: Michele")
    log("="*60)
    url = "https://www.michele.com/en-us/watches/"
    log(f"  Loading (waiting longer for Cloudflare)...")
    load(driver, url, wait=30)  # Long wait for CF
    log(f"  Title: {driver.title}")
    log(f"  URL: {driver.current_url}")
    log(f"  Source: {len(driver.page_source)} bytes")

    src = driver.page_source
    is_blocked = ("just a moment" in driver.title.lower() or
                  len(src) < 5000 or
                  "challenge" in src.lower())

    if is_blocked:
        log("  STILL BLOCKED after 30s wait — Cloudflare JS challenge not resolving")
        log("  Trying alternate URL...")
        load(driver, "https://www.michele.com/en-us/watches/all-watches/", wait=30)
        log(f"  Title: {driver.title}")
        log(f"  Source: {len(driver.page_source)} bytes")
    else:
        log("  Page loaded successfully!")
        candidates = [
            "a[href*='/products/']",
            "a[href*='/watches/']",
            ".product-card a",
            ".product-tile a",
            "li.product a",
            ".collection-grid a",
        ]
        for sel in candidates:
            n, hrefs = count_unique_hrefs(driver, sel)
            log(f"    [{n}u] {sel}: {hrefs[:2]}")

        src = driver.page_source
        prods = re.findall(r'"(/en-us/[^"]+)"', src)
        unique = list(set(prods))
        log(f"\n  Product URLs from source ({len(unique)}): {unique[:5]}")


def research_gshock(driver):
    log("\n" + "="*60)
    log("DEEP DIVE: G-Shock (Casio)")
    log("="*60)

    # Try direct casio gshock URL with longer wait
    urls = [
        "https://www.casio.com/us/watches/gshock/",
        "https://www.gshock.com/",
        "https://gshock.casio.com/us/",
    ]
    for url in urls:
        log(f"\n  Trying: {url}")
        load(driver, url, wait=20)
        title = driver.title
        src_len = len(driver.page_source)
        log(f"  Title: {title}")
        log(f"  Source: {src_len} bytes")
        if src_len > 5000 and "access denied" not in title.lower():
            log("  -> Page loaded!")
            src = driver.page_source

            candidates = [
                "a[href*='/gshock/']",
                ".product-tile a",
                ".product-card a",
                "a.product-tile__link",
                "[class*='product'] a",
                "a[href*='/watches/']",
                ".item a",
            ]
            for sel in candidates:
                n, hrefs = count_unique_hrefs(driver, sel)
                if n > 0:
                    log(f"    [{n}u] {sel}: {hrefs[:2]}")

            prods = re.findall(r'"((?:https://www\.casio\.com)?/us/watches/gshock/[^"]+)"', src)
            log(f"  Product URLs: {list(set(prods))[:5]}")

            # Check pagination
            for sel in ["a[rel='next']", ".pagination a", "a[href*='page']",
                       "[data-page]", "button[class*='more']"]:
                els = q(driver, sel)
                if els:
                    log(f"  Pagination: {sel} -> {len(els)} els")
            break
        else:
            log(f"  -> Blocked (src={src_len})")


def main():
    driver = make_driver()
    try:
        research_gshock(driver)
        research_hamilton(driver)
        research_tissot(driver)
        research_rado(driver)
        research_victorinox(driver)
        research_movado(driver)
        research_michele(driver)
    finally:
        driver.quit()

    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))
    print(f"\nSaved deep research to: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
