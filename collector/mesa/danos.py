"""Daños de los reportes INDECI agrupados por evento: base del panel "últimos 7 días" y del historial (/danos).

Cómo se cuenta (para no inflar ni perder cifras):
- Un evento recibe varios reportes (preliminar, complementarios N° 1, 2…) y cada uno repite el ACUMULADO del evento.
  Nunca se suman reportes: de cada evento vale el reporte más reciente que trae cifras.
- Un evento se identifica por su código SINPAD. Sin código, por evento + distrito + departamento + FECHA DE OCURRENCIA:
  el mismo distrito puede tener varios incendios en una temporada y cada uno es un evento distinto. Fechas a un día
  de distancia se toman como el mismo evento (un incendio que se reactiva, un reporte que cambia la hora a medianoche).
- Los reportes sin fecha de ocurrencia legible se suman al evento más reciente del mismo lugar ocurrido hasta 30 días
  antes; si no hay ninguno, cuentan como evento propio fechado el día del reporte (fecha_estimada).
- Las vías llegan en metros o en kilómetros según el reporte: se pasan todas a kilómetros antes de sumar.
- Muchos reportes preliminares no traen cifras ("EDAN en proceso de evaluación"): el evento existe pero sin números
  todavía. Se cuentan aparte, para que un vacío no se lea como "sin daños".
"""
import collections
import datetime
import re
import time

from . import db, geo

EVENTO_GAP_DIAS = 1           # fechas de ocurrencia a esta distancia o menos: mismo evento
SIN_FECHA_DIAS = 30           # un reporte sin fecha se une a un evento del mismo lugar ocurrido hasta estos días antes

# Tipo de evento → categoría del gráfico (la primera regla que coincide).
CATEGORIAS = [
    ("incendio_forestal", "Incendios forestales", ("INCENDIO FORESTAL",)),
    ("incendio_urbano", "Incendios urbanos", ("INCENDIO",)),
    ("lluvias", "Lluvias, huaicos e inundaciones", ("LLUVIA", "HUAICO", "INUNDA", "DESLIZ", "DERRUMBE", "ALUD", "EROSI",
                                                    "DESBORDE", "AVALANCHA", "MOVIMIENTO EN MASA", "REPTACI", "HUNDIMIENTO",
                                                    "TORMENTA ELECTRICA")),
    ("vientos", "Vientos fuertes", ("VIENTO", "TORMENTA", "OLEAJE", "MARETAZO")),
    ("frio", "Heladas, nevadas y granizo", ("HELADA", "NEVADA", "GRANIZ", "FRIAJE", "BAJAS TEMPERATURAS", "DESCENSO DE TEMPERATURA")),
    ("sismo", "Sismos", ("SISMO", "TSUNAMI")),
    ("deficit", "Déficit hídrico (sequía)", ("DEFICIT", "SEQUIA")),
    ("otros", "Otros", ()),
]
CAT_NOMBRE = {k: n for k, n, _ in CATEGORIAS}


def nombre_evento(evento):
    """Algunos títulos traen delante "(Reporte N°. 2)": se quita para que el mismo evento no quede partido en dos."""
    return re.sub(r"^[\s–-]*\(\s*Reporte\s+N[^)]*\)\s*", "", evento or "", flags=re.I).strip()


def categoria(evento):
    e = geo.norm(evento or "")
    for k, _, palabras in CATEGORIAS:
        if not palabras or any(p in e for p in palabras):
            return k
    return "otros"


def normalizar(tot):
    """Vías en metros → kilómetros (se suman a los campos _km). Lo demás queda igual."""
    if not tot:
        return tot
    out = dict(tot)
    for m, km in (("vias_afectadas_m", "vias_afectadas_km"), ("vias_destruidas_m", "vias_destruidas_km")):
        if m in out:
            out[km] = round(out.get(km, 0) + out.pop(m) / 1000, 3)
    return out


def _reparto(rec, fallback_reg, fallback_prov):
    """Reparte las cifras por región y provincia usando las filas de subtotal DPTO./PROV. de la tabla, si existen."""
    from . import indeci_danos
    filas = (rec.get("danos") or {}).get("filas") or []
    por_reg, por_prov, dep_name = {}, {}, None
    for f in filas:
        loc = (f.get("ubicacion") or "").strip()
        head = geo.norm(loc.split(" ")[0]) if loc else ""
        name = loc.split(" ", 1)[1].strip() if " " in loc else ""
        canon = {}
        for label, v in f["valores"].items():
            field = indeci_danos.classify(label)
            if field:
                canon[field] = canon.get(field, 0) + v
        if head in ("DPTO.", "DEP.", "DPTO", "DEP", "DEPARTAMENTO") and name:
            dep_name = name
            code = geo.region_code(name)
            if code:
                por_reg[code] = canon
        elif head in ("PROV.", "PROV", "PROVINCIA") and name:
            code = geo.province_code(dep_name or "", name)
            if code:
                por_prov[code] = canon
    tot = (rec.get("danos") or {}).get("totales") or {}
    if not por_reg and fallback_reg:
        por_reg = {fallback_reg: tot}
    if not por_prov and fallback_prov:
        por_prov = {fallback_prov: tot}
    return por_reg, por_prov


