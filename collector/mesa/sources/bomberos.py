"""Emergencias de las últimas 24 horas del Cuerpo General de Bomberos Voluntarios del Perú (CGBVP).

La página pública https://sgonorte.bomberosperu.gob.pe/24horas lista cada parte con número, fecha y hora, dirección
(con coordenadas cuando las hay), tipo, estado y unidades. Cubre sobre todo Lima y Callao, más la costa sur (Cañete,
Chincha, Pisco, Ica), Huacho y algo de Ayacucho: el CGBVP no publica algo equivalente para el resto del país.

Las emergencias médicas se guardan solo para contarlas (fecha, distrito, estado): su dirección suele ser la de una
vivienda y no se guarda ni se publica. Por lo mismo, la página cruda no se archiva (keep_raw=False).

La página solo muestra el estado actual de cada parte. Como se consulta cada 10 min, aquí se registra lo que cambia:
cuándo pasó a "Cerrado" (duración, con hasta 10 min de más) y qué unidades se fueron sumando (escalamiento). Los
partes que salen de la ventana de 24 h quedan en la base (no vigentes): son el historial de 7 y 30 días.
"""
import datetime
import html
import json
import re
import time
from zoneinfo import ZoneInfo

from .. import db, geo
from ..http import fetch_text

URL = "https://sgonorte.bomberosperu.gob.pe/24horas/"
MEDICA = "EMERGENCIA MEDICA"
# Departamentos que cubre la página: desempatan distritos homónimos (p. ej. San Andrés, Independencia)
COBERTURA = ("15", "07", "11", "05")
# Ante muchas consultas el sitio redirige a "Acceso Prohibido" (pide captcha). No se intenta sortear: se espera.
PAUSA_BLOQUEO = 3600
LIMA = ZoneInfo("America/Lima")
_pausa = {"hasta": 0.0}


