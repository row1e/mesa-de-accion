"""Utilidades compartidas por los probes: HTTP con reintentos y guardado de muestras."""
import json
import pathlib
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128 Safari/537.36"
ROOT = pathlib.Path(__file__).resolve().parent.parent
TODAY = time.strftime("%Y-%m-%d")


def fetch(url, method="GET", timeout=60, retries=2):
    url = urllib.parse.quote(url, safe=":/?&=%#+,;@~")  # INDECI usa "N.º" y tildes en rutas de PDF
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 — reportamos y reintentamos
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"fetch failed after {retries + 1} attempts: {url} ({last})")


def fetch_json(url, **kw):
    return json.loads(fetch(url, **kw))


def sample_dir(source):
    d = ROOT / "samples" / source / TODAY
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(source, name, data):
    path = sample_dir(source) / name
    if isinstance(data, (dict, list)):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    elif isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    return path
