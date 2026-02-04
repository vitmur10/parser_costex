from __future__ import annotations

import csv
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from debug_utils import dbg_dump, debug
from playwright.sync_api import sync_playwright, Page

import category as cat
from config import URL_LOGIN, USER_AGENTS, CREDENTIALS
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
    lvl = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    log_level = getattr(logging, lvl, logging.INFO)

    Path(log_dir).mkdir(parents=True, exist_ok=True)
    file_path = Path(log_dir) / log_file

    logger = logging.getLogger("costex")
    logger.setLevel(log_level)
    logger.propagate = False

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
# Incremental Stage4 storage
# =========================

def _jsonl_append(path: Path, rows: list[dict[str, Any]]) -> None:
    """Append rows to JSONL (one JSON dict per line)."""
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            try:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            except Exception:
                # as a fallback, stringify everything
                safe = {k: str(v) for k, v in (r or {}).items()}
                f.write(json.dumps(safe, ensure_ascii=False) + "\n")


def _jsonl_load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    out.append(obj)
            except Exception:
                continue
    return out


def _jsonl_processed_partnos(path: Path) -> set[str]:
    """Used for resume: if a part_no has already been written, we can skip it."""
    done: set[str] = set()
    for r in _jsonl_load(path):
        p = str(r.get("part_no") or "").strip()
        if p:
            done.add(p)
    return done


# =========================
# URL / Navigation tracing
# =========================

def _safe_page_url(page: Page | None) -> str:
    if page is None:
        return ""
    try:
        return page.url
    except Exception:
        return ""


def _safe_page_title(page: Page | None) -> str:
    if page is None:
        return ""
    try:
        return page.title()
    except Exception:
        return ""


def attach_page_tracing(page: Page, variant: str):
    """
    Логуємо реальні переходи/URL щоб не гадати де ми.
    Вмикається завжди (легке), але можна вимкнути env TRACE_NAV=0
    """
    if os.getenv("TRACE_NAV", "1").strip() in ("0", "false", "False"):
        return

    def on_framenavigated(frame):
        try:
            if frame == page.main_frame:
                logger.info("NAV[%s] -> %s", variant, frame.url)
        except Exception:
            pass

    def on_load():
        try:
            logger.info("LOAD[%s] url=%s title=%r", variant, _safe_page_url(page), _safe_page_title(page))
        except Exception:
            pass

    page.on("framenavigated", on_framenavigated)
    page.on("load", lambda: on_load())

    if os.getenv("TRACE_HTTP_ERRORS", "0").strip() in ("1", "true", "True"):
        def on_response(resp):
            try:
                st = resp.status
                if st >= 400:
                    logger.warning("HTTP[%s] %s %s", variant, st, resp.url)
            except Exception:
                pass
        page.on("response", on_response)






# =========================
# Stage 2 (Subcategories)
# =========================

def _run_parser_subcategory(subcategories_csv: str, headless: bool) -> None:
    logger.info("Stage 2: subcategories -> %s (headless=%s)", subcategories_csv, headless)

    if not hasattr(cat, "parser_subcategory"):
        raise RuntimeError("category.py: не знайдено parser_subcategory()")

    kwargs = {"csv_path": "categories.csv", "out_path": subcategories_csv}

    try:
        import inspect
        sig = inspect.signature(cat.parser_subcategory)
        if "headless" in sig.parameters:
            kwargs["headless"] = headless
        if "variant" in sig.parameters:
            kwargs["variant"] = "stealth"
    except Exception:
        pass

    cat.parser_subcategory(**kwargs)
    logger.info("Stage 2 done.")


# =========================
# Stage 4 helpers
# =========================

