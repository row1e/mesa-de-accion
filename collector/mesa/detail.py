"""Ficha completa de un registro: dato guardado + enriquecimiento bajo demanda (cacheado) + enlaces a la fuente.

Forma de respuesta:
  {source, kind, key, title, subtitle, level (1-4|None), facts: [[etiqueta, valor]], text, pdf_text,
   images: [{label, url}], links: [{label, url}], fields: {campo: valor}, fetched_at, first_seen, last_seen, current}
"""
import datetime
import time
import html
import re
import urllib.parse

from . import config, db, indeci_pdf
from .http import fetch, fetch_json, fetch_text
from .sources.others import SERFOR

GEOCATMIN = "https://geocatmin.ingemmet.gob.pe/arcgis/rest/services"
TTL = {  # segundos que vale el enriquecimiento cacheado
    ("igp", "sismo"): 7 * 86400, ("serfor", "alerta"): 600, ("serfor", "foco"): 3600,
    ("ingemmet", "zona_alerta"): 3600, ("indeci", "item"): 3600, ("senamhi_avisos", "aviso"): 6 * 3600,
    **{(sid, "noticia"): 6 * 3600 for sid in ("com_pnp", "com_provias", "com_mtc", "com_mininter")},
}
LEVEL_WORDS = {"AMARILLO": 2, "NARANJA": 3, "ROJO": 4}


def _clean(s):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _arcgis_feature(source, layer, oid):
    """Atributos completos de un objeto ArcGIS, con alias de campo y dominios decodificados."""
    meta = fetch_json(source, f"{layer}?f=json", keep_raw=False)
    res = fetch_json(source, f"{layer}/query?where=OBJECTID%3D{int(oid)}&outFields=*&returnGeometry=false&f=json", keep_raw=False)
    if not res.get("features"):
        return None
    attrs = res["features"][0]["attributes"]
    out = {}
    for f in meta.get("fields", []):
        name, alias = f["name"], f.get("alias") or f["name"]
        v = attrs.get(name)
        if v in (None, "", " ") or name.lower() in ("shape", "created_user", "last_edited_user"):
            continue
        dom = {c["code"]: c["name"] for c in (f.get("domain") or {}).get("codedValues", [])}
        if v in dom:
            v = dom[v]
        elif f.get("type") == "esriFieldTypeDate" and isinstance(v, (int, float)):
            v = datetime.datetime.fromtimestamp(v / 1000, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        out[alias if alias != name else name] = v
    return out


def _feature_page(layer, oid):
    return f"{layer}/query?where=OBJECTID%3D{int(oid)}&outFields=*&f=html"


# ── enriquecedores ─────────────────────────────────────────────────────────────
def _url_ok(url, timeout=12):
    """True si la URL responde 200. La API del IGP anuncia mapas que no existen (404) para casi todos los sismos."""
    import urllib.request
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": config.USER_AGENT})
        from .http import SSL_CONTEXT
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _igp(item):
    ev = fetch_json("igp", f"https://ultimosismo.igp.gob.pe/api/ultimo-sismo/sismo/{item['codigo']}", keep_raw=False)
    labels = {"mapa_sismico_url": "Mapa sísmico", "mapa_intensidades_url": "Intensidades", "mapa_aceleracion_maxima_url": "Aceleración máxima",
              "mapa_velocidades_maxima_url": "Velocidad máxima", "mapa_aceleracion_teorica_url": "Aceleración teórica",
              "mapa_pseudo_aceleracion_url": "Pseudoaceleración"}
    return {"images": [{"label": l, "url": ev[k]} for k, l in labels.items() if ev.get(k) and _url_ok(ev[k])],
            "links": [{"label": "Reporte acelerométrico (PDF)", "url": ev["reporte_acelerometrico_pdf"]}] if ev.get("reporte_acelerometrico_pdf") else [],
            "fields": {k: v for k, v in ev.items() if not str(k).endswith("_url") and v not in (None, "")}}


def _indeci(item):
    """Usa el PDF ya procesado por el job indeci_fotos; si aún no está, lo procesa ahora (y queda guardado)."""
    rec = db.get_item("indeci", "reporte_pdf", item["_key"])
    if rec is None or rec.get("error"):
        rec = indeci_pdf.process(item)
    out = {"text": rec.get("hechos"), "pdf_text": rec.get("pdf_text"), "fotos": rec.get("fotos", []), "mapa": rec.get("mapa"),
           "danos": (rec.get("danos") or {}).get("totales") or {}, "danos_actualizado": rec.get("danos_actualizado"),
           "links": [{"label": "Reporte completo (PDF)", "url": rec["pdf_url"]}] if rec.get("pdf_url") else []}
    if rec.get("sinpad"):
        out["fields"] = {"Código SINPAD": rec["sinpad"]}
    return out


def _aviso(item):
    if not item.get("link"):
        return {}
    page = fetch_text("senamhi_avisos", item["link"], name="aviso_vigente.html", timeout=90)
    text = _clean(re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S))
    m = re.search(rf"Aviso N°\s*{item['nro']}\b(.*?)(?=Aviso N°\s*\d+\b|Anteriores|$)", text, re.S)
    body = m.group(1) if m else ""
    body = re.sub(r"\s*-->\s*", " ", body)
    return {"text": body[:3000].strip() or None}


