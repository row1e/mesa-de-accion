"""Arranque: `python -m mesa` levanta el programador de fuentes y el servidor HTTP.

`python -m mesa run <fuente|all>` ejecuta fuentes una vez, sin servidor (útil para depurar).
"""
import logging
import re
import sys

import uvicorn

from . import config, db, runner


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    if len(sys.argv) > 2 and sys.argv[1] == "token":
        # token de integración para otra plataforma: se muestra una sola vez; en el servidor se guarda solo la huella
        import secrets
        from .integracion import huella
        nombre = sys.argv[2]
        if not re.fullmatch(r"[a-z0-9_-]{2,40}", nombre):
            sys.exit("el nombre va en minúsculas, sin espacios (p. ej. una-sola-vista)")
        tok = secrets.token_urlsafe(32)
        print(f"Token para {nombre} (entregarlo por un canal seguro; no se vuelve a mostrar):\n  {tok}\n")
        print(f"Agregar al entorno del servicio (separar con comas si ya hay otros):\n  MESA_INTEGRACION_TOKENS={nombre}:{huella(tok)}")
        return
    db.init()
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        from .sources import SOURCES
        targets = list(SOURCES) if len(sys.argv) < 3 or sys.argv[2] == "all" else sys.argv[2:]
        for s in targets:
            runner.run_source(s, trigger="cli")
            row = db.conn().execute("SELECT * FROM runs WHERE source=? ORDER BY id DESC LIMIT 1", (s,)).fetchone()
            print(f"{'OK ' if row['ok'] else 'ERR'} {s:20} {row['finished_at'] - row['started_at']:6.1f}s  {row['message']}")
        return
    runner.start()
    from .api import app
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
