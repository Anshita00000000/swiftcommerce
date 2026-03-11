"""
gender_mapper.py

Assigns a gender tag to each collected product URL by cross-referencing the
master product list against per-gender collection results.

This is a pure utility module — no Selenium, no file I/O, no pipeline imports.

Public API
----------
assign_genders(master_entries, gender_collection_results, default_gender)
    → {normalized_url: "Men"|"Women"|"Unisex"|""}

get_unknown_urls(gender_map)
    → [normalized_url, ...]   # all URLs with empty-string gender
"""

import logging
import re
from typing import Dict, List, Set

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def assign_genders(
    master_entries: List[Dict],
    gender_collection_results: List[Dict],
    default_gender: str = "",
) -> Dict[str, str]:
    """
    Match every master entry against the per-gender collection results and
    assign each product a gender string.

    Matching strategy (tried in order, most-specific first):
      1. Exact normalized-URL match — same URL appears in a gender collection.
      2. Product-slug match — /products/{slug} segment matches across different
         collection paths (e.g. /collections/all vs /collections/mens).
      3. Product-code match — both sides have a non-empty code and they match.

    If a product appears in exactly one gender collection → that gender.
    If it appears in both Men and Women collections      → "Unisex".
    If it appears in neither                            → default_gender (or "").

    When default_gender is "" and a URL has no match, a warning is logged.

    Args:
        master_entries:
            Ordered list of dicts, each containing at minimum:
              {url, normalized_url, code}
        gender_collection_results:
            List of dicts, each:
              {gender: "Men"|"Women", url: ..., pairs: [{url, code}, ...]}
        default_gender:
            Fallback gender string ("Men", "Women", "", …) used when a product
            is not found in any gender collection.

    Returns:
        {normalized_url: "Men"|"Women"|"Unisex"|""} for every URL in
        master_entries.
    """
    # ------------------------------------------------------------------
    # Build lookup structures from the gender collections
    # ------------------------------------------------------------------
    gender_url_sets:  Dict[str, Set[str]] = {}
    gender_slug_sets: Dict[str, Set[str]] = {}
    gender_code_sets: Dict[str, Set[str]] = {}

    for gcr in gender_collection_results:
        gender = gcr["gender"]
        gender_url_sets.setdefault(gender, set())
        gender_slug_sets.setdefault(gender, set())
        gender_code_sets.setdefault(gender, set())

        for pair in gcr["pairs"]:
            norm = _normalize_url(pair["url"])
            gender_url_sets[gender].add(norm)

            slug = _get_slug(norm)
            if slug:
                gender_slug_sets[gender].add(slug)

            code = pair.get("code", "")
            if code:
                gender_code_sets[gender].add(code)

    # ------------------------------------------------------------------
    # Assign gender to each master entry
    # ------------------------------------------------------------------
    url_gender_map: Dict[str, str] = {}

    for entry in master_entries:
        norm       = entry["normalized_url"]
        code       = entry.get("code", "")
        entry_slug = _get_slug(norm)

        matched: Set[str] = set()

        for gender in gender_url_sets:
            # Match 1: exact URL
            if norm in gender_url_sets[gender]:
                matched.add(gender)
                continue
            # Match 2: product slug
            if entry_slug and entry_slug in gender_slug_sets.get(gender, set()):
                matched.add(gender)
                continue
            # Match 3: product code
            if code and code in gender_code_sets.get(gender, set()):
                matched.add(gender)

        if not matched:
            url_gender_map[norm] = default_gender
            if not default_gender:
                logger.warning("No gender match for URL (no default): %s", norm)
        elif matched == {"Men"}:
            url_gender_map[norm] = "Men"
        elif matched == {"Women"}:
            url_gender_map[norm] = "Women"
        else:
            url_gender_map[norm] = "Unisex"

    return url_gender_map


def get_unknown_urls(gender_map: Dict[str, str]) -> List[str]:
    """
    Return all URLs from gender_map whose assigned gender is an empty string.

    Args:
        gender_map: The dict returned by assign_genders().

    Returns:
        List of normalized URLs with no gender assigned.
    """
    return [url for url, gender in gender_map.items() if gender == ""]


def infer_gender_from_url(cat_url: str) -> str:
    """
    Infer a gender tag from keywords in a category URL.

    Returns "Men", "Women", or "" (cannot determine).
    Used as a last-resort fallback when no gender_urls are configured and
    the brand uses separate Men/Women category pages as its primary source.
    """
    url_lower = cat_url.lower()
    is_men   = any(kw in url_lower for kw in ("/mens", "/men/", "/men-", "men's", "gents"))
    is_women = any(kw in url_lower for kw in ("/womens", "/women/", "/women-", "women's", "ladies"))
    if is_men and not is_women:
        return "Men"
    if is_women and not is_men:
        return "Women"
    return ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_slug(url: str) -> str:
    """
    Extract the product slug from a URL.
    Matches the /products/{slug} path segment (Shopify / common convention).
    Returns "" if the pattern is not found.
    """
    m = re.search(r"/products/([^/?#]+)", url.lower().strip())
    return m.group(1) if m else ""


def _normalize_url(url: str) -> str:
    """Normalise URL to https://, strip trailing slash."""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[7:]
    return url.rstrip("/")
