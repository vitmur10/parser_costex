import csv
import random
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from config import BASE_URL, SELECTOR


def human_sleep(page, a=1.2, b=3.5):
    """Рандомна затримка як у людини"""
    page.wait_for_timeout(int(random.uniform(a, b) * 1000))


def _as_class_selector(value: str) -> str:
    """
    У Selenium ти використовував By.CLASS_NAME.
    Тут робимо CSS селектор класу. Якщо раптом у конфігу передадуть не клас —
    залишимо як є (як CSS).
    """
    v = (value or "").strip()
    if not v:
        return v
    # якщо вже css (містить . # [ : пробіл) — не чіпаємо
    if any(ch in v for ch in [".", "#", "[", ":", " "]):
        return v
    return f".{v}"


def parser_category(out_path="categories.csv", url=None, headless=False):
    """
    Йде на BASE_URL + "ctp-products/" і збирає посилання категорій.
    Зберігає у categories.csv (колонка url).
    """
    url = url or (BASE_URL + "ctp-products/")

    category_sel = _as_class_selector(SELECTOR.get("category_path"))

    category_list = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        human_sleep(page, 1.5, 3.0)

        # Selenium: driver.find_elements(By.CLASS_NAME, SELECTOR["category_path"])
        categories = page.locator(category_sel)
        count = categories.count()

        for i in range(count):
            block = categories.nth(i)
            try:
                # як у тебе: img[width='257'] + a[href]
                img = block.locator("img[width='257']").first
                a = block.locator("a").first

                # якщо картинки нема — пропускаємо (як у Selenium try/except)
                if img.count() == 0 or a.count() == 0:
                    continue

                href = (a.get_attribute("href") or "").strip()
                if href:
                    category_list.append({"url": href})
            except Exception:
                pass

        # write csv
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["url"])
            writer.writeheader()
            writer.writerows(category_list)

        browser.close()

    return category_list


def parser_subcategory(csv_path="categories.csv", out_path="subcategories.csv", headless=False):
    """
    Читає categories.csv (колонка url),
    для кожної категорії парсить підкатегорії:
      - category_url
      - subcategory_name
      - subcategory_url
    Зберігає в subcategories.csv
    """
    subcategory_sel = _as_class_selector(SELECTOR.get("subcategory"))

    subcategory_list = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context()

            for row in reader:
                category_url = (row.get("url") or "").strip()
                if not category_url:
                    continue


                # як у Selenium: кожне посилання — в окремому драйвері
                page = context.new_page()

                try:
                    human_sleep(page, 2.0, 5.0)
                    page.goto(category_url, wait_until="domcontentloaded", timeout=60000)
                    human_sleep(page, 1.5, 3.0)

                    # Selenium: driver.find_elements(By.CLASS_NAME, SELECTOR["subcategory"])
                    blocks = page.locator(subcategory_sel)
                    bcount = blocks.count()

                    for bi in range(bcount):
                        block = blocks.nth(bi)
                        # Selenium: subcategory.find_elements(By.CSS_SELECTOR, "li")
                        lis = block.locator("li")
                        licount = lis.count()

                        for li_i in range(licount):
                            li = lis.nth(li_i)
                            human_sleep(page, 0.2, 0.8)

                            try:
                                a = li.locator("a").first
                                link = (a.get_attribute("href") or "").strip()
                                name = (li.inner_text() or "").strip()

                                if link:
                                    subcategory_list.append(
                                        {
                                            "category_url": category_url,
                                            "subcategory_name": name,
                                            "subcategory_url": link,
                                        }
                                    )
                            except Exception:
                                pass

                except PWTimeoutError as e:
                    pass
                except Exception as e:
                    pass
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass

                    # пауза між сесіями (як у Selenium driver.quit + sleep)
                    # робимо її після закриття сторінки
                    # (щоб не роздувати контекст)
                    # 3..6 сек
                    # (пауза на context рівні, але ок)
                    # якщо хочеш — можна зменшити
                    pass

            browser.close()

    # write csv
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["category_url", "subcategory_name", "subcategory_url"]
        )
        writer.writeheader()
        writer.writerows(subcategory_list)

    return subcategory_list


