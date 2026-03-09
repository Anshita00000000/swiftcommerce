"""
inventory_manager.py
Maintains outputs/{brand}/master_catalog.csv — one row per unique product,
accumulating across all listing runs.
"""

import csv
import logging
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

CATALOG_COLUMNS = [
    "handle",
    "title",
    "sku",
    "price",
    "vendor",
    "type",
    "tags",
    "source_url",
    "first_seen",
    "last_seen",
    "run_count",
    "status",
]


def _normalize_url(url: str) -> str:
    """Normalize to https://, strip trailing slash."""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[7:]
    return url.rstrip("/")


def update_inventory(brand: str, normalized_products: list, run_timestamp: str) -> dict:
    """
    Update master_catalog.csv for the given brand with results from this run.

    Args:
        brand: Brand name (used to locate the catalog file).
        normalized_products: List of normalized product dicts from normalizer.py.
        run_timestamp: Timestamp string (YYYYMMDD_HHMMSS) for this run.

    Returns:
        Dict with keys: total_in_catalog, new_this_run, updated_this_run.
    """
    catalog_path = Path("outputs") / brand / "master_catalog.csv"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing catalog, handling encoding errors gracefully
    existing_rows: List[dict] = []
    if catalog_path.exists():
        for enc in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                with open(catalog_path, "r", encoding=enc, newline="") as f:
                    reader = csv.DictReader(f)
                    existing_rows = list(reader)
                break
            except UnicodeDecodeError:
                continue
            except Exception as e:
                logger.warning(f"Could not read master_catalog.csv with {enc}: {e}")
                break

    # Deduplicate existing rows on load (primary: source_url, secondary: sku)
    deduped_rows: List[dict] = []
    seen_urls: Dict[str, dict] = {}
    seen_skus: Dict[str, dict] = {}

    for row in existing_rows:
        src_url = _normalize_url(row.get("source_url", ""))
        sku = row.get("sku", "").strip()

        if src_url:
            if src_url not in seen_urls:
                seen_urls[src_url] = row
                deduped_rows.append(row)
            # else: duplicate by source_url — skip
        elif sku:
            if sku not in seen_skus:
                seen_skus[sku] = row
                deduped_rows.append(row)
            # else: duplicate by sku — skip
        else:
            deduped_rows.append(row)

    # Rebuild indexes after dedup (pointing to live row objects in deduped_rows)
    url_index: Dict[str, dict] = {}
    sku_index: Dict[str, dict] = {}
    for row in deduped_rows:
        src_url = _normalize_url(row.get("source_url", ""))
        sku = row.get("sku", "").strip()
        if src_url:
            url_index[src_url] = row
        elif sku:
            sku_index[sku] = row

    new_this_run = 0
    updated_this_run = 0

    for product in normalized_products:
        src_url = _normalize_url(product.get("source_url", ""))
        sku = product.get("sku", "").strip()

        # Match against existing rows: primary key = source_url, fallback = sku
        matched_row = None
        if src_url:
            matched_row = url_index.get(src_url)
        if matched_row is None and sku:
            matched_row = sku_index.get(sku)

        if matched_row is not None:
            # Update last_seen and run_count only — keep all other fields unchanged
            matched_row["last_seen"] = run_timestamp
            try:
                matched_row["run_count"] = str(int(matched_row.get("run_count", "1")) + 1)
            except (ValueError, TypeError):
                matched_row["run_count"] = "2"
            updated_this_run += 1
        else:
            # New product — add as new row
            new_row = {
                "handle": product.get("handle", ""),
                "title": product.get("title", ""),
                "sku": sku,
                "price": product.get("price", ""),
                "vendor": product.get("vendor", ""),
                "type": product.get("type", ""),
                "tags": product.get("tags", ""),
                "source_url": product.get("source_url", ""),
                "first_seen": run_timestamp,
                "last_seen": run_timestamp,
                "run_count": "1",
                "status": "active",
            }
            deduped_rows.append(new_row)
            if src_url:
                url_index[src_url] = new_row
            elif sku:
                sku_index[sku] = new_row
            new_this_run += 1

    # Write updated catalog
    with open(catalog_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CATALOG_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(deduped_rows)

    logger.info(
        f"master_catalog.csv updated: total={len(deduped_rows)}, "
        f"new={new_this_run}, updated={updated_this_run}"
    )

    return {
        "total_in_catalog": len(deduped_rows),
        "new_this_run": new_this_run,
        "updated_this_run": updated_this_run,
    }
