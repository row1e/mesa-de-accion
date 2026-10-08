"""Configuración del colector. Todo ajustable por variables de entorno MESA_* o por un archivo de configuración."""
import os
import pathlib
import sys

if getattr(sys, "frozen", False):        # app empaquetada para Windows (PyInstaller): recursos junto al ejecutable
    ROOT = PROJECT = pathlib.Path(sys._MEIPASS)
else:
    ROOT = pathlib.Path(__file__).resolve().parent.parent      # collector/
    PROJECT = ROOT.parent                                       # raíz del repositorio


def _cargar_config(path):
    """Archivo de configuración: una línea CLAVE=valor por ajuste (MESA_MARCA, ANTHROPIC_API_KEY, MESA_BACKUP…).
    Las líneas con # son comentarios. Lo que ya está en el entorno tiene prioridad sobre el archivo."""
    try:
        crudo = pathlib.Path(path).read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return
    try:
        lineas = crudo.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError:   # editado con el Bloc de notas antiguo o escrito por el instalador en ANSI (Windows-1252)
        lineas = crudo.decode("cp1252", "replace").splitlines()
    for ln in lineas:
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))


CONFIG_FILE = pathlib.Path(os.environ.get("MESA_CONFIG", ROOT / "local.env"))   # en la app de Windows: %LOCALAPPDATA%\MesaDeAccion\config.env
_cargar_config(CONFIG_FILE)
DATA = pathlib.Path(os.environ.get("MESA_DATA", ROOT / "data"))
RAW = DATA / "raw"
DB_PATH = DATA / "mesa.sqlite3"
REF = PROJECT / "ref"                                           # límites dpto/prov (INGEMMET, simplificados)
WEB = ROOT / "web"

HOST = os.environ.get("MESA_HOST", "127.0.0.1")
PORT = int(os.environ.get("MESA_PORT", "8787"))
MARCA = os.environ.get("MESA_MARCA", "").strip().lower()   # identidad visual del cliente (ver api.MARCAS); vacía = neutra
USER_AGENT = os.environ.get(
    "MESA_UA", "MesaDeAccion-Collector/0.1 (+monitoreo de fuentes oficiales; contacto: r@manya.pe)")

# Segundos entre ejecuciones por fuente (ver 04-live-service.md para la justificación).
INTERVALS = {
    "indeci": 5 * 60,
    "igp": 3 * 60,
    "senamhi_avisos": 15 * 60,
    "senamhi_hidro": 30 * 60,
    "serfor": 30 * 60,
    "ingemmet": 60 * 60,
    "firms": 60 * 60,
    "senamhi_pronostico": 4 * 3600,
    "senamhi_uv": 6 * 3600,
    "enfen": 12 * 3600,
    "provias": 15 * 60,       # visor de emergencias viales (SGCV)
    "provias_fotos": 10 * 60, # cronología y fotos, hasta 40 emergencias por turno
    "com_pnp": 15 * 60, "com_provias": 15 * 60, "com_mtc": 15 * 60, "com_mininter": 15 * 60,   # gob.pe, una fuente por institución
    "indeci_fotos": 5 * 60,
    "respaldo": 24 * 3600,    # copia diaria de data/ (mesa/backup.py)   # procesa hasta 15 PDF nuevos por turno (reportes de las últimas 72 h)
    "sidpol": 24 * 3600,      # el CSV es mensual; se baja solo si cambió el enlace
    "midis": 30 * 86400,      # solo el catálogo de ubigeos; los indicadores se piden por distrito bajo demanda
}
REFRESH_COOLDOWN = 60          # segundos mínimos entre refrescos manuales de una misma fuente
STALE_FACTOR = 3               # una fuente está "atrasada" si su último éxito supera 3× su intervalo
MIN_SHARE = 0.03               # fracción mínima del área provincial cubierta por un aviso para contarla
INDECI_PAGES_FIRST_RUN = 6     # páginas del feed a leer en el primer arranque (≈ 84 ítems)