def _gobpe(item):
    page = fetch_text(item.get("source") or "com_pnp", item["url"], name="noticia.html")
    body = re.sub(r"<script.*?</script>|<style.*?</style>", "", page.split("</head>")[-1], flags=re.S)
    m = re.search(r'class="description institution-document__abstract"(.*?)class="row social-utils', body, re.S) \
        or re.search(r'class="body"(.*?)social-utils', body, re.S)
    text = _clean("<x " + m.group(1)) if m else None
    if text:
        text = re.sub(r"^[^>]*>\s*", "", text)
    imgs = re.findall(r'<img[^>]+src="(https://cdn\.www\.gob\.pe/uploads/[^"]+)"', body)
    pdfs = sorted(set(re.findall(r'href="(https://cdn\.www\.gob\.pe/uploads/document/file/[^"]+\.pdf[^"]*)"', body)))
    return {"text": text[:6000] if text else None, "images": [{"label": "Imagen", "url": u} for u in dict.fromkeys(imgs)][:4],
            "links": [{"label": "Documento adjunto (PDF)", "url": u} for u in pdfs]}


# ── fichas por tipo ────────────────────────────────────────────────────────────
def build(source, kind, key, fresh=False):
    item = db.get_item(source, kind, key)
    if item is None:
        return None
    ttl = TTL.get((source, kind))
    extra, fetched_at = ({}, None)
    if ttl:
        extra, fetched_at = db.detail_get(source, kind, key, 0 if fresh else ttl)
        if extra is None:
            try:
                extra = ENRICH[(source, kind)](item)
                db.detail_set(source, kind, key, extra)
                fetched_at = time.time()
            except Exception as e:  # noqa: BLE001 — se muestra el dato guardado aunque la fuente no responda
                extra = {"error": f"No se pudo consultar la fuente: {type(e).__name__}: {e}"}
    d = FORMAT[(source, kind)](item, extra or {})
    d.update({"source": source, "kind": kind, "key": key, "fetched_at": fetched_at, "first_seen": item["_first_seen"],
              "last_seen": item["_last_seen"], "current": bool(item["_current"]), "error": (extra or {}).get("error")})
    d.setdefault("images", (extra or {}).get("images", []))
    d.setdefault("text", (extra or {}).get("text"))
    d.setdefault("pdf_text", (extra or {}).get("pdf_text"))
    d.setdefault("fotos", (extra or {}).get("fotos", []))
    d.setdefault("mapa", (extra or {}).get("mapa"))
    d["links"] = d.get("links", []) + (extra or {}).get("links", [])
    d["sat"] = _sat_params(kind, item)
    return d


