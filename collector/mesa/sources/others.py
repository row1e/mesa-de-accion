"""INDECI, IGP, SERFOR, INGEMMET, ENFEN, FIRMS y verificación de acceso a PROVIAS."""
import csv
import datetime
import hashlib
import html
import io
import re
import time

import pymupdf
from shapely.geometry import Point
from shapely.ops import unary_union
from shapely.prepared import prep

from .. import config, db, geo, incendios
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
    if not db.has_item("igp", "sismo", f"{int(year) - 1}-0001"):
        # el catálogo del IGP es por año: el anterior se lee una vez, para la capa de 12 meses (y los 30 días de enero)
        prev = str(int(year) - 1)
        cat += fetch_json("igp", f"https://ultimosismo.igp.gob.pe/api/ultimo-sismo/ajaxb/{prev}", name=f"sismos_{prev}.json")
    recs = {s["codigo"]: {"codigo": s["codigo"], "fecha": s["fecha_local"][:10], "hora": s["hora_local"][11:16],
                          "mag": float(s["magnitud"]), "prof": int(float(s["profundidad"])), "lat": float(s["latitud"]),
                          "lon": float(s["longitud"]), "ref": s["referencia"], "int": s.get("intensidad") or ""}
            for s in cat}
    total, new = db.upsert_items("igp", "sismo", recs)
    return total, new, f"{new} sismos nuevos · último {last['codigo']} M{last['magnitud']} — {last['referencia']}"


# ── ArcGIS helpers ─────────────────────────────────────────────────────────────
def _arcgis(source, layer, fields="*", name=None):
    """Todos los objetos de una capa ArcGIS en GeoJSON. El servidor entrega a lo sumo su maxRecordCount (1000 en
    INGEMMET) y no todos permiten paginar: si la respuesta llega al tope, se piden los IDs y se trae el resto por tandas."""
    q = f"{layer}/query?where=1%3D1&outFields={fields}&returnGeometry=true&outSR=4326&f=geojson"
    gj = fetch_json(source, q, name=name, timeout=180)
    feats = gj.get("features") or []
    tope = gj.get("exceededTransferLimit") or (gj.get("properties") or {}).get("exceededTransferLimit")
    if not tope and (not feats or len(feats) % 500):
        return gj
    ids = sorted(fetch_json(source, f"{layer}/query?where=1%3D1&returnIdsOnly=true&f=json", keep_raw=False, timeout=180).get("objectIds") or [])
    if len(ids) <= len(feats):
        return gj
    todos = []
    for i in range(0, len(ids), 400):
        lote = ",".join(map(str, ids[i:i + 400]))
        todos += fetch_json(source, f"{layer}/query?objectIds={lote}&outFields={fields}&returnGeometry=true&outSR=4326&f=geojson",
                            keep_raw=False, timeout=180).get("features") or []
    return {**gj, "features": todos}


SERFOR = "https://geo.serfor.gob.pe/geoservicios/rest/services/UFMS/Incendios_SAMI/MapServer"
ESTADOS = {1: "Alertado", 2: "Confirmado", 3: "Controlado", 4: "Extinguido"}


def _ms_date(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).date().isoformat() if ms else None


