"""Frontend local (Flask) para el extractor de perfiles de TikTok."""
import json
import os
import re
import threading
import time
import webbrowser

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

from scraper import OUTPUT_DIR, ScraperError, scrape_profile

app = Flask(__name__)
busy = threading.Lock()  # una extracción a la vez
analysis_busy = threading.Lock()  # un análisis a la vez


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/scrape")
def api_scrape():
    data = request.get_json(silent=True) or {}
    if not busy.acquire(blocking=False):
        return jsonify(error="Ya hay una extracción en curso. Espera a que termine."), 429
    try:
        result = scrape_profile(
            data.get("username", ""),
            headless=not data.get("show_browser", False),
        )
        return jsonify(result)
    except ScraperError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:  # noqa: BLE001
        app.logger.exception("Fallo inesperado")
        return jsonify(error=f"Error inesperado: {e}"), 500
    finally:
        busy.release()


@app.get("/analisis")
def analisis_page():
    return render_template("analisis.html")


@app.get("/api/analisis")
def list_analisis():
    files = sorted(OUTPUT_DIR.glob("analisis_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    return jsonify([f.name for f in files])


@app.get("/api/analisis/<name>")
def get_analisis(name):
    if not re.fullmatch(r"analisis_[\w.\-]+\.json", name):
        abort(400)
    path = OUTPUT_DIR / name
    if not path.is_file():
        abort(404)
    return jsonify(json.loads(path.read_text(encoding="utf-8")))


def _csv_files():
    """CSV de output/ del más reciente al más antiguo (por la fecha del nombre)."""
    def stamp(p):
        m = re.search(r"(\d{8})_(\d{6})", p.stem)
        return m.group(1) + m.group(2) if m else time.strftime("%Y%m%d%H%M%S", time.localtime(p.stat().st_mtime))
    return sorted(OUTPUT_DIR.glob("*.csv"), key=stamp, reverse=True)


@app.get("/api/estado")
def estado():
    items = [
        {"name": f.name, "has_analysis": (OUTPUT_DIR / f"analisis_{f.stem}.json").is_file()}
        for f in _csv_files()
    ]
    resp = jsonify(
        csvs=items,
        latest_csv=items[0]["name"] if items else None,
        has_env_key=bool(os.environ.get("GEMINI_API_KEY")),
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/analizar")
def api_analizar():
    data = request.get_json(silent=True) or {}
    api_key = (data.get("api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return jsonify(error="Falta la clave de API de Gemini."), 400
    try:
        import analizar_big5
    except ImportError:
        return jsonify(error="Falta la librería de Gemini. Ejecuta: pip install google-genai"), 500
    if not analysis_busy.acquire(blocking=False):
        return jsonify(error="Ya hay un análisis en curso. Espera a que termine."), 429
    try:
        wanted = (data.get("csv") or "").strip()
        names = [f.name for f in _csv_files()]
        if wanted and wanted not in names:
            return jsonify(error=f"No se encontró el CSV «{wanted}»."), 400
        csv_file = OUTPUT_DIR / (wanted or (names[0] if names else ""))
        result = analizar_big5.run_analysis(csv_path=csv_file, api_key=api_key)
        return jsonify(json=result["json"], csv=result["csv"])
    except analizar_big5.AnalysisError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:  # noqa: BLE001
        app.logger.error("Fallo en el análisis: %s", type(e).__name__)
        return jsonify(error=f"Error inesperado en el análisis: {type(e).__name__}"), 500
    finally:
        analysis_busy.release()


@app.get("/download/<path:filename>")
def download(filename):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)