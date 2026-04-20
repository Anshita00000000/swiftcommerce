"""
exporter.py — 86-column Shopify CSV
Column names match shopify_temp.xlsx exactly.
Data is read from normalizer's product dict keys (e.g. metafield_band_material).
"""

import csv
import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Exact 86-column order from shopify_temp.xlsx
SHOPIFY_COLUMNS = [
    "Handle",
    "Title",
    "Body (HTML)",
    "Vendor",
    "Product Category",
    "Type",
    "Tags",
    "Published",
    "Option1 Name",
    "Option1 Value",
    "Option1 Linked To",
    "Option2 Name",
    "Option2 Value",
    "Option2 Linked To",
    "Option3 Name",
    "Option3 Value",
    "Option3 Linked To",
    "Variant SKU",
    "Variant Grams",
    "Variant Inventory Tracker",
    "Variant Inventory Policy",
    "Variant Fulfillment Service",
    "Variant Price",
    "Variant Compare At Price",
    "Variant Requires Shipping",
    "Variant Taxable",
    "Unit Price Total Measure",
    "Unit Price Total Measure Unit",
    "Unit Price Base Measure",
    "Unit Price Base Measure Unit",
    "Variant Barcode",
    "Image Src",
    "Image Position",
    "Image Alt Text",
    "Gift Card",
    "SEO Title",
    "SEO Description",
    "Band Material (product.metafields.custom.band_material)",
    "Case Color (product.metafields.custom.case_color)",
    "Department (product.metafields.custom.department)",
    "Dial Color (product.metafields.custom.dial_color)",
    "Display (product.metafields.custom.display)",
    "Frame Color (product.metafields.custom.frame_color)",
    "Increased Price (product.metafields.custom.increased_price)",
    "Lug Width (product.metafields.custom.lug_width)",
    "Product Source URL (product.metafields.custom.product_source_url)",
    "Water Resistance (product.metafields.custom.water_resistance)",
    "Google: Custom Product (product.metafields.mm-google-shopping.custom_product)",
    "Product rating count (product.metafields.reviews.rating_count)",
    "Age group (product.metafields.shopify.age-group)",
    "Band color (product.metafields.shopify.band-color)",
    "Barware material (product.metafields.shopify.barware-material)",
    "Case color (product.metafields.shopify.case-color)",
    "Clothing accessory material (product.metafields.shopify.clothing-accessory-material)",
    "Color (product.metafields.shopify.color-pattern)",
    "Connectivity technology (product.metafields.shopify.connectivity-technology)",
    "Dial color (product.metafields.shopify.dial-color)",
    "Dispenser type (product.metafields.shopify.dispenser-type)",
    "Eyewear frame design (product.metafields.shopify.eyewear-frame-design)",
    "Eyewear frame material (product.metafields.shopify.eyewear-frame-material)",
    "Eyewear lens material (product.metafields.shopify.eyewear-lens-material)",
    "Fabric (product.metafields.shopify.fabric)",
    "Hardware material (product.metafields.shopify.hardware-material)",
    "Knife type (product.metafields.shopify.knife-type)",
    "Lens polarization (product.metafields.shopify.lens-polarization)",
    "Lens type (product.metafields.shopify.lens-type)",
    "Lock type (product.metafields.shopify.lock-type)",
    "Material (product.metafields.shopify.material)",
    "Occasion (product.metafields.shopify.occasion)",
    "Optical frame design (product.metafields.shopify.optical-frame-design)",
    "Season (product.metafields.shopify.season)",
    "Target gender (product.metafields.shopify.target-gender)",
    "Wallet features (product.metafields.shopify.wallet-features)",
    "Watch accessory style (product.metafields.shopify.watch-accessory-style)",
    "Watch display (product.metafields.shopify.watch-display)",
    "Watch features (product.metafields.shopify.watch-features)",
    "Watch material (product.metafields.shopify.watch-material)",
    "Complementary products (product.metafields.shopify--discovery--product_recommendation.complementary_products)",
    "Related products (product.metafields.shopify--discovery--product_recommendation.related_products)",
    "Related products settings (product.metafields.shopify--discovery--product_recommendation.related_products_display)",
    "Search product boosts (product.metafields.shopify--discovery--product_search_boost.queries)",
    "Variant Image",
    "Variant Weight Unit",
    "Variant Tax Code",
    "Cost per item",
    "Status",
]


