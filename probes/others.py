"""Probe INGEMMET, IGP, SERFOR, NASA FIRMS y ENFEN.

Uso:  python3 probes/others.py [fuente ...]   (sin args = todas)
Guarda muestras en samples/<fuente>/<fecha>/ e imprime un resumen.
"""
import collections
import csv
import datetime
import html
import io
import re
import sys

from common import fetch, fetch_json, save

GEOCATMIN = "https://geocatmin.ingemmet.gob.pe/arcgis/rest/services"
SERFOR = "https://geo.serfor.gob.pe/geoservicios/rest/services/UFMS/Incendios_SAMI/MapServer"
PERU_BBOX = (-81.4, -18.4, -68.6, 0.1)  # lon_min, lat_min, lon_max, lat_max


def arcgis_geojson(layer_url, where="1=1", fields="*"):
    q = f"{layer_url}/query?where={where}&outFields={fields}&returnGeometry=true&outSR=4326&f=geojson"
    return fetch_json(q, timeout=180)


def ms_to_date(ms):
    return datetime.datetime.fromtimestamp(int(ms) / 1000, datetime.timezone.utc).date().isoformat() if ms else None


def ingemmet():
    """Zonas críticas de peligro geológico que caen dentro de avisos SENAMHI vigentes (Perú Alerta)."""
    gj = arcgis_geojson(f"{GEOCATMIN}/SERV_PERU_ALERTA/MapServer/0")
    save("ingemmet", "zonas_criticas_alerta.geojson", gj)
    fs = gj["features"]
    p = lambda k: collections.Counter(f["properties"].get(k) for f in fs)  # noqa: E731
    print(f"[ingemmet] {len(fs)} zonas críticas en alerta | niveles {dict(p('NIVEL'))} | avisos {dict(p('NRO_AVISO'))}")
    print(f"[ingemmet] regiones: {p('REGION').most_common(6)}")
    total = fetch_json(f"{GEOCATMIN}/SERV_PELIGROS_GEOLOGICOS/MapServer/2/query?where=1%3D1&returnCountOnly=true&f=json")
    print(f"[ingemmet] inventario base de zonas críticas: {total.get('count')}")


def igp():
    """Catálogo sísmico del año + último sismo."""
    year = datetime.date.today().year
    cat = fetch_json(f"https://ultimosismo.igp.gob.pe/api/ultimo-sismo/ajaxb/{year}")
    last = fetch_json("https://ultimosismo.igp.gob.pe/api/ultimo-sismo")
    save("igp", f"sismos_{year}.json", cat)
    save("igp", "ultimo_sismo.json", last)
    mags = [float(x["magnitud"]) for x in cat if x.get("magnitud")]
    print(f"[igp] {len(cat)} sismos en {year} | M≥4.5: {sum(m >= 4.5 for m in mags)} | M≥6: {sum(m >= 6 for m in mags)}")
    print(f"[igp] último: {last.get('codigo')} M{last.get('magnitud')} {last.get('fecha_hora')} — {last.get('referencia')}")


def serfor():
    """Focos de calor 24 h y alertas de incendio forestal (con estado y ubigeo distrital)."""
    focos = arcgis_geojson(f"{SERFOR}/0", fields="NOMDEP,NOMPRO,NOMDIS,CATDIS,FECHA,HORA,SENSAT,TIPCOB,PELIGRO")
    alertas = arcgis_geojson(f"{SERFOR}/2")
    save("serfor", "focos_calor_24h.geojson", focos)
    save("serfor", "alertas_incendio.geojson", alertas)
    estados = {1: "Alertado", 2: "Confirmado", 3: "Controlado", 4: "Extinguido"}
    fa = alertas["features"]
    print(f"[serfor] focos 24h: {len(focos['features'])} | top dptos "
          f"{collections.Counter(f['properties']['NOMDEP'] for f in focos['features']).most_common(5)}")
    print(f"[serfor] alertas incendio: {len(fa)} | estados "
          f"{dict(collections.Counter(estados.get(f['properties'].get('ESTADO')) for f in fa))}")
    fechas = [f["properties"].get("FECHA") for f in fa if f["properties"].get("FECHA")]
    if fechas:
        print(f"[serfor] rango alertas: {ms_to_date(min(fechas))} → {ms_to_date(max(fechas))}")


def firms():
    """Respaldo sin API key: CSV abierto VIIRS (NOAA-20) 24 h de Sudamérica, recortado a Perú."""
    raw = fetch("https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_South_America_24h.csv",
                timeout=120).decode()
    x0, y0, x1, y1 = PERU_BBOX
    rows = [r for r in csv.DictReader(io.StringIO(raw))
            if x0 <= float(r["longitude"]) <= x1 and y0 <= float(r["latitude"]) <= y1]
    save("firms", "viirs_noaa20_24h_peru_bbox.json", rows)
    print(f"[firms] {len(rows)} detecciones VIIRS NOAA-20 en el bbox de Perú (incluye bordes de países vecinos)")


def enfen():
    """Estado del sistema de alerta ENFEN: último comunicado (página de descargas) + feed."""
    home = fetch("https://enfen.imarpe.gob.pe/").decode("utf-8", "replace")
    save("enfen", "home.html", home)
    comunicados = re.findall(r'href="(https://enfen\.imarpe\.gob\.pe/download/comunicado-oficial-enfen-n-(\d+)-(\d{4})/\?wpdmdl=\d+)', home)
    feed = fetch("https://enfen.imarpe.gob.pe/feed/").decode("utf-8", "replace")
    titles = [html.unescape(t) for t in re.findall(r"<item>.*?<title>(.*?)</title>", feed, re.S)]
    estado_feed = next((t for t in titles if "sistema de alerta" in t.lower()), None)
    out = {"comunicados_descarga": [{"url": u, "numero": int(n), "anio": int(a)} for u, n, a in comunicados],
           "ultimo_estado_en_feed": estado_feed}
    if comunicados:
        url, n, a = max(comunicados, key=lambda c: (int(c[2]), int(c[1])))
        pdf = fetch(url, timeout=120)
        save("enfen", f"comunicado_{n}_{a}.pdf", pdf)
        try:
            import fitz
            text = fitz.open(stream=pdf, filetype="pdf")[0].get_text()
            m = re.search(r"Estado del sistema de alerta:\s*(.+)", text)
            fecha = re.search(r"COMUNICADO OFICIAL ENFEN N° ?\d+-\d{4}\s*\n\s*(.+)", text)
            out["ultimo_comunicado"] = {"numero": int(n), "anio": int(a),
                                        "fecha": fecha.group(1).strip() if fecha else None,
                                        "estado": re.sub(r"\d+$", "", m.group(1).strip()) if m else None,
                                        "resumen": text.split("RESUMEN EJECUTIVO")[-1][:1200].strip()}
        except ImportError:
            pass
    save("enfen", "estado.json", out)
    uc = out.get("ultimo_comunicado", {})
    print(f"[enfen] último comunicado N°{uc.get('numero')}-{uc.get('anio')} ({uc.get('fecha')}): {uc.get('estado')}")
    print(f"[enfen] feed (posts) llega hasta: {estado_feed}")


PROBES = {"ingemmet": ingemmet, "igp": igp, "serfor": serfor, "firms": firms, "enfen": enfen}

if __name__ == "__main__":
    for name in sys.argv[1:] or PROBES:
        try:
            PROBES[name]()
        except Exception as e:  # noqa: BLE001 — un probe caído no detiene los demás
            print(f"[{name}] ERROR: {e}")
