"""Mesa de Acción de escritorio (Windows y macOS): el colector y el tablero en segundo plano, con un ícono junto al
reloj (Windows) o en la barra de menús (Mac).

Es el punto de entrada de la app empaquetada (PyInstaller; instalador Inno Setup en windows/, imagen .dmg en mac/).
Datos, configuración y registro quedan en %LOCALAPPDATA%\\MesaDeAccion (Windows) o en
~/Library/Application Support/MesaDeAccion (Mac):
  config.env    ajustes (MESA_MARCA, ANTHROPIC_API_KEY…); en Windows lo escribe el instalador, en Mac la propia app
  data/         base, crudos y fotos
  respaldos/    copia diaria de data/
  logs/mesa.log registro del colector

`--minimizado` (así arranca con la sesión) no abre el navegador. Si ya hay una Mesa corriendo, abrir la app de nuevo
solo abre el tablero.
"""
import os
import pathlib
import plistlib
import socket
import subprocess
import sys
import threading
import time
import webbrowser

MAC = sys.platform == "darwin"
if os.environ.get("MESA_BASE"):            # pruebas: otra carpeta de datos
    BASE = pathlib.Path(os.environ["MESA_BASE"])
elif MAC:
    BASE = pathlib.Path.home() / "Library" / "Application Support" / "MesaDeAccion"
else:
    BASE = pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home()) / "MesaDeAccion"
os.environ.setdefault("MESA_CONFIG", str(BASE / "config.env"))
os.environ.setdefault("MESA_DATA", str(BASE / "data"))
os.environ.setdefault("MESA_BACKUP", str(BASE / "respaldos"))
(BASE / "logs").mkdir(parents=True, exist_ok=True)
LOG = open(BASE / "logs" / "mesa.log", "a", encoding="utf-8", buffering=1)   # noqa: SIM115 — dura lo que dura la app
if getattr(sys, "frozen", False) or sys.stdout is None:
    sys.stdout = sys.stderr = LOG          # app de ventana: no hay consola; uvicorn y logging escriben aquí

# ── Mac: lo que en Windows hace el instalador (configuración inicial e inicio con la sesión) ─────────────────────
RELE = "https://mesa.ondaai.com"
AGENTE = pathlib.Path.home() / "Library" / "LaunchAgents" / "pe.mesadeaccion.app.plist"


def _leer_config():
    f = pathlib.Path(os.environ["MESA_CONFIG"])
    try:
        return f.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, UnicodeDecodeError):
        return []


def _guardar_config(cambios):
    """Como el instalador de Windows: conserva lo que haya, fija marca y relé, y reemplaza solo lo que se ingresó."""
    cambios = {"MESA_MARCA": "irtp", "MESA_RELE_SERVIDOR": RELE, **{k: v for k, v in cambios.items() if v}}
    lineas = [ln for ln in _leer_config() if not ln.startswith("#") and ln.split("=", 1)[0].strip() not in cambios]
    lineas = ["# Configuración de Mesa de Acción. Una línea CLAVE=valor por ajuste; se aplica al reiniciar la Mesa."] + lineas
    lineas += [f"{k}={v}" for k, v in cambios.items()]
    f = pathlib.Path(os.environ["MESA_CONFIG"])
    f.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    f.chmod(0o600)                           # tiene la clave de Claude