def _celdas(fila):
    return [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", fila, re.S)]


def _fecha_hora(txt):
    """'09/10/2026 09:41:07 p.m.' → ('2026-10-09', '21:41')"""
    m = re.match(r"(\d{2})/(\d{2})/(\d{4}) (\d{1,2}):(\d{2})(?::\d{2})?\s*([ap])\.?\s*m\.?", txt or "", re.I)
    if not m:
        return None, None
    h = int(m[4]) % 12 + (12 if m[6].lower() == "p" else 0)
    return f"{m[3]}-{m[2]}-{m[1]}", f"{h:02d}:{m[5]}"


_IDX = {}


def _distritos():
    """Nombre normalizado → [(ubigeo provincial, región)] según el catálogo de distritos de MIDIS. Se arma una vez,
    cuando el catálogo ya existe (en una instalación nueva MIDIS puede correr después que esta fuente)."""
    if not _IDX:
        for d in db.get_items("midis", "distrito"):
            _IDX.setdefault(geo.norm(d["distrito"]), []).append((d["prov"], d["region"]))
    return _IDX


def _ubicar_distrito(nombre):
    """(prov, reg) de un distrito por su nombre; la página a veces lo trae truncado ('CARMEN DE LA LEGUA REYNOS')."""
    n = geo.norm(nombre)
    if not n:
        return None, None
    idx = _distritos()
    cands = idx.get(n) or [v for k, vs in idx.items() if k.startswith(n) and len(n) >= 8 for v in vs]
    preferidos = [c for c in cands if c[1] in COBERTURA] or cands
    return preferidos[0] if len({c[0] for c in preferidos}) == 1 else (None, None)


def _direccion(txt):
    """Dirección sin coordenadas, distrito y (lon, lat). (0,0) significa sin coordenadas."""
    coords = re.search(r"\((-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)\)", txt)
    lat, lon = (float(coords[1]), float(coords[2])) if coords else (0.0, 0.0)
    limpio = re.sub(r"\s*\(-?[\d.]+,\s*-?[\d.]+\)\s*", " ", txt)
    limpio = re.sub(r"\s+Nro\.\s*(-|000|S/N)?\s*(?= - |$)", "", limpio)
    limpio = re.sub(r"\s+Nro\.\s*", " N° ", limpio)
    calle, _, distrito = limpio.rpartition(" - ")
    if not calle:
        calle, distrito = limpio, ""
    ok = lat != 0 and lon != 0 and -18.5 < lat < 0 and -82 < lon < -68
    return " ".join(calle.split()), distrito.strip(), (round(lon, 5), round(lat, 5)) if ok else None


def _ts_llamada(p):
    try:
        return datetime.datetime.fromisoformat(f"{p['fecha']}T{p['hora']}").replace(tzinfo=LIMA).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def _seguir(p, previo, ahora):
    """Suma al parte leído ahora lo ya registrado: cambios de estado, unidades que se fueron sumando y duración.

    Un parte que la Mesa vio por primera vez ya cerrado (al arrancar, o tras estar apagada) no tiene duración medida:
    solo un máximo (duracion_max_min). Las unidades que ya estaban cuando lo vio por primera vez cuentan como iniciales.
    """
    ahora = round(ahora)
    if previo is None:
        estados, llegada = [[ahora, p["estado"]]], [[u, ahora] for u in p["unidades"]]
        iniciales, visto_abierto = len(p["unidades"]), p["estado"] == "Atendiendo"
    else:   # los registros anteriores a este seguimiento no traen estos campos: se reconstruyen con lo que hay
        t0 = round(previo.get("_first_seen") or ahora)
        estados = previo.get("estados") or [[t0, previo.get("estado")]]
        llegada = previo.get("unidades_llegada") or [[u, t0] for u in previo.get("unidades") or []]
        iniciales = previo.get("unidades_iniciales", len(llegada))
        visto_abierto = previo.get("visto_abierto", previo.get("estado") == "Atendiendo")
        if estados[-1][1] != p["estado"]:
            estados.append([ahora, p["estado"]])
        ya = {u for u, _ in llegada}
        llegada += [[u, ahora] for u in p["unidades"] if u not in ya]
        visto_abierto = visto_abierto or p["estado"] == "Atendiendo"
    cierre = next((t for t, e in estados if e == "Cerrado"), None) if p["estado"] == "Cerrado" else None
    t_llamada = _ts_llamada(p)
    minutos = round((cierre - t_llamada) / 60) if cierre and t_llamada else None
    return p | {"estados": estados, "unidades_llegada": llegada, "unidades_iniciales": iniciales, "unidades_total": len(llegada),
                "escalo": len(llegada) > iniciales, "visto_abierto": visto_abierto, "cerrado_ts": cierre,
                "duracion_min": minutos if visto_abierto else None, "duracion_max_min": None if visto_abierto else minutos}


def _bloqueado(hasta):
    return RuntimeError("el sitio del CGBVP bloqueó temporalmente esta conexión por exceso de consultas (pide captcha); "
                        f"sin consultar hasta las {time.strftime('%H:%M', time.localtime(hasta))}")


def bomberos():
    if time.time() < _pausa["hasta"]:
        raise _bloqueado(_pausa["hasta"])
    page = fetch_text("bomberos", URL, name="24horas.html", keep_raw=False, timeout=60)
    if "Acceso Prohibido" in page or "no eres humano" in page:
        _pausa["hasta"] = time.time() + PAUSA_BLOQUEO
        raise _bloqueado(_pausa["hasta"])
    total_pagina = re.search(r"24 horas:\s*(\d+)\s*registro", html.unescape(page))
    partes, medicas = {}, {}
    for fila in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        c = _celdas(fila)
        if len(c) < 5 or not c[0].isdigit():
            continue
        parte, (fecha, hora), tipo, estado = c[0], _fecha_hora(c[1]), c[3], c[4]
        unidades = [u for u in re.findall(r"<li[^>]*>.*?<span>([^<]+)</span>", fila, re.S)]
        calle, distrito, ll = _direccion(c[2])
        prov = geo.province_of_point(*ll) if ll else None
        reg = prov[:2] if prov else (geo.region_of_point(*ll) if ll else None)
        if not prov and distrito:
            p2, r2 = _ubicar_distrito(distrito)
            prov, reg = prov or p2, reg or r2
        partes_tipo = [t.strip() for t in tipo.split(" / ")]
        base = {"parte": parte, "fecha": fecha, "hora": hora, "distrito": distrito.title() or None, "estado": estado.title(),
                "prov": prov, "reg": reg}
        if partes_tipo[0] == MEDICA:
            medicas[parte] = base | {"subtipo": partes_tipo[1].title() if len(partes_tipo) > 1 else None}
            continue
        partes[parte] = base | {"tipo": tipo, "categoria": partes_tipo[0].title(), "detalle": " / ".join(partes_tipo[1:]).capitalize() or None,
                                "direccion": calle.title(), "lon": ll[0] if ll else None, "lat": ll[1] if ll else None,
                                "ubicacion": "coordenadas" if ll else ("distrito" if prov else None), "unidades": unidades}
    if not partes and not medicas:
        raise RuntimeError("la página no trajo partes (¿cambió su formato?)")
    ahora = time.time()
    partes = {k: _seguir(p, db.get_item("bomberos", "parte", k), ahora) for k, p in partes.items()}
    total, new = db.upsert_items("bomberos", "parte", partes, snapshot=True)
    db.upsert_items("bomberos", "medica", medicas, snapshot=True)
    activos = sum(1 for p in partes.values() if p["estado"] == "Atendiendo")
    return total + len(medicas), new, (f"{total} emergencias ({new} nuevas, {activos} en atención) y {len(medicas)} médicas"
                                       f" en 24 h" + (f" · la página indica {total_pagina[1]}" if total_pagina else ""))


CAMPOS_HIST = ("fecha", "hora", "categoria", "detalle", "distrito", "prov", "reg", "estado", "duracion_min", "duracion_max_min",
               "unidades_total", "escalo", "direccion")


def historial(dias=7):
    """Partes y médicas de los últimos `dias` días (por fecha del parte) en filas compactas para las estadísticas del tablero.

    Las médicas van sin dirección: fecha, región, provincia y distrito. `desde` es el primer parte que registró esta Mesa:
    antes de esa fecha no hay datos (la página del CGBVP no guarda historial)."""
    lim = (datetime.datetime.now(LIMA).date() - datetime.timedelta(days=dias - 1)).isoformat()
    filas = lambda kind: db.conn().execute(   # noqa: E731
        "SELECT key, payload FROM items WHERE source='bomberos' AND kind=? AND json_extract(payload,'$.fecha') >= ?", (kind, lim))
    partes = [[r["key"], *(json.loads(r["payload"]).get(c) for c in CAMPOS_HIST)] for r in filas("parte")]
    medicas = [[(p := json.loads(r["payload"])).get("fecha"), p.get("reg"), p.get("prov"), p.get("distrito")] for r in filas("medica")]
    desde = db.conn().execute("SELECT MIN(json_extract(payload,'$.fecha')) d FROM items WHERE source='bomberos' AND kind='parte'").fetchone()["d"]
    return {"dias": dias, "desde_fecha": lim, "registro_desde": desde, "campos": ["key", *CAMPOS_HIST], "partes": partes, "medicas": medicas}
