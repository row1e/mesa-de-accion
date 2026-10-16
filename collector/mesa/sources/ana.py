"""Obras de prevención de la ANA: puntos críticos priorizados 2026 (limpieza y descolmatación de ríos y quebradas) y
barreras dinámicas contra huaicos, con su avance, plazos, presupuesto y responsable.

Origen: el tablero público "Puntos Críticos Priorizados 2026" del SNIRH (https://snirhgeo.ana.gob.pe), que se arma con
capas ArcGIS abiertas. No responde a IPs de nubes: en un servidor así va por el relé peruano (MESA_PROXY).

La capa trae solo el avance actual de cada obra. Como se consulta cada hora, aquí se registra cada vez que la ANA
publica un avance nuevo (fecha y porcentajes): así se ve qué obras avanzaron en la semana y cuáles llevan días sin
reporte. "Vencida" = pasó su fecha de fin sin llegar al 100 %.
"""
import datetime
from zoneinfo import ZoneInfo

from .. import db
from ..http import fetch_json

LIMA = ZoneInfo("America/Lima")
SERVICIOS = "https://snirhgeo.ana.gob.pe/server/rest/services/PC_Priorizados_2026"
PUNTOS = f"{SERVICIOS}/Puntos_Criticos_Final_Priorizados_2026_Automatizado/FeatureServer/0"
BARRERAS = f"{SERVICIOS}/Barreras_Dinamicas_Programadas_2026/FeatureServer/0"
TABLERO = "https://snirhgeo.ana.gob.pe/portal/apps/dashboards/c1d16bc07ccd49769253c442a50ffec1"


def _q(capa, nombre):
    return fetch_json("ana_obras", f"{capa}/query?where=1%3D1&outFields=*&returnGeometry=true&outSR=4326&f=json",
                      name=nombre, timeout=120)


def _dmy(s):
    """'15/10/2026' → '2026-10-15'"""
    try:
        d, m, y = (s or "").strip()[:10].split("/")
        return datetime.date(int(y), int(m), int(d)).isoformat()
    except (ValueError, TypeError):
        return None


def _ms(v):
    return datetime.datetime.fromtimestamp(v / 1000, LIMA).date().isoformat() if isinstance(v, (int, float)) and v > 0 else None


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _hist(previo, actual):
    """Agrega una entrada al historial de avances cuando cambia la fecha de avance o algún porcentaje."""
    h = list((previo or {}).get("hist") or [])
    punto = [actual.get("fec_avance"), actual.get("avance_km_pct"), actual.get("avance_m3_pct"), actual.get("avance_ppto_pct")]
    if not h or h[-1] != punto:
        h.append(punto)
    return h[-40:]


def _estado(r, hoy):
    # avance principal: km del tramo (la meta de todos los puntos críticos está en km) o m² instalados en las barreras
    if (r.get("avance_km_pct") or 0) >= 100:
        return "Concluida"
    if r.get("fin") and r["fin"] < hoy:
        return "Vencida"
    return r.get("estado_ana") or "—"


