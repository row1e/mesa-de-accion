"""Declaratorias de Estado de Emergencia (decretos supremos de la PCM publicados en El Peruano).

Descubrimiento: la búsqueda de busquedas.elperuano.pe por rango de fechas y entidad (PCM = 2066), Normas Legales y
Edición Extraordinaria. La página es de React Router: los datos vienen en un payload turbo-stream embebido
(`streamController.enqueue("…")`) que aquí se decodifica y valida; si cambia el formato, la fuente falla con un mensaje
claro en vez de devolver vacío. Método tomado del monitor regulatorio de PeruAcuerda (peruano-client.ts).

Lectura: de cada decreto «Estado de Emergencia» se clasifica el motivo (desastre · criminalidad · otro). De los de
desastre se lee el artículo 1 (declaratoria o prórroga, plazo, inicio, decreto que prorroga) y el Anexo del PDF
(departamento → provincia → distritos numerados), resuelto contra el catálogo de distritos del MIDIS.

Resultado: `decreto` (uno por número) y `distrito` (cobertura vigente por ubigeo, solo motivo desastre).
El Peruano pide pausa: una consulta cada 5 s por host, como en PeruAcuerda."""
import datetime
import html
import json
import re
import time
import urllib.parse
from collections import defaultdict

import pymupdf

from .. import db, geo
from ..http import fetch

SRC = "declaratorias"
BASE = "https://busquedas.elperuano.pe"
PCM = "2066"                 # id de la entidad Presidencia del Consejo de Ministros en El Peruano (verificado 2026-11-20)
DESDE_INICIAL = 200          # días hacia atrás en la primera lectura (cubre cadenas de declaratorias y prórrogas)
SOLAPE = 3                   # días que se releen en cada vuelta (publicaciones tardías del día)
VENTANA = 31                 # días por consulta
PAUSA = 5.0                  # segundos entre consultas a El Peruano
MAX_PAGINAS = 50
LECTOR_V = 4                 # subir al mejorar la lectura: los decretos leídos con otra versión se vuelven a procesar

MESES = {m: i for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                     "septiembre", "octubre", "noviembre", "diciembre"], 1)}
MESES["setiembre"] = 9
NUM = re.compile(r"(\d{1,3})\s*[-–]\s*(20\d{2})\s*[-–]\s*PCM", re.I)
DESASTRE = re.compile(r"peligro inminente|impacto de da[ñn]os|precipitaci|lluvi|d[ée]ficit h[íi]drico|sismo|huaico|"
                      r"helada|friaje|incendio forestal|erupci|tsunami|oleaje|deslizamiento|inundaci|ni[ñn]o", re.I)
CRIMEN = re.compile(r"criminalidad|orden interno|violencia|penitenciari|delincuencia|seguridad ciudadana|"
                    r"narcotr[áa]fico|miner[íi]a ilegal|terroris", re.I)


class FormatoError(RuntimeError):
    def __init__(self, msg):
        super().__init__(f"El Peruano: estructura no reconocida — {msg}")


# ── turbo-stream (React Router) ──────────────────────────────────────────────────────────────────────────

def _trozos(html):
    out, i, marca = [], 0, "streamController.enqueue("
    while True:
        a = html.find(marca, i)
        if a < 0:
            return out
        j = a + len(marca)
        if html[j:j + 1] != '"':
            i = j
            continue
        k = j + 1
        while k < len(html):            # literal JSON con escapes (una regex se corta en `\");`)
            if html[k] == "\\":
                k += 2
                continue
            if html[k] == '"':
                break
            k += 1
        out.append(json.loads(html[j:k + 1]))
        i = k + 1


def _decodificar(payload):
    try:
        vals = json.loads(payload.split("\n")[0])
    except ValueError:
        raise FormatoError("payload no es JSON")
    if not isinstance(vals, list) or not vals:
        raise FormatoError("payload vacío")
    memo = {}

    def at(ix):
        if not isinstance(ix, int) or ix < 0:        # índices negativos: undefined/null/NaN…
            return None
        if ix in memo:
            return memo[ix]
        raw = vals[ix]
        if isinstance(raw, list):
            if raw and isinstance(raw[0], str):      # valor tipado ["D", …]
                memo[ix] = raw[1] if len(raw) > 1 else None
                return memo[ix]
            out = []
            memo[ix] = out
            out.extend(at(x) for x in raw)
            return out
        if isinstance(raw, dict):
            out = {}
            memo[ix] = out
            for k, v in raw.items():
                nombre = vals[int(k[1:])] if k.startswith("_") and k[1:].isdigit() else None
                if not isinstance(nombre, str):
                    raise FormatoError("clave de objeto inválida")
                out[nombre] = at(v)
            return out
        return raw
    return at(0)


