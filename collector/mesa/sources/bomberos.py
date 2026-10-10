"""Emergencias de las últimas 24 horas del Cuerpo General de Bomberos Voluntarios del Perú (CGBVP).

La página pública https://sgonorte.bomberosperu.gob.pe/24horas lista cada parte con número, fecha y hora, dirección
(con coordenadas cuando las hay), tipo, estado y unidades. Cubre sobre todo Lima y Callao, más la costa sur (Cañete,
Chincha, Pisco, Ica), Huacho y algo de Ayacucho: el CGBVP no publica algo equivalente para el resto del país.

Las emergencias médicas se guardan solo para contarlas (fecha, distrito, estado): su dirección suele ser la de una
vivienda y no se guarda ni se publica. Por lo mismo, la página cruda no se archiva (keep_raw=False).
"""
import html
import re

from .. import db, geo
from ..http import fetch_text

URL = "https://sgonorte.bomberosperu.gob.pe/24horas/"
MEDICA = "EMERGENCIA MEDICA"
# Departamentos que cubre la página: desempatan distritos homónimos (p. ej. San Andrés, Independencia)
COBERTURA = ("15", "07", "11", "05")


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


def bomberos():
    page = fetch_text("bomberos", URL, name="24horas.html", keep_raw=False, timeout=60)
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
    total, new = db.upsert_items("bomberos", "parte", partes, snapshot=True)
    db.upsert_items("bomberos", "medica", medicas, snapshot=True)
    activos = sum(1 for p in partes.values() if p["estado"] == "Atendiendo")
    return total + len(medicas), new, (f"{total} emergencias ({new} nuevas, {activos} en atención) y {len(medicas)} médicas"
                                       f" en 24 h" + (f" · la página indica {total_pagina[1]}" if total_pagina else ""))
