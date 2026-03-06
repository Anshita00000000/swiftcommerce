"""
deduplicator.py
Compares scraped product URLs against the Shopify export CSV.
Auto-detects the source URL column name (handles both legacy and current formats).
All URL comparisons are done after normalizing to https://.
"""

import logging
from pathlib import Path
from typing import List, Set

import pandas as pd

logger = logging.getLogger(__name__)

# Possible column names for the source URL — checked in order
SOURCE_URL_COL_CANDIDATES = [
    "Product Source URL (product.metafields.custom.product_source_url)",
    "Variant Metafield: custom.source_url",
    "custom.source_url",
]


def find_new_urls(
    scraped_urls: List[str],
    brand: str,
    shopify_exports_dir: str = "shopify_exports",
) -> List[str]:
    """
    Return scraped URLs that do NOT already exist in the Shopify export CSV.
    """
    existing = _load_shopify_urls(brand, shopify_exports_dir)
    logger.info(f"Shopify export has {len(existing)} existing source URLs.")

    new_urls = [url for url in scraped_urls if _normalize_url(url) not in existing]
    logger.info(
        f"Scraped {len(scraped_urls)} URLs -> {len(new_urls)} are new "
        f"({len(scraped_urls) - len(new_urls)} already in Shopify)."
    )
    return new_urls


def find_removed_products(
    scraped_urls: List[str],
    brand: str,
    shopify_exports_dir: str = "shopify_exports",
) -> pd.DataFrame:
    """
    Find Shopify products whose source URLs no longer exist on the brand website.
    Used in DRAFTING mode.
    """
    csv_path = _get_csv_path(brand, shopify_exports_dir)
    if not csv_path.exists():
        logger.warning(f"No Shopify export found at {csv_path}. Returning empty.")
        return pd.DataFrame()

    df = _load_csv(csv_path)
    source_col = _detect_source_col(df)
    if not source_col:
        logger.warning("No source URL column found in Shopify CSV. Returning empty.")
        return pd.DataFrame()

    live_normalized = {_normalize_url(u) for u in scraped_urls}

    mask = df[source_col].notna() & (df[source_col].str.strip() != "")
    df_with_url = df[mask].copy()

    removed_mask = df_with_url[source_col].apply(
        lambda u: _normalize_url(str(u)) not in live_normalized
    )
    removed_df = df_with_url[removed_mask].copy()

    if not removed_df.empty and "Handle" in removed_df.columns:
        removed_df = removed_df.drop_duplicates(subset=["Handle"], keep="first")

    logger.info(f"Found {len(removed_df)} Shopify products no longer on website.")
    return removed_df


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_shopify_urls(brand: str, shopify_exports_dir: str) -> Set[str]:
    csv_path = _get_csv_path(brand, shopify_exports_dir)
    if not csv_path.exists():
        logger.warning(
            f"Shopify export not found: {csv_path}. "
            "Treating all scraped URLs as new."
        )
        return set()

    df = _load_csv(csv_path)
    source_col = _detect_source_col(df)

    if not source_col:
        logger.warning(
            f"No source URL column found in {csv_path}. "
            "Treating all scraped URLs as new."
        )
        return set()

    logger.info(f"Using source URL column: '{source_col}'")
    urls = df[source_col].dropna().astype(str)
    return {_normalize_url(u) for u in urls if u.strip()}


def _detect_source_col(df: pd.DataFrame) -> str:
    """Find the source URL column from known candidates."""
    for candidate in SOURCE_URL_COL_CANDIDATES:
        if candidate in df.columns:
            return candidate
    # Fuzzy fallback: find any column containing 'source_url' or 'product_source'
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
