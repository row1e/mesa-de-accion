"""API HTTP + dashboard en vivo (mismo origen). Sin autenticación: pensado para correr en localhost."""
import json
import time

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles

from . import asistente, config, db, detail, ficha, geo, latest, licencia, runner, sat, snapshot
from .sources import SOURCES, midis

app = FastAPI(title="Mesa de Acción · Colector", version="0.1")
# El snapshot pasa de 3 MB en JSON; comprimido baja a una fracción (detrás de Caddy, este ya no recomprime)
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402
app.add_middleware(GZipMiddleware, minimum_size=2000)


# Licencia (solo en la app instalada): vencida o ausente, el tablero muestra la página de licencia y la API no
# entrega datos. Quedan abiertos lo necesario para esa página: /api/licencia, logos y marca.
LIBRES = ("/api/licencia", "/marca/", "/fuentes/", "/licencia")


@app.middleware("http")
async def control_licencia(request: Request, call_next):
    p = request.url.path
    if licencia.requerida() and not p.startswith(LIBRES) and licencia.bloqueada():
        if p in ("/", "/index.html", "/reportar", "/resumen"):
            return _con_marca("licencia.html")
        return JSONResponse({"detail": "Licencia vencida o ausente.", "licencia": licencia.estado()}, status_code=403)
    return await call_next(request)


class LicenciaCodigo(BaseModel):
    codigo: str


@app.get("/api/licencia")
def licencia_estado():
    return licencia.estado()


@app.post("/api/licencia")
def licencia_guardar(b: LicenciaCodigo):
    try:
        return licencia.guardar(b.codigo)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@app.get("/licencia", include_in_schema=False)
def licencia_pagina():
    return _con_marca("licencia.html")
(config.DATA / "media").mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=config.DATA / "media"), name="media")   # fotos extraídas de los PDF


# Marcas de cliente: MESA_MARCA=<clave> pone el logo (web/marcas/<clave>.png) y los colores de [data-marca] en index.html.
MARCAS = {"irtp": "IRTP"}


def _con_marca(pagina):
    html = (config.WEB / pagina).read_text(encoding="utf-8")
    if config.MARCA in MARCAS:
        html = (html.replace("<!--MARCA-->", f'<script>document.documentElement.dataset.marca = "{config.MARCA}";</script>')
                    .replace("<!--MARCA-LOGO-->", f'<img class="marca-logo" src="/marca/{config.MARCA}.png" alt="{MARCAS[config.MARCA]}">'))
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def index():
    return _con_marca("index.html")


@app.get("/resumen", include_in_schema=False)
def resumen_pagina():
    """Resumen del día: lo más significativo de las últimas 24 horas, con sus registros y la redacción con IA."""
    return _con_marca("resumen.html")


@app.get("/api/resumen")
def get_resumen():
    from . import resumen
    return resumen.build()


# ── Reportes de corresponsales (ver reportes.py). Enviar es público; listar, revisar y ver archivos, solo el tablero ──
def _ip(request):
    return (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else None)


@app.post("/api/reportes")
async def reporte_nuevo(request: Request):
    from . import reportes
    try:
        b = await request.json()
    except ValueError:
        raise HTTPException(400, "Envío no válido.") from None
    try:
        return reportes.guardar(b if isinstance(b, dict) else {}, _ip(request), request.headers.get("user-agent"))
    except reportes.ReporteError as e:
        raise HTTPException(400, str(e)) from None


@app.get("/api/reportes")
def reportes_lista(estado: str | None = None):
    from . import reportes
    return reportes.listar(estado)


class ReporteEstado(BaseModel):
    estado: str
    actor: str
    nota: str | None = None


@app.post("/api/reportes/{rid}/estado")
def reporte_estado(rid: str, b: ReporteEstado):
    from . import reportes
    try:
        return reportes.cambiar_estado(rid, b.estado, b.actor, b.nota)
    except reportes.ReporteError as e:
        raise HTTPException(400, str(e)) from None


@app.get("/api/reportes/{rid}/archivo", include_in_schema=False)
def reporte_archivo(rid: str):
    from . import reportes
    ruta, tipo = reportes.ruta_archivo(rid)
    if not ruta or not ruta.exists():
        raise HTTPException(404, "sin archivo")
    return FileResponse(ruta, media_type=tipo)


