"""Geoservicios del INDECI (geosinpad.indeci.gob.pe): recursos para responder e historial de emergencias del SINPAD.

Son las capas ArcGIS abiertas que alimentan el visor público del INDECI (geo.indeci.gob.pe/visorindeci). No piden
clave, salvo `Multisectorial/INDECI`, que aquí no se usa.

Recursos (`indeci_recursos`): maquinaria, albergues, zonas seguras, helipuntos y puntos de agua que el Estado
declaró para la respuesta al Fenómeno El Niño. Hay tres niveles, según cómo lo publica cada entidad:
  · distrito     → inventario de maquinaria de Vivienda (MVCS), con fecha de carga;
  · provincia    → maquinaria de los gobiernos regionales de Piura, Tumbes, La Libertad y Lambayeque;
  · departamento → maquinaria de MINDEF, MTC (PROVIAS Nacional y DGPPT) y Vivienda;
  · punto        → albergues, zonas seguras, helipuntos e II.EE. refugio (Piura), albergues y agua (Lima/Callao).
Es lo DECLARADO por cada entidad, no lo disponible hoy: así se presenta en la Mesa. Cada registro lleva la fecha
de la capa cuando el servidor la publica (las capas "Hosted" y el inventario MVCS); si no, queda en None.

Historial SINPAD (`sinpad_historial`): las ~200 mil emergencias registradas en el SINPAD con las cifras de la
ficha EDAN (personas, viviendas, aulas, salud, vías, agua, cultivos). La capa es una copia que el INDECI
refresca cada pocos meses, no el SINPAD en vivo: la Mesa guarda la fecha de la emergencia más reciente como
"fecha de corte" y la muestra junto a cada cifra. El servidor rechaza los filtros por atributo (error 400), así que
se pagina la capa completa (unos 5 minutos) y se agrega por distrito aquí.
"""
import collections
import datetime
import re
from zoneinfo import ZoneInfo

from .. import db, geo
from ..http import fetch_json

LIMA = ZoneInfo("America/Lima")
BASE = "https://geosinpad.indeci.gob.pe/indeci/rest/services"
VISOR = "https://geo.indeci.gob.pe/visorindeci/"
SINPAD = f"{BASE}/Emergencias/EMERGENCIAS_SINPAD/MapServer/0"

# ── Recursos ─────────────────────────────────────────────────────────────────────────────────────────────────

DISTRITAL = ("Multisectorial/SDE_MVCS_INVENTARIO_RECURSOS_DISTRITAL/MapServer/0", "Vivienda (MVCS)")
DEPARTAMENTAL = [("Hosted/maquinariasMINDEF/FeatureServer/0", "MINDEF · Ejército"),
                 ("Hosted/maquinariasMTC_PVN/FeatureServer/0", "MTC · PROVIAS Nacional"),
                 ("Hosted/maquinariasMTC_DGPPT/FeatureServer/0", "MTC · DGPPT"),
                 ("Hosted/maquinariasMVCS/FeatureServer/0", "Vivienda (MVCS)")]
PROVINCIAL = [("Recursos_GORE/Maquinarias/MapServer/22", "Gobierno Regional de Piura"),
              ("Hosted/Tumbes_recursos_DepProv/FeatureServer/0", "Gobierno Regional de Tumbes"),
              ("Hosted/LaLibertad_recursos_DepProv/FeatureServer/0", "Gobierno Regional de La Libertad"),
              ("Hosted/Lambayeque_recursos_DepProv/FeatureServer/0", "Gobierno Regional de Lambayeque")]
