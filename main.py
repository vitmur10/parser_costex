from __future__ import annotations

import csv
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from config import URL_LOGIN
from authorization import login
from deteil_product import (
    go_to_price_inquiry,
    fill_price_inquiry_form,
    open_detail_update_qty_and_collect,
)
from products import run_from_input_csv
from write import save_costex_results_xlsx, dedupe_results


# =========================
# Logging
# =========================

def setup_logging(
    log_dir: str = "logs",
    log_file: str = "costex_parser.log",
    level: str | None = None,
) -> logging.Logger:
    """
    Налаштування логів для сервера:
      - console handler
      - file handler logs/costex_parser.log
    Рівень: env LOG_LEVEL (INFO/DEBUG/WARNING/ERROR)
    """
    lvl = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    log_level = getattr(logging, lvl, logging.INFO)

    Path(log_dir).mkdir(parents=True, exist_ok=True)
    file_path = Path(log_dir) / log_file

    logger = logging.getLogger("costex")
    logger.setLevel(log_level)
    logger.propagate = False

    # щоб не дублювало хендлери при повторному імпорті
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(log_level)
    ch.setFormatter(fmt)

    fh = logging.FileHandler(file_path, encoding="utf-8")
    fh.setLevel(log_level)
    fh.setFormatter(fmt)

    logger.addHandler(ch)
    logger.addHandler(fh)

    logger.info("Logger initialized. level=%s file=%s", lvl, str(file_path))
    return logger


logger = setup_logging()


# =========================
# Stage 1/2: Categories + Subcategories
# (підтримує і Selenium-версію category.py, і Playwright-версію)
# =========================

def _run_parser_category(categories_csv: str = "categories.csv", headless: bool = True) -> None:
    """
    Підтримує 2 варіанти category.py:
    1) Playwright-версія: parser_category(out_path="categories.csv", headless=False, ...)
    2) Selenium-версія: create_driver(); parser_category(driver)  (пише categories.csv всередині)
    """
    import inspect
    import category as cat

    logger.info("Stage 1: categories -> %s (headless=%s)", categories_csv, headless)

    if hasattr(cat, "parser_category"):
        sig = inspect.signature(cat.parser_category)
        # Playwright-версія має out_path або headless
        if "out_path" in sig.parameters or "headless" in sig.parameters:
            cat.parser_category(out_path=categories_csv, headless=headless)
            logger.info("Stage 1 done (playwright category.py).")
            return

    # fallback Selenium
    if not hasattr(cat, "create_driver"):
        raise RuntimeError("category.py: не знайдено create_driver() для Selenium fallback")

    driver = cat.create_driver()
    try:
        cat.parser_category(driver)  # selenium-версія сама пише categories.csv
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    if categories_csv != "categories.csv":
        Path("categories.csv").rename(categories_csv)

    logger.info("Stage 1 done (selenium fallback).")


def _run_parser_subcategory(
    categories_csv: str = "categories.csv",
    subcategories_csv: str = "subcategories.csv",
    headless: bool = False,
) -> None:
    """
    Підтримує:
    - Playwright-версія: parser_subcategory(csv_path=..., out_path=..., headless=...)
    - Selenium-версія: parser_subcategory(csv_path=..., out_path=...)
    """
    import inspect
    import category as cat

    logger.info("Stage 2: subcategories (%s -> %s) (headless=%s)", categories_csv, subcategories_csv, headless)

    if not hasattr(cat, "parser_subcategory"):
        raise RuntimeError("category.py: не знайдено parser_subcategory()")

    sig = inspect.signature(cat.parser_subcategory)
    kwargs = {"csv_path": categories_csv, "out_path": subcategories_csv}
    if "headless" in sig.parameters:
        kwargs["headless"] = headless
    cat.parser_subcategory(**kwargs)

    logger.info("Stage 2 done.")


# =========================
# Stage 4 helpers (detail -> rows)
# =========================

def iter_parts_from_csv(csv_path: str, limit: int | None = None):
    """
    Очікує колонки: category_url, subcategory_name, subcategory_url, PART_NO
    subcategory_name НЕ пишемо в excel, але можемо використати в логах.
    """
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        count = 0
        for row in reader:
            part_no = (row.get("PART_NO") or "").strip()
            if not part_no:
                continue

            yield {
                "category_url": (row.get("category_url") or "").strip(),
                "subcategory_name": (row.get("subcategory_name") or "").strip(),  # тільки лог
                "subcategory_url": (row.get("subcategory_url") or "").strip(),
                "part_no": part_no,  # fallback
            }

            count += 1
            if limit is not None and count >= limit:
                break


def _money_to_float(s: str) -> float | None:
    if not s:
        return None
    s = str(s).replace(",", "")
    m = re.search(r"(-?\d+(\.\d+)?)", s)
    return float(m.group(1)) if m else None


def _calc_totals(row: dict) -> dict:
    """
    Додає:
    - Requested Qty (9999)
    - Total Price (Requested) = Unit Price * Requested Qty
    - Total Price (Available) = Unit Price * Qty Available
    """
    if not row.get("Requested Qty"):
        row["Requested Qty"] = 9999

    unit_price = _money_to_float(row.get("Unit Price", ""))
    req_qty = row.get("Requested Qty")
    qty_av = row.get("Qty Available")

    try:
        req_qty_i = int(req_qty) if req_qty is not None else None
    except Exception:
        req_qty_i = None

    try:
        qty_av_i = int(qty_av) if qty_av is not None else None
    except Exception:
        qty_av_i = None

    if unit_price is not None and req_qty_i is not None:
        row["Total Price (Requested)"] = round(unit_price * req_qty_i, 2)

    if unit_price is not None and qty_av_i is not None:
        row["Total Price (Available)"] = round(unit_price * qty_av_i, 2)

    return row


