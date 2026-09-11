# Catálogo de fuentes — INGEMMET, IGP, SERFOR, FIRMS, ENFEN, MTC/PROVIAS, AgroClima

Sondeo real del 2026-09-10. Probe: `python3 probes/others.py [ingemmet|igp|serfor|firms|enfen]`.
Ningún endpoint requiere autenticación.

## INGEMMET — GEOCATMIN (ArcGIS REST) ★★ reemplaza `Peligros_Geologicos_INGEMMET` (muerto desde 27-04)

| | |
|---|---|
| Servidor | `https://geocatmin.ingemmet.gob.pe/arcgis/rest/services` (~80 servicios públicos) |
| Capa clave | `SERV_PERU_ALERTA/MapServer/0` "Zonas Críticas en Alerta": zonas críticas geológicas que caen dentro de un aviso SENAMHI |
| Consulta | `…/0/query?where=1=1&outFields=*&outSR=4326&f=geojson` (sin paginación; 343 hoy) |
| Campos | región, provincia, distrito, paraje, peligro, elementos expuestos, NIVEL, NRO_AVISO |
| Otras capas | `/1` Zonas críticas (3.351) · `/3` Poblados afectados (0 hoy) · `/4` Zonas por activación de quebradas (1.585, con `CD_DIST` ubigeo) · `SERV_PELIGROS_GEOLOGICOS/0` inventario (28.915) · `SERV_VOLCANES` |
| Límites | `SERV_CARTOGRAFIA_DEMARCACION_WGS84/MapServer/{0,1,2}` dpto/prov/distrito con ubigeo; admite `maxAllowableOffset` (simplificación en servidor). Usado para el mapa (`ref/`) |
| Nota | Hoy las 343 zonas vienen de un solo aviso (#353): el servicio parece publicar un aviso a la vez |

## IGP — Sismos ★★

| | |
|---|---|
| Catálogo anual | `GET https://ultimosismo.igp.gob.pe/api/ultimo-sismo/ajaxb/{año}` → JSON (652 sismos en 2026) |
| Último sismo | `GET https://ultimosismo.igp.gob.pe/api/ultimo-sismo` |
| Campos | código, fecha/hora local y UTC, lat, lon, magnitud, profundidad, referencia, intensidad, PDF acelerométrico |
| Geo | Coordenadas → cruce con distrito |
| vs. hoja | La hoja lo tiene vivo pero como texto libre por departamento |

## SERFOR — Incendios (ArcGIS REST) ★★

| | |
|---|---|
| Servidor | `https://geo.serfor.gob.pe/geoservicios/rest/services/UFMS/Incendios_SAMI/MapServer` (maxRecordCount 5000, admite paginación) |
| `/0` Focos de calor | 2.845 en 24 h · NOMDEP/NOMPRO/NOMDIS + **CATDIS (ubigeo)**, fecha, hora, sensor, cobertura |
| `/2` Alertas de incendio | 190 · **ESTADO** (1 Alertado · 2 Confirmado · 3 Controlado · 4 Extinguido), código PIF, ubigeo, cobertura |
| `/4` Ocurrencias | 97.376 históricas (año, mes, superficie, código SINPAD) |
| vs. hoja | La hoja agrega a texto por departamento ("N focos en la provincia de X") |

## NASA FIRMS ★ respaldo

`https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_South_America_24h.csv`: CSV abierto, sin API key. Recortar a Perú por polígono (el bbox incluye bordes vecinos). Útil si SERFOR cae.

## ENFEN ★★ fuente nueva

| | |
|---|---|
| Sitio | `https://enfen.imarpe.gob.pe/` (WordPress; `enfen.gob.pe` no resuelve en DNS) |
| Estado | En el título de cada "Comunicado Oficial": "Estado del sistema de alerta: …" |
| Vías | `/feed/` y `/wp-json/wp/v2/posts`, pero **se detienen en N° 13 (17-07)**. La sección de descargas (`/download/comunicado-oficial-enfen-n-{n}-{año}/?wpdmdl=…`, enlazada en la portada) ya tiene N° 14 y 15 → leer el PDF más reciente |
| Hoy | N° 15-2026 (28-08): **Alerta de El Niño Costero**; magnitud extraordinaria sep 2026–ene 2027 (≥ 62 %) |
| Geo | Nacional (regiones Niño 1+2 / 3.4) |

## MTC / PROVIAS — sin acceso desde esta red ✖

- `www.pvn.gob.pe`, `sinac.proviasnac.gob.pe`, `ide.mtc.gob.pe`, `geo.mtc.gob.pe`, `emergenciasviales.mtc.gob.pe`: DNS resuelve (p. ej. 200.62.243.167), pero la conexión TCP expira. Probable bloqueo por IP o país.
- `portal.mtc.gob.pe` responde, pero solo con mapas estáticos.
- La hoja sí recibe `MTC_Transportes` (vivo; 138 eventos hoy), así que el scraper de terceros llega desde otra red.
- **Pendiente:** probar desde una IP peruana (VPS en Lima o la red del cliente) y encontrar el endpoint del que sale `MTC_Transportes`.

## AgroClima — parcial

- No se encontró un feed estructurado. `agroclima.midagri.gob.pe` no responde; en SENAMHI, `?p=agrometeorologia`, `?p=sequia` y `?p=monitoreo-agrometeorologico` son páginas vacías o en mantenimiento.
- Heladas y friaje sí llegan como avisos SENAMHI (p. ej. #358 "Décimo friaje") → filtrar S1/S2 por título.
- "Distritos priorizados" es un PDF estático de 2019–2021.
- **Pendiente:** acordar con el equipo editorial qué se entiende por "AgroClima" (¿boletín agrometeorológico decadal? ¿SIEA MIDAGRI?).
