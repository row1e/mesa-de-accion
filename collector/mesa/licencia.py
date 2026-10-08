"""Licencia de uso con fecha de vencimiento, para la app instalada en Windows.

Un código de licencia es "MESA-<datos>.<firma>": datos en JSON (cliente, vence, contacto, emitida) firmados con
Ed25519. La clave privada queda solo con quien emite (windows/emitir_licencia.py); aquí va la pública. Cambiar
cualquier dato del código invalida la firma.

Solo se exige en la app empaquetada (o con MESA_LICENCIA_REQUERIDA=1): corrida desde el código, la Mesa no la pide.
Vencida o ausente: no se consultan fuentes ni se redacta con IA, y el tablero muestra la página de licencia, donde
se puede pegar un código nuevo. Los datos ya guardados no se tocan.

Fecha confiable: la mayor entre la del reloj del equipo, la última vista en la cabecera Date de las fuentes
oficiales que consulta el colector y la mayor guardada antes. Atrasar el reloj no extiende la licencia.
"""
import base64
import datetime
import email.utils
import json
import os
import sys
import threading
import time
from zoneinfo import ZoneInfo

from . import config

PUBLICA = "TqiuE3sLIIjI1bvTDZP2Htx0o+YBeM0cj6qlzjXej5Q="
AVISO_DIAS = 10
LIMA = ZoneInfo("America/Lima")
ARCHIVO = config.DATA / "licencia.txt"            # código pegado desde el tablero (ver codigo_actual)
RELOJ = config.DATA / "licencia_reloj.json"       # mayor fecha vista (no retrocede)
_lock = threading.Lock()
_reloj = {"max": 0.0, "guardado": 0.0}


def requerida():
    return getattr(sys, "frozen", False) or os.environ.get("MESA_LICENCIA_REQUERIDA") == "1"


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verificar(codigo):
    """Datos de un código válido (firma correcta); ValueError con el motivo si no lo es. No mira la fecha."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    c = "".join((codigo or "").split())
    if not c.startswith("MESA-") or "." not in c:
        raise ValueError("El código no tiene el formato de una licencia (empieza con MESA-).")
    datos_b64, firma_b64 = c[5:].split(".", 1)
    try:
        datos, firma = _b64d(datos_b64), _b64d(firma_b64)
        Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLICA)).verify(firma, datos)
        d = json.loads(datos)
        datetime.date.fromisoformat(d["vence"])
    except InvalidSignature:
        raise ValueError("El código no es válido: la firma no corresponde (¿se copió incompleto o se modificó?).") from None
    except (ValueError, KeyError, json.JSONDecodeError):
        raise ValueError("El código está incompleto o dañado.") from None
    return d


def _cargar_reloj():
    if not _reloj["max"]:
        try:
            _reloj["max"] = float(json.loads(RELOJ.read_text(encoding="utf-8")).get("max") or 0)
        except (OSError, ValueError):
            pass


def observar(ts):
    """Registra una hora (epoch) confiable; guarda en disco a lo sumo cada 10 min."""
    with _lock:
        _cargar_reloj()
        if ts > _reloj["max"]:
            _reloj["max"] = ts
            if ts - _reloj["guardado"] > 600:
                try:
                    RELOJ.parent.mkdir(parents=True, exist_ok=True)
                    RELOJ.write_text(json.dumps({"max": ts}), encoding="utf-8")
                    _reloj["guardado"] = ts
                except OSError:
                    pass


def observar_cabecera(fecha_http):
    """Cabecera Date de una respuesta HTTP de una fuente oficial."""
    try:
        observar(email.utils.parsedate_to_datetime(fecha_http).timestamp())
    except (TypeError, ValueError, IndexError):
        pass


def hoy():
    observar(time.time())
    return datetime.datetime.fromtimestamp(_reloj["max"], LIMA).date()


def codigo_actual():
    """El código vigente de mayor vencimiento entre el pegado en el tablero (licencia.txt) y el del instalador
    (MESA_LICENCIA en config.env): una renovación cargada por cualquiera de los dos caminos manda."""
    cands = []
    try:
        cands.append(ARCHIVO.read_text(encoding="utf-8").strip())
    except OSError:
        pass
    cands.append((os.environ.get("MESA_LICENCIA") or "").strip())
    cands = [c for c in cands if c]
    validos = []
    for c in cands:
        try:
            validos.append((verificar(c)["vence"], c))
        except ValueError:
            pass
    return max(validos)[1] if validos else (cands[0] if cands else "")


def estado():
    """{requerida, valida, bloqueada, cliente, vence, dias, contacto, aviso, motivo}"""
    e = {"requerida": requerida(), "valida": False, "bloqueada": False, "cliente": None, "vence": None, "dias": None,
         "contacto": None, "aviso": False, "motivo": None}
    codigo = codigo_actual()
    if codigo:
        try:
            d = verificar(codigo)
            vence = datetime.date.fromisoformat(d["vence"])
            dias = (vence - hoy()).days
            e.update(cliente=d.get("cliente"), vence=d["vence"], dias=dias, contacto=d.get("contacto"), valida=dias >= 0,
                     aviso=0 <= dias <= AVISO_DIAS, motivo=None if dias >= 0 else f"La licencia venció el {vence.strftime('%d/%m/%Y')}.")
        except ValueError as err:
            e["motivo"] = str(err)
    else:
        e["motivo"] = "Esta instalación no tiene un código de licencia."
    e["bloqueada"] = e["requerida"] and not e["valida"]
    return e


def bloqueada():
    return requerida() and estado()["bloqueada"]


def guardar(codigo):
    """Valida y guarda un código pegado en el tablero. Devuelve el estado nuevo; ValueError si no sirve."""
    d = verificar(codigo)
    if datetime.date.fromisoformat(d["vence"]) < hoy():
        raise ValueError(f"Ese código ya venció ({d['vence']}). Pida uno vigente.")
    ARCHIVO.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVO.write_text("".join(codigo.split()), encoding="utf-8")
    return estado()
