"""
run.py
Entry point for the Shopify product scraping pipeline.

Usage:
    python run.py --brand seiko --mode listing
    python run.py --brand seiko --mode drafting
    python run.py --brand seiko --mode listing --limit 5
"""

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

import yaml

from pipeline.config_adapter import adapt


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
        raw = yaml.safe_load(f)
    # Translate brand YAML format -> internal pipeline format
    return adapt(raw)


# ---------------------------------------------------------------------------
# LISTING mode
# ---------------------------------------------------------------------------

def run_listing(brand: str, config: dict, mode: str = "listing", limit: int = None) -> None:
    from pipeline.run_context import RunContext
    from pipeline.inventory_manager import update_inventory
    from pipeline.driver_factory import build_driver
    from pipeline.url_collector import collect_urls
    from pipeline.deduplicator import find_new_urls
    from pipeline.product_scraper import scrape_products
    from pipeline.image_downloader import download_images
    from pipeline.normalizer import normalize_products
    from pipeline.exporter import export_csv

    logger = logging.getLogger("run.listing")
    logger.info(f"=== LISTING MODE | brand={brand} | limit={limit} ===")

    ctx = RunContext(brand, mode)

    counts = {
        "urls_collected": 0,
        "new_urls": 0,
        "products_scraped": 0,
        "products_exported": 0,
        "images_downloaded": 0,
        "catalog_total": 0,
        "catalog_new": 0,
        "catalog_updated": 0,
    }

    driver = None
    try:
        driver = build_driver(config.get("anti_bot", {}))

        logger.info("Step 1: Collecting product URLs...")
        all_urls, url_gender_map = collect_urls(driver, config, limit=limit)

        # Save urls_collected.txt — one URL per line, with gender label if known
        url_lines = []
        for url in all_urls:
            gender = url_gender_map.get(url, "")
            if gender:
                url_lines.append(f"{url}  [{gender}]")
            else:
                url_lines.append(url)
        ctx.path("urls_collected.txt").write_text("\n".join(url_lines), encoding="utf-8")
        counts["urls_collected"] = len(all_urls)
        ctx.log_event(f"Collected {len(all_urls)} product URLs")

        if not all_urls:
            logger.warning("No URLs collected. Exiting.")
            ctx.log_event("No URLs collected — exiting early")
            ctx.save_summary(counts)
            ctx.copy_to_latest()
            return

        logger.info("Step 2: Deduplicating against Shopify export...")
        new_urls = find_new_urls(all_urls, brand)

        # Save new_urls.txt — count header comment then one URL per line
        n = len(new_urls)
        new_url_lines = [f"# {n} new URLs"] + new_urls
        ctx.path("new_urls.txt").write_text("\n".join(new_url_lines), encoding="utf-8")
        counts["new_urls"] = n
        ctx.log_event(f"Found {n} new URLs after deduplication")

        if not new_urls:
            logger.info("No new products found. Nothing to do.")
            ctx.log_event("No new products found — exiting early")
            ctx.save_summary(counts)
            ctx.copy_to_latest()
            return

        logger.info(f"Step 3: Scraping {len(new_urls)} new products...")
        raw_products = scrape_products(driver, new_urls, config)

        # Save scraped_products.json
        ctx.path("scraped_products.json").write_text(
            json.dumps(raw_products, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        counts["products_scraped"] = len(raw_products)
        ctx.log_event(f"Scraped {len(raw_products)} products")

        if not raw_products:
            logger.warning("No products scraped successfully.")
            ctx.log_event("No products scraped — exiting early")
            ctx.save_summary(counts)
            ctx.copy_to_latest()
            return

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    logger.info("Step 4: Downloading images...")
    image_map = {}
    images_downloaded = 0
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
            images_downloaded += len(local_paths)
        else:
            image_map[source_url] = []
    counts["images_downloaded"] = images_downloaded
    ctx.log_event(f"Downloaded {images_downloaded} images")

    logger.info("Step 5: Normalizing products...")
    normalized = normalize_products(raw_products, config, image_map, brand, url_gender_map)

    # Export CSV to both: run folder and flat outputs/{brand}/ location
    run_csv_path = ctx.path("new_products.csv")
    flat_csv_path = Path("outputs") / brand / "new_products.csv"
    flat_csv_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Step 6: Exporting CSV...")
    export_csv(normalized, str(run_csv_path))
    export_csv(normalized, str(flat_csv_path))
    counts["products_exported"] = len(normalized)
    ctx.log_event(f"Exported {len(normalized)} products to CSV")

    logger.info(f"=== LISTING COMPLETE: {len(normalized)} products -> {flat_csv_path} ===")

    # Update master inventory — log error but do not crash if it fails
    try:
        inv_result = update_inventory(brand, normalized, ctx.timestamp)
        counts["catalog_total"] = inv_result["total_in_catalog"]
        counts["catalog_new"] = inv_result["new_this_run"]
        counts["catalog_updated"] = inv_result["updated_this_run"]
        ctx.log_event(
            f"Inventory updated: total={inv_result['total_in_catalog']}, "
            f"new={inv_result['new_this_run']}, updated={inv_result['updated_this_run']}"
        )
    except Exception as e:
        logger.error(f"Inventory update failed (non-fatal): {e}")
        ctx.log_event(f"Inventory update failed: {e}")

    ctx.save_summary(counts)
    ctx.copy_to_latest()


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

        logger.info("Step 1: Collecting all live product URLs...")
        all_urls, _ = collect_urls(driver, config, limit=limit)
        if not all_urls:
            logger.warning("No URLs collected. Cannot determine removed products.")
            return

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    logger.info("Step 2: Finding removed products and building draft CSV...")
    out_path = build_draft_csv(
        scraped_urls=all_urls,
        brand=brand,
        output_root="outputs",
        shopify_exports_dir="shopify_exports",
    )

    if out_path:
        logger.info(f"=== DRAFTING COMPLETE: draft CSV -> {out_path} ===")
    else:
        logger.info("=== DRAFTING COMPLETE: no products to draft ===")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _url_to_handle(url: str) -> str:
    from slugify import slugify
    segment = url.rstrip("/").split("/")[-1]
    return slugify(segment) or slugify(url)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Shopify product scraping pipeline")
    parser.add_argument("--brand", required=True, help="Brand name (e.g. seiko)")
    parser.add_argument("--mode", required=True, choices=["listing", "drafting"])
    parser.add_argument("--limit", type=int, default=None,
                        help="Max product URLs to process (for testing)")
    args = parser.parse_args()

    setup_logging(args.brand)

    # Always run from the directory containing run.py
    os.chdir(Path(__file__).parent)

    try:
        config = load_config(args.brand)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if args.mode == "listing":
        run_listing(args.brand, config, mode=args.mode, limit=args.limit)
    elif args.mode == "drafting":
        run_drafting(args.brand, config, limit=args.limit)


if __name__ == "__main__":
    main()
