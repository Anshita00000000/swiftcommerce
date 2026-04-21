"""
pipeline/run_context.py

Manages per-run output folder structure, event logging, and summary writing
for the SwiftCommerce scraping pipeline.

Folder layout
─────────────
  Listing:
    outputs/{brand}/listing/{timestamp}/
    outputs/{brand}/listing/latest/   ← copy of the most recent run
  Drafting:
    outputs/{brand}/drafting/{timestamp}/
  Images (brand-level, shared across all runs):
    outputs/{brand}/images/raw/{handle}/
    outputs/{brand}/images/processed/{handle}/

Public API
──────────
  RunContext(brand, mode)
  .path(filename)                    → Path  inside run folder; creates parents
  .brand_images_raw_path(handle)     → Path  outputs/{brand}/images/raw/{handle}/
  .brand_images_processed_path(handle) → Path outputs/{brand}/images/processed/{handle}/
  .log_event(message)                append a timestamped line to the event log
  .save_summary(counts)              write run_summary.txt to the run folder
  .copy_to_latest()                  listing only — copy run folder → latest/

Expected keys in the counts dict passed to save_summary()
─────────────────────────────────────────────────────────
  Counts section:
    urls_collected, new_urls, products_scraped, products_exported,
    images_downloaded, images_processed

  Deduplication section  (omitted when none of the keys are present):
    dedup_matched_url, dedup_matched_sku, dedup_new

  Gender Assignment section  (omitted when none of the keys are present):
    gender_men, gender_women, gender_unisex, gender_unknown

  Inventory section  (omitted when none of the keys are present):
    catalog_total, catalog_new, catalog_updated
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Width of the section-header border line
_BORDER = "=" * 60

# Label column width used for most stat rows (pad to align the ":" separator)
_LABEL_W = 21

# Label column width for the header block (Brand / Mode / Timestamp / Runtime)
_HDR_W = 13


def _row(label: str, value: Any, width: int = _LABEL_W) -> str:
    """Format one stat row: left-padded label + ': ' + value."""
    return f"{label:<{width}}: {value}"


def _get_shopify_export_info(brand: str):
    """
    Return (csv_path_str, unique_handle_count).
    Counts distinct Handle values in the brand's Shopify export CSV.
    Returns count = -1 when the file is missing or unreadable.
    """
    csv_path = Path("shopify_exports") / f"{brand}_shopify.csv"
    if not csv_path.exists():
        return str(csv_path), -1
    try:
        handles = set()
        with open(csv_path, encoding="utf-8", errors="replace") as f:
            header_line = f.readline()
            cols = [c.strip().strip('"') for c in header_line.split(",")]
            try:
                handle_idx = cols.index("Handle")
            except ValueError:
                handle_idx = 0
            for line in f:
                if not line.strip():
                    continue
                parts = line.split(",")
                if handle_idx < len(parts):
                    h = parts[handle_idx].strip().strip('"')
                    if h:
                        handles.add(h)
        return str(csv_path), len(handles)
    except Exception:
        return str(csv_path), -1


class RunContext:
    """
    Holds all per-run state: output folder, event log, timing, and metadata.
    One instance is created at the start of each pipeline run.
    """

    def __init__(self, brand: str, mode: str) -> None:
        """
        Create the timestamped output folder and initialise run state.

        Args:
            brand: Brand slug (e.g. "seiko").  Used in folder paths.
            mode:  "listing" or "drafting".  Controls folder layout and
                   whether copy_to_latest() does anything.
        """
        self.brand      = brand
        self.mode       = mode
        self.timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.start_time = datetime.now()

        # Build the mode-specific run folder
        self._run_folder = Path("outputs") / brand / mode / self.timestamp
        self._run_folder.mkdir(parents=True, exist_ok=True)

        self._events: list = []

        # Attributes that other modules may write directly for summary use.
        # url_collector sets _urls_collected_total and _urls_after_limit.
        self._urls_collected_total: Optional[int] = None
        self._urls_after_limit:     Optional[int] = None

        logger.debug(
            "RunContext created: brand=%s mode=%s folder=%s",
            brand, mode, self._run_folder,
        )

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def path(self, filename: str) -> Path:
        """
        Return the full path to filename inside the run folder.
        Creates any intermediate parent directories that don't yet exist.
        """
        p = self._run_folder / filename
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def brand_images_raw_path(self, handle: str) -> Path:
        """
        Return the brand-level raw image directory for a product handle:
          outputs/{brand}/images/raw/{handle}/
        Creates the directory if it does not exist.
        """
        p = Path("outputs") / self.brand / "images" / "raw" / handle
        p.mkdir(parents=True, exist_ok=True)
        return p

    def brand_images_processed_path(self, handle: str) -> Path:
        """
        Return the brand-level processed image directory for a product handle:
          outputs/{brand}/images/processed/{handle}/
        Creates the directory if it does not exist.
        """
        p = Path("outputs") / self.brand / "images" / "processed" / handle
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ------------------------------------------------------------------
    # Event log
    # ------------------------------------------------------------------

    def log_event(self, message: str) -> None:
        """
        Append a timestamped line to the internal event log.
        Events are written to run_summary.txt by save_summary().
        """
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._events.append(f"[{ts}] {message}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def save_summary(self, counts: Dict[str, Any]) -> None:
        """
        Write a human-readable run_summary.txt to the run folder.

        Args:
            counts: Dict of pipeline counters.  See module docstring for the
                    expected keys and which summary section each maps to.
                    Missing keys are silently omitted; sections are skipped
                    entirely when none of their keys are present.
        """
        runtime_s = (datetime.now() - self.start_time).total_seconds()

        # Work on a local copy — never mutate the caller's dict
        c = dict(counts)

        # ── Reconcile URL counts against the pre-limit total ──────────────
        # url_collector sets ctx._urls_collected_total to the full count before
        # any --limit is applied.  Use it to show the real number scraped.
        if self._urls_collected_total is not None:
            displayed_collected = self._urls_collected_total
            after_limit = c.get("urls_collected", self._urls_collected_total)
            c["urls_collected"] = displayed_collected
            if after_limit != displayed_collected:
                c["urls_after_limit"] = after_limit
        else:
            after_limit = c.get("urls_collected", 0)

        # ── Enrich "nothing new" runs with an actionable explanation ──────
        new_urls = c.get("new_urls", -1)
        processed = c.get("urls_after_limit", after_limit)
        if new_urls == 0 and processed > 0:
            csv_path, existing_count = _get_shopify_export_info(self.brand)
            if existing_count >= 0:
                explanation = (
                    f"All {processed} processed URLs already exist in the Shopify "
                    f"export ({csv_path}, {existing_count} products). "
                    "Run without --limit or wait for new products to appear on the site."
                )
                detail_event = (
                    f"Deduplication: {processed} URLs checked, 0 new — "
                    f"all {processed} already present in Shopify export "
                    f"({existing_count} existing products). Nothing to scrape."
                )
            else:
                explanation = (
                    f"All {processed} processed URLs already exist in the Shopify "
                    "export. Run without --limit or wait for new products."
                )
                detail_event = (
                    f"Deduplication: {processed} URLs checked, 0 new — "
                    "all already present in Shopify export. Nothing to scrape."
                )
            c["exit_reason"] = explanation
            # Replace the generic "No new products found" event with the richer one
            self._events = [
                (e.split("] ", 1)[0] + "] " + detail_event
                 if "No new products found" in e else e)
                for e in self._events
            ]

        # ── Build the summary text ─────────────────────────────────────────
        lines = [
            _BORDER,
            "SwiftCommerce Pipeline — Run Summary",
            _BORDER,
            _row("Brand",     self.brand,     _HDR_W),
            _row("Mode",      self.mode,      _HDR_W),
            _row("Timestamp", self.timestamp, _HDR_W),
            _row("Runtime",   f"{runtime_s:.2f}s", _HDR_W),
        ]

        # ── Counts ───────────────────────────────────────────────────────
        count_rows = [
            ("urls_collected",    "URLs collected"),
            ("urls_after_limit",  "URLs after limit"),
            ("new_urls",          "New URLs"),
            ("products_scraped",  "Products scraped"),
            ("products_exported", "Products exported"),
            ("images_downloaded", "Images downloaded"),
            ("images_processed",  "Images processed"),
        ]
        _append_section(lines, "Counts", count_rows, c)

        # ── Deduplication ────────────────────────────────────────────────
        dedup_rows = [
            ("dedup_matched_url", "Matched by URL"),
            ("dedup_matched_sku", "Matched by SKU"),
            ("dedup_new",         "New (unmatched)"),
        ]
        _append_section(lines, "Deduplication", dedup_rows, c)

        # ── Gender Assignment ────────────────────────────────────────────
        gender_rows = [
            ("gender_men",     "Men"),
            ("gender_women",   "Women"),
            ("gender_unisex",  "Unisex"),
            ("gender_unknown", "Unknown"),
        ]
        _append_section(lines, "Gender Assignment", gender_rows, c)

        # ── Inventory ────────────────────────────────────────────────────
        inventory_rows = [
            ("catalog_total",   "Catalog total"),
            ("catalog_new",     "New this run"),
            ("catalog_updated", "Updated this run"),
        ]
        _append_section(lines, "Inventory", inventory_rows, c)

        # ── Exit reason (only present when run ended early) ───────────────
        if "exit_reason" in c:
            lines.append("")
            lines.append("--- Note ---")
            lines.append(c["exit_reason"])

        # ── Events ───────────────────────────────────────────────────────
        lines.append("")
        lines.append("--- Events ---")
        if self._events:
            lines.extend(self._events)
        else:
            lines.append("(none)")

        lines.append(_BORDER)

        self.path("run_summary.txt").write_text(
            "\n".join(lines), encoding="utf-8"
        )
        logger.debug("run_summary.txt written to %s", self._run_folder)

    # ------------------------------------------------------------------
    # Copy to latest
    # ------------------------------------------------------------------

    def copy_to_latest(self) -> None:
        """
        Copy the run folder to outputs/{brand}/listing/latest/, overwriting any
        previous latest.

        Only runs in listing mode — drafting runs are not promoted to latest.
        Images are stored at the brand level (outputs/{brand}/images/) and are
        not part of the run folder, so no exclusion is needed.
        """
        if self.mode != "listing":
            return

        latest_dir = Path("outputs") / self.brand / "listing" / "latest"
        if latest_dir.exists():
            shutil.rmtree(latest_dir)
        latest_dir.mkdir(parents=True, exist_ok=True)

        for item in self._run_folder.iterdir():
            dest = latest_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        logger.debug("Copied run folder → %s", latest_dir)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _append_section(
    lines: list,
    title: str,
    rows: list,          # list of (count_key, display_label)
    counts: Dict[str, Any],
) -> None:
    """
    Append a titled section to lines if at least one of the rows has a value
    in counts.  Rows whose key is absent are silently skipped.
    """
    present = [(key, label) for key, label in rows if key in counts]
    if not present:
        return
    lines.append("")
    lines.append(f"--- {title} ---")
    for key, label in present:
        lines.append(_row(label, counts[key]))
