"""
drafter.py

DRAFTING mode: finds Shopify products that no longer exist on the brand
website and produces a minimal Shopify CSV to set them to Draft status.

Flow
────
  1. Collect all live URLs from the brand website via url_collector
     (primary_url only — gender pass is skipped for drafting).
  2. Load the Shopify export CSV.
  3. For every Shopify product that has a source URL set:
       a. If normalized source URL IS in the live set  → still live, skip.
       b. If URL is missing from live set but the product's Variant SKU IS
          found in the live SKU set                   → still live (URL moved),
                                                        skip.
       c. Otherwise                                   → product is gone, draft it.
  4. Deduplicate by Handle (keep first row per Handle).

Output files (written via ctx.path())
──────────────────────────────────────
  shopify_draft_update.csv  — Shopify bulk-update format (Handle, Title,
                               Published, Tags, Status)
  links_to_draft.json       — intermediate list for human auditing

run_summary.txt (drafting mode) includes
─────────────────────────────────────────
  Brand, Timestamp, Runtime (header block)
  Counts → URLs collected
  Events → total live URLs, total products to draft, handles list

Public API
──────────
build_draft_csv(driver, config, ctx, shopify_exports_dir) -> str
    Returns path to the written CSV, or "" when there is nothing to draft.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set

import pandas as pd

from pipeline import url_collector

logger = logging.getLogger(__name__)

# Tag appended to products being set to Draft
DRAFT_TAG = "SwiftCommerce_PD"

# Columns written to the Shopify bulk-update CSV
DRAFT_COLUMNS = ["Handle", "Title", "Published", "Tags", "Status"]

# Known column names for the product source URL — tried in order
_SOURCE_URL_CANDIDATES = [
    "Product Source URL (product.metafields.custom.product_source_url)",
    "Variant Metafield: custom.source_url",
    "custom.source_url",
]

_SKU_COL = "Variant SKU"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_draft_csv(
    driver,
    config: dict,
    ctx,
    shopify_exports_dir: str = "shopify_exports",
) -> str:
    """
    Orchestrate the full drafting flow.

    Args:
        driver:              Selenium WebDriver instance.
        config:              Adapted brand config dict.
        ctx:                 RunContext for the current drafting run.
        shopify_exports_dir: Directory that contains {brand}_shopify.csv.

    Returns:
        Absolute path to shopify_draft_update.csv, or "" if nothing to draft.
    """
    brand = config.get("brand_name", "")

    # ------------------------------------------------------------------
    # Step 1 — Collect live URLs (primary URL only, no gender pass)
    # ------------------------------------------------------------------
    live_config = {**config, "gender_urls": []}
    all_urls, url_data_map = url_collector.collect_urls(
        driver, live_config, limit=None, ctx=None
    )

    n_live  = len(all_urls)
    live_url_norms: Set[str] = set(all_urls)   # already normalized by url_collector
    live_skus: Set[str]      = {
        d["sku"] for d in url_data_map.values() if d.get("sku")
    }

    msg = f"Collected {n_live} live URLs from brand website"
    logger.info(msg)
    ctx.log_event(msg)
    ctx._urls_collected_total = n_live

    # ------------------------------------------------------------------
    # Step 2 — Load Shopify export CSV
    # ------------------------------------------------------------------
    csv_path = Path(shopify_exports_dir) / f"{brand}_shopify.csv"
    if not csv_path.exists():
        msg = f"No Shopify export found at {csv_path}. Nothing to draft."
        logger.warning(msg)
        ctx.log_event(msg)
        ctx.save_summary({"urls_collected": n_live})
        return ""

    df = _load_csv(csv_path)
    source_col = _detect_source_col(df)
    if not source_col:
        msg = f"No source URL column found in {csv_path}. Nothing to draft."
        logger.warning(msg)
        ctx.log_event(msg)
        ctx.save_summary({"urls_collected": n_live})
        return ""

    logger.info("Using source URL column: '%s'", source_col)

    # ------------------------------------------------------------------
    # Step 3 — Find removed products
    # ------------------------------------------------------------------
    to_draft: List[Dict] = []
    seen_handles: Set[str] = set()

    for _, row in df.iterrows():
        handle = _cell(row, "Handle")
        if not handle:
            continue

        source_url_raw = _cell(row, source_col)
        if not source_url_raw:
            continue   # No source URL → cannot verify, skip

        source_norm = _normalize_url(source_url_raw)

        # (a) URL still live
        if source_norm in live_url_norms:
            continue

        # (b) URL gone but SKU still live → product URL moved, not removed
        shopify_sku = _cell(row, _SKU_COL)
        if shopify_sku and shopify_sku in live_skus:
            continue

        # (c) Product is gone — record it
        match_method: str = "sku" if shopify_sku else "url"

        # Step 4: dedup by Handle (keep first row)
        if handle in seen_handles:
            continue
        seen_handles.add(handle)

        to_draft.append({
            "handle":        handle,
            "title":         _cell(row, "Title"),
            "source_url":    source_url_raw,
            "match_method":  match_method,
            "existing_tags": _cell(row, "Tags"),
        })

    # ------------------------------------------------------------------
    # Nothing to draft
    # ------------------------------------------------------------------
    if not to_draft:
        msg = "No products to draft — all Shopify products are still live."
        logger.info(msg)
        ctx.log_event(msg)
        ctx.save_summary({"urls_collected": n_live})
        return ""

    # ------------------------------------------------------------------
    # Log summary to run events (included in run_summary.txt)
    # ------------------------------------------------------------------
    ctx.log_event(f"Products to draft: {len(to_draft)}")
    ctx.log_event(
        "Handles to draft: " + ", ".join(p["handle"] for p in to_draft)
    )

    # ------------------------------------------------------------------
    # Write shopify_draft_update.csv
    # ------------------------------------------------------------------
    csv_rows = [
        {
            "Handle":    p["handle"],
            "Title":     p["title"],
            "Published": "FALSE",
            "Tags":      _add_tag(p["existing_tags"], DRAFT_TAG),
            "Status":    "draft",
        }
        for p in to_draft
    ]
    draft_df  = pd.DataFrame(csv_rows, columns=DRAFT_COLUMNS)
    csv_out   = ctx.path("shopify_draft_update.csv")
    draft_df.to_csv(csv_out, index=False, encoding="utf-8")
    logger.info("shopify_draft_update.csv written: %d products", len(csv_rows))

    # ------------------------------------------------------------------
    # Write links_to_draft.json
    # ------------------------------------------------------------------
    json_payload = {
        "meta": {
            "brand":          brand,
            "timestamp":      ctx.timestamp,
            "total_to_draft": len(to_draft),
        },
        "products": [
            {
                "handle":       p["handle"],
                "title":        p["title"],
                "source_url":   p["source_url"],
                "match_method": p["match_method"],
            }
            for p in to_draft
        ],
    }
    ctx.path("links_to_draft.json").write_text(
        json.dumps(json_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("links_to_draft.json written: %d products", len(to_draft))

    # ------------------------------------------------------------------
    # Save run summary
    # ------------------------------------------------------------------
    ctx.save_summary({"urls_collected": n_live})

    return str(csv_out)


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def _load_csv(path: Path) -> pd.DataFrame:
    """Try utf-8, utf-8-sig, then latin-1 before giving up."""
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(path, encoding=enc, dtype=str, low_memory=False)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Could not read CSV with any supported encoding: {path}")


def _detect_source_col(df: pd.DataFrame) -> str:
    """Return the source URL column name — known candidates first, then fuzzy."""
    for candidate in _SOURCE_URL_CANDIDATES:
        if candidate in df.columns:
            return candidate
    for col in df.columns:
        col_lower = col.lower()
        if "source_url" in col_lower or "product_source" in col_lower:
            return col
    return ""


def _cell(row, col: str) -> str:
    """Return a stripped cell value, or '' for missing/NaN/nan."""
    val = str(row.get(col, "")).strip()
    return "" if val in ("", "nan", "None") else val


# ---------------------------------------------------------------------------
# Tag helper
# ---------------------------------------------------------------------------

def _add_tag(existing_tags: str, new_tag: str) -> str:
    """Append new_tag to the comma-separated tag string if not already present."""
    if not existing_tags:
        return new_tag
    tags = [t.strip() for t in existing_tags.split(",") if t.strip()]
    if new_tag not in tags:
        tags.append(new_tag)
    return ", ".join(tags)


# ---------------------------------------------------------------------------
# URL normalisation
# ---------------------------------------------------------------------------

def _normalize_url(url: str) -> str:
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[7:]
    return url.rstrip("/")
