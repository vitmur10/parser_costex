from datetime import datetime
from pathlib import Path
from playwright.sync_api import Page
from config import DEBUG
import logging
import time
import os
logger = logging.getLogger("costex")


def dbg_dump(page, name: str, out_dir: str | None = None):
    """
    Debug dump that must never crash.
    Saves screenshot + html.
    """
    out_dir = out_dir or os.getenv("DBG_DIR") or "dbg"

    # окрема папка під кожен дамп
    dump_dir = Path(out_dir) / time.strftime("%Y%m%d_%H%M%S")
    dump_dir.mkdir(parents=True, exist_ok=True)

    # 1) screenshot
    try:
        page.screenshot(path=str(dump_dir / f"{name}.png"), full_page=True)
    except Exception:
        pass

    # 2) html
    try:
        (dump_dir / f"{name}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass

    # 3) (опційно) поточний url/title
    try:
        meta = f"url={page.url}\n"
        (dump_dir / f"{name}.txt").write_text(meta, encoding="utf-8")
    except Exception:
        pass


def debug(msg: str):
    if DEBUG:
        logger.info(msg)
