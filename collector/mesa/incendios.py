"""Historial de incendios: focos de calor y alertas de SERFOR, para la página /incendios.

SERFOR les cambia el OBJECTID a sus registros en cada actualización, así que la misma detección quedaba guardada una y
otra vez. Ahora cada registro se identifica por lo que es (lugar, fecha, hora y sensor o código de reporte) y su
OBJECTID va en "oid". migrar() convierte lo ya guardado la primera vez que corre esta versión.

Para sumar el historial de otra Mesa (una vez, a mano):
  python -m mesa.incendios exportar ruta/a/mesa.sqlite3 historial.jsonl.gz     en la Mesa que lo tiene
  python -m mesa.incendios importar historial.jsonl.gz                         en la que lo recibe
"""
import gzip
import json
import logging
import sqlite3
import sys
import threading
import time

from . import db, geo

log = logging.getLogger("mesa.incendios")
GRUPOS_SENSOR = {"N": "VIIRS", "N20": "VIIRS", "N21": "VIIRS", "A": "MODIS", "T": "MODIS"}   # el resto: GOES
_cache = {"en": 0, "datos": None}
_lock = threading.Lock()


def clave_foco(r):
    return f"{r['lat']},{r['lon']},{r.get('fecha')},{r.get('hora')},{r.get('sensor')}"


def clave_alerta(r):
    return f"{r['lat']},{r['lon']},{r.get('fecha')},{r.get('hora')},{r.get('cod')}"


CLAVES = {"foco": clave_foco, "alerta": clave_alerta}


def _unir(a, b):
    """Dos copias de un mismo registro: la fecha de llegada más antigua, y el contenido de la vista más reciente."""
    reciente = a if a["last_seen"] >= b["last_seen"] else b
    return {"first_seen": min(a["first_seen"], b["first_seen"]), "last_seen": reciente["last_seen"],
            "current": max(a["current"], b["current"]), "payload": reciente["payload"]}


def migrar():
    """Pasa los registros de SERFOR guardados por OBJECTID a su clave nueva, sin repetidos. Si no queda nada por pasar,
    no hace nada. Devuelve cuántas filas quitó."""
    c = db.conn()
    total = 0
    for kind, clave in CLAVES.items():
        viejas = c.execute("SELECT key, first_seen, last_seen, current, payload FROM items "
                           "WHERE source='serfor' AND kind=? AND key NOT LIKE '%,%'", (kind,)).fetchall()
        if not viejas:
            continue
        unidas = {}
        for r in viejas:
            d = json.loads(r["payload"])
            d.setdefault("oid", int(r["key"]))
            k = clave(d)
            fila = {"first_seen": r["first_seen"], "last_seen": r["last_seen"], "current": r["current"], "payload": d}
            unidas[k] = _unir(unidas[k], fila) if k in unidas else fila
        with db.tx() as t:
            for r in t.execute("SELECT key, first_seen, last_seen, current, payload FROM items "
                               "WHERE source='serfor' AND kind=? AND key LIKE '%,%'", (kind,)).fetchall():
                if r["key"] in unidas:
                    fila = {"first_seen": r["first_seen"], "last_seen": r["last_seen"], "current": r["current"], "payload": json.loads(r["payload"])}
                    unidas[r["key"]] = _unir(unidas[r["key"]], fila)
            t.execute("DELETE FROM items WHERE source='serfor' AND kind=? AND key NOT LIKE '%,%'", (kind,))
            t.executemany("INSERT OR REPLACE INTO items(source,kind,key,first_seen,last_seen,current,payload) VALUES('serfor',?,?,?,?,?,?)",
                          [(kind, k, f["first_seen"], f["last_seen"], f["current"], json.dumps(f["payload"], ensure_ascii=False, separators=(",", ":")))
                           for k, f in unidas.items()])
        log.info("SERFOR %s: %d filas guardadas → %d registros únicos", kind, len(viejas), len(unidas))
        total += len(viejas) - len(unidas)
    if total > 10000:
        try:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.execute("VACUUM")                  # devuelve al disco el espacio de las filas quitadas
        except sqlite3.Error as e:
            log.warning("No se pudo compactar la base: %s", e)
    return total