def iter_parts_from_csv(csv_path: str, limit: int | None = None):
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        count = 0
        for row in reader:
            part_no = (row.get("PART_NO") or "").strip()
            if not part_no:
                continue

            yield {
                "category_url": (row.get("category_url") or "").strip(),
                "subcategory_name": (row.get("subcategory_name") or "").strip(),
                "subcategory_url": (row.get("subcategory_url") or "").strip(),
                "part_no": part_no,
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
    base = {
        "category_url": item.get("category_url", ""),
        "subcategory_url": item.get("subcategory_url", ""),
        "Category": item.get("subcategory_name", ""),
        "Categories": item.get("subcategory_name", ""),
    }

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
    cleaned.pop("Part No", None)

    row = {**base, **cleaned}
    return [_calc_totals(row)]


# =========================
# Validators
# =========================

def _count_rows(csv_path: Path) -> int:
    if not csv_path.exists():
        return 0
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        return sum(1 for _ in r)


def _count_parts(csv_path: Path) -> int:
    if not csv_path.exists():
        return 0
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        return sum(1 for row in r if (row.get("PART_NO") or "").strip())


# =========================
# Login detectors & retry loop
# =========================

def is_cloudflare_challenge(page: Page) -> bool:
    try:
        t = (page.title() or "").lower()
    except Exception:
        t = ""
    try:
        u = (page.url or "").lower()
    except Exception:
        u = ""

    if "just a moment" in t or "трохи зачекайте" in t or "__cf_" in u or "cf_chl" in u:
        return True

    try:
        if page.locator("iframe[src*='challenges.cloudflare.com']").count() > 0:
            return True
        if page.locator("input[name='cf-turnstile-response']").count() > 0:
            return True
    except Exception:
        pass

    return False


def is_login_success(page: Page) -> bool:
    try:
        if page.locator('a.nav-link[href*="QuoteOnline/mainQuotePage.php"]').count() > 0:
            return True
    except Exception:
        pass

    try:
        if page.locator('a[href*="logout"], .logout, button[name*="logout"]').count() > 0:
            return True
    except Exception:
        pass

    return False


def login_with_retries(
    p,
    *,
    variant: str,
    headless_default: bool,
    out_dir: Path,
    max_attempts: int = 0,
) -> tuple:
    attempt = 0
    while True:
        attempt += 1
        logger.info("Login loop: variant=%s attempt=%s", variant, attempt)

        browser = None
        context = None
        page = None

        try:
            browser, context, page = create_browser_and_page(p, variant, headless_default)

            logger.info("Login... (target=%s)", URL_LOGIN)
            # authorization.login може мати різний підпис залежно від твоєї версії файлу:
            #   login(page, url)
            #   login(page, url, username, password) або з keyword args
            try:
                login(
                    page,
                    URL_LOGIN,
                    username=CREDENTIALS["login"],
                    password=CREDENTIALS["password"],
                )
            except TypeError:
                # fallback: старий підпис без username/password
                login(page, URL_LOGIN)

            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

            logger.info("After login: url=%s title=%r", _safe_page_url(page), _safe_page_title(page))

            if is_cloudflare_challenge(page):
                logger.warning("Cloudflare challenge detected. waiting...")
                dbg_dump(page, f"login_cf_{variant}_try{attempt}", out_dir / "dbg")
                try:
                    page.wait_for_timeout(20000)
                except Exception:
                    pass

            if is_login_success(page):
                logger.info("Login success confirmed. variant=%s attempt=%s", variant, attempt)
                return browser, context, page

            logger.warning("Login not confirmed. url=%s title=%r", _safe_page_url(page), _safe_page_title(page))
            dbg_dump(page, f"login_fail_{variant}_try{attempt}", out_dir / "dbg")

        except Exception as e:
            logger.exception(
                "Login attempt failed variant=%s attempt=%s err=%s (url=%s title=%r)",
                variant,
                attempt,
                e,
                _safe_page_url(page),
                _safe_page_title(page),
            )
            if page is not None:
                dbg_dump(page, f"login_exc_{variant}_try{attempt}", out_dir / "dbg")

        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass

        if max_attempts and attempt >= max_attempts:
            raise RuntimeError(f"Login failed after max_attempts={max_attempts} variant={variant}")

        sleep_s = random.uniform(2.0, 8.0)
        logger.info("Retrying login in %.1fs...", sleep_s)
        time.sleep(sleep_s)


# =========================
# Browser variants (Stage 4)
# =========================

def create_browser_and_page(p, variant: str, headless_default: bool) -> tuple:
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]

    headless = headless_default
    if variant == "headed":
        headless = False

    browser = p.chromium.launch(
        headless=headless,
        channel="chrome",
        args=args,
        slow_mo=int(os.getenv("PW_SLOW_MO_MS", "0")),
    )

    ua = random.choice(USER_AGENTS) if USER_AGENTS else None

    if variant == "basic":
        context = browser.new_context()
    else:
        context = browser.new_context(
            user_agent=ua,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="Europe/Kyiv",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9,uk;q=0.8",
            },
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

    page = context.new_page()
    page.set_default_timeout(int(os.getenv("PW_TIMEOUT_MS", "30000")))
    page.set_default_navigation_timeout(int(os.getenv("PW_NAV_TIMEOUT_MS", "45000")))

    attach_page_tracing(page, variant)
    return browser, context, page