# (capa, tipo, etiqueta, entidad, campo de aforo, campo de carpas)
PUNTOS = [("Recursos_GORE/Piura/MapServer/7", "albergue", "Albergue", "Gobierno Regional de Piura", "AFORO_POB", "NÚM_CARPAS"),
          ("Recursos_GORE/Piura/MapServer/12", "zona_segura", "Zona segura", "Gobierno Regional de Piura", "AFORO_POB", "NÚM_CARPAS"),
          ("Recursos_GORE/Piura/MapServer/9", "helipunto", "Helipunto", "Gobierno Regional de Piura", None, None),
          ("Recursos_GORE/Piura/MapServer/10", "refugio_ie", "Institución educativa refugio", "Gobierno Regional de Piura", "AFORO_ESTU", None),
          ("Recursos_GORE/LimaMCallao/MapServer/17", "albergue", "Albergue temporal", "Lima Metropolitana y Callao", None, None),
          ("Recursos_GORE/LimaMCallao/MapServer/19", "agua", "EPS · agua potable", "Lima Metropolitana y Callao", None, None),
          ("Recursos_GORE/LimaMCallao/MapServer/20", "agua", "Pozo operativo SEDAPAL", "Lima Metropolitana y Callao", None, None)]
TIPOS = {"maquinaria": "Maquinaria y vehículos", "albergue": "Albergues", "zona_segura": "Zonas seguras",
         "helipunto": "Helipuntos", "refugio_ie": "II.EE. refugio", "agua": "Puntos de agua"}

# Columnas numéricas que no son unidades de maquinaria
_NO_UNIDAD = re.compile(r"^(fid|objectid.*|gmlid|form_.*|shape.*|pob_.*|total|cod_.*|ubigeo.*)$", re.I)
# Nombres de columna truncados a 10 letras (shapefile): solo se expanden los que no admiten dudas
_ETIQUETAS = {"retroexcav": "retroexcavadora", "motonivela": "motoniveladora", "cargador_f": "cargador frontal",
              "minicargad": "minicargador", "electrobom": "electrobomba", "camioneta": "camioneta", "volquete": "volquete",
              "excavadora": "excavadora", "motobomba": "motobomba", "compresora": "compresora", "hidrojet": "hidrojet",
              "blader": "blader", "bladers": "blader", "vehiculos": "vehículos", "maquinas": "máquinas", "equipo": "equipos",
              "camion_vo": "camión volquete", "camion_vol": "camión volquete", "camion_ci": "camión cisterna",
              "camion_cis": "camión cisterna", "camion": "camión", "camionetas": "camionetas", "cisterna_d": "cisterna",
              "tractor_so": "tractor sobre orugas", "rodillo_co": "rodillo compactador", "camion_pl": "camión plataforma",
              "camion_gru": "camión grúa", "plataforma": "plataforma", "remolcador": "remolcador", "torre_de_i": "torre de iluminación",
              "maquina_es": "otras máquinas", "tractor_a": "tractor", "tractor__1": "tractor", "tractor": "tractor"}


def _layer(path, name):
    return fetch_json("indeci_recursos", f"{BASE}/{path}?f=json", name=f"{name}_meta.json", timeout=60)


def _features(path, name, geometry=False):
    url = (f"{BASE}/{path}/query?where=1%3D1&outFields=*&returnGeometry={'true' if geometry else 'false'}"
           f"&outSR=4326&f=json")
    d = fetch_json("indeci_recursos", url, name=f"{name}.json", timeout=120)
    if d.get("error"):
        raise RuntimeError(f"{path}: {d['error'].get('message')}")
    return d.get("features") or []


def _fecha_ms(v):
    return datetime.datetime.fromtimestamp(v / 1000, LIMA).date().isoformat() if isinstance(v, (int, float)) and v > 0 else None


def _etiqueta(alias):
    a = (alias or "").strip().lower()
    clave = re.sub(r"[\s_]+", "_", a.translate(str.maketrans("áéíóú", "aeiou"))).strip("_")
    return _ETIQUETAS.get(clave) or _ETIQUETAS.get(a) or re.sub(r"_+\d*$", "", a).replace("_", " ").strip()


def _unidades(meta, attrs):
    """Columnas de maquinaria con valor > 0 → [[etiqueta, cantidad], …] y el total (el de la capa si lo trae)."""
    campos = [f for f in meta.get("fields") or [] if f["type"] in ("esriFieldTypeInteger", "esriFieldTypeSmallInteger",
                                                                     "esriFieldTypeDouble") and not _NO_UNIDAD.match(f["name"])]
    suma = collections.Counter()
    for f in campos:   # columnas distintas pueden tener la misma etiqueta (TRACTOR_A, TRACTOR__1): se suman
        if (attrs.get(f["name"]) or 0) > 0:
            suma[_etiqueta(f.get("alias") or f["name"])] += int(attrs[f["name"]])
    det = [[k, v] for k, v in suma.most_common()]
    total = next((attrs[k] for k in attrs if k.lower() == "total" and attrs[k]), None)
    return det, int(total) if total else sum(n for _, n in det)


