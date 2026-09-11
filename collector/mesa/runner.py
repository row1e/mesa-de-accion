"""Ejecuta fuentes (programadas o manuales), registra `runs` y mantiene `source_health`."""
import logging
import threading
import time
import traceback

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from . import config, db
from .sources import SOURCES

log = logging.getLogger("mesa.runner")
_locks = {s: threading.Lock() for s in SOURCES}
_listeners = []          # callbacks sin argumentos, llamados tras cada ejecución (p. ej. invalidar caché)
scheduler = BackgroundScheduler(timezone="America/Lima", job_defaults={"coalesce": True, "max_instances": 1,
                                                                          "misfire_grace_time": 300})


def on_run(cb):
    _listeners.append(cb)


def run_source(source, trigger="schedule"):
    """Corre una fuente si no está ya corriendo. Devuelve False si se omitió por estar en curso."""
    lock = _locks[source]
    if not lock.acquire(blocking=False):
        return False
    started = time.time()
    run_id = db.conn().execute("INSERT INTO runs(source,trigger,started_at) VALUES(?,?,?)",
                               (source, trigger, started)).lastrowid
    db.health_set(source, running=1, last_run=started)
    try:
        total, new, msg = SOURCES[source]["fn"]()
        db.conn().execute("UPDATE runs SET finished_at=?, ok=1, items=?, new_items=?, message=? WHERE id=?",
                          (time.time(), total, new, msg, run_id))
        db.health_set(source, running=0, last_ok=time.time(), last_message=msg, consecutive_failures=0, items=total)
        log.info("%s ok (%.1fs): %s", source, time.time() - started, msg)
    except Exception as e:  # noqa: BLE001 — una fuente caída no afecta a las demás
        msg = f"{type(e).__name__}: {e}"
        db.conn().execute("UPDATE runs SET finished_at=?, ok=0, message=? WHERE id=?", (time.time(), msg[:2000], run_id))
        row = db.conn().execute("SELECT consecutive_failures FROM source_health WHERE source=?", (source,)).fetchone()
        db.health_set(source, running=0, last_error=time.time(), last_message=msg[:500],
                      consecutive_failures=(row["consecutive_failures"] or 0) + 1)
        log.warning("%s FAIL: %s\n%s", source, msg, traceback.format_exc(limit=3))
    finally:
        lock.release()
        for cb in _listeners:
            cb()
    return True


def request_refresh(source):
    """Refresco manual: respeta el enfriamiento y no duplica ejecuciones. Devuelve (aceptado, motivo)."""
    if _locks[source].locked():
        return False, "ya se está actualizando"
    row = db.conn().execute("SELECT last_manual FROM source_health WHERE source=?", (source,)).fetchone()
    if row and row["last_manual"] and time.time() - row["last_manual"] < config.REFRESH_COOLDOWN:
        return False, f"espere {int(config.REFRESH_COOLDOWN - (time.time() - row['last_manual']))} s"
    db.health_set(source, last_manual=time.time())
    threading.Thread(target=run_source, args=(source, "manual"), daemon=True, name=f"manual-{source}").start()
    return True, "en cola"


def start():
    now = time.time()
    for i, (source, _) in enumerate(SOURCES.items()):
        interval = config.INTERVALS[source]
        db.health_set(source, interval_s=interval, running=0)
        row = db.conn().execute("SELECT last_ok, last_run FROM source_health WHERE source=?", (source,)).fetchone()
        last = row["last_run"] or 0
        # Al arrancar: si el último intento es más viejo que el intervalo, corre pronto (escalonado 4 s por fuente).
        first = now + 4 * i if now - last >= interval else last + interval
        scheduler.add_job(run_source, IntervalTrigger(seconds=interval), args=(source,), id=source,
                          next_run_time=__import__("datetime").datetime.fromtimestamp(first), replace_existing=True)
    scheduler.start()


def next_runs():
    return {j.id: j.next_run_time.timestamp() if j.next_run_time else None for j in scheduler.get_jobs()}
