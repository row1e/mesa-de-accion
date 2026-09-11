"""Arma el objeto que consume el dashboard a partir de la base (misma forma que dashboard/build.py)."""
import collections
import datetime
import re
import threading
import time
from zoneinfo import ZoneInfo

from . import config, db, geo, runner
from .sources import SOURCES

LIMA = ZoneInfo("America/Lima")
INDECI_WINDOW_H = 24
_cache = {"at": 0, "data": None}
_lock = threading.Lock()


def invalidate():
    _cache["data"] = None


runner.on_run(invalidate)


def health():
    nxt = runner.next_runs()
    now = time.time()
    rows = {r["source"]: r for r in db.health_all()}
    stored = {r["source"]: r["n"] for r in db.conn().execute(
        "SELECT source, COUNT(*) n FROM items WHERE current=1 GROUP BY source")}
    out = []
    for sid, meta in SOURCES.items():
        h = rows.get(sid, {})
        interval = config.INTERVALS[sid]
        last_ok, fails = h.get("last_ok"), h.get("consecutive_failures") or 0
        if h.get("running"):
            status = "actualizando"
        elif not h.get("last_run"):
            status = "pendiente"
        elif fails and not last_ok:
            status = "sin_acceso" if sid == "provias" else "fallando"
        elif fails:
            status = "fallando"
        elif last_ok and now - last_ok > interval * config.STALE_FACTOR:
            status = "atrasada"
        else:
            status = "ok"
        out.append({"id": sid, **{k: meta[k] for k in ("org", "name", "via", "geo", "sheet")}, "status": status,
                    "interval_s": interval, "last_run": h.get("last_run"), "last_ok": last_ok,
                    "last_error": h.get("last_error"), "message": h.get("last_message"), "failures": fails,
                    "items": stored.get(sid), "last_run_items": h.get("items"), "next_run": nxt.get(sid)})
    return out


def _today():
    return datetime.datetime.now(LIMA).date().isoformat()


DANOS_DAYS = 7


def _event_key(it, rec):
    """Un mismo evento tiene varios reportes (N° 1, 2, 3…) con cifras acumuladas: se identifica por SINPAD o por evento+lugar."""
    if rec.get("sinpad"):
        return f"sinpad:{rec['sinpad']}"
    return "lugar:" + "|".join(geo.norm(it.get(k)) for k in ("evento", "distrito", "dpto"))


