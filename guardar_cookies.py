"""Abre un navegador para que inicies sesión en TikTok (y resuelvas captchas)
y guarda las cookies en cookies.json. Ejecútalo una vez, o cuando caduquen."""
from playwright.sync_api import sync_playwright

from scraper import USER_AGENT, save_cookies

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False, args=["--disable-blink-features=AutomationControlled"]
    )
    context = browser.new_context(user_agent=USER_AGENT, locale="en-US")
    page = context.new_page()
    page.goto("https://www.tiktok.com/login")
    input("Inicia sesión en la ventana del navegador y pulsa ENTER aquí cuando termines... ")
    save_cookies(context)
    print("Cookies guardadas en cookies.json")
    browser.close()
