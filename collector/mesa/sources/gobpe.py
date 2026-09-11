"""Comunicados y noticias de instituciones en gob.pe (JSON) y denuncias policiales SIDPOL (datos abiertos MININTER)."""
import collections
import csv
import datetime
import html
import io
import re

from .. import db, geo
from ..http import fetch, fetch_json, fetch_text

# slug de gob.pe → (id de fuente, nombre visible). Cada institución es una fuente propia: su estado, su historial.
# El JSON devuelve siempre las 9 más recientes (no pagina): con un sondeo cada 15 min alcanza, y la base acumula el historial.
INSTITUCIONES = {"pnp": ("com_pnp", "PNP"), "pvn": ("com_provias", "PROVIAS Nacional"),
                 "mtc": ("com_mtc", "MTC"), "mininter": ("com_mininter", "MININTER")}
SOURCE_IDS = {sid: slug for slug, (sid, _) in INSTITUCIONES.items()}
MESES = {m: i for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                     "septiembre", "octubre", "noviembre", "diciembre"], 1)} | {"setiembre": 9}


def _fecha(txt):
    m = re.search(r"(\d{1,2}) de (\w+) de (\d{4})", txt or "")
    if not m or m.group(2).lower() not in MESES:
        return None
    return datetime.date(int(m.group(3)), MESES[m.group(2).lower()], int(m.group(1))).isoformat()


def _record(slug, it):
    sid, nombre = INSTITUCIONES[slug]
    return {"inst": slug, "source": sid, "inst_nombre": nombre, "titulo": (it.get("title") or "").strip(),
            "descripcion": (it.get("description") or "").strip(), "url": it["url"], "imagen": it.get("image"),
            "fecha": _fecha(it.get("date")), "fecha_texto": (it.get("date") or "").strip()}


def noticias(slug):
    """Lector de las noticias/comunicados de una institución en gob.pe."""
    sid, nombre = INSTITUCIONES[slug]

    def run():
        items = fetch_json(sid, f"https://www.gob.pe/institucion/{slug}/noticias.json", name=f"{slug}.json")
        if not items:
            raise RuntimeError("gob.pe devolvió una lista vacía")
        total, new = db.upsert_items(sid, "noticia", {it["url"]: _record(slug, it) for it in items})
        last = max((_fecha(it.get("date")) or "" for it in items), default="")
        return total, new, f"{new} nuevas · {total} leídas (gob.pe muestra las 9 más recientes) · la más reciente del {last or '—'}"
    return run


def migrate_combined():
    """Una vez: reparte lo guardado bajo la fuente combinada 'comunicados' entre las cuatro instituciones."""
    c = db.conn()
    if not c.execute("SELECT 1 FROM items WHERE source='comunicados' LIMIT 1").fetchone():
        return 0
    moved = 0
    with db.tx() as t:
        for slug, (sid, nombre) in INSTITUCIONES.items():
            rows = t.execute("SELECT key, payload FROM items WHERE source='comunicados' AND json_extract(payload,'$.inst')=?", (slug,)).fetchall()
            for r in rows:
                t.execute("UPDATE items SET source=?, payload=json_set(payload,'$.source',?) WHERE source='comunicados' AND key=?", (sid, sid, r["key"]))
                t.execute("UPDATE details SET source=? WHERE source='comunicados' AND key=?", (sid, r["key"]))
            t.execute("UPDATE fetches SET source=? WHERE source='comunicados' AND url LIKE ?", (sid, f"%/institucion/{slug}/%"))
            moved += len(rows)
        t.execute("DELETE FROM source_health WHERE source='comunicados'")
    return moved


# ── SIDPOL ─────────────────────────────────────────────────────────────────────
DATASET = "https://www.datosabiertos.gob.pe/dataset/denuncias-policiales-1"


def sidpol():
    """Revisa la página del dataset; si el CSV cambió (su nombre lleva el último mes), lo baja y agrega por provincia."""
    page = fetch_text("sidpol", DATASET, name="dataset.html")
    m = re.search(r'href="(https://www\.datosabiertos\.gob\.pe/sites/default/files/DATASET_Denuncias_Policiales[^"]*\.csv)"', page)
    if not m:
        raise RuntimeError("no se encontró el enlace al CSV en la página del dataset")
    url = html.unescape(m.group(1))
    meta = db.get_item("sidpol", "meta", "dataset")
    if meta and meta.get("url") == url:
        return meta["filas"], 0, f"sin cambios · datos hasta {meta['periodo']}"

    raw = fetch("sidpol", url, name="denuncias.csv", timeout=900).decode("utf-8-sig", "replace")
    provs = geo.provinces()
    series = collections.defaultdict(lambda: collections.defaultdict(dict))   # prov -> modalidad -> periodo -> n
    names, unmatched, filas, periodos, modalidades = {}, collections.Counter(), 0, set(), set()
    for r in csv.DictReader(io.StringIO(raw)):
        filas += 1
        ubigeo = r["UBIGEO_HECHO"].strip().zfill(6)            # el CSV pierde el cero inicial (10202 → 010202)
        prov, per, mod = ubigeo[:4], f"{int(r['ANIO']):04d}-{int(r['MES']):02d}", r["P_MODALIDADES"].strip()
        n = int(float(r["cantidad"] or 0))
        if prov not in provs:
            unmatched[prov] += n
            continue
        series[prov][mod][per] = series[prov][mod].get(per, 0) + n
        names[prov] = (r["PROV_HECHO"], r["DPTO_HECHO_NEW"])
        periodos.add(per)
        modalidades.add(mod)
    periodo = max(periodos)
    recs = {p: {"ubigeo": p, "provincia": names[p][0], "dpto_sidpol": names[p][1], "series": s} for p, s in series.items()}
    db.upsert_items("sidpol", "provincia", recs, snapshot=True)
    db.upsert_items("sidpol", "meta", {"dataset": {"url": url, "periodo": periodo, "desde": min(periodos), "filas": filas,
                                                    "modalidades": sorted(modalidades), "provincias": len(recs),
                                                    "sin_provincia": dict(unmatched)}})
    extra = f" · {sum(unmatched.values())} denuncias con ubigeo fuera de las 196 provincias" if unmatched else ""
    return filas, len(recs), f"nuevo archivo · {filas:,} filas · {min(periodos)} → {periodo} · {len(recs)} provincias{extra}"
