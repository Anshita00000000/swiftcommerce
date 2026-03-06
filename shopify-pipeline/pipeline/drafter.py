"""
drafter.py
DRAFTING mode: produces a CSV that sets removed products to Draft
and adds the tag "swithommerce_PD" (sic — matching spec exactly).
"""

import logging
from pathlib import Path
from typing import List

import pandas as pd

from pipeline.deduplicator import find_removed_products

logger = logging.getLogger(__name__)

DRAFT_TAG = "swithommerce_PD"

# Columns Shopify needs to update existing products
DRAFT_COLUMNS = [
    "Handle",
    "Title",
    "Published",
    "Tags",
]


def build_draft_csv(
    scraped_urls: List[str],
    brand: str,
    output_root: str = "outputs",
    shopify_exports_dir: str = "shopify_exports",
) -> str:
    """
    Find products that no longer exist on the website and produce a CSV
    that sets them to Draft + adds the tag "swithommerce_PD".

    Args:
        scraped_urls: All live URLs from the brand website.
        brand: Brand name.
        output_root: Base output directory.
        shopify_exports_dir: Directory containing Shopify export CSVs.

    Returns:
        Path to the output CSV, or empty string if nothing to draft.
    """
    removed_df = find_removed_products(scraped_urls, brand, shopify_exports_dir)

    if removed_df.empty:
        logger.info("No products to draft — everything is still live.")
        return ""

    draft_rows = []
    for _, row in removed_df.iterrows():
        handle = str(row.get("Handle", "")).strip()
        title = str(row.get("Title", "")).strip()
        existing_tags = str(row.get("Tags", "")).strip()

        # Append DRAFT_TAG if not already present
        tags = _add_tag(existing_tags, DRAFT_TAG)

        draft_rows.append({
            "Handle": handle,
            "Title": title,
            "Published": "FALSE",
            "Tags": tags,
        })

    draft_df = pd.DataFrame(draft_rows, columns=DRAFT_COLUMNS)

    out_dir = Path(output_root) / brand
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "draft_products.csv"

    draft_df.to_csv(out_path, index=False, encoding="utf-8")
    logger.info(f"Draft CSV written: {out_path} ({len(draft_rows)} products)")

    return str(out_path)


def _add_tag(existing_tags: str, new_tag: str) -> str:
    """Add new_tag to existing comma-separated tags if not already present."""
    if not existing_tags or existing_tags == "nan":
        return new_tag
    tags = [t.strip() for t in existing_tags.split(",") if t.strip()]
    if new_tag not in tags:
        tags.append(new_tag)
    return ", ".join(tags)
