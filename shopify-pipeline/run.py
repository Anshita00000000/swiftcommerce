"""
run.py
Entry point for the Shopify product scraping pipeline.

Usage:
    python run.py --brand wolf1834 --mode listing
    python run.py --brand wolf1834 --mode drafting
    python run.py --brand wolf1834 --mode listing --limit 5
"""

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(brand: str) -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"{brand}_{date.today().isoformat()}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config(brand: str) -> dict:
    config_path = Path("config") / f"{brand}.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config not found: {config_path}\n"
            f"Please create config/{brand}.yaml based on config/_template.yaml"
        )
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# LISTING mode
# ---------------------------------------------------------------------------

def run_listing(brand: str, config: dict, limit: int = None) -> None:
    from pipeline.driver_factory import build_driver
    from pipeline.url_collector import collect_urls
    from pipeline.deduplicator import find_new_urls
    from pipeline.product_scraper import scrape_products
    from pipeline.image_downloader import download_images
    from pipeline.normalizer import normalize_products
    from pipeline.exporter import export_csv

    logger = logging.getLogger("run.listing")
    logger.info(f"=== LISTING MODE | brand={brand} | limit={limit} ===")

    driver = None
    try:
        driver = build_driver(config.get("anti_bot", {}))

        # Step 1: Collect all product URLs from category pages
        logger.info("Step 1: Collecting product URLs...")
        all_urls = collect_urls(driver, config, limit=limit)
        if not all_urls:
            logger.warning("No URLs collected. Exiting.")
            return

        # Step 2: Find new URLs not in Shopify export
        logger.info("Step 2: Deduplicating against Shopify export...")
        new_urls = find_new_urls(all_urls, brand)
        if not new_urls:
            logger.info("No new products found. Nothing to do.")
            return

        # Step 3: Scrape new products
        logger.info(f"Step 3: Scraping {len(new_urls)} new products...")
        raw_products = scrape_products(driver, new_urls, config)
        if not raw_products:
            logger.warning("No products scraped successfully.")
            return

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    # Step 4: Download images
    logger.info("Step 4: Downloading images...")
    image_map = {}  # {source_url: [local_paths]}
    for raw in raw_products:
        source_url = raw.get("source_url", "")
        images = raw.get("images", [])
        if images:
            local_paths = download_images(
                image_urls=images,
                handle=_url_to_handle(source_url),
                brand=brand,
                output_root="outputs",
                delay=config.get("anti_bot", {}).get("image_delay", 0.3),
            )
            image_map[source_url] = local_paths
        else:
            image_map[source_url] = []

    # Step 5: Normalize
    logger.info("Step 5: Normalizing products...")
    normalized = normalize_products(raw_products, config, image_map, brand)

    # Step 6: Export CSV
    out_path = Path("outputs") / brand / "new_products.csv"
    logger.info(f"Step 6: Exporting CSV to {out_path}...")
    export_csv(normalized, str(out_path))

    logger.info(f"=== LISTING COMPLETE: {len(normalized)} products → {out_path} ===")


# ---------------------------------------------------------------------------
# DRAFTING mode
# ---------------------------------------------------------------------------

def run_drafting(brand: str, config: dict, limit: int = None) -> None:
    from pipeline.driver_factory import build_driver
    from pipeline.url_collector import collect_urls
    from pipeline.drafter import build_draft_csv

    logger = logging.getLogger("run.drafting")
    logger.info(f"=== DRAFTING MODE | brand={brand} ===")

    driver = None
    try:
        driver = build_driver(config.get("anti_bot", {}))

        # Collect all live product URLs
        logger.info("Step 1: Collecting all live product URLs...")
        all_urls = collect_urls(driver, config, limit=limit)
        if not all_urls:
            logger.warning("No URLs collected. Cannot determine removed products.")
            return

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    # Find and export removed products
    logger.info("Step 2: Finding removed products and building draft CSV...")
    out_path = build_draft_csv(
        scraped_urls=all_urls,
        brand=brand,
        output_root="outputs",
        shopify_exports_dir="shopify_exports",
    )

    if out_path:
        logger.info(f"=== DRAFTING COMPLETE: draft CSV written to {out_path} ===")
    else:
        logger.info("=== DRAFTING COMPLETE: no products to draft ===")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _url_to_handle(url: str) -> str:
    """Derive a slug from the last path segment of a URL."""
    from slugify import slugify
    segment = url.rstrip("/").split("/")[-1]
    return slugify(segment) or slugify(url)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Shopify product scraping pipeline"
    )
    parser.add_argument("--brand", required=True, help="Brand name (e.g. wolf1834)")
    parser.add_argument(
        "--mode",
        required=True,
        choices=["listing", "drafting"],
        help="Pipeline mode",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of product URLs to process (for testing)",
    )
    args = parser.parse_args()

    setup_logging(args.brand)
    logger = logging.getLogger("run")

    # Ensure we run from the project root (where run.py lives)
    script_dir = Path(__file__).parent
    os.chdir(script_dir)

    try:
        config = load_config(args.brand)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if args.mode == "listing":
        run_listing(args.brand, config, limit=args.limit)
    elif args.mode == "drafting":
        run_drafting(args.brand, config, limit=args.limit)


if __name__ == "__main__":
    main()