def _sat_params(kind, it):
    """Punto, fecha y ancho (km) para la vista satelital de un registro con coordenadas; None si no aplica."""
    today = datetime.date.today().isoformat()
    lat, lon = it.get("lat"), it.get("lon")
    if lat is None or lon is None:
        return None
    date = {"sismo": it.get("fecha"), "alerta": it.get("fecha"), "foco": it.get("fecha"), "deteccion": it.get("fecha"),
            "aviso_estacion": (it.get("fecha_hora") or "")[:10]}.get(kind) or today
    km = {"sismo": 60, "aviso_estacion": 25, "zona_alerta": 10}.get(kind, 15)
    return {"lat": lat, "lon": lon, "date": min(date, today), "km": km}


def _f_sismo(it, ex):
    return {"title": f"Sismo M{it['mag']} · {it['codigo']}", "subtitle": it["ref"], "level": None,
            "facts": [["Fecha y hora local", f"{it['fecha']} {it['hora']}"], ["Magnitud", it["mag"]], ["Profundidad", f"{it['prof']} km"],
                      ["Coordenadas", f"{it['lat']}, {it['lon']}"], ["Intensidad", it.get("int") or "—"]],
            "links": [{"label": "Reporte en IGP", "url": f"https://ultimosismo.igp.gob.pe/evento/{it['codigo']}"}],
            "fields": {**{k: v for k, v in it.items() if not k.startswith("_")}, **ex.get("fields", {})}}


def _arc(layer_url, oid_of):
    def enrich(it):
        oid = oid_of(it)
        return {"fields": _arcgis_feature("serfor" if "serfor" in layer_url else "ingemmet", layer_url, oid) or {}}
    return enrich


def _f_alerta(it, ex):
    return {"title": f"Incendio forestal · {it['estado']}", "subtitle": f"{it['dist']}, {it['prov']} · {it['dep']}".title(),
            "level": {"Alertado": 2, "Confirmado": 3}.get(it["estado"]),
            "facts": [["Estado", it["estado"]], ["Fecha", f"{it.get('fecha') or '—'} {it.get('hora') or ''}"], ["Ubigeo", it.get("ubigeo")],
                      ["Código de reporte", it.get("cod") or "—"], ["Cobertura", it.get("cob") or "—"], ["Coordenadas", f"{it['lat']}, {it['lon']}"]],
            "links": [{"label": "Registro en SERFOR (ArcGIS)", "url": _feature_page(f"{SERFOR}/2", it["_key"])},
                      {"label": "Visor SERFOR", "url": "https://geo.serfor.gob.pe/visor/"}],
            "fields": ex.get("fields") or {k: v for k, v in it.items() if not k.startswith("_")}}


def _f_foco(it, ex):
    return {"title": "Foco de calor", "subtitle": f"{it['prov']} · {it['dep']}".title(), "level": None,
            "facts": [["Fecha", f"{it.get('fecha') or '—'} {it.get('hora') or ''}"], ["Sensor", it.get("sensor")], ["Ubigeo distrital", it.get("ubigeo")],
                      ["Coordenadas", f"{it['lat']}, {it['lon']}"]],
            "links": [{"label": "Registro en SERFOR (ArcGIS)", "url": _feature_page(f"{SERFOR}/0", it["_key"])},
                      {"label": "NASA FIRMS en el punto", "url": f"https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@{it['lon']},{it['lat']},12.0z"}],
            "fields": ex.get("fields") or {k: v for k, v in it.items() if not k.startswith("_")}}


