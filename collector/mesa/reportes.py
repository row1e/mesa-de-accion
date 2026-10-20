"""Reportes de corresponsales y ciudadanos: el formulario público /reportar los envía; el tablero los muestra en
"Nuestros corresponsales" para que la mesa los verifique o descarte.

Cada reporte se guarda como un registro más (items, source "corresponsales", kind "reporte"), así tiene ficha, aparece
en el feed y se puede redactar con IA una vez verificado. El teléfono, la IP y el navegador de quien reporta van en
"privado": solo los ve el tablero (detrás de la clave en el servidor), nunca la ficha, el feed, la IA ni la página
pública. La foto o el video se guardan en DATA/reportes/.

Contra el abuso: campo trampa para robots, a lo sumo 5 reportes cada 10 min por IP y 500 por día, y archivos de hasta
15 MB (solo imagen o video).

En una Mesa instalada (MESA_RELE_SERVIDOR puesto) la bandeja es la del servidor: los reportes llegan al formulario
público de la versión en línea, y esta Mesa los trae con su licencia (/api/relay/reportes), los guarda como registros
propios (para la ficha, el feed y la IA) y manda allá las verificaciones. Así todas las Mesas ven la misma bandeja.
"""
import base64
import json
import logging
import collections
import datetime
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from . import config, db

LIMA = ZoneInfo("America/Lima")
SOURCE, KIND = "corresponsales", "reporte"
TIPOS = {"lluvia": "Lluvia intensa o inundación", "huaico": "Huaico o deslizamiento", "incendio": "Incendio",
         "sismo": "Daños por sismo", "via": "Vía bloqueada", "otro": "Otro"}
QUIEN = {"ciudadano": "Ciudadano/a", "medio": "Medio de comunicación", "autoridad": "Autoridad o institución local"}
ESTADOS = ("sin_verificar", "verificado", "descartado")
MAX_ARCHIVO = 15 * 1024 * 1024
CARPETA = config.DATA / "reportes"
_lock = threading.Lock()
_por_ip = collections.defaultdict(list)
log = logging.getLogger("mesa.reportes")


class ReporteError(ValueError):
    pass


def _limpio(s, n):
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def _tel(v):
    d = re.sub(r"\D", "", v or "")
    if len(d) == 11 and d.startswith("51"):
        d = d[2:]
    if not 7 <= len(d) <= 9:
        raise ReporteError("El teléfono no es válido: escriba un celular de 9 dígitos o un fijo con su código.")
    return d


