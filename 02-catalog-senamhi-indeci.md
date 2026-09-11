# Catálogo de fuentes — SENAMHI e INDECI

Sondeo real del 2026-09-10. Probes re-ejecutables: `python3 probes/senamhi.py`, `python3 probes/indeci.py [páginas]`.
Muestras en `samples/<fuente>/2026-09-10/`. Ningún endpoint requiere autenticación.

Leyenda nivel geográfico: **P** = provincia real · **D** = departamento · **pt** = coordenadas · **poly** = polígono

---

## SENAMHI

### S1. Lista de avisos meteorológicos ★ reemplaza `SENAHMI_Meteorologico` (muerto desde 07-07)

| | |
|---|---|
| Endpoint | `GET https://www.senamhi.gob.pe/?p=aviso-meteorologico` (HTML, ~1.2 MB, una tabla) |
| Método | Scraping de tabla HTML estable (`<tr><td>` × 7) |
| Campos | título, nro, estado (vigente/emitido/vencido), emisión, inicio, fin, duración, **nivel (AMARILLO/NARANJA/ROJO)** |
| Historia | 2.037 avisos, 2021 → hoy (en una sola página) |
| Frescura | Tiempo real; hoy aviso #360, la hoja se quedó en #269 |
| Geo | Ninguno (ver S2/S3) |
| Hallazgo | Hoy hay un **aviso ROJO vigente** (#356, temperatura diurna costa y sierra) que la hoja no muestra |

### S2. Polígonos de aviso por día ★★ nivel por provincia real

| | |
|---|---|
| Endpoint | `GET https://idesep.senamhi.gob.pe/geoserver/g_aviso/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=g_aviso:view_aviso&viewparams=qry:{nro}_{dia}_{año}&outputFormat=application/json` |
| Método | WFS GeoServer → GeoJSON (EPSG:4326) |
| Campos | nro_aviso, nro_mapa (día 1–3), **nivel (Nivel 1–4)**, fecha_emi, fech_ini, fech_fin, cod_fen, cod_even |
| Historia | Funciona para 2023 y 2026; los IDs de 2021 no responden con este esquema |
| Geo | **poly** → intersección con provincias da nivel de peligro exacto por provincia/distrito |
| Nota | Respuesta pesada (~2.4 MB por aviso-día); cachear. `cod_fen`/`cod_even` sin diccionario aún |

### S3. Departamentos/provincias de posible afectación

| | |
|---|---|
| Endpoint | `GET https://www.senamhi.gob.pe/mapas/mapa-avisos-meteorologicos/index.php?av={nro}&nl=4&mp={dia}&fc={año}` |
| Método | Scraping HTML; **encoding Latin-1** (FERREÑAFE llega como `FERRE�AFE` si se lee en UTF-8) |
| Campos | lista de departamentos, lista de provincias (sin nivel por provincia) |
| Geo | **P** (por nombre, no por código) |
| Uso | Validación cruzada de S2; es lo que tenía la hoja |

### S4. Límites distritales INEI (servidos por SENAMHI)

| | |
|---|---|
| Endpoint | `…/geoserver/g_carto_fundamento/ows?…typeName=g_carto_fundamento:distritos&outputFormat=application/json` |
| Campos | `iddist` (ubigeo INEI 6 dígitos), distrito, capital, fuente=INEI |
| Uso | Geometría base para cruzar S2 y puntos (IGP, FIRMS) con ubigeo. Descargar una vez y versionar |

### S5. Pronóstico por ciudad ★ reemplaza `SENAHMI_Pronostico` (muerto)

| | |
|---|---|
| Endpoint | `GET https://www.senamhi.gob.pe/?p=pronostico-meteorologico` |
| Método | Scraping de texto (patrón `CIUDAD - DPTO día, N de mes Tmax Tmin descripción`) |
| Campos | ciudad, departamento, día (3 días), Tmax, Tmin, descripción |
| Volumen | **277 ciudades** (la hoja solo tenía 24 capitales) → 1.978 filas |
| Geo | Ciudad → provincia (requiere tabla ciudad↔ubigeo, a construir) |
| Riesgo | El parser depende del texto; frágil ante cambios de maquetación |

### S6. Índice UV pronosticado ★★ reemplaza `Radiacion_UV_Capitales` (muerto)

| | |
|---|---|
| Endpoint | `GET https://www.senamhi.gob.pe/usr/dms/modelo/iuv/prono_ruv.json` |
| Método | **JSON directo** (el que usa el mapa oficial) |
| Campos | `c_cod_zona` (**ubigeo provincial 4 dígitos**), nombre (capital), lat/lon, fecha emisión, pronóstico 2 días (índice, hora pico) |
| Geo | **P** real — 215 zonas (Perú tiene 196 provincias; hay zonas extra por verificar) |

### S7. Avisos hidrológicos ★★ fuente nueva (no estaba en la hoja)

| | |
|---|---|
| Endpoint | `POST https://www.senamhi.gob.pe/mapas/mapa-aviso-hidro/include/ajaxIdesepWFSAvisos.php` |
| Método | GeoJSON directo |
| Campos | estación, lat/lon, fecha_hora, **nivel + color**, título, peligro, recomendación, **nom_departamento, nom_provincia, nom_distrito**, cuenca, zonal |
| Hoy | 5 avisos (Loreto): 1 ROJO (Lagunas, río Huallaga), 2 NARANJA, 2 AMARILLO |
| Pendiente | Caudales/niveles por estación (`mapa-monitoreohidro/include/ajaxEstaciones.php`, `ajaxDataEstacion.php`) requieren parámetros; la página de monitoreo dice "en mantenimiento" |

---

## INDECI / COEN

### I1. Feed de emergencias ★★ fuente central que faltaba

| | |
|---|---|
| Endpoint | `GET https://portal.indeci.gob.pe/emergencias/feed/?paged={n}` (RSS WordPress) |
| Método | RSS, 14 ítems/página, paginable hacia atrás (página 500 ≈ marzo 2026) |
| Volumen | ~56 ítems el 10-09 (37 reportes de emergencia) |
| Tipos | Reporte Preliminar, Reporte Complementario (con N° de secuencia), Informe de Emergencia, Boletín de aviso meteorológico (espejo de SENAMHI), Boletín sísmico, Aviso de corto plazo, Monitoreo de peligros |
| Campos (título) | tipo, número, fecha, hora, secuencia, **tipo de evento**, **distrito**, **departamento** |
| Campos (descripción) | fecha/hora de ocurrencia, descripción con **provincia** (36/37) y sector |
| Geo | Distrito + provincia + dpto por nombre → ubigeo por diccionario |
| Nota | La REST API de WordPress no expone este tipo de post (`/wp/v2/posts` termina en 2020) |

### I2. PDF del reporte ★ cifras de daños

| | |
|---|---|
| Endpoint | enlace "DESCARGAR ARCHIVO" en cada página de reporte (`wp-content/uploads/…pdf`) |
| Método | PDF (texto extraíble con PyMuPDF; 5–6 páginas) |
| Secciones | 1. Hechos · 2. **Ubicación (DPTO/PROV/DIST/SECTOR)** · 3. **Evaluación de daños** · 4. Acciones de respuesta · estado (p. ej. "se procede al cierre del reporte") |
| Clave | **Código SINPAD** en el texto (p. ej. N.° 270731) → une reportes del mismo evento |
| Dificultad | La tabla de daños cambia de columnas según el evento (vivienda, vías, ganadería…). La extracción de texto la desordena → requiere `find_tables()` o extracción con LLM |

### I3. SINPAD — no accesible

`sinpad.indeci.gob.pe` devuelve 404 en todas las rutas probadas. `geoportal.indeci.gob.pe` y `www.indeci.gob.pe` no responden. Por ahora la vía es I1 + I2.

### I4. COEN (coen.indeci.gob.pe)

Aplicaciones con login (Sistema COEN, boletines sísmicos, directorio). No hay datos públicos útiles.

---

## Mapa contra la hoja

| Dato del dashboard | Hoy (hoja) | Fuente directa propuesta | Mejora |
|---|---|---|---|
| Avisos meteorológicos | Muerto desde 07-07, sin nivel | S1 + S2 | Tiempo real, con nivel, **por provincia real** |
| Temperatura / tiempo | Muerto, solo capitales | S5 | 277 ciudades |
| UV | Muerto, solo capitales | S6 | JSON, por provincia |
| Hidrología | No existía | S7 | Nivel por estación con distrito |
| Emergencias activas | No existía | I1 (+ I2) | Por distrito, casi en tiempo real |
| Afectados / damnificados / daños | No existía | I2 | Requiere extracción de tablas PDF |

## Pendientes

- Diccionario `cod_fen` / `cod_even` de SENAMHI.
- Tabla ciudad → ubigeo para S5; verificar las 215 zonas UV contra las 196 provincias.
- Parser de títulos INDECI: cubrir "INFORME DE EMERGENCIA" y "SISMO … EN {distrito} – {provincia} – {dpto}".
- Estrategia de extracción de tablas de daños (I2).
- Términos de uso / cortesía de scraping (frecuencia, User-Agent identificable).