def serfor():
    focos = _arcgis("serfor", f"{SERFOR}/0", "OBJECTID,NOMDEP,NOMPRO,NOMDIS,CATDIS,FECHA,HORA,SENSAT", "focos.geojson")
    frec = [{"oid": f["properties"]["OBJECTID"], "lon": round(f["geometry"]["coordinates"][0], 4),
                                              "lat": round(f["geometry"]["coordinates"][1], 4),
                                              "dep": f["properties"]["NOMDEP"], "prov": f["properties"]["NOMPRO"],
                                              "ubigeo": f["properties"]["CATDIS"], "fecha": _ms_date(f["properties"].get("FECHA")),
                                              "hora": f["properties"].get("HORA"), "sensor": f["properties"].get("SENSAT")}
            for f in focos["features"] if f.get("geometry")]
    frec = {incendios.clave_foco(r): r for r in frec}    # SERFOR cambia el OBJECTID en cada actualización (ver incendios.py)
    alertas = _arcgis("serfor", f"{SERFOR}/2", name="alertas.geojson")
    arec = {}
    for f in alertas["features"]:
        p = f["properties"]
        if not f.get("geometry"):
            continue
        r = {  # CODREP se repite entre alertas de un mismo reporte PIF
            "oid": p["OBJECTID"], "lon": round(f["geometry"]["coordinates"][0], 4), "lat": round(f["geometry"]["coordinates"][1], 4),
            "estado": ESTADOS.get(p.get("ESTADO"), str(p.get("ESTADO"))), "dep": p["NOMDEP"], "prov": p["NOMPRO"],
            "dist": p["NOMDIS"], "ubigeo": p["CATDIS"], "cod": p.get("CODREP"), "fecha": _ms_date(p.get("FECHA")),
            "hora": p.get("HORA"), "cob": p.get("TIPCOB")}
        arec[incendios.clave_alerta(r)] = r
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
        if (db.get_item("enfen", "comunicado", key) or {}).get("estado"):
            continue   # ya leído (los que quedaron sin estado se vuelven a leer)
        pdf = fetch("enfen", url, name=f"comunicado_{n}_{a}.pdf", timeout=120)
        import pymupdf
        text = pymupdf.open(stream=pdf, filetype="pdf")[0].get_text()
        # Hasta el N° 16 el PDF decía "Estado del sistema de alerta: …"; desde el N° 17 el estado va solo, en su línea,
        # bajo la fecha y con una llamada a nota al pie ("Alerta de El Niño Costero1").
        m = (re.search(r"Estado del sistema de alerta:\s*(.+)", text)
             or re.search(r"^\s*((?:Alerta|Vigilancia)\s+de\s+(?:El\s+Niño|La\s+Niña)[^\n]*?|(?:Sistema de alerta\s+)?No\s+activo)\s*\d*\s*$", text, re.M | re.I))
        fecha = re.search(r"COMUNICADO OFICIAL ENFEN N° ?\d+ ?- ?\d{4}\s*\n\s*(.+)", text)
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
# Visor de Emergencias Viales (SGCV) de PROVIAS Nacional: el mismo JSON que usa su mapa.
PROVIAS = "https://wsgcv.proviasnac.gob.pe/emergenciavial/EmergenciaVial"
PROVIAS_XHR = {"X-Requested-With": "XMLHttpRequest"}
TRANSITO = {"01": "Tránsito normal", "02": "Tránsito restringido", "03": "Tránsito interrumpido", "04": "Por confirmar"}


def _dmy(s):
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s or "")
    return f"{m[3]}-{m[2]}-{m[1]}" if m else None