def normalize_price_rows(item: dict, price_data: dict) -> list[dict]:
    """
    Перетворює price_data в список рядків для Excel.
    - detail_view -> один рядок (meta + price-table)
    - modal -> багато рядків (по кожній локації)

    В Excel НЕ пишемо:
      - subcategory_name
      - mode
      - Part No (бо буде part_no)
    """
    base = {
        "category_url": item.get("category_url", ""),
        "subcategory_url": item.get("subcategory_url", ""),
    }

    # part_no беремо з detail (Part No), fallback на CSV
    part_from_detail = (price_data.get("Part No") or price_data.get("part_no") or "").strip()
    base["part_no"] = part_from_detail if part_from_detail else item.get("part_no", "")

    base["Requested Qty"] = 9999

    mode = price_data.get("mode")
    if mode == "modal":
        out = []
        for r in (price_data.get("rows") or []):
            row = {**base, **r}
            out.append(_calc_totals(row))
        return out or [_calc_totals({**base})]

    cleaned = {k: v for k, v in price_data.items() if k not in ("rows", "mode")}
    cleaned.pop("Part No", None)  # прибираємо дубль
    row = {**base, **cleaned}
    return [_calc_totals(row)]


# =========================
# Full Pipeline
# =========================

def run_full_pipeline(
    *,
    headless_categories: bool = False,
    headless_detail: bool = True,  # на сервері зазвичай True
    categories_csv: str = "categories.csv",
    subcategories_csv: str = "subcategories.csv",
    products_csv: str = "Products_ALL.csv",
    limit_categories: int | None = None,
    limit_subcategories: int | None = None,
    limit_parts_detail: int | None = None,
    sniff_seconds: int = 20,
    out_dir: str = ".",
):
    """
    Порядок:
      1) categories -> categories.csv
      2) subcategories -> subcategories.csv
      3) products sniff (ajax) -> Products_ALL.csv
      4) detail parse + delete each -> xlsx (latest + dated + archive)
    """
    started = datetime.now()
    logger.info("Pipeline start. out_dir=%s", out_dir)

    # 1) Categories
    _run_parser_category(categories_csv=categories_csv, headless=headless_categories)

    # optionally trim categories
    if limit_categories is not None:
        logger.info("Trim categories to limit=%s", limit_categories)
        rows = []
        with open(categories_csv, newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for i, row in enumerate(r, start=1):
                rows.append(row)
                if i >= limit_categories:
                    break
        with open(categories_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["url"])
            w.writeheader()
            w.writerows(rows)

    # 2) Subcategories
    _run_parser_subcategory(
        categories_csv=categories_csv,
        subcategories_csv=subcategories_csv,
        headless=headless_categories,
    )

    # 3) Products list (PART_NO)
    logger.info(
        "Stage 3: products sniff (%s -> %s) seconds=%s limit_subcategories=%s",
        subcategories_csv, products_csv, sniff_seconds, limit_subcategories
    )
    run_from_input_csv(
        input_csv=subcategories_csv,
        seconds=sniff_seconds,
        limit=limit_subcategories,
        out_csv=products_csv,
    )
    logger.info("Stage 3 done.")

    # 4) Detail (login once, price inquiry once)
    results: list[dict[str, Any]] = []
    logger.info("Stage 4: detail parsing from %s (limit_parts=%s)", products_csv, limit_parts_detail)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless_detail)
        context = browser.new_context()
        page = context.new_page()

        logger.info("Login...")
        login(page, URL_LOGIN)

        logger.info("Go to Price Inquiry...")
        go_to_price_inquiry(page)

        for idx, item in enumerate(iter_parts_from_csv(products_csv, limit=limit_parts_detail), start=1):
            part_no = item["part_no"]
            sub_url = item["subcategory_url"]
            sub_name = item["subcategory_name"]

            logger.info("Detail [%s]: part_no=%s | sub=%s | url=%s", idx, part_no, sub_name, sub_url)

            fill_price_inquiry_form(page, part_number=part_no)
            price_data = open_detail_update_qty_and_collect(page)

            part_from_detail = price_data.get("Part No")
            if part_from_detail and part_from_detail != part_no:
                logger.debug("Part corrected by detail: csv=%s detail=%s", part_no, part_from_detail)

            results.extend(normalize_price_rows(item, price_data))

        browser.close()

    # 5) Dedupe + Save XLSX
    logger.info("Dedupe results...")
    results = dedupe_results(results)

    logger.info("Save XLSX...")
    latest_path, dated_path = save_costex_results_xlsx(results, out_dir=out_dir)

    elapsed = (datetime.now() - started).total_seconds()
    logger.info("Pipeline done. elapsed_sec=%.1f latest=%s dated=%s", elapsed, latest_path, dated_path)


if __name__ == "__main__":
    # Для сервера зазвичай:
    # LOG_LEVEL=INFO (або DEBUG)
    # headless_detail=True
    run_full_pipeline(
        limit_categories=None,
        limit_subcategories=None,
        limit_parts_detail=None,
        sniff_seconds=20,
        headless_categories=True,
        headless_detail=True,
        out_dir=".",
    )
