"""
run.py
Orchestration entry point for the SwiftCommerce Shopify pipeline.

Usage:
    python run.py --brand tissot --mode listing
    python run.py --brand tissot --mode drafting
    python run.py --brand tissot --mode listing --limit 5 --skip-images
"""

import argparse
import json
import logging
import os
import sys
import yaml
from datetime import date
from pathlib import Path

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
            f"Please create config/{brand}.yaml"
        )
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return adapt(raw)

# ---------------------------------------------------------------------------
# LISTING mode
# ---------------------------------------------------------------------------

def run_listing(brand: str, config: dict, limit: int = None, skip_images: bool = False) -> None:
    from pipeline.run_context import RunContext
    from pipeline.driver_factory import build_driver
    from pipeline.url_collector import collect_urls
    from pipeline.deduplicator import find_new_urls
    from pipeline.product_scraper import scrape_products
    from pipeline.image_downloader import download_images
    from pipeline.image_processor import process_images
    from pipeline.normalizer import normalize_products
    from pipeline.exporter import export_csv
    from pipeline.inventory_manager import update_inventory

    logger = logging.getLogger("run.listing")
    logger.info(f"=== STARTING LISTING MODE | brand={brand} | limit={limit} ===")

    # Step 1 — Create context
    ctx = RunContext(brand, "listing")

    counts = {
        "urls_collected": 0,
        "new_urls": 0,
        "products_scraped": 0,
        "images_downloaded": 0,
        "images_processed": 0,
        "products_normalized": 0,
        "products_exported": 0,
        "catalog_total": 0,
        "catalog_new": 0,
        "catalog_updated": 0,
    }

    # Step 2 — Collect URLs
    driver = None
    try:
        driver = build_driver(config.get("anti_bot", {}))
        logger.info("Step 1/8: Collecting product URLs...")
        all_urls, url_data_map = collect_urls(driver, config, limit=limit, ctx=ctx)
        counts["urls_collected"] = len(all_urls)
    finally:
        if driver:
            driver.quit()

    if not all_urls:
        logger.warning("No URLs collected — exiting.")
        ctx.log_event("No URLs collected — exiting early")
        ctx.save_summary(counts)
        ctx.copy_to_latest()
        return

    # Step 3 — Deduplicate
    logger.info("Step 2/8: Deduplicating against Shopify export...")
    dedup_result = find_new_urls(url_data_map, brand, ctx=ctx)
    counts["new_urls"] = len(dedup_result["new"])

    if not dedup_result["new"]:
        logger.info("No new products found. Nothing to scrape.")
        ctx.log_event("No new products found — exiting early")
        ctx.save_summary(counts)
        ctx.copy_to_latest()
        return

    # Step 4 — Scrape Products
    driver = None
    try:
        driver = build_driver(config.get("anti_bot", {}))
        logger.info(f"Step 3/8: Scraping {len(dedup_result['new'])} products...")
        raw_products = scrape_products(driver, dedup_result["new"], config, ctx=ctx)
        
        ctx.path("products_raw.json").write_text(
            json.dumps(raw_products, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        counts["products_scraped"] = len(raw_products)
    finally:
        if driver:
            driver.quit()

    if not raw_products:
        logger.warning("No products scraped. Exiting.")
        ctx.log_event("No products scraped — exiting early")
        ctx.save_summary(counts)
        ctx.copy_to_latest()
        return

    # Step 5 — Images
    image_map = {}
    if not skip_images:
        logger.info("Step 4/8: Downloading and processing images...")
        for raw in raw_products:
            source_url = raw.get("source_url", "")
            images = raw.get("images", [])
            if images:
                handle = _url_to_handle(source_url)
                raw_paths = download_images(
                    image_urls=images,
                    handle=handle,
                    ctx=ctx,
                    config=config
                )
                processed_paths = process_images(raw_paths, handle, ctx, sku=raw.get("sku", ""))
                
                # Prefer processed webp; fallback to raw paths if processing failed
                image_map[source_url] = processed_paths if processed_paths else raw_paths
                counts["images_downloaded"] += len(raw_paths)
                counts["images_processed"] += len(processed_paths)
            else:
                image_map[source_url] = []
    else:
        logger.info("Step 4/8: Skipping image download and processing (--skip-images)")
        for raw in raw_products:
            image_map[raw.get("source_url", "")] = raw.get("images", [])

    # Step 6 — Normalize
    logger.info("Step 5/8: Normalizing data for Shopify...")
    normalized = normalize_products(raw_products, config, image_map, brand, url_data_map)
    ctx.path("products_info.json").write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    counts["products_normalized"] = len(normalized)

    # Step 7 — Export CSV
    logger.info("Step 6/8: Exporting Shopify CSV...")
    csv_out = ctx.path("new_products_shopify.csv")
    export_csv(normalized, str(csv_out))
    counts["products_exported"] = len(normalized)

    # Step 8 — Update Inventory
    logger.info("Step 7/8: Updating master catalog...")
    try:
        inv_result = update_inventory(brand, normalized, ctx.timestamp)
        counts["catalog_total"] = inv_result.get("total_in_catalog", 0)
        counts["catalog_new"] = inv_result.get("new_this_run", 0)
        counts["catalog_updated"] = inv_result.get("updated_this_run", 0)
    except Exception as e:
        logger.error(f"Inventory update failed (non-fatal): {e}")
        ctx.log_event(f"Inventory update failed: {e}")

    # Step 9 — Wrap up
    logger.info("Step 8/8: Finalizing run folder...")
    ctx.save_summary(counts)
    ctx.copy_to_latest()
    logger.info(f"=== LISTING COMPLETE | Brand: {brand} | New Products: {len(normalized)} ===")

# ---------------------------------------------------------------------------
# DRAFTING mode
# ---------------------------------------------------------------------------

def run_drafting(brand: str, config: dict, limit: int = None) -> None:
    from pipeline.run_context import RunContext
    from pipeline.driver_factory import build_driver
    from pipeline.url_collector import collect_urls
    from pipeline.deduplicator import find_removed_products
    from pipeline.drafter import build_draft_output

    logger = logging.getLogger("run.drafting")
    logger.info(f"=== STARTING DRAFTING MODE | brand={brand} ===")

    # Step 1 — Create context
    ctx = RunContext(brand, "drafting")

    # Step 2 — Collect URLs
    driver = None
    try:
        driver = build_driver(config.get("anti_bot", {}))
        all_urls, url_data_map = collect_urls(driver, config, limit=limit, ctx=ctx)
    finally:
        if driver:
            driver.quit()

    if not all_urls:
        logger.warning("No URLs collected — cannot determine removed products.")
        return

    # Step 3 — Find removed products (writes links_to_draft.json)
    logger.info("Comparing live site against Shopify export...")
    removed_df = find_removed_products(all_urls, brand, ctx=ctx, total_live_scraped=len(all_urls))

    # Step 4 — Build draft output
    logger.info("Generating Draft CSV...")
    out_path = build_draft_output(brand, ctx)

    if out_path:
        logger.info(f"=== DRAFTING COMPLETE: {out_path} ===")
    else:
        logger.info("=== DRAFTING COMPLETE: no products to draft. ===")

    # Step 5 — Save summary
    # Try to get drafted count from the json file we just wrote
    drafted_count = 0
    draft_json = ctx.path("links_to_draft.json")
    if draft_json.exists():
        try:
            draft_data = json.loads(draft_json.read_text(encoding="utf-8"))
            drafted_count = draft_data.get("meta", {}).get("total_to_draft", 0)
        except Exception:
            pass

    ctx.save_summary({
        "urls_collected": len(all_urls),
        "products_drafted": drafted_count
    })

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _url_to_handle(url: str) -> str:
    from slugify import slugify
    # Get the last part of the URL (the slug)
    segment = url.rstrip("/").split("/")[-1]
    # Clean it up for Shopify
    return slugify(segment) or slugify(url)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="SwiftCommerce Product Pipeline")
    parser.add_argument("--brand", required=True, help="Brand slug (e.g. seiko, tissot)")
    parser.add_argument("--mode", required=True, choices=["listing", "drafting"])
    parser.add_argument("--limit", type=int, default=None, help="Limit URLs for testing")
    parser.add_argument("--skip-images", action="store_true", help="Skip image processing")
    args = parser.parse_args()

    setup_logging(args.brand)
    
    # Set working directory to project root
    os.chdir(Path(__file__).parent)

    try:
        config = load_config(args.brand)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if args.mode == "listing":
        run_listing(args.brand, config, limit=args.limit, skip_images=args.skip_images)
    elif args.mode == "drafting":
        run_drafting(args.brand, config, limit=args.limit)

if __name__ == "__main__":
    main()