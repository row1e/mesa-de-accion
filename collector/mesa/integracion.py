"""API de integración: otras plataformas (p. ej. una vista para tomadores de decisión) leen lo que reúne la Mesa con un
token de solo lectura, sin la contraseña del tablero.

    Authorization: Bearer <token>

Rutas (todas GET, bajo /api/integracion):
    cambios   — registros que cambiaron desde un cursor (sincronización incremental, con altas, cambios y bajas)
    avisos    — polígonos de un aviso-día del SENAMHI, desde el crudo guardado (el SENAMHI no responde a IPs de nubes)
    detalle   — la ficha enriquecida de un registro (fotos, cronología, PDF, satélite), igual que el tablero
    media     — las imágenes que cita el detalle
    danos     — eventos INDECI con cifras de daños, sin duplicar, con el ubigeo del distrito resuelto
    salud     — estado y última lectura buena de cada fuente

Solo fuentes oficiales públicas. Los reportes de corresponsales (datos personales) y las emergencias médicas de los
bomberos quedan fuera siempre, aunque se configure lo contrario."""
import base64
import gzip
import hashlib
import hmac
import json
import logging
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from . import config, db, detail, geo, snapshot
from .sources import SOURCES

log = logging.getLogger("mesa.integracion")
router = APIRouter(prefix="/api/integracion", tags=["integración"])

NUNCA = {("corresponsales", None), ("bomberos", "medica")}       # (fuente, tipo); None = todos los tipos
SIN_CAMPOS = {("indeci", "reporte_pdf"): ("pdf_text",)}           # pesados: van en /detalle, no en /cambios
PAGINA_MAX = 2000


def _permitido(source, kind):
    if (source, None) in NUNCA or (source, kind) in NUNCA:
        return False
    return source in SOURCES and source != "respaldo"


def huella(token):
    return hashlib.sha256(token.encode()).hexdigest()


