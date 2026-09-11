# Auditoría de la hoja "Scrapping" — Mesa de Acción

- Hoja de Google Sheets de un tercero, compartida en modo lectura (identificador y dueño omitidos)
- Snapshot: export xlsx del 2026-09-10 (última modificación 14:18 UTC ≈ 09:18 Lima)
- Leído: 17 pestañas completas, todas las filas.

## Cómo se comporta

- El scraper corre **una vez al día, ~09:10 Lima** (DICAPI marca "a la hora 09:10").
- **Ventana móvil de ~31 días**: las filas antiguas se borran. No hay historial más allá de un mes.
- Una fila por departamento por día (24 departamentos; **falta Callao**).

## Inventario de pestañas

| Pestaña | Fuente | Última fecha | Estado | Nivel geográfico real | Formato |
|---|---|---|---|---|---|
| Datos_Combinados | (agregado) | 2026-09-10 | Vivo, pero engañoso | Departamento | Texto |
| IGP_Sismos | IGP | 2026-09-10 | ✅ Vivo | Coordenadas lat/lon + referencia | Texto libre multi-reporte |
| SERFOR_Focos_de_Calor | SERFOR | 2026-09-10 | ✅ Vivo | Provincia (dentro del texto) | "N focos … provincia de X" |
| SERFOR_incendios | SERFOR | 2026-09-10 | ✅ Vivo (disperso) | Provincia (dentro del texto) | "N incendios alertados … provincia de X" |
| MTC_Transportes | MTC | 2026-09-10 | ✅ Vivo (~120–138 eventos/día) | Dpto / Provincia / Distrito | Semi-estructurado |
| DICAPI_Puertos | DICAPI | 2026-09-10 | ✅ Vivo | Capitanía / puerto | Texto |
| DIHIDRONAV_avisos | DHN | 2026-09-10 | ✅ Vivo | Tramo de litoral | Texto |
| AMBIAND_Aeropuertos | "Ambiand" (sin identificar) | 2026-09-10 | ✅ Vivo | Aeropuerto | Multi-valor por `\n\n` |
| Vedas_Regional | PRODUCE | 2026-09-10 | ✅ Vivo (estático, 13 filas/día) | Región | Tabla; mojibake |
| SENAHMI_Meteorologico | SENAMHI avisos | **2026-07-07** | ❌ Muerto hace 65 días | Provincias (lista) | Texto; **sin nivel/color de aviso** |
| SENAHMI_Pronostico | SENAMHI | **2026-07-07** | ❌ Muerto | Capital de dpto | "21ºC" texto |
| Radiacion_UV_Capitales | SENAMHI | **2026-07-07** | ❌ Muerto | Capital de dpto | Tabla |
| PROVIAS_emergencia_vial | PROVIAS | **2026-05-19** | ❌ Muerto | UBIGEO dentro del texto | Texto largo |
| Peligros_Geologicos_INGEMMET | INGEMMET | **2026-04-27** | ❌ Muerto | Departamento | Tabla numérica |
| DIHIDRONAV_avisos_especiales | DHN | **2026-04-27** | ❌ Muerto | Puerto | Texto |
| MTC_Transito | MTC | **2026-02-21** | ❌ Muerto | Departamento | "13 restringidas" |
| PRODUCE_vedas (oculta) | PRODUCE | **2025-10-02** | ❌ Muerto | Nacional | Tabla |

## Problemas críticos

1. **Datos_Combinados reporta ceros falsos.** En el 100% de las filas:
   - Temperatura, tiempo y UV = "Sin datos" (SENAMHI muerto).
   - MTC = "Sin tránsito reportado", mientras `MTC_Transportes` tiene **138 vías restringidas hoy**.
   - PROVIAS = `0`, cuando la fuente murió en mayo. Viola la regla del handoff "null ≠ 0".
   - Falta el día 2026-08-18.
2. **De las 9 fuentes del handoff, solo 3 están vivas** (SERFOR, DHN, MTC).
   - Sin pestaña alguna: INDECI, SENAMHI Hidrología, ENFEN, AgroClima.
   - Muertas: SENAMHI Meteorología, INGEMMET, PROVIAS.
   - En la hoja pero no en el handoff: IGP, DICAPI, Ambiand, PRODUCE, UV.
3. **Los avisos SENAMHI no traen el nivel** (amarillo/naranja/rojo): 0 menciones en 1.462 filas.

## Calidad de datos

- Fechas mezcladas: `2026-09-10`, `10/09/2026`, `2026-04-27 09:45:37`.
- Nombres de departamento inconsistentes: `JUNIN` vs `Junín` vs `Junín ` (espacio final) vs `Piura  `. Un join exacto pierde eventos (hoy: 3 de Junín, 1 de Piura, 2 de San Martín).
- Mojibake: `JunÃ­n` en Vedas_Regional.
- Códigos como float: `23897.0`.
- Celdas multi-valor emparejadas por posición (aeropuerto ↔ humedad ↔ visibilidad).
- Números como texto: `21ºC`, `13 restringidas`.
- MTC incluye restricciones activas desde 2020 (no son "nuevas").

## Lo bueno

Hay **detalle provincial real** en las pestañas vivas, pero enterrado en texto:
SERFOR (provincia), MTC (dpto/prov/distrito), IGP (lat/lon), SENAMHI avisos (lista de provincias), PROVIAS (UBIGEO).
Datos_Combinados lo pierde al aplanar por departamento.

## Implicaciones para la Fase 1

- Prioridad de sondeo: SENAMHI (avisos con nivel, pronóstico, UV) e INDECI → luego PROVIAS, INGEMMET → luego ENFEN, SENAMHI Hidrología, AgroClima.
- Para las fuentes vivas, evaluar la fuente directa: probablemente es más estructurada que el texto de la hoja (catálogo IGP, puntos de SERFOR/FIRMS, tabla MTC).
- El historial hay que guardarlo nosotros: la hoja solo retiene ~31 días.
- Dependencia operativa: el scraper es de un tercero, sin código accesible.
