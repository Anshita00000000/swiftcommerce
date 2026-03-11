"""
config_adapter.py

Translates any brand YAML config into one standardised internal dict that
every pipeline module consumes.  No pipeline module reads raw YAML keys
directly — they always operate on the output of adapt().

Canonical output keys (pipeline contract):
    brand_name, base_url, primary_url, primary_pagination, gender_urls,
    product_link_selector, code_extraction, listing_availability, driver,
    anti_bot, pre_scrape_actions, selectors, product_availability,
    spec_extraction, spec_table, images, shopify_mapping

Backward-compat aliases are also emitted so existing modules keep working
without modification:
    category_urls   — url_collector fallback source list
    default_gender  — url_collector unmatched-product fallback
    pagination      — url_collector alias for primary_pagination
    fields_selectors — product_scraper field → selector-list dict
    fields           — product_scraper field → first-selector string dict
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Selector fields that live inside the YAML `selectors` block and are
# used for per-product text extraction.  Order is not significant here.
# ---------------------------------------------------------------------------
_SELECTOR_FIELDS = ("title", "price", "sku", "description", "features", "color", "size")

# ---------------------------------------------------------------------------
# Pagination type aliases: normalise legacy names to canonical names.
# ---------------------------------------------------------------------------
_PAGINATION_TYPE_ALIASES: Dict[str, str] = {
    "scroll":          "infinite_scroll",
    "infinite_scroll": "infinite_scroll",
    "next_button":     "next_button",
    "load_more":       "load_more",
    "url_param":       "url_param",
    "none":            "none",
}

# ---------------------------------------------------------------------------
# Image method aliases: normalise legacy short names.
# ---------------------------------------------------------------------------
_IMAGE_METHOD_ALIASES: Dict[str, str] = {
    "dom":          "dom_selector",
    "dom_selector": "dom_selector",
    "magento_json": "magento_json",
    "both":         "both",
}


# ===========================================================================
# Public API
# ===========================================================================

def adapt(raw: dict) -> dict:
    """
    Normalise a brand YAML config dict into the pipeline's internal format.

    Accepts any YAML style found in this project (template, tissot, hamilton,
    seiko, wolf1834) and emits a single, consistent output shape regardless
    of which optional sections are present in the source file.

    Args:
        raw: Dict loaded directly from a brand YAML file via PyYAML.

    Returns:
        Normalised config dict ready for consumption by all pipeline modules.
    """
    # Top-level YAML sections used throughout this function
    url_col       = raw.get("url_collection", {}) or {}
    anti_bot_raw  = raw.get("anti_bot", {}) or {}
    driver_raw    = raw.get("driver", {}) or {}
    sel_raw       = raw.get("selectors", {}) or {}
    img_raw       = raw.get("image_extraction", raw.get("images", {})) or {}
    shopify_raw   = raw.get("shopify", {}) or {}
    spec_tbl_raw  = raw.get("spec_table", {}) or {}

    # ------------------------------------------------------------------
    # Shared scroll_pause default used as fallback for all pagination
    # blocks.  Prefer anti_bot.scroll_pause when explicitly set.
    # ------------------------------------------------------------------
    scroll_pause_default = float(anti_bot_raw.get("scroll_pause") or 3.0)

    # Shared fallback pagination block: a pagination block nested directly
    # under url_collection, or a top-level pagination block from older YAML
    # styles.  Used whenever a more-specific block is absent.
    pag_fallback: dict = url_col.get("pagination") or raw.get("pagination") or {}

    # ===================================================================
    # 1. IDENTITY
    # ===================================================================
    # Prefer display_name (human label), fall back to brand_name, then brand.
    brand_name: str = (
        raw.get("display_name")
        or raw.get("brand_name")
        or raw.get("brand", "")
    )
    base_url: str = raw.get("base_url", "")

    # ===================================================================
    # 2. PRIMARY URL + PRIMARY PAGINATION
    # ===================================================================
    primary_url: str = url_col.get("primary_url", "")

    # primary_pagination block: explicit block wins, otherwise fall back to
    # the shared pag_fallback so brands with a single pagination block work.
    primary_pag_raw: dict = url_col.get("primary_pagination") or pag_fallback
    primary_pagination: dict = _normalize_pagination(primary_pag_raw, scroll_pause_default)

    # ===================================================================
    # 3. GENDER URLS
    # Each entry: {url, gender, pagination}
    # Each entry's pagination falls back to the shared pag_fallback.
    # ===================================================================
    gender_urls: List[Dict[str, Any]] = []
    for entry in url_col.get("gender_urls", []):
        entry_pag_raw: dict = entry.get("pagination") or pag_fallback
        # Per-entry scroll_pause: entry block > anti_bot > global default
        entry_scroll_pause = float(
            anti_bot_raw.get("scroll_pause")
            or entry_pag_raw.get("scroll_pause")
            or scroll_pause_default
        )
        gender_urls.append({
            "url":        entry.get("url", ""),
            "gender":     entry.get("gender", ""),
            "pagination": _normalize_pagination(entry_pag_raw, entry_scroll_pause),
        })

    # ===================================================================
    # 4. PRODUCT LINK SELECTOR
    # Prefer url_collection.product_link_selector; fall back to the legacy
    # position at selectors.product_link (old template style).
    # ===================================================================
    product_link_selector: str = _first(
        url_col.get("product_link_selector")
        or sel_raw.get("product_link", "")
    )

    # ===================================================================
    # 5. CODE EXTRACTION
    # Passed through as-is; url_collector interprets the sub-fields.
    # ===================================================================
    code_extraction: Dict[str, Any] = url_col.get("code_extraction") or {}

    # ===================================================================
    # 6. LISTING AVAILABILITY  (optional / best-effort)
    # Records sold-out status from listing pages into products_raw.json.
    # Does NOT influence which products are imported.
    # ===================================================================
    listing_availability: Dict[str, Any] = url_col.get("listing_availability") or {}

    # ===================================================================
    # 7. DRIVER
    # Timing values that control Selenium page-load behaviour.
    # ===================================================================
    driver: Dict[str, Any] = {
        # Seconds to wait after the very first page load in a session.
        "first_load_wait": float(driver_raw.get("first_load_wait", 15)),
        # Seconds to wait after each subsequent page load before parsing.
        # Also used to derive page_load_timeout (value × 6, minimum 60 s).
        "page_load_wait":  float(driver_raw.get("page_load_wait", 5)),
    }

    # ===================================================================
    # 8. ANTI-BOT
    # Merged from the anti_bot and driver YAML blocks into one flat dict
    # that all modules consuming timing / browser settings can use.
    # ===================================================================
    delay_min   = float(anti_bot_raw.get("random_delay_min", 2))
    delay_max   = float(anti_bot_raw.get("random_delay_max", 5))
    # page_delay is the average of min/max; used as a single-value fallback.
    page_delay  = (delay_min + delay_max) / 2
    # first_load_wait comes from driver block; fall back to page_delay average.
    first_load_wait    = float(driver_raw.get("first_load_wait", page_delay))
    # page_load_timeout = page_load_wait × 6, floored at 60 seconds.
    page_load_timeout  = max(60.0, float(driver_raw.get("page_load_wait", 10)) * 6)

    anti_bot: Dict[str, Any] = {
        "page_delay":        page_delay,
        "random_delay_min":  delay_min,
        "random_delay_max":  delay_max,
        "scroll_pause":      scroll_pause_default,
        "first_load_wait":   first_load_wait,
        "page_load_timeout": page_load_timeout,
        "slow_scroll":       bool(anti_bot_raw.get("slow_scroll", False)),
        "window_size":       anti_bot_raw.get("window_size", [1400, 900]),
    }
    # Optional custom User-Agent forwarded to driver_factory if present.
    if "user_agent" in anti_bot_raw:
        anti_bot["user_agent"] = anti_bot_raw["user_agent"]

    # ===================================================================
    # 9. PRE-SCRAPE ACTIONS
    # List of browser actions run on each product page before extraction.
    # Passed through as-is; product_scraper interprets the action dicts.
    # ===================================================================
    pre_scrape_actions: List[Dict[str, Any]] = raw.get("pre_scrape_actions") or []

    # ===================================================================
    # 10. SELECTORS  (field → list of CSS selectors)
    # Every field is always stored as a list.  A single string in YAML is
    # wrapped in a list automatically.  Empty YAML value → empty list.
    # ===================================================================
    selectors: Dict[str, Any] = {
        field: _as_list(sel_raw.get(field, [])) for field in _SELECTOR_FIELDS
    }

    # sku_prefix_to_strip is a sibling of sku inside the selectors block.
    sku_prefix: str = sel_raw.get("sku_prefix_to_strip", "")
    if sku_prefix:
        selectors["sku_prefix_to_strip"] = sku_prefix

    # Backward-compat: url_collector reads config["selectors"]["product_link"]
    # as a string.  Inject it here so that code path keeps working unchanged.
    selectors["product_link"] = product_link_selector

    # ===================================================================
    # 11. PRODUCT AVAILABILITY  (optional / best-effort)
    # Records availability text from product pages into products_raw.json.
    # Does NOT filter out any products from the import pipeline.
    # ===================================================================
    product_availability: Dict[str, Any] = raw.get("product_availability") or {}

    # ===================================================================
    # 12. SPEC EXTRACTION
    # Pass the entire block through as-is so that new types (accordion,
    # icon_map, etc.) are available without any adapter changes.
    # If spec_extraction is absent, derive it from the legacy
    # spec_table.extraction_type field used by older brand YAMLs.
    # Default type is "row" when nothing is configured.
    # ===================================================================
    spec_extraction: Dict[str, Any] = _normalize_spec_extraction(raw, sel_raw)

    # ===================================================================
    # 13. SPEC TABLE
    # Header text + ordered row definitions for the HTML spec table that
    # is injected into each product's Shopify body HTML.
    # ===================================================================
    spec_table: Dict[str, Any] = {
        "header": spec_tbl_raw.get("header", "Item Specification"),
        "rows":   spec_tbl_raw.get("rows") or [],
    }

    # ===================================================================
    # 14. IMAGES
    # Unified image extraction config from image_extraction or legacy
    # images YAML blocks.
    # ===================================================================
    images: Dict[str, Any] = _normalize_images(img_raw)

    # ===================================================================
    # 15. SHOPIFY MAPPING
    # Built from the shopify YAML block.  Includes both the new flat fields
    # (vendor, product_type, product_category, default_tags, gender_tags)
    # and the legacy nested-dict structure that normalizer.py still reads.
    # ===================================================================
    shopify_mapping: Dict[str, Any] = _normalize_shopify_mapping(shopify_raw, brand_name)

    # ===================================================================
    # Assemble and return the canonical output dict
    # ===================================================================
    cfg: Dict[str, Any] = {
        # ── Canonical contract keys ─────────────────────────────────────
        "brand_name":            brand_name,
        "base_url":              base_url,
        "primary_url":           primary_url,
        "primary_pagination":    primary_pagination,
        "gender_urls":           gender_urls,
        "product_link_selector": product_link_selector,
        "code_extraction":       code_extraction,
        "listing_availability":  listing_availability,
        "driver":                driver,
        "anti_bot":              anti_bot,
        "pre_scrape_actions":    pre_scrape_actions,
        "selectors":             selectors,
        "product_availability":  product_availability,
        "spec_extraction":       spec_extraction,
        "spec_table":            spec_table,
        "images":                images,
        "shopify_mapping":       shopify_mapping,

        # ── Backward-compat aliases ─────────────────────────────────────
        # url_collector: config.get("category_urls", [])
        "category_urls": (
            url_col.get("category_urls")
            or raw.get("category_urls")
            or []
        ),
        # url_collector: config.get("default_gender", "")
        "default_gender": url_col.get("default_gender", ""),
        # url_collector: config.get("primary_pagination", config.get("pagination", {}))
        "pagination": primary_pagination,
        # product_scraper: config.get("fields_selectors", {})
        # Excludes product_link and sku_prefix_to_strip — only text-extraction
        # fields so scraper iteration over the dict stays clean.
        "fields_selectors": {f: selectors[f] for f in _SELECTOR_FIELDS},
        # product_scraper legacy: config["fields"][field] single-string shortcut
        "fields": {
            f: selectors[f][0] if selectors[f] else ""
            for f in _SELECTOR_FIELDS
        },
    }

    return cfg


def validate(adapted_config: dict) -> None:
    """
    Log warnings for missing or empty critical fields in an adapted config.

    Never raises — validation is advisory only.  Call immediately after
    adapt() during brand onboarding or pipeline startup to surface
    misconfigured brands early without aborting the run.

    Args:
        adapted_config: Dict returned by adapt().
    """
    brand = adapted_config.get("brand_name", "?")
    warnings: List[str] = []

    # ── Identity ────────────────────────────────────────────────────────
    if not adapted_config.get("brand_name"):
        warnings.append(
            "[brand_name] Missing — set display_name, brand_name, or brand in YAML."
        )
    if not adapted_config.get("base_url"):
        warnings.append(
            "[base_url] Missing — relative product links and image URLs will break."
        )

    # ── URL collection ───────────────────────────────────────────────────
    if not adapted_config.get("primary_url"):
        warnings.append(
            "[primary_url] Missing — url_collection.primary_url not set. "
            "Pipeline will fall back to category_urls if present."
        )
    if not adapted_config.get("product_link_selector"):
        warnings.append(
            "[product_link_selector] Missing — no CSS selector for product links. "
            "URL collection will find zero products."
        )

    # ── Per-product selectors ────────────────────────────────────────────
    selectors = adapted_config.get("selectors", {})
    for field in ("title", "price", "sku"):
        if not selectors.get(field):
            warnings.append(
                f"[selectors.{field}] Empty selector list — "
                f"'{field}' will be blank on every product."
            )

    # ── Image extraction ─────────────────────────────────────────────────
    images = adapted_config.get("images", {})
    if (
        images.get("method") in ("dom_selector", "both")
        and not images.get("selectors")
    ):
        warnings.append(
            "[image_extraction.dom_selectors] Empty — method is 'dom_selector' or "
            "'both' but no DOM selectors are configured. No images will be collected."
        )

    # ── Spec table ───────────────────────────────────────────────────────
    if not adapted_config.get("spec_table", {}).get("rows"):
        warnings.append(
            "[spec_table.rows] Empty — product body HTML will have no specification table."
        )

    # ── Shopify mapping ──────────────────────────────────────────────────
    shopify = adapted_config.get("shopify_mapping", {})
    if not shopify.get("vendor", {}).get("value"):
        warnings.append("[shopify.vendor] Empty — Shopify Vendor column will be blank.")
    if not shopify.get("product_type"):
        warnings.append("[shopify.product_type] Empty — Shopify Type column will be blank.")

    if warnings:
        logger.warning(
            "Config validation warnings for brand '%s':\n  %s",
            brand,
            "\n  ".join(warnings),
        )
    else:
        logger.debug("Config validation passed for brand '%s'.", brand)


# ===========================================================================
# Normalisation helpers
# ===========================================================================

def _normalize_pagination(raw: Any, scroll_pause_default: float) -> Dict[str, Any]:
    """
    Normalise any pagination block from YAML into the canonical internal format.

    Canonical output keys:
        type                 — pagination strategy (see _PAGINATION_TYPE_ALIASES)
        scroll_pause         — seconds between scroll steps
        max_scrolls          — hard scroll limit for infinite_scroll
        next_button_selector — CSS selector for next-page button
        load_more_selector   — CSS selector for load-more button
        max_pages            — page cap for next_button / url_param
        param_template       — .format() template for url_param page numbers

    Args:
        raw:                  Raw pagination dict from YAML (may be None / {}).
        scroll_pause_default: Fallback scroll_pause when not set in the block.
    """
    if not raw or not isinstance(raw, dict):
        raw = {}

    raw_type = str(raw.get("type", "none")).lower()
    pag_type = _PAGINATION_TYPE_ALIASES.get(raw_type, raw_type)

    return {
        "type":                 pag_type,
        "scroll_pause":         float(raw.get("scroll_pause", scroll_pause_default)),
        "max_scrolls":          int(raw.get("max_scrolls", 60)),
        "next_button_selector": raw.get("next_button_selector", ""),
        "load_more_selector":   raw.get("load_more_selector", ""),
        "max_pages":            int(raw.get("max_pages", 20)),
        # Accept param_template (new template key) or param (legacy brand YAMLs).
        "param_template":       (
            raw.get("param_template")
            or raw.get("param", "?page={}")
        ),
    }


def _normalize_images(raw: dict) -> Dict[str, Any]:
    """
    Unify image_extraction (new template key) and images (legacy key) YAML
    blocks into one internal dict consumed by product_scraper.

    Canonical output keys:
        method             — options: dom_selector | magento_json | both
        selectors          — list of CSS selectors for DOM image elements
        attr               — HTML attribute holding the image URL
        fallback_attr      — fallback attribute when attr is empty
        exclude_keywords   — URL substrings that disqualify an image
        strip_query_params — strip '?...' from image URLs before dedup
        script_contains    — substring to identify Magento gallery scripts
        script_pattern     — regex pattern to extract URLs from script text
    """
    if not raw or not isinstance(raw, dict):
        raw = {}

    raw_method = str(raw.get("method", "dom_selector"))
    method = _IMAGE_METHOD_ALIASES.get(raw_method, "dom_selector")

    # dom_selectors: accept list (new template) or single string (legacy "selector" key).
    dom_sels: List[str] = _as_list(
        raw.get("dom_selectors")
        or raw.get("selector", "")
    )

    # Attribute key: accept dom_attribute (new template) or attr (legacy).
    attr: str = raw.get("dom_attribute") or raw.get("attr", "src")

    # strip_query_params may sit at the top level or inside a url_cleanup sub-block.
    url_cleanup: dict = raw.get("url_cleanup") or {}
    strip_qs: bool = bool(
        url_cleanup.get("strip_query_params", raw.get("strip_query_params", False))
    )

    return {
        "method":             method,
        "selectors":          dom_sels,
        "attr":               attr,
        "fallback_attr":      raw.get("fallback_attr", "data-src"),
        "exclude_keywords":   raw.get("exclude_keywords") or [],
        "strip_query_params": strip_qs,
        "script_contains":    raw.get("script_contains", ""),
        "script_pattern":     raw.get("script_pattern", r'"url":"(https?://[^"]+)"'),
    }


def _normalize_shopify_mapping(shopify_raw: dict, brand_name: str) -> Dict[str, Any]:
    """
    Build the internal shopify_mapping dict from the shopify YAML block.

    The output contains both:
      - Flat canonical fields (vendor, product_type, product_category,
        default_tags, gender_tags) for current and future modules.
      - Legacy nested-dict fields (vendor → {value: ...}, type → {value: ...},
        title → {source: ...}, tags → {static: [...]}) that normalizer.py
        currently reads via _resolve_mapping().

    Args:
        shopify_raw: The raw `shopify` block dict from YAML.
        brand_name:  Fallback vendor string if shopify.vendor is absent.
    """
    if not shopify_raw or not isinstance(shopify_raw, dict):
        shopify_raw = {}

    vendor       = shopify_raw.get("vendor") or brand_name
    product_type = shopify_raw.get("product_type", "")
    product_cat  = shopify_raw.get("product_category", "")
    default_tags = list(shopify_raw.get("default_tags") or [])

    # gender_tags: {mens, womens, unisex} — tag strings appended to the
    # Shopify Tags column depending on which gender collection a product
    # was found in during URL collection (Pass 2).
    raw_gender_tags = shopify_raw.get("gender_tags") or {}
    if not isinstance(raw_gender_tags, dict):
        raw_gender_tags = {}
    gender_tags: Dict[str, str] = {
        "mens":   str(raw_gender_tags.get("mens", "")),
        "womens": str(raw_gender_tags.get("womens", "")),
        "unisex": str(raw_gender_tags.get("unisex", "")),
    }

    return {
        # ── Flat canonical fields (new) ──────────────────────────────────
        "vendor":           vendor,          # plain string
        "product_type":     product_type,    # plain string
        "product_category": product_cat,     # plain string
        "default_tags":     default_tags,    # list[str]
        "gender_tags":      gender_tags,     # {mens, womens, unisex}

        # ── Legacy nested-dict fields (consumed by normalizer.py) ────────
        # normalizer: shopify_mapping.get("vendor", {}).get("value", ...)
        "vendor_mapping": {"value": vendor},
        # normalizer: _resolve_mapping(raw, shopify_mapping.get("type", {}))
        "type":        {"value": product_type},
        # normalizer: _get_mapped(raw, shopify_mapping, "title", "title")
        "title":       {"source": "title"},
        "description": {"source": "description"},
        "price":       {"source": "price"},
        "sku":         {"source": "sku"},
        "published":   "TRUE",
        # normalizer: shopify_mapping.get("tags", config.get("default_tags", []))
        "tags": {"static": default_tags},
    }


def _normalize_spec_extraction(raw: dict, sel_raw: dict) -> Dict[str, Any]:
    """
    Produce the spec_extraction dict consumed by product_scraper.

    Resolution order:
      1. spec_extraction block in YAML (new template format) — passed through
         as-is with type defaulted to "row" if absent.
      2. spec_table.extraction_type (legacy format used by tissot / hamilton)
         — translated into the equivalent spec_extraction structure.
      3. Default: {type: "row"} with empty selectors.

    All six extraction types are supported:
        seiko_dual  — no extra fields; logic is hardcoded in product_scraper
        row         — row_selector, key_selector, value_selector
        label_value — label_selector, value_selector
        aria_label  — row_selector, label_attr, known_keys
        accordion   — item_selector, label_selector, value_selector
        icon_map    — icon_item_selector, text_selector, icon_selector,
                      icon_attribute, icon_label_map

    Args:
        raw:     Full raw YAML dict.
        sel_raw: The raw `selectors` block (used by legacy brand YAMLs to
                 store spec-related selectors alongside field selectors).
    """
    # ── Path 1: explicit spec_extraction block (new template YAML) ──────
    explicit: dict = raw.get("spec_extraction") or {}
    if explicit:
        result = dict(explicit)
        if "type" not in result:
            result["type"] = "row"
        return result

    # ── Path 2: legacy spec_table.extraction_type (tissot, hamilton) ────
    spec_tbl_raw = raw.get("spec_table") or {}
    extraction_type: str = spec_tbl_raw.get("extraction_type", "")

    if extraction_type == "aria_label_pairs":
        # Tissot: elements carry aria-label="KEY VALUE" as a single string.
        # Selectors were stored inside the `selectors` YAML block.
        return {
            "type":         "aria_label",
            "row_selector": sel_raw.get(
                "specs_row_selector", ".ti-pdp-details-content[aria-label]"
            ),
            "label_attr":   sel_raw.get("specs_label_attr", "aria-label"),
        }

    if extraction_type == "label_value_pairs":
        # Hamilton: separate .attribute-label / .attribute-value elements
        # zipped together in DOM order.
        return {
            "type":           "label_value",
            "label_selector": sel_raw.get("specs_label_selector", ".attribute-label"),
            "value_selector": sel_raw.get("specs_value_selector", ".attribute-value"),
        }

    # ── Path 3: derive from spec_table row/key/value selectors ──────────
    # Older template-style configs that put row/key/value directly on spec_table.
    row_sel = spec_tbl_raw.get("row_selector", "")
    key_sel = spec_tbl_raw.get("key_selector", "")
    val_sel = spec_tbl_raw.get("value_selector", "")
    if row_sel or key_sel or val_sel:
        return {
            "type":           "row",
            "row_selector":   row_sel,
            "key_selector":   key_sel,
            "value_selector": val_sel,
        }

    # ── Default ──────────────────────────────────────────────────────────
    return {"type": "row"}


# ===========================================================================
# Primitive helpers
# ===========================================================================

def _as_list(val: Any) -> List[str]:
    """
    Coerce val to a list of non-empty, stripped strings.

      None / "" / [] / {}  →  []
      "some string"        →  ["some string"]
      ["a", "", "b"]       →  ["a", "b"]
    """
    if not val:
        return []
    if isinstance(val, list):
        return [str(v).strip() for v in val if v and str(v).strip()]
    s = str(val).strip()
    return [s] if s else []


def _first(val: Any) -> str:
    """Return the first non-empty string from val (list or scalar), or ''."""
    items = _as_list(val)
    return items[0] if items else ""
