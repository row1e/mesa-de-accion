# Catálogo — PNP / MININTER

Sondeo real del 2026-09-10. Muestras en `samples/pnp/`.

## `pnp.gob.pe` — no responde

El servidor no acepta conexión desde esta red (igual que PROVIAS). La PNP publica por gob.pe y, sus datos, por MININTER.

## Denuncias policiales (SIDPOL) ★★

| | |
|---|---|
| Página | `https://www.datosabiertos.gob.pe/dataset/denuncias-policiales-1` (MININTER; licencia ODC-By) |
| Archivo | `https://www.datosabiertos.gob.pe/sites/default/files/DATASET_Denuncias_Policiales_Ene%202018%20a%20Julio%202026.csv` (27 MB, UTF-8 con BOM) |
| Diccionario | `…/DICCIONARIO_DATOS_Denuncias_Policiales_Abr%202026.xlsx` |
| Origen | SIDPOL, suministrado por la DIRTIC de la PNP |
| Columnas | `ANIO, MES, DPTO_HECHO_NEW, PROV_HECHO, DIST_HECHO, UBIGEO_HECHO, P_MODALIDADES, cantidad` |
| Volumen | 369.100 filas · ene 2018 → jul 2026 · 1.839 ubigeos |
| Geo | **Distrito** (ubigeo). Ojo: el ubigeo pierde el cero inicial (`10202` = `010202`) y los departamentos vienen como `LIMA METROPOLITANA`, `REGION LIMA`, `PROV. CONST. DEL CALLAO` |
| Modalidades | Estafa, Extorsión, Hurto, Otros, Robo, Secuestro, Violencia contra la mujer e integrantes |
| Frescura | Mensual; julio se publicó el 13-08-2026 (`Last-Modified`). El nombre del archivo cambia cada mes → hay que leer el enlace desde la página del dataset |
| Dato | Jul 2026: 61.436 denuncias, 1.456 por extorsión (610 en Lima). Extorsión ene–jul: 7.469 (2022) → 16.202 (2025) → 12.174 (2026) |

## Comisarías básicas ★ (desactualizado)

`https://observatorio.mininter.gob.pe/sites/default/files/proyecto/archivos/Relaci%C3%B3n%20de%20comisar%C3%ADas%20b%C3%A1sicas%201318.xlsx`: 1.318 comisarías con **coordenadas GPS**, ubigeo, región policial, DIVPOL y tipo. La hoja se llama "BASICAS NOV19" (noviembre 2019) y no trae teléfonos.

## Personas desaparecidas

`…/DATASET_Personas%20Desaparecidas%20-%20Enero%202019%20a%20Diciembre%202025.csv`: 2019–2025, actualización anual. No sirve para una emergencia en curso.

## Otros datasets MININTER en datosabiertos

Violencia contra la mujer, Producción policial, Trata de personas, Unidades especializadas, Victimización (ENAPRES), Barrio Seguro 2016.

## Observatorio Nacional de Seguridad Ciudadana

`observatorio.mininter.gob.pe`: reportes mensuales SIDPOL en PDF (p. ej. `Reporte_SIDPOL_Enero2026.pdf`). Son los mismos datos del CSV, ya analizados.

## Comunicados PNP y noticias gob.pe (JSON) ★★ patrón reutilizable

`https://www.gob.pe/institucion/{slug}/noticias.json` devuelve las últimas noticias (título, descripción, URL, imagen, fecha) de **cualquier institución en gob.pe**:

- `pnp`: comunicados oficiales (p. ej. N.º 24-2026, 8 set)
- `pvn` (PROVIAS): obras y emergencias viales, aunque `pvn.gob.pe` no responde
- `mtc`, `mininter`, `indeci`, …

Pendiente: `?page=2` devuelve la misma primera página → falta encontrar el parámetro de paginación.