def _ccdd(attrs):
    v = next((attrs[k] for k in ("ccdd", "CCDD", "ubigeo_dep", "UBIGEO_DEP") if attrs.get(k)), None)
    return str(v).zfill(2)[:2] if v else geo.region_code(attrs.get("nombdep") or attrs.get("NOMBDEP") or attrs.get("departamen") or "")


def recursos():
    recs, capas = {}, []

    # distrito: inventario MVCS ("CAMIONETA (3), VOLQUETE (2)…")
    path, entidad = DISTRITAL
    for f in _features(path, "mvcs_distrital"):
        a = f["attributes"]
        ub = str(a.get("UBIGEO") or "").zfill(6)
        if len(ub) != 6 or ub == "000000":
            continue
        det = [[m.group(1).strip().lower(), int(m.group(2))] for m in re.finditer(r"([^,()]+)\((\d+)\)", a.get("CONSOLIDADO_TIPOUNIDAD") or "")]
        recs[f"mvcs-dist-{ub}"] = {"tipo": "maquinaria", "nivel": "distrito", "entidad": entidad, "ubigeo": ub, "prov": ub[:4], "reg": ub[:2],
                                   "dep": a.get("DEPARTAMENTO"), "prov_nombre": a.get("PROVINCIA"), "dist": a.get("DISTRITO"),
                                   "nombre": f"Maquinaria de Vivienda en {a.get('DISTRITO') or ub}", "cantidad": int(a.get("CANTIDAD_TOTAL") or 0),
                                   "detalle": det, "fecha_capa": _fecha_ms(a.get("FECHA_CARGA")), "capa": path}
    capas.append(path)

    # departamento y provincia: una fila por ámbito con columnas por tipo de máquina
    for nivel, lista in (("departamento", DEPARTAMENTAL), ("provincia", PROVINCIAL)):
        for path, entidad in lista:
            name = path.split("/")[1]
            meta = _layer(path, name)
            fecha = _fecha_ms((meta.get("editingInfo") or {}).get("lastEditDate"))
            for f in _features(path, name):
                a = f["attributes"]
                det, total = _unidades(meta, a)
                if not total:
                    continue
                reg = _ccdd(a)
                if nivel == "provincia":
                    prov = str(next((a[k] for k in ("ubigeo_pro", "UBIGEO_PRO", "ubigeo", "UBIGEO", "cod_prov", "COD_PROV") if a.get(k)), "")).zfill(4)[:4]
                    if len(prov) != 4 or prov == "0000":
                        continue
                    lugar = a.get("nombprov") or a.get("NOMBPROV") or a.get("provincia") or a.get("PROVINCIA")
                    key, extra = f"{name}-{prov}", {"prov": prov, "reg": prov[:2], "prov_nombre": lugar}
                else:
                    if not reg:
                        continue
                    lugar = a.get("nombdep") or a.get("NOMBDEP") or a.get("departamen")
                    key, extra = f"{name}-{reg}", {"prov": None, "reg": reg}
                    if a.get("comando_de") or a.get("compañias"):
                        extra["unidades"] = ", ".join(x for x in (a.get("comando_de"), a.get("compañias")) if x)
                recs[key] = {"tipo": "maquinaria", "nivel": nivel, "entidad": entidad, "dep": a.get("nombdep") or a.get("NOMBDEP"),
                             "nombre": f"Maquinaria de {entidad} en {lugar}", "cantidad": total, "detalle": det,
                             "fecha_capa": fecha, "capa": path, **extra}
            capas.append(path)

    # puntos: albergues, zonas seguras, helipuntos, II.EE. refugio, agua
    for path, tipo, etiqueta, entidad, f_aforo, f_carpas in PUNTOS:
        name = path.replace("Recursos_GORE/", "").replace("/MapServer/", "_")
        for f in _features(path, name, geometry=True):
            a, g = f["attributes"], f.get("geometry") or {}
            lon, lat = g.get("x") or a.get("LONGITUD"), g.get("y") or a.get("LATITUD")
            if lon is None or lat is None:
                continue
            prov = geo.province_of_point(lon, lat)
            recs[f"{name}-{a['OBJECTID']}"] = {
                "tipo": tipo, "nivel": "punto", "entidad": entidad, "etiqueta": etiqueta,
                "nombre": (a.get("NOMBRE") or a.get("GOBIERNO_S") or etiqueta).strip(), "lon": round(lon, 5), "lat": round(lat, 5),
                "prov": prov, "reg": prov[:2] if prov else geo.region_of_point(lon, lat), "dist": a.get("DISTRITO"),
                "prov_nombre": a.get("PROVINCIA"), "direccion": (a.get("UBICACIÓN") or a.get("DIRECCION") or a.get("DIRECCIÓN") or "").strip() or None,
                "aforo": a.get(f_aforo) if f_aforo else None, "carpas": a.get(f_carpas) if f_carpas else None,
                "cantidad": 1, "fecha_capa": None, "capa": path}
        capas.append(path)

    if not recs:
        raise RuntimeError("los geoservicios del INDECI no trajeron recursos")
    total, new = db.upsert_items("indeci_recursos", "recurso", recs, snapshot=True)
    maq = sum(r["cantidad"] for r in recs.values() if r["tipo"] == "maquinaria")
    pts = collections.Counter(r["tipo"] for r in recs.values() if r["nivel"] == "punto")
    return total, new, (f"{nf(maq)} máquinas y vehículos declarados en {sum(1 for r in recs.values() if r['tipo'] == 'maquinaria')} registros; "
                        + ", ".join(f"{n} {TIPOS[t].lower()}" for t, n in pts.most_common()) + f" ({len(capas)} capas)")


