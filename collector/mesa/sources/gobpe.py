"""Comunicados y noticias de instituciones en gob.pe (JSON)."""
import datetime
import re

from .. import db
from ..http import fetch_json

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