def _datos(html, ruta):
    trozos = _trozos(html)
    if not trozos:
        raise FormatoError("sin datos embebidos")
    d = ((_decodificar("".join(trozos)) or {}).get("loaderData") or {}).get(ruta)
    if not isinstance(d, dict):
        raise FormatoError(f"sin datos para la ruta {ruta}")
    return d


def _limpio(v):
    return v.strip() if isinstance(v, str) and v.strip() and v.strip() != "None" else None


_ultima = [0.0]


def _get(url, name, keep_raw=False, timeout=90):
    espera = PAUSA - (time.time() - _ultima[0])
    if espera > 0:
        time.sleep(espera)
    try:
        return fetch(SRC, url, name=name, keep_raw=keep_raw, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        # El Peruano a veces responde 404 a una página que sirve segundos después (también lo vio PeruAcuerda):
        # un reintento tras una pausa larga. 403/429 no se reintentan.
        if not re.search(r"HTTP (404|5\d\d)", str(e)):
            raise
        time.sleep(30)
        return fetch(SRC, url, name=name, keep_raw=keep_raw, timeout=timeout)
    finally:
        _ultima[0] = time.time()


def buscar(desde, hasta, tipo):
    """Todos los dispositivos de la PCM publicados entre `desde` y `hasta` (date) en la edición `tipo` (NL | EX)."""
    recs, start, total = [], 0, None
    for _ in range(MAX_PAGINAS):
        q = urllib.parse.urlencode({"ci": "ONLY", "tipoPublicacion": tipo, "fechaIni": desde.strftime("%Y%m%d"),
                                    "fechaFin": hasta.strftime("%Y%m%d"), "entidad": PCM, "start": start})
        html = _get(f"{BASE}/?{q}", name=f"busqueda_{tipo}_{desde:%Y%m%d}_{start}.html").decode("utf-8", "replace")
        d = _datos(html, "routes/_index")
        if d.get("isCuadernillos"):
            raise FormatoError("la búsqueda devolvió cuadernillos, no dispositivos")
        r = d.get("results") or {}
        if not isinstance(r.get("totalHits"), int) or not isinstance(r.get("hits"), list):
            raise FormatoError("resultados sin totalHits/hits")
        total = r["totalHits"]
        for h in r["hits"]:
            if not isinstance(h, dict) or not re.fullmatch(r"\d+-\d+", str(h.get("op") or "")) or \
                    not re.fullmatch(r"\d{8}", str(h.get("fechaPublicacion") or "")):
                raise FormatoError("registro sin op o fecha")
            recs.append(h)
        if not r.get("hasNext") or not r["hits"]:
            break
        start += 20
    unicos = {h["op"]: h for h in recs}
    if len(unicos) != total:
        raise RuntimeError(f"El Peruano declaró {total} dispositivos {tipo} del {desde} al {hasta} y se leyeron {len(unicos)}")
    return list(unicos.values())


# ── catálogo de distritos (MIDIS) ────────────────────────────────────────────────────────────────────────

def _n(s):
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", geo.norm(s)).split())


ALIAS_DEP = {"PROVINCIA CONSTITUCIONAL DEL CALLAO": "CALLAO", "PROV CONST DEL CALLAO": "CALLAO", "LIMA METROPOLITANA": "LIMA"}


class Catalogo:
    def __init__(self):
        filas = [r for r in db.get_items("midis", "distrito") if r.get("ubigeo")]
        if len(filas) < 1500:
            raise RuntimeError(f"el catálogo de distritos del MIDIS tiene {len(filas)} filas; correr la fuente midis primero")
        self.deps, self.provs, self.dists = {}, defaultdict(dict), defaultdict(dict)
        for r in filas:
            ub = r["ubigeo"]
            self.deps[_n(r["departamento"])] = ub[:2]
            self.provs[ub[:2]][_n(r["provincia"])] = ub[:4]
            self.dists[ub[:4]][_n(r["distrito"])] = ub

    def dep(self, s):
        s = _n(s)
        return _buscar(self.deps, ALIAS_DEP.get(s, s))      # también recortados: «JUNI» por Junín

    def prov(self, dep, s):
        p = self.provs.get(dep, {})
        return _buscar(p, _n(s)) or (p.get("CALLAO") if dep == "07" and _n(s).endswith("CALLAO") else None)

    def dist(self, prov, s):
        return _buscar(self.dists.get(prov, {}), _n(s))


def _buscar(opciones, s, aproximado=True):
    """Nombre normalizado → código. El catálogo del MIDIS trae nombres recortados tras una vocal con tilde
    («Copalli» por Copallín, «Morropo» por Morropón), así que además de la coincidencia exacta se acepta: sin espacios
    (el PDF corta o junta palabras), el nombre del catálogo como prefijo de 1-2 letras menos, y una coincidencia
    cercana única. Siempre dentro de una sola provincia o departamento, nunca en todo el país."""
    if not s:
        return None
    if s in opciones:
        return opciones[s]
    sin = s.replace(" ", "")
    for k, v in opciones.items():
        if k.replace(" ", "") == sin:
            return v
    if not aproximado:
        return None
    pref = [v for k, v in opciones.items() if len(k) >= 3 and s.startswith(k) and 0 < len(s) - len(k) <= (1 if len(k) < 5 else 2)]
    if len(set(pref)) == 1:
        return pref[0]
    import difflib
    cerca = difflib.get_close_matches(s, list(opciones), n=2, cutoff=0.88)
    if len(cerca) == 1 or (len(cerca) == 2 and difflib.SequenceMatcher(None, s, cerca[0]).ratio() -
                           difflib.SequenceMatcher(None, s, cerca[1]).ratio() > 0.05):
        return opciones[cerca[0]]
    return None


# ── lectura del decreto ──────────────────────────────────────────────────────────────────────────────────

def _fecha_texto(s):
    m = re.search(r"(\d{1,2})\s+de\s+([a-záéíóú]+)\s+(?:de|del)\s+(?:año\s+)?(20\d{2})", s, re.I)
    if m and m.group(2).lower() in MESES:
        return datetime.date(int(m.group(3)), MESES[m.group(2).lower()], int(m.group(1)))
    return None


def motivo(sumilla, art1=""):
    t = f"{sumilla} {art1}"
    if CRIMEN.search(t):
        return "criminalidad"
    if DESASTRE.search(t):
        return "desastre"
    return "otro"


def leer_articulo1(texto, publicado):
    """Tipo (declaratoria | prórroga), causa, fenómeno, plazo, inicio, fin y decretos que prorroga."""
    t = re.sub(r"\s+", " ", texto)
    i = t.find("Artículo 1")
    art = t[i:i + 2500] if i >= 0 else t[:2500]
    prorroga = bool(re.search(r"\bprorr", art[:200], re.I))
    m = re.search(r"\((\d{1,3})\)\s*d[íi]as\s+calendario", art) or re.search(r"(\d{1,3})\s*d[íi]as\s+calendario", art)
    plazo = int(m.group(1)) if m else None
    m = re.search(r"a partir del?\s+(\d{1,2}\s+de\s+[a-záéíóú]+\s+(?:de|del)\s+(?:año\s+)?20\d{2})", art, re.I)
    inicio = _fecha_texto(m.group(1)) if m else publicado + datetime.timedelta(days=1)
    causa = "impacto de daños" if re.search(r"impacto de da[ñn]os", art, re.I) else \
        "peligro inminente" if re.search(r"peligro inminente", art, re.I) else None
    m = re.search(r"(?:peligro inminente ante|impacto de da[ñn]os (?:ocasionados|generados) por|a consecuencia de)\s+(.{5,140}?)"
                  r"(?:,|\.|;| por el (?:plazo|t[ée]rmino)| para la)", art, re.I)
    fenomeno = re.split(r"\s+(?:DECRETO SUPREMO|Que,|CONSIDERANDO)", m.group(1))[0].strip() if m else None
    previos = []
    if prorroga:
        for n, a in NUM.findall(art):
            k = f"{int(n):03d}-{a}-PCM"
            if k not in previos:
                previos.append(k)
    fin = inicio + datetime.timedelta(days=plazo - 1) if plazo and inicio else None
    return {"tipo": "prorroga" if prorroga else "declaratoria", "causa": causa, "fenomeno": fenomeno, "plazo_dias": plazo,
            "inicio": inicio.isoformat() if inicio else None, "fin": fin.isoformat() if fin else None,
            "prorroga_de": previos, "articulo1": art[:1200]}


RUIDO = re.compile(r"NORMAS LEGALES|El Peruano|^\d{1,4}$|^DEPARTAMENTOS?$|^PROVINCIAS?$|^DISTRITOS?$|^N[º°o]\.?\s*DE\b|"
                   r"^ANEXO|^DECRETO SUPREMO|^\d+\s+(?:NORMAS|El Peruano)", re.I)


def _lineas(texto):
    """pymupdf separa el número de orden y el nombre del distrito en dos líneas: se vuelven a unir."""
    crudas = [x.strip() for x in texto.splitlines()]
    out, i = [], 0
    while i < len(crudas):
        ln = crudas[i]
        if re.fullmatch(r"\d{1,4}", ln) and i + 1 < len(crudas) and crudas[i + 1] and not re.fullmatch(r"\d{1,4}", crudas[i + 1]):
            out.append(f"{ln} {crudas[i + 1]}")
            i += 2
            continue
        out.append(ln)
        i += 1
    return out


def leer_anexo(texto, cat):
    """Anexo del decreto → (ubigeos resueltos, nombres sin resolver). Las filas son: departamento, provincia y
    distritos numerados; los nombres largos pueden venir partidos en dos líneas."""
    i = texto.upper().rfind("ANEXO")
    if i < 0:
        return [], [], {}
    dep = prov = None
    pend, ubigeos, sueltos, nombres = [], [], [], {}
    distrito_pend = None                     # distrito numerado que aún no se resolvió (puede seguir en la línea siguiente)

    def resolver_distrito(nombre):
        return cat.dist(prov, nombre) if prov else None

    lineas = [x for x in _lineas(texto[i:]) if x and not RUIDO.search(x)]

    def siguiente_distrito(k):
        for x in lineas[k + 1:k + 6]:
            m2 = re.match(r"^\d{1,4}\s+(.+)$", x)
            if m2:
                return m2.group(1)
        return None

    for k, ln in enumerate(lineas):
        m = re.match(r"^(\d{1,4})\s+(.+)$", ln)
        if m:
            if distrito_pend:
                sueltos.append(distrito_pend)
            pend = []
            nombre = m.group(2)
            ub = resolver_distrito(nombre)
            if ub:
                ubigeos.append(ub)
                nombres[ub] = nombre
                distrito_pend = None
            else:
                distrito_pend = nombre
            continue
        if distrito_pend:                     # continuación del nombre del distrito anterior
            ub = resolver_distrito(f"{distrito_pend} {ln}")
            if ub:
                ubigeos.append(ub)
                nombres[ub] = f"{distrito_pend} {ln}"
                distrito_pend = None
                continue
        pend = (pend + [ln])[-3:]
        for n in (1, 2, 3):                   # la última línea sola, o unida a las anteriores (nombres partidos)
            if len(pend) < n:
                break
            cand = " ".join(pend[-n:])
            d = cat.dep(cand)
            if d and d != dep:
                dep, prov, pend = d, None, []
                break
            p = cat.prov(dep, cand) if dep else None
            if p and prov and p != prov:
                # Al saltar de página la tabla repite el departamento («AREQUIPA», «CAJAMARCA»), que también es nombre de
                # provincia: solo se cambia si el distrito que sigue es de la provincia nueva y no de la actual.
                sig = siguiente_distrito(k)
                if sig and cat.dist(prov, sig) and not cat.dist(p, sig):
                    pend = []
                    break
            if p:
                if distrito_pend:
                    sueltos.append(distrito_pend)
                    distrito_pend = None
                prov, pend = p, []
                break
    if distrito_pend:
        sueltos.append(distrito_pend)
    return list(dict.fromkeys(ubigeos)), sueltos, nombres


# Nombre propio: palabras con mayúscula inicial, unidas solo por «de/del/la/las/los» seguidos de otra mayúscula.
# Sin re.I: es la mayúscula la que separa el nombre del texto que sigue («Chupaca y en los distritos…»).
_PAL = r"[A-ZÁÉÍÓÚÑÜ][\wÁÉÍÓÚÑÜáéíóúñü'\-]*"
_NOMBRE = rf"{_PAL}(?:\s+(?:(?:de|del|la|las|los)\s+)?{_PAL})*"


def leer_en_texto(art1, cat):
    """Decretos chicos sin Anexo: «… en los distritos de A, B y C de la provincia de P, y en el distrito de D de la provincia
    de Q, del departamento de R …». Se toma cada grupo «distrito(s) … provincia» y el departamento que le sigue."""
    t = re.sub(r"\s+", " ", art1)
    ubigeos, sueltos, nombres = [], [], {}
    patron = re.compile(r"(?i:distritos?)\s+(?:de\s+)?(.+?)\s+(?i:de la provincia)\s+(?P<ydep>(?i:y departamento)\s+)?"
                        r"(?:(?i:constitucional)\s+)?(?:de\s+|del\s+)?(?P<prov>" + _NOMBRE + r")")
    for m in patron.finditer(t):
        if m.group("ydep"):                  # «de la provincia y departamento de Lima»
            dep = cat.dep(m.group("prov"))
        else:
            dm = re.search(r"(?i:departamentos?)\s+(?:de\s+|del\s+)?(" + _NOMBRE + r")", t[m.end():m.end() + 300])
            dep = cat.dep(dm.group(1)) if dm else None
        if not dep and re.search(r"callao", m.group("prov"), re.I):
            dep = "07"
        prov = cat.prov(dep, m.group("prov")) if dep else None
        if not prov:
            continue
        for nombre in re.split(r",\s*|\s+y\s+", m.group(1)):
            nombre = re.sub(r"^(?:(?:en\s+)?los distritos de|(?:en\s+)?el distrito de|de)\s+", "", nombre.strip(), flags=re.I)
            if not nombre or len(nombre) > 60:
                continue
            ub = cat.dist(prov, nombre)
            (ubigeos if ub else sueltos).append(ub or nombre)
            if ub:
                nombres[ub] = nombre
    return list(dict.fromkeys(ubigeos)), sueltos, nombres


def _html_texto(url, op):
    """Texto oficial de la página del dispositivo (el <title> embebido puede ser de otro documento: solo el cuerpo)."""
    d = _datos(_get(url, name=f"dispositivo_{op}.html").decode("utf-8", "replace"), "routes/dispositivo.$tipoPublicacion.$op.($view)")
    cuerpo = d.get("htmlContent") or ""
    cuerpo = re.sub(r"(?is)<(script|style|title|head)\b.*?</\1>", " ", cuerpo)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cuerpo))).strip()


