"""Extracción de datos de perfiles públicos de TikTok con Playwright.

Flujo:
  1. Carga las cookies de cookies.json (si existe).
  2. Abre el perfil y lee usuario, nombre, seguidores y seguidos.
  3. Toma los primeros 10 videos del perfil (del HTML o de la API interna),
     lee su descripción y los primeros 5 comentarios de cada uno.
  4. Guarda todo en un CSV dentro de /output y actualiza cookies.json.
"""
import csv
import json
import random
import re
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
COOKIES_FILE = BASE_DIR / "cookies.json"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

MAX_VIDEOS = 10
MAX_COMMENTS = 5

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SAME_SITE = {
    "strict": "Strict",
    "lax": "Lax",
    "none": "None",
    "no_restriction": "None",
    "unspecified": "Lax",
}


class ScraperError(Exception):
    """Error esperado que se muestra tal cual al usuario."""


# --------------------------------------------------------------------------
# Cookies
# --------------------------------------------------------------------------
def load_cookies():
    """Lee cookies.json (formato Cookie-Editor o el que guarda Playwright)."""
    if not COOKIES_FILE.exists():
        return []
    try:
        raw = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise ScraperError(
            "cookies.json no es un JSON válido. Expórtalo de nuevo o bórralo."
        )
    if isinstance(raw, dict):
        raw = raw.get("cookies", [])

    cookies = []
    for c in raw:
        if not c.get("name") or c.get("value") is None:
            continue
        same_site = SAME_SITE.get(str(c.get("sameSite", "lax")).lower(), "Lax")
        cookie = {
            "name": c["name"],
            "value": str(c["value"]),
            "domain": c.get("domain") or ".tiktok.com",
            "path": c.get("path") or "/",
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure": bool(c.get("secure", True)) or same_site == "None",
            "sameSite": same_site,
        }
        expires = c.get("expirationDate", c.get("expires"))
        if isinstance(expires, (int, float)) and expires > 0:
            cookie["expires"] = expires
        cookies.append(cookie)
    return cookies


def save_cookies(context):
    """Guarda las cookies actuales de la sesión en cookies.json."""
    cookies = context.cookies()
    if cookies:
        COOKIES_FILE.write_text(
            json.dumps(cookies, indent=2, ensure_ascii=False), encoding="utf-8"
        )


# --------------------------------------------------------------------------
# Utilidades de página
# --------------------------------------------------------------------------
def read_state(page):
    """Devuelve el JSON que TikTok incrusta en la página (__DEFAULT_SCOPE__)."""
    raw = page.evaluate(
        """() => {
            const el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
            return el ? el.textContent : null;
        }"""
    )
    if not raw:
        return {}
    try:
        return json.loads(raw).get("__DEFAULT_SCOPE__", {})
    except json.JSONDecodeError:
        return {}


CAPTCHA_TEXT = re.compile(
    r"drag the slider|verify to continue|slide to verify|fit the puzzle", re.I
)


def captcha_visible(page):
    """Detecta el captcha por su contenedor o por su texto visible."""
    try:
        if page.locator('[id*="captcha" i], [class*="captcha" i]').first.is_visible():
            return True
        return page.get_by_text(CAPTCHA_TEXT).first.is_visible()
    except Exception:
        return False


def handle_captcha(page, headless):
    """Si hay captcha: en modo oculto falla; en modo visible espera a que lo
    resuelvas a mano. Devuelve True si hubo captcha y se resolvió."""
    if not captcha_visible(page):
        return False
    if headless:
        raise ScraperError(
            "TikTok pidió una verificación (captcha). Activa «Mostrar el navegador», "
            "resuélvela a mano y vuelve a intentar."
        )
    print("CAPTCHA detectado: resuélvelo en la ventana del navegador (máx. 3 min)...")
    for _ in range(360):  # hasta ~3 minutos
        if not captcha_visible(page):
            page.wait_for_timeout(1500)
            return True
        page.wait_for_timeout(500)
    raise ScraperError("El captcha no se resolvió a tiempo. Inténtalo de nuevo.")


def video_key(url):
    """Identificador numérico del video, para evitar duplicados."""
    m = re.search(r"/(?:video|photo)/(\d+)", url)
    return m.group(1) if m else url