def provias():
    # El panel trae tipo, ruta, tramo y fecha; la lista del mapa, la condición de tránsito vigente (incluye
    # "por confirmar"), los días transcurridos y si es puente. Se unen por IdEmergenciaVial.
    adm = "idTipoAdministracion=1_2_4_5_6"
    panel = fetch_json("provias", f"{PROVIAS}/PanelGetEmergenciaList?fecha=&idDepartamento=&idZonal=&searchValue=&histo=false&{adm}",
                       name="panel.json", headers=PROVIAS_XHR)
    mapa = fetch_json("provias", f"{PROVIAS}/GetEmergenciaList?fecha=&idDepartamento=&idZonal=&idCondicionTransito=&idEvento=&{adm}",
                      name="mapa.json", headers=PROVIAS_XHR)
    if not panel.get("success") or not mapa.get("success"):
        raise RuntimeError(f"el visor respondió sin éxito: {panel.get('message') or mapa.get('message') or '—'}")
    en_mapa = {str(x["IdEmergenciaVial"]): x for x in mapa.get("data") or []}
    recs = {}
    for e in panel.get("data") or []:
        k, m = str(e["IdEmergenciaVial"]), en_mapa.get(str(e["IdEmergenciaVial"]), {})
        lon, lat = e.get("Longitud"), e.get("Latitud")
        cod = m.get("CondicionTransitoCodigo") or e.get("CodigoCondicionTransito")
        prov = geo.province_of_point(lon, lat) if lon and lat else None
        recs[k] = {"id": e["IdEmergenciaVial"], "lon": round(lon, 5) if lon else None, "lat": round(lat, 5) if lat else None,
                   "transito_cod": cod, "transito": TRANSITO.get(cod, e.get("CondicionTransito")),
                   "tipo": " ".join((e.get("TipoEmergencia") or "").split()), "ruta": e.get("Ruta"), "tramo": e.get("Tramo"),
                   "sector": e.get("Sector"), "km_ini": e.get("ProgresivaIni"), "km_fin": e.get("ProgresivaFin"),
                   "fecha": _dmy(e.get("Fecha")), "dias": m.get("CantDias"), "puente": m.get("EsPuente") == "1",
                   "en_mapa": bool(m), "prov": prov, "reg": prov[:2] if prov else (geo.region_of_point(lon, lat) if lon and lat else None)}
    total, new = db.upsert_items("provias", "emergencia", recs, snapshot=True)
    # PROVIAS deja eventos en su lista aunque ya no tengan emergencias (el paro de pescadores del 06/10 siguió
    # listado días después, con 0 emergencias). Se cuentan las suyas y solo se muestran los que tienen alguna.
    ev = fetch_json("provias", f"{PROVIAS}/GetEventos", name="eventos.json", headers=PROVIAS_XHR)
    eventos = {}
    for x in ev.get("data") or []:
        em = fetch_json("provias", f"{PROVIAS}/GetEmergenciaList?fecha=&idDepartamento=&idZonal=&idCondicionTransito=&idEvento={x['IdEvento']}&{adm}",
                        name=f"evento_{x['IdEvento']}.json", headers=PROVIAS_XHR)
        eventos[str(x["IdEvento"])] = {"id": x["IdEvento"], "nombre": x.get("Nombre"), "emergencias": len(em.get("data") or [])}
    db.upsert_items("provias", "evento", eventos, snapshot=True)
    eventos = {k: e for k, e in eventos.items() if e["emergencias"]}
    n = {t: sum(1 for r in recs.values() if r["transito_cod"] == c) for c, t in TRANSITO.items()}
    return total, new, (f"{total} emergencias viales ({new} nuevas): {n['Tránsito interrumpido']} interrumpidas, "
                        f"{n['Tránsito restringido']} restringidas, {n['Por confirmar']} por confirmar"
                        + (f" · eventos: {'; '.join(e['nombre'] for e in eventos.values())}" if eventos else ""))


FOTOS_VIAS = config.DATA / "media" / "provias"
FOTOS_TABLERO, FOTO_MAX_PX = 80, 1600
FIRMAS_FOTO = {b"\xff\xd8\xff": "jpg", b"\x89PNG": "png", b"GIF8": "gif", b"RIFF": "webp"}


def foto_via_local(url):
    """Dirección de la copia guardada de una foto de PROVIAS; sin copia, la del visor (que solo abre desde el Perú)."""
    base = hashlib.sha1(url.encode()).hexdigest()[:20]
    for ext in FIRMAS_FOTO.values():
        if (FOTOS_VIAS / f"{base}.{ext}").exists():
            return f"/media/provias/{base}.{ext}"
    return url


def _achicar_foto(datos, ext):
    """A lo sumo FOTO_MAX_PX de ancho, en JPEG. Si no se puede leer, queda tal cual."""
    try:
        pix = pymupdf.Pixmap(datos)
        if pix.alpha or pix.n > 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        while pix.width > FOTO_MAX_PX:
            pix.shrink(1)                       # a la mitad
        return pix.tobytes("jpeg", jpg_quality=80), "jpg"
    except Exception:  # noqa: BLE001
        return datos, ext


