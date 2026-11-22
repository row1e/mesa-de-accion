"""Lluvia, nubes y viento pronosticados por provincia (Open-Meteo: modelos globales ECMWF, GFS y otros; referencial, no
es SENAMHI).

Una provincia no es un punto: en Maynas o en La Convención llueve muy distinto de una punta a la otra. Por eso se
consulta una malla de 0,5° (~55 km) sobre el Perú más el punto representativo de cada provincia (las chicas pueden
no tener ningún punto de la malla), y cada provincia toma el PROMEDIO de sus puntos para pintar el mapa y el MÁXIMO
para decir "hasta X mm". La malla queda guardada para la capa de viento.
"""
import datetime
import functools
import time

from shapely.geometry import Point
from shapely.ops import unary_union
from shapely.prepared import prep

from .. import db, geo
from ..http import fetch_json

LIMA = datetime.timezone(datetime.timedelta(hours=-5))
PASO = 0.5                 # grados de la malla
DIAS = 3                   # hoy y los dos siguientes
POR_PEDIDO = 120           # ubicaciones por consulta (la URL queda en ~4 KB)
PAUSA_S = 20               # el plan libre admite 600 ubicaciones por minuto: 120 cada 20 s quedan en 360
DIARIO = ("precipitation_sum", "precipitation_probability_max", "precipitation_hours", "cloud_cover_mean",
          "wind_speed_10m_max", "wind_gusts_10m_max", "wind_direction_10m_dominant")


@functools.lru_cache(maxsize=1)
def puntos():
    """[(lon, lat, prov)]: la malla dentro del Perú y, después, el punto representativo de cada provincia."""
    provs = geo.provinces()
    prep_provs = [(code, prep(p["geom"])) for code, p in provs.items()]
    pais = prep(unary_union([p["geom"] for p in provs.values()]).buffer(0.02))
    out = []
    lat = -18.5
    while lat <= 0.25:
        lon = -81.5
        while lon <= -68.5:
            pt = Point(lon, lat)
            if pais.contains(pt):
                prov = next((c for c, g in prep_provs if g.contains(pt)), None)
                if prov is None:   # en el borde (costa, frontera): la provincia más cercana
                    prov = min(provs, key=lambda c: provs[c]["geom"].distance(pt))
                out.append((round(lon, 2), round(lat, 2), prov))
            lon += PASO
        lat += PASO
    out += [(p["c"][0], p["c"][1], code) for code, p in provs.items()]
    return out


def _consultar(pts):
    url = ("https://api.open-meteo.com/v1/forecast?latitude=" + ",".join(str(p[1]) for p in pts)
           + "&longitude=" + ",".join(str(p[0]) for p in pts)
           + "&daily=" + ",".join(DIARIO) + f"&forecast_days={DIAS}&timezone=America%2FLima")
    data = fetch_json("clima_openmeteo", url, name=f"forecast_{pts[0][0]}_{pts[0][1]}.json", keep_raw=False, timeout=90)
    return data if isinstance(data, list) else [data]


def _r(v, n=1):
    return None if v is None else round(v, n)


def clima():
    pts = puntos()
    resp = []
    for i in range(0, len(pts), POR_PEDIDO):
        if i:
            time.sleep(PAUSA_S)
        resp += _consultar(pts[i:i + POR_PEDIDO])
    if len(resp) != len(pts):
        raise RuntimeError(f"Open-Meteo devolvió {len(resp)} de {len(pts)} puntos")
    fechas = resp[0]["daily"]["time"]
    n_malla = len(pts) - len(geo.provinces())
    por_prov = {}
    malla = []
    for k, ((lon, lat, prov), r) in enumerate(zip(pts, resp)):
        d = r["daily"]
        dias = [{"mm": _r(d["precipitation_sum"][j]), "prob": d["precipitation_probability_max"][j],
                 "horas": _r(d["precipitation_hours"][j], 0), "nubes": _r(d["cloud_cover_mean"][j], 0),
                 "viento": _r(d["wind_speed_10m_max"][j], 0), "rafaga": _r(d["wind_gusts_10m_max"][j], 0),
                 "dir": d["wind_direction_10m_dominant"][j]} for j in range(len(fechas))]
        por_prov.setdefault(prov, []).append(dias)
        if k < n_malla:
            malla.append({"lon": lon, "lat": lat, "prov": prov, "dias": dias})

    def media(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 1) if xs else None

    recs = {}
    for prov, lista in por_prov.items():
        # sin contar el punto representativo si la provincia ya tiene puntos de la malla: si no, una provincia grande
        # con 40 puntos y una chica con 1 pesarían distinto su centro
        malla_prov = lista[:-1] if len(lista) > 1 else lista
        dias = []
        for j, fecha in enumerate(fechas):
            col = [x[j] for x in malla_prov]
            mm = [x["mm"] for x in col if x["mm"] is not None]
            dias.append({"fecha": fecha, "mm": media(mm), "mm_max": max(mm) if mm else None,
                         "prob": max((x["prob"] for x in col if x["prob"] is not None), default=None),
                         "horas": media([x["horas"] for x in col]), "nubes": media([x["nubes"] for x in col]),
                         "viento": max((x["viento"] for x in col if x["viento"] is not None), default=None),
                         "rafaga": max((x["rafaga"] for x in col if x["rafaga"] is not None), default=None)})
        recs[prov] = {"prov": prov, "reg": prov[:2], "n_puntos": len(malla_prov), "dias": dias}
    emitido = datetime.datetime.now(LIMA).isoformat(timespec="minutes")
    recs["_malla"] = {"emitido": emitido, "fechas": fechas, "paso": PASO, "puntos": malla}
    total, new = db.upsert_items("clima_openmeteo", "provincia", recs, snapshot=True)
    top = max(((r["dias"][0]["mm"] or 0, p) for p, r in recs.items() if p != "_malla"), default=(0, "—"))
    nombre = geo.provinces().get(top[1], {}).get("nombre", top[1])
    return total, new, f"{len(pts)} puntos ({n_malla} de la malla) · más lluvia hoy: {top[0]} mm en {nombre} (modelo Open-Meteo)"