def nf(n):
    return f"{n:,}".replace(",", " ")


def recursos_de(provs=None, reg=None):
    """Resumen para un ámbito: provincias (códigos de 4) o un departamento (código de 2).
    Junta lo distrital y provincial del ámbito con lo departamental de su región (eso no se puede repartir)."""
    items = db.get_items("indeci_recursos", "recurso", order="key")
    provs = set(provs or [])
    reg = reg or (next(iter(provs))[:2] if provs else None)
    propio = [r for r in items if (r.get("prov") in provs) or (not provs and r.get("reg") == reg and r["nivel"] != "departamento")]
    region = [r for r in items if r["nivel"] == "departamento" and r.get("reg") == reg]
    maq = [r for r in propio if r["tipo"] == "maquinaria"]
    puntos = [r for r in propio if r["nivel"] == "punto"]
    resumen = {t: {"n": sum(1 for r in puntos if r["tipo"] == t), "aforo": sum(r.get("aforo") or 0 for r in puntos if r["tipo"] == t)}
               for t in TIPOS if t != "maquinaria" and any(r["tipo"] == t for r in puntos)}
    fechas = sorted({r["fecha_capa"] for r in propio + region if r.get("fecha_capa")})
    return {"maquinaria": sorted(maq, key=lambda r: (r["nivel"] != "provincia", -r["cantidad"])),
            "maquinaria_region": sorted(region, key=lambda r: -r["cantidad"]),
            "n_maquinas": sum(r["cantidad"] for r in maq), "n_maquinas_region": sum(r["cantidad"] for r in region),
            "puntos": resumen, "puntos_lista": sorted(puntos, key=lambda r: (r["tipo"], -(r.get("aforo") or 0)))[:40],
            "fechas": [fechas[0], fechas[-1]] if fechas else None, "tipos": TIPOS, "visor": VISOR,
            "nota": ("Recursos declarados por cada entidad ante el FEN, publicados en los geoservicios del INDECI. "
                     "No equivalen a recursos disponibles hoy: el tablero del INDECI cuenta 6 190 asignados y solo 2 986 para despliegue.")}


