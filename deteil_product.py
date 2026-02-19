from __future__ import annotations
from debug_utils import dbg_dump, debug
from playwright.sync_api import Page, TimeoutError as PWTimeoutError
import time
import re
from datetime import datetime
from pathlib import Path


# =========================
# Helpers
# =========================

ORIGIN = "https://www.ctpsales.costex.com:11443"


def _abs_url(page: Page, url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return ORIGIN + url


def _first_int(s: str) -> int | None:
    m = re.search(r"(\d+)", (s or "").replace("\xa0", " "))
    return int(m.group(1)) if m else None


def _parse_money_to_float(s: str) -> float | None:
    if not s:
        return None
    s = s.strip().replace(",", "")
    m = re.search(r"(-?\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


def _extract_part_no_from_delete_id(raw_id: str) -> str:
    raw_id = (raw_id or "").strip()
    if not raw_id:
        return ""
    return raw_id.split("|", 1)[0].strip()


def accept_dialogs(page: Page):
    try:
        page.on("dialog", lambda d: d.accept())
    except Exception:
        pass


def dbg_state(page: Page, tag: str):
    print(f"DEBUG[{tag}] url={page.url}")





def close_modal_if_present(page: Page):
    try:
        modal = page.locator("#myModal")
        if modal.count() and modal.is_visible():
            page.locator("#myModal button.close, #myModal .close").first.click(force=True)
            page.wait_for_timeout(300)
    except Exception:
        pass


def close_fancybox_if_present(page: Page):
    try:
        fb = page.locator(".fancybox-overlay, .fancybox-wrap")
        if fb.count() and fb.first.is_visible():
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)
    except Exception:
        pass


def wait_modal_open(page: Page, timeout_ms: int = 5000) -> bool:
    """
    Надійніше за wait_for_selector(..., visible).
    Перевіряє стилі, бо інколи visible не спрацьовує стабільно в headless.
    """
    try:
        page.wait_for_function(
            """() => {
                const m = document.querySelector('#myModal');
                if (!m) return false;
                const st = getComputedStyle(m);
                if (!st) return false;
                const displayed = st.display !== 'none';
                const visible = st.visibility !== 'hidden';
                const opaque = st.opacity !== '0';
                return displayed && visible && opaque;
            }""",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


def wait_signal_after_submit(page: Page, timeout_ms: int = 12000) -> None:
    """
    Чекаємо один з “сигналів” після вводу part_no:
      - модалка
      - кнопка detailView (в DOM)
      - qty input у detail view
    """
    page.wait_for_function(
        """() => {
            const m = document.querySelector('#myModal');
            const detailBtn = document.querySelector('button[onclick="detailView()"]');
            const qtyDetail = document.querySelector('div.qty-updt-div input[type="number"]');
            const modalOpen = m && getComputedStyle(m).display !== 'none';
            return modalOpen || !!detailBtn || !!qtyDetail;
        }""",
        timeout=timeout_ms,
    )


def wait_detail_or_modal(page: Page, timeout_ms: int = 15000) -> None:
    """
    Після detailView/update qty — чекаємо або qty input у detail view, або модалку.
    """
    page.wait_for_function(
        """() => {
            const m = document.querySelector('#myModal');
            const qty = document.querySelector('div.qty-updt-div input[type="number"]');
            const modalOpen = m && getComputedStyle(m).display !== 'none';
            return !!qty || modalOpen;
        }""",
        timeout=timeout_ms,
    )


def extract_quote_table_and_qty_from_detail_view(page: Page) -> dict:
    """
    Зчитує з detail_view (до кліку More Details):
      - Unit Price -> для Excel як Retail Price Tax Exc (write.py)
      - Qty Available
      - (опційно) Lbs, Kgs, Vol...
    """
    out = {}

    # Key/Value table
    try:
        rows = page.locator("table tbody tr")
        for i in range(rows.count()):
            tds = rows.nth(i).locator("td")
            if tds.count() >= 2:
                k = tds.nth(0).inner_text().strip().rstrip(":")
                v = tds.nth(1).inner_text().strip()

                kl = k.lower()
                if kl == "unit price":
                    out["Unit Price"] = v
                elif kl == "lbs":
                    out["Lbs"] = v
                elif kl == "kgs":
                    out["Kgs"] = v
                elif kl == "vol (cm3)":
                    out["Vol (cm3)"] = v
                elif kl == "vol (ft3)":
                    out["Vol (ft3)"] = v
    except Exception:
        pass

    # Qty Available
    try:
        span = page.locator("p.text-warning span.text-success").first
        if span.count():
            txt = span.inner_text().strip()
            m = re.search(r"(\d+)", txt)
            if m:
                out["Qty Available"] = int(m.group(1))
    except Exception:
        pass

    return out


# =========================
# NAVIGATION
# =========================

def go_to_price_inquiry(page: Page):
    accept_dialogs(page)
    dbg_state(page, "before go_to_price_inquiry")

    link = page.locator('a.nav-link[href*="QuoteOnline/mainQuotePage.php"]').first
    link.wait_for(state="visible", timeout=30000)

    # Навігація інколи не ловиться без expect_navigation
    try:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
            link.click()
    except Exception:
        # fallback
        link.click(force=True)

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    page.wait_for_url("**/QuoteOnline/mainQuotePage.php*", timeout=30000)
    dbg_state(page, "after go_to_price_inquiry")


def ensure_on_quote_page(page: Page, timeout: int = 45000):
    if "QuoteOnline/mainQuotePage.php" in page.url:
        # підстрахуємось що поле існує
        page.locator('input[name="TxtOPartNum"]').wait_for(state="visible", timeout=timeout)
        return

    try:
        go_to_price_inquiry(page)
    except Exception:
        try:
            page.evaluate("window.location.href='/Sales/QuoteOnline/mainQuotePage.php'")
        except Exception:
            pass

    page.wait_for_url("**/QuoteOnline/mainQuotePage.php*", timeout=timeout)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.locator('input[name="TxtOPartNum"]').wait_for(state="visible", timeout=timeout)


def back_to_quote_and_wait(page: Page, timeout: int = 45000):
    try:
        page.evaluate("typeof backToQuote === 'function' && backToQuote()")
    except Exception:
        pass

    if "QuoteOnline/mainQuotePage.php" not in page.url:
        btn = page.locator('div.back-quote button:has-text("BACK TO QUOTE")')
        if btn.count():
            btn.first.wait_for(state="visible", timeout=15000)
            try:
                with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                    btn.first.click()
            except Exception:
                btn.first.click(force=True)

    page.wait_for_url("**/QuoteOnline/mainQuotePage.php*", timeout=timeout)
    page.locator('input[name="TxtOPartNum"]').wait_for(state="visible", timeout=timeout)


def return_to_quote_page(page: Page):
    try:
        back_to_quote_and_wait(page)
        return
    except Exception:
        pass

    page.goto(
        f"{ORIGIN}/Sales/QuoteOnline/mainQuotePage.php",
        wait_until="domcontentloaded",
        timeout=45000,
    )
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.locator('input[name="TxtOPartNum"]').wait_for(state="visible", timeout=45000)


# =========================
# FORM: fill part_no -> ready for Detail
# =========================

def fill_price_inquiry_form(page: Page, part_number: str):
    dbg_state(page, f"before fill_price_inquiry_form part={part_number}")

    ensure_on_quote_page(page)
    close_modal_if_present(page)
    close_fancybox_if_present(page)

    part_input = page.locator('input[name="TxtOPartNum"]')
    qty_input = page.locator('input[name="IntOPartQt"]')

    try:
        part_input.wait_for(state="visible", timeout=30000)
        qty_input.wait_for(state="visible", timeout=30000)
    except PWTimeoutError:
        dbg_dump(page, f"timeout_form_fields_{part_number}")
        raise RuntimeError(f"❌ Timeout waiting for form fields for part={part_number}")

    part_input.fill("")
    qty_input.fill("")
    part_input.fill(part_number)

    # ✅ “людський” submit
    try:
        part_input.press("Tab")
    except Exception:
        pass
    try:
        part_input.press("Enter")
    except Exception:
        page.keyboard.press("Enter")

    # ✅ чекаємо сигнал: modal OR detailView OR qty detail
    try:
        wait_signal_after_submit(page, timeout_ms=12000)
    except Exception:
        dbg_dump(page, f"no_signal_after_submit_{part_number}")
        # не валимо одразу — інколи просто довше
        pass

    dbg_state(page, f"after fill_price_inquiry_form part={part_number}")


# =========================
# DELETE + MORE DETAILS
# =========================

def delete_current_item(page: Page):
    close_modal_if_present(page)
    close_fancybox_if_present(page)

    del_btn = page.locator("div.product-detail a.delete-single-part").first
    if del_btn.count():
        try:
            del_btn.click(force=True)
        except Exception:
            pass
        page.wait_for_timeout(600)
        close_modal_if_present(page)
        close_fancybox_if_present(page)
        return

    del_btn2 = page.locator("a:has-text('Видалити'), a:has-text('Delete')").first
    if del_btn2.count():
        try:
            del_btn2.click(force=True)
        except Exception:
            pass
        page.wait_for_timeout(600)
        close_modal_if_present(page)
        close_fancybox_if_present(page)


def click_more_details(page: Page) -> bool:
    close_modal_if_present(page)
    close_fancybox_if_present(page)

    more_link = page.locator("p.small a:has-text('More Details')").first
    if more_link.count():
        try:
            more_link.wait_for(state="visible", timeout=15000)
            more_link.click(force=True)
            page.wait_for_timeout(500)
            return True
        except Exception:
            pass

    more_link2 = page.locator("a[href*='partDetails1.php']").first
    if more_link2.count():
        try:
            more_link2.wait_for(state="attached", timeout=15000)
            more_link2.click(force=True)
            page.wait_for_timeout(500)
            return True
        except Exception:
            pass

    try:
        handle = page.locator("a[href*='partDetails1.php']").first.element_handle()
        if handle:
            page.evaluate("(el) => el.click()", handle)
            page.wait_for_timeout(500)
            return True
    except Exception:
        pass

    return False


# =========================
# META EXTRACTORS (normalized to write.py)
# =========================

def extract_product_meta_from_detail(page: Page) -> dict:
    meta: dict = {}

    try:
        title = page.locator("div.partno-main-tit").first
        if title.count():
            meta["Title"] = title.inner_text().strip()
    except Exception:
        pass

    try:
        del_link = page.locator("a.delete-single-part").first
        if del_link.count():
            raw_id = del_link.get_attribute("id") or ""
            part_no = _extract_part_no_from_delete_id(raw_id)
            if part_no:
                meta["part_no"] = part_no
    except Exception:
        pass

    try:
        qa = page.locator("span.qty-avl").first
        if qa.count():
            meta["Qty Available"] = _first_int(qa.inner_text()) or 0
    except Exception:
        pass

    try:
        img = page.locator("div.partimg-block img").first
        if img.count():
            src = img.get_attribute("src")
            if src:
                meta["Image URL"] = src
    except Exception:
        pass

    # best-effort unit price (optional)
    try:
        txt = page.locator("body").inner_text()
        m = re.search(r"\$\s*\d+(?:\.\d+)?", txt)
        if m:
            meta["Unit Price"] = m.group(0).replace(" ", "").strip()
    except Exception:
        pass

    return meta


def extract_from_part_details_page(page: Page) -> dict:
    raw = {}
    out = {}

    rows = page.locator("div.col-12.col-sm-12.col-md-12.col-lg-6.p-0 table tbody tr")
    if rows.count() == 0:
        rows = page.locator("div.col-lg-6.p-0 table tr")

    for i in range(rows.count()):
        ths = rows.nth(i).locator("th")
        if ths.count() >= 2:
            k = ths.nth(0).inner_text().strip().rstrip(":")
            v = ths.nth(1).inner_text().strip()
            if k:
                raw[k] = v

    part_no = raw.get("Part No.") or raw.get("Part No") or ""
    if part_no:
        out["part_no"] = part_no

    desc = raw.get("Description") or ""
    if desc:
        out["Title"] = desc

    w_lbs = raw.get("Weight (lbs)") or ""
    if w_lbs:
        out["Lbs"] = w_lbs

    width = raw.get("Width (cm)") or raw.get("Width") or ""
    height = raw.get("Height (cm)") or raw.get("Height") or ""
    depth = raw.get("Depth (cm)") or raw.get("Depth") or ""

    if width:
        out["Width"] = width
    if height:
        out["Height"] = height
    if depth:
        out["Depth"] = depth

    try:
        a = page.locator("div.prdct-img a[href]").first
        if a.count():
            href = a.get_attribute("href")
            if href:
                out["Image URL"] = _abs_url(page, href)
        else:
            img = page.locator("div.prdct-img img[src]").first
            if img.count():
                src = img.get_attribute("src")
                if src:
                    out["Image URL"] = _abs_url(page, src)
    except Exception:
        pass

    return out


# =========================
# DETAIL FLOW
# =========================

def open_detail_update_qty_and_collect(page: Page):
    """
    Робастний збір:
      - якщо модалка зʼявилась — парсимо модалку, закриваємо, back->delete
      - інакше: detailView -> qty=9999 -> Update Qty -> pre_more -> More Details -> partDetails1.php -> parse -> back->delete
    """
    close_modal_if_present(page)
    close_fancybox_if_present(page)
    dbg_state(page, "before open_detail_update_qty_and_collect")

    # -------- modal parsing helpers --------

    def _parse_th_th_rows_in_scope(scope_selector: str) -> dict:
        raw = {}
        rows = page.locator(f"{scope_selector} table tbody tr")
        for i in range(rows.count()):
            ths = rows.nth(i).locator("th")
            if ths.count() >= 2:
                k = ths.nth(0).inner_text().strip().rstrip(":")
                v = ths.nth(1).inner_text().strip()
                if k:
                    raw[k] = v
        return raw

    def _normalize_specs(raw: dict) -> dict:
        out = {}
        part_no = raw.get("Part No.") or raw.get("Part No") or raw.get("Part Number") or ""
        if part_no:
            out["part_no"] = part_no.strip()

        desc = raw.get("Description") or raw.get("Desc") or ""
        if desc:
            out["Title"] = desc.strip()

        lbs = raw.get("Weight (lbs)") or raw.get("Lbs") or ""
        if lbs:
            out["Lbs"] = lbs.strip()

        width = raw.get("Width (cm)") or raw.get("Width") or ""
        height = raw.get("Height (cm)") or raw.get("Height") or ""
        depth = raw.get("Depth (cm)") or raw.get("Depth") or ""

        if width:
            out["Width"] = width.strip()
        if height:
            out["Height"] = height.strip()
        if depth:
            out["Depth"] = depth.strip()

        rq = raw.get("Requested Qty") or ""
        if rq:
            try:
                out["Requested Qty"] = int(str(rq).strip())
            except Exception:
                pass

        return out

    def _extract_unit_price_from_modal_locations() -> str:
        try:
            table = page.locator("#myModal table.border.desktop-view").first
            if not table.count():
                return ""
            first_row = table.locator("tbody tr").first
            if not first_row.count():
                return ""
            tds = first_row.locator("td")
            if tds.count() >= 3:
                return tds.nth(2).inner_text().strip()
        except Exception:
            pass
        return ""

    def _extract_qty_available_from_modal_locations() -> int | None:
        try:
            table = page.locator("#myModal table.border.desktop-view").first
            if not table.count():
                return None
            first_row = table.locator("tbody tr").first
            if not first_row.count():
                return None
            tds = first_row.locator("td")
            if tds.count() >= 5:
                av = tds.nth(4).inner_text().strip()
                if av.isdigit():
                    return int(av)
        except Exception:
            pass
        return None

    def _extract_image_from_modal() -> str:
        try:
            img = page.locator("#myModal div.prdct-img img[src]").first
            if img.count():
                src = img.get_attribute("src") or ""
                if src:
                    return _abs_url(page, src)
        except Exception:
            pass
        return ""

    def _close_modal():
        try:
            cancel = page.locator("#myModal input[value='CANCEL'], #myModal button:has-text('CANCEL')").first
            if cancel.count() and cancel.is_visible():
                cancel.click(force=True)
                page.wait_for_timeout(400)
                return
        except Exception:
            pass
        close_modal_if_present(page)
        close_fancybox_if_present(page)
        page.wait_for_timeout(250)

    def _parse_modal_close_delete() -> dict:
        if not wait_modal_open(page, timeout_ms=15000):
            raise RuntimeError("Modal expected but not open.")

        raw = _parse_th_th_rows_in_scope("#myModal div.col-12.col-sm-12.col-md-12.col-lg-6.p-0")
        if not raw:
            raw = _parse_th_th_rows_in_scope("#myModal")

        out = _normalize_specs(raw)

        img_url = _extract_image_from_modal()
        if img_url:
            out["Image URL"] = img_url

        unit_price = _extract_unit_price_from_modal_locations()
        if unit_price:
            out["Unit Price"] = unit_price

        qa = _extract_qty_available_from_modal_locations()
        if qa is not None:
            out["Qty Available"] = qa

        _close_modal()

        return_to_quote_page(page)
        delete_current_item(page)
        close_modal_if_present(page)
        close_fancybox_if_present(page)

        return out

    # -------------------------
    # 0) Early modal window (bigger for headless)
    # -------------------------
    if wait_modal_open(page, timeout_ms=5000):
        dbg_state(page, "modal_visible_early")
        return _parse_modal_close_delete()

    # -------------------------
    # 1) Click Detail View
    # -------------------------
    close_modal_if_present(page)
    close_fancybox_if_present(page)

    detail_btn = page.locator('button[onclick="detailView()"]').first
    detail_btn.wait_for(state="visible", timeout=30000)

    try:
        detail_btn.click(force=True)
    except Exception:
        try:
            page.evaluate("detailView()")
        except Exception:
            pass

    # ✅ wait for real state
    try:
        wait_detail_or_modal(page, timeout_ms=15000)
    except Exception:
        dbg_dump(page, "after_detailView_no_state")
        raise RuntimeError(f"❌ After detailView: neither modal nor qty input. url={page.url}")

    # -------------------------
    # 2) If modal -> parse it
    # -------------------------
    if wait_modal_open(page, timeout_ms=2000):
        dbg_state(page, "modal_visible_after_detailView")
        return _parse_modal_close_delete()

    # -------------------------
    # 3) Normal detail_view flow: qty input
    # -------------------------
    qty_input_detail = page.locator('div.qty-updt-div input[type="number"]').first
    try:
        qty_input_detail.wait_for(state="visible", timeout=30000)
    except Exception:
        dbg_dump(page, "no_qty_input_detail")
        raise RuntimeError(f"❌ detail_view qty input not visible. url={page.url}")

    qty_input_detail.fill("")
    qty_input_detail.fill("9999")

    # click Update Qty
    qty_box = page.locator("div.qty-updt-div").first
    qty_box.wait_for(state="attached", timeout=30000)

    candidates = qty_box.locator("button.quanity-more, button.quanity-now, button:has-text('Update Qty')")
    clicked_update = False

    for i in range(candidates.count()):
        btn = candidates.nth(i)
        try:
            if btn.is_visible():
                btn.click(force=True)
                clicked_update = True
                break
        except Exception:
            pass

    if not clicked_update:
        try:
            handle = candidates.first.element_handle()
            page.evaluate("(el) => el && el.click()", handle)
            clicked_update = True
        except Exception:
            pass

    if not clicked_update:
        dbg_dump(page, "update_qty_button_not_clickable")
        raise RuntimeError("❌ Cannot click Update Qty.")

    # wait refresh
    try:
        page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:
        page.wait_for_timeout(1200)

    # 4) modal may appear after update
    if wait_modal_open(page, timeout_ms=3000):
        dbg_state(page, "modal_visible_after_update_qty")
        return _parse_modal_close_delete()

    # 5) collect Unit Price + Qty Available before More Details
    pre_more = extract_quote_table_and_qty_from_detail_view(page)

    # 6) Click More Details -> partDetails1.php (retry)
    ok = False
    for attempt in range(1, 4):
        if click_more_details(page):
            try:
                page.wait_for_url("**/partDetails1.php*", timeout=20000)
                ok = True
                break
            except Exception:
                pass
        page.wait_for_timeout(600)

    # even if url not changed, continue
    if not ok and "partDetails1.php" not in page.url:
        dbg_dump(page, "more_details_not_opened")
        # не валимо — можливо detail view вже містить все потрібне

    # 7) Parse from partDetails1.php
    details_norm = {}
    if "partDetails1.php" in page.url:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        details_norm = extract_from_part_details_page(page)

    # 8) Meta (best effort)
    meta = extract_product_meta_from_detail(page)
    meta["Requested Qty"] = 9999

    out = {**meta, **pre_more, **details_norm}

    # 9) Cleanup
    return_to_quote_page(page)
    delete_current_item(page)
    close_modal_if_present(page)
    close_fancybox_if_present(page)

    dbg_state(page, "after open_detail_update_qty_and_collect (normal branch)")
    return out