def guardar(b, ip=None, ua=None):
    """Valida y guarda un reporte enviado desde el formulario. Devuelve {id}. ReporteError con un mensaje para mostrar."""
    if b.get("web"):                                   # campo trampa: invisible para personas
        raise ReporteError("No se pudo enviar el reporte.")
    ahora = time.time()
    with _lock:
        ventana = [t for t in _por_ip[ip] if ahora - t < 600]
        if len(ventana) >= 5:
            raise ReporteError("Se enviaron varios reportes seguidos desde esta conexión. Espere unos minutos e intente de nuevo.")
        hoy = db.conn().execute("SELECT COUNT(*) n FROM items WHERE source=? AND kind=? AND first_seen > ?", (SOURCE, KIND, ahora - 86400)).fetchone()["n"]
        if hoy >= 500:
            raise ReporteError("El formulario recibió muchos reportes hoy. Intente más tarde o llame a la mesa de noticias.")
        _por_ip[ip] = ventana + [ahora]
    tipo = b.get("tipo")
    if tipo not in TIPOS:
        raise ReporteError("Elija qué está pasando.")
    desc = _limpio(b.get("descripcion"), 2000)
    if len(desc) < 10:
        raise ReporteError("Cuéntenos un poco más de lo que ve (al menos una frase).")
    nombre = _limpio(b.get("nombre"), 80)
    if len(nombre) < 3:
        raise ReporteError("Escriba su nombre y apellido.")
    tel = _tel(b.get("telefono"))
    quien = b.get("quien") if b.get("quien") in QUIEN else "ciudadano"
    lat, lon = b.get("lat"), b.get("lon")
    try:
        lat, lon = (round(float(lat), 5), round(float(lon), 5)) if lat is not None and lon is not None else (None, None)
        if lat is not None and not (-18.6 < lat < 0.2 and -81.5 < lon < -68.5):
            lat = lon = None                           # fuera del Perú: se ignora la ubicación
    except (TypeError, ValueError):
        lat = lon = None
    from . import geo
    prov = geo.province_of_point(lon, lat) if lat is not None else None
    dep = _limpio(b.get("departamento"), 40)
    reg = prov[:2] if prov else geo.region_code(dep) if dep else None
    rid = f"{datetime.datetime.now(LIMA).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    archivo = None
    if b.get("archivo"):
        a = b["archivo"]
        mime = str(a.get("tipo") or "")
        if not re.match(r"^(image|video)/[\w.+-]+$", mime):
            raise ReporteError("Solo se aceptan fotos o videos.")
        try:
            datos = base64.b64decode(a.get("datos") or "", validate=True)
        except ValueError:
            raise ReporteError("No se pudo leer el archivo adjunto.") from None
        if len(datos) > MAX_ARCHIVO:
            raise ReporteError("El archivo pesa más de 15 MB. Envíe una foto o un video más corto.")
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic", "video/mp4": "mp4",
               "video/quicktime": "mov", "video/webm": "webm"}.get(mime, mime.split("/")[1][:5])
        CARPETA.mkdir(parents=True, exist_ok=True)
        (CARPETA / f"{rid}.{ext}").write_bytes(datos)
        archivo = {"nombre": f"{rid}.{ext}", "tipo": mime, "bytes": len(datos)}
    rec = {"id": rid, "tipo": tipo, "tipo_nombre": TIPOS[tipo], "descripcion": desc, "departamento": dep or None,
           "lugar": _limpio(b.get("lugar"), 160) or None, "lat": lat, "lon": lon, "prov": prov, "reg": reg,
           "afectados": {"si": "sí", "no": "no", "nose": "no sabe"}.get(b.get("afectados")),
           "nombre": nombre, "nombre_al_aire": bool(b.get("nombre_al_aire")), "quien": quien, "quien_nombre": QUIEN[quien],
           "medio": {"nombre": _limpio(b.get("medio_nombre"), 80), "canal": _limpio(b.get("medio_canal"), 40),
                     "detalle": _limpio(b.get("medio_detalle"), 120)} if quien == "medio" else None,
           "autoridad": _limpio(b.get("autoridad_detalle"), 120) if quien == "autoridad" else None,
           "archivo": archivo, "estado": "sin_verificar", "revision": None,
           "privado": {"telefono": tel, "ip": ip, "navegador": _limpio(ua, 200)}}
    db.upsert_items(SOURCE, KIND, {rid: rec})
    return {"id": rid}


def listar(estado=None, limit=200):
    """Para el tablero: incluye el teléfono (privado) y un cruce rápido con las fuentes oficiales."""
    if remoto():
        try:
            sincronizar()
        except Exception as e:  # noqa: BLE001 — sin conexión con el servidor: se muestra lo último que se trajo
            log.warning("Bandeja del servidor no disponible: %s", e)
    rows = db.get_items(SOURCE, KIND, current_only=False, limit=limit)
    if estado:
        rows = [r for r in rows if r.get("estado") == estado]
    return [r | {"cruce": _cruce(r)} for r in rows]


def cambiar_estado(rid, estado, actor, nota=None):
    if estado not in ESTADOS:
        raise ReporteError("Estado no válido.")
    actor = _limpio(actor, 80)
    if not actor:
        raise ReporteError("Indique su nombre para registrar la revisión.")
    if remoto():
        try:
            r = _servidor(f"/api/relay/reportes/{urllib.parse.quote(rid)}/estado", {"estado": estado, "actor": actor, "nota": nota})
        except urllib.error.HTTPError as e:
            raise ReporteError(_detalle(e) or f"El servidor rechazó la revisión (HTTP {e.code}).") from None
        except Exception:  # noqa: BLE001
            raise ReporteError("No hay conexión con la versión en línea: la revisión no se guardó. Intente de nuevo.") from None
        _guardar_local([r])
        return db.get_item(SOURCE, KIND, rid) | {"cruce": r.get("cruce") or _cruce(r)}
    r = db.get_item(SOURCE, KIND, rid)
    if r is None:
        raise ReporteError("El reporte no existe.")
    rec = {k: v for k, v in r.items() if not k.startswith("_")}
    rec["estado"] = estado
    rec["revision"] = {"por": actor, "en": time.time(), "nota": _limpio(nota, 300) or None,
                       "historial": ((r.get("revision") or {}).get("historial") or []) + [[time.time(), actor, estado]]}
    db.upsert_items(SOURCE, KIND, {rid: rec})
    return db.get_item(SOURCE, KIND, rid) | {"cruce": _cruce(rec)}