def _f_zona(it, ex):
    oid = it["_key"].split("-")[-1]
    layer = f"{GEOCATMIN}/SERV_PERU_ALERTA/MapServer/0"
    return {"title": f"Zona crítica · {it.get('paraje') or '—'}", "subtitle": f"{it.get('distrito')}, {it.get('provincia')} · {it.get('region')}",
            "level": int(str(it.get("nivel", "Nivel 1")).split()[-1]),
            "facts": [["Peligro", it.get("peligros_g")], ["Elementos expuestos", it.get("elemento")], ["Nivel", it.get("nivel")],
                      ["Aviso SENAMHI", f"#{it.get('nro_aviso')}"], ["Recomendación", it.get("recomend") or "—"]],
            "links": [{"label": "Registro en GEOCATMIN (ArcGIS)", "url": _feature_page(layer, oid)},
                      {"label": "Visor GEOCATMIN", "url": "https://geocatmin.ingemmet.gob.pe/geocatmin/"}],
            "fields": ex.get("fields") or {k: v for k, v in it.items() if not k.startswith("_")}}


def _f_hidro(it, ex):
    raw = it.get("raw") or {}
    links = [{"label": "Mapa de avisos hidrológicos (SENAMHI)", "url": "https://www.senamhi.gob.pe/?p=aviso-hidrologico"}]
    # raw["url_centros_pob"] apunta a un host interno de SENAMHI (phisis) que no resuelve desde fuera: no se enlaza.
    return {"title": it.get("titulo"), "subtitle": f"{it.get('nom_distrito')}, {it.get('nom_provincia')} · {it.get('nom_departamento')}".title(),
            "level": LEVEL_WORDS.get(it.get("color_text")),
            "facts": [["Estación", it.get("nom_estacion")], ["Nivel", it.get("color_text")], ["Fecha", it.get("fecha_hora")],
                      ["Cuenca", it.get("nom_cuenca")], ["Aviso N°", it.get("num_aviso")]],
            "text": " ".join(x for x in (raw.get("peligro_nivel"), raw.get("recomendacion_nivel")) if x) or None,
            "links": links, "fields": raw or {k: v for k, v in it.items() if not k.startswith("_")}}


def _f_indeci_danos(ex):
    from .indeci_danos import LABELS, ORDER
    tot = ex.get("danos") or {}
    rows = [[f"Daños · {LABELS[k]}", f"{tot[k]:,}".replace(",", " ") if isinstance(tot[k], int) else tot[k]] for k in ORDER if k in tot]
    if rows and ex.get("danos_actualizado"):
        rows.append(["Cifras actualizadas al", ex["danos_actualizado"]])
    return rows


def _f_indeci(it, ex):
    facts = [["Publicado", it.get("pub")], ["Tipo", (it.get("tipo") or it.get("clase") or "").title()]]
    if it.get("clase") == "reporte":
        facts += [["Evento", (it.get("evento") or "").title()], ["Distrito", (it.get("distrito") or "").title()],
                  ["Provincia", (it.get("provincia") or "—").title()], ["Departamento", (it.get("dpto") or "").title()],
                  ["N° de reporte", f"{it.get('num')}" + (f" · secuencia {it['seq']}" if it.get("seq") else "")]]
    facts += _f_indeci_danos(ex)
    return {"title": it.get("titulo"), "subtitle": it.get("descripcion"), "level": None, "facts": facts,
            "links": [{"label": "Publicación en INDECI", "url": it["link"]}],
            "fields": {**{k: v for k, v in it.items() if not k.startswith("_")}, **ex.get("fields", {})}}