def export_csv(products: List[Dict[str, Any]], output_path: str) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for product in products:
        rows.extend(_product_to_rows(product))
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SHOPIFY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    logger.info(f"Exported {len(products)} products ({len(rows)} rows) → {output_path}")


def _product_to_rows(product: dict) -> List[Dict[str, str]]:
    handle     = product.get("handle", "")
    images     = _resolve_images(product.get("images", []))
    title      = product.get("title", "")
    source_url = product.get("source_url", "")

    primary = {
        # ── Core ──────────────────────────────────────────────────────────
        "Handle":                        handle,
        "Title":                         title,
        "Body (HTML)":                   product.get("body_html", ""),
        "Vendor":                        product.get("vendor", ""),
        "Product Category":              product.get("product_category", ""),
        "Type":                          product.get("type", ""),
        "Tags":                          product.get("tags", ""),
        "Published":                     "FALSE",
        # ── Options ───────────────────────────────────────────────────────
        "Option1 Name":                  "Title",
        "Option1 Value":                 "Default Title",
        "Option1 Linked To":             "",
        "Option2 Name":                  "",
        "Option2 Value":                 "",
        "Option2 Linked To":             "",
        "Option3 Name":                  "",
        "Option3 Value":                 "",
        "Option3 Linked To":             "",
        # ── Variant ───────────────────────────────────────────────────────
        "Variant SKU":                   product.get("sku", ""),
        "Variant Grams":                 "90",
        "Variant Inventory Tracker":     "shopify",
        "Variant Inventory Policy":      "deny",
        "Variant Fulfillment Service":   "manual",
        "Variant Price":                 product.get("price", ""),
        "Variant Compare At Price":      "",
        "Variant Requires Shipping":     "TRUE",
        "Variant Taxable":               "TRUE",
        "Unit Price Total Measure":      "",
        "Unit Price Total Measure Unit": "",
        "Unit Price Base Measure":       "",
        "Unit Price Base Measure Unit":  "",
        "Variant Barcode":               "",
        # ── Image ─────────────────────────────────────────────────────────
        "Image Src":                     images[0] if images else "",
        "Image Position":                "1"          if images else "",
        "Image Alt Text":                f"{title}_1" if images else "",
        # ── Other ─────────────────────────────────────────────────────────
        "Gift Card":                     "FALSE",
        "SEO Title":                     "",
        "SEO Description":               "",
        # ── Custom metafields (display-name format) ───────────────────────
        "Band Material (product.metafields.custom.band_material)":            product.get("metafield_band_material", ""),
        "Case Color (product.metafields.custom.case_color)":                  product.get("metafield_case_color", ""),
        "Department (product.metafields.custom.department)":                  product.get("metafield_department", ""),
        "Dial Color (product.metafields.custom.dial_color)":                  product.get("metafield_dial_color", ""),
        "Display (product.metafields.custom.display)":                        "",
        "Frame Color (product.metafields.custom.frame_color)":                product.get("metafield_frame_color", ""),
        "Increased Price (product.metafields.custom.increased_price)":        "",
        "Lug Width (product.metafields.custom.lug_width)":                    product.get("metafield_lug_width", ""),
        "Product Source URL (product.metafields.custom.product_source_url)":  source_url,
        "Water Resistance (product.metafields.custom.water_resistance)":      product.get("metafield_water_resistance", ""),
        # ── Shopify standard metafields ───────────────────────────────────
        "Google: Custom Product (product.metafields.mm-google-shopping.custom_product)": "",
        "Product rating count (product.metafields.reviews.rating_count)":     "",
        "Age group (product.metafields.shopify.age-group)":                   "adults",
        "Band color (product.metafields.shopify.band-color)":                 product.get("metafield_band_color", ""),
        "Barware material (product.metafields.shopify.barware-material)":     "",
        "Case color (product.metafields.shopify.case-color)":                 product.get("metafield_case_color_shopify", ""),
        "Clothing accessory material (product.metafields.shopify.clothing-accessory-material)": "",
        "Color (product.metafields.shopify.color-pattern)":                   "",
        "Connectivity technology (product.metafields.shopify.connectivity-technology)": "",
        "Dial color (product.metafields.shopify.dial-color)":                 product.get("metafield_dial_color_shopify", ""),
        "Dispenser type (product.metafields.shopify.dispenser-type)":         "",
        "Eyewear frame design (product.metafields.shopify.eyewear-frame-design)": "",
        "Eyewear frame material (product.metafields.shopify.eyewear-frame-material)": "",
        "Eyewear lens material (product.metafields.shopify.eyewear-lens-material)": "",
        "Fabric (product.metafields.shopify.fabric)":                         "",
        "Hardware material (product.metafields.shopify.hardware-material)":   "",
        "Knife type (product.metafields.shopify.knife-type)":                 "",
        "Lens polarization (product.metafields.shopify.lens-polarization)":   "",
        "Lens type (product.metafields.shopify.lens-type)":                   "",
        "Lock type (product.metafields.shopify.lock-type)":                   "",
        "Material (product.metafields.shopify.material)":                     "",
        "Occasion (product.metafields.shopify.occasion)":                     "",
        "Optical frame design (product.metafields.shopify.optical-frame-design)": "",
        "Season (product.metafields.shopify.season)":                         "",
        "Target gender (product.metafields.shopify.target-gender)":           product.get("metafield_target_gender", ""),
        "Wallet features (product.metafields.shopify.wallet-features)":       "",
        "Watch accessory style (product.metafields.shopify.watch-accessory-style)": "",
        "Watch display (product.metafields.shopify.watch-display)":           "",
        "Watch features (product.metafields.shopify.watch-features)":         "",
        "Watch material (product.metafields.shopify.watch-material)":         "",
        "Complementary products (product.metafields.shopify--discovery--product_recommendation.complementary_products)": "",
        "Related products (product.metafields.shopify--discovery--product_recommendation.related_products)": "",
        "Related products settings (product.metafields.shopify--discovery--product_recommendation.related_products_display)": "",
        "Search product boosts (product.metafields.shopify--discovery--product_search_boost.queries)": "",
        # ── Variant tail ──────────────────────────────────────────────────
        "Variant Image":       "",
        "Variant Weight Unit": "g",
        "Variant Tax Code":    "",
        "Cost per item":       "",
        "Status":              "draft",
    }

    rows = [primary]

    # Extra image rows — only Handle + image columns
    for i, img_src in enumerate(images[1:], start=2):
        rows.append({
            "Handle":         handle,
            "Image Src":      img_src,
            "Image Position": str(i),
            "Image Alt Text": f"{title}_{i}",
        })

    return rows


def _resolve_images(raw_images: list) -> list:
    """
    For each image path, prefer the processed webp file in the processed/
    subfolder.  Fall back to the original path (or raw URL) when the
    processed file doesn't exist — e.g. when --skip-images was used.
    """
    resolved = []
    for img in raw_images:
        img_str = str(img).strip()
        if not img_str:
            continue
        if not img_str.startswith(("http://", "https://")):
            p = Path(img_str)
            processed = p.parent / "processed" / (p.stem + ".webp")
            if processed.exists():
                resolved.append(str(processed))
                continue
        resolved.append(img_str)
    return resolved
