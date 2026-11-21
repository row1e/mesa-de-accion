"""Arma ref/vias.geojson, la capa de carreteras principales del mapa: la Red Vial Nacional del MTC (une las capitales
de departamento; no incluye vías departamentales ni vecinales). Se corre a mano:

  python vias_mapa.py

Fuente: MTC, Red Vial Nacional del Sistema Nacional de Carreteras al 31/12/2014, publicada por INGEMMET en
https://geocatmin.ingemmet.gob.pe/arcgis/rest/services/SERV_OTRAS_FUENTES/MapServer/23
Cada ruta (PE-1N, PE-3S…) se une en una línea, se simplifica (~1 km) y se redondea. Rango 1 = los tres ejes
longitudinales (PE-1 Panamericana, PE-3 Longitudinal de la Sierra, PE-5 Longitudinal de la Selva); 2 = transversales;
3 = ramales.
"""
import collections
import json
import pathlib
import re
import time
import urllib.request

from shapely.geometry import mapping, shape
from shapely.ops import linemerge

from mesa.http import SSL_CONTEXT     # certificados de los sitios del Estado que el colector ya usa

CAPA = "https://geocatmin.ingemmet.gob.pe/arcgis/rest/services/SERV_OTRAS_FUENTES/MapServer/23"
SALIDA = pathlib.Path(__file__).resolve().parent.parent / "ref" / "vias.geojson"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "MesaDeAccion/1.0 (capa de carreteras)"})
    for intento in range(3):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=180, context=SSL_CONTEXT).read())
        except Exception:  # noqa: BLE001
            if intento == 2:
                raise
            time.sleep(5 * (intento + 1))


def _redondear(g, n=3):
    if isinstance(g, (list, tuple)):
        return [_redondear(x, n) for x in g] if g and isinstance(g[0], (list, tuple)) else [round(g[0], n), round(g[1], n)]
    return g


def main():
    ids = sorted(_get(f"{CAPA}/query?where=1%3D1&returnIdsOnly=true&f=json")["objectIds"])
    por_ruta = collections.defaultdict(list)
    for i in range(0, len(ids), 300):
        lote = ",".join(map(str, ids[i:i + 300]))
        d = _get(f"{CAPA}/query?objectIds={lote}&outFields=CCODRUTA_4&returnGeometry=true&outSR=4326&f=geojson")
        for f in d.get("features", []):
            if not f.get("geometry"):
                continue
            ruta = str(f["properties"].get("CCODRUTA_4") or "").strip().upper()
            por_ruta[ruta].append(shape(f["geometry"]))
        time.sleep(1)
    out = []
    for ruta, geoms in sorted(por_ruta.items()):
        partes = [g for gg in geoms for g in (getattr(gg, "geoms", None) or [gg])]
        g = linemerge(partes).simplify(0.01, preserve_topology=False)
        if g.is_empty:
            continue
        m = mapping(g)
        try:
            ruta = ruta.encode("cp1252").decode("utf-8")       # el servicio devuelve "Ñ" mal codificada ("Ã‘")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        # 1 = ejes longitudinales; 2 = transversales (PE-02, PE-04…: dos dígitos y nada más); 3 = ramales (PE-1NA, PE-04B…)
        rango = 1 if ruta in ("PE-1N", "PE-1S", "PE-3N", "PE-3S", "PE-5N", "PE-5S") else 2 if re.fullmatch(r"PE-\d{2}", ruta) else 3
        out.append({"type": "Feature", "properties": {"ruta": ruta or None, "r": rango},
                    "geometry": {"type": m["type"], "coordinates": _redondear(m["coordinates"])}})
    SALIDA.write_text(json.dumps({"type": "FeatureCollection", "features": out}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(ids)} tramos MTC → {len(out)} rutas · {SALIDA.stat().st_size // 1024} KB · ejes: {[f['properties']['ruta'] for f in out if f['properties']['r'] == 1]}")


if __name__ == "__main__":
    main()
