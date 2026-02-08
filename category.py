from __future__ import annotations
from debug_utils import dbg_dump, debug
import csv
import random
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, Page

from config import BASE_URL, USER_AGENTS


# =========================
# Utils
# =========================

def human_sleep(page: Page, a=1.2, b=3.5):
    page.wait_for_timeout(int(random.uniform(a, b) * 1000))




def make_page(p, headless: bool, variant: str = "stealth") -> tuple:
    """
    variant:
      - "basic": мінімум
      - "stealth": UA/headers/locale/tz + anti-webdriver
      - "headed": headless=False, але з тими ж налаштуваннями
    """
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]

    if variant == "headed":
        headless = False

    browser = p.chromium.launch(headless=headless, args=args)

    ua = random.choice(USER_AGENTS) if USER_AGENTS else None

    context = browser.new_context(
        user_agent=ua,
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="Europe/Kyiv",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9,uk;q=0.8",
        },
    )

    if variant in ("stealth", "headed"):
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

    page = context.new_page()
    # трохи більші таймаути — headless часто повільніше дорендерює
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


def scroll_until_loaded(page: Page, locator_css: str, max_rounds: int = 12):
    """
    Скролить вниз, доки кількість елементів не перестане зростати.
    Це майже завжди потрібно для Elementor listing у headless.
    """
    prev = -1
    stable_rounds = 0

    for _ in range(max_rounds):
        count = page.locator(locator_css).count()

        if count == prev:
            stable_rounds += 1
        else:
            stable_rounds = 0

        if stable_rounds >= 2:  # двічі підряд без росту — вважаємо завантаженим
            break

        prev = count
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(800)

    # повернемось нагору (іноді після цього елементи стають visible стабільніше)
    try:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
    except Exception:
        pass


# =========================
# Category / Subcategory
# =========================

def parser_category(out_path="categories.csv", url=None, headless=False, variant="stealth"):
    url = url or (BASE_URL + "ctp-products/")
    category_list = []
    seen = set()

    def normalize_costex_url(href: str) -> str | None:
        if not href:
            return None
        href = href.strip()

        if href.startswith(("mailto:", "tel:", "javascript:")) or href.startswith("#"):
            return None

        parsed = urlparse(href)

        if not parsed.netloc:
            if href.startswith("/"):
                href = "https://www.costex.com" + href
                parsed = urlparse(href)
            else:
                return None

        host = parsed.netloc.lower()
        if host not in ("www.costex.com", "costex.com"):
            return None

        path = parsed.path or "/"
        if path in ("/", "/ctp-products/", "/ctp-products"):
            return None

        parts = [p for p in path.split("/") if p]
        if len(parts) != 1:
            return None

        slug = parts[0].strip()
        if not slug:
            return None

        return f"https://www.costex.com/{slug}/"

    with sync_playwright() as p:
        browser, context, page = make_page(p, headless=headless, variant=variant)

        try:
            goto_with_retry(page, url, tries=3)
            human_sleep(page, 1.0, 2.0)

            links = page.locator("a[href]")
            count = links.count()

            for i in range(count):
                try:
                    a = links.nth(i)
                    href = (a.get_attribute("href") or "").strip()
                    clean = normalize_costex_url(href)
                    if not clean or clean in seen:
                        continue
                    seen.add(clean)
                    category_list.append({"url": clean})
                except Exception:
                    pass

            with open(out_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["url"])
                writer.writeheader()
                writer.writerows(category_list)

            return category_list

        except Exception:
            dbg_dump(page, "categories", out_dir="dbg")
            raise
        finally:
            browser.close()


def parser_subcategory(csv_path="categories.csv", out_path="subcategories.csv", headless=False, variant="stealth"):
    """
    Парсить підкатегорії з:
      https://www.costex.com/full-product-listing/
    """
    listing_url = BASE_URL + "full-product-listing/"

    subcategory_list = []
    seen = set()

    article_sel = "article.elementor-post.ctp-categories[role='listitem']"
    thumb_link_sel = "a.elementor-post__thumbnail__link"
    title_link_sel = "h3.elementor-post__title a"

    with sync_playwright() as p:
        browser, context, page = make_page(p, headless=headless, variant=variant)

        try:
            goto_with_retry(page, listing_url, tries=3)
            human_sleep(page, 1.0, 2.0)

            # 1) дочекаємось появи хоча б одного article у DOM
            try:
                page.wait_for_selector(article_sel, timeout=25_000, state="attached")
            except Exception:
                dbg_dump(page, "subcats_no_articles", out_dir="dbg")
                raise

            # 2) скролимо, щоб Elementor догрузив все (або максимум що встигає)
            scroll_until_loaded(page, article_sel)

            articles = page.locator(article_sel)
            count = articles.count()
            if count == 0:
                dbg_dump(page, "subcats_count0", out_dir="dbg")
                raise RuntimeError("No subcategory articles found (count=0). Possibly blocked or different DOM in headless.")

            for i in range(count):
                art = articles.nth(i)
                try:
                    href = ""
                    a_thumb = art.locator(thumb_link_sel).first
                    if a_thumb.count():
                        href = (a_thumb.get_attribute("href") or "").strip()

                    if not href:
                        a_title = art.locator(title_link_sel).first
                        if a_title.count():
                            href = (a_title.get_attribute("href") or "").strip()

                    if not href or href in seen:
                        continue
                    seen.add(href)

                    name = ""
                    a_title = art.locator(title_link_sel).first
                    if a_title.count():
                        name = (a_title.inner_text() or "").strip()

                    subcategory_list.append(
                        {
                            "subcategory_name": name,
                            "subcategory_url": href,
                        }
                    )
                except Exception:
                    pass

            with open(out_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["subcategory_name", "subcategory_url"])
                writer.writeheader()
                writer.writerows(subcategory_list)

            return subcategory_list

        except Exception:
            dbg_dump(page, "subcats_failed", out_dir="dbg")
            raise
        finally:
            browser.close()


# Нема автозапуску тут.
# Запускай з main.py або вручну:
# parser_subcategory(headless=True, variant="stealth")
