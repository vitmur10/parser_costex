from __future__ import annotations
from debug_utils import dbg_dump, debug
import csv
import json
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright, Page

from config import USER_AGENTS



# =========================
# Browser/context
# =========================

def make_page(p, headless: bool, variant: str = "stealth"):
    """
    variant:
      - "basic": мінімум
      - "stealth": UA/headers/locale/tz + anti-webdriver
      - "headed": форсить headless=False
    """
    if variant == "headed":
        headless = False

    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]

    browser = p.chromium.launch(
        headless=headless,
        channel="chrome",
        args=args,
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

        # трохи "анти-бот"
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

    page = context.new_page()
    page.set_default_timeout(30_000)
    page.set_default_navigation_timeout(45_000)

    return browser, context, page


def goto_with_retry(page: Page, url: str, tries: int = 3):
    last = None
    for i in range(1, tries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=25_000)
            except Exception:
                pass
            return
        except Exception as e:
            last = e
            print(f"[WARN] goto failed try={i}/{tries}: {e}")
            time.sleep(1.0)
    raise last


# =========================
# wpDataTables helpers
# =========================

PART_RE = re.compile(r"[A-Z0-9][A-Z0-9\-]{3,30}")  # обережний, без пробілів


def is_wdtable_response(resp) -> bool:
    """
    Ловимо admin-ajax відповіді, які можуть належати wpDataTables.
    Не прив'язуємось до action=get_wdtable, бо action часто інший.
    """
    try:
        req = resp.request
        url = (req.url or "").lower()
        if "admin-ajax.php" not in url:
            return False
        # найчастіше wpDataTables робить admin-ajax?action=...
        if "action=" in url:
            return True
        return False
    except Exception:
        return False


def trigger_wdatatable(page: Page):
    """
    Часто wpDataTables відправляє ajax тільки після взаємодії.
    Ці дії безпечні й збільшують шанс, що ajax піде.
    """
    # scroll вниз/вгору (ініціалізація/рендер)
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(800)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
    except Exception:
        pass

    # search input (dataTables_filter)
    try:
        s = page.locator(
            "input[type='search'], .dataTables_filter input, input[aria-controls]"
        ).first
        if s.count():
            s.click(timeout=800)
            s.fill("a")
            page.wait_for_timeout(350)
            s.fill("")
            page.wait_for_timeout(350)
    except Exception:
        pass

    # length select (dataTables_length)
    try:
        sel = page.locator(
            "select[name*='length'], .dataTables_length select"
        ).first
        if sel.count():
            # просто вибрати першу опцію
            sel.select_option(index=0)
            page.wait_for_timeout(300)
    except Exception:
        pass

    # клік по таблиці
    for css in ["table", ".wpDataTable", ".dataTable", ".wpdt-c", ".wpdatatable"]:
        try:
            t = page.locator(css).first
            if t.count():
                t.click(timeout=800)
                page.wait_for_timeout(250)
                break
        except Exception:
            pass


def extract_parts_from_any_payload(payload_text: str) -> list[str]:
    """
    Витягуємо PART_NO з різних форматів відповіді wpDataTables:
      - JSON: data / aaData / rows
      - fallback regex по всьому тексту
    """
    parts: set[str] = set()

    js: Any = None
    try:
        js = json.loads(payload_text)
    except Exception:
        js = None

    if isinstance(js, dict):
        for key in ("data", "aaData", "rows"):
            if key in js and isinstance(js[key], list):
                for row in js[key]:
                    if isinstance(row, list):
                        # часто part_no у 2-й колонці
                        for cell in row[:5]:
                            s = str(cell).strip()
                            if PART_RE.fullmatch(s):
                                parts.add(s)
                    elif isinstance(row, dict):
                        for v in row.values():
                            s = str(v).strip()
                            if PART_RE.fullmatch(s):
                                parts.add(s)

        # інколи part_no може бути у js["data"]["rows"] (вкладений)
        if not parts:
            for v in js.values():
                if isinstance(v, str) and PART_RE.fullmatch(v.strip()):
                    parts.add(v.strip())

    # якщо мало знайшли — regex по тексту
    if len(parts) < 3:
        for m in PART_RE.finditer(payload_text):
            parts.add(m.group(0))

    return list(parts)


# =========================
# Stage 3: capture parts
# =========================

def log_network(
    url: str,
    seconds: int = 15,  # fallback wait
    out_path: str = "Products_ALL.csv",
    category_url: str | None = None,
    subcategory_name: str | None = None,
    append: bool = True,
    headless: bool = True,
    variant: str = "stealth",
    max_wait_ms: int = 25_000,
) -> list[dict]:
    """
    Відкриває subcategory_url і намагається зловити wpDataTables ajax (admin-ajax.php).
    Витягує PART_NO і дописує у CSV.

    На відміну від старої версії:
      - збирає ВСІ ajax responses у вікні max_wait_ms
      - тригерить таблицю діями (scroll/search/select/click)
      - парсить parts із різних JSON-структур
    """
    product_list: list[dict] = []
    responses = []
    debug_ajax = os.getenv("DEBUG_AJAX", "0") == "1"

    with sync_playwright() as p:
        browser, context, page = make_page(p, headless=headless, variant=variant)

        def on_resp(resp):
            try:
                if is_wdtable_response(resp):
                    responses.append(resp)
                    if debug_ajax:
                        print("[AJAX]", resp.request.url)
            except Exception:
                pass

        page.on("response", on_resp)

        try:
            goto_with_retry(page, url, tries=3)

            # ✅ тригернемо wpDataTables
            trigger_wdatatable(page)

            # ✅ збираємо responses певний час
            page.wait_for_timeout(max_wait_ms)

            # fallback: якщо дуже мало — ще почекаємо seconds
            if len(responses) == 0 and seconds > 0:
                page.wait_for_timeout(seconds * 1000)

            if len(responses) == 0:
                print(f"[WARN] No admin-ajax responses captured for: {url}")
                dbg_dump(page, "stage3_no_ajax", out_dir="dbg")
                return []

            # ✅ парсимо parts із всіх responses (беремо останні — вони актуальніші)
            all_parts: set[str] = set()

            # останні відповіді часто містять актуальні дані
            for resp in responses[::-1]:
                try:
                    txt = resp.text()
                except Exception:
                    continue

                parts = extract_parts_from_any_payload(txt)
                for pno in parts:
                    all_parts.add(pno)

                # якщо вже багато — можна зупинитись
                if len(all_parts) >= 50:
                    break

            if not all_parts:
                print(f"[WARN] admin-ajax captured but no parts parsed: {url}")
                dbg_dump(page, "stage3_ajax_no_parts", out_dir="dbg")
                return []

            parts_sorted = sorted(all_parts)

            # назва підкатегорії
            if not subcategory_name:
                try:
                    subcategory_name_local = page.locator("h1").first.inner_text(timeout=2000).strip()
                except Exception:
                    subcategory_name_local = (page.title() or "").strip()
            else:
                subcategory_name_local = subcategory_name

            category_url_local = category_url or url
            subcategory_url_local = url

            for part in parts_sorted:
                product_list.append(
                    {
                        "category_url": category_url_local,
                        "subcategory_name": subcategory_name_local,
                        "subcategory_url": subcategory_url_local,
                        "PART_NO": part,
                    }
                )

            # ✅ завжди append (header гарантовано створюється в run_from_input_csv)
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            file_exists = os.path.exists(out_path)

            with open(out_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["category_url", "subcategory_name", "subcategory_url", "PART_NO"],
                )
                if not file_exists:
                    writer.writeheader()
                writer.writerows(product_list)

            return product_list

        finally:
            try:
                browser.close()
            except Exception:
                pass


def log_network_with_variants(
    url: str,
    seconds: int,
    out_path: str,
    category_url: str | None,
    subcategory_name: str | None,
    append: bool,
    headless: bool,
    variants: list[str],
) -> list[dict]:
    last_err = None
    for v in variants:
        try:
            return log_network(
                url=url,
                seconds=seconds,
                out_path=out_path,
                category_url=category_url,
                subcategory_name=subcategory_name,
                append=append,
                headless=headless,
                variant=v,
            )
        except Exception as e:
            last_err = e
            print(f"[WARN] variant failed ({v}): {e}")
            time.sleep(1.0)
    if last_err:
        raise last_err
    return []


def run_from_input_csv(
    input_csv: str,
    seconds: int = 20,
    limit: int | None = None,
    out_csv: str = "Products_ALL.csv",
    headless: bool = True,
    variants: list[str] | None = None,
):
    """
    Читає subcategories.csv і для кожної підкатегорії ловить PART_NO у out_csv.

    Підтримує variants:
      stealth -> basic -> headed
    """
    if variants is None:
        variants = ["stealth", "basic", "headed"]

    fieldnames = ["category_url", "subcategory_name", "subcategory_url", "PART_NO"]

    # чистий старт
    if os.path.exists(out_csv):
        os.remove(out_csv)

    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        writer.writeheader()

    with open(input_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"subcategory_name", "subcategory_url"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise RuntimeError(f"CSV має містити колонки: {sorted(required)}")

        for i, row in enumerate(reader, start=1):
            if limit is not None and i > limit:
                break

            cat_url = (row.get("category_url") or row.get("subcategory_url") or "").strip()
            sub_name = (row.get("subcategory_name") or "").strip()
            sub_url = (row.get("subcategory_url") or "").strip()
            if not sub_url:
                continue

            products = log_network_with_variants(
                url=sub_url,
                seconds=seconds,
                out_path=out_csv,
                category_url=cat_url,
                subcategory_name=sub_name,
                append=True,
                headless=headless,
                variants=variants,
            )

            if not products:
                print(f"[WARN] No parts captured for: {sub_url}")
