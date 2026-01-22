from config import *
from playwright.sync_api import Page


def login(page: Page, url: str):
    page.goto(url, wait_until="domcontentloaded")

    login_name = SELECTOR["authorization"]["selector_login"]
    password_name = SELECTOR["authorization"]["selector_password"]

    # ✅ беремо перший елемент (або можна .nth(0))
    login_input = page.locator(f'input[name="{login_name}"]').first
    pass_input = page.locator(f'input[name="{password_name}"]').first

    login_input.wait_for(state="visible", timeout=20000)
    pass_input.wait_for(state="visible", timeout=20000)

    login_input.fill(CREDENTIALS["login"])
    pass_input.fill(CREDENTIALS["password"])

    page.keyboard.press("Enter")

    # (опційно) дочекатися редіректу після логіну
    # page.wait_for_url("**/Sales/**", timeout=30000)