def run_stage4_with_variants(
    products_csv: Path,
    limit_parts_detail: int | None,
    out_dir: Path,
    headless_default: bool,
    variants: list[str],
) -> list[dict[str, Any]]:
    last_err = None

    # incremental file (so we don't lose progress if the run crashes)
    partial_path = out_dir / "stage4_partial.jsonl"
    resume = os.getenv("RESUME_STAGE4", "1").strip().lower() not in ("0", "false", "no")

    for variant in variants:
        logger.info("Stage 4 attempt: variant=%s headless_default=%s", variant, headless_default)
        results: list[dict[str, Any]] = []  # still kept for final return, but we also write to JSONL

        with sync_playwright() as p:
            browser = None
            page = None
            try:
                max_attempts = int(os.getenv("LOGIN_MAX_ATTEMPTS", "0"))  # 0 => без ліміту
                browser, context, page = login_with_retries(
                    p,
                    variant=variant,
                    headless_default=headless_default,
                    out_dir=out_dir,
                    max_attempts=max_attempts,
                )

                logger.info("Go to Price Inquiry... current_url=%s", _safe_page_url(page))
                go_to_price_inquiry(page)
                logger.info("After go_to_price_inquiry: url=%s title=%r", _safe_page_url(page), _safe_page_title(page))

                processed = _jsonl_processed_partnos(partial_path) if resume else set()
                if processed:
                    logger.info("Stage 4 resume enabled. already_processed_parts=%s", len(processed))

                for idx, item in enumerate(iter_parts_from_csv(str(products_csv), limit=limit_parts_detail), start=1):
                    part_no = item["part_no"]

                    if resume and part_no in processed:
                        logger.info("Skip already processed part_no=%s", part_no)
                        continue

                    logger.info("Detail [%s] variant=%s part_no=%s url=%s", idx, variant, part_no, _safe_page_url(page))

                    fill_price_inquiry_form(page, part_number=part_no)
                    logger.info("After fill form: url=%s", _safe_page_url(page))

                    price_data = open_detail_update_qty_and_collect(page)
                    logger.info("After collect: url=%s", _safe_page_url(page))

                    new_rows = normalize_price_rows(item, price_data)
                    results.extend(new_rows)

                    # ✅ incremental save after each part
                    _jsonl_append(partial_path, new_rows)
                    processed.add(part_no)

                try:
                    browser.close()
                except Exception:
                    pass

                # Prefer JSONL as the source of truth (includes everything saved incrementally)
                all_rows = _jsonl_load(partial_path)
                logger.info("Stage 4 success: variant=%s rows_in_memory=%s rows_in_jsonl=%s", variant, len(results), len(all_rows))
                return all_rows or results

            except Exception as e:
                last_err = e
                logger.exception(
                    "Stage 4 failed variant=%s: %s (url=%s title=%r)",
                    variant,
                    e,
                    _safe_page_url(page),
                    _safe_page_title(page),
                )
                if page is not None:
                    dbg_dump(page, f"stage4_{variant}", out_dir / "dbg")

                try:
                    if browser is not None:
                        browser.close()
                except Exception:
                    pass

                time.sleep(1.0)

    raise RuntimeError(f"Stage 4 failed for all variants={variants}. last_err={last_err}")


# =========================
# Full Pipeline
# =========================

