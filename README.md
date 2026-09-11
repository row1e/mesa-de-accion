# Mesa de Acción — fuentes oficiales de emergencias (Perú)

Colector local de fuentes oficiales peruanas de emergencias y riesgo (SENAMHI, INDECI, IGP, SERFOR, INGEMMET, ENFEN, NASA FIRMS, PNP/MININTER y notas oficiales de gob.pe), con API, dashboard en vivo y fichas en PDF por provincia y departamento.

| Carpeta | Qué hay |
|---|---|
| [`collector/`](collector/README.md) | **El servicio**: programador de fuentes, SQLite, API FastAPI, dashboard en vivo, fichas PDF. Empezar aquí |
| `probes/` | Scripts de la fase de investigación: consultan cada fuente una vez y guardan muestras en `samples/` |
| `dashboard/` | Dashboard estático generado desde `samples/` (versión previa al colector) |
| `ref/` | Límites de departamento y provincia (INEI vía INGEMMET, simplificados a 0,01°) |
| `0X-*.md` | Notas de investigación de cada fuente: endpoints, campos, nivel geográfico, frescura, límites |

## Documentos de investigación

- `01-sheet-audit.md`: auditoría de la hoja de cálculo que alimentaba el prototipo anterior
- `02-catalog-senamhi-indeci.md`: SENAMHI e INDECI
- `03-catalog-otras-fuentes.md`: INGEMMET, IGP, SERFOR, FIRMS, ENFEN, MTC/PROVIAS, AgroClima
- `05-catalog-pnp-mininter.md`: PNP / MININTER (SIDPOL) y notas oficiales de gob.pe

## Probes (opcional)

```bash
python3 -m venv .venv
.venv/bin/pip install shapely pymupdf
cd probes
../.venv/bin/python senamhi.py
../.venv/bin/python indeci.py 4
../.venv/bin/python others.py
cd ..
.venv/bin/python dashboard/build.py
```

Cada probe guarda en `samples/<fuente>/<AAAA-MM-DD>/` (fuera de git); `build.py` toma la fecha más reciente de cada fuente y escribe `dashboard/index.html`.
