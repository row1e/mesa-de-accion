"""Feed "Últimos datos recibidos": registros nuevos de todas las fuentes en una forma común, del más reciente al más viejo.

Cada fila: {source, kind, key, received, event_time, title, subtitle, level, place, reg|regs, detail, group?}
- received   = cuándo lo recibió el colector (first_seen)
- event_time = fecha que le da la fuente (ocurrencia, emisión, publicación)
Las fuentes masivas (focos, detecciones FIRMS) se agrupan por lectura cuando no se filtra por esa fuente.
Las fuentes que se actualizan en el mismo registro (UV, pronóstico, SIDPOL) aparecen como "actualización"
cada vez que su contenido cambió (tabla fetches, changed=1).
"""
import datetime
import json
import re

from . import db, geo
from .sources import SOURCES

LEVEL = {"AMARILLO": 2, "NARANJA": 3, "ROJO": 4}
BULK = {("serfor", "foco"): ("foco de calor nuevo", "focos de calor nuevos"),
        ("firms", "deteccion"): ("detección satelital nueva", "detecciones satelitales nuevas")}
FEED_KINDS = [("senamhi_avisos", "aviso"), ("senamhi_hidro", "aviso_estacion"), ("indeci", "item"), ("igp", "sismo"),
              ("serfor", "alerta"), ("serfor", "foco"), ("ingemmet", "zona_alerta"), ("enfen", "comunicado"),
              ("com_pnp", "noticia"), ("com_provias", "noticia"), ("com_mtc", "noticia"), ("com_mininter", "noticia"),
              ("firms", "deteccion")]
# expresión SQL con la fecha propia de cada tipo: desempata registros recibidos en el mismo lote (p. ej. la carga inicial)
EVENT_SQL = {"aviso": "json_extract(payload,'$.emision') || printf('%05d', json_extract(payload,'$.nro'))",
             "aviso_estacion": "json_extract(payload,'$.fecha_hora')", "item": "json_extract(payload,'$.ts')",
             "sismo": "json_extract(payload,'$.fecha') || json_extract(payload,'$.hora')",
             "alerta": "json_extract(payload,'$.fecha') || json_extract(payload,'$.hora')",
             "foco": "json_extract(payload,'$.fecha') || json_extract(payload,'$.hora')",
             "comunicado": "key", "noticia": "json_extract(payload,'$.fecha')",
             "deteccion": "json_extract(payload,'$.fecha') || json_extract(payload,'$.hora')", "zona_alerta": "key"}
UPDATES = {"senamhi_uv": ("prono_ruv.json", "Índice UV actualizado"),
           "senamhi_pronostico": ("pronostico.html", "Pronóstico por ciudad actualizado"),
           "sidpol": ("denuncias.csv", "Nuevo mes de denuncias SIDPOL publicado")}


def _t(s):
    return (s or "").title()


def _aviso_regs(it):
    rows = db.conn().execute("SELECT payload FROM items WHERE source='senamhi_avisos' AND kind='aviso_dia' AND key LIKE ?",
                             (f"{it['anio']}-{it['nro']}-%",))
    regs = {p[:2] for r in rows for p in json.loads(r["payload"]).get("levels", {})}
    return sorted(regs)