def _f_aviso(it, ex):
    q = urllib.parse.quote
    links = [{"label": "Aviso en SENAMHI", "url": it["link"]}] if it.get("link") else []
    links += [{"label": f"Mapa día {m}", "url": f"https://www.senamhi.gob.pe/mapas/mapa-avisos-meteorologicos/index.php?av={it['nro']}&nl=4&mp={m}&fc={it['anio']}"}
              for m in range(1, 4) if db.has_item("senamhi_avisos", "aviso_dia", f"{it['anio']}-{it['nro']}-{m}")]
    links.append({"label": "Polígonos (Shapefile)", "url": "https://idesep.senamhi.gob.pe/geoserver/g_aviso/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=g_aviso:view_aviso"
                  f"&viewparams=qry:{it['nro']}_1_{it['anio']}&outputFormat=SHAPE-ZIP&format_options={q('filename:aviso_' + str(it['nro']))}"})
    return {"title": f"Aviso N° {it['nro']} · {it['titulo']}", "subtitle": f"{it['estado']} · {it['inicio']} → {it['fin']}",
            "level": LEVEL_WORDS.get(it["nivel"]),
            "facts": [["Nivel", it["nivel"]], ["Emisión", it["emision"]], ["Vigencia", f"{it['inicio']} → {it['fin']} ({it['duracion']})"],
                      ["Estado", it["estado"]]],
            "links": links, "fields": {k: v for k, v in it.items() if not k.startswith("_")}}


def _f_firms(it, ex):
    return {"title": "Detección VIIRS (NASA FIRMS)", "subtitle": f"{it['fecha']} {it['hora']} UTC", "level": None,
            "facts": [["Confianza", it.get("conf")], ["Potencia radiativa (FRP)", f"{it.get('frp')} MW"], ["Coordenadas", f"{it['lat']}, {it['lon']}"]],
            "links": [{"label": "NASA FIRMS en el punto", "url": f"https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@{it['lon']},{it['lat']},12.0z"}],
            "fields": {k: v for k, v in it.items() if not k.startswith("_")}}


def _f_noticia(it, ex):
    return {"title": it["titulo"], "subtitle": it.get("descripcion"), "level": None,
            "facts": [["Institución", it["inst_nombre"]], ["Fecha", it.get("fecha_texto")]],
            "links": [{"label": f"Publicación en gob.pe ({it['inst_nombre']})", "url": it["url"]}],
            "fields": {k: v for k, v in it.items() if not k.startswith("_")}}


def _f_enfen(it, ex):
    return {"title": f"Comunicado Oficial ENFEN N° {it['numero']}-{it['anio']}", "subtitle": f"Estado del sistema de alerta: {it.get('estado') or '—'}",
            "level": 3 if "Alerta" in (it.get("estado") or "") else None,
            "facts": [["Estado", it.get("estado")], ["Fecha", it.get("fecha")], ["Alcance", "Nacional (regiones Niño 1+2 y 3.4)"]],
            "text": it.get("resumen"), "links": [{"label": "Comunicado (PDF)", "url": it["url"]},
                                                   {"label": "Comunicados ENFEN", "url": "https://enfen.imarpe.gob.pe/comunicados/"}],
            "fields": {k: v for k, v in it.items() if not k.startswith("_") and k != "resumen"}}


ENRICH = {
    **{(sid, "noticia"): _gobpe for sid in ("com_pnp", "com_provias", "com_mtc", "com_mininter")},
    ("igp", "sismo"): _igp, ("indeci", "item"): _indeci, ("senamhi_avisos", "aviso"): _aviso,
    ("serfor", "alerta"): _arc(f"{SERFOR}/2", lambda it: it["_key"]),
    ("serfor", "foco"): _arc(f"{SERFOR}/0", lambda it: it["_key"]),
    ("ingemmet", "zona_alerta"): _arc(f"{GEOCATMIN}/SERV_PERU_ALERTA/MapServer/0", lambda it: it["_key"].split("-")[-1]),
}
FORMAT = {
    ("enfen", "comunicado"): _f_enfen,
    **{(sid, "noticia"): _f_noticia for sid in ("com_pnp", "com_provias", "com_mtc", "com_mininter")},
    ("igp", "sismo"): _f_sismo, ("serfor", "alerta"): _f_alerta, ("serfor", "foco"): _f_foco,
    ("ingemmet", "zona_alerta"): _f_zona, ("senamhi_hidro", "aviso_estacion"): _f_hidro, ("indeci", "item"): _f_indeci,
    ("senamhi_avisos", "aviso"): _f_aviso, ("firms", "deteccion"): _f_firms,
}
