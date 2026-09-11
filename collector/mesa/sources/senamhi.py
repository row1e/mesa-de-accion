"""SENAMHI: avisos meteorológicos (+ nivel por provincia), UV por provincia, pronóstico por ciudad, avisos hidrológicos."""
import html
import re

from .. import db, geo
from ..http import fetch, fetch_json, fetch_text

BASE = "https://www.senamhi.gob.pe"
WFS = "https://idesep.senamhi.gob.pe/geoserver/g_aviso/ows"


def _strip(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _aviso_provincias(nro, mapa, anio):
    raw = fetch("senamhi_avisos", f"{BASE}/mapas/mapa-avisos-meteorologicos/index.php?av={nro}&nl=4&mp={mapa}&fc={anio}",
                name=f"mapa_{nro}_{mapa}_{anio}.html")
    page = raw.decode("utf-8", "replace")
    if "�" in page:  # la página del mapa viene en Latin-1
        page = raw.decode("latin-1")
    text = re.sub(r"(\|\s*)+", "| ", html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " | ", page))))

    def grab(label, nxt):
        m = re.search(label + r"(.*?)" + nxt, text)
        return [x.strip(" ,") for x in m.group(1).split("|") if x.strip(" ,")] if m else []

    return grab("DEPARTAMENTOS DE POSIBLE AFECTACIÓN:", "PROVINCIAS DE POSIBLE"), grab("PROVINCIAS DE POSIBLE AFECTACIÓN:", "DESCARGAR")


