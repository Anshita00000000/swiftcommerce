"""
normalizer.py
Transforms raw scraped product dicts into Shopify-ready dicts.
Builds the Body (HTML) from description + spec table + footer.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List

from slugify import slugify

logger = logging.getLogger(__name__)


def normalize_products(
    raw_products: List[Dict[str, Any]],
    config: dict,
    image_map: Dict[str, List[str]],
    brand: str,
) -> List[Dict[str, Any]]:
    shopify_mapping = config.get("shopify_mapping", {})
    footer_html = _load_footer()
    normalized = []

    for raw in raw_products:
        try:
            product = _normalize_one(raw, config, shopify_mapping, image_map, footer_html, brand)
            normalized.append(product)
        except Exception as e:
            logger.error(f"  Normalization failed for {raw.get('source_url', '?')}: {e}")

    return normalized


def _normalize_one(raw, config, shopify_mapping, image_map, footer_html, brand):
    source_url = raw.get("source_url", "")

    title_raw = _get_mapped(raw, shopify_mapping, "title", "title")
    handle = slugify(title_raw) if title_raw else slugify(source_url.split("/")[-1])

    vendor = shopify_mapping.get("vendor", {}).get("value", config.get("brand_name", brand))
    product_type = _resolve_mapping(raw, shopify_mapping.get("type", {}))
    tags = _resolve_tags(raw, shopify_mapping.get("tags", {}))
    price = _clean_price(_get_mapped(raw, shopify_mapping, "price", "price"))
    sku = _get_mapped(raw, shopify_mapping, "sku", "sku")

    description = _get_mapped(raw, shopify_mapping, "description", "description")
    spec_html = _build_spec_table(raw, config)
    body_html = _build_body_html(description, spec_html, footer_html)

    image_paths = image_map.get(source_url, raw.get("images", []))

    return {
        "handle": handle,
        "title": title_raw,
        "body_html": body_html,
        "vendor": vendor,
        "type": product_type,
        "tags": tags,
        "published": shopify_mapping.get("published", "TRUE"),
        "option1_name": "Title",
        "option1_value": "Default Title",
        "sku": sku,
        "price": price,
        "images": image_paths,
        "source_url": source_url,
    }


# ---------------------------------------------------------------------------
# Spec table builder
# ---------------------------------------------------------------------------

def _build_spec_table(raw: dict, config: dict) -> str:
    """
    Build a styled HTML table from spec_table.rows in config.

    Row types:
      static       — constant value from config
      scraped      — value from a scraped field
      label_value  — from raw["specs"] by spec_key
      aria_label   — from raw["specs"] by aria_key
      features_list — renders as <ul>
    """
    rows_cfg = config.get("spec_table", {}).get("rows", [])
    if not rows_cfg:
        return ""

    specs = raw.get("specs", {})
    features = raw.get("features", [])
    rows_html = []

    for row in rows_cfg:
        row_type = row.get("type", "static")
        label = row.get("label", "")

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

            elif row_type == "label_value":
                # Hamilton: look up by spec_key in raw["specs"]
                spec_key = row.get("spec_key", label)
                value = specs.get(spec_key, "")
                if not value:
                    # Try case-insensitive match
                    value = _lookup_spec_ci(specs, spec_key)
                if label and value:
                    rows_html.append(_table_row(label, value))

            elif row_type == "aria_label":
                # Tissot: look up by aria_key in raw["specs"]
                aria_key = row.get("aria_key", label)
                value = specs.get(aria_key, "")
                if not value:
                    value = _lookup_spec_ci(specs, aria_key)
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

    style = 'style="width:100%;border-collapse:collapse;font-family:sans-serif;font-size:14px;"'
    return f'<table {style}><tbody>{"".join(rows_html)}</tbody></table>'


def _lookup_spec_ci(specs: dict, key: str) -> str:
    """Case-insensitive lookup in specs dict."""
    key_lower = key.lower()
    for k, v in specs.items():
        if k.lower() == key_lower:
            return v
    return ""


def _table_row(label: str, value: str, raw_value: bool = False) -> str:
    cell_style = 'style="padding:6px 10px;border:1px solid #ddd;vertical-align:top;"'
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
