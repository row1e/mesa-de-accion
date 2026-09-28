"""Asistente de IA para la redacción: borradores a partir de datos oficiales, nunca de memoria.

Cuatro garantías, cada una aplicada en código y no confiada al modelo:

1. Solo datos estructurados. El modelo recibe un "dossier" armado aquí con el registro de la fuente
   oficial (o el resumen del día) y nada más. El dossier se guarda con el borrador.
2. Verificación de cifras. Todo número del texto generado tiene que existir en el dossier. Si no,
   se reintenta una vez indicando qué cifras sobran; si vuelve a fallar, el borrador queda
   "bloqueado" y su texto no se entrega. Los números escritos en letras cuentan como falla, porque
   esquivarían la verificación.
   Límite conocido: la verificación garantiza que cada cifra EXISTE en los datos, no que se use con el
   sentido correcto. Un "2" pasa si hay un 2 en cualquier parte (incluso en una fecha). Por eso la
   revisión humana es obligatoria y no un adorno.
3. Cita de la fuente. La institución, el documento, la hora del dato y el enlace se agregan desde
   el dossier, no los escribe el modelo.
4. Aprobación humana. Nada sale sin que una persona lo apruebe: el texto final solo se entrega por
   la API cuando el borrador está "aprobado". Cada generación, edición, aprobación y descarte queda
   en ia_auditoria, que es solo de inserción.

Mientras no haya cuentas de usuario, quien actúa se identifica con su nombre en cada acción.
"""
import datetime
import hashlib
import json
import os
import re
import time
import unicodedata
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from . import db, detail, snapshot
from .sources import SOURCES

MODELO = os.environ.get("MESA_IA_MODEL", "claude-opus-5")
ESFUERZO = os.environ.get("MESA_IA_EFFORT", "medium")   # textos cortos: medium basta; subir si la calidad no alcanza
LIMA = ZoneInfo("America/Lima")
TEXTO_MAX = 6000            # caracteres de texto libre de la fuente que se pasan al modelo
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class IAError(RuntimeError):
    """Error presentable al usuario (se traduce a 4xx/5xx en la API)."""

    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


# ── Tipos de pieza ────────────────────────────────────────────────────────────
# (nombre, alcances admitidos, instrucciones, límites de caracteres por etiqueta: solo aviso)
TIPOS = {
    "guion": ("Guion radial", {"registro"},
              "Escribe un guion para leer al aire en radio, de entre 130 y 200 palabras. Tono informativo y "
              "sereno, frases cortas, sin adjetivos alarmistas. Nombra a la institución que informa. "
              "Devuelve una sola pieza con etiqueta \"Guion\".", {}),
    "titulares": ("Titulares y zócalo", {"registro"},
                  "Devuelve tres piezas: \"Titular\" (máximo 90 caracteres), \"Zócalo\" (texto de pantalla de "
                  "televisión, máximo 60 caracteres) y \"Bajada\" (una o dos oraciones que amplían el titular).",
                  {"Titular": 90, "Zócalo": 60}),
    "redes": ("Publicaciones para redes", {"registro"},
              "Devuelve tres piezas: \"X\" (máximo 260 caracteres), \"Facebook\" (dos o tres oraciones) e "
              "\"Instagram\" (dos o tres oraciones, como máximo tres etiquetas con #). No incluyas enlaces: "
              "se agregan después.", {"X": 260}),
    "preguntas": ("Preguntas para entrevista", {"registro"},
                  "Propón 5 preguntas concretas para una autoridad (alcaldía, gobierno regional, COER o la "
                  "institución que informa), basadas en los datos. Cada pregunta es una pieza con etiqueta "
                  "\"Pregunta\". Deben poder responderse con hechos y no presuponer lo que los datos no dicen.", {}),
    "briefing": ("Briefing de turno", {"briefing"},
                 "Escribe el resumen para el inicio de turno de una redacción. Devuelve tres piezas: "
                 "\"Lo principal\" (3 a 5 oraciones con lo más relevante), \"Qué está escalando\" (avisos de "
                 "nivel alto, eventos con muchas personas afectadas o que siguen reportando) y \"Qué vigilar\" "
                 "(lo que puede cambiar en las próximas horas según los datos). Si una sección no tiene "
                 "datos que la sustenten, dilo en una frase en lugar de rellenar.", {}),
}