def _base(request):
    """Origen público con que se abrió la página (detrás de Caddy: https y el dominio real)."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _meta_reportar(base):
    """Etiquetas para que el enlace compartido se vea bien en WhatsApp, Facebook, X y Google: título, descripción e
    imagen de 1200×630 (pública: /marca/ está abierta en el servidor). Direcciones absolutas, del dominio de la visita."""
    import html as _h
    marca = MARCAS.get(config.MARCA)
    titulo = f"Reporta una emergencia · {marca}" if marca else "Reporta una emergencia · Mesa de Acción"
    desc = (f"¿Ves una emergencia? Repórtala a la mesa de noticias{' del ' + marca if marca else ''}: qué pasa, dónde y una foto. "
            "Verificamos cada reporte antes de publicarlo.")
    img = f"{base}/marca/{config.MARCA}-reportar.png" if marca else f"{base}/marca/reportar.png"
    alt = "Reporta una emergencia: cuéntanos qué pasa y dónde, con una foto. Riesgo para la vida: Bomberos 116, Policía 105, SAMU 106."
    url = f"{base}/reportar"
    e = lambda s: _h.escape(s, quote=True)   # noqa: E731
    ld = json.dumps({"@context": "https://schema.org", "@type": "WebPage", "name": titulo, "description": desc, "url": url,
                     "inLanguage": "es-PE", "image": img, **({"publisher": {"@type": "Organization", "name": marca}} if marca else {})},
                    ensure_ascii=False)
    tags = [f'<meta name="description" content="{e(desc)}">', f'<link rel="canonical" href="{e(url)}">',
            '<meta name="robots" content="index, follow, max-image-preview:large">', '<meta name="theme-color" content="#C1002D">' if marca else '<meta name="theme-color" content="#15140F">',
            '<meta property="og:type" content="website">', f'<meta property="og:site_name" content="{e(marca or "Mesa de Acción")}">',
            '<meta property="og:locale" content="es_PE">', f'<meta property="og:url" content="{e(url)}">',
            f'<meta property="og:title" content="{e(titulo)}">', f'<meta property="og:description" content="{e(desc)}">',
            f'<meta property="og:image" content="{e(img)}">', *([f'<meta property="og:image:secure_url" content="{e(img)}">'] if img.startswith("https") else []),
            '<meta property="og:image:type" content="image/png">', '<meta property="og:image:width" content="1200">',
            '<meta property="og:image:height" content="630">', f'<meta property="og:image:alt" content="{e(alt)}">',
            '<meta name="twitter:card" content="summary_large_image">', f'<meta name="twitter:title" content="{e(titulo)}">',
            f'<meta name="twitter:description" content="{e(desc)}">', f'<meta name="twitter:image" content="{e(img)}">',
            f'<meta name="twitter:image:alt" content="{e(alt)}">',
            *([f'<link rel="icon" type="image/png" href="/marca/{config.MARCA}.png">', f'<link rel="apple-touch-icon" href="/marca/{config.MARCA}.png">'] if marca else []),
            f'<script type="application/ld+json">{ld}</script>']
    return titulo, "\n".join(tags)


@app.get("/reportar", include_in_schema=False)
def reportar(request: Request):
    """Formulario público para corresponsales y ciudadanos: envía a /api/reportes (ver reportes.py)."""
    titulo, meta = _meta_reportar(_base(request))
    r = _con_marca("reportar.html")
    html = r.body.decode("utf-8").replace("<title>Reporta una emergencia · Mesa de Acción</title>", f"<title>{titulo}</title>").replace("<!--META-->", meta)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/robots.txt", include_in_schema=False)
def robots(request: Request):
    """Solo el formulario público se indexa; el tablero y la API no (además, en el servidor piden clave)."""
    return PlainTextResponse(f"User-agent: *\nAllow: /reportar\nAllow: /marca/\nDisallow: /\n\nSitemap: {_base(request)}/sitemap.xml\n")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    return Response(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    f'<url><loc>{_base(request)}/reportar</loc><changefreq>monthly</changefreq></url></urlset>\n', media_type="application/xml")


@app.get("/marca/{name}", include_in_schema=False)
def marca(name: str):
    path = config.WEB / "marcas" / name
    if not path.is_file() or path.parent != config.WEB / "marcas":
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "max-age=86400"})


@app.get("/fuentes/{name}", include_in_schema=False)
def logo_fuente(name: str):
    """Logos de las instituciones para la sección de enlaces oficiales."""
    path = config.WEB / "fuentes" / name
    if not path.is_file() or path.parent != config.WEB / "fuentes":
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "max-age=86400"})


@app.get("/app.js", include_in_schema=False)
def app_js():
    return FileResponse(config.WEB / "app.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/vendor/{name}", include_in_schema=False)
def vendor(name: str):
    path = config.WEB / "vendor" / name
    if not path.is_file() or path.parent != config.WEB / "vendor":
        raise HTTPException(404)
    return FileResponse(path, media_type="text/javascript", headers={"Cache-Control": "max-age=86400"})


@app.get("/ficha/{ubigeo}.pdf", include_in_schema=False)
def ficha_pdf(ubigeo: str, request: Request):
    """Ficha en PDF (impresa por Chrome headless). 2 dígitos = departamento, 4 = provincia."""
    if not ficha.scope_of(ubigeo):
        raise HTTPException(404, "código desconocido: use 2 dígitos (departamento) o 4 (provincia)")
    try:
        # Siempre por la dirección local: detrás de un túnel/Access, la URL pública llevaría a Chrome a la pantalla de login.
        data, fname = ficha.pdf(ubigeo, f"http://127.0.0.1:{config.PORT}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"No se pudo generar el PDF: {e}") from None
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/ficha/{ubigeo}", include_in_schema=False)
def ficha_html(ubigeo: str):
    """Ficha provincial imprimible (A4)."""
    res = ficha.html(ubigeo)
    if res is None:
        raise HTTPException(404, "código desconocido: use 2 dígitos (departamento) o 4 (provincia)")
    return Response(res[0], media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


@app.get("/api/ficha/{ubigeo}")
def ficha_json(ubigeo: str):
    """Datos de la ficha provincial (lo mismo que se imprime)."""
    d = ficha.build(ubigeo)
    if d is None:
        raise HTTPException(404, "ubigeo provincial desconocido")
    return d


@app.get("/api/health")
def health():
    return {"now": time.time(), "sources": snapshot.health()}


@app.get("/api/snapshot")
def get_snapshot():
    data = snapshot.build()
    body = json.dumps({**data, "health": snapshot.health()}, ensure_ascii=False, separators=(",", ":"))
    return Response(body, media_type="application/json", headers={"Cache-Control": "no-store"})


@app.post("/api/refresh/{source}")
def refresh(source: str):
    targets = list(SOURCES) if source == "all" else [source]
    if any(t not in SOURCES for t in targets):
        raise HTTPException(404, f"fuente desconocida: {source}")
    result = {t: dict(zip(("accepted", "reason"), runner.request_refresh(t))) for t in targets}
    return JSONResponse(result, status_code=202)


@app.get("/api/detail")
def get_detail(source: str, kind: str, key: str, fresh: bool = False):
    """Ficha completa de un registro con enlaces a la fuente. `fresh=true` ignora la caché del enriquecimiento."""
    d = detail.build(source, kind, key, fresh=fresh)
    if d is None:
        raise HTTPException(404, "registro no encontrado")
    return d


@app.get("/api/latest")
def get_latest(source: str | None = None, region: str | None = None, limit: int = Query(60, le=300),
               before: float | None = None, days: int = Query(7, le=60), seguimientos: bool = False):
    """Últimos registros recibidos (todas las fuentes o una), opcionalmente por región; paginar con `before`."""
    if source and source not in SOURCES and source not in {s for s, _ in latest.FEED_KINDS}:
        raise HTTPException(404, f"fuente desconocida: {source}")
    # Por defecto no se muestran los reportes INDECI que siguen eventos ocurridos hace más de una semana.
    data = latest.feed(source=source, region=region, limit=limit, before=before, days=days, seguimientos=seguimientos)
    data["counts24h"] = latest.counts(region=region, seguimientos=seguimientos)
    return data


@app.get("/api/bomberos/historial")
def get_bomberos_historial(dias: int = Query(7, ge=1, le=90)):
    """Partes del CGBVP de los últimos días (filas compactas) y médicas solo con fecha y lugar, para las estadísticas."""
    from .sources import bomberos
    return bomberos.historial(dias)


# ── Relé web (ver rele_hub.py): las Mesas instaladas en el Perú hacen las consultas que el servidor no puede ─────
def _cliente_rele(request):
    from . import rele_hub
    if not rele_hub.activo():
        raise HTTPException(404, "este servidor no tiene el relé web activo")
    auth = request.headers.get("authorization", "")
    cliente = rele_hub.cliente_de(auth[len("Licencia "):]) if auth.startswith("Licencia ") else None
    if not cliente:
        raise HTTPException(401, "licencia inválida o vencida")
    return cliente


@app.post("/api/relay/poll", include_in_schema=False)
def relay_poll(request: Request):
    from . import rele_hub
    t = rele_hub.tomar(_cliente_rele(request))
    return t if t else Response(status_code=204)


class RelayResultado(BaseModel):
    id: int
    status: int | None = None
    headers: dict | None = None
    body_b64: str | None = None
    error: str | None = None


@app.post("/api/relay/result", include_in_schema=False)
def relay_result(request: Request, b: RelayResultado):
    from . import rele_hub
    ok = rele_hub.entregar(_cliente_rele(request), b.id, b.status, b.headers, b.body_b64, b.error)
    return {"ok": ok}


@app.get("/api/relay/estado")
def relay_estado():
    """En el servidor: las Mesas conectadas al relé web. En una Mesa instalada: si está atendiendo al servidor."""
    from . import rele_cliente, rele_hub
    return rele_hub.estado() if rele_hub.activo() else rele_cliente.estado()


@app.get("/api/sat")
def get_sat(lat: float, lon: float, date: str, km: float = Query(25, ge=2, le=300), layer: str = "auto", fires: bool = True):
    """Vista satelital NASA GIBS centrada en un punto (mejor imagen disponible, cacheada)."""
    if layer not in ("auto", "viirs", "modis"):
        raise HTTPException(400, "layer: auto | viirs | modis")
    try:
        return sat.best(lat, lon, km, date, layer=layer, fires=fires)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"NASA GIBS no respondió: {e}") from None


@app.get("/api/runs")
def runs(source: str | None = None, limit: int = Query(50, le=500)):
    q, args = "SELECT * FROM runs", []
    if source:
        q, args = q + " WHERE source=?", [source]
    return [dict(r) for r in db.conn().execute(q + " ORDER BY started_at DESC LIMIT ?", (*args, limit))]


@app.get("/api/items/{source}/{kind}")
def items(source: str, kind: str, current: bool = True, limit: int = Query(1000, le=20000)):
    """Registros normalizados tal como se guardan (para integrar con otras aplicaciones)."""
    return db.get_items(source, kind, current_only=current, limit=limit)


@app.get("/api/provincia/{ubigeo}")
def provincia(ubigeo: str):
    """Todo lo que dicen las fuentes sobre una provincia (ubigeo INEI de 4 dígitos)."""
    p = geo.provinces().get(ubigeo)
    if not p:
        raise HTTPException(404, "ubigeo provincial desconocido")
    d = snapshot.build()
    name = geo.norm(p["nombre"])
    return {
        "ubigeo": ubigeo, "provincia": p["nombre"], "departamento": p["dpto"],
        "avisos_por_dia": {day: cells[ubigeo] for day, cells in d["levelsByDay"].items() if ubigeo in cells},
        "uv": d["uv"].get(ubigeo),
        "indeci": [i for i in d["indeci"] if i.get("prov") == ubigeo],
        "alertas_incendio": [a for a in d["alertas"] if str(a.get("ubigeo", "")).startswith(ubigeo)],
        "focos_24h": sum(1 for f in d["focos"] if geo.norm(f[3]) == name),
        "zonas_criticas": [z for z in d["zonas"] if geo.norm(z.get("provincia")) == name],
        "hidro": [h for h in d["hidro"] if geo.norm(h.get("nom_provincia")) == name],
        "denuncias_sidpol": ({"meses": d["sidpol"]["months"], "por_modalidad": d["sidpol"]["provs"].get(ubigeo)}
                             if d.get("sidpol") else None),
    }


@app.get("/api/midis/distrito/{ubigeo}")
def midis_distrito(ubigeo: str, fresh: bool = False):
    """Contexto social del distrito (MIDIS · REDInforma). Se pide a MIDIS bajo demanda y se cachea 30 días."""
    try:
        return midis.distrito(ubigeo, fresh=fresh)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@app.get("/api/midis/provincia/{ubigeo}")
def midis_provincia(ubigeo: str, fresh: bool = False):
    """Contexto social de la provincia: suma de sus distritos (MIDIS solo responde por distrito)."""
    try:
        return midis.provincia(ubigeo, fresh=fresh)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@app.get("/api/midis/region/{code}")
def midis_region(code: str, fresh: bool = False):
    """Indicadores regionales del reporte «Mi Región» (MIDIS · REDInforma)."""
    return midis.region(code, fresh=fresh)


@app.get("/api/midis/distritos")
def midis_distritos(prov: str | None = None, limit: int = Query(2000, le=5000)):
    """Catálogo de distritos recogido de REDInforma; `prov` filtra por ubigeo provincial de 4 dígitos."""
    rows = db.get_items("midis", "distrito", limit=limit, order="key")
    return [r for r in rows if not prov or r.get("prov") == prov]


# ── Asistente de IA (mesa/asistente.py) ─────────────────────────────────────
class IAGenerar(BaseModel):
    tipo: str
    alcance: str = "registro"
    source: str | None = None
    kind: str | None = None
    key: str | None = None
    lugar: str | None = None   # briefing de un departamento (2 dígitos) o provincia (4); sin él, nacional
    actor: str


class IAPieza(BaseModel):
    etiqueta: str
    texto: str


class IAEditar(BaseModel):
    piezas: list[IAPieza]
    actor: str


class IAAccion(BaseModel):
    actor: str
    confirmar_cifras: bool = False
    motivo: str | None = None


def _ia(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except asistente.IAError as e:
        raise HTTPException(e.status, str(e)) from None


@app.get("/api/ia/estado")
def ia_estado():
    return {"habilitado": asistente.habilitado(), "modelo": asistente.MODELO,
            "tipos": {k: {"nombre": v[0], "alcances": sorted(v[1])} for k, v in asistente.TIPOS.items()}}


@app.post("/api/ia/borradores")
def ia_generar(b: IAGenerar, request: Request):
    """Genera un borrador. Tarda lo que tarde el modelo (típicamente 10-60 s), con un reintento si falla la verificación."""
    ref = {"source": b.source, "kind": b.kind, "key": b.key} if b.alcance == "registro" else None
    if b.alcance == "registro" and not all(ref.values()):
        raise HTTPException(400, "Falta source, kind o key del registro.")
    lugar = b.lugar if b.alcance == "briefing" and b.lugar else None
    return _ia(asistente.generar, b.tipo, b.alcance, ref, b.actor, request.client.host if request.client else None, lugar)


@app.get("/api/ia/borradores")
def ia_listar(estado: str | None = None, limit: int = Query(50, le=500)):
    return asistente.listar(estado, limit)


@app.get("/api/ia/borradores/{bid}")
def ia_obtener(bid: int, dossier: bool = False):
    """Borrador con su verificación y su auditoría completa; `dossier=true` agrega los datos que recibió el modelo."""
    return _ia(asistente.obtener, bid, dossier)


@app.post("/api/ia/borradores/{bid}/editar")
def ia_editar(bid: int, b: IAEditar, request: Request):
    return _ia(asistente.editar, bid, [p.model_dump() for p in b.piezas], b.actor, request.client.host if request.client else None)


@app.post("/api/ia/borradores/{bid}/aprobar")
def ia_aprobar(bid: int, b: IAAccion, request: Request):
    return _ia(asistente.aprobar, bid, b.actor, b.confirmar_cifras, request.client.host if request.client else None)


@app.post("/api/ia/borradores/{bid}/descartar")
def ia_descartar(bid: int, b: IAAccion, request: Request):
    return _ia(asistente.descartar, bid, b.actor, b.motivo, request.client.host if request.client else None)


@app.post("/api/ia/borradores/{bid}/texto", response_class=PlainTextResponse)
def ia_texto(bid: int, b: IAAccion, request: Request):
    """Texto final con la línea de fuente. Solo para borradores aprobados; cada entrega queda registrada."""
    return _ia(asistente.texto_final, bid, b.actor, request.client.host if request.client else None)