def summarize(source, kind, it):
    """Forma común de un registro para el feed."""
    rc = geo.region_code
    base = {"source": source, "kind": kind, "key": it["_key"], "received": it["_first_seen"], "updated": it["_last_seen"],
            "current": bool(it["_current"]), "detail": True, "level": None, "reg": None, "event_time": None, "place": None}
    if kind == "aviso":
        return base | {"title": f"Aviso N° {it['nro']} · {_t(it['titulo'])}", "subtitle": f"{it['estado']} · {it['inicio']} → {it['fin']}",
                       "level": LEVEL.get(it["nivel"]), "event_time": it.get("emision"), "regs": _aviso_regs(it)}
    if kind == "aviso_estacion":
        return base | {"title": _t(it.get("titulo")), "subtitle": f"Estación {_t(it.get('nom_estacion'))} · cuenca {_t(it.get('nom_cuenca'))}",
                       "level": LEVEL.get(it.get("color_text")), "event_time": it.get("fecha_hora"),
                       "place": f"{_t(it.get('nom_distrito'))}, {_t(it.get('nom_provincia'))}", "reg": rc(it.get("nom_departamento"))}
    if kind == "item":
        rep = it.get("clase") == "reporte"
        rec = db.get_item("indeci", "reporte_pdf", it["_key"]) if rep else None
        base["danos"] = ((rec or {}).get("danos") or {}).get("totales") or None
        return base | {"title": f"{_t(it.get('evento'))} — {_t(it.get('distrito'))}" if rep else it.get("titulo"),
                       "subtitle": f"{_t(it.get('tipo'))} N° {it.get('num')}" + (f" · reporte {it['seq']}" if it.get("seq") else "") if rep else "Boletín",
                       "event_time": datetime.datetime.fromtimestamp(it["ts"]).isoformat(timespec="minutes") if it.get("ts") else it.get("pub"),
                       "place": f"{_t(it.get('provincia'))} · {_t(it.get('dpto'))}" if rep else None,
                       "reg": (it["prov"][:2] if it.get("prov") else None) or (rc(re.split(r"\s*[–-]\s*", it.get("dpto") or "")[-1]) if rep else None)}
    if kind == "sismo":
        return base | {"title": f"Sismo M{it['mag']} · prof. {it['prof']} km", "subtitle": it["ref"], "event_time": f"{it['fecha']}T{it['hora']}",
                       "level": 4 if it["mag"] >= 6 else 3 if it["mag"] >= 5 else None,
                       "reg": geo.region_of_point(it["lon"], it["lat"]) or (geo.regions_mentioned(it["ref"].rsplit(",", 1)[-1]) or [None])[-1]}
    if kind == "alerta":
        return base | {"title": f"Incendio forestal · {it['estado']}", "subtitle": f"Reporte {it.get('cod') or '—'} · {it.get('cob') or ''}",
                       "level": {"Alertado": 2, "Confirmado": 3}.get(it["estado"]), "event_time": f"{it.get('fecha') or ''} {it.get('hora') or ''}".strip(),
                       "place": f"{_t(it['dist'])}, {_t(it['prov'])}", "reg": str(it.get("ubigeo") or "").zfill(6)[:2] or rc(it.get("dep"))}
    if kind == "foco":
        return base | {"title": "Foco de calor", "subtitle": f"Sensor {it.get('sensor') or '—'}", "event_time": f"{it.get('fecha') or ''} {it.get('hora') or ''}".strip(),
                       "place": f"{_t(it['prov'])} · {_t(it['dep'])}", "reg": str(it.get("ubigeo") or "").zfill(6)[:2] or rc(it.get("dep"))}
    if kind == "zona_alerta":
        return base | {"title": f"Zona crítica en alerta · {it.get('paraje') or '—'}", "subtitle": f"{it.get('peligros_g')} · aviso #{it.get('nro_aviso')}",
                       "level": int(str(it.get("nivel", "Nivel 1")).split()[-1]), "place": f"{it.get('distrito')}, {it.get('provincia')}", "reg": rc(it.get("region"))}
    if kind == "comunicado":
        return base | {"title": f"Comunicado ENFEN N° {it['numero']}-{it['anio']}: {it.get('estado') or '—'}", "subtitle": "Estado del sistema de alerta ante El Niño",
                       "event_time": it.get("fecha"), "place": "Nacional"}
    if kind == "noticia":
        return base | {"title": it["titulo"], "subtitle": it["inst_nombre"], "event_time": it.get("fecha"), "imagen": it.get("imagen"),
                       "regs": geo.regions_mentioned(f"{it.get('titulo', '')} {it.get('descripcion', '')}")}
    if kind == "deteccion":
        return base | {"title": "Detección VIIRS (NASA FIRMS)", "subtitle": f"Confianza {it.get('conf')} · FRP {it.get('frp')} MW",
                       "event_time": f"{it['fecha']} {it['hora'][:2]}:{it['hora'][2:]} UTC", "reg": geo.region_of_point(it["lon"], it["lat"])}
    return base | {"title": it["_key"], "subtitle": kind}


BACKFILL_WINDOW = 3600   # lo recibido en la primera hora de una fuente es su carga histórica


def _first_batch(source, kind):
    r = db.conn().execute("SELECT MIN(first_seen) FROM items WHERE source=? AND kind=?", (source, kind)).fetchone()[0]
    return r or 0


def _in_region(row, region):
    return not region or row.get("reg") == region or region in (row.get("regs") or [])


