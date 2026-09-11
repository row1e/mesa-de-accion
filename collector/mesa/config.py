"""Configuración del colector. Todo ajustable por variables de entorno MESA_*."""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent          # collector/
PROJECT = ROOT.parent                                           # raíz del repositorio
DATA = pathlib.Path(os.environ.get("MESA_DATA", ROOT / "data"))
RAW = DATA / "raw"
DB_PATH = DATA / "mesa.sqlite3"
REF = PROJECT / "ref"                                           # límites dpto/prov (INGEMMET, simplificados)
WEB = ROOT / "web"

HOST = os.environ.get("MESA_HOST", "127.0.0.1")
PORT = int(os.environ.get("MESA_PORT", "8787"))
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
    "provias": 60 * 60,
    "com_pnp": 15 * 60, "com_provias": 15 * 60, "com_mtc": 15 * 60, "com_mininter": 15 * 60,   # gob.pe, una fuente por institución
    "indeci_fotos": 5 * 60,
    "respaldo": 24 * 3600,    # copia diaria de data/ (mesa/backup.py)   # procesa hasta 15 PDF nuevos por turno (reportes de las últimas 72 h)
    "sidpol": 24 * 3600,      # el CSV es mensual; se baja solo si cambió el enlace
}
REFRESH_COOLDOWN = 60          # segundos mínimos entre refrescos manuales de una misma fuente
STALE_FACTOR = 3               # una fuente está "atrasada" si su último éxito supera 3× su intervalo
MIN_SHARE = 0.03               # fracción mínima del área provincial cubierta por un aviso para contarla
INDECI_PAGES_FIRST_RUN = 6     # páginas del feed a leer en el primer arranque (≈ 84 ítems)
