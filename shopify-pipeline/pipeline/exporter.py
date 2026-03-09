"""
exporter.py
Writes normalized products to a Shopify-compatible CSV.
Full 85-column template matching the demo seiko_shopify.csv.
Multi-image products generate one row per image (continuation rows have
only Handle + Image Src + Image Position filled).
"""

import csv
import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Full Shopify import column set
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
    "Option2 Name",
    "Option2 Value",
    "Option3 Name",
    "Option3 Value",
    "Variant SKU",
    "Variant Grams",
    "Variant Inventory Tracker",
    "Variant Inventory Qty",
    "Variant Inventory Policy",
    "Variant Fulfillment Service",
    "Variant Price",
    "Variant Compare At Price",
    "Variant Requires Shipping",
    "Variant Taxable",
    "Variant Barcode",
    "Image Src",
    "Image Position",
    "Image Alt Text",
    "Gift Card",
    "SEO Title",
    "SEO Description",
    "Google Shopping / Google Product Category",
    "Google Shopping / Gender",
    "Google Shopping / Age Group",
    "Google Shopping / MPN",
    "Google Shopping / AdWords Grouping",
    "Google Shopping / AdWords Labels",
    "Google Shopping / Condition",
    "Google Shopping / Custom Product",
    "Google Shopping / Custom Label 0",
    "Google Shopping / Custom Label 1",
    "Google Shopping / Custom Label 2",
    "Google Shopping / Custom Label 3",
    "Google Shopping / Custom Label 4",
    "Variant Image",
    "Variant Weight Unit",
    "Variant Tax Code",
    "Cost per item",
    "Included / India",
    "Price / India",
    "Compare At Price / India",
    "Status",
    "product.metafields.custom.band_material",
    "product.metafields.custom.case_color",
    "product.metafields.custom.department",
    "product.metafields.custom.dial_color",
    "product.metafields.custom.frame_color",
    "product.metafields.custom.lug_width",
    "product.metafields.custom.product_source_url",
    "product.metafields.custom.water_resistance",
    "product.metafields.shopify.target-gender",
    "variant.metafields.custom.increased_price",
    "variant.metafields.custom.display",
    "Variant Metafield: custom.source_url",
    # Shopify taxonomy metafields
    "product.metafields.shopify.band-color",
    "product.metafields.shopify.case-color",
    "product.metafields.shopify.dial-color",
    "Age group",
]


def export_csv(
    products: List[Dict[str, Any]],
    output_path: str,
) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for product in products:
        rows.extend(_product_to_rows(product))

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SHOPIFY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"Exported {len(products)} products ({len(rows)} rows) to {output_path}")


def _product_to_rows(product: dict) -> List[Dict[str, str]]:
    handle  = product.get("handle", "")
    images  = product.get("images", [])
    title   = product.get("title", "")
    sku     = product.get("sku", "")

    source_url = product.get("source_url", "")

    # --- Primary row ---
    primary = {
        "Handle":                    handle,
        "Title":                     title,
        "Body (HTML)":               product.get("body_html", ""),
        "Vendor":                    product.get("vendor", ""),
        "Product Category":          "",
        "Type":                      product.get("type", ""),
        "Tags":                      product.get("tags", ""),
        "Published":                 "FALSE",
        "Option1 Name":              "Title",
        "Option1 Value":             "Default Title",
        "Option2 Name":              "",
        "Option2 Value":             "",
        "Option3 Name":              "",
        "Option3 Value":             "",
        "Variant SKU":               sku,
        "Variant Grams":             "90",
        "Variant Inventory Tracker": "shopify",
        "Variant Inventory Qty":     "",
        "Variant Inventory Policy":  "deny",
        "Variant Fulfillment Service": "manual",
        "Variant Price":             product.get("price", ""),
        "Variant Compare At Price":  "",
        "Variant Requires Shipping": "TRUE",
        "Variant Taxable":           "TRUE",
        "Variant Barcode":           "",
        "Image Src":                 images[0] if images else "",
        "Image Position":            "1" if images else "",
        "Image Alt Text":            f"{title}_1" if images else "",
        "Gift Card":                 "FALSE",
        "SEO Title":                 "",
        "SEO Description":           "",
        "Google Shopping / Google Product Category": "",
        "Google Shopping / Gender":  "",
        "Google Shopping / Age Group": "",
        "Google Shopping / MPN":     "",
        "Google Shopping / AdWords Grouping": "",
        "Google Shopping / AdWords Labels": "",
        "Google Shopping / Condition": "",
        "Google Shopping / Custom Product": "",
        "Google Shopping / Custom Label 0": "",
        "Google Shopping / Custom Label 1": "",
        "Google Shopping / Custom Label 2": "",
        "Google Shopping / Custom Label 3": "",
        "Google Shopping / Custom Label 4": "",
        "Variant Image":             "",
        "Variant Weight Unit":       "g",
        "Variant Tax Code":          "",
        "Cost per item":             "",
        "Included / India":          "",
        "Price / India":             "",
        "Compare At Price / India":  "",
        "Status":                    "draft",
        # Custom metafields
        "product.metafields.custom.band_material":    product.get("metafield_band_material", ""),
        "product.metafields.custom.case_color":       product.get("metafield_case_color", ""),
        "product.metafields.custom.department":       product.get("metafield_department", ""),
        "product.metafields.custom.dial_color":       product.get("metafield_dial_color", ""),
        "product.metafields.custom.frame_color":      product.get("metafield_frame_color", ""),
        "product.metafields.custom.lug_width":        product.get("metafield_lug_width", ""),
        "product.metafields.custom.product_source_url": source_url,
        "product.metafields.custom.water_resistance": product.get("metafield_water_resistance", ""),
        "product.metafields.shopify.target-gender":   product.get("metafield_target_gender", ""),
        "variant.metafields.custom.increased_price":  "",
        "variant.metafields.custom.display":          "",
        "Variant Metafield: custom.source_url":       source_url,
        # Shopify taxonomy metafields
        "product.metafields.shopify.band-color":  product.get("metafield_band_color", ""),
        "product.metafields.shopify.case-color":  product.get("metafield_case_color_shopify", ""),
        "product.metafields.shopify.dial-color":  product.get("metafield_dial_color_shopify", ""),
        "Age group":                              "adults",
    }

    rows = [primary]

    # --- Continuation rows for extra images ---
    for i, img_src in enumerate(images[1:], start=2):
        rows.append({
            "Handle":         handle,
            "Image Src":      img_src,
            "Image Position": str(i),
            "Image Alt Text": f"{title}_{i}",
        })

    return rows
