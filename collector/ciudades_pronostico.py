"""Arma ref/ciudades_senamhi.json: dónde está cada ciudad del pronóstico de SENAMHI (que solo trae nombre y
departamento), para la capa de temperaturas del mapa. Se corre a mano cuando SENAMHI agrega ciudades:

  python ciudades_pronostico.py snapshot.json      (snapshot.json = respuesta de /api/snapshot)

1. OpenStreetMap (Nominatim, una consulta por segundo, solo nombre de ciudad y departamento); el punto se acepta
   solo si cae dentro de ese departamento.
2. Si no aparece y la ciudad se llama como su provincia, el centro de la provincia (aproximado: en provincias
   grandes puede quedar a decenas de km). Lo que no pasa ninguna de las dos queda fuera de la capa.
"""
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

from mesa import geo

SALIDA = pathlib.Path(__file__).resolve().parent.parent / "ref" / "ciudades_senamhi.json"


def nominatim(q):
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q": q, "format": "jsonv2", "countrycodes": "pe", "limit": 5})
    req = urllib.request.Request(url, headers={"User-Agent": "MesaDeAccion/1.0 (gazetteer de ciudades del pronostico SENAMHI)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main(snapshot):
    D = json.load(open(snapshot, encoding="utf-8"))
    ciudades = sorted({(p["ciudad"], p["departamento"]) for p in D["pronostico"]})
    provs = geo.provinces()
    por_nombre = {(geo.norm(p["nombre"]), geo.norm(p["dpto"])): c for c, p in provs.items()}
    previo = json.loads(SALIDA.read_text(encoding="utf-8")) if SALIDA.exists() else {}
    out, sin = {}, []
    for ciudad, dpto in ciudades:
        k = f"{ciudad}|{dpto}"
        if previo.get(k, {}).get("como") == "OpenStreetMap":
            out[k] = previo[k]
            continue
        reg = geo.region_code(dpto)
        nombre = ciudad.split("/")[0].split("(")[0].strip()
        prov = por_nombre.get((geo.norm(nombre), geo.norm(dpto)))
        punto = None
        for q in (f"{nombre}, {dpto.title()}, Perú", f"{nombre}, Perú"):
            time.sleep(1.1)
            try:
                res = nominatim(q)
            except Exception as e:  # noqa: BLE001
                print("  error", q, e, file=sys.stderr)
                continue
            for r in res:
                lon, lat = float(r["lon"]), float(r["lat"])
                p = geo.province_of_point(lon, lat)
                if p and p[:2] == reg:
                    punto = {"lon": round(lon, 4), "lat": round(lat, 4), "como": "OpenStreetMap", "osm": r.get("display_name", "")[:120]}
                    break
            if punto:
                break
        if not punto and prov:
            punto = {"lon": round(provs[prov]["c"][0], 4), "lat": round(provs[prov]["c"][1], 4), "como": "centro de la provincia"}
        if punto:
            out[k] = punto
        else:
            sin.append(k)
        print(f"{'ok ' if punto else '-- '}{k}", file=sys.stderr)
    SALIDA.write_text(json.dumps(out, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    print(f"{len(out)} de {len(ciudades)} ciudades ubicadas · sin ubicar: {sin}")


if __name__ == "__main__":
    main(sys.argv[1])