def obras():
    hoy = datetime.datetime.now(LIMA).date().isoformat()
    recs = {}
    for f in _q(PUNTOS, "puntos_criticos.json").get("features") or []:
        a, g = f["attributes"], f.get("geometry") or {}
        lon, lat = a.get("LON") or g.get("x"), a.get("LAT") or g.get("y")
        if lon is None or lat is None:
            continue
        k = f"pc-{a['IDE_INTERVENCION']}"
        r = {"tipo": "punto_critico", "id": a["IDE_INTERVENCION"], "codigo": a.get("CODIGO"), "sector": a.get("SECTOR"),
             "rio": a.get("NOMBRE"), "fuente": a.get("FUENTE_NATURAL"), "cuenca": a.get("CUENCA"),
             "dep": a.get("nombdep"), "prov_nombre": a.get("nombprov"), "dist": a.get("nombdistri"),
             "lon": round(lon, 5), "lat": round(lat, 5), "actividad": a.get("NOMBRE_DE_ACTIVIDAD"), "estado_ana": a.get("ESTADO"),
             "meta_km": _num(a.get("META")), "avance_km": _num(a.get("AVANCEKM")), "avance_km_pct": _num(a.get("AVANCEKMPOR")),
             "meta_m3": _num(a.get("VOLUMEN_M3")), "avance_m3": _num(a.get("AVANCEDESCOLMA")), "avance_m3_pct": _num(a.get("AVANCEDESCOLMAPOR")),
             "costo": _num(a.get("COSTO_DE_LA_ACTIVIDAD")), "avance_ppto": _num(a.get("AVANCEPRESUPUESTO")),
             "avance_ppto_pct": _num(a.get("AVANCEPRESUPUESTOPOR")), "inicio": _dmy(a.get("FECHAINGRESO")), "fin": _dmy(a.get("FECHAFIN")),
             "mes_inicio": a.get("MES_INICIO_INTERVENCION"), "fec_avance": _dmy(a.get("FEC_AVANCE")),
             "peligro": a.get("PELIGRO"), "nivel_peligro": a.get("NIVEL_DE_PELIGRO"), "poblacion": a.get("POB"), "viviendas": a.get("VIVIENDAS"),
             "hectareas": _num(a.get("SUPERFICIE_HA")), "responsable": a.get("RESPONSABLE_MIDAGRI") or a.get("RESPONSABLE"),
             "decreto": a.get("DECRETO"), "ficha": a.get("RUTA"), "galeria": a.get("GALERIA") if a.get("FLG_GALERIA") else None}
        recs[k] = r
    for f in _q(BARRERAS, "barreras_dinamicas.json").get("features") or []:
        a, g = f["attributes"], f.get("geometry") or {}
        if g.get("x") is None:
            continue
        k = f"bd-{a['OBJECTID']}"
        recs[k] = {"tipo": "barrera", "id": a["OBJECTID"], "sector": a.get("SECTOR"), "rio": a.get("QUEBRADA"), "fuente": "Quebrada",
                   "cuenca": a.get("CUENCA"), "dep": a.get("nombdep") or a.get("DEPARTAMENTO"), "prov_nombre": a.get("nombprov") or a.get("PROVINCIA"),
                   "dist": a.get("nombdist") or a.get("DISTRITO"), "lon": round(g["x"], 5), "lat": round(g["y"], 5),
                   "actividad": a.get("DESCRIPC") or f"Barrera dinámica en la quebrada {a.get('QUEBRADA')}", "estado_ana": a.get("ESTADO"),
                   "avance_m2": _num(a.get("AVANCEM2")), "avance_m2_pct": _num(a.get("AVANCEM2POR")),
                   "avance_m3": _num(a.get("AVANCEM3")), "avance_m3_pct": _num(a.get("AVANCEM3POR")),
                   "avance_km_pct": None, "costo": _num(a.get("PRESUP_SOL")), "avance_ppto": _num(a.get("AVANCEPRESUPUESTOSOL")),
                   "avance_ppto_pct": _num(a.get("AVANCEPRESUPUESTOPOR")), "inicio": _ms(a.get("FEC_INI")), "fin": _ms(a.get("FEC_FIN")),
                   "plazo_dias": a.get("PLAZO_DIAS"), "fec_avance": None, "peligro": "Flujo de detritos (huaico)",
                   "nivel_peligro": (a.get("NIV_PELIG") or "").upper() or None, "poblacion": a.get("HABITANTES"), "viviendas": a.get("VIVIENDAS"),
                   "hectareas": _num(a.get("AREA_CULT")), "responsable": "ANA", "decreto": None, "ficha": a.get("FICHA"), "galeria": None}
    if not recs:
        raise RuntimeError("las capas de la ANA no trajeron obras")
    from .. import geo
    for k, r in recs.items():
        prov = geo.province_of_point(r["lon"], r["lat"])
        r["prov"], r["reg"] = prov, prov[:2] if prov else geo.region_of_point(r["lon"], r["lat"])
        # en las barreras el avance principal es el de la instalación (m²); en los puntos críticos, el de km del tramo
        if r["tipo"] == "barrera":
            r["avance_km_pct"] = r.get("avance_m2_pct")
        previo = db.get_item("ana_obras", "obra", k)
        r["hist"] = _hist(previo, r)
        r["estado"] = _estado(r, hoy)
    total, new = db.upsert_items("ana_obras", "obra", recs, snapshot=True)
    n = {e: sum(1 for r in recs.values() if r["estado"] == e) for e in ("En Ejecución", "Por Ejecutar", "Vencida", "Concluida")}
    nb = sum(1 for r in recs.values() if r["tipo"] == "barrera")
    return total, new, (f"{total - nb} puntos críticos y {nb} barreras dinámicas: {n['En Ejecución']} en ejecución, "
                        f"{n['Por Ejecutar']} por ejecutar, {n['Vencida']} vencidas, {n['Concluida']} concluidas")
