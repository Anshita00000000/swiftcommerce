"""
run_context.py
Manages per-run output folders, event logging, and summary writing.
"""

import shutil
from datetime import datetime
from pathlib import Path


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
