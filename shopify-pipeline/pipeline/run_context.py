"""
run_context.py
Manages per-run output folders, event logging, and summary writing.
"""

import shutil
from datetime import datetime
from pathlib import Path


def _get_shopify_info(brand: str):
    """
    Return (csv_path_str, unique_product_count).
    Counts distinct Handle values in the Shopify export CSV.
    product_count is -1 if the file is missing or unreadable.
    """
    csv_path = Path("shopify_exports") / f"{brand}_shopify.csv"
    if not csv_path.exists():
        return str(csv_path), -1
    try:
        handles = set()
        with open(csv_path, encoding="utf-8", errors="replace") as f:
            header_line = f.readline()
            header = [h.strip().strip('"') for h in header_line.split(',')]
            try:
                handle_idx = header.index('Handle')
            except ValueError:
                handle_idx = 0
            for line in f:
                if not line.strip():
                    continue
                parts = line.split(',')
                if handle_idx < len(parts):
                    h = parts[handle_idx].strip().strip('"')
                    if h:
                        handles.add(h)
        return str(csv_path), len(handles)
    except Exception:
        return str(csv_path), -1


class RunContext:
    def __init__(self, brand: str, mode: str):
        self.brand = brand
        self.mode = mode
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.start_time = datetime.now()
        self._run_folder = Path("outputs") / brand / self.timestamp
        self._run_folder.mkdir(parents=True, exist_ok=True)
        self._events = []

    def path(self, filename: str) -> Path:
        """Return full path inside the run folder, creating parent dirs if needed."""
        p = self._run_folder / filename
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def log_event(self, message: str) -> None:
        """Append a timestamped line to the internal events list."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._events.append(f"[{ts}] {message}")

    def save_summary(self, counts: dict) -> None:
        """Write run_summary.txt to the run folder."""
        runtime = (datetime.now() - self.start_time).total_seconds()
        counts = dict(counts)  # work on a copy; don't mutate caller's dict

        # Bug 3: replace urls_collected with the real total found before --limit
        if hasattr(self, '_urls_collected_total'):
            urls_after_limit = counts.get('urls_collected', self._urls_collected_total)
            counts['urls_collected'] = self._urls_collected_total
            if urls_after_limit != self._urls_collected_total:
                counts['urls_after_limit'] = urls_after_limit

        # Bug 4: when new_urls is 0, add a clear exit_reason and improve the log event
        urls_after_limit = counts.get('urls_after_limit', counts.get('urls_collected', 0))
        new_urls = counts.get('new_urls', -1)
        if new_urls == 0 and urls_after_limit > 0:
            csv_filename, existing_count = _get_shopify_info(self.brand)
            if existing_count >= 0:
                exit_reason = (
                    f"All {urls_after_limit} processed URLs already exist in Shopify "
                    f"export ({csv_filename}, {existing_count} products). "
                    f"Run without --limit or add new products to the website "
                    f"to find new items."
                )
                detailed_event = (
                    f"Deduplication: {urls_after_limit} URLs checked, 0 new, "
                    f"{urls_after_limit} already in Shopify export "
                    f"({existing_count} existing products). Nothing to scrape."
                )
            else:
                exit_reason = (
                    f"All {urls_after_limit} processed URLs already exist in Shopify export. "
                    f"Run without --limit or add new products to the website "
                    f"to find new items."
                )
                detailed_event = (
                    f"Deduplication: {urls_after_limit} URLs checked, 0 new, "
                    f"all already in Shopify export. Nothing to scrape."
                )
            counts['exit_reason'] = exit_reason

            # Replace the generic "No new products found" event with the detailed one
            new_events = []
            for e in self._events:
                if 'No new products found' in e:
                    ts_prefix = e.split('] ', 1)[0] + '] ' if '] ' in e else ''
                    new_events.append(ts_prefix + detailed_event)
                else:
                    new_events.append(e)
            self._events = new_events

        lines = [
            f"brand: {self.brand}",
            f"mode: {self.mode}",
            f"timestamp: {self.timestamp}",
            "",
            "=== Counts ===",
        ]
        for k, v in counts.items():
            lines.append(f"{k}: {v}")
        lines.append("")
        lines.append("=== Events ===")
        lines.extend(self._events)
        lines.append("")
        lines.append(f"runtime_seconds: {runtime:.2f}")

        self.path("run_summary.txt").write_text("\n".join(lines), encoding="utf-8")

    def copy_to_latest(self) -> None:
        """Copy entire run folder contents to outputs/{brand}/latest/ (overwrite)."""
        latest_dir = Path("outputs") / self.brand / "latest"
        if latest_dir.exists():
            shutil.rmtree(latest_dir)
        shutil.copytree(self._run_folder, latest_dir)
