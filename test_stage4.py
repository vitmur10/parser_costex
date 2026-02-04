import csv
from pathlib import Path
from playwright.sync_api import sync_playwright

from authorization import login
from deteil_product import (
    fill_price_inquiry_form,
    open_detail_update_qty_and_collect,
    go_to_price_inquiry,
)

# ===== НАЛАШТУВАННЯ =====
PRODUCTS_CSV = Path("Products_ALL.csv")   # має вже існувати після Stage 3
LIMIT_PARTS = 5                           # скільки деталей тестуємо
HEADLESS = False                          # щоб бачити браузер

PAUSE_BETWEEN_PARTS_SEC = 3               # пауза між part_no
FINAL_PAUSE_SEC = 9999                    # фінальна пауза перед закриттям
STEP_MODE = False                         # True = чекати Enter після кожного part


def iter_part_numbers(csv_path: Path, limit: int):
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)

        print("CSV HEADERS:", reader.fieldnames)

        # 🔴 ЯВНО вказуємо колонку з part_no
        # ЗМІНИ тут, якщо назва інша
        PART_NO_KEY = "PART_NO"

        if PART_NO_KEY not in reader.fieldnames:
            raise RuntimeError(
                f"❌ У CSV немає колонки '{PART_NO_KEY}'. "
                f"Доступні колонки: {reader.fieldnames}"
            )

        for i, row in enumerate(reader, start=1):
            part_no = (row.get(PART_NO_KEY) or "").strip()

            if not part_no:
                raise RuntimeError(
                    f"❌ Порожній part_no у рядку #{i}. Рядок: {row}"
                )

            yield part_no, row

            if i >= limit:
                break


def main():
    if not PRODUCTS_CSV.exists():
        raise FileNotFoundError(
            f"{PRODUCTS_CSV} не знайдено. Спочатку один раз згенеруй його Stage 3."
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()
        page = context.new_page()

        print("== Login ==")
        login(page, "https://www.costex.com/ctp-online-login/")

        print("== Go to Price Inquiry ==")
        go_to_price_inquiry(page)

        for i, (part_no, row) in enumerate(iter_part_numbers(PRODUCTS_CSV, LIMIT_PARTS), start=1):
            print(f"\n--- TEST [{i}] part_no={part_no} ---")

            fill_price_inquiry_form(page, part_number=part_no)
            data = open_detail_update_qty_and_collect(page)

            print("RESULT:", data)

            # ===== ПАУЗА ПІСЛЯ КОЖНОГО PART =====
            if STEP_MODE:
                input("⏸ Натисни Enter, щоб перейти до наступного part...")
            else:
                page.wait_for_timeout(PAUSE_BETWEEN_PARTS_SEC * 1000)

        print("\n✅ Тестування завершено")

        # ===== ФІНАЛЬНА ПАУЗА =====
        if FINAL_PAUSE_SEC:
            print(f"⏸ Браузер залишиться відкритим {FINAL_PAUSE_SEC} сек.")
            try:
                page.wait_for_timeout(FINAL_PAUSE_SEC * 1000)
            except KeyboardInterrupt:
                print("⛔ Закрито вручну")

        context.close()
        browser.close()


if __name__ == "__main__":
    main()