def _dialogo(texto, oculto=False):
    """Cuadro de diálogo de macOS con un campo de texto. None si se cancela."""
    q = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')   # noqa: E731
    guion = (f'display dialog "{q(texto)}" default answer "" {"with hidden answer " if oculto else ""}'
             'with title "Mesa de Acción" buttons {"Omitir", "Guardar"} default button "Guardar"')
    try:
        r = subprocess.run(["osascript", "-e", guion, "-e", "text returned of result"], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def configurar_mac(preguntar):
    """Primera vez (o desde el menú): pide el código de licencia y la clave de Claude, como la pantalla del instalador
    de Windows. Lo que se deja vacío no cambia."""
    actual = {ln.split("=", 1)[0] for ln in _leer_config() if "=" in ln}
    cambios = {}
    if preguntar:
        lic = _dialogo("Pegue el código de licencia que le entregaron (empieza con MESA-). Sin él, la Mesa abre una página "
                       "para ingresarlo." + (" Deje vacío para conservar el actual." if "MESA_LICENCIA" in actual else ""))
        clave = _dialogo("Clave de la API de Claude (empieza con sk-ant-api). Es opcional: sin ella solo queda desactivado "
                         "el asistente. Se guarda únicamente en esta computadora.", oculto=True)
        cambios = {"MESA_LICENCIA": "".join((lic or "").split()), "ANTHROPIC_API_KEY": (clave or "").strip()}
    _guardar_config(cambios)


def _ejecutable_estable():
    """Ruta de la app si está en una ubicación fija (no la copia temporal que macOS usa al abrirla desde Descargas)."""
    exe = pathlib.Path(sys.executable)
    return None if "AppTranslocation" in str(exe) or str(exe).startswith("/Volumes/") else exe


def inicio_con_sesion(activar=None):
    """Mac: abrir la Mesa al iniciar sesión (LaunchAgent), para que siga recolectando. Sin argumento, dice si está activo
    y, si lo está, actualiza la ruta por si la app se movió."""
    if activar is None:
        activar = AGENTE.exists()
        if not activar:
            return False
    exe = _ejecutable_estable()
    if activar and exe and getattr(sys, "frozen", False):
        AGENTE.parent.mkdir(parents=True, exist_ok=True)
        AGENTE.write_bytes(plistlib.dumps({"Label": "pe.mesadeaccion.app", "ProgramArguments": [str(exe), "--minimizado"],
                                           "RunAtLoad": True, "ProcessType": "Interactive"}))
        return True
    if not activar:
        AGENTE.unlink(missing_ok=True)
    return False


primera_vez = not pathlib.Path(os.environ["MESA_CONFIG"]).exists()
if MAC:
    configurar_mac(preguntar=primera_vez and "--minimizado" not in sys.argv)

from mesa import config  # noqa: E402 — después de fijar rutas y configuración

URL = f"http://127.0.0.1:{config.PORT}/"


def en_marcha():
    try:
        socket.create_connection(("127.0.0.1", config.PORT), timeout=1).close()
        return True
    except OSError:
        return False


def abrir_carpeta():
    if MAC:
        subprocess.Popen(["open", str(BASE)])
    else:
        os.startfile(BASE)


def bandeja(server):
    """Ícono junto al reloj o en la barra de menús. Si no está disponible (p. ej. una sesión sin escritorio), sigue sin
    ícono."""
    try:
        import pystray
        from PIL import Image
        ico = config.ROOT / "windows" / "icono.png"
        imagen = Image.open(ico) if ico.exists() else Image.new("RGB", (64, 64), (193, 0, 45))

        def salir(icon):
            server.should_exit = True
            icon.stop()

        def reconfigurar():
            configurar_mac(preguntar=True)
            subprocess.run(["osascript", "-e", 'display dialog "Guardado. Se aplica al volver a abrir la Mesa (Salir y abrir '
                            'de nuevo)." with title "Mesa de Acción" buttons {"OK"} default button "OK"'], capture_output=True)

        extra = []
        if MAC:
            extra = [pystray.MenuItem("Abrir al iniciar sesión", lambda: inicio_con_sesion(not AGENTE.exists()),
                                      checked=lambda item: AGENTE.exists()),
                     pystray.MenuItem("Licencia y clave de Claude…", reconfigurar)]
        menu = pystray.Menu(pystray.MenuItem("Abrir tablero", lambda: webbrowser.open(URL), default=True),
                            pystray.MenuItem("Abrir carpeta de datos", abrir_carpeta),
                            *extra,
                            pystray.Menu.SEPARATOR,
                            pystray.MenuItem("Salir (deja de recolectar)", salir))
        pystray.Icon("mesa", imagen, "Mesa de Acción", menu).run()
    except Exception as e:  # noqa: BLE001
        print(f"Sin ícono ({type(e).__name__}: {e}); la Mesa sigue corriendo.", flush=True)
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
    if MAC:                  # como la casilla "iniciar con Windows" del instalador: activado la primera vez que corre instalada
        marca = BASE / ".inicio_con_sesion"
        if _ejecutable_estable() is None and getattr(sys, "frozen", False):
            subprocess.Popen(["osascript", "-e", 'display dialog "Para que la Mesa siga recolectando y se abra al iniciar sesión, '
                              'arrástrela a la carpeta Aplicaciones y ábrala desde allí." with title "Mesa de Acción" '
                              'buttons {"OK"} default button "OK"'])
        elif not marca.exists() and inicio_con_sesion(True):
            marca.touch()
        else:
            inicio_con_sesion()
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
