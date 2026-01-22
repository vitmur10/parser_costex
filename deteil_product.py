from playwright.sync_api import Page, TimeoutError as PWTimeoutError
import time
import re
from urllib.parse import urljoin


# =========================
# Helpers
# =========================

def _first_int(s: str) -> int | None:
    m = re.search(r"(\d+)", (s or "").replace("\xa0", " "))
    return int(m.group(1)) if m else None


def _parse_money_to_float(s: str) -> float | None:
    # "$38.12" -> 38.12
    s = (s or "").strip()
    if not s:
        return None
    s = s.replace(",", "")
    m = re.search(r"(-?\d+(\.\d+)?)", s)
    return float(m.group(1)) if m else None


def extract_product_meta_from_detail(page: Page) -> dict:
    """
    Бере дані з блоку:
      <div class="product-detail ...">
        <h5>Title</h5>
        <p class="font-weight-bold">Part No. 1900649</p>
        ...
        <a class="delete-single-part" id="1900649|04|343.08|...|9|04">Delete</a>
      </div>

    Важливо: Qty Available беремо НАЙНАДІЙНІШЕ з id delete-single-part (parts[6]).
    """
    meta: dict = {}

    # Title
    title_loc = page.locator("div.product-detail h5").first
    if title_loc.count():
        meta["Title"] = title_loc.inner_text().strip()

    # Part No з "Part No. 1900649" (англ)
    part_no = None
    part_p = page.locator("div.product-detail p.font-weight-bold").first
    if part_p.count():
        part_no = _first_int(part_p.inner_text().strip())

    # Найнадійніше: id у a.delete-single-part
    del_a = page.locator("div.product-detail a.delete-single-part").first
    del_id = (del_a.get_attribute("id") or "").strip() if del_a.count() else ""
    if del_id:
        parts = del_id.split("|")

        # part_no з parts[0]
        if not part_no and len(parts) >= 1:
            part_no = _first_int(parts[0])

        # ✅ Qty Available з parts[6] (як у прикладі ...|293.7400|9|04)
        if len(parts) >= 7:
            qa = _first_int(parts[6])
            if qa is not None:
                meta["Qty Available"] = qa

    # fallback: якщо в id не знайшлося — беремо з тексту "9 Qty Available"
    if "Qty Available" not in meta:
        qty_loc = page.locator("div.product-detail p.text-warning span.text-success").first
        if qty_loc.count():
            qa = _first_int(qty_loc.inner_text().strip())
            if qa is not None:
                meta["Qty Available"] = qa

    if part_no:
        # ✅ стандартизуємо ключ: тільки part_no (без "Part No")
        meta["part_no"] = str(part_no)

    # Image URL (BigPictures)
    img_a = page.locator("div.product-img a").first
    href = (img_a.get_attribute("href") or "").strip() if img_a.count() else ""
    if href:
        meta["Image URL"] = urljoin(page.url, href)

    return meta


# =========================
# Dialogs / Debug
# =========================

def accept_dialogs(page: Page):
    """Auto-accept confirm() for Delete, safe to call once."""
    try:
        page.on("dialog", lambda d: d.accept())
    except Exception:
        pass


def dbg_dump(page: Page, tag: str):
    ts = int(time.time())
    png = f"dbg_{tag}_{ts}.png"
    html = f"dbg_{tag}_{ts}.html"
    try:
        page.screenshot(path=png, full_page=True)
    except Exception as e:
        pass
    try:
        with open(html, "w", encoding="utf-8") as f:
            f.write(page.content())
    except Exception as e:
        pass


def dbg_state(page: Page, label: str):
    fancy = page.locator("div.fancybox-container.fancybox-is-open")

    modal = page.locator("#myModal")


    detail = page.locator('button[onclick="detailView()"]')


    qty = page.locator('div.qty-updt-div input[type="number"]')



# =========================
# Overlays
# =========================

def close_fancybox_if_present(page: Page):
    fancy = page.locator("div.fancybox-container.fancybox-is-open")
    if fancy.count() and fancy.first.is_visible():
        close_btn = page.locator(".fancybox-close-small, .fancybox-button--close")
        if close_btn.count():
            close_btn.first.click(force=True)
        else:
            page.keyboard.press("Escape")
        try:
            page.wait_for_selector("div.fancybox-container.fancybox-is-open", state="hidden", timeout=8000)
        except PWTimeoutError:
            pass

def close_modal_if_present(page: Page):
    modal = page.locator("#myModal")
    if modal.count() and modal.first.is_visible():
        cancel = modal.locator('input[value="CANCEL"], button:has-text("CANCEL")')
        if cancel.count():
            cancel.first.click(force=True)
        else:
            page.keyboard.press("Escape")
        try:
            page.wait_for_selector("#myModal", state="hidden", timeout=8000)
        except PWTimeoutError:
            pass

# =========================
# NAV / FORM
# =========================

def go_to_price_inquiry(page: Page):
    accept_dialogs(page)

    dbg_state(page, "before go_to_price_inquiry")

    link = page.locator('a.nav-link[href*="QuoteOnline/mainQuotePage.php"]')
    link.wait_for(state="attached", timeout=20000)
    link.click(force=True)

    page.wait_for_url("**/QuoteOnline/mainQuotePage.php*", timeout=20000)

    dbg_state(page, "after go_to_price_inquiry")


