"""Arranque: `python -m mesa` levanta el programador de fuentes y el servidor HTTP.

`python -m mesa run <fuente|all>` ejecuta fuentes una vez, sin servidor (útil para depurar).
"""
import logging
import sys

import uvicorn

from . import config, db, runner


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
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