# ── Historial SINPAD ─────────────────────────────────────────────────────────────────────────────────────────

CAMPOS = {"SAFECTA": "personas_afectadas", "SDAMNI": "personas_damnificadas", "SFALLE": "personas_fallecidas",
          "SLESI": "personas_heridas", "SDESA": "personas_desaparecidas", "SDESTRUVIVI": "viviendas_destruidas",
          "SINHABIVIVI": "viviendas_inhabitables", "SAFECTAVIVI": "viviendas_afectadas", "SAFEC_AULA": "aulas_afectadas",
          "SAFECTA_SALUD": "salud_afectados", "SPUENTE_AFECTA": "puentes_afectados", "SPUENTE_DESTRU": "puentes_destruidos",
          "SCARRE_AFECTA": "carreteras_afectadas_m", "SAREACULTI_AFECTA": "cultivo_afectado_ha", "SAREACULTI_PERDIDO": "cultivo_perdido_ha"}
LABELS = {"personas_afectadas": "personas afectadas", "personas_damnificadas": "personas damnificadas",
          "personas_fallecidas": "fallecidos", "personas_heridas": "heridos", "personas_desaparecidas": "desaparecidos",
          "viviendas_destruidas": "viviendas destruidas", "viviendas_inhabitables": "viviendas inhabitables",
          "viviendas_afectadas": "viviendas afectadas", "aulas_afectadas": "aulas afectadas", "salud_afectados": "establecimientos de salud afectados",
          "puentes_afectados": "puentes afectados", "puentes_destruidos": "puentes destruidos", "carreteras_afectadas_m": "m de carretera afectados",
          "cultivo_afectado_ha": "ha de cultivo afectadas", "cultivo_perdido_ha": "ha de cultivo perdidas"}
ORDEN = list(LABELS)
# Sin unidad publicada: carretera va en metros (como en los reportes del INDECI); agua potable y canales se dejan
# fuera porque la capa no dice si son sistemas, conexiones o metros.
# Lo que suele traer El Niño: lluvias, huaicos, inundaciones, deslizamientos, oleaje
FEN = re.compile(r"LLUVIA|HUAICO|INUNDA|DESLIZ|DERRUMBE|ALUD|EROSI|DESBORDE|MAREJADA|OLEAJE|TORMENTA|AVALANCHA|REPTACI|HUNDIMIENTO", re.I)
PAGINA = 5000


def _num(v):
    if not isinstance(v, (int, float)) or v <= 0:
        return 0
    return int(v) if float(v).is_integer() else round(v, 2)


