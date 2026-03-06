"""
driver_factory.py
Creates and configures an undetected ChromeDriver instance.
Browser is kept visible so the user can handle captchas manually.
"""

import logging
import undetected_chromedriver as uc
from selenium.webdriver.chrome.options import Options

logger = logging.getLogger(__name__)


def build_driver(anti_bot: dict = None) -> uc.Chrome:
    """
    Build and return an undetected Chrome WebDriver.

    Args:
        anti_bot: dict from YAML config with optional keys:
            - window_size: [width, height]  (default [1400, 900])
            - user_agent: custom UA string
            - page_load_timeout: seconds (default 60)

    Returns:
        Configured uc.Chrome instance (visible, not headless).
    """
    if anti_bot is None:
        anti_bot = {}

    options = uc.ChromeOptions()

    # Never headless — user may need to solve captchas
    window_size = anti_bot.get("window_size", [1400, 900])
    options.add_argument(f"--window-size={window_size[0]},{window_size[1]}")

    # Suppress automation flags
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    if "user_agent" in anti_bot:
        options.add_argument(f"--user-agent={anti_bot['user_agent']}")

    logger.info("Launching undetected Chrome (visible)...")
    driver = uc.Chrome(options=options, use_subprocess=True)

    timeout = anti_bot.get("page_load_timeout", 60)
    driver.set_page_load_timeout(timeout)

    logger.info("Chrome launched successfully.")
    return driver
