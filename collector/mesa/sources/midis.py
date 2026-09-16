"""MIDIS · REDInforma — contexto social por distrito y por región.

REDInforma (https://app.midis.gob.pe/RedInforma/) publica dos reportes en Tableau:
"MIDIStrito" (id=18, distrital) y "Mi Región" (id=17, regional). No hay API documentada,
pero el propio botón de descarga del sitio llama a estos endpoints JSON sin autenticación:

    POST /RedInforma/Reporte/DownloadDatabase          {"codigoUbigeo": "150101"} → CSV distrital
    POST /RedInforma/Reporte/DownloadDatabaseMiregion  {"codigoUbigeo": "15"}     → CSV regional
    POST /RedInforma/Reporte/GetUbigeo                 {"codigoUbigeo": "00"}     → catálogo

`DownloadDatabase` solo acepta ubigeo distrital de 6 dígitos: con 2 o 4 dígitos devuelve el
encabezado sin filas. No existe descarga masiva.

Qué hace (y qué no hace) este módulo
------------------------------------
La fuente programada refresca **solo el catálogo de ubigeos** (~222 peticiones pequeñas, las
mismas que hace el desplegable del sitio al abrirse). NO recorre los 1.843 distritos.

Los indicadores se piden **por distrito, bajo demanda**, cuando alguien abre esa zona, y se
cachean 30 días: las fuentes del reporte son de 2017-2021 (Censo INEI 2017, INFOMIDIS 2021,
RENIPRES y HIS-MINSA 2020, MINEDU 2019-2021), así que es una línea de base, no un dato vivo.

Esa decisión es deliberada. El pie del sitio dice "Reservados todos los derechos" y la
interfaz pide registro para descargar, aunque el endpoint no lo exija. Mientras MIDIS no
autorice expresamente la recolección masiva, aquí se pide lo mismo que pediría una persona
usando el sitio, al mismo ritmo, y siempre citando la fuente con enlace al reporte original.
"""
import csv
import io
import json
import re
import time

from .. import db
from ..http import fetch

BASE = "https://app.midis.gob.pe/RedInforma/Reporte"
REPORTE_DISTRITO = "https://app.midis.gob.pe/RedInforma/Reporte/Reporte?id=18"
REPORTE_REGION = "https://app.midis.gob.pe/RedInforma/Reporte/Reporte?id=17"
JSON_HEADERS = {"Content-Type": "application/json; charset=utf-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": REPORTE_DISTRITO}
PAUSA = 0.15            # segundos entre peticiones al recorrer el catálogo
TTL_DATOS = 30 * 86400  # los indicadores casi no cambian; se recachean al mes


def _post(endpoint, ubigeo, *, keep_raw=True):
    """POST JSON a REDInforma. Devuelve el objeto decodificado."""
    body = json.dumps({"codigoUbigeo": ubigeo}).encode("utf-8")
    raw = fetch("midis", f"{BASE}/{endpoint}", method="POST", data=body, headers=JSON_HEADERS,
                name=f"{endpoint}_{ubigeo or 'vacio'}.json", keep_raw=keep_raw,
                key=f"{BASE}/{endpoint} {ubigeo}")   # el cuerpo distingue la petición, no la URL
    d = json.loads(raw)
    if d.get("Result") != "OK":
        raise RuntimeError(f"REDInforma devolvió Result={d.get('Result')!r} para {endpoint} {ubigeo!r}")
    return d


def _num(s):
    """'45,8000' → 45.8 · '268352' → 268352.0 · '' o texto → None (nunca 0)."""
    t = (s or "").strip().replace(" ", "")
    if not t:
        return None
    if re.fullmatch(r"-?\d+,\d+", t):          # coma decimal, como la usa el reporte
        t = t.replace(",", ".")
    if re.fullmatch(r"-?\d+(\.\d+)?", t):
        return float(t)
    return None


def _filas(csv_text):
    """El CSV viene con '; ' como separador. Devuelve dicts con las columnas del reporte."""
    rows = list(csv.reader(io.StringIO(csv_text), delimiter=";", skipinitialspace=True))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return [], 0
    head = [c.strip() for c in rows[0]]
    out, malas = [], 0
    for r in rows[1:]:
        if len(r) != len(head):                 # un ';' dentro de un campo desalinearía la fila
            malas += 1
            continue
        out.append({k: (v or "").strip() for k, v in zip(head, r)})
    return out, malas


# ── catálogo de ubigeos (fuente programada) ────────────────────────────────────

def _ubigeos(code, keep_raw=False):
    return _post("GetUbigeo", code, keep_raw=keep_raw).get("Records") or []