SISTEMA = """Redactas borradores para la mesa de prensa de un medio de radio y televisión en Perú, a partir de datos oficiales de instituciones del Estado.

Reglas que no admiten excepción:
- Usa solo la información del bloque DATOS. No agregues hechos, antecedentes ni contexto que no estén ahí, aunque los conozcas.
- Cada cifra que escribas debe aparecer tal cual en DATOS: sin redondear, sin aproximar ("más de", "casi", "cerca de") y sin hacer cálculos (sumas, restas, porcentajes, promedios).
- Escribe todas las cantidades, fechas y horas con dígitos, nunca en letras.
- Si los datos no alcanzan para algo que pide la tarea, dilo en el campo "advertencia" en lugar de suponer.
- No escribas enlaces ni la línea de fuente: se agregan automáticamente después.
- Español de Perú, registro periodístico de servicio público: claro, preciso, sin dramatizar y sin opiniones."""


class Pieza(BaseModel):
    etiqueta: str
    texto: str


class Redaccion(BaseModel):
    piezas: list[Pieza]
    advertencia: str        # vacío si los datos alcanzaron


# ── Dossier: lo único que ve el modelo ──────────────────────────────────────
def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, LIMA).strftime("%Y-%m-%d %H:%M") if ts else None


def _hora_fuente(d):
    """Hora que da la propia fuente (publicación), no la hora en que la plataforma vio el registro:
    un reporte de INDECI que se actualiza conserva el registro original pero cambia de fecha."""
    f = d.get("fields") or {}
    if f.get("pub"):
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(f["pub"]).astimezone(LIMA).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            pass
    for k in ("fecha_hora", "fecha_emision", "fecha"):
        if f.get(k):
            return str(f[k]) + (f" {f['hora']}" if k == "fecha" and f.get("hora") else "")
    return _iso(d.get("first_seen"))


def dossier_registro(source, kind, key):
    d = detail.build(source, kind, key)
    if d is None:
        raise IAError("El registro ya no está en la base.", 404)
    org = SOURCES.get(source, {}).get("org", source)
    fields = {k: v for k, v in (d.get("fields") or {}).items()
              if not k.startswith("_") and k not in ("link", "guid", "url", "geometry", "ts", "pub") and not isinstance(v, (dict, list))}
    dossier = {
        "alcance": "registro", "institucion": org, "titulo": d.get("title"), "subtitulo": d.get("subtitle"),
        "nivel": d.get("level"), "datos": {k: v for k, v in (d.get("facts") or [])}, "campos": fields,
        "texto": (d.get("text") or "")[:TEXTO_MAX] or None,
        "texto_reporte": (d.get("pdf_text") or "")[:TEXTO_MAX] or None,
        "registrado_en_plataforma": _iso(d.get("first_seen")),
    }
    links = d.get("links") or []
    cita = [{"institucion": org, "documento": d.get("title"), "hora_dato": _hora_fuente(d),
             "url": links[0]["url"] if links else None}]
    return dossier, cita, d.get("title")


