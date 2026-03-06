"""
config_adapter.py
Translates brand YAML configs (wolf1834, seiko, tissot, hamilton style)
into the internal format expected by all pipeline modules.
Allows zero Python changes when adding a new brand.
"""

import random
from typing import Any, Dict, List, Optional


def adapt(raw: dict) -> dict:
    """
    Normalize a brand YAML config dict into the pipeline's internal format.

    Handles both the template style and the richer brand-specific style.
    """
    cfg: Dict[str, Any] = {}

    # -----------------------------------------------------------------------
    # Identity
    # -----------------------------------------------------------------------
    cfg["brand_name"] = (
        raw.get("display_name")
        or raw.get("brand_name")
        or raw.get("brand", "")
    )
    cfg["base_url"] = raw.get("base_url", "")

    # -----------------------------------------------------------------------
    # Category URLs + product link selector
    # -----------------------------------------------------------------------
    url_col = raw.get("url_collection", {})
    cfg["category_urls"] = (
        url_col.get("category_urls")
        or raw.get("category_urls", [])
    )
    cfg["selectors"] = {}

    # Product link selector (string or first of list)
    product_link = (
        url_col.get("product_link_selector")
        or raw.get("selectors", {}).get("product_link", "")
    )
    cfg["selectors"]["product_link"] = _first(product_link)

    # -----------------------------------------------------------------------
    # Pagination
    # -----------------------------------------------------------------------
    pag_raw = url_col.get("pagination", raw.get("pagination", {}))
    pag_type = pag_raw.get("type", "none")

    # Normalize pagination type names
    type_map = {
        "scroll": "infinite_scroll",
        "infinite_scroll": "infinite_scroll",
        "next_button": "next_button",
        "load_more": "load_more",
        "url_param": "url_param",
        "none": "none",
    }
    pag_type = type_map.get(pag_type, pag_type)

    anti_bot_raw = raw.get("anti_bot", {})
    driver_raw = raw.get("driver", {})

    scroll_pause = (
        anti_bot_raw.get("scroll_pause")
        or pag_raw.get("scroll_pause")
        or 3.0
    )

    cfg["pagination"] = {
        "type": pag_type,
        "max_scrolls": pag_raw.get("max_scrolls", 60),
        "scroll_pause": scroll_pause,
        "next_button_selector": pag_raw.get("next_button_selector", ""),
        "load_more_selector": pag_raw.get("load_more_selector", ""),
        "max_pages": pag_raw.get("max_pages", 20),
        # url_param pagination (Tissot)
        "param_template": pag_raw.get("param", "?page={}"),
    }

    # -----------------------------------------------------------------------
    # Per-field selectors (each stored as a list for fallback tries)
    # -----------------------------------------------------------------------
    sel_raw = raw.get("selectors", {})
    field_names = ["title", "price", "sku", "description", "color", "size"]

    cfg["fields_selectors"] = {}
    cfg["fields"] = {}

    for field in field_names:
        sel_val = sel_raw.get(field, [])
        sel_list = _as_list(sel_val)
        cfg["fields_selectors"][field] = sel_list
        cfg["fields"][field] = sel_list[0] if sel_list else ""

    # Features list selector (for <li> bullets)
    features_raw = sel_raw.get("features", [])
    cfg["selectors"]["features_list"] = _first(features_raw)
    cfg["fields_selectors"]["features"] = _as_list(features_raw)

    # -----------------------------------------------------------------------
    # Spec extraction (brand-specific)
    # -----------------------------------------------------------------------
    spec_extraction: Dict[str, Any] = {}
    extraction_type = raw.get("spec_table", {}).get("extraction_type", "")

    if extraction_type == "aria_label_pairs":
        # Tissot: elements with aria-label="KEY VALUE"
        spec_extraction["type"] = "aria_label"
        spec_extraction["row_selector"] = sel_raw.get(
            "specs_row_selector", ".ti-pdp-details-content[aria-label]"
        )
        spec_extraction["label_attr"] = sel_raw.get("specs_label_attr", "aria-label")

    elif extraction_type == "label_value_pairs":
        # Hamilton: separate .attribute-label / .attribute-value elements
        spec_extraction["type"] = "label_value"
        spec_extraction["label_selector"] = sel_raw.get(
            "specs_label_selector", ".attribute-label"
        )
        spec_extraction["value_selector"] = sel_raw.get(
            "specs_value_selector", ".attribute-value"
        )

    else:
        # Template-style: row / key / value CSS selectors
        spec_extraction["type"] = "row"
        spec_extraction["row_selector"] = raw.get("spec_table", {}).get(
            "row_selector", ""
        )
        spec_extraction["key_selector"] = raw.get("spec_table", {}).get(
            "key_selector", ""
        )
        spec_extraction["value_selector"] = raw.get("spec_table", {}).get(
            "value_selector", ""
        )

    cfg["spec_extraction"] = spec_extraction

    # -----------------------------------------------------------------------
    # Spec table rows (for HTML output)
    # -----------------------------------------------------------------------
    cfg["spec_table"] = {
        "header": raw.get("spec_table", {}).get("header", "Item Specification"),
        "rows": raw.get("spec_table", {}).get("rows", []),
    }

    # -----------------------------------------------------------------------
    # Image extraction
    # -----------------------------------------------------------------------
    img_raw = raw.get("image_extraction", raw.get("images", {}))
    img_method = img_raw.get("method", "dom_selector")

    # Normalize method names
    method_map = {
        "dom": "dom_selector",
        "dom_selector": "dom_selector",
        "magento_json": "magento_json",
        "both": "both",
    }
    img_method = method_map.get(img_method, "dom_selector")

    dom_sels = _as_list(
        img_raw.get("dom_selectors") or img_raw.get("selector", "")
    )

    url_cleanup = img_raw.get("url_cleanup", {})
    strip_qs = url_cleanup.get("strip_query_params", img_raw.get("strip_query_params", False))

    cfg["images"] = {
        "method": img_method,
        "selectors": dom_sels,
        "selector": dom_sels[0] if dom_sels else "",
        "attr": img_raw.get("dom_attribute") or img_raw.get("attr", "src"),
        "fallback_attr": img_raw.get("fallback_attr", "data-src"),
        "exclude_keywords": img_raw.get("exclude_keywords", []),
        "strip_query_params": strip_qs,
        "magento_json": img_raw.get("magento_json", False),
        "script_contains": img_raw.get("script_contains", ""),
        "script_pattern": img_raw.get(
            "script_pattern", r'"url":"(https?://[^"]+)"'
        ),
    }

    # -----------------------------------------------------------------------
    # Shopify field mappings
    # -----------------------------------------------------------------------
    shopify_raw = raw.get("shopify", {})

    cfg["shopify_mapping"] = {
        "vendor": {"value": shopify_raw.get("vendor", cfg["brand_name"])},
        "type": {"value": shopify_raw.get("product_type", "")},
        "title": {"source": "title"},
        "description": {"source": "description"},
        "price": {"source": "price"},
        "sku": {"source": "sku"},
        "published": "TRUE",
        "tags": {
            "static": shopify_raw.get("default_tags", []),
        },
    }

    # -----------------------------------------------------------------------
    # Anti-bot / timing
    # -----------------------------------------------------------------------
    delay_min = anti_bot_raw.get("random_delay_min", 2)
    delay_max = anti_bot_raw.get("random_delay_max", 5)
    page_delay = (delay_min + delay_max) / 2  # use average as default

    # first_load_wait: wait after opening a page the first time
    first_load_wait = driver_raw.get("first_load_wait", page_delay)

    cfg["anti_bot"] = {
        "page_delay": page_delay,
        "random_delay_min": delay_min,
        "random_delay_max": delay_max,
        "scroll_pause": scroll_pause,
        "first_load_wait": first_load_wait,
        "image_delay": 0.3,
        "window_size": [1400, 900],
        "page_load_timeout": max(
            60, driver_raw.get("page_load_wait", 10) * 6
        ),
        "slow_scroll": anti_bot_raw.get("slow_scroll", False),
    }

    # -----------------------------------------------------------------------
    # Pre-scrape actions (open accordions, etc.)
    # -----------------------------------------------------------------------
    cfg["pre_scrape_actions"] = raw.get("pre_scrape_actions", [])

    return cfg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_list(val: Any) -> List[str]:
    """Ensure value is a list of non-empty strings."""
    if not val:
        return []
    if isinstance(val, list):
        return [str(v) for v in val if v]
    return [str(val)]


def _first(val: Any) -> str:
    """Return the first item if val is a list, else val itself."""
    items = _as_list(val)
    return items[0] if items else ""