def _pdf_texto(url, numero):
    raw = _get(url, name=f"{numero}.pdf", keep_raw=True, timeout=120)
    doc = pymupdf.open(stream=raw, filetype="pdf")
    return "\n".join(p.get_text() for p in doc)


def leer_decreto(h, cat):
    numero = NUM.search(str(h.get("numeroDispositivo") or ""))
    numero = f"{int(numero.group(1)):03d}-{numero.group(2)}-PCM" if numero else None
    publicado = datetime.date(int(h["fechaPublicacion"][:4]), int(h["fechaPublicacion"][4:6]), int(h["fechaPublicacion"][6:]))
    sumilla = _limpio(h.get("sumilla")) or ""
    tipo_pub = h.get("tipoPublicacion") or "NL"
    url = f"{BASE}/dispositivo/{tipo_pub}/{h['op']}"
    pdf = _limpio(h.get("urlPDF"))
    rec = {"numero": numero, "op": h["op"], "edicion": tipo_pub, "publicado": publicado.isoformat(), "sumilla": sumilla,
           "url": url, "pdf": f"{BASE}{pdf}" if pdf and pdf.startswith("/api/") else None,
           "motivo": motivo(sumilla), "distritos": [], "sin_resolver": [], "v": LECTOR_V,
           "tipo_dispositivo": h.get("tipoDispositivo"), "numero_dispositivo": h.get("numeroDispositivo")}
    if rec["motivo"] != "desastre":
        # La sumilla de una prórroga puede no decir el motivo ("prorroga el Estado de Emergencia en varios distritos
        # de Piura"): se decide con el artículo 1 del texto oficial antes de descartarla.
        rec["articulo1"] = leer_articulo1(_html_texto(url, h["op"]), publicado)["articulo1"]
        rec["motivo"] = motivo(sumilla, rec["articulo1"])
    if rec["motivo"] != "desastre":
        return rec                            # criminalidad u otro: se registra, sin leer el anexo
    if not rec["pdf"]:
        rec["error"] = "sin PDF en El Peruano"
        return rec
    texto = _pdf_texto(rec["pdf"], numero or h["op"])
    rec.update(leer_articulo1(texto, publicado))
    rec["motivo"] = motivo(sumilla, rec["articulo1"])
    rec["distritos"], rec["sin_resolver"], rec["nombres"] = leer_anexo(texto, cat)
    if not rec["distritos"]:
        # sin Anexo: los distritos van en el texto del artículo 1
        rec["distritos"], rec["sin_resolver"], rec["nombres"] = leer_en_texto(rec["articulo1"], cat)
        rec["lectura"] = "texto del artículo 1"
    if not rec["distritos"] and not (rec["tipo"] == "prorroga" and rec["prorroga_de"]):
        rec["error"] = "no se pudo leer el anexo de distritos"
    elif not rec["distritos"]:
        rec["lectura"] = "hereda los distritos del decreto que prorroga"
    return rec


