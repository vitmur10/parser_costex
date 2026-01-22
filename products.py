from playwright.sync_api import sync_playwright
import json
import csv
import os
import time


def short(s: str, n=300):
    s = s or ""
    s = s.replace("\n", "\\n")
    return s[:n] + ("..." if len(s) > n else "")


def log_network(
        url: str,
        seconds: int = 15,
        only_xhr: bool = True,
        only_ajax: bool = False,
        only_get_wdtable: bool = False,
        out_path="Products.csv",
        category_url: str | None = None,
        subcategory_name: str | None = None,
        append: bool = False,
):
    product_list = []
    captured = {"done": False}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        def should_log_request(req):
            if only_xhr and req.resource_type not in ("xhr", "fetch"):
                return False
            if only_ajax and ("admin-ajax.php" not in req.url):
                return False
            if only_get_wdtable and ("action=get_wdtable" not in req.url):
                return False
            return True

        def on_request(req):
            if not should_log_request(req):
                return
            pd = req.post_data or ""
        def on_response(resp):
            req = resp.request
            if not should_log_request(req):
                return
            if captured["done"]:
                return

            try:
                body_text = resp.text()
            except Exception as e:
                return

            try:
                js = json.loads(body_text)
            except Exception:
                js = None

            if not isinstance(js, dict) or "data" not in js:
                return

            captured["done"] = True

            # ✅ НЕ міняю логіку парсингу part_no
            parts = []
            for row in js.get("data", []):
                if isinstance(row, list) and len(row) > 1:
                    part = str(row[1]).strip()
                    if part:
                        parts.append(part)

            # ✅ NEW: беремо category_url + subcategory_name з параметрів (з CSV)
            # якщо не передали — fallback як було (h1/title)
            if not subcategory_name:
                try:
                    subcategory_name_local = page.locator("h1").first.inner_text(timeout=2000).strip()
                except Exception:
                    subcategory_name_local = (page.title() or "").strip()
            else:
                subcategory_name_local = subcategory_name

            category_url_local = category_url or url
            subcategory_url_local = url  # url = subcategory_url із CSV

            for part in parts:
                product_list.append({
                    "category_url": category_url_local,
                    "subcategory_name": subcategory_name_local,
                    "subcategory_url": subcategory_url_local,
                    "PART_NO": part
                })

            # ✅ ОДИН ФАЙЛ: append або write
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

            file_exists = os.path.exists(out_path)
            mode = "a" if append else "w"
            write_header = (not file_exists) or (not append)

            with open(out_path, mode, newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["category_url", "subcategory_name", "subcategory_url", "PART_NO"]
                )
                if write_header:
                    writer.writeheader()
                writer.writerows(product_list)


        page.on("request", on_request)
        page.on("response", on_response)

        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(seconds * 1000)

        browser.close()

    return product_list


def run_from_input_csv(input_csv: str, seconds: int = 20, limit: int | None = None, out_csv: str = "Products_ALL.csv"):
    # чистий старт
    if os.path.exists(out_csv):
        os.remove(out_csv)

    with open(input_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"category_url", "subcategory_name", "subcategory_url"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise RuntimeError(f"CSV має містити колонки: {sorted(required)}")

        for i, row in enumerate(reader, start=1):
            if limit is not None and i > limit:
                break

            cat_url = (row.get("category_url") or "").strip()
            sub_name = (row.get("subcategory_name") or "").strip()
            sub_url = (row.get("subcategory_url") or "").strip()
            if not sub_url:
                continue


            log_network(
                url=sub_url,
                seconds=seconds,
                only_xhr=False,
                only_ajax=True,
                only_get_wdtable=True,
                out_path=out_csv,  # ✅ один файл
                category_url=cat_url,
                subcategory_name=sub_name,
                append=True  # ✅ ДОПИСУЄМО
            )
