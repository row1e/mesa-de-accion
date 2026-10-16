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
    ("provias", "emergencia"): 1800, ("bomberos", "parte"): 600,
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
    if kind == "parte" and not (it.get("detalle") or "").lower().startswith("forestal"):
        return None   # bomberos: a esa escala el satélite solo sirve para incendios forestales, no para una vivienda o un choque
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


def _provias(it):
    from .sources.others import PROVIAS, PROVIAS_XHR
    det = fetch_json("provias", f"{PROVIAS}/GetEmergenciaContent?idEmergenciaVial={it['id']}", keep_raw=False, headers=PROVIAS_XHR)
    lin = fetch_json("provias", f"{PROVIAS}/GetListaFotograficaPorEmergencia?id={it['id']}", keep_raw=False, headers=PROVIAS_XHR)
    host = PROVIAS.split("/emergenciavial")[0]
    linea = [{"fecha": x.get("fecha"), "texto": " ".join((x.get("contenido") or "").split()),
              "fotos": [{"label": " ".join((f.get("contenido") or "").split()) or "Foto", "url": host + f["url"], "fecha": x.get("fecha"),
                         "credit": "Foto: PROVIAS Nacional — visor de emergencias viales"} for f in x.get("fotos") or [] if f.get("url")]}
             for x in lin.get("data") or []]
    return {"fields": {k: v for k, v in (det.get("data") or {}).items() if k != "Fotos"}, "linea": linea}


def _f_provias(it, ex):
    f, linea = ex.get("fields") or {}, ex.get("linea") or []
    cron = "\n\n".join(f"{x['fecha']} — {x['texto']}" for x in linea[:12] if x["texto"])
    return {"title": f"{it.get('transito')} · {(it.get('tipo') or '').capitalize()}",
            "subtitle": f"{it.get('ruta')} · {it.get('tramo')} · sector {it.get('sector')}",
            "level": None,   # la escala N1–N4 es de avisos; aquí manda la condición de tránsito, que va en el título
            "facts": [["Condición de tránsito", it.get("transito")], ["Tipo", it.get("tipo")], ["Ruta", f.get("Ruta") or it.get("ruta")],
                      ["Tramo y sector", f"{it.get('tramo')} · {it.get('sector')}"], ["Progresiva", f"km {it.get('km_ini')} a {it.get('km_fin')}"],
                      ["Ubicación", (f.get("Ubigeo") or "—").replace("-", " · ").title()], ["Desde", it.get("fecha")],
                      ["Días", f.get("Duracion", it.get("dias"))], ["Última actualización", f.get("FechaActualizacion") or "—"],
                      ["Estado", f.get("Estado") or "—"], ["Contratista / concesión", f.get("Contratista") or f.get("Concesion") or "—"],
                      ["Unidad zonal", f.get("Zonal") or "—"], ["Rutas alternas", (f.get("RutasAlt") or "").strip() or "—"]],
            "text": "\n\n".join(x for x in (f.get("UltimaActualizacion") and f"Última actualización: {' '.join(f['UltimaActualizacion'].split())}",
                                             cron and f"Cronología (lo más reciente primero):\n{cron}") if x) or None,
            "images": [ph for x in linea for ph in x["fotos"]][:6],
            "links": [{"label": "Visor de Emergencias Viales (PROVIAS)", "url": "https://wsgcv.proviasnac.gob.pe/emergenciavial"}],
            "fields": f or {k: v for k, v in it.items() if not k.startswith("_")}}


def _bomberos(it):
    from .sources.bomberos import URL
    res = fetch_json("bomberos", f"{URL}Home/GetResume?numeroparte={it['parte']}", keep_raw=False)
    return {"unidades_tipo": [[x.get("TipoVehiculo"), x.get("Cantidad")] for x in res or []]}


def _bom_min(m):
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def _bom_duracion(it):
    if it.get("duracion_min") is not None:
        return f"{_bom_min(it['duracion_min'])} (desde la llamada hasta que la Mesa lo vio cerrado; hasta 10 min de más)"
    if it.get("duracion_max_min") is not None:
        return f"como máximo {_bom_min(it['duracion_max_min'])} (la Mesa lo vio por primera vez ya cerrado)"
    return "en atención" if it.get("estado") == "Atendiendo" else "—"


def _bom_escalamiento(it):
    llegada, ini = it.get("unidades_llegada") or [], it.get("unidades_iniciales")
    if ini is None or not llegada:
        return "—"
    sumadas = llegada[ini:]
    if not sumadas:
        return f"no: {ini} unidad{'es' if ini != 1 else ''} desde que la Mesa lo vio"
    return f"sí: de {ini} a {len(llegada)} unidades · " + ", ".join(f"{u} ({time.strftime('%H:%M', time.localtime(t))})" for u, t in sumadas)


