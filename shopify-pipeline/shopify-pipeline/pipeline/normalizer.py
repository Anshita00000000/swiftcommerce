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
            # This catches the 'str' object has no attribute 'get' error
            logger.error(f"  Normalization failed for {raw.get('source_url', '?')}: {e}")

    return normalized

def _normalize_one(raw, config, shopify_mapping, image_map, footer_html, brand, url_data_map):
    source_url = raw.get("source_url", "")
    canonical_url = _canonical_product_url(source_url)

    # --- FIX: Handle vendor mapping correctly (check if it's a dict or string) ---
    vendor_cfg = shopify_mapping.get("vendor", brand)
    if isinstance(vendor_cfg, dict):
        brand_name = vendor_cfg.get("value", config.get("brand_name", brand))
    else:
        brand_name = str(vendor_cfg)

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
    
    # --- Metafields ---
    band_material    = _spec(specs, "Strap", "Band Material", "Strap Material")
    case_color       = _spec(specs, "Case Material", "Case color")
    dial_color       = _spec(specs, "Dial Color", "Dial color")
    water_resistance = _spec(specs, "Water Resistance", "Water-resistance")

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
        if gt: tags = f"{tags}, {gt}" if tags else gt
    elif gender == "Women":
        gt = gender_tags.get("womens", "")
        if gt: tags = f"{tags}, {gt}" if tags else gt

    # --- Type & Category ---
    product_type = _resolve_mapping(raw, shopify_mapping.get("type", {})) or "Watch"
    if not product_type or product_type == "None":
         product_type = shopify_mapping.get("product_type", "Watch")
         
    product_category = shopify_mapping.get("product_category", "")

    return {
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
        "metafield_band_material":    band_material,
        "metafield_case_color":       case_color,
        "metafield_department":       gender,
        "metafield_dial_color":       dial_color,
        "metafield_water_resistance": water_resistance,
        "metafield_target_gender":    gender_shopify,
    }

# ---------------------------------------------------------------------------
# Helper functions (Keep these as they were)
# ---------------------------------------------------------------------------

def _canonical_product_url(url: str) -> str:
    cleaned = re.sub(r'/collections/[^/]+(/products/)', r'\1', url)
    return cleaned

def _normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.startswith("//"): url = "https:" + url
    elif url.startswith("http://"): url = "https://" + url[7:]
    return url

def _format_title(title_raw: str, sku: str, brand: str) -> str:
    if not title_raw: return f"{brand} {sku}".strip() if sku else sku
    bad_patterns = ["on your wrist", "try on", "virtual try"]
    if any(p in title_raw.lower() for p in bad_patterns):
        return f"{brand} {sku}".strip() if sku else sku
    return title_raw

def _gender_to_shopify(gender: str) -> str:
    return {"Men": "male", "Women": "female", "Unisex": "unisex"}.get(gender, "")

def _spec(specs: dict, *keys: str) -> str:
    for key in keys:
        val = specs.get(key, "")
        if not val:
            key_lower = key.lower()
            for k, v in specs.items():
                if k.lower() == key_lower:
                    val = v
                    break
        if val: return val
    return ""

def _build_spec_table(raw: dict, config: dict) -> str:
    rows_cfg = config.get("spec_table", {}).get("rows", [])
    if not rows_cfg: return ""
    specs = raw.get("specs", {})
    features = raw.get("features", [])
    rows_html = []
    for row in rows_cfg:
        label = row.get("label", "")
        rtype = row.get("type", "static")
        val = ""
        if rtype == "static": val = row.get("value", "")
        elif rtype == "scraped": val = raw.get(row.get("field", ""), "")
        elif rtype in ("spec", "spec_accordion"):
            s_key = row.get("spec_key", label)
            val = specs.get(s_key, "")
        if label and val:
            rows_html.append(f'<tr><td style="font-weight:bold; border:1px solid #ddd; padding:8px;">{label}</td><td style="border:1px solid #ddd; padding:8px;">{val}</td></tr>')
    return f'<table style="width:100%; border-collapse:collapse;">{ "".join(rows_html) }</table>'

def _build_body_html(description: str, spec_html: str, footer_html: str) -> str:
    return f"{description}<br><br>{spec_html}<br>{footer_html}"

def _load_footer() -> str:
    p = Path("config/footer.html")
    return p.read_text(encoding="utf-8") if p.exists() else ""

def _get_mapped(raw: dict, mapping: dict, mapping_key: str, raw_key: str) -> str:
    cfg = mapping.get(mapping_key, {})
    if isinstance(cfg, dict):
        return _resolve_mapping(raw, cfg) or raw.get(raw_key, "")
    return raw.get(raw_key, "")

def _resolve_mapping(raw: dict, cfg: dict) -> str:
    if "value" in cfg: return str(cfg["value"])
    if "source" in cfg: return str(raw.get(cfg["source"], ""))
    return ""

def _resolve_tags(raw: dict, tags_cfg) -> str:
    if isinstance(tags_cfg, list): return ", ".join(tags_cfg)
    if isinstance(tags_cfg, dict): return ", ".join(tags_cfg.get("static", []))
    return str(tags_cfg)

def _clean_price(price: str) -> str:
    if not price: return ""
    return "".join(c for c in price if c.isdigit() or c in ".,").replace(",", ".")