def _guardar_fotos_vias(limite=40):
    """Copia aquí las fotos que muestra el tablero (las FOTOS_TABLERO más recientes, hasta 3 por emergencia vigente),
    porque PROVIAS solo responde a IPs peruanas y quien mira el tablero puede estar en otro lado. Las achica para cuidar
    el disco y borra las que el tablero ya no muestra."""
    vigentes = {v["_key"] for v in db.get_items("provias", "emergencia")}
    fotos = [f for ln in db.get_items("provias", "linea", current_only=False) if ln["_key"] in vigentes
             for f in (ln.get("fotos") or [])[:3]]
    fotos.sort(key=lambda f: f.get("fecha") or "", reverse=True)
    urls = list(dict.fromkeys(f["url"] for f in fotos[:FOTOS_TABLERO]))
    FOTOS_VIAS.mkdir(parents=True, exist_ok=True)
    nuevas, fallas = 0, 0
    for url in urls:
        if nuevas >= limite or fallas >= 3:     # sin camino al Perú cada intento tarda: se sigue en el próximo turno
            break
        if not foto_via_local(url).startswith("http"):
            continue
        try:
            datos = fetch("provias_fotos", url, keep_raw=False, retries=0)
        except Exception:  # noqa: BLE001
            fallas += 1
            continue
        ext = next((e for firma, e in FIRMAS_FOTO.items() if datos.startswith(firma)), None)
        if not ext:
            fallas += 1
            continue
        datos, ext = _achicar_foto(datos, ext)
        (FOTOS_VIAS / f"{hashlib.sha1(url.encode()).hexdigest()[:20]}.{ext}").write_bytes(datos)
        nuevas += 1
    usadas = {foto_via_local(u).rsplit("/", 1)[-1] for u in urls}
    for f in FOTOS_VIAS.iterdir():
        if f.name not in usadas:
            f.unlink()
    return nuevas, len(urls)


def provias_fotos(limite=40, cada=12 * 3600):
    """Fotos de la cronología de cada emergencia vial (el visor las entrega de a una emergencia).

    Por turno revisa hasta `limite`: primero las nunca revisadas, luego interrumpidas y por confirmar, y entre
    las demás las revisadas hace más tiempo. Cada emergencia se vuelve a mirar cada `cada` segundos. Además guarda
    una copia de las fotos que muestra el tablero (ver _guardar_fotos_vias)."""
    vias = db.get_items("provias", "emergencia")
    lineas = {x["_key"]: x for x in db.get_items("provias", "linea", current_only=False)}
    ahora, orden = time.time(), {"03": 0, "04": 1, "02": 2, "01": 3}
    revisado = lambda v: lineas.get(v["_key"], {}).get("revisado") or 0   # noqa: E731
    pend = sorted((v for v in vias if ahora - revisado(v) > cada),
                  key=lambda v: (v["_key"] in lineas, orden.get(v.get("transito_cod"), 9), revisado(v)))
    host = PROVIAS.split("/emergenciavial")[0]
    hechas, fotos, errores = {}, 0, []
    for v in pend[:limite]:
        try:
            d = fetch_json("provias_fotos", f"{PROVIAS}/GetListaFotograficaPorEmergencia?id={v['id']}", keep_raw=False, headers=PROVIAS_XHR)
        except Exception as e:  # noqa: BLE001 — una emergencia que no responde no detiene la cola
            errores.append(f"{v['id']}: {type(e).__name__}")
            continue
        fs = [{"url": host + f["url"], "caption": " ".join((f.get("contenido") or "").split()) or None, "fecha": _dmy(x.get("fecha"))}
              for x in d.get("data") or [] for f in x.get("fotos") or [] if f.get("url")]
        hechas[v["_key"]] = {"id": v["id"], "revisado": ahora, "n_fotos": len(fs), "fotos": fs[:12]}
        fotos += len(fs)
    if hechas:
        db.upsert_items("provias", "linea", hechas)
    nuevas, total = _guardar_fotos_vias()
    msg = (f"{len(hechas)} emergencias revisadas · {fotos} fotos · quedan {len(pend) - len(hechas)} por revisar"
           f" · {nuevas} copias nuevas de {total} fotos del tablero")
    return len(hechas), fotos, msg + (f" · errores: {', '.join(errores)}" if errores else "")
