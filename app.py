"""Frontend local (Flask) para el extractor de perfiles de TikTok."""
import threading
import webbrowser

from flask import Flask, jsonify, render_template, request, send_from_directory

from scraper import OUTPUT_DIR, ScraperError, scrape_profile

app = Flask(__name__)
busy = threading.Lock()  # una extracción a la vez


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


@app.get("/download/<path:filename>")
def download(filename):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