def _f_bomberos(it, ex):
    from .sources.bomberos import URL
    tipos = ex.get("unidades_tipo") or []
    return {"title": f"{it.get('categoria')} · {it.get('detalle') or '—'}", "subtitle": f"{it.get('direccion') or '—'} · {it.get('distrito') or '—'}",
            "level": None,
            "facts": [["Parte", it.get("parte")], ["Fecha y hora", f"{it.get('fecha')} {it.get('hora')}"], ["Estado", it.get("estado")],
                      ["Tipo (CGBVP)", it.get("tipo")], ["Dirección", it.get("direccion") or "—"], ["Distrito", it.get("distrito") or "—"],
                      ["Unidades", ", ".join(it.get("unidades") or []) or "—"],
                      ["Unidades por tipo", ", ".join(f"{n} {t.lower()}" for t, n in tipos) or "—"],
                      ["Duración", _bom_duracion(it)],
                      ["Escalamiento", _bom_escalamiento(it)],
                      ["Ubicación en el mapa", {"coordenadas": "coordenadas del parte", "distrito": "centro de la provincia (el parte no trae coordenadas)"}.get(it.get("ubicacion"), "sin ubicar")]],
            "links": [{"label": "Mapa del parte (CGBVP)", "url": f"{URL}Home/Map?numparte={it['parte']}"},
                      {"label": "Emergencias 24 horas (CGBVP)", "url": URL}],
            "fields": {k: v for k, v in it.items() if not k.startswith("_")}}


NIVEL_MAR = {"oleaje": {2: "ligero", 3: "moderado", 4: "fuerte"}, "viento": {2: "brisa fresca", 3: "brisa fuerte", 4: "viento fuerte"}}


def _f_aviso_dhn(it, ex):
    from .sources.dhn import ZONA_NOMBRE
    palabras = NIVEL_MAR.get(it.get("tipo"), {})
    facts = [["Tipo", f"Aviso especial de {it.get('tipo')}"], ["Número", it.get("numero")], ["Vigencia", it.get("validez")],
             ["Emitido", it.get("emitido") or "—"]]
    if it.get("tipo") == "oleaje":
        for z, v in (it.get("zonas") or {}).items():
            dias = ", ".join(f"{d[8:]}/{d[5:7]} {palabras.get(n, n)}" for d, n in sorted((v.get("por_dia") or {}).items()))
            facts.append([ZONA_NOMBRE.get(z, z), f"{dias or 'sin oleaje anómalo'} — {v.get('texto')}"])
    else:
        facts += [["Intensidad en la costa", palabras.get(it.get("nivel"), "—") + (f" (hasta {it['nudos']} nudos)" if it.get("nudos") else "")],
                  ["Provincias costeras afectadas", str(len(it.get("provs") or []))]]
    niveles = [n for pp in (it.get("provs_por_dia") or {}).values() for n in pp.values()]
    links = [x for x in ({"label": "Aviso en PDF (DHN)", "url": it.get("pdf")}, {"label": "Imagen del aviso (DHN)", "url": it.get("imagen")},
                         {"label": "Avisos especiales de la DHN", "url": "https://www.dhn.mil.pe/portal/avisos-especiales"}) if x["url"]]
    return {"title": it.get("titulo"), "subtitle": f"Dirección de Hidrografía y Navegación · {it.get('validez')}", "level": max(niveles) if niveles else None,
            "facts": facts, "text": it.get("texto"), "links": links, "fields": {k: v for k, v in it.items() if not k.startswith("_") and k != "texto"}}


def _f_ola_punto(it, ex):
    nombres = ("Próximas 24 h", "De 24 a 48 h", "De 48 a 72 h")
    facts = [[n, f"{v['ola_max_m']} m (máx. a las {v['hora_max'][11:]} del {v['hora_max'][8:10]}/{v['hora_max'][5:7]}) · de {v['direccion']}° · período {v['periodo_s']} s"
              + (f" · mar de fondo {v['mar_de_fondo_max_m']} m" if v.get("mar_de_fondo_max_m") is not None else "") if v else "sin dato"]
             for n, v in zip(nombres, it.get("ventanas") or [])]
    return {"title": f"Altura de ola · {it.get('nombre')}", "subtitle": "Modelo Open-Meteo Marine — referencial, no es un pronóstico oficial de la DHN",
            "level": None, "facts": facts + [["Calculado desde", it.get("desde")]],
            "links": [{"label": "Open-Meteo Marine", "url": "https://open-meteo.com/en/docs/marine-weather-api"},
                      {"label": "Pronóstico oficial de olas (DHN)", "url": "https://www.dhn.mil.pe/portal/"}],
            "fields": {k: v for k, v in it.items() if not k.startswith("_")}}


