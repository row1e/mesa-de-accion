"""Respaldo diario de collector/data/ a la carpeta indicada en MESA_BACKUP (no hay destino por defecto).

- Base: copia consistente con la API de respaldo de SQLite (segura mientras el colector escribe), comprimida.
  Retención: las últimas KEEP_DAILY diarias + la primera de cada mes (KEEP_MONTHLY meses).
- media/ (fotos INDECI, imágenes satelitales): copia incremental; nunca borra en el destino.
- raw/: un .tar.gz por día ya cerrado (hoy todavía se está escribiendo). Retención: KEEP_RAW_DAYS días.

Restaurar: ver RESTAURAR.txt en el destino.
"""
import datetime
import gzip
import os
import pathlib
import shutil
import sqlite3
import tarfile
import time

from . import config

DEST = pathlib.Path(os.environ["MESA_BACKUP"]).expanduser() if os.environ.get("MESA_BACKUP") else None
KEEP_DAILY, KEEP_MONTHLY, KEEP_RAW_DAYS = 14, 12, 30

RESTORE = """Restaurar el colector Mesa de Acción desde este respaldo
=======================================================

1. Detener el colector:
   launchctl bootout gui/$(id -u)/pe.mesadeaccion.collector

2. Base de datos (elegir el archivo más reciente de db/):
   gunzip -c "db/mesa-AAAA-MM-DD_HHMM.sqlite3.gz" > collector/data/mesa.sqlite3
   rm -f collector/data/mesa.sqlite3-wal collector/data/mesa.sqlite3-shm

3. Fotos e imágenes:
   rsync -a media/ collector/data/media/

4. Crudos (opcional, historial de respuestas originales):
   for f in raw/raw-*.tar.gz; do tar -xzf "$f" -C collector/data/; done

5. Volver a iniciar:
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/pe.mesadeaccion.collector.plist
"""


def _db_snapshot(stamp):
    """Copia consistente de la base con sqlite3.Connection.backup, luego gzip."""
    out_dir = DEST / "db"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f".mesa-{stamp}.sqlite3.tmp"
    src = sqlite3.connect(config.DB_PATH, timeout=60)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    final = out_dir / f"mesa-{stamp}.sqlite3.gz"
    with open(tmp, "rb") as fi, gzip.open(final, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo)
    tmp.unlink()
    return final


def _prune_db():
    files = sorted((DEST / "db").glob("mesa-*.sqlite3.gz"))
    by_day = {}
    for f in files:                                   # una por día: la más reciente
        by_day[f.name[5:15]] = f
    days = sorted(by_day)
    keep = set(days[-KEEP_DAILY:])
    months = {}
    for d in days:                                    # primera de cada mes
        months.setdefault(d[:7], d)
    keep |= set(sorted(months.values())[-KEEP_MONTHLY:])
    removed = 0
    for f in files:
        if f.name[5:15] not in keep or by_day[f.name[5:15]] != f:
            f.unlink()
            removed += 1
    return removed


def _sync_media():
    src, dst = config.DATA / "media", DEST / "media"
    copied = 0
    for p in src.rglob("*"):
        if not p.is_file() or p.suffix == ".blank":
            continue
        q = dst / p.relative_to(src)
        if q.exists() and q.stat().st_size == p.stat().st_size:
            continue
        q.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, q)
        copied += 1
    return copied


def _archive_raw():
    """Un .tar.gz por cada día cerrado de raw/ que aún no esté respaldado."""
    today = datetime.date.today().isoformat()
    out = DEST / "raw"
    out.mkdir(parents=True, exist_ok=True)
    days = sorted({d.name for d in config.RAW.glob("*/*") if d.is_dir() and d.name < today})
    made = 0
    for day in days:
        target = out / f"raw-{day}.tar.gz"
        if target.exists():
            continue
        tmp = out / f".raw-{day}.tar.gz.tmp"
        with tarfile.open(tmp, "w:gz") as tar:
            for d in sorted(config.RAW.glob(f"*/{day}")):
                tar.add(d, arcname=str(d.relative_to(config.DATA)))
        tmp.rename(target)
        made += 1
    cutoff = (datetime.date.today() - datetime.timedelta(days=KEEP_RAW_DAYS)).isoformat()
    for f in out.glob("raw-*.tar.gz"):
        if f.name[4:14] < cutoff:
            f.unlink()
    return made


def _size(path):
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def run():
    """Job del programador: devuelve (total, nuevos, mensaje)."""
    t0 = time.time()
    if DEST is None:
        raise RuntimeError("sin destino de respaldo configurado: los datos no se están respaldando. Defina MESA_BACKUP "
                           "(en el LaunchAgent) con la carpeta de destino.")
    try:
        DEST.mkdir(parents=True, exist_ok=True)
        (DEST / ".escritura").write_text(str(t0))
    except OSError as e:
        raise RuntimeError(f"no se puede escribir en {DEST} ({e.strerror}). "
                           "Revise que la carpeta de MESA_BACKUP exista y sea escribible.") from None
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    snap = _db_snapshot(stamp)
    pruned = _prune_db()
    media = _sync_media()
    raw_days = _archive_raw()
    (DEST / "RESTAURAR.txt").write_text(RESTORE, encoding="utf-8")
    total = _size(DEST)
    msg = (f"base {snap.stat().st_size / 1e6:.1f} MB · {media} archivos de media nuevos · {raw_days} días de crudos archivados"
           f" · {pruned} copias viejas eliminadas · destino {total / 1e6:.0f} MB en {time.time() - t0:.0f} s")
    (DEST / "ULTIMO_RESPALDO.txt").write_text(f"{datetime.datetime.now():%Y-%m-%d %H:%M}\n{msg}\n", encoding="utf-8")
    return 1, 1, msg
