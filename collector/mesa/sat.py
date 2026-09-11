"""Vista satelital de un punto con NASA GIBS (WMS, sin clave), cacheada en data/media/sat/.

GIBS devuelve una imagen negra (~3 KB) cuando aún no hay pasada para esa fecha: se detecta y se prueba otra capa/fecha.
"""
import datetime
import hashlib
import math
import urllib.parse

import pymupdf

from . import config
from .http import fetch

WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
CACHE = config.DATA / "media" / "sat"
LAYERS = {   # id → (capa color verdadero, capa de focos, etiqueta, resolución)
    "viirs": ("VIIRS_NOAA20_CorrectedReflectance_TrueColor", "VIIRS_NOAA20_Thermal_Anomalies_375m_All", "VIIRS · NOAA-20", "375 m"),
    "modis": ("MODIS_Terra_CorrectedReflectance_TrueColor", "MODIS_Terra_Thermal_Anomalies_All", "MODIS · Terra", "250 m"),
}
SIZE = 640


def _bbox(lat, lon, km):
    dlat = km / 111.0
    dlon = km / (111.0 * max(0.2, math.cos(math.radians(lat))))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def _blank(data):
    """True si la imagen es (casi) toda negra: GIBS aún no tiene pasada para esa fecha."""
    if len(data) < 6000:
        return True
    pix = pymupdf.Pixmap(data)
    s, step = pix.samples, pix.n * 53
    dark = sum(1 for i in range(0, len(s) - pix.n, step) if s[i] < 10 and s[i + 1] < 10 and s[i + 2] < 10)
    return dark / max(1, len(s) // step) > 0.6


def worldview(lat, lon, km, date, layer="viirs"):
    s, w, n, e = _bbox(lat, lon, km)
    base, fire, *_ = LAYERS[layer]
    return (f"https://worldview.earthdata.nasa.gov/?v={w:.4f},{s:.4f},{e:.4f},{n:.4f}&t={date}-T15%3A00%3A00Z"
            f"&l=Coastlines_15m,{fire},{base}")


def image(lat, lon, km, date, layer, fires=True):
    """Descarga (o toma de caché) una imagen. Devuelve (ruta_relativa, en_blanco)."""
    base, fire, *_ = LAYERS[layer]
    key = hashlib.sha1(f"{lat:.3f},{lon:.3f},{km},{date},{layer},{fires}".encode()).hexdigest()[:20]
    CACHE.mkdir(parents=True, exist_ok=True)
    path, blank_marker = CACHE / f"{key}.jpg", CACHE / f"{key}.blank"
    if blank_marker.exists():   # un día pasado sin imagen no cambia; para hoy se vuelve a probar cada 30 min
        import time
        if date < datetime.date.today().isoformat() or time.time() - blank_marker.stat().st_mtime < 1800:
            return None, True
    if not path.exists():
        s, w, n, e = _bbox(lat, lon, km)
        q = {"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": f"{base},{fire}" if fires else base,
             "CRS": "EPSG:4326", "BBOX": f"{s:.5f},{w:.5f},{n:.5f},{e:.5f}", "WIDTH": SIZE, "HEIGHT": SIZE,
             "FORMAT": "image/jpeg", "TIME": date}
        data = fetch("sat", f"{WMS}?{urllib.parse.urlencode(q)}", keep_raw=False, timeout=45)
        if _blank(data):
            blank_marker.touch()
            return None, True
        path.write_bytes(data)
    return f"/media/sat/{path.name}", False


def best(lat, lon, km, date, layer="auto", fires=True, back_days=3):
    """Mejor imagen disponible: la fecha pedida con VIIRS → MODIS → días anteriores. Devuelve dict o None."""
    order = ["viirs", "modis"] if layer == "auto" else [layer]
    d0 = datetime.date.fromisoformat(date)
    tried = []
    for back in range(back_days + 1):
        d = (d0 - datetime.timedelta(days=back)).isoformat()
        if d > datetime.date.today().isoformat():
            continue
        for ly in order:
            url, blank = image(lat, lon, km, d, ly, fires)
            tried.append(f"{d} {ly}{' (sin imagen)' if blank else ''}")
            if url:
                base, fire, label, res = LAYERS[ly]
                return {"url": url, "date": d, "requested": date, "layer": ly, "label": label, "res": res, "km": km,
                        "lat": lat, "lon": lon, "fires": fires, "worldview": worldview(lat, lon, km, d, ly), "tried": tried,
                        "credit": f"NASA GIBS / Worldview · {label} ({res}) · {d}"}
    return {"url": None, "requested": date, "tried": tried, "km": km, "lat": lat, "lon": lon,
            "worldview": worldview(lat, lon, km, date, "viirs")}
