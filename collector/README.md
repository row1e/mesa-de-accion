# Colector Mesa de Acción (local)

Servicio único: un programador consulta cada fuente oficial a su ritmo, guarda todo en SQLite y sirve una API + el dashboard en vivo en `http://127.0.0.1:8787`.

## Arranque

Primera vez (crea el entorno Python 3.12):

```bash
cd collector
~/.local/bin/uv venv --python 3.12 .venv
~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt
```

Levantar el servicio (queda corriendo en esa terminal; Ctrl+C para detenerlo):

```bash
cd collector && .venv/bin/python run.py
```

Abrir el dashboard en el navegador: `http://127.0.0.1:8787`. Documentación interactiva de la API: `http://127.0.0.1:8787/docs`.

Ejecutar fuentes una vez, sin servidor (para depurar un lector):

```bash
cd collector && .venv/bin/python run.py run senamhi_avisos indeci
```

## Qué hace cada pieza

| Archivo | Rol |
|---|---|
| `mesa/config.py` | Intervalos por fuente, puerto, User-Agent, umbrales. Variables `MESA_PORT`, `MESA_HOST`, `MESA_DATA` |
| `mesa/sources/senamhi.py`, `others.py` | Un lector por fuente → registros normalizados |
| `mesa/runner.py` | Programador (APScheduler), ejecuciones manuales, estado por fuente |
| `mesa/http.py` | Peticiones con reintentos; guarda el crudo en `data/raw/<fuente>/<fecha>/` solo si cambió |
| `mesa/geo.py` | Límites INEI y cruce aviso × provincia |
| `mesa/snapshot.py` | Arma lo que consume el dashboard; calcula el estado de cada fuente |
| `mesa/api.py` | API HTTP y dashboard |
| `web/index.html`, `web/app.js` | Dashboard en vivo |
| `data/mesa.sqlite3` | Base (tablas `items`, `runs`, `fetches`, `source_health`) |

## Intervalos

| Fuente | Cada | Nota |
|---|---|---|
| `igp` | 3 min | Solo pide el catálogo anual cuando cambia el último sismo |
| `indeci` | 5 min | Lee el feed hasta encontrar un ítem ya visto |
| `senamhi_avisos` | 15 min | Polígonos y cruce solo para aviso-días nuevos |
| `com_pnp`, `com_provias`, `com_mtc`, `com_mininter` | 15 min | Una fuente por institución (gob.pe `/institucion/{pnp|pvn|mtc|mininter}/noticias.json`); cada una trae solo sus 9 últimas, el historial queda en la base. `provias` es aparte: solo verifica si el servidor de emergencias viales responde |
| `indeci_fotos` | 5 min | Procesa hasta 15 PDF de reportes INDECI (últimas 72 h): fotos del anexo "VISTAS FOTOGRÁFICAS" con leyenda y fecha, mapa de ubicación (base Google Earth/Airbus: no apto para emisión), texto, código SINPAD y **cifras de daños** (`mesa/indeci_danos.py`: la tabla "3.1 Reporte de daños" leída por posición de palabras → campos estándar). Imágenes en `data/media/indeci/`, servidas en `/media/…` (~250 KB por reporte) |
| `sidpol` | 24 h | Relee la página del dataset; baja el CSV (27 MB) solo si cambió el enlace (cambia cada mes) |
| `senamhi_hidro`, `serfor` | 30 min | |
| `ingemmet`, `firms`, `provias` | 1 h | `provias` solo verifica si el servidor acepta conexión |
| `senamhi_pronostico` | 4 h | |
| `senamhi_uv` | 6 h | |
| `enfen` | 12 h | Baja el PDF solo si hay comunicado nuevo |

## Estados de una fuente

- **Al día**: el último intento salió bien, dentro de 3× su intervalo.
- **Actualizando**: hay una ejecución en curso.
- **Atrasada**: el último éxito supera 3× el intervalo.
- **Fallando**: el último intento dio error. El mensaje explica cuál.
- **Sin acceso**: nunca se pudo leer. Hoy aplica a PROVIAS.

## API

