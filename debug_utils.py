from datetime import datetime
from pathlib import Path
from playwright.sync_api import Page
from config import DEBUG
import logging

logger = logging.getLogger("costex")


def dbg_dump(page: Page, tag: str, out_dir: Path):
    if not DEBUG:
        return
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        html = out_dir / f"dbg_{tag}_{ts}.html"
        png = out_dir / f"dbg_{tag}_{ts}.png"

        page.screenshot(path=str(png), full_page=True)
        html.write_text(page.content(), encoding="utf-8")

        logger.warning("DBG url=%s title=%r", page.url, page.title())
        logger.warning("DBG saved: %s | %s", html, png)
    except Exception:
        pass


def debug(msg: str):
    if DEBUG:
        logger.info(msg)