def _dia(ts):
    return datetime.date.fromtimestamp(ts).isoformat() if ts else None


def _dist(a, b):
    return abs((datetime.date.fromisoformat(a) - datetime.date.fromisoformat(b)).days)


def eventos():
    """Todos los eventos de los reportes INDECI guardados, cada uno con su último reporte con cifras."""
    from .snapshot import _fecha_ocurrencia, _hora_ocurrencia
    pdfs = {r["_key"]: r for r in db.get_items("indeci", "reporte_pdf", current_only=False)}
    reps = [i for i in db.get_items("indeci", "item", current_only=False, limit=1_000_000) if i.get("clase") == "reporte"]
    reps.sort(key=lambda i: i.get("ts") or 0)       # del más viejo al más nuevo: los eventos se forman en orden

    grupos, por_sinpad, por_lugar = [], {}, collections.defaultdict(list)   # lugar → [grupo] (con fecha de ocurrencia)

    def nuevo(clave, lugar, oc):
        g = {"clave": clave, "lugar": lugar, "oc": oc, "reps": []}
        grupos.append(g)
        if oc:
            por_lugar[lugar].append(g)
        return g

    for it in reps:
        rec = pdfs.get(it["_key"]) or {}
        lugar = "|".join(geo.norm(v) for v in (nombre_evento(it.get("evento")), it.get("distrito"), it.get("dpto")))
        oc = _fecha_ocurrencia(rec) or _hora_ocurrencia(it.get("descripcion"))[0]   # sin PDF: la fecha que trae el feed
        sinpad = rec.get("sinpad")
        g = None
        if sinpad:
            g = por_sinpad.get(sinpad)
            if g is None:
                # el mismo evento pudo llegar antes sin código: se une a ese grupo
                g = next((x for x in por_lugar[lugar] if oc and x["clave"].startswith("lugar:") and _dist(x["oc"], oc) <= EVENTO_GAP_DIAS), None) or nuevo(f"sinpad:{sinpad}", lugar, oc)
                g["clave"] = f"sinpad:{sinpad}"
                por_sinpad[sinpad] = g
        elif oc:
            g = min((x for x in por_lugar[lugar] if _dist(x["oc"], oc) <= EVENTO_GAP_DIAS), key=lambda x: _dist(x["oc"], oc), default=None) \
                or nuevo(f"lugar:{lugar}|{oc}", lugar, oc)
        else:
            dia = _dia(it.get("ts")) or "1970-01-01"
            previos = [x for x in por_lugar[lugar] if x["oc"] <= dia and _dist(x["oc"], dia) <= SIN_FECHA_DIAS]
            g = max(previos, key=lambda x: x["oc"], default=None) or nuevo(f"lugar:{lugar}|r{dia}", lugar, None)
        if not g["oc"] and oc:
            g["oc"] = oc
            por_lugar[lugar].append(g)
        g["reps"].append((it, rec))

    out = []
    for g in grupos:
        reps_g = g["reps"]
        it_ult = reps_g[-1][0]
        con = [(it, rec) for it, rec in reps_g if (rec.get("danos") or {}).get("totales")]
        it, rec = con[-1] if con else reps_g[-1]
        reg = (it["prov"][:2] if it.get("prov") else None) or geo.region_code(re.split(r"\s*[–-]\s*", it.get("dpto") or "")[-1])
        if con:
            p_reg, p_prov = _reparto(rec, reg, it.get("prov"))
            p_reg = {k: normalizar(v) for k, v in p_reg.items()}
            p_prov = {k: normalizar(v) for k, v in p_prov.items()}
        else:
            p_reg, p_prov = ({reg: {}} if reg else {}), ({it["prov"]: {}} if it.get("prov") else {})
        out.append({
            "evento_key": g["clave"], "item_key": it["_key"], "ts": it.get("ts") or 0, "sinpad": rec.get("sinpad"),
            "evento": nombre_evento(it.get("evento")), "categoria": categoria(nombre_evento(it.get("evento"))), "distrito": it.get("distrito"),
            "provincia": it.get("provincia"), "dpto": it.get("dpto"), "prov": it.get("prov"), "reg": reg,
            "tipo": it.get("tipo"), "num": it.get("num"), "seq": it.get("seq"), "link": it.get("link"),
            "ocurrencia": g["oc"], "fecha": g["oc"] or _dia(reps_g[0][0].get("ts")), "fecha_estimada": not g["oc"],
            "n_reportes": len(reps_g), "primer_ts": reps_g[0][0].get("ts") or 0, "ultimo_ts": it_ult.get("ts") or 0,
            "cifras": bool(con), "totales": normalizar((rec.get("danos") or {}).get("totales") or {}),
            "actualizado": rec.get("danos_actualizado") if con else None,
            "por_reg": p_reg, "por_prov": p_prov, "multi_region": len(p_reg) > 1,
        })
    return out