def leer_seguro(h, cat):
    """Un decreto que no se puede leer no detiene la fuente: queda con su error y se reintenta en la próxima vuelta."""
    try:
        return leer_decreto(h, cat)
    except FormatoError:
        raise                                  # cambió la estructura de El Peruano: que se vea en el tablero de fuentes
    except Exception as e:  # noqa: BLE001
        n = NUM.search(str(h.get("numeroDispositivo") or ""))
        return {"numero": f"{int(n.group(1)):03d}-{n.group(2)}-PCM" if n else None, "op": h["op"],
                "edicion": h.get("tipoPublicacion") or "NL",
                "publicado": f"{h['fechaPublicacion'][:4]}-{h['fechaPublicacion'][4:6]}-{h['fechaPublicacion'][6:]}",
                "sumilla": _limpio(h.get("sumilla")), "pdf": f"{BASE}{h['urlPDF']}" if _limpio(h.get("urlPDF")) else None,
                "tipo_dispositivo": h.get("tipoDispositivo"), "numero_dispositivo": h.get("numeroDispositivo"),
                "motivo": motivo(_limpio(h.get("sumilla")) or ""), "distritos": [], "sin_resolver": [], "v": LECTOR_V,
                "error": f"{type(e).__name__}: {str(e)[:200]}"}


