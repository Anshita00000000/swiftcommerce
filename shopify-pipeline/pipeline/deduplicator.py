"""
deduplicator.py

Compares scraped URL + SKU data against the existing Shopify export CSV to
determine which products are new (not yet in Shopify) and which already exist.

Public API
──────────
find_new_urls(url_data_map, brand, shopify_exports_dir, ctx) -> dedup_result
    url_data_map  — dict from url_collector: {normalized_url: {url, sku, gender, availability}}
    Returns dedup_result:
      {
        "new":      [{"url", "sku", "gender", "match_method": None}, ...],
        "existing": [{"url", "sku", "match_method": "url"|"sku"},    ...],
        "summary":  {"total_scraped", "new", "existing"},
      }

find_removed_products(scraped_urls, brand, shopify_exports_dir) -> pd.DataFrame
    Used in drafting mode to detect products removed from the brand website.

Matching logic
──────────────
  1. Normalize scraped URL → check against set of normalized source URLs from export.
  2. If no URL match AND scraped SKU is non-empty → check against Variant SKU column.
  3. Record match_method: "url", "sku", or None (new).

Source URL column auto-detection
─────────────────────────────────
  Known candidates tried in order; fuzzy fallback finds any column containing
  "source_url" or "product_source".

Output files (written via ctx when ctx is provided)
────────────────────────────────────────────────────
  dedup_result.json  — meta block + new_urls list + existing list
  new_urls.txt       — one URL per line with header comment
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

import pandas as pd

logger = logging.getLogger(__name__)

# Possible column names for the source URL — checked in order
_SOURCE_URL_COL_CANDIDATES = [
    "Product Source URL (product.metafields.custom.product_source_url)",
    "Variant Metafield: custom.source_url",
    "custom.source_url",
]

_SKU_COL = "Variant SKU"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_new_urls(
    url_data_map: Dict[str, Dict],
    brand: str,
    shopify_exports_dir: str = "shopify_exports",
    ctx=None,
) -> Dict:
    """
    Compare scraped URL+SKU data against the Shopify export CSV.

    Args:
        url_data_map:        {normalized_url: {url, sku, gender, availability}}
        brand:               Brand slug, used to locate {brand}_shopify.csv.
        shopify_exports_dir: Directory containing the Shopify export CSV files.
        ctx:                 Optional RunContext for writing output files and
                             logging events.

    Returns:
        dedup_result dict — see module docstring.
    """
    existing_url_norms, existing_skus = _load_shopify_data(brand, shopify_exports_dir)

    new_entries:      List[Dict] = []
    existing_entries: List[Dict] = []
    n_matched_url = 0
    n_matched_sku = 0

    for norm_url, data in url_data_map.items():
        scraped_url  = data.get("url", norm_url)
        sku          = data.get("sku", "")
        gender       = data.get("gender", "")

        # Match 1 — URL
        if norm_url in existing_url_norms:
            existing_entries.append({
                "url":          scraped_url,
                "sku":          sku,
                "match_method": "url",
            })
            n_matched_url += 1
            continue

        # Match 2 — SKU (only when a non-empty SKU was extracted from listing)
        if sku and sku in existing_skus:
            existing_entries.append({
                "url":          scraped_url,
                "sku":          sku,
                "match_method": "sku",
            })
            n_matched_sku += 1
            continue

        # No match — new product
        new_entries.append({
            "url":          scraped_url,
            "sku":          sku,
            "gender":       gender,
            "match_method": None,
        })

    summary = {
        "total_scraped": len(url_data_map),
        "new":           len(new_entries),
        "existing":      len(existing_entries),
    }

    msg = (
        f"Deduplication: {summary['total_scraped']} scraped → "
        f"{summary['new']} new, {summary['existing']} existing "
        f"(matched by URL: {n_matched_url}, by SKU: {n_matched_sku})"
    )
    logger.info(msg)
    if ctx:
        ctx.log_event(msg)

    dedup_result = {
        "new":      new_entries,
        "existing": existing_entries,
        "summary":  summary,
    }

    if ctx:
        _write_outputs(ctx, dedup_result, n_matched_url, n_matched_sku)

    return dedup_result


def find_removed_products(
    scraped_urls: List[str],
    brand: str,
    shopify_exports_dir: str = "shopify_exports",
) -> pd.DataFrame:
    """
    Find Shopify products whose source URLs no longer exist on the brand website.
    Used in drafting mode.
    """
    csv_path = _get_csv_path(brand, shopify_exports_dir)
    if not csv_path.exists():
        logger.warning("No Shopify export found at %s. Returning empty.", csv_path)
        return pd.DataFrame()

    df = _load_csv(csv_path)
    source_col = _detect_source_col(df)
    if not source_col:
        logger.warning("No source URL column found in Shopify CSV. Returning empty.")
        return pd.DataFrame()

    live_normalized = {_normalize_url(u) for u in scraped_urls}

    mask       = df[source_col].notna() & (df[source_col].str.strip() != "")
    df_with_url = df[mask].copy()

    removed_mask = df_with_url[source_col].apply(
        lambda u: _normalize_url(str(u)) not in live_normalized
    )
    removed_df = df_with_url[removed_mask].copy()

    if not removed_df.empty and "Handle" in removed_df.columns:
        removed_df = removed_df.drop_duplicates(subset=["Handle"], keep="first")

    logger.info("Found %d Shopify products no longer on website.", len(removed_df))
    return removed_df


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _write_outputs(ctx, dedup_result: Dict, n_matched_url: int, n_matched_sku: int) -> None:
    """Write dedup_result.json and new_urls.txt to the run folder."""
    summary   = dedup_result["summary"]
    new_urls  = [e["url"] for e in dedup_result["new"]]

    # dedup_result.json
    payload = {
        "meta": {
            "total_scraped":  summary["total_scraped"],
            "new":            summary["new"],
            "existing":       summary["existing"],
            "matched_by_url": n_matched_url,
            "matched_by_sku": n_matched_sku,
        },
        "new_urls": new_urls,
        "existing": [
            {"url": e["url"], "match_method": e["match_method"]}
            for e in dedup_result["existing"]
        ],
    }
    ctx.path("dedup_result.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # new_urls.txt
    ts     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (
        f"# {ts} | {summary['new']} new URLs "
        f"(of {summary['total_scraped']} scraped, "
        f"{summary['existing']} already in Shopify)"
    )
    ctx.path("new_urls.txt").write_text(
        "\n".join([header] + new_urls),
        encoding="utf-8",
    )

    logger.debug("Wrote dedup_result.json and new_urls.txt to run folder.")


# ---------------------------------------------------------------------------
# Shopify CSV loading
# ---------------------------------------------------------------------------

def _load_shopify_data(
    brand: str, shopify_exports_dir: str
) -> tuple:
    """
    Load existing normalized source URLs and SKUs from the Shopify export CSV.

    Returns:
        (existing_url_norms, existing_skus) — two sets of strings.
        Both sets are empty when the file is missing or unreadable.
    """
    csv_path = _get_csv_path(brand, shopify_exports_dir)
    if not csv_path.exists():
        logger.warning(
            "Shopify export not found: %s. Treating all scraped URLs as new.", csv_path
        )
        return set(), set()

    df = _load_csv(csv_path)

    # Source URL set
    source_col = _detect_source_col(df)
    if not source_col:
        logger.warning(
            "No source URL column found in %s. URL matching disabled.", csv_path
        )
        existing_url_norms: Set[str] = set()
    else:
        logger.info("Using source URL column: '%s'", source_col)
        existing_url_norms = {
            _normalize_url(u)
            for u in df[source_col].dropna().astype(str)
            if u.strip()
        }

    # SKU set
    if _SKU_COL in df.columns:
        existing_skus: Set[str] = {
            s.strip()
            for s in df[_SKU_COL].dropna().astype(str)
            if s.strip()
        }
    else:
        logger.warning("Column '%s' not found in %s. SKU matching disabled.",
                       _SKU_COL, csv_path)
        existing_skus = set()

    logger.info(
        "Shopify export: %d existing source URLs, %d SKUs loaded from %s",
        len(existing_url_norms), len(existing_skus), csv_path,
    )
    return existing_url_norms, existing_skus


def _detect_source_col(df: pd.DataFrame) -> str:
    """Find the source URL column — known candidates first, then fuzzy fallback."""
    for candidate in _SOURCE_URL_COL_CANDIDATES:
        if candidate in df.columns:
            return candidate
    for col in df.columns:
        col_lower = col.lower()
        if "source_url" in col_lower or "product_source" in col_lower:
            return col
    return ""


def _load_csv(path: Path) -> pd.DataFrame:
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(path, encoding=enc, dtype=str, low_memory=False)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Could not read CSV with any supported encoding: {path}")


def _get_csv_path(brand: str, exports_dir: str) -> Path:
    return Path(exports_dir) / f"{brand}_shopify.csv"


def _normalize_url(url: str) -> str:
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[7:]
    return url.rstrip("/")