def ruta_archivo(rid):
    r = db.get_item(SOURCE, KIND, rid)
    a = (r or {}).get("archivo")
    if not a:
        return None, None
    ruta = CARPETA / a["nombre"]
    if remoto() and not ruta.exists():                 # se trae del servidor la primera vez que se abre
        try:
            datos = _servidor(f"/api/relay/reportes/{urllib.parse.quote(rid)}/archivo", crudo=True, timeout=120)
            CARPETA.mkdir(parents=True, exist_ok=True)
            ruta.write_bytes(datos)
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo traer el archivo del reporte %s: %s", rid, e)
    return ruta, a["tipo"]


# ── Mesa instalada: bandeja compartida con el servidor ──────────────────────────────────────────────────────────
_ultima_sync = [0.0]


def remoto():
    return bool(config.RELE_SERVIDOR) and not config.RELE_HUB


def _servidor(ruta, datos=None, crudo=False, timeout=30):
    from . import licencia
    from .http import SSL_CONTEXT
    cod = licencia.codigo_actual()
    if not cod:
        raise ReporteError("Esta Mesa no tiene licencia: no puede leer la bandeja de la versión en línea.")
    req = urllib.request.Request(config.RELE_SERVIDOR.rstrip("/") + ruta, method="POST" if datos is not None else "GET",
                                 data=json.dumps(datos).encode() if datos is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": f"Licencia {cod}",
                                          "User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as r:
        cuerpo = r.read()
    return cuerpo if crudo else json.loads(cuerpo)


def _detalle(e):
    try:
        return json.loads(e.read()).get("detail")
    except Exception:  # noqa: BLE001
        return None


def _guardar_local(rows):
    recs = {r["id"]: {k: v for k, v in r.items() if not k.startswith("_") and k != "cruce"} for r in rows if r.get("id")}
    if recs:
        db.upsert_items(SOURCE, KIND, recs)


def sincronizar(cada=20):
    """Trae los reportes del servidor (a lo sumo cada `cada` segundos). Lanza la excepción si no hay conexión."""
    if time.time() - _ultima_sync[0] < cada:
        return
    _guardar_local(_servidor("/api/relay/reportes"))
    _ultima_sync[0] = time.time()


def start():
    """Mesa instalada: trae la bandeja del servidor cada 2 minutos aunque nadie tenga el tablero abierto (para el feed)."""
    if not remoto():
        return

    def bucle():
        while True:
            try:
                sincronizar()
            except Exception as e:  # noqa: BLE001
                log.debug("Bandeja del servidor: %s", e)
            time.sleep(120)
    threading.Thread(target=bucle, name="bandeja", daemon=True).start()


def _cruce(r):
    """Qué dicen las fuentes oficiales del lugar del reporte: aviso SENAMHI vigente en la provincia, reportes de INDECI
    del mismo departamento en las últimas 24 h y, según el tipo, incendios SERFOR o emergencias viales cercanas."""
    from . import snapshot
    D = snapshot.build()
    out = []
    p, reg = r.get("prov"), r.get("reg")
    c = (D["levelsByDay"].get(D["snapshot"]) or {}).get(p) if p else None
    if c:
        out.append(f"aviso SENAMHI nivel {c['m']} vigente en la provincia")
    if reg:
        n = sum(1 for i in D["indeci"] if i.get("clase") == "reporte" and i.get("reg") == reg)
        out.append(f"{n} reporte{'s' if n != 1 else ''} INDECI en el departamento (24 h)" if n else "sin reportes INDECI en el departamento (24 h)")
    if r.get("tipo") == "incendio" and r.get("lat") is not None:
        cerca = [a for a in D["alertas"] if abs(a["lat"] - r["lat"]) < .05 and abs(a["lon"] - r["lon"]) < .05]
        out.append(f"{len(cerca)} alerta{'s' if len(cerca) != 1 else ''} de incendio SERFOR a menos de ~5 km" if cerca else "sin alertas de incendio SERFOR a menos de ~5 km")
    if r.get("tipo") in ("via", "huaico") and p:
        vs = [v for v in D.get("vias") or [] if v.get("prov") == p]
        if vs:
            out.append(f"{len(vs)} emergencia{'s' if len(vs) != 1 else ''} vial{'es' if len(vs) != 1 else ''} PROVIAS en la provincia")
    return out
