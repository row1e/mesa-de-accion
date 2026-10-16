"""Relé web, lado servidor: la Mesa en la nube encarga a las Mesas instaladas en el Perú las consultas a los sitios que
bloquean IPs de nubes (SENAMHI, PROVIAS, MIDIS, ANA; config.PROXY_HOSTS).

Cada Mesa instalada (rele_cliente.py) pregunta aquí si hay trabajo (POST /api/relay/poll, espera hasta 25 s), hace la
consulta desde su conexión peruana y devuelve la respuesta (POST /api/relay/result). Se identifica con su código de
licencia, firmado por el emisor: sin licencia válida no recibe trabajos ni puede entregar datos.

Se activa con MESA_RELE_HUB=1 (solo en el servidor). Si no hay ninguna Mesa conectada, http.abrir sigue con el relé SSH
(MESA_PROXY) o directo, como antes.
"""
import base64
import email.message
import io
import itertools
import queue
import threading
import time
import urllib.error

from . import config

ACTIVO_S = 60          # una Mesa que preguntó hace menos de esto cuenta como conectada
ESPERA_TOMA_S = 15     # si nadie toma el trabajo en este tiempo, se desiste (y http.abrir prueba otro camino)

_trabajos = queue.Queue()
_resultados = {}
_ids = itertools.count(1)
_lock = threading.Lock()
_clientes = {}          # cliente → {ultimo, atendidos, errores, version}
_licencias = {}         # código → (cliente, verificado_en)


def activo():
    return config.RELE_HUB


def cliente_de(codigo):
    """Nombre del cliente si el código de licencia es válido y está vigente; None si no."""
    from . import licencia
    c = "".join((codigo or "").split())
    hit = _licencias.get(c)
    if hit and time.time() - hit[1] < 600:
        return hit[0]
    try:
        d = licencia.verificar(c)
    except ValueError:
        return None
    if d["vence"] < licencia.hoy().isoformat():
        return None
    nombre = d.get("cliente") or "?"
    _licencias[c] = (nombre, time.time())
    return nombre


def conectados():
    ahora = time.time()
    return {n: c for n, c in _clientes.items() if ahora - c["ultimo"] < ACTIVO_S}


def disponible():
    return activo() and bool(conectados())


def estado():
    ahora = time.time()
    return {"activo": activo(), "en_cola": _trabajos.qsize(),
            "mesas": [{"cliente": n, "hace_s": round(ahora - c["ultimo"]), "atendidos": c["atendidos"], "errores": c["errores"],
                       "conectada": ahora - c["ultimo"] < ACTIVO_S} for n, c in sorted(_clientes.items())]}


def tomar(cliente, espera=25):
    """Lo llama el poll de una Mesa cliente: el próximo trabajo, o None si no hay en `espera` segundos."""
    with _lock:
        c = _clientes.setdefault(cliente, {"ultimo": 0, "atendidos": 0, "errores": 0})
        c["ultimo"] = time.time()
    fin = time.time() + espera
    while time.time() < fin:
        try:
            t = _trabajos.get(timeout=min(2, max(0.1, fin - time.time())))
        except queue.Empty:
            with _lock:
                _clientes[cliente]["ultimo"] = time.time()
            continue
        r = _resultados.get(t["id"])
        if r is None or r["tomado"] or r["cancelado"]:
            continue
        r["tomado"] = cliente
        return t
    return None


def entregar(cliente, tid, status=None, headers=None, body_b64=None, error=None):
    r = _resultados.get(tid)
    if not r or r["tomado"] != cliente:
        return False
    with _lock:
        c = _clientes.setdefault(cliente, {"ultimo": 0, "atendidos": 0, "errores": 0})
        c["ultimo"] = time.time()
        c["atendidos" if error is None else "errores"] += 1
    r.update(status=status, headers=headers or {}, body=base64.b64decode(body_b64) if body_b64 else b"", error=error)
    r["listo"].set()
    return True


class _Respuesta(io.BytesIO):
    """Lo mínimo de una respuesta de urllib que usa http.fetch: .status, .headers.get(), .read() y `with`."""

    def __init__(self, status, headers, body):
        super().__init__(body)
        self.status = status
        self.headers = email.message.Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v


def abrir(req, timeout):
    """Encarga la petición a una Mesa conectada y espera su respuesta. Errores como los de urllib."""
    tid = next(_ids)
    r = {"tomado": None, "cancelado": False, "listo": threading.Event()}
    _resultados[tid] = r
    _trabajos.put({"id": tid, "url": req.full_url, "method": req.get_method(), "headers": dict(req.header_items()),
                   "body": base64.b64encode(req.data).decode() if req.data else None, "timeout": timeout})
    try:
        fin_toma = time.time() + ESPERA_TOMA_S
        while not r["tomado"] and time.time() < fin_toma:
            time.sleep(0.2)
        if not r["tomado"]:
            r["cancelado"] = True
            raise urllib.error.URLError("ninguna Mesa del relé tomó la consulta")
        if not r["listo"].wait(timeout + 15):
            raise urllib.error.URLError(f"la Mesa {r['tomado']} no respondió a tiempo")
        if r["error"]:
            raise urllib.error.URLError(f"desde la Mesa {r['tomado']}: {r['error']}")
        if r["status"] >= 400:
            raise urllib.error.HTTPError(req.full_url, r["status"], f"HTTP {r['status']} (vía {r['tomado']})", None, io.BytesIO(r["body"]))
        return _Respuesta(r["status"], r["headers"], r["body"])
    finally:
        _resultados.pop(tid, None)
