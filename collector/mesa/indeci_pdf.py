"""Procesa el PDF de un reporte INDECI: texto (ubicación, daños, contexto), código SINPAD, fotos del anexo y mapa de ubicación.

Las fotos del anexo "VISTAS FOTOGRÁFICAS" son fotos de campo (COER, municipalidades). El mapa de la página 1 está
hecho sobre Google Earth / Airbus: se guarda aparte y se marca como no apto para emisión.
"""
import collections
import hashlib
import html
import re

import pymupdf

from . import config, db, indeci_danos
from .http import fetch, fetch_text

MEDIA = config.DATA / "media" / "indeci"
MIN_W, MIN_H = 250, 150
NOISE = re.compile(r"P á g i n a|Distribución: A los tres|Centro de Operaciones de Emergencia Nacional|Av\. El Sol|COENPeru|www\.indeci", re.I)
DATE_RE = re.compile(r"\b(\d{1,2}\s+(?:ENE|FEB|MAR|ABR|MAY|JUN|JUL|AGO|SET|SEP|OCT|NOV|DIC)\s+\d{4})\b")


def _save_jpeg(doc, xref, path):
    pix = pymupdf.Pixmap(doc, xref)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.colorspace and pix.colorspace.n not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    data = pix.tobytes("jpeg", jpg_quality=84)
    path.write_bytes(data)
    return hashlib.sha1(data).hexdigest()[:16], pix.width, pix.height


def _caption(page, rect):
    """Leyenda de una foto: INDECI la pone sobre el borde inferior de la imagen o justo debajo."""
    best, best_d = None, 1e9
    for x0, y0, x1, y1, text, *_ in page.get_text("blocks"):
        text = re.sub(r"\s+", " ", text).strip()
        if not text or NOISE.search(text) or re.fullmatch(r"ANEXO.*|VISTAS? FOTOGR[ÁA]FICAS?|\d{1,2} \w{3} \d{4}", text, re.I):
            continue
        if min(x1, rect.x1) - max(x0, rect.x0) < 20:        # sin solapamiento horizontal
            continue
        if rect.y1 - 40 <= y0 <= rect.y1 + 45:
            d = abs(y0 - (rect.y1 - 12))
            if d < best_d:
                best, best_d = text, d
    return best