def fill_price_inquiry_form(page: Page, part_number: str):
    dbg_state(page, f"before fill_price_inquiry_form part={part_number}")

    close_modal_if_present(page)
    close_fancybox_if_present(page)

    part_input = page.locator('input[name="TxtOPartNum"]')
    qty_input = page.locator('input[name="IntOPartQt"]')

    part_input.wait_for(state="visible", timeout=20000)
    qty_input.wait_for(state="visible", timeout=20000)

    part_input.fill("")
    qty_input.fill("")

    part_input.fill(part_number)

    # submit (2x enter)
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    page.keyboard.press("Enter")

    # anchor: Detail button exists
    page.locator('button[onclick="detailView()"]').wait_for(state="attached", timeout=20000)

    dbg_state(page, f"after fill_price_inquiry_form part={part_number}")


# =========================
# DELETE
# =========================

def delete_current_item(page: Page):
    close_modal_if_present(page)
    close_fancybox_if_present(page)

    # Prefer delete link in product detail block
    del_btn = page.locator("div.product-detail a.delete-single-part").first
    if del_btn.count():
        del_btn.click(force=True)
        page.wait_for_timeout(600)
        close_modal_if_present(page)
        close_fancybox_if_present(page)
        return

    # UA/EN fallback by text
    del_btn2 = page.locator("a:has-text('Видалити'), a:has-text('Delete')").first
    if del_btn2.count():
        del_btn2.click(force=True)
        page.wait_for_timeout(600)
        close_modal_if_present(page)
        close_fancybox_if_present(page)


# =========================
# DETAIL: set qty=9999 -> update -> meta -> collect -> delete
# =========================

def open_detail_update_qty_and_collect(page: Page):
    dbg_state(page, "before open_detail_update_qty_and_collect")

    close_modal_if_present(page)
    close_fancybox_if_present(page)

    detail_btn = page.locator('button[onclick="detailView()"]')
    detail_btn.wait_for(state="visible", timeout=20000)
    detail_btn.scroll_into_view_if_needed()

    try:
        detail_btn.click(force=True, timeout=5000)
    except Exception as e:
        try:
            page.evaluate("() => { if (typeof detailView === 'function') detailView(); }")
        except Exception as e2:
            pass
    qty_sel = 'div.qty-updt-div input[type="number"]'
    modal_sel = "#myModal"

    opened_mode = None
    try:
        page.wait_for_selector(qty_sel, state="visible", timeout=8000)
        opened_mode = "detail_view"
    except PWTimeoutError:
        pass

    if opened_mode is None:
        try:
            page.wait_for_selector(modal_sel, state="visible", timeout=8000)
            opened_mode = "modal"
        except PWTimeoutError:
            pass

    if opened_mode is None:
        dbg_dump(page, "detail_not_opened")
        dbg_state(page, "detail_not_opened")
        raise RuntimeError("❌ Не відкрився ні detail-view, ні modal після кліку Detail")


    # -------------------------
    # MODE A: detail view
    # -------------------------
    if opened_mode == "detail_view":
        # set qty=9999 in detail qty input
        qty_detail_input = page.locator(qty_sel).first
        qty_detail_input.scroll_into_view_if_needed()
        qty_detail_input.fill("")
        qty_detail_input.fill("9999")

        # click Update Qty (two buttons exist; pick quanity-now first)
        update_btn = page.locator('div.qty-updt-div button.quanity-now:has-text("Update Qty")')
        if update_btn.count() == 0:
            update_btn = page.locator('div.qty-updt-div button.quanity-more:has-text("Update Qty")')
        if update_btn.count() == 0:
            update_btn = page.locator('div.qty-updt-div button:has-text("Update Qty")')

        update_btn.first.wait_for(state="visible", timeout=20000)
        update_btn.first.click(force=True)

        # wait refresh
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            page.wait_for_timeout(600)

        # ✅ IMPORTANT: meta AFTER Update Qty (so Qty Available correct)
        meta = extract_product_meta_from_detail(page)
        meta["Requested Qty"] = 9999  # fixed

        # collect price table
        price_table = page.locator('div.price-table table').first
        price_table.wait_for(state="visible", timeout=20000)

        rows = price_table.locator("tr")
        result = {}
        for i in range(rows.count()):
            cells = rows.nth(i).locator("td")
            if cells.count() >= 2:
                k = cells.nth(0).inner_text().strip()
                v = cells.nth(1).inner_text().strip()
                if k:
                    result[k] = v

        out = {**meta, **result}

        # delete and cleanup
        delete_current_item(page)
        close_modal_if_present(page)
        close_fancybox_if_present(page)

        return out

    # -------------------------
    # MODE B: modal #myModal
    # -------------------------
    modal = page.locator("#myModal")
    modal.wait_for(state="visible", timeout=20000)

    # collect rows from modal table
    rows = modal.locator("table.desktop-view tbody tr")
    data_rows = []
    for i in range(rows.count()):
        tds = rows.nth(i).locator("td")
        if tds.count() >= 7:
            data_rows.append({
                "Location": tds.nth(1).inner_text().strip(),
                "Unit Price": tds.nth(2).inner_text().strip(),
                "Available In": tds.nth(3).inner_text().strip(),
                "Available": tds.nth(4).inner_text().strip(),
                "Backorder": tds.nth(5).inner_text().strip(),
                "Production": tds.nth(6).inner_text().strip(),
            })

    close_modal_if_present(page)
    close_fancybox_if_present(page)
    delete_current_item(page)

    return {"mode": "modal", "rows": data_rows}
