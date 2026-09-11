"""Ficha provincial imprimible: datos de todas las fuentes para una provincia + exportación a PDF con Chrome headless.

GET /ficha/{ubigeo}      → HTML A4 con los datos embebidos (web/ficha.html)
GET /ficha/{ubigeo}.pdf  → el mismo HTML impreso por Chrome (--headless --print-to-pdf)
"""
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
from zoneinfo import ZoneInfo

from shapely.geometry import Point
from shapely.prepared import prep

from . import config, db, geo, snapshot
from .sources import SOURCES

LIMA = ZoneInfo("America/Lima")
CHROME_CANDIDATES = [os.environ.get("MESA_CHROME", ""),
                     "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                     "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                     "/Applications/Chromium.app/Contents/MacOS/Chromium",
                     shutil.which("google-chrome") or "", shutil.which("chromium") or ""]
NEAR_KM = 50          # sismos "cercanos": epicentro a menos de esta distancia del límite provincial
INDECI_DAYS = 7


def _km(deg):
    return deg * 111.0


def scope_of(code):
    """code de 2 dígitos = departamento; de 4 = provincia. Devuelve None si no existe."""
    provs = geo.provinces()
    if len(code) == 4 and code in provs:
        p = provs[code]
        return {"tipo": "provincial", "code": code, "dep": code[:2], "nombre": p["nombre"], "departamento": p["dpto"],
                "provs": [code], "geom": p["geom"]}
    if len(code) == 2:
        codes = sorted(c for c in provs if c.startswith(code))
        if not codes:
            return None
        from shapely.ops import unary_union
        return {"tipo": "departamental", "code": code, "dep": code, "nombre": provs[codes[0]]["dpto"], "departamento": provs[codes[0]]["dpto"],
                "provs": codes, "geom": unary_union([provs[c]["geom"] for c in codes])}
    return None