def catalogo():
    """Recorre departamentos → provincias → distritos y guarda el catálogo. No baja indicadores."""
    t0 = time.time()
    registros = {}
    deps = _ubigeos("00", keep_raw=True)
    for d in deps:
        dc, dnom = d["strCodUbi"], (d["strDesUbi"] or "").strip()
        time.sleep(PAUSA)
        for p in _ubigeos(dc):
            pc, pnom = p["strCodUbi"], (p["strDesUbi"] or "").strip()
            time.sleep(PAUSA)
            for x in _ubigeos(dc + pc):
                xc = x["strCodUbi"]
                registros[dc + pc + xc] = {
                    "ubigeo": dc + pc + xc, "distrito": (x["strDesUbi"] or "").strip(),
                    "provincia": pnom, "departamento": dnom,
                    "prov": dc + pc, "region": dc,
                }
    total, nuevos = db.upsert_items("midis", "distrito", registros, snapshot=True)
    provincias = len({r["prov"] for r in registros.values()})
    return total, nuevos, (f"{total} distritos en {provincias} provincias y {len(deps)} departamentos "
                           f"({nuevos} nuevos) en {time.time() - t0:.0f} s")


# ── indicadores bajo demanda ───────────────────────────────────────────────────

def distrito(ubigeo, fresh=False):
    """Indicadores del distrito (ubigeo de 6 dígitos), agrupados. Cacheado TTL_DATOS."""
    ubigeo = re.sub(r"\D", "", str(ubigeo or ""))
    if len(ubigeo) != 6:
        raise ValueError("MIDIS solo responde por distrito: se necesita un ubigeo de 6 dígitos")
    cache, fetched_at = db.detail_get("midis", "distrito", ubigeo, 0 if fresh else TTL_DATOS)
    if cache:
        return {**cache, "fetched_at": fetched_at, "cacheado": True}
    filas, malas = _filas(_post("DownloadDatabase", ubigeo)["csv"])
    grupos, fuentes = {}, {}
    for f in filas:
        g = f.get("vGrupo") or "Sin grupo"
        grupos.setdefault(g, []).append({
            "indicador": f.get("vIndicador"), "valor": _num(f.get("vValor")),
            "valor_texto": f.get("vTexto_valor") or None, "valor_crudo": f.get("vValor"),
            "periodo": (f.get("vPeriodo_Dato") or "").strip(" .") or None,
            "fuente": f.get("vfuente"),
        })
        if f.get("vfuente"):
            fuentes[f["vfuente"]] = fuentes.get(f["vfuente"], 0) + 1
    cab = filas[0] if filas else {}
    out = {
        "ubigeo": ubigeo, "distrito": cab.get("vDistrito"), "provincia": cab.get("vProvincia"),
        "departamento": cab.get("vDepartamento"),
        "n_indicadores": len(filas), "filas_descartadas": malas,
        "grupos": grupos, "fuentes": sorted(fuentes, key=fuentes.get, reverse=True),
        "atribucion": "MIDIS · REDInforma, reporte «MIDIStrito»",
        "enlace": REPORTE_DISTRITO,
    }
    db.detail_set("midis", "distrito", ubigeo, out)
    return {**out, "fetched_at": time.time(), "cacheado": False}


def region(code, fresh=False):
    """Indicadores regionales del reporte «Mi Región» (ubigeo de 2 dígitos)."""
    code = re.sub(r"\D", "", str(code or "")).zfill(2)[:2]
    cache, fetched_at = db.detail_get("midis", "region", code, 0 if fresh else TTL_DATOS)
    if cache:
        return {**cache, "fetched_at": fetched_at, "cacheado": True}
    filas, malas = _filas(_post("DownloadDatabaseMiregion", code)["csv"])
    grupos = {}
    for f in filas:
        g = f.get("vGrupo") or "Sin grupo"
        grupos.setdefault(g, []).append({
            "indicador": f.get("vIndicador"), "valor": _num(f.get("vValor")),
            "valor_crudo": f.get("vValor"),
            "periodo": (f.get("vPeriodo_Dato") or "").split(" ")[0] or None,
            "fuente": f.get("vfuente"),
        })
    out = {
        "region": code, "nombre": (filas[0].get("VRegion") if filas else None),
        "n_indicadores": len(filas), "filas_descartadas": malas, "grupos": grupos,
        "atribucion": "MIDIS · REDInforma, reporte «Mi Región»", "enlace": REPORTE_REGION,
        # Junín (12) y San Martín (22) devuelven cero filas y el nombre llega truncado
        # ("Juni", "San Marti"): parece un problema de codificación en origen, reportado a MIDIS.
        "sin_datos_en_origen": len(filas) == 0,
    }
    db.detail_set("midis", "region", code, out)
    return {**out, "fetched_at": time.time(), "cacheado": False}
