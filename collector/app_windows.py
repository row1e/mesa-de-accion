"""Mesa de Acción para Windows: el colector y el tablero en segundo plano, con un ícono junto al reloj.

Es el punto de entrada de la app empaquetada (PyInstaller + Inno Setup, ver windows/). Datos, configuración y
registro quedan en %LOCALAPPDATA%\\MesaDeAccion:
  config.env    ajustes (MESA_MARCA, ANTHROPIC_API_KEY…), lo escribe el instalador
  data\\         base, crudos y fotos
  respaldos\\    copia diaria de data\\
  logs\\mesa.log registro del colector

`MesaDeAccion.exe --minimizado` (así arranca con Windows) no abre el navegador. Si ya hay una Mesa corriendo,
abrir el ícono de nuevo solo abre el tablero.
"""
import os
import pathlib
import socket
import sys
import threading
import time
import webbrowser

BASE = pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home()) / "MesaDeAccion"
os.environ.setdefault("MESA_CONFIG", str(BASE / "config.env"))
os.environ.setdefault("MESA_DATA", str(BASE / "data"))
os.environ.setdefault("MESA_BACKUP", str(BASE / "respaldos"))
(BASE / "logs").mkdir(parents=True, exist_ok=True)
LOG = open(BASE / "logs" / "mesa.log", "a", encoding="utf-8", buffering=1)   # noqa: SIM115 — dura lo que dura la app
if getattr(sys, "frozen", False) or sys.stdout is None:
    sys.stdout = sys.stderr = LOG          # app de ventana: no hay consola; uvicorn y logging escriben aquí

from mesa import config  # noqa: E402 — después de fijar rutas y configuración

URL = f"http://127.0.0.1:{config.PORT}/"


def en_marcha():
    try:
        socket.create_connection(("127.0.0.1", config.PORT), timeout=1).close()
        return True
    except OSError:
        return False


def bandeja(server):
    """Ícono junto al reloj. Si la bandeja no está disponible (p. ej. una sesión sin escritorio), sigue sin ícono."""
    try:
        import pystray
        from PIL import Image
        ico = config.ROOT / "windows" / "icono.png"
        imagen = Image.open(ico) if ico.exists() else Image.new("RGB", (64, 64), (193, 0, 45))

        def salir(icon):
            server.should_exit = True
            icon.stop()

        menu = pystray.Menu(pystray.MenuItem("Abrir tablero", lambda: webbrowser.open(URL), default=True),
                            pystray.MenuItem("Abrir carpeta de datos", lambda: os.startfile(BASE)),
                            pystray.Menu.SEPARATOR,
                            pystray.MenuItem("Salir (deja de recolectar)", salir))
        pystray.Icon("mesa", imagen, "Mesa de Acción", menu).run()
    except Exception as e:  # noqa: BLE001
        print(f"Sin ícono en la bandeja ({type(e).__name__}: {e}); la Mesa sigue corriendo.", flush=True)
        while not server.should_exit:
            time.sleep(1)


def main():
    if en_marcha():          # ya hay una Mesa corriendo en esta computadora: solo abrir el tablero
        webbrowser.open(URL)
        return
    import logging

    import uvicorn

    from mesa import db, runner
    logging.basicConfig(level=logging.INFO, stream=LOG, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.info("Mesa de Acción · datos en %s · configuración %s", config.DATA, config.CONFIG_FILE)
    db.init()
    runner.start()
    from mesa.api import app
    server = uvicorn.Server(uvicorn.Config(app, host=config.HOST, port=config.PORT, log_level="info", log_config=None))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(120):     # hasta 60 s para que el servidor responda
        if en_marcha():
            break
        time.sleep(0.5)
    if "--minimizado" not in sys.argv:
        webbrowser.open(URL)
    bandeja(server)
    os._exit(0)              # el programador de fuentes corre en hilos propios: salir sin esperarlos


if __name__ == "__main__":
    main()