def build(code):
    sc = scope_of(code)
    if not sc:
        return None
    provs = geo.provinces()
    D = snapshot.build()
    now = datetime.datetime.now(LIMA)
    dep, P, dept_level = sc["dep"], set(sc["provs"]), sc["tipo"] == "departamental"
    names = {geo.norm(provs[c]["nombre"]) for c in P}
    name = geo.norm(sc["nombre"])
    g = sc["geom"]
    pg = prep(g)

    # ── geometría: el ámbito, las provincias de su departamento y el departamento ──
    deps, provs_gj = D["geo"]["deps"], D["geo"]["provs"]
    geo_out = {"scope": [f for f in provs_gj["features"] if f["properties"]["id"] in P],
               "dep_provs": [f for f in provs_gj["features"] if f["properties"]["id"].startswith(dep) and f["properties"]["id"] not in P],
               "dep": next((f for f in deps["features"] if f["properties"]["id"] == dep), None)}

    # ── SENAMHI: nivel por día (por provincia) y avisos que lo explican ──
    days = [d for d in sorted(D["levelsByDay"]) if d >= now.date().isoformat()]
    niveles = []
    for d in days:
        cells = {c: D["levelsByDay"][d].get(c) for c in P}
        lv = {c: (x["m"] if x else 0) for c, x in cells.items()}
        niveles.append({"dia": d, "m": max(lv.values(), default=0), "por_prov": lv,
                        "conteo": {n: sum(1 for v in lv.values() if v == n) for n in (4, 3, 2)},
                        "a": sorted({tuple(a) for x in cells.values() if x for a in x["a"]}, reverse=True)})
    nros = {n for x in niveles for n, _ in x["a"]}
    avisos = [a for a in D["avisos"] if a["nro"] in nros]

    uv_by = {c: D["uv"][c] for c in P if c in D["uv"]}
    uv = uv_by.get(code) if not dept_level else None

    # ── Pronóstico: provincia = capital / nombre ("LIMA OESTE / CALLAO" vale por partes); departamento = todas sus ciudades ──
    same_dep = lambda x: geo.region_code(x["departamento"]) in (dep, "15" if dep == "07" else dep)  # noqa: E731
    capital = geo.norm(uv["z"]) if uv else None
    def city_match(c):
        parts = [geo.norm(x) for x in c.split("/")]
        if dept_level and dep != "07":
            return True
        keys = {capital, name} if not dept_level else {"CALLAO"}
        return any(pt in keys or pt.startswith(name + " ") for pt in parts)
    pron = [x for x in D["pronostico"] if same_dep(x) and city_match(x["ciudad"])]

    # ── INDECI: últimos 7 días, con fotos y texto del PDF ──
    cutoff = time.time() - INDECI_DAYS * 86400
    pdfs = {r["_key"]: r for r in db.get_items("indeci", "reporte_pdf", current_only=False)}
    indeci = []
    for it in db.get_items("indeci", "item", current_only=False, limit=3000):
        if it.get("clase") != "reporte" or it.get("prov") not in P or (it.get("ts") or 0) < cutoff:
            continue
        rec = pdfs.get(it["_key"]) or {}
        indeci.append({k: it.get(k) for k in ("titulo", "link", "tipo", "num", "seq", "evento", "distrito", "provincia", "dpto", "ts", "descripcion", "prov")}
                      | {"hechos": rec.get("hechos"), "fotos": rec.get("fotos", [])[:2 if dept_level else 3], "pdf_url": rec.get("pdf_url"),
                         "sinpad": rec.get("sinpad"), "danos": ((rec.get("danos") or {}).get("totales") or None),
                         "danos_actualizado": rec.get("danos_actualizado")})
    indeci.sort(key=lambda i: i["ts"] or 0, reverse=True)

    # ── SERFOR, INGEMMET, hidrología ──
    alertas = [a for a in D["alertas"] if str(a.get("ubigeo") or "").zfill(6)[:4] in P]
    focos = [f for f in D["focos"] if f[5] == dep and (dept_level or geo.norm(f[3]) == name)]
    zonas = [z for z in D["zonas"] if z.get("reg") == dep and (dept_level or geo.norm(z.get("provincia")) == name)]
    hidro = [h for h in D["hidro"] if h.get("reg") == dep and (dept_level or geo.norm(h.get("nom_provincia")) == name)]

    # ── IGP: últimos 30 días, dentro del ámbito o a menos de NEAR_KM ──
    lim = (now.date() - datetime.timedelta(days=30)).isoformat()
    sismos = []
    for s in D["sismos"]:
        if s[0] < lim:
            continue
        pt = Point(s[5], s[4])
        inside = pg.contains(pt)
        dist = 0 if inside else round(_km(g.distance(pt)))
        if inside or dist <= NEAR_KM:
            sismos.append({"fecha": s[0], "hora": s[1], "mag": s[2], "prof": s[3], "lat": s[4], "lon": s[5], "ref": s[6],
                           "codigo": s[8], "dentro": inside, "dist_km": dist})
    sismos.sort(key=lambda s: s["fecha"] + s["hora"], reverse=True)

    # ── SIDPOL: serie del ámbito (suma de sus provincias) ──
    sid = None
    if D.get("sidpol") and any(c in D["sidpol"]["provs"] for c in P):
        S = D["sidpol"]
        series = {m: [sum(S["provs"].get(c, {}).get(m, [0] * len(S["months"]))[i] for c in P) for i in range(len(S["months"]))] for m in S["mods"]}
        sid = {"months": S["months"], "periodo": S["periodo"], "mods": S["mods"], "series": series, "dataset": S["dataset"]}

    # ── Comunicados: provincia = nombre en el texto; departamento = región mencionada ──
    if dept_level:
        comunicados = [c for c in D["comunicados"] if dep in (c.get("regs") or [])][:12]
    else:
        pat = re.compile(rf"\b{re.escape(name)}\b")
        comunicados = [c for c in D["comunicados"] if pat.search(geo.norm(f"{c.get('titulo', '')} {c.get('descripcion', '')}"))][:10]

    # ── Daños INDECI (7 días, sin duplicar eventos; repartidos por DPTO./PROV. si el evento abarca varios) ──
    DN = D.get("danos") or {}
    if dept_level:
        part = (DN.get("por_reg") or {}).get(dep)
        evs = [{**e, "totales": e["por_reg"][dep]} for e in DN.get("eventos", []) if dep in (e.get("por_reg") or {})]
    else:
        part = (DN.get("por_prov") or {}).get(code)
        evs = [{**e, "totales": e["por_prov"][code]} for e in DN.get("eventos", []) if code in (e.get("por_prov") or {})]
    danos = {"dias": DN.get("dias", 7), "desde": DN.get("desde"), "labels": DN.get("labels", {}), "orden": DN.get("orden", []),
             **(part or {"totales": {}, "nuevos": {}, "seguimiento": {}, "n_eventos": 0, "n_nuevos": 0}),
             "eventos": sorted(evs, key=lambda e: -((e["totales"].get("personas_damnificadas") or 0) + (e["totales"].get("personas_afectadas") or 0)))[:12]}

    # ── Tabla de provincias (solo ficha departamental) ──
    tabla = []
    if dept_level:
        last = len(D["sidpol"]["months"]) - 1 if D.get("sidpol") else 0
        for c in sc["provs"]:
            srow = (D.get("sidpol") or {}).get("provs", {}).get(c)
            tot = [sum(srow[m][i] for m in srow) for i in (0, last)] if srow else None
            tabla.append({"ubigeo": c, "provincia": provs[c]["nombre"], "nivel": niveles[0]["por_prov"].get(c, 0) if niveles else 0,
                          "uv": uv_by.get(c, {}).get("v", [None])[0], "indeci": sum(1 for i in indeci if i["prov"] == c),
                          "incendios": sum(1 for a in alertas if str(a.get("ubigeo") or "").zfill(6)[:4] == c and a["estado"] != "Extinguido"),
                          "zonas": sum(1 for z in zonas if geo.norm(z.get("provincia")) == geo.norm(provs[c]["nombre"])),
                          "denuncias": tot[1] if tot else None, "denuncias_prev": tot[0] if tot else None,
                          "damnificados": ((DN.get("por_prov") or {}).get(c, {}).get("totales") or {}).get("personas_damnificadas"),
                          "afectados": ((DN.get("por_prov") or {}).get(c, {}).get("totales") or {}).get("personas_afectadas")})

    health = {h["id"]: h for h in snapshot.health()}
    fuentes = [{"id": sid_, "org": m["org"], "name": m["name"], "last_ok": health.get(sid_, {}).get("last_ok"),
                "status": health.get(sid_, {}).get("status")} for sid_, m in SOURCES.items()]

    return {"tipo": sc["tipo"], "code": code, "ubigeo": code, "provincia": sc["nombre"], "nombre": sc["nombre"], "departamento": sc["departamento"],
            "n_provincias": len(P), "generado": time.time(), "generado_txt": now.strftime("%d/%m/%Y %H:%M"), "hoy": now.date().isoformat(),
            "geo": geo_out, "niveles": niveles, "avisos": avisos, "uv": uv, "uv_max": max(uv_by.values(), key=lambda u: u["v"][0], default=None) if dept_level else None,
            "pronostico": pron, "indeci": indeci, "indeci_dias": INDECI_DAYS, "alertas": alertas, "focos": len(focos),
            "focos_pts": [[f[0], f[1]] for f in focos][:600], "zonas": zonas, "hidro": hidro, "sismos": sismos, "near_km": NEAR_KM,
            "sidpol": sid, "comunicados": comunicados, "tabla": tabla, "danos": danos, "enfen": D["enfen"].get("ultimo_comunicado", {}), "fuentes": fuentes}


