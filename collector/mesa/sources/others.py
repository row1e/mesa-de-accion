"""INDECI, IGP, SERFOR, INGEMMET, ENFEN, FIRMS y verificación de acceso a PROVIAS."""
import csv
import datetime
import html
import io
import re
import socket

from shapely.geometry import Point
from shapely.ops import unary_union
from shapely.prepared import prep

from .. import config, db, geo
from ..http import fetch, fetch_json, fetch_text

# ── INDECI ─────────────────────────────────────────────────────────────────────
FEED = "https://portal.indeci.gob.pe/emergencias/feed/"
TITLE_RE = re.compile(
    r"(?P<tipo>REPORTE PRELIMINAR|REPORTE COMPLEMENTARIO|INFORME DE EMERGENCIA)\s+N\.?\s*[°º]?\s*(?:O\s*)?(?P<num>\d+)\s*[–-]\s*"
    r"(?P<fecha>\d{1,2}/\d{1,2}/\d{4}).*?(?P<hora>\d{1,2}:\d{2})\s*HORAS\s*(?:\((?:Reporte|Informe) N\.?\s*[°º]?\s*(?P<seq>\d+)\))?\s*"
    r"(?P<evento>.+?) EN (?:EL DISTRITO DE )?(?P<distrito>.+?)\s*[–-]\s*(?P<dpto>[^–-]+)$", re.I)


def _indeci_item(raw):
    def g(tag):
        m = re.search(f"<{tag}>(.*?)</{tag}>", raw, re.S)
        return html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))).strip() if m else ""

    title = re.sub(r"\s+", " ", g("title"))
    desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", g("description"))).split(" The post ")[0].strip()
    it = {"pub": g("pubDate"), "titulo": title, "link": g("link"), "guid": g("guid") or g("link"), "descripcion": desc}
    up = title.upper()
    m = TITLE_RE.search(title)
    if m:
        it.update({k: (v.strip() if v else v) for k, v in m.groupdict().items()})
        it["clase"] = "reporte"
        # "SISMO … EN MORONA – DATEM DEL MARAÑÓN – LORETO": el distrito captura "MORONA – DATEM DEL MARAÑÓN"
        parts = re.split(r"\s*[–-]\s*", it["distrito"])
        if len(parts) > 1:
            it["distrito"], it["provincia_titulo"] = parts[0], parts[-1]
    elif "AVISO METEOROL" in up:
        it["clase"] = "boletin_aviso_meteorologico"
    elif "SISMIC" in up or "SÍSMIC" in up:
        it["clase"] = "boletin_sismico"
    elif "CORTO PLAZO" in up:
        it["clase"] = "boletin_aviso_corto_plazo"
    elif "MONITOREO DE PELIGROS" in up:
        it["clase"] = "boletin_monitoreo_peligros"
    else:
        it["clase"] = "otro"
    prov = re.search(r"provincia de ([^,.]+)", desc, re.I)
    it["provincia"] = prov.group(1).strip() if prov else it.get("provincia_titulo")
    if it["clase"] == "reporte":
        it["prov"] = geo.province_code(it.get("dpto"), it.get("provincia"))
    try:
        it["ts"] = datetime.datetime.strptime(it["pub"], "%a, %d %b %Y %H:%M:%S %z").timestamp()
    except ValueError:
        it["ts"] = None
    return it


def indeci():
    first = not db.conn().execute("SELECT 1 FROM items WHERE source='indeci' LIMIT 1").fetchone()
    pages = config.INDECI_PAGES_FIRST_RUN if first else 5
    recs, seen_old = {}, False
    for p in range(1, pages + 1):
        xml = fetch_text("indeci", f"{FEED}?paged={p}", name=f"feed_p{p}.xml")
        items = [_indeci_item(x) for x in re.findall(r"<item>(.*?)</item>", xml, re.S)]
        for it in items:
            if db.has_item("indeci", "item", it["guid"]):
                seen_old = True
            recs[it["guid"]] = it
        if seen_old and not first:
            break
    total, new = db.upsert_items("indeci", "item", recs)
    reps = sum(1 for r in recs.values() if r["clase"] == "reporte")
    return total, new, f"{new} ítems nuevos ({reps} reportes en las páginas leídas)"


