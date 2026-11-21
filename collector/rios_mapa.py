"""Arma ref/rios.geojson, la capa de ríos del mapa. Se corre a mano (no hace falta repetirlo):

  python rios_mapa.py ne_10m_rivers_lake_centerlines.geojson

Dos fuentes, porque ninguna cubre sola todo el país:
- Natural Earth (dominio público), ríos grandes: Amazonas, Marañón, Ucayali, Huallaga, Madre de Dios, Napo…
  https://www.naturalearthdata.com/ (ne_10m_rivers_lake_centerlines)
- ANA, ríos principales de sus cuencas modeladas (costa y sierra: Rímac, Chillón, Piura, Tumbes, Mantaro, Urubamba…)
  https://snirhgeo.ana.gob.pe/server/rest/services/SSDH/Rios_Cuencas_WEAP/FeatureServer/0
Se recortan al Perú, se simplifican (~1 km) y se redondean para que pesen poco.
"""
import json
import pathlib
import sys
import urllib.request

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from mesa import geo

SALIDA = pathlib.Path(__file__).resolve().parent.parent / "ref" / "rios.geojson"
ANA = "https://snirhgeo.ana.gob.pe/server/rest/services/SSDH/Rios_Cuencas_WEAP/FeatureServer/0/query"


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
    SALIDA.write_text(json.dumps({"type": "FeatureCollection", "features": out}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(out)} tramos · {SALIDA.stat().st_size // 1024} KB · ríos: {sorted({f['properties']['n'] for f in out if f['properties']['n']})}")


if __name__ == "__main__":
    main(sys.argv[1])