def html(ubigeo):
    data = build(ubigeo)
    if data is None:
        return None
    tpl = (config.WEB / "ficha.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    footer_name = re.sub(r'["\\]', "", f"Ficha {data['tipo']} · {data['nombre'].title()}")
    return tpl.replace("/*__FICHA__*/null", payload).replace("Ficha provincial · __PROVINCIA__", footer_name), data


def chrome():
    return next((c for c in CHROME_CANDIDATES if c and pathlib.Path(c).exists()), None)


def pdf(ubigeo, base_url):
    """Imprime /ficha/{ubigeo} con Chrome headless y devuelve (bytes, nombre_de_archivo)."""
    exe = chrome()
    if not exe:
        raise RuntimeError("No se encontró Chrome/Chromium/Brave. Defina MESA_CHROME con la ruta del ejecutable.")
    sc = scope_of(ubigeo)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", geo.norm(sc["nombre"]).title()).strip("_")
    kind = "Departamento_" if sc["tipo"] == "departamental" else ""
    fname = f"Ficha_{kind}{slug}_{ubigeo}_{datetime.datetime.now(LIMA).strftime('%Y-%m-%d_%H%M')}.pdf"
    with tempfile.TemporaryDirectory(prefix="mesa-pdf-") as tmp:
        out = pathlib.Path(tmp) / "ficha.pdf"
        cmd = [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--hide-scrollbars",
               "--use-mock-keychain", "--password-store=basic", "--disable-extensions", "--disable-background-networking",
               f"--user-data-dir={tmp}/profile", "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
               "--virtual-time-budget=10000", f"--print-to-pdf={out}", f"{base_url}/ficha/{ubigeo}?print=1"]
        # En macOS, Chrome headless escribe el PDF y a veces no termina: se vigila el archivo y se cierra el proceso
        # cuando el PDF está completo (termina en %%EOF y su tamaño no cambia).
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        deadline, last_size, stable = time.time() + 60, -1, 0
        try:
            while time.time() < deadline:
                if proc.poll() is not None and not out.exists():
                    break
                if out.exists():
                    size = out.stat().st_size
                    stable = stable + 1 if size == last_size and size > 1000 else 0
                    last_size = size
                    if stable >= 3 and out.read_bytes()[-1024:].rstrip().endswith(b"%%EOF"):
                        break
                time.sleep(0.25)
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, 15)
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    os.killpg(proc.pid, 9)
        if not out.exists() or out.stat().st_size < 1000 or not out.read_bytes()[-1024:].rstrip().endswith(b"%%EOF"):
            err = (proc.stderr.read() or b"")[-400:].decode("utf-8", "replace") if proc.stderr else ""
            raise RuntimeError(f"Chrome no generó un PDF completo en 60 s. {err}")
        return out.read_bytes(), fname