# ── IGP ────────────────────────────────────────────────────────────────────────
def igp():
    last = fetch_json("igp", "https://ultimosismo.igp.gob.pe/api/ultimo-sismo", name="ultimo.json", keep_raw=False)
    if db.has_item("igp", "sismo", last["codigo"]):
        return 0, 0, f"sin cambios · último {last['codigo']} M{last['magnitud']}"
    year = last["codigo"][:4]
    cat = fetch_json("igp", f"https://ultimosismo.igp.gob.pe/api/ultimo-sismo/ajaxb/{year}", name=f"sismos_{year}.json")
    recs = {s["codigo"]: {"codigo": s["codigo"], "fecha": s["fecha_local"][:10], "hora": s["hora_local"][11:16],
                          "mag": float(s["magnitud"]), "prof": int(float(s["profundidad"])), "lat": float(s["latitud"]),
                          "lon": float(s["longitud"]), "ref": s["referencia"], "int": s.get("intensidad") or ""}
            for s in cat}
    total, new = db.upsert_items("igp", "sismo", recs)
    return total, new, f"{new} sismos nuevos · último {last['codigo']} M{last['magnitud']} — {last['referencia']}"


# ── ArcGIS helpers ─────────────────────────────────────────────────────────────
def _arcgis(source, layer, fields="*", name=None):
    return fetch_json(source, f"{layer}/query?where=1%3D1&outFields={fields}&returnGeometry=true&outSR=4326&f=geojson",
                      name=name, timeout=180)


SERFOR = "https://geo.serfor.gob.pe/geoservicios/rest/services/UFMS/Incendios_SAMI/MapServer"
ESTADOS = {1: "Alertado", 2: "Confirmado", 3: "Controlado", 4: "Extinguido"}


def _ms_date(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).date().isoformat() if ms else None


def serfor():
    focos = _arcgis("serfor", f"{SERFOR}/0", "OBJECTID,NOMDEP,NOMPRO,NOMDIS,CATDIS,FECHA,HORA,SENSAT", "focos.geojson")
    frec = {str(f["properties"]["OBJECTID"]): {"lon": round(f["geometry"]["coordinates"][0], 4),
                                              "lat": round(f["geometry"]["coordinates"][1], 4),
                                              "dep": f["properties"]["NOMDEP"], "prov": f["properties"]["NOMPRO"],
                                              "ubigeo": f["properties"]["CATDIS"], "fecha": _ms_date(f["properties"].get("FECHA")),
                                              "hora": f["properties"].get("HORA"), "sensor": f["properties"].get("SENSAT")}
            for f in focos["features"] if f.get("geometry")}
    alertas = _arcgis("serfor", f"{SERFOR}/2", name="alertas.geojson")
    arec = {}
    for f in alertas["features"]:
        p = f["properties"]
        if not f.get("geometry"):
            continue
        arec[str(p["OBJECTID"])] = {  # CODREP se repite entre alertas de un mismo reporte PIF
            "lon": round(f["geometry"]["coordinates"][0], 4), "lat": round(f["geometry"]["coordinates"][1], 4),
            "estado": ESTADOS.get(p.get("ESTADO"), str(p.get("ESTADO"))), "dep": p["NOMDEP"], "prov": p["NOMPRO"],
            "dist": p["NOMDIS"], "ubigeo": p["CATDIS"], "cod": p.get("CODREP"), "fecha": _ms_date(p.get("FECHA")),
            "hora": p.get("HORA"), "cob": p.get("TIPCOB")}
    nf, newf = db.upsert_items("serfor", "foco", frec, snapshot=True)
    na, newa = db.upsert_items("serfor", "alerta", arec, snapshot=True)
    return nf + na, newf + newa, f"{nf} focos 24 h ({newf} nuevos) · {na} alertas ({newa} nuevas)"


