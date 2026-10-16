"""Mar: avisos especiales de oleaje y viento y pronósticos de olas de la DHN (Marina de Guerra), y alturas de ola de
Open-Meteo Marine como modelo de referencia aparte.

- Avisos (https://www.dhn.mil.pe/portal/avisos-especiales): el texto va en la página. Los de oleaje dicen la condición
  por tramo de litoral (norte: Tumbes–Salaverry; centro: al sur de Salaverry–San Juan de Marcona; sur: al sur de San
  Juan de Marcona–Tacna) con su intensidad y cómo cambia por día; los de viento, la intensidad y entre qué localidades.
  Se traducen a niveles por provincia costera y por día, como los avisos de SENAMHI: ligero / brisa fresca = 2,
  moderado / brisa fuerte = 3, fuerte o más / viento fuerte = 4.
- Pronósticos DHN (naylamp.dhn.mil.pe): mapas del modelo de olas cada 3 h hasta 120 h, por zona y por bahía. Son
  imágenes: se guardan sus direcciones para +0, +24, +48 y +72 h, no valores.
- Open-Meteo Marine: altura de ola máxima en puntos de la costa para las próximas 0–24, 24–48 y 48–72 h. No es un dato
  oficial; el tablero lo muestra separado y rotulado como modelo de referencia.
"""
import datetime
import email.utils
import html
import re
from zoneinfo import ZoneInfo

from .. import db
from ..http import fetch_json, fetch_text

LIMA = ZoneInfo("America/Lima")
AVISOS = "https://www.dhn.mil.pe/portal/avisos-especiales"
HOME = "https://www.dhn.mil.pe/portal/"
NAYLAMP = "https://www.naylamp.dhn.mil.pe"
MESES = {m: i for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                     "septiembre", "octubre", "noviembre", "diciembre"], 1)} | {"setiembre": 9}

# Provincias costeras de norte a sur, por tramo de litoral de los avisos de la DHN
COSTA = {
    "norte": ["2403", "2401", "2402", "2007", "2005", "2008", "1403", "1401", "1304", "1307", "1302", "1301"],
    "centro": ["1312", "0218", "0208", "0211", "1502", "1508", "1506", "0701", "1501", "1505", "1102", "1105", "1101", "1103"],
    "sur": ["0403", "0402", "0407", "1803", "2303", "2301"],
}
ZONA_NOMBRE = {"norte": "Litoral norte (Tumbes–Salaverry)", "centro": "Litoral centro (Salaverry–San Juan de Marcona)",
               "sur": "Litoral sur (San Juan de Marcona–Tacna)"}
ORDEN = COSTA["norte"] + COSTA["centro"] + COSTA["sur"]
# Localidades que nombran los avisos de viento → provincia costera
LOCALIDADES = {
    "puerto pizarro": "2401", "tumbes": "2401", "zorritos": "2402", "mancora": "2007", "máncora": "2007", "cabo blanco": "2007",
    "talara": "2007", "paita": "2005", "sechura": "2008", "bayovar": "2008", "bayóvar": "2008", "san jose": "1403", "san josé": "1403",
    "pimentel": "1401", "puerto eten": "1401", "eten": "1401", "pacasmayo": "1307", "malabrigo": "1302", "chicama": "1302",
    "huanchaco": "1301", "salaverry": "1301", "trujillo": "1301", "chimbote": "0218", "casma": "0208", "huarmey": "0211",
    "supe": "1502", "barranca": "1502", "huacho": "1508", "chancay": "1506", "ancon": "1501", "ancón": "1501", "callao": "0701",
    "lima": "1501", "chorrillos": "1501", "pucusana": "1501", "cerro azul": "1505", "cañete": "1505", "chincha": "1102",
    "tambo de mora": "1102", "pisco": "1105", "paracas": "1105", "san juan de marcona": "1103", "marcona": "1103",
    "lomas": "0403", "chala": "0403", "atico": "0403", "ocoña": "0402", "camana": "0402", "camaná": "0402", "quilca": "0402",
    "matarani": "0407", "mollendo": "0407", "ilo": "1803", "ite": "2303", "morro sama": "2301", "tacna": "2301",
}
NIVEL_OLEAJE = [(r"muy fuerte|fuerte", 4), (r"moderad[oa]", 3), (r"liger[oa]", 2)]
DIAS_SEMANA = r"(?:lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo)"