def _bien_leido(d):
    return d.get("v") == LECTOR_V and not d.get("error")


# ── cobertura por distrito ───────────────────────────────────────────────────────────────────────────────

def cobertura(decretos, hoy):
    """ubigeo → vigentes hoy (motivo desastre) y el historial reciente. Una prórroga sin anexo propio hereda los
    distritos de los decretos que prorroga."""
    por_num = {d["numero"]: d for d in decretos if d.get("numero")}

    def distritos(d, visto=()):
        if d.get("distritos"):
            return d["distritos"]
        out = []
        for p in d.get("prorroga_de") or []:
            if p in por_num and p not in visto:
                out += distritos(por_num[p], visto + (p,))
        return out

    por_dist = defaultdict(list)
    for d in decretos:
        if d.get("motivo") != "desastre" or not d.get("inicio") or not d.get("fin"):
            continue
        for ub in distritos(d):
            por_dist[ub].append({k: d.get(k) for k in ("numero", "tipo", "causa", "fenomeno", "inicio", "fin", "url", "publicado")})
    out = {}
    for ub, lista in por_dist.items():
        lista.sort(key=lambda x: x["inicio"])
        vig = [x for x in lista if x["inicio"] <= hoy <= x["fin"]]
        hasta = max((x["fin"] for x in vig), default=None)
        out[ub] = {"ubigeo": ub, "prov": ub[:4], "reg": ub[:2], "vigente": bool(vig), "cubierto_hasta": hasta,
                   "dias_restantes": (datetime.date.fromisoformat(hasta) - datetime.date.fromisoformat(hoy)).days if hasta else None,
                   "vigentes": vig, "ultimo": lista[-1], "historial": lista[-6:]}
    return out