def _fecha_ocurrencia(rec):
    """Primera fecha dd/mm/aaaa de la sección HECHOS ("Fecha y hora de la ocurrencia")."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", rec.get("hechos") or "")
    if not m:
        return None
    try:
        return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None


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


def danos_resumen(days=DANOS_DAYS):
    """Daños de los reportes INDECI de los últimos `days` días, sin duplicar eventos (cuenta el reporte más reciente de cada uno).

    Separa eventos NUEVOS (ocurridos en la ventana) de eventos ANTERIORES aún en seguimiento (p. ej. un sismo de julio que sigue
    recibiendo reportes). Reparte por región/provincia con las filas DPTO./PROV. de la tabla cuando el evento abarca varias."""
    from . import indeci_danos
    now = time.time()
    cutoff = now - days * 86400
    since_date = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    pdfs = {r["_key"]: r for r in db.get_items("indeci", "reporte_pdf", current_only=False)}
    latest = {}
    for it in db.get_items("indeci", "item", current_only=False, limit=5000):
        if it.get("clase") != "reporte" or (it.get("ts") or 0) < cutoff:
            continue
        rec = pdfs.get(it["_key"]) or {}
        tot = (rec.get("danos") or {}).get("totales")
        if not tot:
            continue
        k = _event_key(it, rec)
        if k in latest and (it.get("ts") or 0) <= latest[k]["ts"]:
            continue
        reg = (it["prov"][:2] if it.get("prov") else None) or geo.region_code(re.split(r"\s*[–-]\s*", it.get("dpto") or "")[-1])
        ocurr = _fecha_ocurrencia(rec)
        rep_reg, rep_prov = _reparto(rec, reg, it.get("prov"))
        latest[k] = {"evento_key": k, "item_key": it["_key"], "ts": it.get("ts") or 0, "sinpad": rec.get("sinpad"),
                     "evento": it.get("evento"), "distrito": it.get("distrito"), "provincia": it.get("provincia"),
                     "dpto": it.get("dpto"), "prov": it.get("prov"), "reg": reg, "tipo": it.get("tipo"), "num": it.get("num"),
                     "seq": it.get("seq"), "totales": tot, "actualizado": rec.get("danos_actualizado"), "link": it.get("link"),
                     "ocurrencia": ocurr, "nuevo": (ocurr is None or ocurr >= since_date), "por_reg": rep_reg, "por_prov": rep_prov,
                     "multi_region": len(rep_reg) > 1}
    eventos = sorted(latest.values(), key=lambda e: e["ts"], reverse=True)

    def add(acc, tot):
        for f, v in tot.items():
            acc[f] = acc.get(f, 0) + v

    def blank():
        return {"totales": {}, "nuevos": {}, "seguimiento": {}, "n_eventos": 0, "n_nuevos": 0}

    out, por_reg, por_prov = blank(), {}, {}
    for e in eventos:
        tipo = "nuevos" if e["nuevo"] else "seguimiento"
        for acc in (out,):
            add(acc["totales"], e["totales"]); add(acc[tipo], e["totales"])
            acc["n_eventos"] += 1; acc["n_nuevos"] += e["nuevo"]
        for bucket, parts in ((por_reg, e["por_reg"]), (por_prov, e["por_prov"])):
            for code, tot in parts.items():
                b = bucket.setdefault(code, blank())
                add(b["totales"], tot); add(b[tipo], tot)
                b["n_eventos"] += 1; b["n_nuevos"] += e["nuevo"]
    return {"dias": days, "desde": since_date, "eventos": eventos, **out, "por_reg": por_reg, "por_prov": por_prov,
            "labels": indeci_danos.LABELS, "orden": indeci_danos.ORDER}


def build():
    with _lock:
        if _cache["data"] is not None:
            return _cache["data"]
        t0 = time.time()
        today = _today()
        deps, provs = geo.boundaries()
        pinfo = geo.provinces()

        # SENAMHI avisos
        avisos = {f"{a['anio']}-{a['nro']}": a for a in db.get_items("senamhi_avisos", "aviso")}
        dias = db.get_items("senamhi_avisos", "aviso_dia")
        by_aviso = collections.defaultdict(list)
        for d in dias:
            by_aviso[f"{d['anio']}-{d['nro']}"].append(d)
        lo = (datetime.date.fromisoformat(today) - datetime.timedelta(days=2)).isoformat()
        levels = collections.defaultdict(dict)
        for d in dias:
            a = avisos.get(f"{d['anio']}-{d['nro']}")
            if not a or d["fecha"] < lo:
                continue
            for code, lvl in d["levels"].items():
                cell = levels[d["fecha"]].setdefault(code, {"m": 0, "a": []})
                cell["m"] = max(cell["m"], lvl)
                cell["a"].append([d["nro"], lvl])
        for cells in levels.values():
            for c in cells.values():
                c["a"].sort(reverse=True)
        vig = sorted((a for a in avisos.values() if a["estado"] in ("vigente", "emitido")), key=lambda a: (-a["anio"], -a["nro"]))
        avisos_out = [{**{k: a[k] for k in ("nro", "anio", "estado", "titulo", "emision", "inicio", "fin", "duracion", "nivel")},
                       "key": f"{a['anio']}-{a['nro']}", "link": a.get("link"),
                       "dias": sorted(d["fecha"] for d in by_aviso[f"{a['anio']}-{a['nro']}"]),
                       "dptos_texto": next((d["dptos_texto"] for d in sorted(by_aviso[f"{a['anio']}-{a['nro']}"], key=lambda x: x["mapa"])), [])}
                      for a in vig]
        hist = collections.Counter((str(a["anio"]), a["nivel"]) for a in avisos.values())
        years = sorted({str(a["anio"]) for a in avisos.values()})
        avisos_hist = {y: {n: hist[(y, n)] for n in ("AMARILLO", "NARANJA", "ROJO")} for y in years}

        # UV
        uv, uv_unmatched = {}, []
        for z in db.get_items("senamhi_uv", "uv_zona"):
            rec = {"z": z["zona"], "v": z["v"], "h": z["h"], "dias": z["dias"]}
            if z["code"] in pinfo:
                if z["code"] not in uv or z["v"][0] > uv[z["code"]]["v"][0]:
                    uv[z["code"]] = rec
            else:
                uv_unmatched.append(f"{z['code']} {z['zona']}")

        pron = [{"ciudad": c["ciudad"], "departamento": c["departamento"], **d}
                for c in db.get_items("senamhi_pronostico", "ciudad", order="key") for d in c["dias"]]
        hidro = [{k: v for k, v in h.items() if k != "raw"} for h in db.get_items("senamhi_hidro", "aviso_estacion")]

        # INDECI: ventana móvil
        cutoff = time.time() - INDECI_WINDOW_H * 3600
        indeci = [i for i in db.get_items("indeci", "item", current_only=False, limit=600) if (i.get("ts") or 0) >= cutoff]
        indeci.sort(key=lambda i: i.get("ts") or 0, reverse=True)

        year = today[:4]
        sismos = [[s["fecha"], s["hora"], s["mag"], s["prof"], s["lat"], s["lon"], s["ref"], s["int"], s["codigo"]]
                  for s in db.get_items("igp", "sismo", current_only=False, order="key") if s["fecha"][:4] == year]
        focos = [[f["lon"], f["lat"], f["dep"], f["prov"], f["_key"],
                  str(f["ubigeo"]).zfill(6)[:2] if f.get("ubigeo") else geo.region_code(f["dep"])]   # índice 5: región
                 for f in db.get_items("serfor", "foco")]
        alertas = db.get_items("serfor", "alerta")
        zonas = db.get_items("ingemmet", "zona_alerta")
        com = db.get_items("enfen", "comunicado", current_only=False, order="key DESC", limit=1)
        firms_n = len(db.get_items("firms", "deteccion"))

        # Comunicados gob.pe: últimos 14 días (por fecha publicada)
        lim = (datetime.date.fromisoformat(today) - datetime.timedelta(days=14)).isoformat()
        comunicados = sorted((c for sid in ("com_pnp", "com_provias", "com_mtc", "com_mininter")
                              for c in db.get_items(sid, "noticia", current_only=False)
                              if (c.get("fecha") or "") >= lim), key=lambda c: (c.get("fecha") or "", c["_first_seen"]), reverse=True)[:120]

        # SIDPOL: últimos 13 meses por provincia y modalidad (+ total nacional)
        sidpol = None
        meta = db.get_item("sidpol", "meta", "dataset")
        if meta:
            end = datetime.date.fromisoformat(meta["periodo"] + "-01")
            months = [(end.replace(day=1) - datetime.timedelta(days=0)).strftime("%Y-%m")]
            d = end
            for _ in range(12):
                d = (d - datetime.timedelta(days=1)).replace(day=1)
                months.insert(0, d.strftime("%Y-%m"))
            mods = meta["modalidades"]
            provs_s, national = {}, {m: [0] * len(months) for m in mods}
            for p in db.get_items("sidpol", "provincia"):
                row = {m: [p["series"].get(m, {}).get(mo, 0) for mo in months] for m in mods}
                provs_s[p["ubigeo"]] = row
                for m in mods:
                    national[m] = [a + b for a, b in zip(national[m], row[m])]
            prev_year = f"{int(meta['periodo'][:4]) - 1}{meta['periodo'][4:]}"
            sidpol = {"periodo": meta["periodo"], "months": months, "mods": mods, "provs": provs_s, "national": national,
                      "prevYearSameMonth": prev_year, "url": meta["url"], "dataset": "https://www.datosabiertos.gob.pe/dataset/denuncias-policiales-1"}

        # ── Región (código de 2 dígitos) en cada registro, para el filtro del dashboard ──
        rc = geo.region_code
        for a in avisos_out:
            ds = by_aviso[a["key"]]
            a["regs"] = sorted({p[:2] for d in ds for p in d["levels"]} | {r for r in map(rc, a["dptos_texto"]) if r})
        for h in hidro:
            h["reg"] = rc(h.get("nom_departamento")) or geo.region_of_point(h["lon"], h["lat"])
        for i in indeci:
            i["reg"] = (i["prov"][:2] if i.get("prov") else None) or rc(re.split(r"\s*[–-]\s*", i.get("dpto") or "")[-1]) \
                or (geo.regions_mentioned(i.get("titulo", ""))[:1] or [None])[0]
        for s in sismos:   # índice 9: región por polígono; mar adentro, por la referencia del IGP ("… Santa - Ancash")
            s.append(geo.region_of_point(s[5], s[4]) or rc(s[6].rsplit(" - ", 1)[-1])
                     or (geo.regions_mentioned(s[6].rsplit(",", 1)[-1]) or [None])[0])   # "…, Provincia Constitucional del Callao"
        for a in alertas:
            a["reg"] = str(a.get("ubigeo") or "").zfill(6)[:2] if a.get("ubigeo") else rc(a.get("dep"))
        for z in zonas:
            z["reg"] = rc(z.get("region")) or geo.region_of_point(z["lon"], z["lat"])
        for p in pron:
            p["reg"] = rc(p["departamento"])
        for c in comunicados:
            c["regs"] = geo.regions_mentioned(f"{c.get('titulo', '')} {c.get('descripcion', '')}")
        regions = [{"id": f["properties"]["id"], "n": f["properties"]["n"]} for f in deps["features"]]
        danos = danos_resumen()
        by_item = {e["item_key"]: e for e in danos["eventos"]}
        pdf_danos = {r["_key"]: ((r.get("danos") or {}).get("totales") or {}) for r in db.get_items("indeci", "reporte_pdf", current_only=False)}
        for i in indeci:
            i["danos"] = pdf_danos.get(i["_key"]) or None

        # Fotos de los PDF INDECI: conteo por ítem + tira "imágenes del día" (sin repetir la misma foto entre reportes)
        pdfs = {r["_key"]: r for r in db.get_items("indeci", "reporte_pdf", current_only=False)}
        fotos_dia, seen_sha = [], set()
        for i in indeci:   # ya ordenado del más nuevo al más viejo
            rec = pdfs.get(i["_key"])
            i["fotos"] = len(rec.get("fotos", [])) if rec else None      # None = PDF aún no procesado
            for f in (rec or {}).get("fotos", []):
                if f.get("sha") in seen_sha:
                    continue
                seen_sha.add(f.get("sha"))
                fotos_dia.append({"url": f["url"], "w": f["w"], "h": f["h"], "caption": f.get("caption"), "fecha": f.get("fecha"),
                                  "key": i["_key"], "reg": i.get("reg"), "evento": i.get("evento"), "distrito": i.get("distrito"),
                                  "dpto": i.get("dpto"), "num": i.get("num"), "tipo": i.get("tipo")})

        data = {
            "live": True, "snapshot": today, "built": datetime.datetime.now(LIMA).strftime("%Y-%m-%d %H:%M"),
            "geo": {"deps": deps, "provs": provs}, "provCentroids": {k: v["c"] for k, v in pinfo.items()},
            "avisos": avisos_out, "avisosHist": avisos_hist, "levelsByDay": dict(sorted(levels.items())),
            "uv": uv, "uvUnmatched": uv_unmatched, "pronostico": pron, "hidro": hidro,
            "indeci": indeci, "indeciWindowH": INDECI_WINDOW_H,
            "indeciUnlocated": sum(1 for i in indeci if i.get("clase") == "reporte" and not i.get("prov")),
            "sismos": sismos, "focos": focos, "alertas": alertas, "zonas": zonas,
            "enfen": {"ultimo_comunicado": com[0] if com else {}}, "firmsCount": firms_n,
            "comunicados": comunicados, "sidpol": sidpol, "regions": sorted(regions, key=lambda r: r["n"]),
            "fotosDia": fotos_dia[:80], "danos": danos,
        }
        data["buildMs"] = int((time.time() - t0) * 1000)
        _cache["data"] = data
        return data
