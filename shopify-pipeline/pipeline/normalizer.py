"""
normalizer.py
Transforms raw scraped product dicts into Shopify-ready dicts.
Builds Body (HTML), metafields, gender, title formatting.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from slugify import slugify

logger = logging.getLogger(__name__)


def normalize_products(
    raw_products: List[Dict[str, Any]],
    config: dict,
    image_map: Dict[str, List[str]],
    brand: str,
    url_data_map: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    shopify_mapping = config.get("shopify_mapping", {})
    footer_html = _load_footer()
    normalized = []
    if url_data_map is None:
        url_data_map = {}

    for raw in raw_products:
        try:
            product = _normalize_one(
                raw, config, shopify_mapping, image_map, footer_html, brand, url_data_map
            )
            normalized.append(product)
        except Exception as e:
            logger.error(f"  Normalization failed for {raw.get('source_url', '?')}: {e}")

    return normalized


def _normalize_one(raw, config, shopify_mapping, image_map, footer_html, brand, url_data_map):
    source_url = raw.get("source_url", "")

    # Canonical product URL — strip /collections/xxx/ prefix if present
    # e.g. /collections/mens/products/ssc941 -> /products/ssc941
    canonical_url = _canonical_product_url(source_url)

    brand_name = shopify_mapping.get("vendor", {}).get("value", config.get("brand_name", brand))

    # --- Core scraped fields ---
    title_raw   = _get_mapped(raw, shopify_mapping, "title", "title")
    price       = _clean_price(_get_mapped(raw, shopify_mapping, "price", "price"))
    sku         = _get_mapped(raw, shopify_mapping, "sku", "sku").strip()
    description = _get_mapped(raw, shopify_mapping, "description", "description")

    # --- Title formatting ---
    title = _format_title(title_raw, sku, brand_name)
    handle = slugify(title) if title else slugify(sku or source_url.split("/")[-1])

    # --- Gender (collection-based) ---
    norm_url = _normalize_url(source_url)
    gender         = url_data_map.get(norm_url, {}).get("gender", "")
    gender_shopify = _gender_to_shopify(gender)

    # --- Specs extraction ---
    specs    = raw.get("specs", {})
    features = raw.get("features", [])

    # --- Metafields ---
    band_material    = _spec(specs, "Strap", "Band Material")
    case_color       = _spec(specs, "Case Material")
    dial_color       = _spec(specs, "Dial Color")
    frame_color      = _spec(specs, "Case Material")   # same source as case
    lug_width        = _spec(specs, "Lug Width")
    water_resistance = _spec(specs, "Water Resistance")

    # Band color / Case color / Dial color for shopify.* metafields
    band_color = band_material
    case_color_shopify = case_color
    dial_color_shopify = dial_color

    # --- Body HTML ---
    spec_html = _build_spec_table(raw, config)
    body_html = _build_body_html(description, spec_html, footer_html)

    # --- Images ---
    image_paths = image_map.get(source_url, raw.get("images", []))

    # --- Tags ---
    tags_cfg = shopify_mapping.get("tags", config.get("default_tags", []))
    tags = _resolve_tags(raw, tags_cfg)

    # --- Gender tags ---
    gender_tags = shopify_mapping.get("gender_tags", {})
    if gender == "Men":
        gt = gender_tags.get("mens", "")
        if gt:
            tags = f"{tags}, {gt}" if tags else gt
    elif gender == "Women":
        gt = gender_tags.get("womens", "")
        if gt:
            tags = f"{tags}, {gt}" if tags else gt
    elif gender == "Unisex":
        gt = gender_tags.get("unisex", [])
        if isinstance(gt, list):
            for item in gt:
                if item:
                    tags = f"{tags}, {item}" if tags else item
        elif gt:
            tags = f"{tags}, {gt}" if tags else gt

    product_type = _resolve_mapping(raw, shopify_mapping.get("type", {})) or "Watch"
    product_category = config.get("shopify_mapping", {}).get("product_category", "")

    return {
        # Core
        "handle":        handle,
        "title":         title,
        "body_html":     body_html,
        "vendor":        brand_name,
        "type":          product_type,
        "product_category": product_category,
        "tags":          tags,
        "published":     "FALSE",
        "option1_name":  "Title",
        "option1_value": "Default Title",
        "sku":           sku,
        "price":         price,
        "images":        image_paths,
        "source_url":    canonical_url,
        # Metafields
        "metafield_band_material":    band_material,
        "metafield_case_color":       case_color,
        "metafield_department":       gender,          # Men / Women / Unisex / ""
        "metafield_dial_color":       dial_color,
        "metafield_frame_color":      frame_color,
        "metafield_lug_width":        lug_width,
        "metafield_water_resistance": water_resistance,
        "metafield_target_gender":    gender_shopify,  # male / female / unisex / ""
        "metafield_band_color":       band_color,
        "metafield_case_color_shopify": case_color_shopify,
        "metafield_dial_color_shopify": dial_color_shopify,
    }


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _canonical_product_url(url: str) -> str:
    """Strip /collections/xxx from collection-scoped product URLs."""
    # e.g. https://seikousa.com/collections/mens/products/ssc941
    #   -> https://seikousa.com/products/ssc941
    cleaned = re.sub(r'/collections/[^/]+(/products/)', r'\1', url)
    return cleaned


def _normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[7:]
    return url


# ---------------------------------------------------------------------------
# Title formatting
# ---------------------------------------------------------------------------

def _format_title(title_raw: str, sku: str, brand: str) -> str:
    """
    Target: clean title that includes the SKU.
    - If title contains 'on your wrist' or similar drawer text, replace with SKU
    - If title already looks like a proper product title, use as-is
    - Otherwise build: {Brand} {SKU}
    """
    if not title_raw:
        return f"{brand} {sku}".strip() if sku else sku

    bad_patterns = ["on your wrist", "try on", "virtual try"]
    if any(p in title_raw.lower() for p in bad_patterns):
        # Scraper picked up the wrong element — fall back to SKU-based title
        return f"{brand} {sku}".strip() if sku else sku

    return title_raw


# ---------------------------------------------------------------------------
# Gender helpers
# ---------------------------------------------------------------------------

def _gender_to_shopify(gender: str) -> str:
    return {"Men": "male", "Women": "female", "Unisex": "unisex"}.get(gender, "")


# ---------------------------------------------------------------------------
# Spec helpers
# ---------------------------------------------------------------------------

def _spec(specs: dict, *keys: str) -> str:
    """Look up first matching key (case-insensitive) from specs dict."""
    for key in keys:
        val = specs.get(key, "")
        if not val:
            val = _lookup_spec_ci(specs, key)
        if val:
            return val
    return ""


def _lookup_spec_ci(specs: dict, key: str) -> str:
    key_lower = key.lower()
    for k, v in specs.items():
        if k.lower() == key_lower:
            return v
    return ""


# ---------------------------------------------------------------------------
# Spec table (Body HTML)
# ---------------------------------------------------------------------------

def _build_spec_table(raw: dict, config: dict) -> str:
    rows_cfg = config.get("spec_table", {}).get("rows", [])
    if not rows_cfg:
        return ""

    specs    = raw.get("specs", {})
    features = raw.get("features", [])
    rows_html = []

    for row in rows_cfg:
        row_type = row.get("type", "static")
        label    = row.get("label", "")

        try:
            if row_type == "static":
                value = row.get("value", "")
                if label and value:
                    rows_html.append(_table_row(label, value))

            elif row_type == "scraped":
                field = row.get("field", "")
                value = raw.get(field, "")
                if label and value:
                    rows_html.append(_table_row(label, value))

            elif row_type == "spec_accordion":
                spec_key = row.get("spec_key") or label
                value = specs.get(spec_key, "") or _lookup_spec_ci(specs, spec_key)
                if label and value:
                    rows_html.append(_table_row(label, value))

            elif row_type in ("spec", "label_value", "aria_label"):
                spec_key = row.get("spec_key") or row.get("aria_key") or label
                value = specs.get(spec_key, "") or _lookup_spec_ci(specs, spec_key)
                if label and value:
                    rows_html.append(_table_row(label, value))

            elif row_type == "features_list":
                if features:
                    items_html = "".join(f"<li>{_escape_html(f)}</li>" for f in features)
                    rows_html.append(_table_row(label, f"<ul>{items_html}</ul>", raw_value=True))

        except Exception as e:
            logger.warning(f"  [spec_table] Row '{label}' failed: {e}")

    if not rows_html:
        return ""

    style = 'style="width:100%;max-width:800px;border-collapse:collapse;font-family:Arial,sans-serif;font-size:14px;"'
    return f'<table {style}><tbody>{"".join(rows_html)}</tbody></table>'


def _table_row(label: str, value: str, raw_value: bool = False) -> str:
    cell_style  = 'style="padding:6px 10px;border:1px solid #ddd;vertical-align:top;"'
    label_style = 'style="padding:6px 10px;border:1px solid #ddd;font-weight:bold;background:#f5f5f5;width:35%;vertical-align:top;"'
    val_cell = value if raw_value else _escape_html(value)
    return f"<tr><td {label_style}>{_escape_html(label)}</td><td {cell_style}>{val_cell}</td></tr>"


def _escape_html(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# Body HTML
# ---------------------------------------------------------------------------

def _build_body_html(description: str, spec_html: str, footer_html: str) -> str:
    parts = []
    if description:
        parts.append(f"<div class='product-description'>{description}</div>")
    if spec_html:
        parts.append(f"<div class='product-specs'>{spec_html}</div>")
    if footer_html:
        parts.append(footer_html)
    return "\n".join(parts)


def _load_footer() -> str:
    footer_path = Path("config") / "footer.html"
    if footer_path.exists():
        try:
            return footer_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"  Could not read footer.html: {e}")
    return ""


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------

def _get_mapped(raw: dict, mapping: dict, mapping_key: str, raw_key: str) -> str:
    cfg = mapping.get(mapping_key, {})
    if isinstance(cfg, dict):
        return _resolve_mapping(raw, cfg) or raw.get(raw_key, "")
    return raw.get(raw_key, "")


def _resolve_mapping(raw: dict, cfg: dict) -> str:
    if not cfg:
        return ""
    if "value" in cfg:
        return str(cfg["value"])
    if "source" in cfg:
        return str(raw.get(cfg["source"], ""))
    return ""


def _resolve_tags(raw: dict, tags_cfg) -> str:
    if isinstance(tags_cfg, dict):
        static = tags_cfg.get("static", [])
        source_field = tags_cfg.get("source")
        tags = list(static)
        if source_field and raw.get(source_field):
            tags.append(raw[source_field])
        return ", ".join(str(t) for t in tags if t)
    if isinstance(tags_cfg, list):
        return ", ".join(str(t) for t in tags_cfg)
    return str(tags_cfg) if tags_cfg else ""


def _clean_price(price: str) -> str:
    if not price:
        return ""
    cleaned = "".join(c for c in price if c.isdigit() or c in ".,")
    if cleaned.count(",") == 1 and cleaned.count(".") == 0:
        cleaned = cleaned.replace(",", ".")
    elif cleaned.count(",") >= 1:
        cleaned = cleaned.replace(",", "")
    return cleaned