def ingemmet():
    gj = _arcgis("ingemmet", "https://geocatmin.ingemmet.gob.pe/arcgis/rest/services/SERV_PERU_ALERTA/MapServer/0",
                 name="zonas_alerta.geojson")
    recs = {}
    for f in gj["features"]:
        p = f["properties"]
        if not f.get("geometry"):
            continue
        clean = {k.lower(): (v.strip() if isinstance(v, str) else v) for k, v in p.items()
                 if k in ("REGION", "PROVINCIA", "DISTRITO", "PARAJE", "PELIGROS_G", "ELEMENTO", "NIVEL", "NRO_AVISO")}
        recs[f"{p.get('NRO_AVISO')}-{p['OBJECTID']}"] = {"lon": round(f["geometry"]["coordinates"][0], 4),
                                                         "lat": round(f["geometry"]["coordinates"][1], 4), **clean}
    total, new = db.upsert_items("ingemmet", "zona_alerta", recs, snapshot=True)
    avisos = sorted({str(r.get("nro_aviso")) for r in recs.values()})
    return total, new, f"{total} zonas críticas en alerta (avisos {', '.join(avisos) or '—'})"


# ── ENFEN ──────────────────────────────────────────────────────────────────────
def enfen():
    home = fetch_text("enfen", "https://enfen.imarpe.gob.pe/", name="home.html")
    links = re.findall(r'href="(https://enfen\.imarpe\.gob\.pe/download/comunicado-oficial-enfen-n-(\d+)-(\d{4})/\?wpdmdl=\d+)', home)
    if not links:
        raise RuntimeError("no se encontraron enlaces a comunicados en la portada")
    new = 0
    for url, n, a in sorted(set(links), key=lambda c: (int(c[2]), int(c[1]))):
        key = f"{a}-{int(n):02d}"
        if db.has_item("enfen", "comunicado", key):
            continue
        pdf = fetch("enfen", url, name=f"comunicado_{n}_{a}.pdf", timeout=120)
        import pymupdf
        text = pymupdf.open(stream=pdf, filetype="pdf")[0].get_text()
        m = re.search(r"Estado del sistema de alerta:\s*(.+)", text)
        fecha = re.search(r"COMUNICADO OFICIAL ENFEN N° ?\d+-\d{4}\s*\n\s*(.+)", text)
        db.upsert_items("enfen", "comunicado", {key: {
            "numero": int(n), "anio": int(a), "url": url, "fecha": fecha.group(1).strip() if fecha else None,
            "estado": re.sub(r"\d+$", "", m.group(1).strip()) if m else None,
            "resumen": text.split("RESUMEN EJECUTIVO")[-1][:1500].strip()}})
        new += 1
    last = db.get_items("enfen", "comunicado", order="key DESC", limit=1)[0]
    return len(links), new, f"último N°{last['numero']}-{last['anio']}: {last['estado']}"


# ── FIRMS ──────────────────────────────────────────────────────────────────────
_peru = None


def firms():
    global _peru
    if _peru is None:
        _peru = prep(unary_union([p["geom"] for p in geo.provinces().values()]).buffer(0.02))
    raw = fetch("firms", "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_South_America_24h.csv",
                name="viirs_n20_sa_24h.csv", timeout=120, keep_raw=False).decode()
    recs = {}
    for r in csv.DictReader(io.StringIO(raw)):
        lon, lat = float(r["longitude"]), float(r["latitude"])
        if -81.5 <= lon <= -68.5 and -18.5 <= lat <= 0.2 and _peru.contains(Point(lon, lat)):
            recs[f"{lat:.4f},{lon:.4f},{r['acq_date']},{r['acq_time']}"] = {
                "lat": lat, "lon": lon, "fecha": r["acq_date"], "hora": r["acq_time"], "conf": r["confidence"], "frp": float(r["frp"])}
    total, new = db.upsert_items("firms", "deteccion", recs, snapshot=True)
    return total, new, f"{total} detecciones VIIRS NOAA-20 dentro del Perú"


# ── PROVIAS (solo accesibilidad) ───────────────────────────────────────────────
def provias():
    for host in ("www.pvn.gob.pe", "sinac.proviasnac.gob.pe"):
        try:
            socket.create_connection((host, 443), timeout=15).close()
        except OSError as e:
            raise RuntimeError(f"{host}:443 no acepta conexión ({type(e).__name__})") from None
    fetch("provias", "https://www.pvn.gob.pe/", name="home.html")
    return 0, 0, "servidor accesible — falta implementar el lector de emergencias viales"