def resumen(days=7, unidades=True):
    """Panel del tablero: eventos con cifras actualizadas en los últimos `days` días (cada uno una vez, con su último
    reporte con cifras). Separa los ocurridos en la ventana de los anteriores que siguen reportando."""
    from . import indeci_danos
    cutoff = time.time() - days * 86400
    since_date = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    evs = []
    for e in eventos():
        if not e["cifras"] or e["ts"] < cutoff:
            continue
        e = {**e, "nuevo": e["ocurrencia"] is None or e["ocurrencia"] >= since_date}
        if not unidades:        # API de integración: las cifras tal como vienen del reporte (vías en m y en km)
            rec = db.get_item("indeci", "reporte_pdf", e["item_key"]) or {}
            e["totales"] = (rec.get("danos") or {}).get("totales") or {}
            e["por_reg"], e["por_prov"] = _reparto(rec, e["reg"], e["prov"])
        evs.append(e)
    evs.sort(key=lambda e: e["ts"], reverse=True)

    def add(acc, tot):
        for f, v in tot.items():
            acc[f] = acc.get(f, 0) + v

    def blank():
        return {"totales": {}, "nuevos": {}, "seguimiento": {}, "n_eventos": 0, "n_nuevos": 0}

    out, por_reg, por_prov = blank(), {}, {}
    for e in evs:
        tipo = "nuevos" if e["nuevo"] else "seguimiento"
        add(out["totales"], e["totales"]); add(out[tipo], e["totales"])
        out["n_eventos"] += 1; out["n_nuevos"] += e["nuevo"]
        for bucket, parts in ((por_reg, e["por_reg"]), (por_prov, e["por_prov"])):
            for code, tot in parts.items():
                b = bucket.setdefault(code, blank())
                add(b["totales"], tot); add(b[tipo], tot)
                b["n_eventos"] += 1; b["n_nuevos"] += e["nuevo"]
    orden = [k for k in indeci_danos.ORDER if not (unidades and k.endswith("_m") and k.startswith("vias_"))]
    return {"dias": days, "desde": since_date, "eventos": evs, **out, "por_reg": por_reg, "por_prov": por_prov,
            "labels": indeci_danos.LABELS, "orden": orden}


_cache = {"at": 0, "data": None}


def historial():
    """Para /danos: todos los eventos (compactos) desde el primer reporte guardado, por fecha de ocurrencia. El
    navegador arma las semanas y filtra por lugar, tipo y cifra."""
    if _cache["data"] and time.time() - _cache["at"] < 300:
        return _cache["data"]
    from . import indeci_danos
    evs = eventos()
    desde = min((_dia(e["primer_ts"]) for e in evs), default=None)
    campos = ("item_key", "evento", "categoria", "distrito", "provincia", "dpto", "reg", "prov", "fecha", "n_reportes", "cifras", "totales")   # item_key es la URL del reporte en INDECI

    def compacto(e):
        c = {k: e[k] for k in campos if e[k] not in (None, "", {})}
        if e["fecha_estimada"]:
            c["fecha_estimada"] = True
        # el reparto solo hace falta si el evento abarca varias regiones o provincias; si no, vale reg/prov + totales
        if len(e["por_reg"]) > 1:
            c["por_reg"] = e["por_reg"]
        if len(e["por_prov"]) > 1:
            c["por_prov"] = e["por_prov"]
        return c

    data = {
        "desde": desde, "hasta": datetime.date.today().isoformat(), "generado": time.time(),
        "eventos": [compacto(e) for e in evs],
        "categorias": [[k, n] for k, n, _ in CATEGORIAS],
        "labels": indeci_danos.LABELS,
        "demora": _demora([e for e in evs if desde and (e["ocurrencia"] or "") >= desde]),
    }
    _cache.update(at=time.time(), data=data)
    return data


def _demora(evs):
    """Cuántos días después de ocurrido llega la última cifra de un evento (mediana y percentil 80): dice qué semanas
    recientes todavía pueden crecer."""
    d = sorted(max(0, (datetime.date.fromtimestamp(e["ts"]) - datetime.date.fromisoformat(e["ocurrencia"])).days)
               for e in evs if e["cifras"] and e["ocurrencia"] and e["ts"])
    if not d:
        return {"n": 0, "mediana": None, "p80": None, "p90": None}
    return {"n": len(d), "mediana": d[len(d) // 2], "p80": d[int(len(d) * .8)], "p90": d[int(len(d) * .9)]}
