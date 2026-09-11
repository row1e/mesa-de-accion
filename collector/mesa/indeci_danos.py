"""Cifras de daños de los reportes INDECI ("3.1 Reporte de daños") leídas por posición de palabras.

La tabla tiene una columna de ubicación (DPTO./PROV./DIST.), bandas de categoría ("VIDA Y SALUD (PERSONA)",
"DAÑOS MATERIALES", …), a veces un grupo ("VIVIENDA") y encabezados hoja ("AFECTADA", "INHABITABLE"). Las columnas se
definen por la posición x de los números; cada palabra de encabezado se asigna a las columnas con las que se superpone.
"""
import re
import unicodedata

NUM = re.compile(r"^\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?$|^\d+$")
STOP = re.compile(r"^(Nota|Fuente|3\.2|Información|Informaci|4\.)", re.I)


def _n(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()


def _num(s):
    s = s.strip()
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", s) or re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
        return int(re.sub(r"[.,]", "", s))
    return float(s.replace(",", ".")) if re.search(r"[.,]", s) else int(s)


# (campo canónico, palabras que deben estar en la etiqueta, palabras que no deben estar)
FIELDS = [
    ("personas_fallecidas", ["FALLECID"], []),
    ("personas_desaparecidas", ["DESAPARECID"], []),
    ("personas_heridas", ["HERID"], []), ("personas_heridas", ["LESIONAD"], []),
    ("personas_damnificadas", ["DAMNIFICAD"], ["VIVIENDA", "INSTITUC", "ESTABLEC"]),
    ("personas_afectadas", ["AFECTAD", "PERSONA"], ["VIVIENDA"]), ("personas_afectadas", ["AFECTAD", "VIDA"], ["VIVIENDA"]),
    ("viviendas_colapsadas", ["VIVIENDA", "COLAPSAD"], []), ("viviendas_colapsadas", ["VIVIENDA", "DESTRUID"], []),
    ("viviendas_inhabitables", ["VIVIENDA", "INHABITABLE"], []),
    ("viviendas_afectadas", ["VIVIENDA", "AFECTAD"], []),
    ("ie_afectadas", ["EDUCATIV", "AFECTAD"], []), ("ie_inhabitables", ["EDUCATIV", "INHABITABLE"], []),
    ("ie_colapsadas", ["EDUCATIV", "COLAPSAD"], []),
    ("salud_afectados", ["SALUD", "AFECTAD"], ["VIDA", "PERSONA"]), ("salud_inhabitables", ["SALUD", "INHABITABLE"], ["VIDA"]),
    ("puentes_afectados", ["PUENTE", "AFECTAD"], []), ("puentes_colapsados", ["PUENTE", "COLAPSAD"], []),
    ("puentes_colapsados", ["PUENTE", "DESTRUID"], []),
    ("vias_afectadas_m", ["AFECTAD", "(M)"], ["CANAL"]), ("vias_destruidas_m", ["DESTRUID", "(M)"], ["CANAL"]),
    ("vias_afectadas_km", ["AFECTAD", "(KM)"], ["CANAL"]), ("vias_destruidas_km", ["DESTRUID", "(KM)"], ["CANAL"]),
    ("canales_afectados_m", ["CANAL", "AFECTAD"], []),
    ("cultivo_afectado_ha", ["CULTIVO", "AFECTAD"], []), ("cultivo_perdido_ha", ["CULTIVO", "PERDID"], []),
    ("cobertura_natural_afectada_ha", ["COBERTURA", "AFECTAD"], []), ("cobertura_natural_perdida_ha", ["COBERTURA", "PERDID"], []),
    ("cobertura_natural_destruida_ha", ["COBERTURA", "DESTRUID"], []),
    ("locales_publicos_afectados", ["LOCAL", "AFECTAD"], ["VIVIENDA"]), ("locales_publicos_inhabitables", ["LOCAL", "INHABITABLE"], ["VIVIENDA"]),
    # bajo "DAÑOS MATERIALES" sin otro objeto, INDECI se refiere a viviendas
    ("viviendas_colapsadas", ["MATERIALES", "COLAPSAD"], ["EDUCATIV", "SALUD", "LOCAL", "PUENTE", "ESTANCIA"]),
    ("viviendas_inhabitables", ["MATERIALES", "INHABITABLE"], ["EDUCATIV", "SALUD", "LOCAL", "PUENTE", "ESTANCIA"]),
    ("ganado_perdido", ["GANAD", "PERDID"], []), ("ganado_afectado", ["GANAD", "AFECTAD"], []),
    ("animales_perdidos", ["PECUARI", "PERDID"], []), ("animales_afectados", ["PECUARI", "AFECTAD"], []),
]
LABELS = {  # para mostrar
    "personas_fallecidas": "Fallecidos", "personas_desaparecidas": "Desaparecidos", "personas_heridas": "Heridos",
    "personas_damnificadas": "Damnificados", "personas_afectadas": "Personas afectadas",
    "viviendas_colapsadas": "Viviendas colapsadas", "viviendas_inhabitables": "Viviendas inhabitables",
    "viviendas_afectadas": "Viviendas afectadas", "ie_afectadas": "Inst. educativas afectadas",
    "ie_inhabitables": "Inst. educativas inhabitables", "ie_colapsadas": "Inst. educativas colapsadas",
    "salud_afectados": "Estab. de salud afectados", "salud_inhabitables": "Estab. de salud inhabitables",
    "puentes_afectados": "Puentes afectados", "puentes_colapsados": "Puentes colapsados",
    "vias_afectadas_m": "Vías afectadas (m)", "vias_destruidas_m": "Vías destruidas (m)", "vias_afectadas_km": "Vías afectadas (km)",
    "vias_destruidas_km": "Vías destruidas (km)", "canales_afectados_m": "Canales afectados (m)",
    "cultivo_afectado_ha": "Cultivos afectados (ha)", "cultivo_perdido_ha": "Cultivos perdidos (ha)",
    "cobertura_natural_afectada_ha": "Cobertura natural afectada (ha)", "cobertura_natural_perdida_ha": "Cobertura natural perdida (ha)",
    "cobertura_natural_destruida_ha": "Cobertura natural destruida (ha)",
    "locales_publicos_afectados": "Locales públicos afectados", "locales_publicos_inhabitables": "Locales públicos inhabitables",
    "ganado_perdido": "Ganado perdido", "ganado_afectado": "Ganado afectado",
    "animales_perdidos": "Animales perdidos", "animales_afectados": "Animales afectados",
}
ORDER = list(LABELS)


def classify(label):
    L = _n(label)
    for field, need, avoid in FIELDS:
        if all(w in L for w in need) and not any(w in L for w in avoid):
            return field
    return None


def _region(page):
    """(y_inicio, y_fin) de la tabla de daños en la página, o None."""
    ws = page.get_text("words")
    start = None
    for i, w in enumerate(ws):
        if w[4].lower() == "reporte" and i + 2 < len(ws) and ws[i + 1][4].lower() == "de" and ws[i + 2][4].lower().startswith("daños"):
            start = w[1]
            break
    if start is None:
        return None, ws
    act = [w[1] for w in ws if w[4] == "Actualizado" and w[1] >= start - 2]
    top = (min(act) + 6) if act else start + 10
    end = min((w[1] for w in ws if w[1] > top + 20 and STOP.match(w[4])), default=page.rect.height - 60)
    return (top, end), ws


def parse_page(page):
    reg, ws = _region(page)
    if not reg:
        return None
    top, end = reg
    area = [w for w in ws if top <= w[1] < end]
    loc = [w for w in area if w[4].upper().startswith("UBICACI")]
    loc_right = (loc[0][2] + 8) if loc else 200
    # etiquetas de ubicación: DPTO./PROV./DIST. + nombre, y TOTAL
    nums = [w for w in area if NUM.match(w[4]) and w[0] > loc_right - 4]
    if not nums:
        return {"filas": [], "columnas": [], "sin_tabla": False}
    data_top = min(w[1] for w in nums)
    # columnas por centro x de los números (agrupando a menos de 18 pt)
    centers = sorted((w[0] + w[2]) / 2 for w in nums)
    cols = []
    for c in centers:
        if cols and c - cols[-1][-1] < 18:
            cols[-1].append(c)
        else:
            cols.append([c])
    cx = [sum(c) / len(c) for c in cols]
    bounds = [(((cx[i - 1] + cx[i]) / 2 if i else loc_right), ((cx[i] + cx[i + 1]) / 2 if i + 1 < len(cx) else page.rect.width))
              for i in range(len(cx))]
    heads = [w for w in area if w[1] < data_top - 2 and w[0] > loc_right - 4 and not NUM.match(w[4])]
    labels = []
    for (l, r), c in zip(bounds, cx):
        mine = [w for w in heads if min(w[2], r) - max(w[0], l) >= 4 or (w[0] <= c <= w[2])]
        lines = {}
        for w in sorted(mine, key=lambda w: (round(w[1] / 3), w[0])):
            lines.setdefault(round(w[1] / 3), []).append(w[4])
        labels.append(" ".join(" ".join(v) for _, v in sorted(lines.items())))
    # filas por y
    rows = {}
    for w in nums:
        rows.setdefault(round(w[1] / 4), []).append(w)
    filas = []
    for key in sorted(rows):
        ws_row = rows[key]
        y = sum(w[1] for w in ws_row) / len(ws_row)
        name = " ".join(w[4] for w in sorted(area, key=lambda w: w[0]) if w[2] <= loc_right and abs(w[1] - y) < 5)
        vals = {}
        for w in ws_row:
            c = (w[0] + w[2]) / 2
            i = min(range(len(cx)), key=lambda k: abs(cx[k] - c))
            vals[labels[i]] = vals.get(labels[i], 0) + _num(w[4])
        filas.append({"ubicacion": name.strip() or None, "valores": vals})
    return {"filas": filas, "columnas": labels}


def extract(doc):
    """Recorre las páginas; devuelve {columnas, filas, totales{campo:n}, sin_clasificar[...]} o None si no hay tabla."""
    for page in doc:
        res = parse_page(page)
        if res is None:
            continue
        filas = res["filas"]
        tot_row = next((f for f in filas if f["ubicacion"] and _n(f["ubicacion"]).startswith("TOTAL")), None)
        use = [tot_row] if tot_row else [f for f in filas if not (f["ubicacion"] or "").upper().startswith(("DPTO", "PROV"))] or filas
        totales, raw, unknown = {}, {}, set()
        for f in use:
            for label, v in f["valores"].items():
                raw[label] = raw.get(label, 0) + v
                field = classify(label)
                if field:
                    totales[field] = totales.get(field, 0) + v
                else:
                    unknown.add(label)
        return {"columnas": res["columnas"], "filas": filas, "totales": {k: totales[k] for k in ORDER if k in totales},
                "sin_clasificar": sorted(unknown), "por_columna": raw}
    return None