| Método | Ruta | Devuelve |
|---|---|---|
| GET | `/api/health` | Estado de cada fuente |
| GET | `/api/snapshot` | Todo lo que usa el dashboard |
| POST | `/api/refresh/{fuente}` o `/api/refresh/all` | Fuerza una lectura (máx. 1/min por fuente; `202`) |
| GET | `/api/runs?source=&limit=` | Historial de ejecuciones |
| GET | `/api/items/{fuente}/{tipo}?current=true` | Registros normalizados (para integrar con otras aplicaciones) |
| GET | `/api/provincia/{ubigeo}` | Lo que dicen todas las fuentes sobre una provincia |
| GET | `/api/latest?source=&region=&limit=&before=&days=` | Últimos registros recibidos, de todas las fuentes o de una, con `received` (llegada al colector) y `event_time` (fecha de la fuente). Focos y FIRMS se agrupan por lectura si no se filtra por esa fuente; UV, pronóstico y SIDPOL aparecen como "actualización" cuando su contenido cambia. `counts24h` excluye la carga inicial (primera hora de cada fuente) |
| GET | `/ficha/{código}` | Ficha imprimible (A4). Código de 2 dígitos = departamento (tabla de provincias, mapa por provincia, pronóstico de todas sus ciudades); 4 dígitos = provincia. Incluye todas las fuentes, fotos INDECI y hora de último éxito de cada fuente |
| GET | `/ficha/{código}.pdf` | La misma ficha en PDF (Chrome headless del sistema; ~4 s). Ruta de Chrome configurable con `MESA_CHROME` |
| GET | `/api/ficha/{código}` | Datos de la ficha en JSON |
| GET | `/api/sat?lat=&lon=&date=&km=&layer=auto\|viirs\|modis` | Vista satelital NASA GIBS (sin clave) centrada en un punto, con focos de calor del día. `auto` prueba VIIRS NOAA-20 → MODIS Terra → hasta 3 días antes, saltando imágenes vacías (GIBS devuelve negro si aún no hay pasada). Caché en `data/media/sat/` |
| GET | `/api/detail?source=&kind=&key=[&fresh=true]` | Ficha completa de un registro + enlaces a la fuente. El enriquecimiento se consulta a la fuente al primer clic y se cachea (tabla `details`; IGP 7 días, SERFOR alertas 10 min, resto 1–6 h) |

Tipos por fuente: `senamhi_avisos/aviso`, `senamhi_avisos/aviso_dia`, `senamhi_uv/uv_zona`, `senamhi_pronostico/ciudad`, `senamhi_hidro/aviso_estacion`, `indeci/item`, `igp/sismo`, `serfor/foco`, `serfor/alerta`, `ingemmet/zona_alerta`, `enfen/comunicado`, `firms/deteccion`, `com_pnp/noticia` (y `com_provias`, `com_mtc`, `com_mininter`), `sidpol/provincia` (serie mensual por modalidad), `sidpol/meta`.

## Cifras de daños INDECI

`snapshot.danos_resumen()` suma los daños de los últimos 7 días **sin duplicar eventos**: un evento (código SINPAD o evento+distrito+departamento) cuenta una sola vez con su reporte más reciente, porque cada reporte trae cifras acumuladas. Separa eventos **nuevos** (fecha de ocurrencia dentro de la ventana, tomada de "HECHOS") de **anteriores aún en seguimiento**. Si la tabla trae subtotales `DPTO.`/`PROV.` (eventos que abarcan varias regiones, p. ej. un sismo), cada región y provincia recibe su propio subtotal.

## Filtro por región

La barra negra bajo el encabezado filtra todo el dashboard por una de las 25 regiones (24 departamentos + Callao); el enlace queda como `http://127.0.0.1:8787/#r=20` (Piura) para compartirlo. `snapshot.py` asigna la región a cada registro: por ubigeo (SERFOR, INDECI, SIDPOL, avisos por provincia), por polígono (sismos; mar adentro, por la referencia del IGP), por nombre de departamento (INGEMMET, hidrología, pronóstico) y, en los comunicados, por mención del nombre en el texto. Nacionales siempre: estado de fuentes, ENFEN e histórico de avisos.

## Límites actuales

- Sin autenticación: escucha solo en `127.0.0.1`. Antes de exponerlo en otra máquina, agregar acceso.
- Sin alertas por correo cuando una fuente falla (previsto; `source_health.consecutive_failures` ya lo registra).
- PROVIAS/MTC: el servidor no acepta conexión desde esta red.
- Corre mientras la terminal esté abierta. Para que arranque con la sesión de macOS, falta un LaunchAgent (no instalado).