def avisos():
    page = fetch_text("senamhi_avisos", f"{BASE}/?p=aviso-meteorologico", name="avisos.html")
    rows = {}
    for row in re.findall(r"<tr>\s*<td.*?</tr>", page, re.S):
        tds = [_strip(t) for t in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        # Las filas recientes traen "360 (emitido)"; las antiguas solo el número.
        m = re.match(r"(\d+)\s*(?:\((\w+)\))?", tds[1]) if len(tds) >= 7 else None
        if not m or not re.match(r"\d{4}-", tds[2]):
            continue
        nro, estado, anio = int(m.group(1)), m.group(2) or "vencido", tds[2][:4]
        href = re.search(r'href="\.?/?([^"]+)"', row)
        rows[f"{anio}-{nro}"] = {"nro": nro, "anio": int(anio), "estado": estado, "titulo": tds[0], "emision": tds[2],
                                 "inicio": tds[3], "fin": tds[4], "duracion": tds[5], "nivel": tds[6],
                                 "link": f"{BASE}/{html.unescape(href.group(1))}" if href else None}
    if not rows:
        raise RuntimeError("la tabla de avisos vino vacía: ¿cambió la maquetación?")
    total, new = db.upsert_items("senamhi_avisos", "aviso", rows)

    dias_nuevos = 0
    for a in rows.values():
        if a["estado"] not in ("vigente", "emitido"):
            continue
        for mapa in (1, 2, 3):
            key = f"{a['anio']}-{a['nro']}-{mapa}"
            if db.has_item("senamhi_avisos", "aviso_dia", key):
                continue
            gj = fetch_json("senamhi_avisos",
                            f"{WFS}?service=WFS&version=1.0.0&request=GetFeature&typeName=g_aviso:view_aviso"
                            f"&viewparams=qry:{a['nro']}_{mapa}_{a['anio']}&outputFormat=application/json",
                            name=f"aviso_{a['nro']}_{mapa}_{a['anio']}.geojson", timeout=180)
            if not gj.get("features"):
                break
            deps, provs = _aviso_provincias(a["nro"], mapa, a["anio"])
            db.upsert_items("senamhi_avisos", "aviso_dia", {key: {
                "nro": a["nro"], "anio": a["anio"], "mapa": mapa,
                "fecha": gj["features"][0]["properties"]["fech_ini"][:10],
                "niveles_poligonos": sorted({f["properties"]["nivel"] for f in gj["features"]}),
                "levels": geo.aviso_levels(gj), "dptos_texto": deps, "provs_texto": provs}})
            dias_nuevos += 1
    vig = sum(1 for a in rows.values() if a["estado"] in ("vigente", "emitido"))
    return total, new, f"{total} avisos, {vig} vigentes/emitidos, {dias_nuevos} aviso-días nuevos cruzados"


def uv():
    data = fetch_json("senamhi_uv", f"{BASE}/usr/dms/modelo/iuv/prono_ruv.json", name="prono_ruv.json")
    recs = {z["c_cod_zona"]: {"code": z["c_cod_zona"], "zona": z["v_nom_zona"].replace("_", " "),
                              "lat": float(z["n_lat_sig"]), "lon": float(z["n_lon_sig"]), "emitido": z["d_fec_pron"],
                              "dias": [p["d_fec_diapron"] for p in z["pronostico"]],
                              "v": [round(float(p["n_indice"]), 1) for p in z["pronostico"]],
                              "h": [p["d_hora_punta"] for p in z["pronostico"]]} for z in data}
    total, new = db.upsert_items("senamhi_uv", "uv_zona", recs, snapshot=True)
    return total, new, f"{total} zonas, emitido {data[0]['d_fec_pron'] if data else '—'}"


def pronostico():
    page = fetch_text("senamhi_pronostico", f"{BASE}/?p=pronostico-meteorologico", name="pronostico.html")
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S))))
    dias = r"(?:lunes|martes|miércoles|jueves|viernes|sábado|domingo)"
    recs = {}
    # ciudades como "LIMA OESTE / CALLAO - LIMA" llevan "/" en el nombre
    heads = list(re.finditer(rf"([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ .'()/-]+?) - ([A-ZÁÉÍÓÚÑ ]+?) (?={dias},)", text))
    for i, m in enumerate(heads):
        # el bloque de una ciudad termina donde empieza la siguiente (antes se cortaba a 900 caracteres y se colaban sus días)
        seg = text[m.end(): heads[i + 1].start() if i + 1 < len(heads) else m.end() + 900]
        ds = [{"dia": d.group(1), "tmax": int(d.group(2)), "tmin": int(d.group(3)), "descripcion": d.group(4).strip()}
              for d in re.finditer(rf"({dias}, \d+ de \w+) (-?\d+)ºC (-?\d+)ºC (.*?)(?= {dias},|[A-ZÁÉÍÓÚÑ]{{3,}}[A-ZÁÉÍÓÚÑ .'()/-]* - |$)", seg)]
        if ds:
            ciudad, dpto = m.group(1).strip(), m.group(2).strip()
            recs[f"{ciudad}|{dpto}"] = {"ciudad": ciudad, "departamento": dpto, "dias": ds}
    if not recs:
        raise RuntimeError("no se reconoció ninguna ciudad: ¿cambió la maquetación?")
    total, new = db.upsert_items("senamhi_pronostico", "ciudad", recs, snapshot=True)
    return total, new, f"{total} ciudades"


def hidro():
    gj = fetch_json("senamhi_hidro", f"{BASE}/mapas/mapa-aviso-hidro/include/ajaxIdesepWFSAvisos.php", method="POST",
                    name="avisos_hidro.geojson")
    keep = ("nom_estacion", "fecha_hora", "nivel", "color_text", "titulo", "nom_departamento", "nom_provincia",
            "nom_distrito", "nom_cuenca", "peligro_nivel", "num_aviso")
    recs = {str(f["properties"]["id_aviso"]): {"lon": f["geometry"]["coordinates"][0], "lat": f["geometry"]["coordinates"][1],
                                               **{k: f["properties"].get(k) for k in keep}, "raw": f["properties"]}
            for f in gj.get("features", []) if f.get("geometry")}
    total, new = db.upsert_items("senamhi_hidro", "aviso_estacion", recs, snapshot=True)
    return total, new, f"{total} estaciones en aviso"