def _texto(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<br\s*/?>", "\n", s))).strip()


def _limpio(s):
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", s))))).strip()


def _validez(txt):
    """'Válido del 30 de setiembre al 01 de octubre del 2026' → ('2026-09-30', '2026-10-01')"""
    m = re.search(r"del?\s+(\d{1,2})(?:\s+de\s+(\w+))?(?:\s+(?:del?\s+)?(\d{4}))?\s+al?\s+(\d{1,2})\s+de\s+(\w+)\s+(?:del?\s+)?(\d{4})", txt, re.I)
    if not m or m[5].lower() not in MESES:
        return None, None
    y2, m2 = int(m[6]), MESES[m[5].lower()]
    m1 = MESES.get((m[2] or "").lower(), m2)
    y1 = int(m[3]) if m[3] else (y2 - 1 if m1 > m2 else y2)
    try:
        return datetime.date(y1, m1, int(m[1])).isoformat(), datetime.date(y2, m2, int(m[4])).isoformat()
    except ValueError:
        return None, None


def _dias(ini, fin):
    if not ini or not fin:
        return []
    a, b = datetime.date.fromisoformat(ini), datetime.date.fromisoformat(fin)
    return [(a + datetime.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)][:15]


def _fecha_de_dia(n, dias):
    """Número de día del texto ('viernes 16') → fecha dentro de la vigencia."""
    return next((d for d in dias if int(d[8:]) == n), None)


def _niveles_por_dia(texto, dias, escala):
    """Nivel por día a partir de la secuencia 'ligero desde el jueves 15 incrementando a moderado desde el viernes 16…'.
    Un cambio que empieza un día cuenta para ese día (vale el mayor del día)."""
    if not dias:
        return {}
    patron = "|".join(f"(?P<n{n}>{p})" for p, n in escala) + rf"|{DIAS_SEMANA}\s+(?P<dia>\d{{1,2}})"
    eventos, pendiente = [], None
    for m in re.finditer(patron, texto, re.I):
        if m["dia"]:
            f = _fecha_de_dia(int(m["dia"]), dias)
            if pendiente is not None and f:
                eventos.append((f, pendiente))
                pendiente = None
        else:
            nivel = next(n for _, n in escala if m[f"n{n}"])
            if pendiente is not None:          # dos intensidades seguidas ("ligero a moderado"): la mayor
                pendiente = max(pendiente, nivel)
            else:
                pendiente = nivel
    if pendiente is not None:
        eventos.append((eventos[-1][0] if eventos else dias[0], pendiente))
    if not eventos:
        return {}
    eventos.sort()
    out, activo = {}, None
    for d in dias:
        del_dia = [n for f, n in eventos if f == d]
        previos = [n for f, n in eventos if f < d]
        activo = previos[-1] if previos else activo
        niveles = [x for x in [activo, *del_dia] if x]
        if niveles:
            out[d] = max(niveles)
    return out


def _tramo(a, b, zona):
    """Provincias de la zona entre dos localidades ('al sur de Salaverry hasta Callao'), incluidas las de los extremos."""
    pa, pb = LOCALIDADES.get(a.strip().lower()), LOCALIDADES.get(b.strip().lower())
    if not pa or not pb:
        return None
    i, j = sorted((ORDEN.index(pa), ORDEN.index(pb)))
    return [p for p in ORDEN[i:j + 1] if p in COSTA[zona]] or None


def _zonas_oleaje(cuerpo, dias):
    """{'norte': {'texto', 'por_dia', 'max', 'provs': {prov: {dia: nivel}}}, ...} a partir de los literales a) b) c)
    (o a. b. c.). Un tramo puede venir partido ('Al sur de Salaverry hasta Callao: …'), cada parte con sus fechas."""
    zonas = {}
    partes = re.split(r"(?:^|\s)[a-d][).]\s*Litoral\s+", cuerpo, flags=re.I)
    for p in partes[1:]:
        nombre = p.split()[0].lower()
        z = "norte" if nombre.startswith("norte") else "centro" if nombre.startswith("centro") else "sur" if nombre.startswith("sur") else None
        if not z:
            continue
        texto = re.split(r"\s\d\.\s", p, maxsplit=1)[0]
        texto = re.sub(r"^\w+\s*\([^)]*\)\s*:?\s*", "", texto).strip()
        provs, total = {}, {}
        sub = [m for m in re.finditer(r"(?:al sur de|desde)\s+([\wáéíóúñ ]+?)\s+hasta\s+([\wáéíóúñ ]+?)\s*:\s*(.*?)(?=(?:[?•\-]\s*)?(?:al sur de|desde)\s+[\wáéíóúñ ]+?\s+hasta\s+[\wáéíóúñ ]+?\s*:|$)",
                                       texto, re.I | re.S)]
        segmentos = [(_tramo(m[1], m[2], z), m[3]) for m in sub] if sub else []
        if not segmentos or any(t is None for t, _ in segmentos):
            segmentos = [(COSTA[z], texto)]
        for tramo, txt in segmentos:
            pd = _niveles_por_dia(txt, dias, NIVEL_OLEAJE)
            for prov in tramo:
                for d, n in pd.items():
                    provs.setdefault(prov, {})[d] = max(n, provs.get(prov, {}).get(d, 0))
            for d, n in pd.items():
                total[d] = max(n, total.get(d, 0))
        zonas[z] = {"texto": re.sub(r"\s*\?\s*", " · ", texto).strip(" ·"), "por_dia": total, "max": max(total.values()) if total else None, "provs": provs}
    return zonas


def _viento(cuerpo, dias):
    """Provincias y nivel de un aviso de viento: 'brisa fresca' 2, 'brisa fuerte' 3, 'viento fuerte'/'temporal' 4
    (en la costa: lo que dice antes de 'mar abierto')."""
    costa = re.split(r"mar abierto", cuerpo, flags=re.I)[0].lower()
    nivel = 4 if re.search(r"viento fuerte|temporal", costa) else 3 if re.search(r"brisa (?:fresca a )?fuerte", costa) else 2
    nudos = re.search(r"hasta\s+(\d+)\s+nudos", costa)
    provs = []
    m = re.search(r"entre\s+(?:las\s+localidades\s+de\s+)?([\wáéíóúñ ]+?)\s+y\s+([\wáéíóúñ ]+?)(?:[;,.]|$)", costa)
    if m:
        a, b = LOCALIDADES.get(m[1].strip()), LOCALIDADES.get(m[2].strip())
        if a and b:
            i, j = sorted((ORDEN.index(a), ORDEN.index(b)))
            provs = ORDEN[i:j + 1]
    if not provs:
        for z in ("norte", "centro", "sur"):
            if re.search(rf"litoral[^.;]*\b{z}\b|\b{z}\b[^.;]*litoral", costa):
                provs += COSTA[z]
    return {"nivel": nivel, "nudos": int(nudos[1]) if nudos else None, "provs": provs or ORDEN,
            "por_dia": {d: nivel for d in dias}, "texto": cuerpo}


def _item(bloque):
    titulo = re.search(r'<div class="accordion-title"><span[^>]*>(.*?)</span>', bloque, re.S)
    if not titulo:
        return None
    t = _texto(titulo[1])
    m = re.match(r"AVISO ESPECIAL(?: DE (\w+))?\s+N[RO°º.]*\s*(\d+)(?:\s*-\s*(\d+))?\s*\(?(.*?)\)?$", t, re.I)
    if not m:
        return None
    cuerpo = _limpio((re.search(r'<div class="accordion-content">(.*)', bloque, re.S) or [None, ""])[1])
    # los avisos antiguos no dicen el tipo en el título: se deduce del texto
    tipo = (m[1] or ("OLEAJE" if re.search(r"oleaj", cuerpo, re.I) else "VIENTO" if re.search(r"\bviento|brisa", cuerpo, re.I) else "")).upper()
    if tipo not in ("OLEAJE", "VIENTO"):
        return None
    ini, fin = _validez(m[4]) if m[4] else (None, None)
    if not ini:
        ini, fin = _validez(cuerpo[:600])
    dias = _dias(ini, fin)
    emitido = re.search(r"Callao,\s*(\d{1,2})\s+de\s+(\w+)\s+(?:del?\s+)?(\d{4})", cuerpo)
    links = re.findall(r'<a href="([^"]+)"[^>]*class="cs-link', bloque)
    anio = m[3] or (fin or (emitido and emitido[3]) or "")[2:4] or "??"
    rec = {"tipo": tipo.lower(), "numero": f"{int(m[2]):02d}-{anio[-2:]}", "titulo": t, "validez": m[4], "inicio": ini, "fin": fin,
           "emitido": (datetime.date(int(emitido[3]), MESES.get(emitido[2].lower(), 1), int(emitido[1])).isoformat()
                       if emitido and emitido[2].lower() in MESES else None),
           "imagen": next((u for u in links if u.lower().endswith(".pdf") and "/Archivos/" in u), None),
           "pdf": next((u for u in links if "pdf-aviso-especial" in u), None), "texto": cuerpo}
    if tipo == "OLEAJE":
        zonas = _zonas_oleaje(cuerpo, dias)
        rec["zonas"] = zonas
        rec["provs_por_dia"] = {d: {p: pd[d] for z in zonas.values() for p, pd in z["provs"].items() if d in pd} for d in dias}
        for z in zonas.values():
            z.pop("provs")
    else:
        v = _viento(cuerpo, dias)
        rec |= {"nivel": v["nivel"], "nudos": v["nudos"], "provs": v["provs"],
                "provs_por_dia": {d: {p: v["nivel"] for p in v["provs"]} for d in dias}}
    rec["regs"] = sorted({p[:2] for por in rec["provs_por_dia"].values() for p in por})
    return rec


def avisos():
    page = fetch_text("dhn_avisos", AVISOS, name="avisos-especiales.html", timeout=90)
    recs = {}
    for bloque in page.split('<div class="position-relative">')[1:]:
        r = _item(bloque)
        if r and r["inicio"]:   # los anteriores a 2025 no traen vigencia legible: quedan fuera
            recs[f"{r['tipo']}-{r['numero']}"] = r
    if not recs:
        raise RuntimeError("la página no trajo avisos (¿cambió su formato?)")
    total, new = db.upsert_items("dhn_avisos", "aviso_dhn", recs)
    hoy = datetime.datetime.now(LIMA).date().isoformat()
    vig = [r for r in recs.values() if r["fin"] and r["inicio"] and r["inicio"] <= hoy <= r["fin"]]
    vig_txt = "; ".join(f"{r['tipo']} N° {r['numero']} hasta {r['fin'][8:]}/{r['fin'][5:7]}" for r in vig) or "ninguno"
    return total, new, f"{total} avisos leídos ({new} nuevos) · vigentes hoy: {vig_txt}"


# ── Pronósticos de olas de la DHN (mapas) ─────────────────────────────────────
HORAS = (0, 24, 48, 72)


def _img_de(url_producto):
    """Producto naylamp (…php?num=N) → dirección absoluta de su imagen."""
    s = fetch_text("dhn_pronosticos", url_producto, keep_raw=False, timeout=60)
    m = re.search(r'<img[^>]+src="([^"]+)"', s)
    if not m:
        return None
    src = m[1]
    if src.startswith("http"):
        return src
    rel = src
    base = url_producto.split("?")[0].rsplit("/", 1)[0]
    while rel.startswith("../"):
        rel, base = rel[3:], base.rsplit("/", 1)[0]
    return f"{base}/{rel}"


def pronosticos():
    home = fetch_text("dhn_pronosticos", HOME, name="portal.html", timeout=60)
    paginas = sorted(set(re.findall(r'href="(https://www\.naylamp\.dhn\.mil\.pe/dhn2/secciones/Pronosticos/[^"]+\.php)"', home)))
    recs, errores = {}, []
    for url in paginas:
        try:
            s = fetch_text("dhn_pronosticos", url, keep_raw=False, timeout=60)
            nombre = _texto(re.sub(r"<[^>]+>", "", (re.search(r"<h2>(.*?)</h2>", s, re.S) or [None, url.rsplit("/", 1)[-1]])[1]))
            prod = re.search(r'<iframe[^>]+src="([^"]+?num=)\d+"', s)
            if not prod:
                continue
            imgs = {}
            for h in HORAS:
                imgs[str(h)] = _img_de(f"{prod[1]}{h // 3 + 1}")
            # condición inicial: fecha de la imagen de +0 h (el modelo corre a las 00Z)
            ini = None
            if imgs.get("0"):
                try:
                    import urllib.request
                    from ..http import SSL_CONTEXT
                    req = urllib.request.Request(imgs["0"], method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as r:
                        lm = r.headers.get("Last-Modified")
                    ini = email.utils.parsedate_to_datetime(lm).timestamp() if lm else None
                except Exception:  # noqa: BLE001
                    pass
            tipo = "bahia" if "costeropro" in url else "zona"
            clave = url.rsplit("/", 1)[-1].replace(".php", "")
            recs[clave] = {"nombre": nombre, "tipo": tipo, "pagina": url, "imagenes": imgs, "generado": ini}
        except Exception as e:  # noqa: BLE001
            errores.append(f"{url.rsplit('/', 1)[-1]}: {type(e).__name__}")
    if not recs:
        raise RuntimeError("no se encontraron pronósticos en el portal" + (f" ({'; '.join(errores[:3])})" if errores else ""))
    total, new = db.upsert_items("dhn_pronosticos", "producto", recs, snapshot=True)
    return total, new, f"{total} mapas de pronóstico (zonas y bahías)" + (f" · errores: {'; '.join(errores[:3])}" if errores else "")


# ── Open-Meteo Marine (modelo de referencia, no oficial) ──────────────────────
# Puntos frente a la costa, de norte a sur, con la provincia costera a la que se asignan
PUNTOS = [
    ("Tumbes", -3.55, -80.55, "2401"), ("Máncora", -4.10, -81.15, "2007"), ("Talara", -4.58, -81.35, "2007"),
    ("Paita", -5.08, -81.20, "2005"), ("Bayóvar", -5.80, -81.15, "2008"), ("Pimentel", -6.84, -80.00, "1401"),
    ("Pacasmayo", -7.42, -79.65, "1307"), ("Salaverry", -8.23, -79.05, "1301"), ("Chimbote", -9.10, -78.70, "0218"),
    ("Huarmey", -10.10, -78.25, "0211"), ("Huacho", -11.12, -77.70, "1508"), ("Chancay", -11.58, -77.35, "1506"),
    ("Callao", -12.06, -77.25, "0701"), ("Pucusana", -12.48, -76.85, "1501"), ("Cerro Azul", -13.03, -76.55, "1505"),
    ("Pisco", -13.72, -76.35, "1105"), ("San Juan de Marcona", -15.36, -75.25, "1103"), ("Chala", -15.88, -74.30, "0403"),
    ("Camaná", -16.65, -72.75, "0402"), ("Mollendo", -17.05, -72.08, "0407"), ("Ilo", -17.66, -71.40, "1803"),
    ("Tacna (Boca del Río)", -18.15, -70.75, "2301"),
]
VENTANAS = ((0, 24), (24, 48), (48, 72))


def openmeteo():
    lat = ",".join(str(p[1]) for p in PUNTOS)
    lon = ",".join(str(p[2]) for p in PUNTOS)
    data = fetch_json("olas_openmeteo", "https://marine-api.open-meteo.com/v1/marine?latitude=" + lat + "&longitude=" + lon
                      + "&hourly=wave_height,wave_direction,wave_period,swell_wave_height&timezone=America%2FLima&forecast_days=4",
                      name="marine.json", timeout=60)
    data = data if isinstance(data, list) else [data]
    ahora = datetime.datetime.now(LIMA).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    recs = {}
    for (nombre, la, lo, prov), d in zip(PUNTOS, data):
        h = d.get("hourly") or {}
        horas = [datetime.datetime.fromisoformat(t) for t in h.get("time", [])]
        ventanas = []
        for a, b in VENTANAS:
            idx = [i for i, t in enumerate(horas) if ahora + datetime.timedelta(hours=a) <= t < ahora + datetime.timedelta(hours=b)
                   and h["wave_height"][i] is not None]
            if not idx:
                ventanas.append(None)
                continue
            i = max(idx, key=lambda k: h["wave_height"][k])
            sw = [h["swell_wave_height"][k] for k in idx if h["swell_wave_height"][k] is not None]
            ventanas.append({"ola_max_m": h["wave_height"][i], "hora_max": horas[i].isoformat(timespec="minutes"),
                             "direccion": h["wave_direction"][i], "periodo_s": h["wave_period"][i], "mar_de_fondo_max_m": max(sw) if sw else None})
        recs[nombre] = {"nombre": nombre, "lat": la, "lon": lo, "prov": prov, "reg": prov[:2], "desde": ahora.isoformat(timespec="minutes"),
                        "ventanas": ventanas}
    total, new = db.upsert_items("olas_openmeteo", "punto", recs, snapshot=True)
    mx = max((v["ola_max_m"], r["nombre"]) for r in recs.values() for v in r["ventanas"][:1] if v)
    return total, new, f"{total} puntos de la costa · ola máxima próximas 24 h: {mx[0]} m en {mx[1]} (modelo Open-Meteo)"