def _f_obra_ana(it, ex):
    from .sources.ana import TABLERO
    pct = lambda v: "—" if v is None else f"{v:g} %"   # noqa: E731
    sol = lambda v: "—" if v is None else f"S/ {v:,.0f}".replace(",", " ")   # noqa: E731
    barrera = it.get("tipo") == "barrera"
    hist = [e for e in (it.get("hist") or []) if e[0]]
    facts = [["Estado", it.get("estado") + (f" (la ANA: {it.get('estado_ana')})" if it.get("estado_ana") and it.get("estado_ana") != it.get("estado") else "")],
             ["Obra", it.get("actividad")], ["Lugar", f"{it.get('rio')} · sector {it.get('sector')} · {it.get('dist')}, {it.get('prov_nombre')} ({it.get('dep')})"],
             ["Avance físico", f"{pct(it.get('avance_m2_pct'))} de la barrera instalada" if barrera
              else f"{pct(it.get('avance_km_pct'))} del tramo ({it.get('avance_km')} de {it.get('meta_km')} km)"],
             ["Descolmatación", f"{pct(it.get('avance_m3_pct'))} ({it.get('avance_m3')} de {it.get('meta_m3') or '—'} m³)" if not barrera else f"{pct(it.get('avance_m3_pct'))} del movimiento de tierra"],
             ["Presupuesto", f"{sol(it.get('costo'))} · ejecutado {pct(it.get('avance_ppto_pct'))}"],
             ["Plazo", f"del {it.get('inicio') or '—'} al {it.get('fin') or '—'}" + (f" ({it['plazo_dias']:g} días)" if it.get("plazo_dias") else "")],
             ["Último avance reportado", it.get("fec_avance") or "—"],
             ["Peligro", f"{it.get('peligro') or '—'} · nivel {it.get('nivel_peligro') or '—'}"],
             ["Protege", f"{it.get('poblacion') or 0} personas · {it.get('viviendas') or 0} viviendas · {it.get('hectareas') or 0} ha"],
             ["Responsable", it.get("responsable") or "—"], ["Financiamiento", it.get("decreto") or "—"], ["Código", it.get("codigo") or "—"]]
    if len(hist) > 1:
        facts.append(["Avances registrados por la Mesa", " · ".join(f"{e[0][8:]}/{e[0][5:7]}: {pct(e[1])}" for e in hist[-8:])])
    links = [x for x in ({"label": "Ficha técnica (PDF, ANA)", "url": it.get("ficha")}, {"label": "Galería de fotos de la intervención (ANA)", "url": it.get("galeria")},
                         {"label": "Tablero de puntos críticos 2026 (ANA)", "url": TABLERO}) if x["url"]]
    return {"title": f"{'Barrera dinámica' if barrera else 'Obra de prevención'} · {it.get('rio')} ({it.get('sector')})",
            "subtitle": f"{it.get('dist')}, {it.get('prov_nombre')} · {it.get('estado')}", "level": None, "facts": facts, "links": links,
            "fields": {k: v for k, v in it.items() if not k.startswith("_") and k != "hist"}}


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
    ("provias", "emergencia"): _provias, ("bomberos", "parte"): _bomberos,
}
FORMAT = {
    ("enfen", "comunicado"): _f_enfen,
    **{(sid, "noticia"): _f_noticia for sid in ("com_pnp", "com_provias", "com_mtc", "com_mininter")},
    ("igp", "sismo"): _f_sismo, ("serfor", "alerta"): _f_alerta, ("serfor", "foco"): _f_foco,
    ("ingemmet", "zona_alerta"): _f_zona, ("senamhi_hidro", "aviso_estacion"): _f_hidro, ("indeci", "item"): _f_indeci,
    ("senamhi_avisos", "aviso"): _f_aviso, ("firms", "deteccion"): _f_firms, ("provias", "emergencia"): _f_provias,
    ("bomberos", "parte"): _f_bomberos, ("dhn_avisos", "aviso_dhn"): _f_aviso_dhn, ("olas_openmeteo", "punto"): _f_ola_punto, ("ana_obras", "obra"): _f_obra_ana,
}
