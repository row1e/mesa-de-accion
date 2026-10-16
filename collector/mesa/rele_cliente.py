"""Relé web, lado cliente: esta Mesa (instalada en el Perú) atiende las consultas que le encarga la Mesa en la nube a
los sitios que bloquean IPs de nubes. Ver rele_hub.py.

Solo hace GET/POST por HTTPS a los dominios de config.PROXY_HOSTS (SENAMHI, PROVIAS, MIDIS, ANA), sin cookies ni
credenciales: aunque el servidor pidiera otra cosa, se rechaza. Se identifica con el código de licencia de esta Mesa.

Se activa con MESA_RELE_SERVIDOR (p. ej. https://mesa.ondaai.com); el instalador lo deja puesto. Sin licencia vigente
o sin servidor, no hace nada.
"""
import base64
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

log = logging.getLogger("mesa.rele")
HILOS = 3
_estado = {"activo": False, "atendidos": 0, "rechazados": 0, "errores": 0, "ultimo_ok": None, "ultimo_error": None}


def estado():
    return dict(_estado, servidor=config.RELE_SERVIDOR or None)


def _permitido(url):
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host == h or host.endswith("." + h) for h in config.PROXY_HOSTS)


def _post(ruta, datos, licencia, timeout):
    req = urllib.request.Request(config.RELE_SERVIDOR.rstrip("/") + ruta, data=json.dumps(datos).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Licencia {licencia}",
                                          "User-Agent": config.USER_AGENT})
    from .http import SSL_CONTEXT
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as r:
        cuerpo = r.read()
        return json.loads(cuerpo) if cuerpo else None


def _atender(t):
    """Hace la consulta encargada desde esta conexión. Devuelve el resultado para el servidor."""
    if not _permitido(t.get("url", "")) or t.get("method") not in ("GET", "POST", "HEAD"):
        _estado["rechazados"] += 1
        return {"id": t.get("id"), "error": "consulta no permitida por el relé de esta Mesa"}
    from .http import SSL_CONTEXT
    hdrs = {k: v for k, v in (t.get("headers") or {}).items() if k.lower() not in ("cookie", "authorization", "host")}
    req = urllib.request.Request(t["url"], data=base64.b64decode(t["body"]) if t.get("body") else None, method=t["method"], headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=min(float(t.get("timeout") or 60), 180), context=SSL_CONTEXT) as r:
            status, headers, body = r.status, dict(r.headers.items()), r.read()
    except urllib.error.HTTPError as e:
        status, headers, body = e.code, dict(e.headers.items()) if e.headers else {}, e.read() or b""
    except Exception as e:  # noqa: BLE001 — timeouts, DNS, TLS: se informan al servidor
        _estado["errores"] += 1
        _estado["ultimo_error"] = f"{type(e).__name__}: {e}"
        return {"id": t["id"], "error": f"{type(e).__name__}: {e}"}
    _estado["atendidos"] += 1
    _estado["ultimo_ok"] = time.time()
    return {"id": t["id"], "status": status, "headers": {k: v for k, v in headers.items() if k.lower() in ("date", "content-type", "last-modified")},
            "body_b64": base64.b64encode(body).decode()}


def _bucle():
    from . import licencia
    espera = 5
    while True:
        cod = licencia.codigo_actual()
        if not cod or (licencia.requerida() and licencia.bloqueada()):
            _estado["activo"] = False
            time.sleep(300)
            continue
        try:
            t = _post("/api/relay/poll", {"version": 1}, cod, timeout=40)
            _estado["activo"] = True
            espera = 5
            if t and t.get("id"):
                _post("/api/relay/result", _atender(t), cod, timeout=60)
        except urllib.error.HTTPError as e:
            _estado["activo"] = False
            _estado["ultimo_error"] = f"servidor: HTTP {e.code}"
            time.sleep(600 if e.code in (401, 403) else 60)       # licencia rechazada: no insistir seguido
        except Exception as e:  # noqa: BLE001 — sin internet, servidor caído: se reintenta con espera creciente
            _estado["activo"] = False
            _estado["ultimo_error"] = f"{type(e).__name__}: {e}"
            time.sleep(espera)
            espera = min(espera * 2, 300)


def start():
    if not config.RELE_SERVIDOR or config.RELE_HUB:
        return
    for i in range(HILOS):
        threading.Thread(target=_bucle, name=f"rele-{i}", daemon=True).start()
    log.info("Relé web: atendiendo consultas de %s con %d hilos", config.RELE_SERVIDOR, HILOS)
