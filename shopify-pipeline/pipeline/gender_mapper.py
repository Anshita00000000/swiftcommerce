"""
gender_mapper.py

Assigns a gender tag to each collected product URL by cross-referencing the
master product list against per-gender collection results.

This is Pass 3 of the URL collection pipeline.  It performs only pure dict
operations — no page loads, no Selenium, no I/O.

Public API
----------
assign_genders(master_entries, gender_collection_results, default_gender)
    → (url_gender_map, diagnostics)
"""

import logging
import re
from typing import Dict, List, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def assign_genders(
    master_entries: List[Dict],
    gender_collection_results: List[Dict],
    default_gender: str = "",
) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
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

    Additionally, if no gender_collection_results are provided at all, the
    caller is responsible for applying any keyword-based fallback before using
    the returned map.

    Args:
        master_entries:
            Ordered list of dicts from Pass 1, each containing at minimum:
              {url, normalized_url, code, availability}
        gender_collection_results:
            List of dicts from Pass 2, each:
              {gender: "Men"|"Women", url: ..., pairs: [{url, code}, ...]}
        default_gender:
            Fallback gender string ("Men", "Women", "", …) used when a product
            is not found in any gender collection.

    Returns:
        url_gender_map:
            {normalized_url: "Men"|"Women"|"Unisex"|""}
        diagnostics:
            {
              "unknown_gender":        [norm_url, ...],   # no match, no default
              "missing_from_primary":  [norm_url, ...],   # in gender but not master
            }
    """
    # ------------------------------------------------------------------
    # Build lookup structures from the gender collections
    # ------------------------------------------------------------------
    # Per-gender sets of: normalized URLs, product slugs, product codes
    gender_url_sets:  Dict[str, Set[str]] = {}
    gender_slug_sets: Dict[str, Set[str]] = {}
    gender_code_sets: Dict[str, Set[str]] = {}
    all_gender_norms: Set[str] = set()

    for gcr in gender_collection_results:
        gender = gcr["gender"]
        gender_url_sets.setdefault(gender, set())
        gender_slug_sets.setdefault(gender, set())
        gender_code_sets.setdefault(gender, set())

        for pair in gcr["pairs"]:
            norm = _normalize_url(pair["url"])
            gender_url_sets[gender].add(norm)
            all_gender_norms.add(norm)

            slug = _get_slug(norm)
            if slug:
                gender_slug_sets[gender].add(slug)

            code = pair.get("code", "")
            if code:
                gender_code_sets[gender].add(code)

    # Slug → normalized_url mapping from the master list (for missing check)
    primary_by_slug: Dict[str, str] = {
        _get_slug(e["normalized_url"]): e["normalized_url"]
        for e in master_entries
    }
    master_slugs: Set[str] = {s for s in primary_by_slug if s}
    master_norms: Set[str] = {e["normalized_url"] for e in master_entries}

    # ------------------------------------------------------------------
    # Assign gender to each master entry
    # ------------------------------------------------------------------
    url_gender_map: Dict[str, str] = {}
    unknown_gender: List[str] = []

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
                unknown_gender.append(norm)
        elif matched == {"Men"}:
            url_gender_map[norm] = "Men"
        elif matched == {"Women"}:
            url_gender_map[norm] = "Women"
        else:
            # Found in more than one gender collection (or an unexpected value)
            url_gender_map[norm] = "Unisex"

    # ------------------------------------------------------------------
    # Detect gender-collection products not found in master
    # ------------------------------------------------------------------
    # Only flag a product as "missing from primary" if its slug is also absent
    # from the master — avoids false positives caused by URL-path differences.
    missing_from_primary: List[str] = [
        u for u in all_gender_norms
        if u not in master_norms and _get_slug(u) not in master_slugs
    ]

    diagnostics = {
        "unknown_gender":       unknown_gender,
        "missing_from_primary": missing_from_primary,
    }

    return url_gender_map, diagnostics


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
