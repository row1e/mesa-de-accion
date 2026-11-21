"""Arma ref/rios.geojson, la capa de ríos del mapa. Se corre a mano (no hace falta repetirlo):

  python rios_mapa.py ne_10m_rivers_lake_centerlines.geojson

Tres fuentes, porque ninguna cubre sola todo el país:
- Natural Earth (dominio público), ríos grandes: Amazonas, Marañón, Ucayali, Huallaga, Madre de Dios, Napo…
  https://www.naturalearthdata.com/ (ne_10m_rivers_lake_centerlines)
- ANA, ríos principales de sus cuencas modeladas (costa y sierra: Rímac, Chillón, Piura, Tumbes, Mantaro, Urubamba…)
  https://snirhgeo.ana.gob.pe/server/rest/services/SSDH/Rios_Cuencas_WEAP/FeatureServer/0
- OpenStreetMap (Overpass), por nombre, los que faltan en las dos anteriores (OSM_RIOS): Apurímac, Ene, Perené, los
  ríos de la costa fuera de las cuencas de la ANA, etc. Una consulta por río, con pausa. © colaboradores de OpenStreetMap.
Se recortan al Perú, se simplifican (~1 km) y se redondean para que pesen poco.
"""
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

from shapely.geometry import LineString, mapping, shape
from shapely.ops import linemerge, unary_union

from mesa import geo

SALIDA = pathlib.Path(__file__).resolve().parent.parent / "ref" / "rios.geojson"
ANA = "https://snirhgeo.ana.gob.pe/server/rest/services/SSDH/Rios_Cuencas_WEAP/FeatureServer/0/query"
OVERPASS = "https://overpass-api.de/api/interpreter"
# (nombre en OSM sin "Río", rango 1 = grande). Fuera los nombres que se repiten en muchos lugares (Grande, Seco, Chico…).
OSM_RIOS = [(n, 1) for n in ("Apurímac", "Ene", "Perené", "Pachitea", "Aguaytía", "Inambari", "Tambopata", "Las Piedras", "Pastaza",
                             "Tigre", "Morona", "Corrientes", "Yavarí", "Urubamba", "Vilcanota", "Mantaro", "Pichis", "Manu")] + \
           [(n, 2) for n in ("Utcubamba", "Chamaya", "Crisnejas", "Chinchipe", "Cenepa", "Nieva", "Paucartambo", "Pampas", "Pachachaca",
                             "Colca", "Majes", "Camaná", "Ocoña", "Cotahuasi", "Acarí", "Yauca", "Chala", "Chaparra", "Ica", "Pisco",
                             "Cañete", "Mala", "Lurín", "Rímac", "Chillón", "Chancay", "Huaura", "Supe", "Pativilca", "Fortaleza",
                             "Huarmey", "Culebras", "Casma", "Nepeña", "Lacramarca", "Santa", "Chao", "Virú", "Moche", "Chicama",
                             "Jequetepeque", "Zaña", "La Leche", "Motupe", "Olmos", "Cascajal", "Piura", "Chira", "Tumbes", "Zarumilla",
                             "Moquegua", "Osmore", "Locumba", "Sama", "Caplina", "Chili", "Huallaga", "Marañón", "Ucayali", "Mayo",
                             "Biabo", "Ramis", "Ilave", "Coata", "Azángaro", "Huancané")]


def _osm(nombre):
    """Tramos del río con ese nombre dentro del Perú, unidos en líneas."""
    patron = "".join(f"[{c}{'íi' if c == 'i' else 'áa' if c == 'a' else 'ée' if c == 'e' else 'óo' if c == 'o' else 'úu' if c == 'u' else 'ñn' if c == 'n' else ''}]" if c in "aeioun" else c
                     for c in nombre.lower().translate(str.maketrans("áéíóúñ", "aeioun")))
    q = (f'[out:json][timeout:120];area["ISO3166-1"="PE"][admin_level=2]->.pe;'
         f'way["waterway"="river"]["name"~"^(R[ií]o )?{patron}$",i](area.pe);out geom;')
    req = urllib.request.Request(OVERPASS, data=urllib.parse.urlencode({"data": q}).encode(), headers={"User-Agent": "MesaDeAccion/1.0 (capa de rios)"})
    d = json.loads(urllib.request.urlopen(req, timeout=180).read())
    lineas = [LineString([(p["lon"], p["lat"]) for p in w["geometry"]]) for w in d.get("elements", []) if len(w.get("geometry") or []) > 1]
    return linemerge(lineas) if lineas else None


def _redondear(g, n=3):
    if isinstance(g, (list, tuple)):
        return [_redondear(x, n) for x in g] if g and isinstance(g[0], (list, tuple)) else [round(g[0], n), round(g[1], n)]
    return g


def _agregar(out, peru, geom, nombre, fuente, rango):
    g = shape(geom).intersection(peru).simplify(0.01, preserve_topology=False)
    if g.is_empty or g.length < 0.05:
        return
    m = mapping(g)
    out.append({"type": "Feature", "properties": {"n": nombre, "f": fuente, "r": rango},
                "geometry": {"type": m["type"], "coordinates": _redondear(m["coordinates"])}})


def main(ne_path):
    peru = unary_union([p["geom"] for p in geo.provinces().values()]).buffer(0.02)
    out = []
    ne = json.load(open(ne_path, encoding="utf-8"))
    for f in ne["features"]:
        p = f["properties"]
        if f["geometry"] and p.get("featurecla") == "River":
            _agregar(out, peru, f["geometry"], p.get("name_es") or p.get("name") or "", "Natural Earth", 1 if (p.get("scalerank") or 9) <= 5 else 2)
    q = urllib.request.Request(ANA + "?where=Jerarquia%3D%27Principal%27&outFields=Nombre&returnGeometry=true&outSR=4326&f=geojson",
                               headers={"User-Agent": "MesaDeAccion/1.0 (capa de rios)"})
    ana = json.loads(urllib.request.urlopen(q, timeout=120).read())
    for f in ana["features"]:
        if f["geometry"]:
            _agregar(out, peru, f["geometry"], f["properties"].get("Nombre") or "", "ANA", 2)
    # lo ya bajado de OSM en una corrida anterior se conserva: al repetir solo se piden los que faltan
    previo = json.loads(SALIDA.read_text(encoding="utf-8"))["features"] if SALIDA.exists() else []
    out += [f for f in previo if f["properties"].get("f") == "OpenStreetMap"]
    ya = {f["properties"]["n"].lower() for f in out if f["properties"]["n"]}
    for nombre, rango in OSM_RIOS:
        if nombre.lower() in ya:
            continue
        g = None
        for intento in range(3):              # el servidor público de Overpass a veces responde 502/504 si está cargado
            time.sleep(2 + 8 * intento)
            try:
                g = _osm(nombre)
                break
            except Exception as e:  # noqa: BLE001
                print(f"  error ({intento + 1}/3)", nombre, e, file=sys.stderr)
        else:
            continue
        if g is None:
            print("  sin datos en OSM:", nombre, file=sys.stderr)
            continue
        antes = len(out)
        _agregar(out, peru, mapping(g), nombre, "OpenStreetMap", rango)
        print(f"  {'ok' if len(out) > antes else 'muy corto'}: {nombre}", file=sys.stderr)
    SALIDA.write_text(json.dumps({"type": "FeatureCollection", "features": out}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(out)} tramos · {SALIDA.stat().st_size // 1024} KB · ríos: {sorted({f['properties']['n'] for f in out if f['properties']['n']})}")


if __name__ == "__main__":
    main(sys.argv[1])