def exportar(ruta_db, salida):
    """Escribe los focos y alertas de SERFOR y las detecciones de NASA de otra base, sin repetidos y sin tocarla."""
    c = sqlite3.connect(f"file:{ruta_db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    filas = {}
    for r in c.execute("SELECT source, kind, key, first_seen, last_seen, payload FROM items "
                       "WHERE (source='serfor' AND kind IN ('foco','alerta')) OR (source='firms' AND kind='deteccion')"):
        d = json.loads(r["payload"])
        if r["source"] == "serfor":
            if "," not in r["key"]:
                d.setdefault("oid", int(r["key"]))
            k = CLAVES[r["kind"]](d)
        else:
            k = r["key"]
        fila = {"source": r["source"], "kind": r["kind"], "key": k, "first_seen": r["first_seen"], "last_seen": r["last_seen"], "current": 0, "payload": d}
        id_ = (r["source"], r["kind"], k)
        filas[id_] = {**fila, **_unir(filas[id_], fila)} if id_ in filas else fila
    with gzip.open(salida, "wt", encoding="utf-8") as f:
        for fila in filas.values():
            f.write(json.dumps(fila, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(filas)


def importar(ruta):
    """Suma el archivo de exportar() a esta base. Se puede repetir: lo que ya está no se duplica y sigue vigente si lo
    estaba; solo se adelanta su fecha de llegada si el archivo la tiene más antigua."""
    migrar()
    nuevos = existentes = 0
    with gzip.open(ruta, "rt", encoding="utf-8") as f, db.tx() as t:
        for ln in f:
            r = json.loads(ln)
            if t.execute("SELECT 1 FROM items WHERE source=? AND kind=? AND key=?", (r["source"], r["kind"], r["key"])).fetchone():
                t.execute("UPDATE items SET first_seen=MIN(first_seen,?) WHERE source=? AND kind=? AND key=?",
                          (r["first_seen"], r["source"], r["kind"], r["key"]))
                existentes += 1
            else:
                t.execute("INSERT INTO items(source,kind,key,first_seen,last_seen,current,payload) VALUES(?,?,?,?,?,0,?)",
                          (r["source"], r["kind"], r["key"], r["first_seen"], r["last_seen"], json.dumps(r["payload"], ensure_ascii=False, separators=(",", ":"))))
                nuevos += 1
    return nuevos, existentes


def _alertas(filas):
    """Una entrada por reporte de SERFOR (código PIF): primera y última fecha, estado más reciente y detecciones."""
    g = {}
    for a in filas:
        if not a.get("fecha") or (a.get("cob") or "").strip() == "Simulacro":
            continue
        g.setdefault(a.get("cod") or a["_key"], []).append(a)
    out = []
    for cod, xs in g.items():
        xs.sort(key=lambda a: (a["fecha"], a.get("hora") or ""))
        u = xs[-1]
        estado = next((x["estado"] for x in reversed(xs) if x.get("estado") in ("Alertado", "Confirmado", "Controlado", "Extinguido")), None)
        out.append({"cod": cod, "ini": xs[0]["fecha"], "fin": u["fecha"], "estado": estado, "n": len(xs), "lat": xs[0]["lat"], "lon": xs[0]["lon"],
                    "ub": str(u.get("ubigeo") or ""), "dist": u.get("dist"), "prov": u.get("prov"), "dep": u.get("dep"), "cob": (u.get("cob") or "").strip() or None})
    out.sort(key=lambda a: a["ini"])
    return out


def historial():
    """Datos de la página /incendios, en columnas para que pesen poco. Se recalcula a lo sumo cada 10 minutos."""
    with _lock:
        if _cache["datos"] and time.time() - _cache["en"] < 600:
            return _cache["datos"]
        vistos, sens, lon, lat, dia, sen, ub = set(), [], [], [], [], [], []
        focos = db.get_items("serfor", "foco", current_only=False, order="json_extract(payload,'$.fecha')")
        fechas = sorted({f["fecha"] for f in focos if f.get("fecha")})
        i_dia = {d: i for i, d in enumerate(fechas)}
        for f in focos:
            k = clave_foco(f)
            if k in vistos or not f.get("fecha"):
                continue
            vistos.add(k)
            s = f.get("sensor") or "?"
            if s not in sens:
                sens.append(s)
            lon.append(f["lon"]); lat.append(f["lat"]); dia.append(i_dia[f["fecha"]]); sen.append(sens.index(s)); ub.append(str(f.get("ubigeo") or "")[:4])  # noqa: E702
        deps, provs = geo.boundaries()
        _cache["datos"] = {"generado": time.time(), "dias": fechas, "sensores": [{"id": s, "grupo": GRUPOS_SENSOR.get(s, "GOES")} for s in sens],
                           "focos": {"lon": lon, "lat": lat, "d": dia, "s": sen, "u": ub},
                           "alertas": _alertas(db.get_items("serfor", "alerta", current_only=False)), "deps": deps, "provs": provs}
        _cache["en"] = time.time()
        return _cache["datos"]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) == 4 and sys.argv[1] == "exportar":
        print(f"{exportar(sys.argv[2], sys.argv[3])} registros exportados a {sys.argv[3]}")
    elif len(sys.argv) == 3 and sys.argv[1] == "importar":
        print("%d registros nuevos, %d que ya estaban" % importar(sys.argv[2]))
    else:
        sys.exit(__doc__)
