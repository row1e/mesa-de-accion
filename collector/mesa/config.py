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

# Relé por una conexión peruana: SENAMHI, MIDIS y PROVIAS no responden a IPs de nubes (AWS, aunque sea Lima).
# Con MESA_PROXY (p. ej. http://127.0.0.1:8888, un túnel a rele.py en una PC en Perú) solo esos dominios salen
# por ahí; el resto va directo. Vacío = todo directo.
PROXY = os.environ.get("MESA_PROXY", "").strip()
# Relé web: en el servidor (MESA_RELE_HUB=1) las Mesas instaladas en el Perú le hacen esas consultas; en cada Mesa
# instalada, MESA_RELE_SERVIDOR es la dirección de ese servidor (ver rele_hub.py y rele_cliente.py).
RELE_HUB = os.environ.get("MESA_RELE_HUB", "") == "1"
RELE_SERVIDOR = os.environ.get("MESA_RELE_SERVIDOR", "").strip()
PROXY_HOSTS = tuple(h.strip().lower() for h in os.environ.get(
    "MESA_PROXY_HOSTS", "senamhi.gob.pe,midis.gob.pe,proviasnac.gob.pe,ana.gob.pe").split(",") if h.strip())

# Segundos entre ejecuciones por fuente (ver 04-live-service.md para la justificación).
# Frecuencias ajustadas el 2026-11-09 a lo que cada fuente publica de verdad (novedades medidas en 14 días en el
# servidor), sin perder lo urgente: sismos, reportes de INDECI y avisos siguen en minutos.
INTERVALS = {
    "indeci": 10 * 60,           # novedad cada ~12 min en promedio (mediana)
    "igp": 5 * 60,               # 22 sismos en 14 días; se mantiene en minutos porque es lo más urgente
    "senamhi_avisos": 30 * 60,   # ~1 aviso nuevo por día, casi siempre entre las 15 y las 17 h
    "senamhi_hidro": 60 * 60,    # ~1 cambio cada día y medio
    "serfor": 30 * 60,           # 6 de cada 10 lecturas traen focos nuevos
    "ingemmet": 3 * 3600,        # ~1 cambio cada día y medio
    "firms": 2 * 3600,           # el archivo de NASA se renueva por pasadas de satélite
    "senamhi_pronostico": 6 * 3600,
    "senamhi_uv": 6 * 3600,
    "enfen": 12 * 3600,
    "provias": 30 * 60,          # visor de emergencias viales (SGCV): novedad cada ~1 h
    "dhn_avisos": 60 * 60,       # avisos especiales de oleaje y viento: unos pocos por semana
    "dhn_pronosticos": 12 * 3600, # mapas del modelo de olas: corre una vez al día (00Z)
    "olas_openmeteo": 6 * 3600,  # modelo global de olas, referencial
    "ana_obras": 6 * 3600,       # obras de prevención de la ANA: avances unas pocas veces por semana
    "bomberos": 15 * 60,         # lista de las últimas 24 horas del CGBVP (no se pierde nada; bloquea si se consulta seguido)
    "provias_fotos": 30 * 60,    # cronología y fotos, hasta 40 emergencias por turno (era lo que más consultas hacía)
    "com_pnp": 30 * 60, "com_mtc": 30 * 60, "com_mininter": 30 * 60,   # gob.pe, una fuente por institución
    "com_provias": 60 * 60,      # PROVIAS publica ~1 nota cada día
    "indeci_fotos": 10 * 60,     # procesa los PDF que deja indeci
    "respaldo": 24 * 3600,       # copia diaria de data/ (mesa/backup.py)
    "midis": 30 * 86400,         # solo el catálogo de ubigeos; los indicadores se piden por distrito bajo demanda
}
REFRESH_COOLDOWN = 60          # segundos mínimos entre refrescos manuales de una misma fuente
ATRASO_S = 12 * 3600           # una fuente está "atrasada" si su último éxito tiene más de esto (o de 3× su intervalo, si es mayor);
STALE_FACTOR = 3               # un error suelto con datos recientes no la atrasa: sigue "al día" con el error a la vista
MIN_SHARE = 0.03               # fracción mínima del área provincial cubierta por un aviso para contarla
INDECI_PAGES_FIRST_RUN = 6     # páginas del feed a leer en el primer arranque (≈ 84 ítems)
