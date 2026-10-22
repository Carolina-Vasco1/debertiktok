"""Analiza el CSV extraído de TikTok con Gemini: estimación de rasgos Big Five
y del tono emocional del contenido.

Uso:
    python analizar_big5.py                      (usa el CSV más reciente de output/)
    python analizar_big5.py output\\mi_archivo.csv

Clave de API (no la escribas dentro del código):
    PowerShell:  $env:GEMINI_API_KEY = "tu_clave"
    Modelo opcional:  $env:GEMINI_MODEL = "gemini-3.5-flash"
"""
import csv
import getpass
import json
import os
import re
import sys
import time
from pathlib import Path

from google import genai
from google.genai import types

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
# Si el modelo principal está saturado o no existe, se prueban estos en orden
FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
RETRY_WAITS = [5, 15, 30, 60]  # segundos entre reintentos
MAX_CHARS = 14000

SYSTEM = """Eres un analista de lenguaje con formación en psicología de la personalidad.
Recibes textos públicos de una cuenta de TikTok: descripciones escritas por el dueño
de la cuenta y comentarios escritos por OTRAS personas (la audiencia).

Reglas estrictas:
- Estima los rasgos Big Five SOLO a partir de las descripciones del dueño. Los
  comentarios son de terceros: úsalos únicamente para describir la reacción de la
  audiencia, nunca para inferir la personalidad del dueño.
- Cada puntaje (0-100) debe ir con evidencia textual breve (citas cortas) y un nivel
  de confianza. Con pocos textos la confianza debe ser baja; no inventes.
- Esto es una impresión sobre contenido público, no un diagnóstico clínico ni una
  descripción del estado mental real de la persona. No hagas diagnósticos.
- Responde en español y SOLO con JSON válido con esta estructura exacta:
{
  "confianza_global": "baja|media|alta",
  "big_five": {
    "apertura":        {"puntaje": 0, "confianza": "baja|media|alta", "evidencia": ["..."]},
    "responsabilidad": {"puntaje": 0, "confianza": "baja|media|alta", "evidencia": ["..."]},
    "extraversion":    {"puntaje": 0, "confianza": "baja|media|alta", "evidencia": ["..."]},
    "amabilidad":      {"puntaje": 0, "confianza": "baja|media|alta", "evidencia": ["..."]},
    "neuroticismo":    {"puntaje": 0, "confianza": "baja|media|alta", "evidencia": ["..."]}
  },
  "tono_emocional_del_contenido": {
    "predominante": "...",
    "emociones": [{"emocion": "...", "intensidad": 0}],
    "resumen": "..."
  },
  "reaccion_de_la_audiencia": {"tono": "...", "resumen": "..."},
  "conclusion": "Párrafo breve sobre cómo se percibe que se expresa la persona en este contenido, con cautela.",
  "limitaciones": ["..."]
}"""


def latest_csv():
    files = sorted(OUTPUT_DIR.glob("tiktok_*.csv"), key=lambda p: p.stat().st_mtime)
    if not files:
        sys.exit("No hay CSV en output/. Primero ejecuta una extracción.")
    return files[-1]


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("El CSV está vacío.")
    return rows


def build_input(rows):
    first = rows[0]
    lines = [
        f"Cuenta: @{first['username']} (nombre: {first['name']})",
        f"Seguidores: {first['followers']} | Siguiendo: {first['following']}",
        "",
        "DESCRIPCIONES ESCRITAS POR EL DUEÑO:",
    ]
    comments = []
    for r in rows:
        desc = (r.get("video_description") or "").strip()
        lines.append(f"- Video {r.get('video_n', '?')}: {desc or '(sin texto)'}")
        for i in range(1, 6):
            c = (r.get(f"comment_{i}") or "").strip()
            if c:
                comments.append(f"- (video {r.get('video_n', '?')}) {c}")
    lines += ["", "COMENTARIOS DE LA AUDIENCIA (terceros):"]
    lines += comments or ["(no hay comentarios)"]
    return "\n".join(lines)[:MAX_CHARS]


def parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    return json.loads(text)


def is_temporary(msg):
    m = msg.lower()
    return any(t in m for t in ("503", "unavailable", "429", "resource_exhausted",
                                "500", "overloaded", "high demand", "timeout", "deadline"))


def analyze(content):
    key = os.environ.get("GEMINI_API_KEY") or getpass.getpass("Clave de API de Gemini (no se muestra): ")
    client = genai.Client(api_key=key)
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM,
        response_mime_type="application/json",
        temperature=0.3,
    )
    models = list(dict.fromkeys([MODEL] + FALLBACK_MODELS))
    last_msg = ""

    for model in models:
        for attempt in range(len(RETRY_WAITS) + 1):
            try:
                print(f"  Modelo {model}, intento {attempt + 1}...")
                resp = client.models.generate_content(model=model, contents=content, config=config)
                return parse_json(resp.text)
            except json.JSONDecodeError as e:
                last_msg = f"JSON inválido: {e}"
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                last_msg = msg
                low = msg.lower()
                if "api key" in low or "401" in msg or "403" in msg or "permission" in low:
                    sys.exit("La clave de API no es válida o no tiene permiso.")
                if "404" in msg or "not found" in low:
                    print(f"  El modelo {model} no está disponible; pruebo con otro.")
                    break
                if not is_temporary(msg):
                    sys.exit(f"Error al llamar a Gemini: {msg}")
            if attempt < len(RETRY_WAITS):
                wait = RETRY_WAITS[attempt]
                print(f"  Gemini ocupado; reintento en {wait} s...")
                time.sleep(wait)
        else:
            print(f"  {model} sigue sin responder; pruebo con otro modelo.")

    sys.exit(f"No se pudo obtener respuesta de Gemini tras varios intentos: {last_msg}\n"
             "Es probable que haya alta demanda; vuelve a intentarlo en unos minutos.")


def to_markdown(data, user):
    nombres = {"apertura": "Apertura", "responsabilidad": "Responsabilidad",
               "extraversion": "Extraversión", "amabilidad": "Amabilidad",
               "neuroticismo": "Neuroticismo"}
    out = [f"# Análisis de @{user}", "",
           f"Confianza global: **{data.get('confianza_global', '?')}**", "",
           "## Big Five (estimación)", ""]
    for k, label in nombres.items():
        d = data["big_five"].get(k, {})
        out.append(f"**{label}: {d.get('puntaje', '?')}/100** (confianza {d.get('confianza', '?')})")
        out += [f"- {e}" for e in d.get("evidencia", [])] + [""]
    t = data.get("tono_emocional_del_contenido", {})
    out += ["## Tono emocional del contenido", "",
            f"Predominante: **{t.get('predominante', '?')}**", t.get("resumen", ""), ""]
    out += [f"- {e.get('emocion')}: {e.get('intensidad')}/100" for e in t.get("emociones", [])]
    a = data.get("reaccion_de_la_audiencia", {})
    out += ["", "## Reacción de la audiencia", "", f"Tono: **{a.get('tono', '?')}**", a.get("resumen", ""),
            "", "## Conclusión", "", data.get("conclusion", ""), "", "## Limitaciones", ""]
    out += [f"- {l}" for l in data.get("limitaciones", [])]
    out += ["", "_Impresión automática sobre contenido público; no es un diagnóstico psicológico._"]
    return "\n".join(out)


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else latest_csv()
    print(f"Analizando {path.name} con {MODEL}...")
    rows = read_csv(path)
    data = analyze(build_input(rows))

    report = to_markdown(data, rows[0]["username"])
    base = OUTPUT_DIR / f"analisis_{path.stem}"
    base.with_suffix(".json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    base.with_suffix(".md").write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"\nGuardado en {base.with_suffix('.md').name} y {base.with_suffix('.json').name}")


if __name__ == "__main__":
    main()