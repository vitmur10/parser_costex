from __future__ import annotations
from config import DEBUG
import random
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeoutError


# -------------------------
# Debug helpers
# -------------------------
def dbg_dump(page: Page, tag: str, out_dir: str = "dbg"):
    if not DEBUG:
        return
    try:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        png = Path(out_dir) / f"dbg_{tag}_{ts}.png"
        html = Path(out_dir) / f"dbg_{tag}_{ts}.html"
        try:
            page.screenshot(path=str(png), full_page=True)
        except Exception:
            pass
        try:
            html.write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        print(f"[DBG] saved: {html} | {png}")
    except Exception:
        pass


def accept_dialogs(page: Page):
    try:
        page.on("dialog", lambda d: d.accept())
    except Exception:
        pass


# -------------------------
# "Human" behavior helpers
# -------------------------
def _jitter(a: float, b: float) -> float:
    return random.uniform(a, b)


def human_pause(min_s: float = 0.15, max_s: float = 0.45):
    time.sleep(_jitter(min_s, max_s))


def human_move_mouse_to_locator(page: Page, locator, steps: int = 18):
    """Наводимо мишу на елемент більш-менш плавно."""
    try:
        box = locator.bounding_box()
        if not box:
            return
        x = box["x"] + box["width"] * _jitter(0.25, 0.75)
        y = box["y"] + box["height"] * _jitter(0.25, 0.75)
        page.mouse.move(x, y, steps=steps)
    except Exception:
        pass


def human_scroll_into_view(locator):
    try:
        locator.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass


def human_click(page: Page, locator, timeout_ms: int = 8000):
    """Клік без force, але з 'олюдненням'."""
    human_scroll_into_view(locator)
    try:
        locator.wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        return False

    human_move_mouse_to_locator(page, locator)
    human_pause(0.08, 0.22)

    try:
        locator.click(timeout=timeout_ms)
        human_pause(0.15, 0.35)
        return True
    except Exception:
        # fallback: спробувати через dispatchEvent
        try:
            locator.evaluate(
                """(el) => {
                    el.focus();
                    el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                    el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
                    el.click();
                }"""
            )
            human_pause(0.15, 0.35)
            return True
        except Exception:
            return False


def human_type(locator, text: str, min_delay_ms: int = 35, max_delay_ms: int = 95):
    """Набір з випадковими затримками між символами."""
    try:
        locator.click(timeout=5000)
    except Exception:
        pass

    # очистимо як людина: Ctrl+A -> Backspace
    try:
        locator.press("Control+A")
        human_pause(0.05, 0.15)
        locator.press("Backspace")
        human_pause(0.08, 0.20)
    except Exception:
        try:
            locator.fill("")
        except Exception:
            pass

    try:
        locator.type(text, delay=int(random.uniform(min_delay_ms, max_delay_ms)))
    except Exception:
        # fallback: fill (менш "людське", але іноді треба)
        locator.fill(text)

    human_pause(0.12, 0.35)


def human_micro_actions(page: Page):
    """Дрібні рухи/скрол як у реального користувача."""
    try:
        # легенький скрол вгору/вниз
        if random.random() < 0.35:
            page.mouse.wheel(0, int(random.choice([120, -120, 240, -240])))
            human_pause(0.08, 0.20)

        # трохи поворушити мишкою
        if random.random() < 0.25:
            page.mouse.move(_jitter(50, 250), _jitter(50, 250), steps=10)
            human_pause(0.05, 0.18)
    except Exception:
        pass


# -------------------------
# Navigation helper
# -------------------------
def goto_with_retry(page: Page, url: str, tries: int = 3, timeout_ms: int = 60000):
    last = None
    for i in range(1, tries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            human_pause(0.25, 0.7)
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            return
        except Exception as e:
            last = e
            print(f"[WARN] goto failed try={i}/{tries}: {e}")
            time.sleep(1.0)
    raise last


# -------------------------
# Login (human-like)
# -------------------------
def login(page: Page, url: str, username: str, password: str):
    """
    "Людський" логін:
      - retry goto
      - очікування visible
      - скрол/наведення/клік
      - друк з delay
      - клік по кнопці Login (#submitBtn)
      - очікування сигналів успіху
    """
    accept_dialogs(page)

    human_pause(0.3, 1.1)
    goto_with_retry(page, url, tries=3, timeout_ms=60000)
    human_micro_actions(page)

    # ✅ Поля з реального HTML Costex:
    #   login:  input[name="TxtRUSERNAME"] / #TxtRUSERNAME
    #   pass:   input[name="PwdRPASSWORD"] / #PwdRPASSWORD
    login_input = page.locator('input[name="TxtRUSERNAME"], #TxtRUSERNAME').first
    pass_input = page.locator('input[name="PwdRPASSWORD"], #PwdRPASSWORD').first

    try:
        login_input.wait_for(state="visible", timeout=30000)
        pass_input.wait_for(state="visible", timeout=30000)
    except PWTimeoutError:
        dbg_dump(page, "login_fields_timeout")
        raise RuntimeError("❌ Login fields not visible (maybe blocked / different DOM / Cloudflare).")

    human_scroll_into_view(login_input)
    human_move_mouse_to_locator(page, login_input)
    human_pause(0.12, 0.35)
    human_type(login_input, username)

    human_micro_actions(page)

    human_scroll_into_view(pass_input)
    human_move_mouse_to_locator(page, pass_input)
    human_pause(0.12, 0.35)
    human_type(pass_input, password)

    human_pause(0.3, 1.0)

    # ✅ Кнопка Login з HTML: id="submitBtn" onclick="processForm();"
    btn = page.locator("#submitBtn").first
    clicked = False

    if btn.count():
        clicked = human_click(page, btn, timeout_ms=8000)

    # fallback: якщо клік не спрацював — викликаємо JS processForm()
    if not clicked:
        try:
            page.evaluate("() => { if (typeof processForm === 'function') processForm(); }")
            clicked = True
        except Exception:
            clicked = False

    # останній fallback: Enter
    if not clicked:
        try:
            human_pause(0.15, 0.45)
            pass_input.press("Enter")
        except Exception:
            page.keyboard.press("Enter")

    human_pause(0.4, 1.2)

    # ✅ Чекаємо успіх:
    # - QuoteOnline лінк
    # - або navbar/nav
    # - або logout
    # + ловимо помилку, якщо вона з'явилась
    try:
        page.wait_for_function(
            """() => {
                const q = document.querySelector('a.nav-link[href*="QuoteOnline/mainQuotePage.php"]');
                const nav = document.querySelector('nav, .navbar, .nav');
                const logout = document.querySelector('a[href*="logout"], button[name*="logout"], .logout');
                const err = document.querySelector('.alert-danger, .error, .invalid-feedback');
                if (err) return "ERR";
                if (q || logout || nav) return "OK";
                return "";
            }""",
            timeout=30000,
        )

        res = page.evaluate(
            """() => {
                const err = document.querySelector('.alert-danger, .error, .invalid-feedback');
                return err ? (err.innerText || err.textContent || "").trim() : "";
            }"""
        )
        if res:
            dbg_dump(page, "login_error_detected")
            raise RuntimeError(f"❌ Login error on page: {res[:200]}")
    except Exception:
        dbg_dump(page, "login_no_success_signal")
        raise RuntimeError("❌ Login probably failed: no success signal after submit (check dbg html/png).")

    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