def dossier_briefing():
    D = snapshot.build()
    now = datetime.datetime.now(LIMA)
    hoy = now.date().isoformat()
    lv = D["levelsByDay"].get(hoy, {})
    por_nivel = {str(n): sum(1 for c in lv.values() if c["m"] == n) for n in (4, 3, 2)}
    avisos = [{"numero": a["nro"], "titulo": a["titulo"], "nivel": a["nivel"], "estado": a["estado"],
               "inicio": a.get("inicio"), "fin": a.get("fin")} for a in D["avisos"]][:10]
    rep = [i for i in D["indeci"] if i.get("clase") == "reporte"]
    indeci = [{"evento": i.get("evento"), "distrito": i.get("distrito"), "provincia": i.get("provincia"),
               "departamento": i.get("dpto"), "tipo": i.get("tipo"), "numero": i.get("num"),
               "fecha": i.get("fecha"), "hora": i.get("hora")} for i in rep[:12]]
    dn = D.get("danos") or {}
    lab = dn.get("labels") or {}
    per = lambda e: (e["totales"].get("personas_damnificadas") or 0) + (e["totales"].get("personas_afectadas") or 0)  # noqa: E731
    danos = [{"evento": e.get("evento"), "distrito": e.get("distrito"), "departamento": e.get("dpto"),
              "ocurrencia": e.get("ocurrencia"), "sigue_reportando_desde_antes": not e.get("nuevo"),
              "cifras": {lab.get(k, k): v for k, v in e["totales"].items() if v}}
             for e in sorted(dn.get("eventos") or [], key=per, reverse=True)[:6]]
    ayer = (now - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
    sismos = [{"fecha": s[0], "hora": s[1], "magnitud": s[2], "profundidad_km": s[3], "referencia": s[6]}
              for s in D["sismos"] if f"{s[0]} {s[1]}" >= ayer and s[2] >= 4.0][:10]
    alertas_activas = sum(1 for a in D["alertas"] if a.get("estado") != "Extinguido")
    enfen = (D.get("enfen") or {}).get("ultimo_comunicado") or {}
    dossier = {
        "alcance": "briefing", "fecha": hoy, "hora_corte": now.strftime("%H:%M"),
        "avisos_senamhi": {"provincias_por_nivel_hoy": por_nivel, "vigentes": avisos},
        "indeci_ultimas_horas": {"ventana_horas": D.get("indeciWindowH"), "reportes": len(rep), "recientes": indeci},
        "danos": {"ventana_dias": dn.get("dias"), "eventos_con_cifras": dn.get("n_eventos"),
                  "eventos_nuevos": dn.get("n_nuevos"), "mayor_impacto": danos},
        "sismos_24h_magnitud_4_o_mas": sismos,
        "incendios": {"alertas_no_extinguidas": alertas_activas, "focos_calor_24h": len(D["focos"])},
        "enfen": {k: enfen.get(k) for k in ("titulo", "estado", "fecha") if enfen.get(k)},
    }
    salud = {h["id"]: h for h in snapshot.health()}   # build() no incluye la salud; la API la agrega aparte
    cita = [{"institucion": SOURCES[s]["org"], "documento": SOURCES[s]["name"], "hora_dato": _iso(salud.get(s, {}).get("last_ok")),
             "url": None} for s in ("senamhi_avisos", "indeci", "igp", "serfor", "enfen") if s in SOURCES]
    return dossier, cita, f"Briefing {now.strftime('%d/%m %H:%M')}"


# ── Verificación de cifras ───────────────────────────────────────────────────
_URL = re.compile(r"https?://\S+")
_NUM = re.compile(r"\d{1,3}(?:[   .,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
_LETRAS = re.compile(r"\b(cero|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|"
                     r"dieci\w+|veinte|veinti\w+|treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa|cien|"
                     r"ciento|cientos|doscient\w+|trescient\w+|cuatrocient\w+|quinient\w+|seiscient\w+|setecient\w+|"
                     r"ochocient\w+|novecient\w+|mil|miles|millon|millones|docenas?|decenas?|centenar(?:es)?|millar(?:es)?)\b")


def _canon(txt):
    """Todas las lecturas posibles de un número escrito: '1.843' puede ser 1843 o 1,843."""
    out = set()
    s = txt.replace(" ", " ").replace(" ", " ")

    def add(x):
        try:
            out.add(Decimal(x).normalize())
        except InvalidOperation:
            pass
    if re.fullmatch(r"\d{1,3}(?:[ .,]\d{3})+(?:[.,]\d+)?", s):
        m = re.fullmatch(r"(\d{1,3}(?:[ .,]\d{3})+)(?:[.,](\d+))?", s)
        entero = re.sub(r"[ .,]", "", m.group(1))
        add(entero + ("." + m.group(2) if m.group(2) else ""))
    if re.fullmatch(r"\d+[.,]\d+", s):
        add(s.replace(",", "."))
    if re.fullmatch(r"\d+", s):
        add(s)
    if not out:
        add(re.sub(r"[ .,]", "", s))
    return out


def numeros_de(obj, acc=None):
    """Todas las cifras presentes en el dossier (valores y textos), sin contar las de los enlaces."""
    acc = set() if acc is None else acc
    if isinstance(obj, bool) or obj is None:
        return acc
    if isinstance(obj, (int, float)):
        acc.add(Decimal(str(obj)).normalize())
    elif isinstance(obj, str):
        for t in _NUM.findall(_URL.sub(" ", obj)):
            acc |= _canon(t)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            numeros_de(k, acc)
            numeros_de(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            numeros_de(v, acc)
    return acc


def _sin_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def verificar(piezas, dossier, limites=None):
    """Compara cada cifra del texto con las del dossier. Devuelve el informe completo."""
    permitidos = numeros_de(dossier)
    cifras, sobran, letras, largos = [], [], [], []
    for p in piezas:
        texto = _URL.sub(" ", p["texto"])
        for t in _NUM.findall(texto):
            ok = bool(_canon(t) & permitidos)
            cifras.append({"cifra": t, "pieza": p["etiqueta"], "en_datos": ok})
            if not ok:
                sobran.append(t)
        letras += [w for w in _LETRAS.findall(_sin_tildes(texto))]
        mx = (limites or {}).get(p["etiqueta"])
        if mx and len(p["texto"]) > mx:
            largos.append({"pieza": p["etiqueta"], "caracteres": len(p["texto"]), "maximo": mx})
    return {"ok": not sobran and not letras, "cifras": cifras, "no_encontradas": sorted(set(sobran)),
            "en_letras": sorted(set(letras)), "excede_largo": largos}


# ── Modelo ───────────────────────────────────────────────────────────────────
_cliente = None


def habilitado():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _llamar(tipo, dossier, correccion=None):
    import anthropic
    global _cliente
    if _cliente is None:
        _cliente = anthropic.Anthropic(timeout=180.0, max_retries=2)
    nombre, _, instr, _ = TIPOS[tipo]
    tarea = f"TAREA: {nombre}.\n{instr}"
    if correccion:
        tarea += ("\n\nUn borrador anterior no pasó la verificación automática de cifras. " + correccion +
                  " Reescribe usando solo cifras que estén en DATOS, con dígitos.")
    try:
        r = _cliente.beta.messages.parse(
            model=MODELO, max_tokens=16000, system=SISTEMA,
            output_config={"effort": ESFUERZO}, output_format=Redaccion,
            betas=[FALLBACK_BETA], fallbacks="default",
            messages=[{"role": "user", "content": f"{tarea}\n\nDATOS:\n{json.dumps(dossier, ensure_ascii=False, indent=1)}"}],
        )
    except anthropic.AuthenticationError:
        raise IAError("La clave de la API de Claude no es válida.", 503) from None
    except anthropic.RateLimitError:
        raise IAError("La API de Claude está limitando las solicitudes. Intente en un minuto.", 429) from None
    except anthropic.BadRequestError as e:
        raise IAError(f"Solicitud rechazada por la API: {e.message}", 502) from None
    except anthropic.APIStatusError as e:
        raise IAError(f"Error de la API de Claude ({e.status_code}). Intente de nuevo.", 502) from None
    except anthropic.APIConnectionError:
        raise IAError("No hay conexión con la API de Claude.", 503) from None
    if r.stop_reason == "refusal":
        raise IAError("El modelo declinó redactar este contenido.", 422)
    if r.stop_reason == "max_tokens" or r.parsed_output is None:
        raise IAError("El modelo no devolvió una redacción completa. Intente de nuevo.", 502)
    uso = {"modelo": r.model, "entrada": r.usage.input_tokens, "salida": r.usage.output_tokens}
    return r.parsed_output, uso


# ── Flujo de borradores ──────────────────────────────────────────────────────
def _audit(c, bid, accion, actor, origen, detalle=None):
    c.execute("INSERT INTO ia_auditoria(ts,borrador_id,accion,actor,origen,detalle) VALUES(?,?,?,?,?,?)",
              (time.time(), bid, accion, actor, origen, json.dumps(detalle or {}, ensure_ascii=False)))


def _actor(actor):
    a = (actor or "").strip()
    if not a or len(a) > 80:
        raise IAError("Indique su nombre (máximo 80 caracteres): toda acción queda registrada.")
    return a


def generar(tipo, alcance, ref, actor, origen=None):
    actor = _actor(actor)
    if tipo not in TIPOS or alcance not in TIPOS[tipo][1]:
        raise IAError(f"Tipo «{tipo}» no válido para «{alcance}».")
    if not habilitado():
        raise IAError("El asistente de IA no está configurado: falta la clave de la API de Claude.", 503)
    if alcance == "registro":
        dossier, cita, titulo = dossier_registro(ref["source"], ref["kind"], ref["key"])
    else:
        dossier, cita, titulo = dossier_briefing()
    sha = hashlib.sha256(json.dumps(dossier, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    limites = TIPOS[tipo][3]
    try:
        red, uso = _llamar(tipo, dossier)
        piezas = [p.model_dump() for p in red.piezas]
        ver = verificar(piezas, dossier, limites)
        intentos = [{"verificacion_ok": ver["ok"], **uso}]
        if not ver["ok"]:
            problemas = []
            if ver["no_encontradas"]:
                problemas.append("Estas cifras no están en DATOS: " + ", ".join(ver["no_encontradas"]) + ".")
            if ver["en_letras"]:
                problemas.append("Estas palabras son números en letras: " + ", ".join(ver["en_letras"]) + ".")
            red, uso = _llamar(tipo, dossier, " ".join(problemas))
            piezas = [p.model_dump() for p in red.piezas]
            ver = verificar(piezas, dossier, limites)
            intentos.append({"verificacion_ok": ver["ok"], **uso})
    except IAError as e:
        with db.tx() as c:
            _audit(c, None, "error", actor, origen, {"tipo": tipo, "alcance": alcance, "ref": ref, "error": str(e)})
        raise
    estado = "borrador" if ver["ok"] else "bloqueado"
    ver["advertencia_modelo"] = red.advertencia or None
    now = time.time()
    with db.tx() as c:
        bid = c.execute(
            "INSERT INTO ia_borradores(creado,actualizado,tipo,alcance,ref,titulo,estado,modelo,dossier,dossier_sha,"
            "contenido,cita,verificacion,uso,autor) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, now, tipo, alcance, json.dumps(ref, ensure_ascii=False) if ref else None, titulo, estado, MODELO,
             json.dumps(dossier, ensure_ascii=False), sha, json.dumps(piezas, ensure_ascii=False),
             json.dumps(cita, ensure_ascii=False), json.dumps(ver, ensure_ascii=False),
             json.dumps(intentos, ensure_ascii=False), actor)).lastrowid
        _audit(c, bid, "generado" if estado == "borrador" else "bloqueado", actor, origen,
               {"tipo": tipo, "dossier_sha": sha, "intentos": intentos,
                "no_encontradas": ver["no_encontradas"], "en_letras": ver["en_letras"]})
    return obtener(bid)


def _fila(bid):
    r = db.conn().execute("SELECT * FROM ia_borradores WHERE id=?", (bid,)).fetchone()
    if r is None:
        raise IAError("Borrador inexistente.", 404)
    return r


def obtener(bid, con_dossier=False):
    r = dict(_fila(bid))
    for k in ("ref", "contenido", "cita", "verificacion", "uso", "dossier"):
        r[k] = json.loads(r[k]) if r.get(k) else None
    if not con_dossier:
        r.pop("dossier")
    if r["estado"] == "bloqueado":
        r["contenido"] = None     # un texto que no pasó la verificación no se entrega
    r["tipo_nombre"] = TIPOS.get(r["tipo"], (r["tipo"],))[0]
    r["auditoria"] = [{**dict(a), "detalle": json.loads(a["detalle"] or "{}")} for a in db.conn().execute(
        "SELECT ts,accion,actor,origen,detalle FROM ia_auditoria WHERE borrador_id=? ORDER BY id", (bid,))]
    return r


def listar(estado=None, limit=50):
    q, args = "SELECT id,creado,actualizado,tipo,alcance,titulo,estado,autor,aprobado_por FROM ia_borradores", []
    if estado:
        q, args = q + " WHERE estado=?", [estado]
    rows = [dict(r) for r in db.conn().execute(q + " ORDER BY id DESC LIMIT ?", (*args, int(limit)))]
    for r in rows:
        r["tipo_nombre"] = TIPOS.get(r["tipo"], (r["tipo"],))[0]
    return rows


def editar(bid, piezas, actor, origen=None):
    actor = _actor(actor)
    r = _fila(bid)
    if r["estado"] != "borrador":
        raise IAError(f"Solo se editan borradores; este está «{r['estado']}».", 409)
    nuevas = [{"etiqueta": str(p["etiqueta"])[:40], "texto": str(p["texto"])} for p in piezas]
    ver = verificar(nuevas, json.loads(r["dossier"]), TIPOS[r["tipo"]][3])
    ver["advertencia_modelo"] = json.loads(r["verificacion"]).get("advertencia_modelo")
    with db.tx() as c:
        c.execute("UPDATE ia_borradores SET contenido=?, verificacion=?, actualizado=? WHERE id=?",
                  (json.dumps(nuevas, ensure_ascii=False), json.dumps(ver, ensure_ascii=False), time.time(), bid))
        _audit(c, bid, "editado", actor, origen, {"antes": json.loads(r["contenido"]), "despues": nuevas,
                                                  "no_encontradas": ver["no_encontradas"], "en_letras": ver["en_letras"]})
    return obtener(bid)


def aprobar(bid, actor, confirmar_cifras=False, origen=None):
    actor = _actor(actor)
    r = _fila(bid)
    if r["estado"] != "borrador":
        raise IAError(f"Solo se aprueban borradores; este está «{r['estado']}».", 409)
    ver = json.loads(r["verificacion"])
    if not ver["ok"] and not confirmar_cifras:
        # Tras una edición humana puede haber cifras nuevas: se aprueba solo si alguien lo asume por escrito.
        raise IAError("El texto editado tiene cifras que no están en los datos de origen: "
                      + ", ".join(ver["no_encontradas"] + ver["en_letras"])
                      + ". Confirme explícitamente para aprobarlo bajo su responsabilidad.", 409)
    now = time.time()
    with db.tx() as c:
        c.execute("UPDATE ia_borradores SET estado='aprobado', aprobado_por=?, aprobado_en=?, actualizado=? WHERE id=?",
                  (actor, now, now, bid))
        _audit(c, bid, "aprobado", actor, origen, {"cifras_no_verificadas": ver["no_encontradas"] + ver["en_letras"],
                                                   "confirmado_por_editor": bool(confirmar_cifras and not ver["ok"])})
    return obtener(bid)


def descartar(bid, actor, motivo=None, origen=None):
    actor = _actor(actor)
    r = _fila(bid)
    if r["estado"] not in ("borrador", "bloqueado"):
        raise IAError(f"No se puede descartar un borrador «{r['estado']}».", 409)
    with db.tx() as c:
        c.execute("UPDATE ia_borradores SET estado='descartado', actualizado=? WHERE id=?", (time.time(), bid))
        _audit(c, bid, "descartado", actor, origen, {"motivo": (motivo or "").strip()[:500] or None})
    return obtener(bid)


def texto_final(bid, actor, origen=None):
    """El texto listo para usar. Solo existe para borradores aprobados; cada entrega se registra."""
    actor = _actor(actor)
    r = obtener(bid)
    if r["estado"] != "aprobado":
        raise IAError("Solo se entrega el texto de borradores aprobados.", 403)
    partes = [f"{p['etiqueta']}\n{p['texto']}" for p in r["contenido"]]
    fuentes = "; ".join(" · ".join(x for x in (c["institucion"], c["documento"], c.get("hora_dato"), c.get("url")) if x)
                        for c in r["cita"])
    texto = "\n\n".join(partes) + f"\n\nFuente: {fuentes}\nBorrador asistido por IA, revisado y aprobado por {r['aprobado_por']}."
    with db.tx() as c:
        _audit(c, bid, "entregado", actor, origen, {})
    return texto
