"""HTTP con reintentos, registro en `fetches` y guardado del crudo solo cuando cambia (por hash)."""
import gzip
import hashlib
import http.client
import ssl
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import truststore

from . import config, db, licencia

# Usa el almacén de certificados del sistema (como curl en macOS): el Python de uv trae su propio
# OpenSSL sin la cadena que exigen INGEMMET y ENFEN.
truststore.inject_into_ssl()

# Intermedios públicos que algunos servidores oficiales NO envían (INGEMMET solo manda su certificado).
# macOS completa la cadena solo; Linux (p. ej. Replit) no, y falla con "unable to get local issuer certificate".
EXTRA_CA = sorted((config.ROOT / "certs").glob("*.pem"))


def _ssl_context():
    ctx = ssl.create_default_context()          # almacén del sistema (truststore)
    try:
        for p in EXTRA_CA:
            ctx.load_verify_locations(str(p))
        return ctx
    except Exception:  # noqa: BLE001 — si el almacén del sistema no acepta extras, certifi + intermedios
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
        for p in EXTRA_CA:
            ctx.load_verify_locations(str(p))
        return ctx


SSL_CONTEXT = _ssl_context()


def _bloqueado(url):
    """Dominio que no responde a IPs de nubes (config.PROXY_HOSTS, incluye subdominios)."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in config.PROXY_HOSTS)


def _por_rele(url):
    return bool(config.PROXY) and _bloqueado(url)


_DIRECTO = urllib.request.build_opener(urllib.request.HTTPSHandler(context=SSL_CONTEXT))
_RELE = urllib.request.build_opener(urllib.request.ProxyHandler({"http": config.PROXY, "https": config.PROXY}),
                                    urllib.request.HTTPSHandler(context=SSL_CONTEXT)) if config.PROXY else None


# Certificados VENCIDOS que se siguen aceptando, fijados por la huella SHA-256 del certificado exacto (no del dominio).
# Solo se usan si la verificación normal falla por vencimiento: cuando la institución lo renueve, la conexión vuelve
# sola a la verificación normal y la entrada queda sin efecto (borrarla entonces). Un certificado distinto, aunque
# sea del mismo dominio, se rechaza: esto no abre la puerta a un intermediario, solo tolera la fecha.
CERT_VENCIDO_FIJADO = {
    # *.serfor.gob.pe, venció el 2026-11-18 23:59 UTC (Sectigo); SERFOR aún no lo renueva.
    "geo.serfor.gob.pe": "4ecfbed7c5b4d37fc1bd3e55591e017dce341f426ec3d0e7cc7da46d7b15817c",
}


class _ConexionFijada(http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        der = self.sock.getpeercert(binary_form=True)
        if hashlib.sha256(der).hexdigest() != CERT_VENCIDO_FIJADO.get(self.host):
            self.close()
            raise ssl.SSLError(f"certificado de {self.host} vencido y distinto del fijado: no se acepta")


class _ManejadorFijado(urllib.request.HTTPSHandler):
    def https_open(self, req):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE        # la huella fijada reemplaza a la cadena y a la fecha
        return self.do_open(_ConexionFijada, req, context=ctx)


_FIJADO = urllib.request.build_opener(_ManejadorFijado())


def _vencido(e):
    r = getattr(e, "reason", e)
    # Linux (OpenSSL): verify_code 10 = X509_V_ERR_CERT_HAS_EXPIRED. macOS (truststore): sin código, "is expired".
    return isinstance(r, ssl.SSLCertVerificationError) and (
        getattr(r, "verify_code", None) == 10 or "expired" in str(r).lower())


def abrir(req, timeout):
    try:
        return _abrir(req, timeout)
    except urllib.error.URLError as e:
        host = (urllib.parse.urlsplit(req.full_url).hostname or "").lower()
        if host in CERT_VENCIDO_FIJADO and _vencido(e):
            return _FIJADO.open(req, timeout=timeout)
        raise


def _abrir(req, timeout):
    """urlopen para los dominios bloqueados para nubes: primero una Mesa del relé web conectada (servidor con
    MESA_RELE_HUB), si no el relé SSH (MESA_PROXY; HTTPS cifrado de punta a punta con CONNECT), si no directo."""
    if config.RELE_HUB and _bloqueado(req.full_url):
        from . import rele_hub
        if rele_hub.disponible():
            try:
                return rele_hub.abrir(req, timeout)
            except urllib.error.HTTPError:
                raise
            except urllib.error.URLError:
                if not config.PROXY:
                    raise            # sin relé SSH de respaldo, el error del relé web es el que vale
    return (_RELE if _por_rele(req.full_url) else _DIRECTO).open(req, timeout=timeout)


TEXTY = {"html", "json", "geojson", "xml", "csv", "txt"}


class FetchError(RuntimeError):
    pass


def _quote(url):
    return urllib.parse.quote(url, safe=":/?&=%#+,;@~")  # INDECI usa "N.º" y tildes en rutas de PDF


def fetch(source, url, *, name=None, method="GET", timeout=60, retries=2, keep_raw=True,
          data=None, headers=None, key=None):
    """Devuelve bytes. Registra la petición; guarda el cuerpo en data/raw/<source>/<fecha>/ si su hash es nuevo.

    `data` y `headers` permiten POST con cuerpo (lo exige el JSON de REDInforma de MIDIS).
    `key` identifica la petición en `fetches`: en un POST la misma URL devuelve algo distinto
    según el cuerpo, así que se le pasa algo que los distinga para que el archivado por hash
    no confunda dos respuestas diferentes. En GET queda igual que antes (la propia URL).
    """
    url = _quote(url)
    ident = key or url
    started, last_err, status, body = time.time(), None, None, None
    hdrs = {"User-Agent": config.USER_AGENT, **(headers or {})}
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
            with abrir(req, timeout) as r:
                status, body = r.status, r.read()
                licencia.observar_cabecera(r.headers.get("Date"))   # hora real de las fuentes: el reloj del equipo puede mentir
            break
        except urllib.error.HTTPError as e:
            status, last_err = e.code, f"HTTP {e.code}"
            if e.code < 500:
                break
        except Exception as e:  # noqa: BLE001 — timeouts, DNS, TLS
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(1.5 * (attempt + 1))
    dur = int((time.time() - started) * 1000)
    sha = hashlib.sha256(body).hexdigest() if body is not None else None
    changed, path = None, None
    if body is not None and keep_raw:
        prev = db.conn().execute("SELECT sha256 FROM fetches WHERE source=? AND url=? AND sha256 IS NOT NULL "
                                 "ORDER BY started_at DESC LIMIT 1", (source, ident)).fetchone()
        changed = int(not prev or prev["sha256"] != sha)
        if changed:
            day = time.strftime("%Y-%m-%d", time.localtime(started))
            d = config.RAW / source / day
            d.mkdir(parents=True, exist_ok=True)
            fname = f"{time.strftime('%H%M%S', time.localtime(started))}_{name or 'body'}"
            if fname.rsplit(".", 1)[-1].lower() in TEXTY:      # HTML/JSON/CSV se comprimen ~10×; PDF e imágenes ya vienen comprimidos
                fname += ".gz"
                (d / fname).write_bytes(gzip.compress(body, compresslevel=6))
            else:
                (d / fname).write_bytes(body)
            path = str((d / fname).relative_to(config.DATA))
    db.conn().execute(
        "INSERT INTO fetches(source,url,started_at,duration_ms,http_status,bytes,sha256,changed,path,error) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (source, ident, started, dur, status, len(body) if body is not None else None, sha, changed, path,
         None if body is not None else last_err))
    if body is None:
        raise FetchError(f"{url}: {last_err}")
    return body


def fetch_json(source, url, **kw):
    return json.loads(fetch(source, url, **kw))


def fetch_text(source, url, encoding="utf-8", **kw):
    return fetch(source, url, **kw).decode(encoding, "replace")
