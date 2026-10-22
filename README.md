# Extractor de perfiles de TikTok (Playwright + Flask)

## Instalación (Windows, VS Code)

Abre la carpeta en VS Code, abre una terminal (PowerShell) y ejecuta:

    python -m venv venv
    venv\Scripts\activate
    pip install -r requirements.txt
    playwright install chromium

Si PowerShell bloquea `activate`:

    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

## Cookies (cookies.json)

Opción A (recomendada): `python guardar_cookies.py`, inicia sesión en la ventana
y pulsa ENTER en la terminal.

Opción B: exporta las cookies de tiktok.com con la extensión Cookie-Editor
(formato JSON) y guárdalas como `cookies.json` junto a `app.py`
(ejemplo de formato en `cookies.example.json`).

Tras cada extracción el programa vuelve a guardar las cookies actualizadas.

## Uso

    python app.py

Se abre http://127.0.0.1:5000. Escribe el usuario y pulsa "Extraer datos".
Cada ejecución genera un CSV en `output/` (una fila por video; columnas:
username, name, followers, following, video_n, video_url, video_description,
comment_1 ... comment_5).

Si TikTok muestra un captcha, marca "Mostrar el navegador" y resuélvelo.
