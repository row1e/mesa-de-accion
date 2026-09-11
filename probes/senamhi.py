"""Probe SENAMHI: avisos meteorológicos (lista + polígonos), pronóstico por ciudad, UV por provincia, avisos hidrológicos.

Uso:  python3 probes/senamhi.py
Guarda muestras en samples/senamhi/<fecha>/ e imprime un resumen.
"""
import collections
import html
import re

from common import fetch, fetch_json, save

BASE = "https://www.senamhi.gob.pe"
WFS = "https://idesep.senamhi.gob.pe/geoserver/g_aviso/ows"
SRC = "senamhi"


def strip(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def avisos_lista():
    """Tabla completa de avisos (2021→hoy) con nivel. ~1.2 MB HTML."""
    page = fetch(f"{BASE}/?p=aviso-meteorologico").decode("utf-8", "replace")
    out = []
    for row in re.findall(r"<tr>\s*<td.*?</tr>", page, re.S):
        tds = [strip(t) for t in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(tds) < 7:
            continue
        link = re.search(r'href="([^"]+)"', row)
        m = re.match(r"(\d+)\s*\((\w+)\)", tds[1])
        nro, estado = (int(m.group(1)), m.group(2)) if m else (None, tds[1])
        out.append({
            "nro": nro,
            "estado": estado,
            "titulo": tds[0],
            "emision": tds[2],
            "inicio": tds[3],
            "fin": tds[4],
            "duracion": tds[5],
            "nivel": tds[6],
            "link": html.unescape(link.group(1)) if link else None,
        })
    return out


def aviso_poligonos(nro, mapa, anio):
    """Polígonos del aviso para un día (mapa=1..3). nivel: 'Nivel 1'..'Nivel 4'."""
    url = (f"{WFS}?service=WFS&version=1.0.0&request=GetFeature&typeName=g_aviso:view_aviso"
           f"&viewparams=qry:{nro}_{mapa}_{anio}&outputFormat=application/json")
    return fetch_json(url, timeout=120)


def aviso_provincias(nro, mapa, anio):
    """Lista textual de departamentos/provincias de posible afectación (página del mapa; viene en Latin-1)."""
    raw = fetch(f"{BASE}/mapas/mapa-avisos-meteorologicos/index.php?av={nro}&nl=4&mp={mapa}&fc={anio}")
    page = raw.decode("utf-8", "replace")
    if "�" in page:
        page = raw.decode("latin-1")
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " | ", page)))
    text = re.sub(r"(\|\s*)+", "| ", text)

    def grab(label, nxt):
        m = re.search(label + r"(.*?)" + nxt, text)
        return [x.strip(" ,") for x in m.group(1).split("|") if x.strip(" ,")] if m else []

    return {
        "departamentos": grab("DEPARTAMENTOS DE POSIBLE AFECTACIÓN:", "PROVINCIAS DE POSIBLE"),
        "provincias": grab("PROVINCIAS DE POSIBLE AFECTACIÓN:", "DESCARGAR"),
    }


def pronostico_ciudades():
    """Pronóstico a 3 días para ~277 ciudades (Tmax, Tmin, descripción)."""
    page = fetch(f"{BASE}/?p=pronostico-meteorologico").decode("utf-8", "replace")
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ",
                        re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S))))
    dias = r"(?:lunes|martes|miércoles|jueves|viernes|sábado|domingo)"
    out = []
    for m in re.finditer(rf"([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ .'()-]+?) - ([A-ZÁÉÍÓÚÑ ]+?) (?={dias},)", text):
        seg = text[m.end(): m.end() + 900]
        for d in re.finditer(rf"({dias}, \d+ de \w+) (-?\d+)ºC (-?\d+)ºC (.*?)(?= {dias},|[A-ZÁÉÍÓÚÑ]{{3,}}[A-ZÁÉÍÓÚÑ .'()-]* - |$)", seg):
            out.append({"ciudad": m.group(1).strip(), "departamento": m.group(2).strip(),
                        "dia": d.group(1), "tmax": int(d.group(2)), "tmin": int(d.group(3)),
                        "descripcion": d.group(4).strip()})
    return out


def uv_provincias():
    """Índice UV pronosticado por provincia (c_cod_zona = ubigeo provincial INEI de 4 dígitos)."""
    return fetch_json(f"{BASE}/usr/dms/modelo/iuv/prono_ruv.json")


def avisos_hidrologicos():
    """Avisos hidrológicos vigentes por estación (GeoJSON con nivel, color, dpto/prov/distrito, cuenca)."""
    return fetch_json(f"{BASE}/mapas/mapa-aviso-hidro/include/ajaxIdesepWFSAvisos.php", method="POST")


def main():
    lista = avisos_lista()
    save(SRC, "avisos_lista.json", lista)
    print(f"[avisos] {len(lista)} avisos | niveles {dict(collections.Counter(a['nivel'] for a in lista))}")
    vigentes = [a for a in lista if a["estado"] in ("vigente", "emitido")]
    print(f"[avisos] vigentes/emitidos: {len(vigentes)}")
    for a in vigentes:
        anio = a["emision"][:4]
        for mapa in (1, 2, 3):  # un mapa por día de vigencia (máx. 3)
            polys = aviso_poligonos(a["nro"], mapa, anio)
            if not polys["features"]:
                break
            provs = aviso_provincias(a["nro"], mapa, anio)
            save(SRC, f"aviso_{a['nro']}_{mapa}_{anio}.geojson", polys)
            save(SRC, f"aviso_{a['nro']}_{mapa}_{anio}_provincias.json", provs)
            niveles = sorted({f["properties"]["nivel"] for f in polys["features"]})
            dia = polys["features"][0]["properties"]["fech_ini"][:10]
            print(f"   #{a['nro']} día {mapa} ({dia}) {a['nivel']:8} {a['titulo'][:50]:50} "
                  f"| polígonos={len(polys['features'])} {niveles} | dptos={len(provs['departamentos'])} provs={len(provs['provincias'])}")

    pron = pronostico_ciudades()
    save(SRC, "pronostico_ciudades.json", pron)
    print(f"[pronóstico] {len(pron)} filas, {len({p['ciudad'] for p in pron})} ciudades, "
          f"{len({p['departamento'] for p in pron})} dptos")

    uv = uv_provincias()
    save(SRC, "uv_provincias.json", uv)
    print(f"[uv] {len(uv)} zonas (ubigeo provincial), emitido {uv[0]['d_fec_pron'] if uv else '—'}")

    hidro = avisos_hidrologicos()
    save(SRC, "avisos_hidrologicos.geojson", hidro)
    print(f"[hidro] {len(hidro['features'])} avisos por estación: "
          + ", ".join(f"{f['properties']['color_text']} {f['properties']['nom_estacion']}" for f in hidro["features"]))


if __name__ == "__main__":
    main()
