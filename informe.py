"""Genera el informe de percepción de audiencia como un único archivo HTML.

El archivo no depende de internet (CSS dentro, sin fuentes ni librerías externas),
así que se puede enviar por correo o WhatsApp, abrir en cualquier navegador e
imprimir como PDF (Ctrl+P > Guardar como PDF).
"""
import csv
from datetime import datetime
from html import escape as esc
from pathlib import Path

TRAITS = [
    ("apertura", "Apertura", "curiosidad y creatividad"),
    ("responsabilidad", "Responsabilidad", "orden y constancia"),
    ("extraversion", "Extraversión", "cercanía y energía"),
    ("amabilidad", "Amabilidad", "calidez y cooperación"),
    ("neuroticismo", "Sensibilidad emocional", "tensión o inestabilidad en el tono"),
]

CSS = """
:root { --ink:#1B2A2F; --muted:#5B6B6E; --line:#D3DAD3; --bg:#F4F6F2; --accent:#0F6E63; --mid:#5FA39A; --low:#A9CBC6; --emo:#8A5A2B; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.55 "Segoe UI", system-ui, sans-serif;
       -webkit-print-color-adjust:exact; print-color-adjust:exact; }
main { max-width:800px; margin:0 auto; padding:36px 22px 60px; background:#fff; }
header { border-bottom:3px solid var(--accent); padding-bottom:16px; margin-bottom:8px; }
h1 { font-size:1.8rem; line-height:1.2; margin:0 0 6px; }
h2 { font-size:1.15rem; margin:30px 0 8px; padding-bottom:4px; border-bottom:1px solid var(--line); break-after:avoid; }
h3 { font-size:1rem; margin:0; }
.meta { color:var(--muted); font-size:.92rem; margin:0; }
.hint { color:var(--muted); font-size:.9rem; margin:0 0 12px; }
dl.kv { display:grid; grid-template-columns:max-content 1fr; gap:6px 24px; margin:0; }
dl.kv dt { color:var(--muted); } dl.kv dd { margin:0; font-weight:600; }
.chart { display:grid; grid-template-columns:10.5rem 1fr; gap:12px 14px; align-items:center; }
.name { font-weight:600; line-height:1.25; } .name small { display:block; color:var(--muted); font-weight:400; font-size:.8rem; }
.track { position:relative; height:26px; border-radius:5px; background:#F1F4F0;
         background-image:linear-gradient(to right,var(--line) 1px,transparent 1px); background-size:25% 100%; }
.fill { height:100%; min-width:2px; border-radius:5px; background:var(--accent); }
.fill.media { background:var(--mid); } .fill.baja { background:var(--low); } .fill.emo { background:var(--emo); }
.val { position:absolute; right:8px; top:50%; transform:translateY(-50%); font-weight:700; font-size:.85rem; }
.val.inside { color:#fff; }
.scale { grid-column:2; display:flex; justify-content:space-between; color:var(--muted); font-size:.75rem; margin-top:-6px; }
.legend { color:var(--muted); font-size:.82rem; margin:10px 0 0; }
.trait { padding:10px 0; border-top:1px solid var(--line); break-inside:avoid; }
.trait:first-of-type { border-top:0; }
.trait p { margin:2px 0 0; color:var(--muted); font-size:.9rem; }
.trait ul, ul.plain { margin:6px 0 0; padding-left:20px; }
.rec { padding:10px 14px; border-left:4px solid var(--accent); background:#F1F6F4; margin:10px 0; break-inside:avoid; }
.rec p { margin:2px 0 0; }
.videos li { margin:0 0 6px; overflow-wrap:anywhere; }
.aviso { margin-top:30px; color:var(--muted); font-size:.85rem; border-left:3px solid var(--line); padding-left:12px; }
.toolbar { max-width:800px; margin:14px auto 0; padding:0 22px; text-align:right; }
.toolbar button { font:inherit; font-weight:600; background:var(--accent); color:#fff; border:0; border-radius:8px; padding:10px 18px; cursor:pointer; }
section { break-inside:auto; }
@page { margin:14mm; }
@media print { body { background:#fff; } main { padding:0; max-width:none; } .toolbar { display:none; } }
@media (max-width:560px) { .chart { grid-template-columns:1fr; gap:4px 0; } .name { margin-top:8px; } .scale { grid-column:1; } }
"""