def run_full_pipeline(
    *,
    headless_subcategories: bool = True,
    headless_products: bool = True,
    headless_detail: bool = True,
    limit_subcategories: int | None = None,
    limit_parts_detail: int | None = None,
    sniff_seconds: int = 20,
    out_dir: str = ".",
):
    started = datetime.now()

    BASE_DIR = Path(__file__).resolve().parent
    OUT_DIR = Path(out_dir) if out_dir not in (".", "", None) else BASE_DIR
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    subcategories_csv = OUT_DIR / "subcategories.csv"
    products_csv = OUT_DIR / "Products_ALL.csv"

    logger.info("Pipeline start. out_dir=%s", str(OUT_DIR))

    _run_parser_subcategory(subcategories_csv=str(subcategories_csv), headless=headless_subcategories)

    sub_rows = _count_rows(subcategories_csv)
    if sub_rows == 0:
        raise RuntimeError(f"Stage 2 produced 0 subcategories. File={subcategories_csv.resolve()}")
    logger.info("Stage 2 validated. subcategories_rows=%s", sub_rows)

    if limit_subcategories is not None:
        logger.info("Trim subcategories to limit=%s", limit_subcategories)
        rows = []
        with open(subcategories_csv, newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for i, row in enumerate(r, start=1):
                rows.append(row)
                if i >= limit_subcategories:
                    break
        with open(subcategories_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["category_url", "subcategory_name", "subcategory_url"])
            w.writeheader()
            w.writerows(rows)

    logger.info(
        "Stage 3: products sniff (%s -> %s) seconds=%s",
        str(subcategories_csv),
        str(products_csv),
        sniff_seconds,
    )
    run_from_input_csv(
        input_csv=str(subcategories_csv),
        seconds=sniff_seconds,
        limit=None,
        out_csv=str(products_csv),
        headless=headless_products,
        variants=["stealth", "basic", "headed"],
    )

    parts_count = _count_parts(products_csv)
    if parts_count == 0:
        raise RuntimeError(
            f"Stage 3 produced 0 PART_NO. "
            f"Check ajax filter/timing/Cloudflare. File={products_csv.resolve()}"
        )
    logger.info("Stage 3 validated. parts_count=%s", parts_count)

    variants_env = os.getenv("STAGE4_VARIANTS", "").strip()
    if variants_env:
        variants = [v.strip() for v in variants_env.split(",") if v.strip()]
    else:
        variants = ["stealth", "basic", "headed"]

    logger.info("Stage 4: detail parsing variants=%s limit_parts=%s", variants, limit_parts_detail)

    # Якщо хочеш стартувати Stage 4 з нуля (не резюмити), постав:
    #   RESUME_STAGE4=0  або  CLEAR_STAGE4=1
    partial_path = OUT_DIR / "stage4_partial.jsonl"
    if os.getenv("CLEAR_STAGE4", "0").strip().lower() in ("1", "true", "yes"):
        try:
            partial_path.unlink(missing_ok=True)
            logger.info("Stage 4 partial cleared: %s", str(partial_path))
        except Exception:
            pass
    elif os.getenv("RESUME_STAGE4", "1").strip().lower() in ("0", "false", "no"):
        try:
            partial_path.unlink(missing_ok=True)
            logger.info("Stage 4 resume disabled -> partial cleared: %s", str(partial_path))
        except Exception:
            pass

    results = run_stage4_with_variants(
        products_csv=products_csv,
        limit_parts_detail=limit_parts_detail,
        out_dir=OUT_DIR,
        headless_default=headless_detail,
        variants=variants,
    )

    logger.info("Dedupe results...")
    results = dedupe_results(results)

    logger.info("Save XLSX...")
    latest_path, dated_path = save_costex_results_xlsx(results, out_dir=OUT_DIR)

    elapsed = (datetime.now() - started).total_seconds()
    logger.info("Pipeline done. elapsed_sec=%.1f latest=%s dated=%s", elapsed, latest_path, dated_path)


if __name__ == "__main__":
    run_full_pipeline(
        limit_subcategories=None,
        limit_parts_detail=None,
        sniff_seconds=20,
        headless_subcategories=True,
        headless_products=True,
        headless_detail=True,
        out_dir=".",
    )
