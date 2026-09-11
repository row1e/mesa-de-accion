"""Límites provinciales (INEI vía INGEMMET) y cruce aviso × provincia."""
import functools
import json
import re
import unicodedata

from shapely.geometry import shape
from shapely.validation import make_valid

from . import config


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().upper()


@functools.lru_cache(maxsize=1)
def boundaries():
    """(deps_geojson, provs_geojson) ya filtrados (sin lagos, código 99) y con propiedades compactas."""
    deps = json.loads((config.REF / "limites_0.geojson").read_text(encoding="utf-8"))
    provs = json.loads((config.REF / "limites_1.geojson").read_text(encoding="utf-8"))
    deps["features"] = [{"type": "Feature", "geometry": f["geometry"],
                         "properties": {"id": f["properties"]["CD_DEPA"], "n": f["properties"]["NM_DEPA"]}}
                        for f in deps["features"] if f["properties"]["CD_DEPA"] != "99"]
    provs["features"] = [{"type": "Feature", "geometry": f["geometry"],
                          "properties": {"id": f["properties"]["CD_PROV"], "n": f["properties"]["NM_PROV"],
                                         "d": f["properties"]["NM_DEPA"]}}
                         for f in provs["features"] if f["properties"]["CD_DEPA"] != "99"]
    return deps, provs


@functools.lru_cache(maxsize=1)
def provinces():
    """ubigeo -> {geom, nombre, dpto, c:[lon,lat]}"""
    out = {}
    for f in boundaries()[1]["features"]:
        g = make_valid(shape(f["geometry"]))
        pt = g.representative_point()
        out[f["properties"]["id"]] = {"geom": g, "nombre": f["properties"]["n"], "dpto": f["properties"]["d"],
                                      "c": [round(pt.x, 3), round(pt.y, 3)]}
    return out


@functools.lru_cache(maxsize=1)
def _name_index():
    by_pair, by_name = {}, {}
    for code, p in provinces().items():
        by_pair[(norm(p["dpto"]), norm(p["nombre"]))] = code
        by_name.setdefault(norm(p["nombre"]), []).append(code)
    return by_pair, by_name


def province_code(dpto, provincia):
    """Ubigeo provincial a partir de nombres (tolera tildes, 'LIMA METROPOLITANA')."""
    by_pair, by_name = _name_index()
    dp = norm(dpto).replace("LIMA METROPOLITANA", "LIMA")
    pv = norm(provincia)
    if not pv:
        return None
    return by_pair.get((dp, pv)) or (by_name[pv][0] if len(by_name.get(pv, [])) == 1 else None)


# ── Regiones (24 departamentos + Callao) ───────────────────────────────────────
REGION_ALIASES = {"LIMA METROPOLITANA": "15", "REGION LIMA": "15", "LIMA PROVINCIAS": "15", "LIMA / CALLAO": "15",
                  "PROV. CONST. DEL CALLAO": "07", "PROVINCIA CONSTITUCIONAL DEL CALLAO": "07"}


@functools.lru_cache(maxsize=1)
def _regions():
    """(nombre normalizado -> código, [(código, geometría preparada)])"""
    from shapely.prepared import prep
    names, geoms = dict(REGION_ALIASES), []
    for f in boundaries()[0]["features"]:
        names[norm(f["properties"]["n"])] = f["properties"]["id"]
        geoms.append((f["properties"]["id"], prep(make_valid(shape(f["geometry"])))))
    return names, geoms


def region_code(name):
    """Código de región (2 dígitos) a partir de un nombre de departamento en cualquiera de sus variantes."""
    n = norm(name).replace("DEPARTAMENTO DE ", "").strip(" .")
    names = _regions()[0]
    return names.get(n) or names.get(re.sub(r"^(REGION|DPTO\.?|DEP\.?)\s+", "", n))


def region_of_point(lon, lat):
    from shapely.geometry import Point
    pt = Point(lon, lat)
    return next((code for code, g in _regions()[1] if g.contains(pt)), None)


@functools.lru_cache(maxsize=1)
def _mention_patterns():
    pats = []
    for f in boundaries()[0]["features"]:
        n = norm(f["properties"]["n"])
        pats.append((f["properties"]["id"], re.compile(rf"\b{re.escape(n)}\b")))
    return pats


def regions_mentioned(text):
    """Regiones cuyo nombre aparece en un texto (detección por palabra completa, sin tildes)."""
    t = norm(text)
    return sorted({code for code, p in _mention_patterns() if p.search(t)})


def aviso_levels(geojson, min_share=config.MIN_SHARE):
    """Polígonos de un aviso-día → {ubigeo_prov: nivel_max} (solo niveles ≥ 2)."""
    out = {}
    provs = provinces()
    for feat in geojson.get("features", []):
        lvl = int(str(feat["properties"].get("nivel", "Nivel 1")).split()[-1])
        if lvl < 2 or not feat.get("geometry"):
            continue
        g = make_valid(shape(feat["geometry"]))
        for code, p in provs.items():
            if g.intersects(p["geom"]) and g.intersection(p["geom"]).area / p["geom"].area >= min_share:
                out[code] = max(out.get(code, 0), lvl)
    return out
