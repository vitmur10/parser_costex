from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

LEIPARTS_HOME = "https://leiparts.com/"


def leiparts_extract_features_line(page: Page) -> str:
    """
    Extracts Data sheet features from product page and returns
    a single line like:
    "Voltage: 24V; Rotation: CW; Refrigerant: R134a"
    """
    features = []

    try:
        # Wait for features section to appear
        section = page.locator("section.product-features").first
        section.wait_for(state="visible", timeout=15000)
    except PWTimeout:
        print("[LEI] Features section not found")
        return ""

    names = page.locator("section.product-features dl.data-sheet dt.name")
    values = page.locator("section.product-features dl.data-sheet dd.value")

    count = min(names.count(), values.count())
    print(f"[LEI] features pairs found = {count}")

    for i in range(count):
        try:
            key = names.nth(i).inner_text().strip()
            val = values.nth(i).inner_text().strip()

            # normalize spaces
            key = " ".join(key.split())
            val = " ".join(val.split())

            if key and val:
                features.append(f"{key}: {val}")
        except Exception as e:
            print(f"[LEI] feature parse error index={i} err={e}")

    features_line = "; ".join(features)
    print(f"[LEI] features_line = {features_line}")
    return features_line


def leiparts_open_first_and_get_features(page: Page, part_no: str) -> str:
    """
    Full flow:
    1. Open leiparts
    2. Insert part_no into search
    3. Click first found product
    4. Return features as one line
    """
    part_no = part_no.strip()
    print(f"[LEI] Searching part_no = '{part_no}'")

    # 1. Go to homepage
    page.goto(LEIPARTS_HOME, wait_until="domcontentloaded")

    # 2. Find search input
    search_input = page.locator("input.search_query").first
    search_input.wait_for(state="visible", timeout=30000)

    # Fill search
    search_input.fill("")
    search_input.fill(part_no)

    try:
        real_value = search_input.input_value()
        print(f"[LEI] value in search input = '{real_value}'")
    except Exception:
        print("[LEI] cannot read input value")

    # 3. Submit search (OK button or Enter fallback)
    try:
        page.locator(".input-group-btn button[type='submit']").first.click(timeout=5000)
    except Exception:
        search_input.press("Enter")

    # Wait for results page
    try:
        page.wait_for_load_state("domcontentloaded", timeout=20000)
    except PWTimeout:
        print("[LEI] DOMContentLoaded timeout after search")

    # 4. Click first product card
    first_product = page.locator("article.product-miniature.js-product-miniature").first

    try:
        first_product.wait_for(state="visible", timeout=30000)
    except PWTimeout:
        print("[LEI] No product cards found (possibly no results)")
        return ""

    product_link = first_product.locator("h3.product-title a").first

    href = product_link.get_attribute("href")
    title = ""
    try:
        title = product_link.inner_text().strip()
    except Exception:
        pass

    print(f"[LEI] Opening first product: title='{title}' url='{href}'")

    product_link.click()

    try:
        page.wait_for_load_state("domcontentloaded", timeout=20000)
    except PWTimeout:
        print("[LEI] Timeout waiting product page load")

    print(f"[LEI] Product page opened: {page.url}")

    # 5. Extract features line
    return leiparts_extract_features_line(page)


# --- MANUAL TEST ---
if __name__ == "__main__":
    # ВСТАВ СЮДИ ТЕСТОВИЙ PART_NO
    TEST_PART_NO = "1065122"  # заміни на свій part_no

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # щоб бачити що відбувається
        context = browser.new_context()
        page = context.new_page()

        try:
            features = leiparts_open_first_and_get_features(page, TEST_PART_NO)
            print("\n=== FINAL FEATURES LINE ===")
            print(features)
        finally:
            browser.close()
