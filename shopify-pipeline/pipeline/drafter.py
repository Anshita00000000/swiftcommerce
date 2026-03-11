"""
drafter.py

DRAFTING mode: reads links_to_draft.json produced by deduplicator.py,
loads the full Shopify export CSV, filters to only the handles being
drafted, modifies Status and Tags, and writes a ready-to-import CSV.

The output file preserves the exact 86-column structure of the original
Shopify export so it can be re-imported without any manual editing.

Public API
──────────
build_draft_output(brand, ctx, shopify_exports_dir) -> str
    Returns path to shopify_draft_copy.csv, or "" if nothing to draft.
"""

import json
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

DRAFT_TAG = "SwiftCommerce_PD"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_draft_output(
    brand: str,
    ctx,
    shopify_exports_dir: str = "shopify_exports",
) -> str:
    """
    Produce a Shopify-importable CSV for products to be set to Draft.

    Args:
        brand:               Brand slug used to locate {brand}_shopify.csv.
        ctx:                 RunContext; used for ctx.path() and logging.
        shopify_exports_dir: Directory containing the Shopify export CSV files.

    Returns:
        Absolute path to shopify_draft_copy.csv, or "" if nothing to draft.
    """
    # ------------------------------------------------------------------
    # Step 1 — Read links_to_draft.json
    # ------------------------------------------------------------------
    json_path = ctx.path("links_to_draft.json")
    if not Path(json_path).exists():
        logger.info("No products to draft (links_to_draft.json not found).")
        return ""

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    products = data.get("products", [])
    if not products:
        logger.info("No products to draft.")
        return ""

    handles_to_draft = {p["handle"] for p in products}

    # ------------------------------------------------------------------
    # Step 2 — Load full Shopify export CSV
    # ------------------------------------------------------------------
    csv_path = Path(shopify_exports_dir) / f"{brand}_shopify.csv"
    if not csv_path.exists():
        logger.warning(
            "Shopify export not found: %s. Cannot produce draft CSV.", csv_path
        )
        return ""

    full_df = _load_csv(csv_path)

    # ------------------------------------------------------------------
    # Step 3 — Filter to only rows for handles being drafted
    #          (ALL rows per handle, including image-only rows)
    # ------------------------------------------------------------------
    draft_df = full_df[full_df["Handle"].isin(handles_to_draft)].copy()

    if draft_df.empty:
        logger.warning(
            "None of the handles from links_to_draft.json were found in "
            "the Shopify export."
        )
        return ""

    # ------------------------------------------------------------------
    # Step 4 — Modify only Status and Tags
    # ------------------------------------------------------------------
    is_primary = (
        draft_df["Title"].notna()
        & (draft_df["Title"].astype(str).str.strip() != "")
        & (draft_df["Title"].astype(str) != "nan")
    )

    # Status → "draft" for every row
    draft_df["Status"] = "draft"

    # Tags → append DRAFT_TAG only on primary (non-image) rows
    if "Tags" in draft_df.columns:
        draft_df.loc[is_primary, "Tags"] = draft_df.loc[is_primary, "Tags"].apply(
            lambda t: _add_tag(str(t) if pd.notna(t) else "", DRAFT_TAG)
        )
    else:
        draft_df.loc[is_primary, "Tags"] = DRAFT_TAG

    # ------------------------------------------------------------------
    # Step 5 — Write shopify_draft_copy.csv (all original columns)
    # ------------------------------------------------------------------
    out_path = ctx.path("shopify_draft_copy.csv")
    draft_df.to_csv(out_path, index=False, encoding="utf-8")

    # ------------------------------------------------------------------
    # Step 6 — Log summary
    # ------------------------------------------------------------------
    n_handles = draft_df["Handle"].nunique()
    n_rows    = len(draft_df)
    logger.info(
        "Draft CSV written: %d unique handles, %d total rows → %s",
        n_handles, n_rows, out_path,
    )

    return str(out_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_tag(existing_tags: str, new_tag: str) -> str:
    if not existing_tags or str(existing_tags).strip() in ("", "nan"):
        return new_tag
    tags = [t.strip() for t in str(existing_tags).split(",") if t.strip()]
    if new_tag not in tags:
        tags.append(new_tag)
    return ", ".join(tags)


def _load_csv(path: Path) -> pd.DataFrame:
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(path, encoding=enc, dtype=str, low_memory=False)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Could not read CSV with any supported encoding: {path}")