def extract(pdf_bytes, key):
    """Devuelve {pdf_text, sinpad, fotos:[…], mapa:{…}|None}; guarda las imágenes en data/media/indeci/<hash>/."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    folder = MEDIA / hashlib.sha1(key.encode()).hexdigest()[:12]
    folder.mkdir(parents=True, exist_ok=True)
    rel = folder.relative_to(config.DATA / "media")

    pages_of = collections.defaultdict(set)
    for i, page in enumerate(doc):
        for im in page.get_images(full=True):
            pages_of[im[0]].add(i)
    text_pages = [p.get_text() for p in doc]
    annex = {i for i, t in enumerate(text_pages) if re.search(r"VISTAS? FOTOGR[ÁA]FICAS?", t, re.I) and "ANEXO" in t.upper()}
    fotos, mapa, seen = [], None, set()
    for i, page in enumerate(doc):
        for info in page.get_image_info(xrefs=True):
            xref = info.get("xref")
            if not xref or len(pages_of[xref]) > 1 or info["width"] < MIN_W or info["height"] < MIN_H or xref in seen:
                continue
            seen.add(xref)
            rect = pymupdf.Rect(info["bbox"])
            if i == 0 and mapa is None:
                sha, w, h = _save_jpeg(doc, xref, folder / "mapa.jpg")
                mapa = {"url": f"/media/{rel}/mapa.jpg", "w": w, "h": h, "page": 1,
                        "nota": "Mapa de ubicación de INDECI sobre Google Earth / Airbus: no apto para emisión."}
                continue
            if annex and i not in annex:
                continue
            n = len(fotos) + 1
            sha, w, h = _save_jpeg(doc, xref, folder / f"foto{n}.jpg")
            fecha = DATE_RE.search(text_pages[i])
            fotos.append({"url": f"/media/{rel}/foto{n}.jpg", "w": w, "h": h, "page": i + 1, "sha": sha,
                          "caption": _caption(page, rect), "fecha": fecha.group(1) if fecha else None})

    txt = "\n".join(text_pages)
    txt = re.sub(r"Distribución: A los tres niveles.*?P á g i n a\s+\d+\s*\|\s*\d+", "", txt, flags=re.S)
    m = re.search(r"(2\.\s*UBICACI[ÓO]N:.*?)(4\.\s*ACCIONES|$)", txt, re.S)
    section = re.sub(r"[ \t]+", " ", (m.group(1) if m else txt)[:6000])
    sinpad = re.search(r"SINPAD\s*N\.?\s*[°º]?\s*(\d{5,})", txt)
    try:
        danos = indeci_danos.extract(doc)
    except Exception as e:  # noqa: BLE001 — una tabla rara no debe impedir guardar fotos y texto
        danos = {"error": f"{type(e).__name__}: {e}"}
    act = re.search(r"Actualizado al ([^\n]*?\d{1,2}:\d{2}) horas", txt)
    return {"danos": danos, "danos_actualizado": act.group(1).strip() if act else None, "pdf_text": re.sub(r"\n\s*\n+", "\n", re.sub(r" *\n *", "\n", section)).strip(),
            "sinpad": sinpad.group(1) if sinpad else None, "fotos": fotos, "mapa": mapa, "paginas": len(doc)}


def process(item):
    """Baja la página y el PDF de un ítem INDECI y guarda el resultado como items(indeci, reporte_pdf, guid)."""
    page = fetch_text("indeci", item["link"], name="reporte.html")
    body = re.sub(r"<script.*?</script>|<style.*?</style>", "", page.split("</head>")[-1], flags=re.S)
    pdf = re.search(r'href="([^"]+\.pdf)"', body)
    hechos = re.search(r"HECHOS:(.*?)(DESCARGAR ARCHIVO|Enlaces de Inter)", body, re.S)
    rec = {"guid": item["_key"], "hechos": html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", hechos.group(1)))).strip() if hechos else None,
           "pdf_url": html.unescape(pdf.group(1)) if pdf else None, "fotos": [], "mapa": None}
    if rec["pdf_url"]:
        rec.update(extract(fetch("indeci", rec["pdf_url"], name="reporte.pdf", timeout=90), item["_key"]))
    db.upsert_items("indeci", "reporte_pdf", {item["_key"]: rec})
    return rec


def pending(limit, hours=72):
    """Reportes recientes aún sin procesar, del más nuevo al más viejo."""
    import time
    cutoff = time.time() - hours * 3600
    done = {r["key"] for r in db.conn().execute("SELECT key FROM items WHERE source='indeci' AND kind='reporte_pdf'")}
    items = [i for i in db.get_items("indeci", "item", current_only=False, limit=800)
             if i.get("clase") == "reporte" and (i.get("ts") or 0) >= cutoff and i["_key"] not in done]
    return sorted(items, key=lambda i: i.get("ts") or 0, reverse=True)[:limit]


def run_pending(limit=15):
    todo, fotos, errores = pending(limit), 0, []
    for it in todo:
        try:
            fotos += len(process(it)["fotos"])
        except Exception as e:  # noqa: BLE001 — un PDF roto no detiene la cola
            errores.append(f"{it.get('num')}: {type(e).__name__}")
            db.upsert_items("indeci", "reporte_pdf", {it["_key"]: {"guid": it["_key"], "error": f"{type(e).__name__}: {e}", "fotos": [], "mapa": None}})
    left = len(pending(10_000))
    msg = f"{len(todo)} reportes procesados · {fotos} fotos · quedan {left} en cola"
    return len(todo), fotos, msg + (f" · errores: {', '.join(errores)}" if errores else "")


def reprocess_missing_danos(limit=200):
    """Vuelve a procesar reportes ya guardados que aún no tienen la clave "danos" (se agregó después)."""
    rows = db.get_items("indeci", "reporte_pdf", current_only=False)
    todo = [r for r in rows if "danos" not in r and r.get("pdf_url")][:limit]
    items = {i["_key"]: i for i in db.get_items("indeci", "item", current_only=False, limit=5000)}
    done = 0
    for r in todo:
        it = items.get(r["_key"])
        if it:
            try:
                process(it)
                done += 1
            except Exception:  # noqa: BLE001
                pass
    return done, len(todo)
