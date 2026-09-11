"""API HTTP + dashboard en vivo (mismo origen). Sin autenticación: pensado para correr en localhost."""
import json
import time

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, db, detail, ficha, geo, latest, runner, sat, snapshot
from .sources import SOURCES

app = FastAPI(title="Mesa de Acción · Colector", version="0.1")
(config.DATA / "media").mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=config.DATA / "media"), name="media")   # fotos extraídas de los PDF


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.WEB / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/app.js", include_in_schema=False)
def app_js():
    return FileResponse(config.WEB / "app.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/vendor/{name}", include_in_schema=False)
def vendor(name: str):
    path = config.WEB / "vendor" / name
    if not path.is_file() or path.parent != config.WEB / "vendor":
        raise HTTPException(404)
    return FileResponse(path, media_type="text/javascript", headers={"Cache-Control": "max-age=86400"})


@app.get("/ficha/{ubigeo}.pdf", include_in_schema=False)
def ficha_pdf(ubigeo: str, request: Request):
    """Ficha en PDF (impresa por Chrome headless). 2 dígitos = departamento, 4 = provincia."""
    if not ficha.scope_of(ubigeo):
        raise HTTPException(404, "código desconocido: use 2 dígitos (departamento) o 4 (provincia)")
    try:
        data, fname = ficha.pdf(ubigeo, str(request.base_url).rstrip("/"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"No se pudo generar el PDF: {e}") from None
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/ficha/{ubigeo}", include_in_schema=False)
def ficha_html(ubigeo: str):
    """Ficha provincial imprimible (A4)."""
    res = ficha.html(ubigeo)
    if res is None:
        raise HTTPException(404, "código desconocido: use 2 dígitos (departamento) o 4 (provincia)")
    return Response(res[0], media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


@app.get("/api/ficha/{ubigeo}")
def ficha_json(ubigeo: str):
    """Datos de la ficha provincial (lo mismo que se imprime)."""
    d = ficha.build(ubigeo)
    if d is None:
        raise HTTPException(404, "ubigeo provincial desconocido")
    return d


@app.get("/api/health")
def health():
    return {"now": time.time(), "sources": snapshot.health()}


@app.get("/api/snapshot")
def get_snapshot():
    data = snapshot.build()
    body = json.dumps({**data, "health": snapshot.health()}, ensure_ascii=False, separators=(",", ":"))
    return Response(body, media_type="application/json", headers={"Cache-Control": "no-store"})


@app.post("/api/refresh/{source}")
def refresh(source: str):
    targets = list(SOURCES) if source == "all" else [source]
    if any(t not in SOURCES for t in targets):
        raise HTTPException(404, f"fuente desconocida: {source}")
    result = {t: dict(zip(("accepted", "reason"), runner.request_refresh(t))) for t in targets}
    return JSONResponse(result, status_code=202)


@app.get("/api/detail")
def get_detail(source: str, kind: str, key: str, fresh: bool = False):
    """Ficha completa de un registro con enlaces a la fuente. `fresh=true` ignora la caché del enriquecimiento."""
    d = detail.build(source, kind, key, fresh=fresh)
    if d is None:
        raise HTTPException(404, "registro no encontrado")
    return d


@app.get("/api/latest")
def get_latest(source: str | None = None, region: str | None = None, limit: int = Query(60, le=300),
               before: float | None = None, days: int = Query(7, le=60)):
    """Últimos registros recibidos (todas las fuentes o una), opcionalmente por región; paginar con `before`."""
    if source and source not in SOURCES:
        raise HTTPException(404, f"fuente desconocida: {source}")
    data = latest.feed(source=source, region=region, limit=limit, before=before, days=days)
    data["counts24h"] = latest.counts(region=region)
    return data


@app.get("/api/sat")
def get_sat(lat: float, lon: float, date: str, km: float = Query(25, ge=2, le=300), layer: str = "auto", fires: bool = True):
    """Vista satelital NASA GIBS centrada en un punto (mejor imagen disponible, cacheada)."""
    if layer not in ("auto", "viirs", "modis"):
        raise HTTPException(400, "layer: auto | viirs | modis")
    try:
        return sat.best(lat, lon, km, date, layer=layer, fires=fires)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"NASA GIBS no respondió: {e}") from None


@app.get("/api/runs")
def runs(source: str | None = None, limit: int = Query(50, le=500)):
    q, args = "SELECT * FROM runs", []
    if source:
        q, args = q + " WHERE source=?", [source]
    return [dict(r) for r in db.conn().execute(q + " ORDER BY started_at DESC LIMIT ?", (*args, limit))]


@app.get("/api/items/{source}/{kind}")
def items(source: str, kind: str, current: bool = True, limit: int = Query(1000, le=20000)):
    """Registros normalizados tal como se guardan (para integrar con otras aplicaciones)."""
    return db.get_items(source, kind, current_only=current, limit=limit)


@app.get("/api/provincia/{ubigeo}")
def provincia(ubigeo: str):
    """Todo lo que dicen las fuentes sobre una provincia (ubigeo INEI de 4 dígitos)."""
    p = geo.provinces().get(ubigeo)
    if not p:
        raise HTTPException(404, "ubigeo provincial desconocido")
    d = snapshot.build()
    name = geo.norm(p["nombre"])
    return {
        "ubigeo": ubigeo, "provincia": p["nombre"], "departamento": p["dpto"],
        "avisos_por_dia": {day: cells[ubigeo] for day, cells in d["levelsByDay"].items() if ubigeo in cells},
        "uv": d["uv"].get(ubigeo),
        "indeci": [i for i in d["indeci"] if i.get("prov") == ubigeo],
        "alertas_incendio": [a for a in d["alertas"] if str(a.get("ubigeo", "")).startswith(ubigeo)],
        "focos_24h": sum(1 for f in d["focos"] if geo.norm(f[3]) == name),
        "zonas_criticas": [z for z in d["zonas"] if geo.norm(z.get("provincia")) == name],
        "hidro": [h for h in d["hidro"] if geo.norm(h.get("nom_provincia")) == name],
        "denuncias_sidpol": ({"meses": d["sidpol"]["months"], "por_modalidad": d["sidpol"]["provs"].get(ubigeo)}
                             if d.get("sidpol") else None),
    }