def _clamp(v):
    try:
        return max(0.0, min(100.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _num(v):
    try:
        return f"{int(float(v)):,}".replace(",", ".")
    except (TypeError, ValueError):
        return esc(str(v)) if v not in (None, "") else "—"


def _chart(rows):
    """rows: lista de (etiqueta, subtítulo, valor 0-100, clase CSS)."""
    out = ['<div class="chart">']
    for label, sub, value, cls in rows:
        v = _clamp(value)
        small = f"<small>{esc(sub)}</small>" if sub else ""
        inside = " inside" if v > 88 else ""
        out.append(
            f'<div class="name">{esc(label)}{small}</div>'
            f'<div class="track"><div class="fill {cls}" style="width:{v:.0f}%"></div>'
            f'<span class="val{inside}">{v:.0f}</span></div>'
        )
    out.append('<div class="scale"><span>0</span><span>25</span><span>50</span>'
               '<span>75</span><span>100</span></div></div>')
    return "".join(out)


def read_csv(csv_path):
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return rows


def build_report(data, csv_path, cliente=""):
    """Devuelve el informe completo como texto HTML."""
    rows = read_csv(csv_path)
    first = rows[0] if rows else {}
    user = first.get("username", "")
    name = first.get("name", "")
    n_videos = sum(1 for r in rows if (r.get("video_url") or "").strip())
    n_comments = sum(1 for r in rows for i in range(1, 6) if (r.get(f"comment_{i}") or "").strip())

    # --- Estilo de comunicación (Big Five)
    chart_rows, trait_blocks = [], []
    for key, label, sub in TRAITS:
        t = (data.get("big_five") or {}).get(key, {})
        conf = t.get("confianza") if t.get("confianza") in ("alta", "media", "baja") else "baja"
        chart_rows.append((label, sub, t.get("puntaje", 0), "" if conf == "alta" else conf))
        ev = "".join(f"<li>{esc(str(e))}</li>" for e in (t.get("evidencia") or []) if e)
        trait_blocks.append(
            f'<div class="trait"><h3>{esc(label)}: {_clamp(t.get("puntaje")):.0f}/100</h3>'
            f"<p>Confianza {esc(conf)}</p>{'<ul>' + ev + '</ul>' if ev else ''}</div>"
        )

    # --- Tono emocional
    tono = data.get("tono_emocional_del_contenido") or {}
    emo_rows = [(e.get("emocion", "—"), "", e.get("intensidad", 0), "emo")
                for e in (tono.get("emociones") or []) if isinstance(e, dict)]
    emo_html = _chart(emo_rows) if emo_rows else ""

    # --- Audiencia
    aud = data.get("reaccion_de_la_audiencia") or {}
    aud_text = " ".join(x for x in [f"Tono: {aud['tono']}." if aud.get("tono") else "", aud.get("resumen", "")] if x)

    # --- Recomendaciones
    recs_html = ""
    for r in data.get("recomendaciones") or []:
        if isinstance(r, dict):
            recs_html += (f'<div class="rec"><h3>{esc(str(r.get("titulo", "")))}</h3>'
                          f'<p>{esc(str(r.get("detalle", "")))}</p></div>')
        else:
            recs_html += f'<div class="rec"><p>{esc(str(r))}</p></div>'
    if not recs_html:
        recs_html = ('<p class="hint">Este análisis se generó antes de existir esta sección. '
                     "Vuelve a pulsar «Analizar este CSV» para incluir recomendaciones.</p>")

    # --- Videos analizados
    vids = "".join(
        f"<li><strong>Video {esc(str(r.get('video_n', '')))}:</strong> "
        f"{esc((r.get('video_description') or '(sin texto)').strip())}</li>"
        for r in rows if (r.get("video_url") or "").strip()
    )

    limit = "".join(f"<li>{esc(str(x))}</li>" for x in (data.get("limitaciones") or []))
    fecha = datetime.now().strftime("%d/%m/%Y")
    para = f" · Preparado para: {esc(cliente)}" if cliente else ""
    title = f"Informe de percepción de audiencia - @{user}"

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title><style>{CSS}</style></head>
<body>
<div class="toolbar"><button onclick="window.print()">Imprimir / Guardar como PDF</button></div>
<main>
<header>
  <h1>Informe de percepción de audiencia</h1>
  <p class="meta">@{esc(user)}{' · ' + esc(name) if name else ''} · {fecha}{para}</p>
</header>

<section><h2>Resumen de la cuenta</h2>
<dl class="kv">
  <dt>Seguidores</dt><dd>{_num(first.get('followers'))}</dd>
  <dt>Siguiendo</dt><dd>{_num(first.get('following'))}</dd>
  <dt>Videos analizados</dt><dd>{n_videos}</dd>
  <dt>Comentarios leídos</dt><dd>{n_comments}</dd>
</dl></section>

<section><h2>Estilo de comunicación</h2>
<p class="hint">Estimación de 0 a 100 a partir de lo que la cuenta escribe en sus videos. El tono de la barra indica la confianza.</p>
{_chart(chart_rows)}
<p class="legend">Oscuro: confianza alta · Medio: confianza media · Claro: confianza baja</p>
{''.join(trait_blocks)}</section>

<section><h2>Tono emocional del contenido</h2>
<p class="hint">{esc(('Predominante: ' + tono['predominante'] + '. ') if tono.get('predominante') else '')}{esc(tono.get('resumen', ''))}</p>
{emo_html}</section>

<section><h2>Reacción de la audiencia</h2><p>{esc(aud_text) or 'Sin datos de comentarios.'}</p></section>

<section><h2>Recomendaciones</h2>{recs_html}</section>

<section><h2>Conclusión</h2><p>{esc(data.get('conclusion', '') or 'Sin conclusión.')}</p></section>

<section><h2>Textos analizados</h2><ul class="plain videos">{vids or '<li>Sin videos.</li>'}</ul></section>

<section><h2>Limitaciones</h2><ul class="plain">{limit or '<li>Muestra pequeña de textos.</li>'}</ul></section>

<p class="aviso">Informe automático basado en contenido público y en una muestra pequeña de textos
({n_videos} descripciones y {n_comments} comentarios). Describe el estilo de comunicación y la
percepción del contenido; no es un diagnóstico psicológico ni refleja el estado emocional real de la persona.
Archivo de origen: {esc(Path(csv_path).name)}.</p>
</main></body></html>"""