def feed(source=None, region=None, limit=60, before=None, days=7):
    """Filas más recientes. `before` = received del último elemento de la página anterior (paginación)."""
    import time
    since = time.time() - days * 86400
    kinds = [(s, k) for s, k in FEED_KINDS if not source or s == source]
    rows = []
    for s, k in kinds:
        q = "SELECT key, first_seen, last_seen, current, payload FROM items WHERE source=? AND kind=? AND first_seen>=?"
        args = [s, k, since]
        if before:
            q += " AND first_seen<?"
            args.append(before)
        cap = 5000 if (s, k) in BULK and not source else limit * 6
        order = f"first_seen DESC, {EVENT_SQL.get(k, 'key')} DESC"
        first = _first_batch(s, k)
        for r in db.conn().execute(q + f" ORDER BY {order} LIMIT ?", (*args, cap)):
            it = {**json.loads(r["payload"]), "_key": r["key"], "_first_seen": r["first_seen"], "_last_seen": r["last_seen"], "_current": r["current"]}
            row = summarize(s, k, it)
            row["backfill"] = r["first_seen"] < first + BACKFILL_WINDOW   # llegó en la carga inicial de la fuente (histórico)
            if _in_region(row, region):
                rows.append(row)

    # agrupar masivos por lectura cuando no se filtra por esa fuente
    if not source:
        grouped, out = {}, []
        for r in rows:
            label = BULK.get((r["source"], r["kind"]))
            if not label:
                out.append(r)
                continue
            bucket = (r["source"], int(r["received"] // 120))
            g = grouped.get(bucket)
            if not g:
                g = grouped[bucket] = {"source": r["source"], "kind": r["kind"], "key": f"grupo-{bucket[1]}", "received": r["received"],
                                       "group": 0, "title": "", "subtitle": "", "level": None, "detail": False, "places": {}}
                out.append(g)
            g["group"] += 1
            g["places"][r.get("place") or "—"] = g["places"].get(r.get("place") or "—", 0) + 1
        for g in grouped.values():
            one, many = BULK[(g["source"], g["kind"])]
            g["title"] = f"{g['group']:,} {one if g['group'] == 1 else many}"
            top = sorted(g.pop("places").items(), key=lambda x: -x[1])[:3]
            g["subtitle"] = "Más en: " + ", ".join(f"{p} ({n})" for p, n in top)
        rows = out

    # actualizaciones de fuentes que se reescriben en el mismo registro (sin región: son nacionales)
    if not region:
        for s, (name, label) in UPDATES.items():
            if source and s != source:
                continue
            q = "SELECT started_at, path FROM fetches WHERE source=? AND changed=1 AND path LIKE ? AND started_at>=?"
            args = [s, f"%{name}", since]
            if before:
                q += " AND started_at<?"
                args.append(before)
            for f in db.conn().execute(q + " ORDER BY started_at DESC LIMIT 20", args):
                rows.append({"source": s, "kind": "actualizacion", "key": f"upd-{f['started_at']}", "received": f["started_at"],
                             "title": label, "subtitle": "La fuente publicó contenido nuevo", "level": None, "detail": False,
                             "place": "Nacional", "event_time": None})

    rows.sort(key=lambda r: (r["received"], r.get("event_time") or ""), reverse=True)
    page = rows[:limit]
    return {"rows": page, "more": len(rows) > limit, "next_before": page[-1]["received"] if len(rows) > limit and page else None,
            "sources": {sid: {"org": m["org"], "name": m["name"]} for sid, m in SOURCES.items()}}


def counts(region=None, hours=24):
    """Registros nuevos por fuente en las últimas `hours` horas (para los filtros)."""
    import time
    since = time.time() - hours * 3600
    out = {}
    for s, k in FEED_KINDS:
        since_k = max(since, _first_batch(s, k) + BACKFILL_WINDOW)   # la carga inicial no cuenta como "nuevo"
        if region:
            n = sum(1 for r in db.conn().execute("SELECT key, first_seen, last_seen, current, payload FROM items WHERE source=? AND kind=? AND first_seen>=?", (s, k, since_k))
                    if _in_region(summarize(s, k, {**json.loads(r["payload"]), "_key": r["key"], "_first_seen": r["first_seen"],
                                                   "_last_seen": r["last_seen"], "_current": r["current"]}), region))
        else:
            n = db.conn().execute("SELECT COUNT(*) FROM items WHERE source=? AND kind=? AND first_seen>=?", (s, k, since_k)).fetchone()[0]
        out[s] = out.get(s, 0) + n
    if not region:
        for s, (name, _) in UPDATES.items():
            out[s] = out.get(s, 0) + db.conn().execute("SELECT COUNT(*) FROM fetches WHERE source=? AND changed=1 AND path LIKE ? AND started_at>=?",
                                                       (s, f"%{name}", since)).fetchone()[0]
    return out
