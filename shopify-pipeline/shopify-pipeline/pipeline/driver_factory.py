"""
driver_factory.py
Creates and configures an undetected ChromeDriver instance.
Browser is kept visible so the user can handle captchas manually.
"""

import logging
import re
import shutil
import subprocess
import undetected_chromedriver as uc

logger = logging.getLogger(__name__)

CHROME_PATHS = [
    # Linux
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
    # macOS
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    # Windows
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Users\anshi\AppData\Local\Google\Chrome\Application\chrome.exe",
]

def _get_chrome_major_version(binary: str) -> int:
    """Return the major version number of the Chrome binary (e.g. 145)."""
    try:
        out = subprocess.check_output([binary, "--version"], stderr=subprocess.DEVNULL).decode()
        match = re.search(r"(\d+)\.\d+\.\d+", out)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return 0


def _find_chrome() -> str:
    import os
    for path in CHROME_PATHS:
        if os.path.isfile(path):
            return path
    found = (
        shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
        # Windows
        or shutil.which("chrome")
    )
    return found or ""


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

    chrome_binary = _find_chrome()
    
    # 1. Detect the binary location
    if chrome_binary:
        options.binary_location = chrome_binary
        logger.info(f"Using Chrome binary: {chrome_binary}")
    else:
        logger.warning("Chrome binary not found — letting undetected_chromedriver auto-detect.")

    # 2. FORCE the version here so it doesn't get overwritten or set to None
    version_main = 147
    logger.info(f"Forcing ChromeDriver version: {version_main}")

    # 3. Now pass this to the driver
    driver = uc.Chrome(
        options=options,
        use_subprocess=True,
        version_main=version_main, # This will now definitely be 144
    )

    timeout = anti_bot.get("page_load_timeout", 60)
    driver.set_page_load_timeout(timeout)

    logger.info("Chrome launched successfully.")
    return driver