def sinpad():
    campos = ",".join(["IDE_SINPAD", "FECHA", "COD_UBIGEO", "FENOMENO", "FLG_SITUACION", *CAMPOS])
    por = collections.defaultdict(lambda: {"n": 0, "n_fen": 0, "por_anio": collections.defaultdict(lambda: collections.Counter()),
                                           "fenomenos": collections.Counter(), "peores": [], "ultimo": None, "primero": None})
    # Rangos de OBJECTID en vez de resultOffset: la paginación por desplazamiento falla al azar (400) en este servidor.
    ids = fetch_json("sinpad_historial", f"{SINPAD}/query?where=1%3D1&returnIdsOnly=true&f=json", name="ids.json",
                     timeout=180, keep_raw=False).get("objectIds") or []
    if not ids:
        raise RuntimeError("la capa del SINPAD no devolvió identificadores")
    n, fecha_max = 0, None
    for i, desde in enumerate(range(min(ids), max(ids) + 1, PAGINA)):
        url = (f"{SINPAD}/query?where=OBJECTID%3E%3D{desde}+AND+OBJECTID%3C{desde + PAGINA}&outFields={campos}"
               f"&returnGeometry=false&f=json")
        for intento in range(3):
            d = fetch_json("sinpad_historial", url, name=f"pagina_{i:03d}.json", timeout=180, keep_raw=False)
            if not d.get("error"):
                break
        else:
            raise RuntimeError(f"SINPAD, página {i}: {d['error'].get('message')}")
        if d.get("exceededTransferLimit"):
            raise RuntimeError(f"SINPAD, página {i}: el servidor cortó la respuesta; bajar PAGINA")
        feats = d.get("features") or []
        for f in feats:
            a = f["attributes"]
            ub = str(a.get("COD_UBIGEO") or "")
            if len(ub) != 6 or not a.get("FECHA"):
                continue
            fecha = datetime.datetime.utcfromtimestamp(a["FECHA"] / 1000).date().isoformat()
            n += 1
            fecha_max = max(fecha_max or fecha, fecha)
            fen = (a.get("FENOMENO") or "").strip()
            cifras = {k: _num(a.get(c)) for c, k in CAMPOS.items() if _num(a.get(c))}
            x = por[ub]
            x["n"] += 1
            x["fenomenos"][fen] += 1
            es_fen = bool(FEN.search(fen))
            x["n_fen"] += es_fen
            anio = x["por_anio"][fecha[:4]]
            anio["n"] += 1
            anio["n_fen"] += es_fen
            anio.update(cifras)
            x["ultimo"] = max(x["ultimo"] or fecha, fecha)
            x["primero"] = min(x["primero"] or fecha, fecha)
            peso = cifras.get("personas_damnificadas", 0) + cifras.get("personas_afectadas", 0) + 100 * cifras.get("personas_fallecidas", 0)
            if peso:
                x["peores"].append((peso, {"fecha": fecha, "fenomeno": fen, "sinpad": int(a["IDE_SINPAD"]) if a.get("IDE_SINPAD") else None,
                                           "situacion": a.get("FLG_SITUACION"), "cifras": cifras}))
                if len(x["peores"]) > 20:
                    x["peores"] = sorted(x["peores"], key=lambda p: -p[0])[:5]
    if n < 0.9 * len(ids):
        raise RuntimeError(f"la capa del SINPAD trajo {n} de {len(ids)} emergencias")
    if n < 1000:
        raise RuntimeError(f"la capa del SINPAD trajo solo {n} emergencias")

    recs = {}
    for ub, x in por.items():
        recs[ub] = {"ubigeo": ub, "prov": ub[:4], "reg": ub[:2], "n": x["n"], "n_fen": x["n_fen"], "primero": x["primero"], "ultimo": x["ultimo"],
                    "por_anio": {y: dict(c) for y, c in sorted(x["por_anio"].items())},
                    "fenomenos": x["fenomenos"].most_common(6),
                    "peores": [p for _, p in sorted(x["peores"], key=lambda p: -p[0])[:5]]}
    recs["_corte"] = {"ubigeo": None, "fecha_corte": fecha_max, "n": n, "distritos": len(por),
                      "leido": datetime.datetime.now(LIMA).isoformat(timespec="minutes")}
    total, new = db.upsert_items("sinpad_historial", "distrito", recs, snapshot=True)
    return total, new, f"{nf(n)} emergencias en {len(por)} distritos; la más reciente es del {fecha_max} (corte de la copia del INDECI)"


