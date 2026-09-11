"""Construye dashboard/index.html a partir de las muestras más recientes de samples/.

Uso:  .venv/bin/python dashboard/build.py
Requiere shapely (cruce polígonos de aviso × provincias).
"""
import collections
import datetime
import glob
import json
import pathlib
import re
import unicodedata

from shapely.geometry import shape
from shapely.validation import make_valid

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
MIN_SHARE = 0.03  # fracción mínima del área provincial cubierta para contar la provincia en un aviso


def latest(source):
    dirs = sorted(d for d in (SAMPLES / source).glob("20??-??-??") if d.is_dir())
    return dirs[-1] if dirs else None


def load(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().upper()


# ── Límites ────────────────────────────────────────────────────────────────────
deps_gj = load(ROOT / "ref/limites_0.geojson")
provs_gj = load(ROOT / "ref/limites_1.geojson")
deps_gj["features"] = [f for f in deps_gj["features"] if f["properties"]["CD_DEPA"] != "99"]
provs_gj["features"] = [f for f in provs_gj["features"] if f["properties"]["CD_DEPA"] != "99"]
provs = {}
for f in provs_gj["features"]:
    p = f["properties"]
    g = make_valid(shape(f["geometry"]))
    provs[p["CD_PROV"]] = {"geom": g, "nombre": p["NM_PROV"], "dpto": p["NM_DEPA"],
                           "c": [round(g.representative_point().x, 3), round(g.representative_point().y, 3)]}
    f["properties"] = {"id": p["CD_PROV"], "n": p["NM_PROV"], "d": p["NM_DEPA"]}
for f in deps_gj["features"]:
    f["properties"] = {"id": f["properties"]["CD_DEPA"], "n": f["properties"]["NM_DEPA"]}
prov_by_name = {(norm(v["dpto"]), norm(v["nombre"])): k for k, v in provs.items()}
prov_by_pname = collections.defaultdict(list)
for k, v in provs.items():
    prov_by_pname[norm(v["nombre"])].append(k)

# ── SENAMHI: avisos → nivel por provincia y día ───────────────────────────────
sen = latest("senamhi")
lista = load(sen / "avisos_lista.json")
vig = {a["nro"]: a for a in lista if a["estado"] in ("vigente", "emitido")}
by_day = collections.defaultdict(dict)  # fecha -> prov -> {"max": n, "avisos": [(nro, n)]}
aviso_days = []
for path in sorted(sen.glob("aviso_*_*_*.geojson")):
    nro, mapa, anio = map(int, re.match(r"aviso_(\d+)_(\d+)_(\d+)", path.name).groups())
    gj = load(path)
    if not gj["features"]:
        continue
    fecha = gj["features"][0]["properties"]["fech_ini"][:10]
    aviso_days.append({"nro": nro, "mapa": mapa, "fecha": fecha})
    for feat in gj["features"]:
        lvl = int(feat["properties"]["nivel"].split()[-1])
        if lvl < 2:  # Nivel 1 = "no es necesario tomar precauciones"
            continue
        g = make_valid(shape(feat["geometry"]))
        for code, pv in provs.items():
            if not g.intersects(pv["geom"]):
                continue
            share = g.intersection(pv["geom"]).area / pv["geom"].area
            if share < MIN_SHARE:
                continue
            cell = by_day[fecha].setdefault(code, {"max": 0, "avisos": {}})
            cell["avisos"][nro] = max(cell["avisos"].get(nro, 0), lvl)
            cell["max"] = max(cell["max"], lvl)
avisos_out = []
for nro, a in sorted(vig.items(), reverse=True):
    txt = load(sen / f"aviso_{nro}_1_{a['emision'][:4]}_provincias.json") if (sen / f"aviso_{nro}_1_{a['emision'][:4]}_provincias.json").exists() else {}
    avisos_out.append({**{k: a[k] for k in ("nro", "estado", "titulo", "emision", "inicio", "fin", "duracion", "nivel")},
                       "dias": sorted(d["fecha"] for d in aviso_days if d["nro"] == nro),
                       "dptos_texto": txt.get("departamentos", [])})
levels_by_day = {d: {p: {"m": c["max"], "a": sorted(c["avisos"].items(), reverse=True)} for p, c in cells.items()}
                 for d, cells in sorted(by_day.items())}
hist = collections.Counter((a["emision"][:4], a["nivel"]) for a in lista)
avisos_hist = {y: {n: hist[(y, n)] for n in ("AMARILLO", "NARANJA", "ROJO")} for y in sorted({a["emision"][:4] for a in lista})}

uv = load(sen / "uv_provincias.json")
uv_out, uv_unmatched = {}, []
for z in uv:
    code = z["c_cod_zona"]
    vals = [round(float(p["n_indice"]), 1) for p in z["pronostico"]]
    rec = {"z": z["v_nom_zona"].replace("_", " "), "v": vals, "h": [p["d_hora_punta"] for p in z["pronostico"]],
           "dias": [p["d_fec_diapron"] for p in z["pronostico"]]}
    if code in provs:
        prev = uv_out.get(code)
        if not prev or vals[0] > prev["v"][0]:
            uv_out[code] = rec
    else:
        uv_unmatched.append(f"{code} {rec['z']}")

pron = load(sen / "pronostico_ciudades.json")
hidro = load(sen / "avisos_hidrologicos.geojson")
hidro_out = [{"lon": f["geometry"]["coordinates"][0], "lat": f["geometry"]["coordinates"][1],
              **{k: f["properties"].get(k) for k in ("nom_estacion", "fecha_hora", "nivel", "color_text", "titulo",
                                                     "nom_departamento", "nom_provincia", "nom_distrito", "nom_cuenca",
                                                     "peligro_nivel")}} for f in hidro["features"]]

# ── INDECI ────────────────────────────────────────────────────────────────────
ind = latest("indeci")
feed = load(ind / "feed_items.json")
indeci_out, unlocated = [], 0
for it in feed:
    code = None
    if it.get("clase") == "reporte":
        dp = norm(re.split(r"\s*[–-]\s*", it.get("dpto") or "")[-1] if it.get("dpto") else "")
        dp = {"LIMA METROPOLITANA": "LIMA", "ANCASH": "ANCASH"}.get(dp, dp)
        pv = norm(it.get("provincia"))
        code = prov_by_name.get((dp, pv))
        if not code and len(prov_by_pname.get(pv, [])) == 1:
            code = prov_by_pname[pv][0]
        if not code:
            unlocated += 1
    indeci_out.append({k: it.get(k) for k in ("pub", "titulo", "link", "descripcion", "clase", "tipo", "num", "seq",
                                             "evento", "distrito", "dpto", "provincia", "hora")} | {"prov": code})

# ── IGP / SERFOR / INGEMMET / ENFEN / FIRMS ───────────────────────────────────
igp_dir = latest("igp")
sismos = load(next(igp_dir.glob("sismos_*.json")))
sismos_out = []
for s in sismos:
    fecha = s["fecha_local"][:10]
    hora = s["hora_local"][11:16]
    sismos_out.append([fecha, hora, float(s["magnitud"]), int(float(s["profundidad"])), float(s["latitud"]),
                       float(s["longitud"]), s["referencia"], s.get("intensidad") or "", s["codigo"]])

ser = latest("serfor")
focos = load(ser / "focos_calor_24h.geojson")
focos_out = [[round(f["geometry"]["coordinates"][0], 3), round(f["geometry"]["coordinates"][1], 3),
              f["properties"]["NOMDEP"], f["properties"]["NOMPRO"]] for f in focos["features"] if f.get("geometry")]
estados = {1: "Alertado", 2: "Confirmado", 3: "Controlado", 4: "Extinguido"}
alertas = load(ser / "alertas_incendio.geojson")
alertas_out = []
for f in alertas["features"]:
    p = f["properties"]
    alertas_out.append({"lon": round(f["geometry"]["coordinates"][0], 4), "lat": round(f["geometry"]["coordinates"][1], 4),
                        "estado": estados.get(p.get("ESTADO"), str(p.get("ESTADO"))), "dep": p["NOMDEP"],
                        "prov": p["NOMPRO"], "dist": p["NOMDIS"], "ubigeo": p["CATDIS"], "cod": p.get("CODREP"),
                        "fecha": datetime.datetime.fromtimestamp(p["FECHA"] / 1000, datetime.timezone.utc).date().isoformat()
                        if p.get("FECHA") else None, "hora": p.get("HORA"), "cob": p.get("TIPCOB")})

ing = latest("ingemmet")
zonas = load(ing / "zonas_criticas_alerta.geojson")
zonas_out = [{"lon": round(f["geometry"]["coordinates"][0], 4), "lat": round(f["geometry"]["coordinates"][1], 4),
              **{k.lower(): (f["properties"].get(k) or "").strip() if isinstance(f["properties"].get(k), str) else f["properties"].get(k)
                 for k in ("REGION", "PROVINCIA", "DISTRITO", "PARAJE", "PELIGROS_G", "ELEMENTO", "NIVEL", "NRO_AVISO")}}
             for f in zonas["features"] if f.get("geometry")]

enfen = load(latest("enfen") / "estado.json")
firms_n = len(load(next(latest("firms").glob("*.json")))) if latest("firms") else None

snapshot = max(d.name for d in SAMPLES.glob("*/20??-??-??"))

sources = [
    {"id": "senamhi-avisos", "org": "SENAMHI", "name": "Avisos meteorológicos", "via": "HTML + WFS GeoServer",
     "geo": "Polígono → provincia", "status": "directo", "sheet": "Muerto desde 07-07-2026 (#269), sin nivel",
     "n": f"{len(lista):,} avisos · {len(vig)} vigentes/emitidos"},
    {"id": "senamhi-uv", "org": "SENAMHI", "name": "Índice UV", "via": "JSON", "geo": "Provincia (ubigeo)",
     "status": "directo", "sheet": "Muerto desde 07-07-2026, solo capitales", "n": f"{len(uv)} zonas"},
    {"id": "senamhi-pron", "org": "SENAMHI", "name": "Pronóstico por ciudad", "via": "HTML", "geo": "Ciudad",
     "status": "directo", "sheet": "Muerto desde 07-07-2026, solo capitales", "n": f"{len({p['ciudad'] for p in pron})} ciudades"},
    {"id": "senamhi-hidro", "org": "SENAMHI", "name": "Avisos hidrológicos", "via": "GeoJSON", "geo": "Estación → distrito",
     "status": "directo", "sheet": "No existía", "n": f"{len(hidro_out)} estaciones en aviso"},
    {"id": "indeci", "org": "INDECI · COEN", "name": "Reportes de emergencia", "via": "RSS + PDF", "geo": "Distrito (texto)",
     "status": "directo", "sheet": "No existía", "n": f"{len(feed)} ítems en la muestra"},
    {"id": "igp", "org": "IGP", "name": "Sismos", "via": "JSON", "geo": "Coordenadas", "status": "directo",
     "sheet": "Vivo, en texto libre", "n": f"{len(sismos)} sismos en {sismos[0]['fecha_local'][:4]}"},
    {"id": "serfor", "org": "SERFOR", "name": "Focos de calor e incendios", "via": "ArcGIS REST", "geo": "Coordenadas + ubigeo",
     "status": "directo", "sheet": "Vivo, agregado en texto", "n": f"{len(focos_out):,} focos 24 h · {len(alertas_out)} alertas"},
    {"id": "ingemmet", "org": "INGEMMET", "name": "Zonas críticas en alerta", "via": "ArcGIS REST", "geo": "Coordenadas + distrito",
     "status": "directo", "sheet": "Muerto desde 27-04-2026", "n": f"{len(zonas_out)} zonas"},
    {"id": "enfen", "org": "ENFEN", "name": "Estado del sistema de alerta", "via": "WordPress + PDF", "geo": "Nacional",
     "status": "directo", "sheet": "No existía", "n": f"Comunicado N°{enfen.get('ultimo_comunicado', {}).get('numero')}"},
    {"id": "firms", "org": "NASA FIRMS", "name": "Focos de calor (respaldo)", "via": "CSV abierto", "geo": "Coordenadas",
     "status": "directo", "sheet": "—", "n": f"{firms_n} detecciones VIIRS" if firms_n else "—"},
    {"id": "mtc", "org": "MTC · PROVIAS", "name": "Estado de vías", "via": "—", "geo": "Provincia/distrito (hoja)",
     "status": "bloqueado", "sheet": "MTC vivo (138 hoy); PROVIAS muerto desde 19-05", "n": "Servidor no responde desde esta red"},
    {"id": "agroclima", "org": "SENAMHI · MIDAGRI", "name": "AgroClima", "via": "—", "geo": "—", "status": "parcial",
     "sheet": "No existía", "n": "Heladas y friaje llegan por los avisos; sin feed propio"},
]

data = {
    "snapshot": snapshot,
    "built": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    "geo": {"deps": deps_gj, "provs": provs_gj},
    "provCentroids": {k: v["c"] for k, v in provs.items()},
    "avisos": avisos_out, "avisosHist": avisos_hist, "levelsByDay": levels_by_day,
    "uv": uv_out, "uvUnmatched": uv_unmatched, "pronostico": pron, "hidro": hidro_out,
    "indeci": indeci_out, "indeciUnlocated": unlocated,
    "sismos": sismos_out, "focos": focos_out, "alertas": alertas_out, "zonas": zonas_out,
    "enfen": enfen, "sources": sources,
}
out = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
tpl = (ROOT / "dashboard/template.html").read_text(encoding="utf-8")
(ROOT / "dashboard/index.html").write_text(tpl.replace("/*__DATA__*/null", out), encoding="utf-8")
print(f"index.html: {len(out) / 1024:.0f} KB de datos | días con avisos: {list(levels_by_day)} | "
      f"prov. con nivel hoy: {len(levels_by_day.get(snapshot, {}))} | UV sin provincia: {len(uv_unmatched)} | "
      f"INDECI sin ubicar: {unlocated}")
