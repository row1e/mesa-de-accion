"""HTTP con reintentos, registro en `fetches` y guardado del crudo solo cuando cambia (por hash)."""
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import truststore

from . import config, db

# Usa el almacén de certificados del sistema (como curl en macOS): el Python de uv trae su propio
# OpenSSL sin la cadena que exigen INGEMMET y ENFEN.
truststore.inject_into_ssl()


class FetchError(RuntimeError):
    pass


def _quote(url):
    return urllib.parse.quote(url, safe=":/?&=%#+,;@~")  # INDECI usa "N.º" y tildes en rutas de PDF


def fetch(source, url, *, name=None, method="GET", timeout=60, retries=2, keep_raw=True):
    """Devuelve bytes. Registra la petición; guarda el cuerpo en data/raw/<source>/<fecha>/ si su hash es nuevo."""
    url = _quote(url)
    started, last_err, status, body = time.time(), None, None, None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent": config.USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status, body = r.status, r.read()
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
                                 "ORDER BY started_at DESC LIMIT 1", (source, url)).fetchone()
        changed = int(not prev or prev["sha256"] != sha)
        if changed:
            day = time.strftime("%Y-%m-%d", time.localtime(started))
            d = config.RAW / source / day
            d.mkdir(parents=True, exist_ok=True)
            fname = f"{time.strftime('%H%M%S', time.localtime(started))}_{name or 'body'}"
            (d / fname).write_bytes(body)
            path = str((d / fname).relative_to(config.DATA))
    db.conn().execute(
        "INSERT INTO fetches(source,url,started_at,duration_ms,http_status,bytes,sha256,changed,path,error) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (source, url, started, dur, status, len(body) if body is not None else None, sha, changed, path,
         None if body is not None else last_err))
    if body is None:
        raise FetchError(f"{url}: {last_err}")
    return body


def fetch_json(source, url, **kw):
    return json.loads(fetch(source, url, **kw))


def fetch_text(source, url, encoding="utf-8", **kw):
    return fetch(source, url, **kw).decode(encoding, "replace")