# --------------------------------------------------------------------------
# Perfil
# --------------------------------------------------------------------------
def get_profile(page, username, headless):
    page.goto(
        f"https://www.tiktok.com/@{username}",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    handle_captcha(page, headless)
    try:
        page.wait_for_selector(
            '#__UNIVERSAL_DATA_FOR_REHYDRATION__, [data-e2e="user-title"]',
            state="attached",
            timeout=20000,
        )
    except Exception:
        pass

    detail = read_state(page).get("webapp.user-detail", {})
    info = detail.get("userInfo") or {}
    user, stats = info.get("user"), info.get("stats") or {}
    if user:
        return {
            "username": user.get("uniqueId", username),
            "name": user.get("nickname", ""),
            "followers": stats.get("followerCount"),
            "following": stats.get("followingCount"),
        }

    # Plan B: leer el HTML visible
    title = page.locator('[data-e2e="user-title"]').first
    if title.count():
        def text_of(sel):
            loc = page.locator(sel).first
            return loc.inner_text().strip() if loc.count() else ""

        return {
            "username": title.inner_text().strip(),
            "name": text_of('[data-e2e="user-subtitle"]'),
            "followers": text_of('[data-e2e="followers-count"]'),
            "following": text_of('[data-e2e="following-count"]'),
        }

    raise ScraperError(
        f"No se encontró el perfil @{username}. Revisa el nombre de usuario."
    )


def get_video_links(page, username, api_ids, headless):
    """Busca enlaces a videos en el HTML y, si faltan, usa los IDs de la API interna."""
    page.wait_for_timeout(3000)  # deja cargar la cuadrícula
    links = []
    for _ in range(8):
        if handle_captcha(page, headless):
            # tras resolverlo, recarga para que la cuadrícula pida los videos
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
        hrefs = page.eval_on_selector_all(
            'a[href*="/video/"], a[href*="/photo/"]',
            "els => els.map(e => e.href)",
        )
        links = list(
            dict.fromkeys(
                h.split("?")[0]
                for h in hrefs
                if f"/@{username}".lower() in h.lower()
            )
        )
        if len(links) >= MAX_VIDEOS:
            break
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(1500)

    # Complemento: IDs que la propia página pidió a la API
    seen = {video_key(u) for u in links}
    for vid in api_ids:
        if len(links) >= MAX_VIDEOS:
            break
        if vid not in seen:
            links.append(f"https://www.tiktok.com/@{username}/video/{vid}")
            seen.add(vid)
    return links[:MAX_VIDEOS]


# --------------------------------------------------------------------------
# Videos y comentarios
# --------------------------------------------------------------------------
def wait_for_comments(page, api_comments):
    clicked = False
    for i in range(16):  # ~8 segundos
        if api_comments or page.locator('[data-e2e="comment-level-1"]').count():
            break
        if i == 6 and not clicked:
            icon = page.locator('[data-e2e="comment-icon"]').first
            if icon.count():
                try:
                    icon.click(timeout=2000)
                except Exception:
                    pass
            clicked = True
        page.wait_for_timeout(500)

    if api_comments:
        return list(dict.fromkeys(api_comments))[:MAX_COMMENTS]
    texts = page.locator('[data-e2e="comment-level-1"]').all_inner_texts()
    return [t.strip() for t in texts if t.strip()][:MAX_COMMENTS]


def back_to_profile(page, profile_url):
    """Vuelve a la cuadrícula del perfil después de abrir un video."""
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)
        if "/video/" in page.url or "/photo/" in page.url:
            page.go_back(wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1500)
        if "/video/" in page.url or "/photo/" in page.url or "/@" not in page.url:
            raise PlaywrightError("no volvió al perfil")
        page.wait_for_selector('a[href*="/video/"], a[href*="/photo/"]', timeout=10000)
    except PlaywrightError:
        page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)