# ── fuente ───────────────────────────────────────────────────────────────────────────────────────────────

def declaratorias():
    hoy = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5))).date()
    estado = db.get_item(SRC, "estado", "cursor") or {}
    if estado.get("leido_hasta"):
        desde = datetime.date.fromisoformat(estado["leido_hasta"]) - datetime.timedelta(days=SOLAPE)
    else:
        desde = hoy - datetime.timedelta(days=DESDE_INICIAL)
    cat = Catalogo()
    existentes = {d["_key"]: d for d in db.get_items(SRC, "decreto", current_only=False)}
    nuevos, leidos, ventanas = {}, 0, 0
    ini = desde
    while ini <= hoy:
        fin = min(ini + datetime.timedelta(days=VENTANA - 1), hoy)
        for tipo in ("NL", "EX"):
            for h in buscar(ini, fin, tipo):
                leidos += 1
                if not re.search(r"estado de emergencia", str(h.get("sumilla") or ""), re.I):
                    continue
                if "DECRETO SUPREMO" not in str(h.get("tipoDispositivo") or "").upper():
                    continue
                n = NUM.search(str(h.get("numeroDispositivo") or ""))
                clave = f"{int(n.group(1)):03d}-{n.group(2)}-PCM" if n else f"op-{h['op']}"
                if clave in existentes and _bien_leido(existentes[clave]):
                    continue                  # ya leído bien: el texto oficial no cambia
                nuevos[clave] = leer_seguro(h, cat)
        ventanas += 1
        ini = fin + datetime.timedelta(days=1)
    # Los leídos con error o con una versión anterior del lector se reprocesan aunque estén fuera de la ventana
    for clave, d in existentes.items():
        if clave not in nuevos and not _bien_leido(d) and d.get("op"):
            h = {"op": d["op"], "tipoPublicacion": d.get("edicion") or "NL", "fechaPublicacion": d["publicado"].replace("-", ""),
                 "sumilla": d.get("sumilla"), "numeroDispositivo": d.get("numero_dispositivo") or d.get("numero"),
                 "tipoDispositivo": d.get("tipo_dispositivo") or "DECRETO SUPREMO",
                 "urlPDF": (d.get("pdf") or "").replace(BASE, "") or None}
            nuevos[clave] = leer_seguro(h, cat)
    if nuevos:
        db.upsert_items(SRC, "decreto", nuevos)
    db.upsert_items(SRC, "estado", {"cursor": {"leido_hasta": hoy.isoformat(), "desde_primera": estado.get("desde_primera") or desde.isoformat()}})
    todos = [d for d in db.get_items(SRC, "decreto", current_only=False)]
    cob = cobertura(todos, hoy.isoformat())
    db.upsert_items(SRC, "distrito", cob, snapshot=True)
    vig = sum(1 for c in cob.values() if c["vigente"])
    errores = [d["numero"] or d["op"] for d in nuevos.values() if d.get("error")]
    sueltos = sum(len(d.get("sin_resolver") or []) for d in nuevos.values())
    pronto = sum(1 for c in cob.values() if c["vigente"] and c["dias_restantes"] is not None and c["dias_restantes"] <= 15)
    msg = (f"{leidos} dispositivos de la PCM revisados del {desde} al {hoy}; {len(nuevos)} decretos de emergencia nuevos. "
           f"{vig} distritos con declaratoria vigente por desastre ({pronto} vencen en 15 días o menos)")
    if sueltos:
        msg += f"; {sueltos} nombres del anexo sin resolver"
    if errores:
        msg += f"; sin anexo legible: {', '.join(errores[:5])}"
    return len(todos), len(nuevos), msg


