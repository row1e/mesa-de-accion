"""Resumen del día: lo más significativo de las últimas 24 horas en todas las fuentes, y algunos cruces entre ellas.

Cada punto trae su titular, sus cifras, un nivel de importancia (3 alto, 2 medio, 1 bajo) y los registros en que se
apoya (source/kind/key, para abrir su ficha y redactar con IA). Lo usan la página /resumen y el resumen diario de la IA,
que redacta solo con estas cifras.
"""
import collections
import datetime
import re
import threading
import time
import unicodedata
from zoneinfo import ZoneInfo

from . import db, snapshot

LIMA = ZoneInfo("America/Lima")
_cache = {"t": 0, "data": None}
_lock = threading.Lock()


def _norm(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKD", (s or "").upper()).encode("ascii", "ignore").decode()).strip()


def _t(s):
    return (s or "").title()


def _item(source, kind, key, titulo, sub=None, lugar=None):
    return {"source": source, "kind": kind, "key": key, "titulo": titulo, "sub": sub, "lugar": lugar}


def _punto(pid, tema, titulo, resumen, nivel, cifras, items=None, nota=None, periodo=None):
    """`periodo`: a qué tiempo se refieren las cifras (hoy, últimas 24 h, estado actual…), visible en la página y para la IA."""
    return {"id": pid, "tema": tema, "titulo": titulo, "resumen": resumen, "nivel": nivel, "cifras": cifras,
            "items": items or [], "nota": nota, "periodo": periodo}


DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic"]


def _dm(iso):
    """'2026-10-21' → 'mié 21 oct'"""
    try:
        d = datetime.date.fromisoformat(iso[:10])
    except (TypeError, ValueError):
        return iso or "—"
    return f"{DIAS[d.weekday()][:3]} {d.day} {MESES[d.month - 1]}"


def _vigencia(ini, fin, hoy):
    """Rango de un aviso en palabras y si ya rige: 'del mié 21 oct al vie 23 oct · empieza el mié 21 oct'."""
    rango = f"del {_dm(ini)} al {_dm(fin)}"
    if ini and ini > hoy:
        return f"{rango} · empieza el {_dm(ini)}"
    if fin and fin < hoy:
        return f"{rango} · ya terminó"
    return f"{rango} · vigente hoy" + (" (último día)" if fin == hoy else "")


def _ocurrencia(texto):
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", texto or "")
    try:
        return datetime.date(int(m[3]), int(m[2]), int(m[1])).isoformat() if m else None
    except ValueError:
        return None


def build(horas=24):
    with _lock:
        if _cache["data"] and time.time() - _cache["t"] < 300:
            return _cache["data"]
        data = _build(horas)
        _cache.update(t=time.time(), data=data)
        return data


def _build(horas):
    D = snapshot.build()
    ahora = datetime.datetime.now(LIMA)
    desde_ts = time.time() - horas * 3600
    desde = (ahora - datetime.timedelta(hours=horas)).strftime("%Y-%m-%d %H:%M")
    hoy = ahora.date().isoformat()
    hoy_txt = f"{DIAS[ahora.weekday()]} {ahora.day} de {['enero','febrero','marzo','abril','mayo','junio','julio','agosto','setiembre','octubre','noviembre','diciembre'][ahora.month - 1]}"
    P = {f["properties"]["id"]: f["properties"] for f in D["geo"]["provs"]["features"]}
    pn = lambda p: _t(P[p]["n"]) if p in P else None   # noqa: E731
    puntos = []

    # ── SENAMHI: avisos emitidos en 24 h y nivel de hoy ──
    lv = D["levelsByDay"].get(hoy, {})
    n4, n3 = sum(1 for c in lv.values() if c["m"] == 4), sum(1 for c in lv.values() if c["m"] == 3)
    nuevos = [a for a in D["avisos"] if (a.get("emision") or "") >= (ahora - datetime.timedelta(hours=horas)).date().isoformat()]
    rojos = [a for a in D["avisos"] if a.get("nivel") == "ROJO" and hoy in (a.get("dias") or [])]
    puntos.append(_punto(
        "senamhi", "Clima", f"{n4 + n3} provincias en aviso naranja o rojo hoy, {hoy_txt}" + (f"; {len(rojos)} aviso{'s' if len(rojos) != 1 else ''} rojo{'s' if len(rojos) != 1 else ''} vigente{'s' if len(rojos) != 1 else ''}" if rojos else ""),
        "El SENAMHI emitió " + (f"{len(nuevos)} avisos nuevos en las últimas {horas} h" if nuevos else f"ningún aviso nuevo en las últimas {horas} h") + ".",
        3 if n4 or rojos else 2 if n3 else 1,
        {"provincias_nivel_4_hoy": n4, "provincias_nivel_3_hoy": n3, "provincias_con_aviso_hoy": len(lv), "avisos_nuevos": len(nuevos)},
        [_item("senamhi_avisos", "aviso", a["key"], f"Aviso N° {a['nro']} · {_t(a['titulo'])}", f"{a['nivel'].lower()} · {_vigencia(a.get('inicio'), a.get('fin'), hoy)}"
               + (" · emitido en las últimas 24 h" if a in nuevos else ""))
         for a in sorted(nuevos + [r for r in rojos if r not in nuevos], key=lambda a: ({"ROJO": 0, "NARANJA": 1}.get(a["nivel"], 2), -a["nro"]))][:8], periodo=f"hoy, {hoy_txt} (niveles por provincia) · avisos emitidos en las últimas 24 h"))

    # ── INDECI: emergencias nuevas (sin seguimientos) y daños de eventos nuevos ──
    rep = [i for i in D["indeci"] if i.get("clase") == "reporte" and float(i.get("ts") or 0) >= desde_ts]
    nuevas = [i for i in rep if not i.get("seguimiento")]
    tipos = collections.Counter(_t(i.get("evento")) for i in nuevas)
    dn = D.get("danos") or {}
    nd = dn.get("nuevos") or {}
    con_danos = sorted([e for e in (dn.get("eventos") or []) if e.get("nuevo") and e.get("ts", 0) >= desde_ts],
                       key=lambda e: -sum(v for k, v in e["totales"].items() if k.startswith("personas") and isinstance(v, (int, float))))
    fall = sum((e["totales"].get("personas_fallecidas") or 0) for e in con_danos)
    puntos.append(_punto(
        "indeci", "Emergencias", f"{len(nuevas)} emergencias nuevas reportadas por INDECI",
        ("Las más frecuentes: " + ", ".join(f"{t.lower()} ({n})" for t, n in tipos.most_common(3)) + ". " if tipos else "")
        + (f"{len(rep) - len(nuevas)} reportes más son seguimientos de eventos antiguos." if len(rep) > len(nuevas) else ""),
        3 if fall or len(nuevas) >= 30 else 2 if nuevas else 1,
        {"emergencias_nuevas": len(nuevas), "reportes_de_seguimiento": len(rep) - len(nuevas), "fallecidos_en_eventos_nuevos": fall,
         **{"tipo_" + re.sub(r"\W+", "_", k.lower()).strip("_"): v for k, v in tipos.most_common(4)}},
        [_item("indeci", "item", e["item_key"], f"{_t(e['evento'])} — {_t(e['distrito'])}", ", ".join(f"{k.replace('_', ' ')}: {v:g}" for k, v in e["totales"].items() if v)[:140], _t(e.get("dpto")))
         for e in con_danos[:6]]
        + [_item("indeci", "item", i["_key"], f"{_t(i.get('evento'))} — {_t(i.get('distrito'))}", f"reporte N° {i.get('num')}", _t(i.get("dpto")))
           for i in nuevas if i["_key"] not in {e["item_key"] for e in con_danos[:6]}][:max(0, 8 - len(con_danos[:6]))],
        f"Daños de los eventos nuevos de los últimos {dn.get('dias') or 7} días: " + ", ".join(f"{k.replace('_', ' ')} {v:g}" for k, v in nd.items() if v) if nd else None, periodo=f"reportes de las últimas 24 h · daños: eventos nuevos de los últimos 7 días"))

    # ── Incendios forestales: SERFOR (satélite) e INDECI ──
    g = collections.defaultdict(list)
    for a in D["alertas"]:
        g[a.get("cod") or a["_key"]].append(a)
    fuegos = []
    for k, al in g.items():
        al.sort(key=lambda a: (a.get("fecha") or "", a.get("hora") or ""))
        fuegos.append({"cod": k, "n": len(al), "ini": f"{al[0].get('fecha')} {al[0].get('hora') or ''}".strip(), "estado": al[-1]["estado"],
                       "dist": al[0].get("dist"), "dep": al[0].get("dep"), "key": al[-1]["_key"]})
    nuevos_f = [f for f in fuegos if f["ini"] >= desde]
    activos = [f for f in fuegos if f["estado"] != "Extinguido"]
    ind_f = sum(1 for i in nuevas if "INCENDIO FORESTAL" in (i.get("evento") or "").upper())
    cob = _cobertura_incendios()
    puntos.append(_punto(
        "incendios", "Incendios", f"{len(nuevos_f)} incendios forestales nuevos detectados por satélite; {len(activos)} siguen activos",
        f"INDECI reportó {ind_f} incendios forestales como emergencia en las últimas {horas} h. "
        + (f"En los últimos 7 días, solo {cob['reportados']} de {cob['detectados']} incendios vistos por el satélite ({cob['pct']} %) llegaron a un reporte de emergencia." if cob["detectados"] else ""),
        3 if len(activos) >= 30 or len(nuevos_f) >= 30 else 2 if nuevos_f or activos else 1,
        {"incendios_nuevos_satelite": len(nuevos_f), "incendios_activos": len(activos), "focos_de_calor_24h": len(D["focos"]),
         "incendios_forestales_reportados_indeci": ind_f, "satelite_7_dias_detectados": cob["detectados"],
         "satelite_7_dias_con_reporte_indeci": cob["reportados"], "satelite_7_dias_porcentaje_reportado": cob["pct"]},
        [_item("serfor", "alerta", f["key"], f"Incendio forestal · {_t(f['dist'])}", f"{f['n']} detecciones · {f['estado'].lower()} · desde {f['ini']}", _t(f["dep"]))
         for f in sorted(activos, key=lambda f: -f["n"])[:8]],
        "El cruce satélite–INDECI se hace por distrito y fecha (±3 días); INDECI solo reporta los incendios que causan daños o piden atención.", periodo=f"nuevos en las últimas 24 h · activos ahora · cobertura satélite–INDECI: últimos 7 días"))

    # ── Sismos ──
    s24 = sorted([s for s in D["sismos"] if f"{s[0]} {s[1]}" >= desde], key=lambda s: -s[2])
    mx = s24[0] if s24 else None
    puntos.append(_punto(
        "sismos", "Sismos", f"{len(s24)} sismos en {horas} h" + (f"; el mayor, M{mx[2]} {mx[6]}" if mx else ""),
        f"{sum(1 for s in s24 if s[2] >= 4)} de magnitud 4 o más." if s24 else "Sin sismos registrados por el IGP.",
        3 if mx and mx[2] >= 5.5 else 2 if mx and mx[2] >= 4.5 else 1,
        {"sismos": len(s24), "sismos_magnitud_4_o_mas": sum(1 for s in s24 if s[2] >= 4), "magnitud_maxima": mx[2] if mx else None},
        [_item("igp", "sismo", s[8], f"Sismo M{s[2]} · prof. {s[3]} km", s[6], None) for s in s24[:6]], periodo=f"últimas 24 h"))

    # ── Vías (PROVIAS) ──
    V = D.get("vias") or []
    nv = [v for v in V if (v.get("_first_seen") or 0) >= desde_ts]
    inter = [v for v in V if v.get("transito_cod") == "03"]
    puntos.append(_punto(
        "vias", "Vías", f"{len(inter)} vías nacionales interrumpidas; {len(nv)} emergencias viales nuevas",
        f"PROVIAS registra {len(V)} emergencias en la red vial nacional: {sum(1 for v in V if v.get('transito_cod') == '02')} con tránsito restringido "
        f"y {sum(1 for v in V if v.get('transito_cod') == '04')} por confirmar.",
        3 if inter else 2 if nv else 1,
        {"vias_interrumpidas": len(inter), "emergencias_viales_nuevas": len(nv), "emergencias_viales_total": len(V)},
        [_item("provias", "emergencia", v["_key"], f"{v['transito']} · {(v.get('tipo') or '').capitalize()}", f"{v.get('ruta')} · {v.get('tramo')}", pn(v.get("prov")))
         for v in inter + [x for x in nv if x not in inter]][:8], periodo=f"estado actual · nuevas: registradas en las últimas 24 h"))

    # ── Mar (DHN) ──
    M = D.get("mar") or {}
    vig = [a for a in M.get("avisos") or [] if (a.get("fin") or "") >= hoy and (a.get("inicio") or "") <= hoy]
    niv = max([n for a in vig for n in (a.get("provs_por_dia") or {}).get(hoy, {}).values()] or [0])
    pal = {2: "ligero", 3: "moderado", 4: "fuerte"}
    pts = [p for p in M.get("puntos") or [] if (p.get("ventanas") or [None])[0]]
    om = max(pts, key=lambda p: p["ventanas"][0]["ola_max_m"]) if pts else None
    puntos.append(_punto(
        "mar", "Mar", (f"Oleaje {pal.get(niv, 'anómalo')} en el litoral (DHN)" if any(a["tipo"] == "oleaje" for a in vig) else "Sin aviso de oleaje vigente")
        + (f"; aviso de viento vigente" if any(a["tipo"] == "viento" for a in vig) else ""),
        (f"Ola máxima esperada en las próximas 24 h: {om['ventanas'][0]['ola_max_m']} m en {om['nombre']} (modelo Open-Meteo, referencial)." if om else ""),
        3 if niv >= 4 else 2 if vig else 1,
        {"avisos_dhn_vigentes": len(vig), "ola_max_m_24h_referencial": om["ventanas"][0]["ola_max_m"] if om else None},
        [_item("dhn_avisos", "aviso_dhn", a["_key"], f"Aviso de {a['tipo']} N° {a['numero']}", a.get("validez")) for a in vig], periodo=f"avisos vigentes hoy · ola: próximas 24 h (modelo referencial)"))

    # ── Bomberos (Lima, Callao y costa sur) ──
    B = D.get("bomberos") or []
    cats = collections.Counter(b["categoria"] for b in B)
    esc = [b for b in B if b.get("escalo")]
    largos = sorted([b for b in B if b.get("duracion_min")], key=lambda b: -b["duracion_min"])
    puntos.append(_punto(
        "bomberos", "Bomberos", f"{len(B)} emergencias atendidas por bomberos, {cats.get('Incendio', 0)} incendios",
        ", ".join(f"{k.lower()} {v}" for k, v in cats.most_common(4)) + f"; además {D.get('bomberosMedicas', {}).get('total', 0)} emergencias médicas."
        + (f" {len(esc)} sumaron unidades en el camino." if esc else ""),
        2 if cats.get("Incendio", 0) >= 15 or esc else 1,
        {"partes": len(B), "emergencias_medicas": D.get("bomberosMedicas", {}).get("total", 0), "partes_que_sumaron_unidades": len(esc),
         **{"bomberos_" + re.sub(r"\W+", "_", k.lower()).strip("_"): v for k, v in cats.most_common(5)}},
        [_item("bomberos", "parte", b["_key"], f"{b['categoria']} · {b.get('detalle') or ''}", f"{b.get('direccion') or ''} · {b.get('estado')}", b.get("distrito"))
         for b in (esc + largos[:4] + [b for b in B if b["categoria"] in ("Incendio", "Materiales Peligrosos (Incidente)")])][:8],
        "La página del CGBVP cubre Lima, Callao y parte de la costa sur.", periodo=f"partes de las últimas 24 h"))

    # ── Obras de prevención (ANA) y cruce con los avisos de lluvia ──
    O = D.get("obras") or []
    de_lluvia = {a["nro"] for a in D["avisos"] if re.search(r"PRECIPITACION|LLUVIA", _norm(a.get("titulo")))}
    llueve = {p for p, c in lv.items() if any(x[0] in de_lluvia for x in c["a"])}
    en_lluvia = [o for o in O if o.get("prov") in llueve]
    sin_empezar = [o for o in en_lluvia if o["estado"] == "Por Ejecutar"]
    avanzaron = [o for o in O if o.get("fec_avance") and o["fec_avance"] >= (ahora - datetime.timedelta(hours=horas)).date().isoformat()]
    venc = [o for o in O if o["estado"] == "Vencida"]
    puntos.append(_punto(
        "obras", "Prevención", (f"{len(sin_empezar)} de {len(en_lluvia)} obras de prevención en provincias con aviso de lluvia aún no empiezan"
                                if en_lluvia else f"{len(venc)} obras de prevención con el plazo vencido"),
        f"La ANA reportó avance de {len(avanzaron)} obras desde ayer; {len(venc)} tienen el plazo vencido y "
        f"{sum(1 for o in O if o['estado'] == 'Por Ejecutar')} de {len(O)} siguen por ejecutar.",
        3 if en_lluvia and len(sin_empezar) >= 20 else 2 if venc or sin_empezar else 1,
        {"obras_en_provincias_con_aviso_de_lluvia": len(en_lluvia), "obras_sin_empezar_en_esas_provincias": len(sin_empezar),
         "obras_con_avance_reportado_hoy": len(avanzaron), "obras_vencidas": len(venc), "obras_por_ejecutar_total": sum(1 for o in O if o["estado"] == "Por Ejecutar"),
         "obras_total": len(O)},
        [_item("ana_obras", "obra", o["_key"], f"{_t(o.get('rio'))} · {o.get('sector')}", f"{o['estado'].lower()} · avance {o.get('avance_km_pct') or 0} %", _t(o.get("dist")))
         for o in (venc + sorted(sin_empezar, key=lambda o: o.get("nivel_peligro") != "MUY ALTO"))][:8], periodo=f"estado actual de las obras · avisos de lluvia de hoy · avance reportado desde ayer"))

    # ── ENFEN ──
    enf = (D.get("enfen") or {}).get("ultimo_comunicado") or {}
    if enf:
        puntos.append(_punto("enfen", "El Niño", f"ENFEN: {enf.get('estado') or 'sin dato'}",
                             f"Último comunicado oficial N° {enf.get('numero')} ({enf.get('fecha') or '—'}).",
                             2 if "ALERTA" in _norm(enf.get("estado")) else 1, {"comunicado_numero": enf.get("numero")},
                             [_item("enfen", "comunicado", enf.get("_key"), enf.get("titulo") or f"Comunicado N° {enf.get('numero')}", enf.get("estado"))] if enf.get("_key") else [], periodo=f"último comunicado oficial"))

    # ── Comunicados oficiales (gob.pe) ──
    com = [c for c in D.get("comunicados") or [] if (c.get("_first_seen") or 0) >= desde_ts]
    if com:
        puntos.append(_punto("comunicados", "Comunicados", f"{len(com)} comunicados y notas de prensa oficiales",
                             ", ".join(f"{k} ({v})" for k, v in collections.Counter(c.get("inst_nombre") for c in com).most_common(4)) + ".", 1,
                             {"comunicados": len(com)},
                             [_item(c.get("source"), "noticia", c["_key"], c.get("titulo"), c.get("inst_nombre")) for c in com[:6]], periodo=f"publicados en las últimas 24 h"))

    puntos.sort(key=lambda p: -p["nivel"])
    return {"generado": ahora.isoformat(timespec="minutes"), "desde": desde, "horas": horas, "hoy": hoy, "hoy_txt": hoy_txt,
            "puntos": puntos}


def _cobertura_incendios(dias=7):
    """Incendios que vio el satélite (SERFOR) en los últimos días y cuántos tienen un reporte de emergencia de INDECI en
    el mismo distrito (±3 días). Mide cuánto del fuego llega a los registros oficiales."""
    hoy = datetime.datetime.now(LIMA).date()
    lim = (hoy - datetime.timedelta(days=dias)).isoformat()
    g = collections.defaultdict(list)
    for a in db.get_items("serfor", "alerta", current_only=False):
        if (a.get("fecha") or "") >= (hoy - datetime.timedelta(days=dias + 10)).isoformat():
            g[a.get("cod") or a["_key"]].append(a)
    fuegos = []
    for xs in g.values():
        fs = sorted(x["fecha"] for x in xs if x.get("fecha"))
        if fs and lim <= fs[0] <= (hoy - datetime.timedelta(days=1)).isoformat():
            fuegos.append((_norm(xs[0].get("dist")), _norm(xs[0].get("dep")), datetime.date.fromisoformat(fs[0]), datetime.date.fromisoformat(fs[-1])))
    ind = collections.defaultdict(list)
    for r in db.conn().execute("SELECT key, payload FROM items WHERE source='indeci' AND kind='item' AND last_seen >= ?", (time.time() - (dias + 14) * 86400,)):
        import json
        it = json.loads(r["payload"])
        if "INCENDIO FORESTAL" not in (it.get("evento") or "").upper():
            continue
        oc = _ocurrencia(it.get("descripcion"))
        if oc:
            ind[_norm(it.get("dpto"))].append((_norm(it.get("distrito")), datetime.date.fromisoformat(oc)))
    mismo = lambda a, b: a == b or a.startswith(b + " ") or b.startswith(a + " ")   # noqa: E731
    rep = sum(1 for dist, dep, a, b in fuegos
              if any(mismo(dist, dn) and a - datetime.timedelta(days=3) <= d <= b + datetime.timedelta(days=3) for dn, d in ind.get(dep, [])))
    return {"detectados": len(fuegos), "reportados": rep, "pct": round(rep * 100 / len(fuegos)) if fuegos else None}


def dossier():
    """Lo que recibe la IA para el resumen diario: los puntos con sus cifras y sus registros principales."""
    R = build()
    return {"alcance": "resumen_diario", "fecha_del_resumen": R["hoy_txt"], "hora_de_corte": R["generado"][11:16],
            "periodo": f"últimas {R['horas']} horas, hasta {R['generado'][:16].replace('T', ' ')} (hora de Lima)",
            "importante": "Cada punto dice a qué periodo se refieren sus cifras (campo 'periodo'): respétalo al redactar.",
            "puntos": [{"tema": p["tema"], "periodo": p.get("periodo"), "importancia": {3: "alta", 2: "media", 1: "baja"}[p["nivel"]], "titular": p["titulo"],
                        "resumen": p["resumen"], "cifras": {k: v for k, v in p["cifras"].items() if v is not None},
                        "registros": [{"que": i["titulo"], "detalle": i["sub"], "lugar": i["lugar"]} for i in p["items"][:5]],
                        **({"nota": p["nota"]} if p.get("nota") else {})} for p in R["puntos"]]}