def get_video_by_click(page, vid, url, headless, fallback_desc, profile_url):
    """Abre el video haciendo clic en su miniatura (como un usuario real) y lee
    sus comentarios. Evita cargar la URL del video directamente, que TikTok rechaza."""
    result = {"url": url, "description": fallback_desc, "comments": [], "error": ""}
    api_comments = []

    def on_response(resp):
        if "/api/comment/list" not in resp.url:
            return
        try:
            data = resp.json()
        except Exception:
            return
        for c in data.get("comments") or []:
            text = (c.get("text") or "").strip()
            if text:
                api_comments.append(text)

    page.on("response", on_response)
    try:
        tile = page.locator(f'a[href*="/video/{vid}"], a[href*="/photo/{vid}"]').first
        for _ in range(5):  # si la miniatura aún no está cargada, baja un poco
            if tile.count():
                break
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(1000)
        if not tile.count():
            raise PlaywrightError("No se encontró la miniatura de este video en el perfil")

        tile.scroll_into_view_if_needed(timeout=5000)
        tile.click(timeout=8000)
        page.wait_for_timeout(2500)
        handle_captcha(page, headless)

        if not result["description"]:
            loc = page.locator(
                '[data-e2e="browse-video-desc"], [data-e2e="video-desc"]'
            ).first
            if loc.count():
                result["description"] = loc.inner_text().strip()

        result["comments"] = wait_for_comments(page, api_comments)
    except PlaywrightError as e:
        result["error"] = str(e).splitlines()[0]
        print("No se pudo leer el video", url, "->", result["error"])
    finally:
        page.remove_listener("response", on_response)
        back_to_profile(page, profile_url)
    return result


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------
def save_csv(username, profile, videos):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"tiktok_{username}_{stamp}.csv"
    header = [
        "username", "name", "followers", "following",
        "video_n", "video_url", "video_description",
    ] + [f"comment_{i}" for i in range(1, MAX_COMMENTS + 1)]
    base = [profile["username"], profile["name"], profile["followers"], profile["following"]]
    # utf-8-sig para que Excel en Windows muestre bien tildes y emojis
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        if not videos:
            writer.writerow(base + [""] * (3 + MAX_COMMENTS))
        for n, v in enumerate(videos, start=1):
            comments = v["comments"] + [""] * (MAX_COMMENTS - len(v["comments"]))
            writer.writerow(base + [n, v["url"], v["description"]] + comments)
    return path


# --------------------------------------------------------------------------
# Función principal
# --------------------------------------------------------------------------
def scrape_profile(username, headless=True):
    username = (username or "").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9._]{2,24}", username):
        raise ScraperError(
            "Usuario no válido. Usa de 2 a 24 caracteres: letras, números, punto o guion bajo."
        )

    cookies = load_cookies()
    warnings = []
    if not cookies:
        warnings.append(
            "No se encontró cookies.json: la extracción corrió sin sesión y TikTok puede "
            "bloquearla. Ejecuta guardar_cookies.py para crearlo."
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        if cookies:
            context.add_cookies(cookies)

        try:
            page = context.new_page()

            # IDs de videos que la página pide a la API interna del perfil
            api_ids = []
            api_desc = {}

            def on_posts(resp):
                if "/api/post/item_list" not in resp.url:
                    return
                try:
                    data = resp.json()
                except Exception:
                    return
                for it in data.get("itemList") or []:
                    vid = str(it.get("id") or "")
                    if vid and vid not in api_ids:
                        api_ids.append(vid)
                    if vid:
                        api_desc[vid] = (it.get("desc") or "").strip()

            page.on("response", on_posts)

            profile = get_profile(page, username, headless)
            links = get_video_links(page, profile["username"], api_ids, headless)

            if not links:
                shot = OUTPUT_DIR / f"debug_{username}.png"
                page.screenshot(path=str(shot))
                print("SIN VIDEOS | URL:", page.url, "| Título:", page.title())
                print("Captura guardada en:", shot)

            videos = []
            profile_url = f"https://www.tiktok.com/@{profile['username']}"
            for link in links:
                vid = video_key(link)
                videos.append(
                    get_video_by_click(
                        page, vid, link, headless,
                        fallback_desc=api_desc.get(vid, ""),
                        profile_url=profile_url,
                    )
                )
                page.wait_for_timeout(random.randint(1500, 3500))
            failed = sum(1 for v in videos if v["error"])
            if failed:
                warnings.append(
                    f"No se pudo leer {failed} de {len(videos)} videos. De esos se "
                    "guardó la descripción de la lista, pero sin comentarios."
                )
            page.close()
            save_cookies(context)
        finally:
            browser.close()

    if not videos:
        warnings.append(
            "No se encontraron videos: el perfil puede ser privado, no tener publicaciones "
            "o TikTok pidió verificación. Revisa output/debug_<usuario>.png."
        )

    csv_path = save_csv(username, profile, videos)
    return {
        "profile": profile,
        "videos": videos,
        "csv_file": csv_path.name,
        "warnings": warnings,
    }