"""
normalizer.py
Transforms raw scraped product dicts into Shopify-ready dicts.
Builds the Body (HTML) from description + spec table + footer.
Extracts all metafields from features list and scraped fields.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from slugify import slugify

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gender detection — collection-based
# ---------------------------------------------------------------------------

def _detect_gender(source_url: str, url_gender_map: Dict[str, str]) -> str:
    """
    Return 'Men' | 'Women' | 'Unisex' | '' based on which collections
    the product URL appeared in.

    url_gender_map: {normalized_product_url: 'Men'|'Women'|'Unisex'|''}
    built by url_collector from category URL keywords.
    """
    return url_gender_map.get(source_url.rstrip("/"), "")


def _gender_to_shopify(gender: str) -> str:
    mapping = {"Men": "male", "Women": "female", "Unisex": "unisex"}
    return mapping.get(gender, "")


# ---------------------------------------------------------------------------
# Title formatting
# ---------------------------------------------------------------------------

def _build_title(raw_title: str, sku: str, gender: str, brand_name: str) -> str:
    """
    Build a Shopify title between 50-70 chars.
    Pattern: {Brand} {SKU} {cleaned_description} [{gender_hint}] {SKU}
    The SKU is appended at the end if the title is too short.
    """
    title = raw_title.strip() if raw_title else ""

    # If scraped title already looks good (has SKU), use it directly but enforce length
    if title and sku and sku.upper() in title.upper():
        if 50 <= len(title) <= 70:
            return title
        if len(title) > 70:
            # Truncate but preserve SKU at end
            trimmed = title[:67 - len(sku)].rstrip()
            return f"{trimmed} {sku.upper()}"
        # Too short — pad with brand or sku
        if len(title) < 50:
            candidate = f"{title} {sku.upper()}"
            if len(candidate) <= 70:
                return candidate
        return title

    # Compose from parts
    parts = []
    if brand_name and brand_name.lower() not in title.lower():
        parts.append(brand_name)
    parts.append(sku.upper() if sku else "")

    # Add cleaned description (strip leading SKU if already there)
    desc = re.sub(re.escape(sku), "", title, flags=re.IGNORECASE).strip(" -–|") if sku else title
    if desc:
        parts.append(desc)

    # Gender hint for women
    if gender == "Women" and "ladies" not in " ".join(parts).lower() and "women" not in " ".join(parts).lower():
        parts.append("Ladies'")

    # Append SKU at end (Shopify convention)
    if sku and parts[-1].upper() != sku.upper():
        parts.append(sku.upper())

    composed = " ".join(p for p in parts if p)

    # Enforce 50-70 chars
    if len(composed) > 70:
        # Drop middle description, keep Brand SKU ... SKU
        short = f"{brand_name} {sku.upper()} Watch {sku.upper()}"
        if len(short) <= 70:
            return short
        return composed[:70]

    return composed


# ---------------------------------------------------------------------------
# Feature extraction helpers (extract metafields from features list)
# ---------------------------------------------------------------------------

_FEATURE_PATTERNS = {
    "band_material":    [r"band[:\s]+(.+)", r"bracelet[:\s]+(.+)", r"strap[:\s]+(.+)"],
    "case_color":       [r"case[:\s]+(.+)"],
    "dial_color":       [r"dial(?:\s+color)?[:\s]+(.+)"],
    "frame_color":      [r"frame(?:\s+color)?[:\s]+(.+)", r"bezel[:\s]+(.+)"],
    "lug_width":        [r"lug\s+width[:\s]+(.+)", r"(\d+\s*mm)\s+lug"],
    "water_resistance": [r"water\s+resist\w*[:\s]+(.+)", r"(\d+\s*(?:m|bar|atm|feet)\b.*)"],
}

# Also map from specs dict keys
_SPEC_TO_META = {
    "Dial Color":        "dial_color",
    "DIAL COLOR":        "dial_color",
    "Lug Width":         "lug_width",
    "LUG WIDTH":         "lug_width",
    "Water Resistance":  "water_resistance",
    "WATER RESISTANCE":  "water_resistance",
    "Strap":             "band_material",
    "STRAP":             "band_material",
    "Case":              "case_color",
    "CASE":              "case_color",
    "Case Material":     "case_color",
}


def _extract_metafields(raw: dict) -> Dict[str, str]:
    """
    Extract metafield values from features list and specs dict.
    Returns dict keyed by metafield name (band_material, dial_color, etc.)
    """
    meta = {}
    features: List[str] = raw.get("features", [])
    specs: Dict[str, str] = raw.get("specs", {})

    # From specs dict (most reliable)
    for spec_key, meta_key in _SPEC_TO_META.items():
        if spec_key in specs and specs[spec_key] and meta_key not in meta:
            meta[meta_key] = specs[spec_key].strip()

    # From features list (regex matching)
    for feature in features:
        feature_lower = feature.lower()
        for meta_key, patterns in _FEATURE_PATTERNS.items():
            if meta_key in meta:
                continue
            for pattern in patterns:
                m = re.search(pattern, feature_lower)
                if m:
                    # Use original casing from feature string
                    start = m.start(1)
                    end = m.end(1)
                    meta[meta_key] = feature[start:end].strip().rstrip(".,;")
                    break

    return meta


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def normalize_products(
    raw_products: List[Dict[str, Any]],
    config: dict,
    image_map: Dict[str, List[str]],
    brand: str,
    url_gender_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    shopify_mapping = config.get("shopify_mapping", {})
    footer_html = _load_footer()
    normalized = []
    if url_gender_map is None:
        url_gender_map = {}

    for raw in raw_products:
        try:
            product = _normalize_one(raw, config, shopify_mapping, image_map, footer_html, brand, url_gender_map)
            normalized.append(product)
        except Exception as e:
            logger.error(f"  Normalization failed for {raw.get('source_url', '?')}: {e}")

    return normalized


def _normalize_one(raw, config, shopify_mapping, image_map, footer_html, brand, url_gender_map):
    source_url = raw.get("source_url", "")
    brand_name = shopify_mapping.get("vendor", {}).get("value", config.get("brand_name", brand))

    # --- Core scraped fields ---
    title_raw   = _get_mapped(raw, shopify_mapping, "title", "title")
    price       = _clean_price(_get_mapped(raw, shopify_mapping, "price", "price"))
    sku         = _get_mapped(raw, shopify_mapping, "sku", "sku").strip()
    description = _get_mapped(raw, shopify_mapping, "description", "description")

    # --- Gender (collection-based) ---
    gender         = _detect_gender(source_url, url_gender_map)
    gender_shopify = _gender_to_shopify(gender)

    # --- Title (formatted to 50-70 chars) ---
    title = _build_title(title_raw, sku, gender, brand_name)

    # --- Handle: lowercase, hyphens, from title ---
    handle = slugify(title) if title else slugify(source_url.split("/")[-1])

    # --- Tags ---
    tags = _resolve_tags(raw, shopify_mapping.get("tags", {}))

    # --- Body HTML ---
    spec_html  = _build_spec_table(raw, config)
    body_html  = _build_body_html(description, spec_html, footer_html)

    # --- Images ---
    images = image_map.get(source_url, raw.get("images", []))

    # --- Metafields from features + specs ---
    meta = _extract_metafields(raw)

    # --- Image alt text: Title_1, Title_2... ---
    image_alt = title

    return {
        "handle":           handle,
        "title":            title,
        "body_html":        body_html,
        "vendor":           brand_name,
        "product_category": "Apparel & Accessories > Jewelry > Watches",
        "type":             "Wristwatches",
        "tags":             tags,
        "published":        "FALSE",
        "option1_name":     "Title",
        "option1_value":    "Default Title",
        "sku":              sku,
        "price":            price,
        "images":           images,
        "image_alt":        image_alt,
        "source_url":       source_url,
        "gender":           gender,
        "gender_shopify":   gender_shopify,
        # metafields custom
        "meta_band_material":    meta.get("band_material", ""),
        "meta_case_color":       meta.get("case_color", ""),
        "meta_department":       gender,
        "meta_dial_color":       meta.get("dial_color", ""),
        "meta_frame_color":      meta.get("frame_color", ""),
        "meta_lug_width":        meta.get("lug_width", ""),
        "meta_water_resistance": meta.get("water_resistance", ""),
        "meta_source_url":       source_url,
    }


# ---------------------------------------------------------------------------
# Spec table builder
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

            elif row_type in ("label_value", "spec"):
                spec_key = row.get("spec_key", label)
                value = specs.get(spec_key, "") or _lookup_spec_ci(specs, spec_key)
                if label and value:
                    rows_html.append(_table_row(label, value))

            elif row_type == "aria_label":
                aria_key = row.get("aria_key", label)
                value = specs.get(aria_key, "") or _lookup_spec_ci(specs, aria_key)
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

    header = config.get("spec_table", {}).get("header", "Item Specification")
    header_row = (
        f'<tr><td colspan="2" style="padding:8px 10px;border:1px solid #000;'
        f'background:#999;font-weight:bold;text-align:center;font-size:15px;'
        f'color:#000;">{_escape_html(header)}</td></tr>'
    )
    style = (
        'style="width:100%;max-width:800px;border-collapse:collapse;'
        'font-family:Arial,sans-serif;font-size:13px;border:1px solid #000;"'
    )
    return f'<table {style}><tbody>{header_row}{"".join(rows_html)}</tbody></table>'


def _lookup_spec_ci(specs: dict, key: str) -> str:
    key_lower = key.lower()
    for k, v in specs.items():
        if k.lower() == key_lower:
            return v
    return ""


def _table_row(label: str, value: str, raw_value: bool = False) -> str:
    cell_style  = 'style="padding:5px 8px;border:1px solid #000;vertical-align:middle;"'
    label_style = 'style="width:25%;min-width:110px;padding:5px 8px;border:1px solid #000;font-weight:bold;background:#f2f2f2;vertical-align:middle;"'
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