# ── para el tablero (snapshot) ───────────────────────────────────────────────────────────────────────────

PRONTO = 15                  # días: una declaratoria que vence en menos se marca para decidir su prórroga


def para_tablero():
    """Declaratorias por desastre vigentes hoy, su cobertura por departamento y provincia, las que vencen pronto y las
    recientes por criminalidad (sin anexo de distritos: se listan, no se mapean)."""
    hoy = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5))).date()
    decs = db.get_items(SRC, "decreto", current_only=False)
    if not decs:
        return None
    cob = db.get_items(SRC, "distrito")
    total_prov = defaultdict(int)
    for r in db.get_items("midis", "distrito"):
        if r.get("ubigeo"):
            total_prov[r["ubigeo"][:4]] += 1
    vig_num = defaultdict(lambda: defaultdict(int))     # número → reg → distritos cubiertos por ese decreto hoy
    por_prov, por_reg = {}, {}
    for c in cob:
        if not c.get("vigente"):
            continue
        ub, hasta = c["ubigeo"], c.get("cubierto_hasta")
        p = por_prov.setdefault(ub[:4], {"n": 0, "total": total_prov.get(ub[:4]), "hasta": hasta, "decretos": set()})
        p["n"] += 1
        p["hasta"] = min(p["hasta"], hasta) if p["hasta"] and hasta else p["hasta"] or hasta
        r = por_reg.setdefault(ub[:2], {"n": 0, "hasta": hasta, "decretos": set()})
        r["n"] += 1
        r["hasta"] = min(r["hasta"], hasta) if r["hasta"] and hasta else r["hasta"] or hasta
        for v in c.get("vigentes") or []:
            vig_num[v["numero"]][ub[:2]] += 1
            p["decretos"].add(v["numero"])
            r["decretos"].add(v["numero"])
    for x in (*por_prov.values(), *por_reg.values()):
        x["decretos"] = sorted(x["decretos"])
    por_numero = {d.get("numero"): d for d in decs if d.get("numero")}
    vigentes = []
    for num, regs in vig_num.items():
        d = por_numero.get(num) or {}
        dias = (datetime.date.fromisoformat(d["fin"]) - hoy).days if d.get("fin") else None
        vigentes.append({"numero": num, "key": num, "tipo": d.get("tipo"), "causa": d.get("causa"), "fenomeno": d.get("fenomeno"),
                         "inicio": d.get("inicio"), "fin": d.get("fin"), "dias": dias, "publicado": d.get("publicado"),
                         "n": sum(regs.values()), "por_reg": dict(regs), "prorroga_de": d.get("prorroga_de") or [],
                         "url": d.get("url"), "pdf": d.get("pdf")})
    vigentes.sort(key=lambda v: (v["fin"] or "9999", -v["n"]))
    desde = (hoy - datetime.timedelta(days=60)).isoformat()
    crimen = sorted(({"numero": d.get("numero"), "key": d.get("numero") or f"op-{d['op']}", "publicado": d.get("publicado"),
                      "sumilla": d.get("sumilla"), "url": d.get("url"), "regs": geo.regions_mentioned(d.get("sumilla") or "")}
                     for d in decs if d.get("motivo") == "criminalidad" and (d.get("publicado") or "") >= desde),
                    key=lambda d: d["publicado"], reverse=True)
    estado = db.get_item(SRC, "estado", "cursor") or {}
    return {"hoy": hoy.isoformat(), "vigentes": vigentes, "por_prov": por_prov, "por_reg": por_reg,
            "n_distritos": sum(r["n"] for r in por_reg.values()), "pronto_dias": PRONTO,
            "pronto": [v for v in vigentes if v["dias"] is not None and v["dias"] <= PRONTO],
            "crimen": crimen[:20], "leido_hasta": estado.get("leido_hasta"),
            "nota": ("Declaratorias de estado de emergencia por desastre publicadas en El Peruano (decretos supremos de la "
                     "PCM), con los distritos de su anexo. Las de criminalidad u orden interno se listan aparte y no cuentan.")}