def cliente(request: Request):
    """Valida el token y devuelve el nombre del cliente. Compara huellas en tiempo constante."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or not config.INTEGRACION_TOKENS:
        raise HTTPException(401, "falta el token de integración", headers={"WWW-Authenticate": "Bearer"})
    h = huella(auth[7:].strip())
    nombre = next((n for n, esperado in config.INTEGRACION_TOKENS.items() if hmac.compare_digest(h, esperado)), None)
    if not nombre:
        raise HTTPException(401, "token de integración inválido", headers={"WWW-Authenticate": "Bearer"})
    log.info("integración %s %s", nombre, request.url.path)
    return nombre


# ── cambios ─────────────────────────────────────────────────────────────────────────────────────────────────

def _cursor_out(ts, source, kind, key):
    return base64.urlsafe_b64encode(json.dumps([ts, source, kind, key]).encode()).decode()


def _cursor_in(s):
    try:
        ts, source, kind, key = json.loads(base64.urlsafe_b64decode(s.encode()))
        return float(ts), source, kind, key
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "cursor inválido")


def _region(p):
    """Departamento (2 dígitos) de un registro, o None si es nacional / no se puede ubicar."""
    for k in ("ubigeo", "prov", "reg", "region"):
        v = str(p.get(k) or "")
        if v[:2].isdigit():
            return v[:2]
    # el nombre que publica la fuente antes que las coordenadas: los límites simplificados dejan fuera puntos de playa
    for k in ("region", "dep", "dpto", "departamento", "nom_departamento"):
        if isinstance(p.get(k), str) and p[k].strip():
            code = geo.region_code(p[k])
            if code:
                return code
    lon, lat = p.get("lon"), p.get("lat")
    if isinstance(lon, (int, float)) and isinstance(lat, (int, float)):
        return geo.region_of_point(lon, lat)
    return None


@router.get("/cambios")
def cambios(desde: str | None = None, fuentes: str | None = None, deps: str | None = None,
            limite: int = Query(1000, ge=1, le=PAGINA_MAX), quien: str = Depends(cliente)):
    """Registros cuyo contenido o vigencia cambió después del cursor, en orden. Sin `desde`, empieza desde el principio
    (carga inicial). Repetir con `siguiente` hasta que `hay_mas` sea falso; guardar `siguiente` para la próxima vez.

    `fuentes`: lista separada por comas de `fuente` o `fuente/tipo`. `deps`: códigos de departamento; los registros
    nacionales o sin ubicación se entregan siempre. Con `deps` una página puede traer menos de `limite` (o ninguno) y aun
    así `hay_mas`: seguir pidiendo. Un registro con `vigente: false` dejó de publicarse en su fuente."""
    filtro = [f.strip() for f in (fuentes or "").split(",") if f.strip()]
    regiones = {d.strip().zfill(2) for d in (deps or "").split(",") if d.strip()}
    ca = "COALESCE(changed_at, last_seen)"
    q, cond, args = f"SELECT source, kind, key, first_seen, last_seen, current, payload, {ca} ca FROM items", [], []
    if desde:
        ts, s, k, key = _cursor_in(desde)
        cond.append(f"({ca} > ? OR ({ca} = ? AND (source, kind, key) > (?, ?, ?)))")
        args += [ts, ts, s, k, key]
    if filtro:      # en SQL, para que la página no venga vacía de lo pedido
        partes = []
        for f in filtro:
            s, _, k = f.partition("/")
            partes.append("(source = ? AND kind = ?)" if k else "source = ?")
            args += [s, k] if k else [s]
        cond.append("(" + " OR ".join(partes) + ")")
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += f" ORDER BY {ca}, source, kind, key LIMIT ?"
    filas = db.conn().execute(q, (*args, limite)).fetchall()
    out = []
    for r in filas:
        if not _permitido(r["source"], r["kind"]):
            continue
        p = json.loads(r["payload"])
        if regiones:
            reg = _region(p)
            if reg is not None and reg not in regiones:
                continue
        for campo in SIN_CAMPOS.get((r["source"], r["kind"]), ()):
            p.pop(campo, None)
        out.append({"fuente": r["source"], "tipo": r["kind"], "clave": r["key"], "vigente": bool(r["current"]),
                    "primera_vez": r["first_seen"], "ultima_vez": r["last_seen"], "cambio": r["ca"], "datos": p})
    ultimo = filas[-1] if filas else None
    return {"registros": out, "hay_mas": len(filas) == limite,
            "siguiente": _cursor_out(ultimo["ca"], ultimo["source"], ultimo["kind"], ultimo["key"]) if ultimo else desde,
            "revisados": len(filas), "ahora": time.time()}


# ── avisos SENAMHI ──────────────────────────────────────────────────────────────────────────────────────────

@router.get("/avisos/{anio}/{nro}/{mapa}")
def aviso_poligonos(anio: int, nro: int, mapa: int, quien: str = Depends(cliente)):
    """GeoJSON del aviso-día (mapa 1, 2 o 3 = día del aviso) tal como lo publicó el SENAMHI, desde el crudo guardado."""
    patron = f"%viewparams=qry:{nro}_{mapa}_{anio}&%"
    fila = db.conn().execute("SELECT path FROM fetches WHERE source='senamhi_avisos' AND url LIKE ? AND path IS NOT NULL "
                             "ORDER BY started_at DESC LIMIT 1", (patron,)).fetchone()
    if not fila:
        raise HTTPException(404, "la Mesa no guardó los polígonos de ese aviso-día")
    ruta = (config.DATA / fila["path"]).resolve()
    if not ruta.is_relative_to(config.DATA.resolve()) or not ruta.exists():
        raise HTTPException(404, "el archivo del aviso ya no está")
    crudo = ruta.read_bytes()
    if ruta.suffix == ".gz":
        crudo = gzip.decompress(crudo)
    return Response(crudo, media_type="application/geo+json", headers={"Cache-Control": "max-age=3600"})


# ── detalle y media ─────────────────────────────────────────────────────────────────────────────────────────

MEDIA = "/api/integracion/media/"


def _reescribir(v):
    """Las rutas /media/… del tablero pasan a la ruta de integración (la otra pide la contraseña)."""
    if isinstance(v, str):
        return MEDIA + v[len("/media/"):] if v.startswith("/media/") else v
    if isinstance(v, list):
        return [_reescribir(x) for x in v]
    if isinstance(v, dict):
        return {k: _reescribir(x) for k, x in v.items()}
    return v


@router.get("/detalle/{source}/{kind}/{key:path}")
def detalle(source: str, kind: str, key: str, fresco: bool = False, quien: str = Depends(cliente)):
    if not _permitido(source, kind):
        raise HTTPException(404, "registro no disponible por integración")
    try:
        d = detail.build(source, kind, key, fresh=fresco)
    except KeyError:
        raise HTTPException(404, "esa fuente no tiene ficha de detalle")
    if d is None:
        raise HTTPException(404, "registro no encontrado")
    return _reescribir(d)


@router.get("/media/{ruta:path}")
def media(ruta: str, quien: str = Depends(cliente)):
    base = (config.DATA / "media").resolve()
    p = (base / ruta).resolve()
    if not p.is_relative_to(base) or not p.is_file():
        raise HTTPException(404, "imagen no encontrada")
    return FileResponse(p, headers={"Cache-Control": "max-age=86400"})


# ── daños INDECI ────────────────────────────────────────────────────────────────────────────────────────────

def _norm(s):
    return re.sub(r"[^A-Z ]", "", geo.norm(s)).strip()


def _catalogo_distritos():
    """(prov, nombre normalizado) → ubigeo, del catálogo del MIDIS que guarda la Mesa (todos los distritos del país)."""
    return {(r["prov"], _norm(r["distrito"])): r["ubigeo"] for r in db.get_items("midis", "distrito") if r.get("prov")}


@router.get("/danos")
def danos(dias: int = Query(45, ge=1, le=120), deps: str | None = None, quien: str = Depends(cliente)):
    """Eventos con cifras de daños en los reportes del INDECI de los últimos `dias`: cada evento una sola vez, con su
    reporte más reciente (las cifras son acumuladas). `ubigeo` es el distrito del reporte; los eventos que abarcan varias
    provincias o regiones traen además el reparto `por_prov` / `por_reg` de la tabla de daños."""
    regiones = {d.strip().zfill(2) for d in (deps or "").split(",") if d.strip()}
    res = snapshot.danos_resumen(days=dias)
    cat = _catalogo_distritos()
    eventos = []
    for e in res["eventos"]:
        if regiones and e.get("reg") not in regiones and not (set(e.get("por_reg") or {}) & regiones):
            continue
        eventos.append({**e, "ubigeo": cat.get((e.get("prov"), _norm(e.get("distrito"))))})
    return {"dias": dias, "desde": res["desde"], "eventos": eventos, "labels": res["labels"],
            "sin_distrito": sum(1 for e in eventos if not e["ubigeo"])}


# ── salud ───────────────────────────────────────────────────────────────────────────────────────────────────

@router.get("/salud")
def salud(quien: str = Depends(cliente)):
    """Estado de cada fuente: ok · actualizando · atrasada · fallando · pendiente, con su última lectura buena."""
    campos = ("id", "org", "name", "status", "interval_s", "last_ok", "last_run", "failures", "message", "items")
    return {"ahora": time.time(),
            "fuentes": [{k: h.get(k) for k in campos} for h in snapshot.health() if _permitido(h["id"], None)]}
