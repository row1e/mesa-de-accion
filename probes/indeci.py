"""Probe INDECI/COEN: feed RSS de reportes (preliminares, complementarios, boletines) + PDF del reporte.

Uso:  python3 probes/indeci.py [paginas_feed]   (default 3 páginas ≈ 42 ítems)
Guarda muestras en samples/indeci/<fecha>/ e imprime un resumen.
"""
import collections
import html
import re
import sys

from common import fetch, save

FEED = "https://portal.indeci.gob.pe/emergencias/feed/"
SRC = "indeci"

TITLE_RE = re.compile(
    r"REPORTE (?P<tipo>PRELIMINAR|COMPLEMENTARIO)\s+N\.?\s*[°º]?\s*(?:O\s*)?(?P<num>\d+)\s*[–-]\s*(?P<fecha>\d{1,2}/\d{1,2}/\d{4})"
    r".*?(?P<hora>\d{1,2}:\d{2})\s*HORAS\s*(?:\(Reporte N\.?\s*[°º]?\s*(?P<seq>\d+)\))?\s*(?P<evento>.+?) EN EL DISTRITO DE (?P<distrito>.+?)\s*[–-]\s*(?P<dpto>.+)$",
    re.I,
)


def parse_item(raw):
    def g(tag):
        m = re.search(f"<{tag}>(.*?)</{tag}>", raw, re.S)
        return html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))).strip() if m else ""

    title = re.sub(r"\s+", " ", g("title"))
    desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", g("description"))).split(" The post ")[0].strip()
    item = {"pub": g("pubDate"), "titulo": title, "link": g("link"), "descripcion": desc,
            "categorias": re.findall(r"<category><!\[CDATA\[(.*?)\]\]></category>", raw)}
    m = TITLE_RE.search(title)
    if m:
        item.update({k: (v.strip() if v else v) for k, v in m.groupdict().items()})
        item["clase"] = "reporte"
    elif "AVISO METEOROL" in title.upper():
        item["clase"] = "boletin_aviso_meteorologico"
    elif "SISMIC" in title.upper() or "SÍSMIC" in title.upper():
        item["clase"] = "boletin_sismico"
    elif "CORTO PLAZO" in title.upper():
        item["clase"] = "boletin_aviso_corto_plazo"
    elif "MONITOREO DE PELIGROS" in title.upper():
        item["clase"] = "boletin_monitoreo_peligros"
    else:
        item["clase"] = "otro"
    prov = re.search(r"provincia de ([^,.]+)", desc, re.I)
    item["provincia"] = prov.group(1).strip() if prov else None
    return item


def feed(pages):
    items = []
    for p in range(1, pages + 1):
        xml = fetch(f"{FEED}?paged={p}").decode("utf-8", "replace")
        items += [parse_item(x) for x in re.findall(r"<item>(.*?)</item>", xml, re.S)]
    return items


def report_pdf(link):
    """Descarga el PDF adjunto de un reporte y extrae UBICACIÓN + primeras secciones (requiere PyMuPDF)."""
    page = fetch(link).decode("utf-8", "replace")
    m = re.search(r'href="([^"]+\.pdf)"', page)
    if not m:
        return None, None
    pdf = fetch(html.unescape(m.group(1)))
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return pdf, None
    text = "\n".join(p.get_text() for p in fitz.open(stream=pdf, filetype="pdf"))
    return pdf, text


def main():
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    items = feed(pages)
    save(SRC, "feed_items.json", items)
    print(f"[feed] {len(items)} ítems ({pages} páginas) | {items[-1]['pub']} → {items[0]['pub']}")
    print(f"[feed] clases: {dict(collections.Counter(i['clase'] for i in items))}")
    reps = [i for i in items if i["clase"] == "reporte"]
    print(f"[feed] reportes parseados: {len(reps)} | eventos: {dict(collections.Counter(r['evento'].upper() for r in reps))}")
    print(f"[feed] con provincia en la descripción: {sum(1 for r in reps if r['provincia'])}/{len(reps)}")
    unparsed = [i["titulo"] for i in items if i["clase"] == "otro"]
    if unparsed:
        print(f"[feed] sin clasificar ({len(unparsed)}): {unparsed[:3]}")
    if reps:
        r = next((x for x in reps if x["tipo"].upper() == "COMPLEMENTARIO"), reps[0])
        pdf, text = report_pdf(r["link"])
        if pdf:
            save(SRC, f"reporte_{r['num']}.pdf", pdf)
            if text:
                save(SRC, f"reporte_{r['num']}.txt", text)
                secciones = [s for s in ["HECHOS", "UBICACIÓN", "EVALUACIÓN DE DAÑOS", "ACCIONES"] if s in text]
                print(f"[pdf] reporte {r['num']} ({r['evento']} – {r['distrito']}): {len(text)} chars, secciones {secciones}")
        else:
            print(f"[pdf] reporte {r['num']}: sin PDF adjunto")


if __name__ == "__main__":
    main()