def historial_de(provs=None, reg=None, desde_anio=2015):
    """Agrega el historial SINPAD de un ámbito (provincias o departamento) desde `desde_anio`."""
    provs = set(provs or [])
    corte = db.get_item("sinpad_historial", "distrito", "_corte")
    if not corte:
        return None
    filas = [d for d in db.get_items("sinpad_historial", "distrito", order="key")
             if d.get("ubigeo") and (d["prov"] in provs if provs else d["reg"] == reg)]
    if not filas:
        return {"fecha_corte": corte.get("fecha_corte"), "n": 0}
    anios = collections.defaultdict(collections.Counter)
    fen = collections.Counter()
    peores = []
    for d in filas:
        for y, c in d["por_anio"].items():
            if int(y) >= desde_anio:
                anios[y].update(c)
        for nombre, k in d["fenomenos"]:
            fen[nombre] += k
        peores += [{**p, "ubigeo": d["ubigeo"]} for p in d["peores"] if p["fecha"][:4] >= str(desde_anio)]
    peso = lambda p: p["cifras"].get("personas_damnificadas", 0) + p["cifras"].get("personas_afectadas", 0)  # noqa: E731
    total = collections.Counter()
    for c in anios.values():
        total.update(c)
    # el año más golpeado por lo que trae El Niño (damnificados + afectados)
    peor_anio = max(anios, key=lambda y: anios[y].get("personas_damnificadas", 0) + anios[y].get("personas_afectadas", 0), default=None)
    return {"fecha_corte": corte.get("fecha_corte"), "desde": desde_anio, "n_distritos": len(filas),
            "n": total.get("n", 0), "n_fen": total.get("n_fen", 0), "totales": {k: total[k] for k in ORDEN if total.get(k)},
            "por_anio": {y: dict(anios[y]) for y in sorted(anios)}, "peor_anio": peor_anio,
            "fenomenos": fen.most_common(8), "peores": sorted(peores, key=lambda p: -peso(p))[:8],
            "labels": LABELS, "orden": ORDEN, "fuente": SINPAD.rsplit("/query", 1)[0],
            "nota": ("Copia del SINPAD que publica el INDECI en sus geoservicios; se refresca cada pocos meses, así que lo más reciente "
                     "va en los reportes de emergencia. Cifras de la ficha EDAN de cada emergencia, sumadas por año.")}


# ── Para el tablero (snapshot) ───────────────────────────────────────────────────────────────────────────────

TABLERO_DESDE = "2010"
SINPAD_KEYS = ("personas_damnificadas", "personas_afectadas", "personas_fallecidas", "viviendas_destruidas", "viviendas_afectadas")


def para_tablero():
    """Lo que dibuja el tablero: recursos (puntos y maquinaria, compactos) e historial SINPAD por año a tres
    escalas (nacional, departamento, provincia). Todo sale de la base: no consulta al INDECI."""
    items = db.get_items("indeci_recursos", "recurso", order="key")
    puntos = [{k: r.get(k) for k in ("_key", "tipo", "etiqueta", "nombre", "lon", "lat", "prov", "reg", "dist", "aforo", "carpas", "entidad")}
              for r in items if r["nivel"] == "punto"]
    maq = [{k: r.get(k) for k in ("_key", "nivel", "entidad", "prov", "reg", "dist", "prov_nombre", "dep", "cantidad", "fecha_capa")}
           | {"detalle": (r.get("detalle") or [])[:6]} for r in items if r["tipo"] == "maquinaria"]
    fechas = sorted({r["fecha_capa"] for r in items if r.get("fecha_capa")})

    corte = db.get_item("sinpad_historial", "distrito", "_corte")
    sinpad = None
    if corte:
        nac = collections.defaultdict(collections.Counter)
        por_reg = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
        por_prov = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
        for d in db.get_items("sinpad_historial", "distrito", order="key"):
            if not d.get("ubigeo"):
                continue
            for y, c in d["por_anio"].items():
                if y < TABLERO_DESDE:   # antes de 2010 el registro es escaso: el gráfico engañaría
                    continue
                fila = {k: c.get(k, 0) for k in ("n", "n_fen", *SINPAD_KEYS)}
                nac[y].update(fila)
                por_reg[d["reg"]][y].update(fila)
                por_prov[d["prov"]][y].update(fila)
        # [n, n_fen, damnificados, afectados, fallecidos, viv. destruidas, viv. afectadas] por año: compacto para el navegador
        comp = lambda serie: {y: [int(round(c.get(k, 0))) for k in ("n", "n_fen", *SINPAD_KEYS)] for y, c in sorted(serie.items())}  # noqa: E731
        sinpad = {"fecha_corte": corte.get("fecha_corte"), "n": corte.get("n"), "desde": TABLERO_DESDE, "campos": ["n", "n_fen", *SINPAD_KEYS],
                  "nacional": comp(nac), "por_reg": {r: comp(s) for r, s in por_reg.items()},
                  "por_prov": {p: comp(s) for p, s in por_prov.items()}}
    return {"puntos": puntos, "maquinaria": maq, "fechas": [fechas[0], fechas[-1]] if fechas else None, "tipos": TIPOS,
            "visor": VISOR}, sinpad
