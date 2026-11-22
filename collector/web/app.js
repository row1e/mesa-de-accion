/* Mesa de Acción · dashboard en vivo. Lee /api/snapshot, sondea /api/health y re-renderiza conservando el estado. */
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const DOW = ["dom","lun","mar","mié","jue","vie","sáb"], MON = ["ene","feb","mar","abr","may","jun","jul","ago","set","oct","nov","dic"];
const fmtDay = iso => { const d = new Date(iso + "T12:00:00"); return `${DOW[d.getDay()]} ${d.getDate()} ${MON[d.getMonth()]}`; };
const lvlNum = s => ({AMARILLO:2, NARANJA:3, ROJO:4})[s] || +String(s).replace(/\D/g,"") || 1;
const lvlChip = n => `<span class="lvl l${n}">${["","N1","N2","N3","N4"][n]}</span>`;
const nf = new Intl.NumberFormat("es-PE");
const title = s => String(s||"").toLowerCase().replace(/(^|[\s(\-/])([a-záéíóúñ])/g,(m,a,b)=>a+b.toUpperCase());
const ago = ts => { if (!ts) return "—"; const s = Math.max(0, Date.now() / 1000 - ts); if (s < 60) return "hace segundos"; if (s < 3600) return `hace ${Math.round(s / 60)} min`; if (s < 86400) return `hace ${Math.round(s / 3600)} h`; return `hace ${Math.round(s / 86400)} d`; };
const until = ts => { if (!ts) return "—"; const s = ts - Date.now() / 1000; if (s <= 60) return "en instantes"; if (s < 3600) return `en ${Math.round(s / 60)} min`; return `en ${Math.round(s / 3600)} h`; };
// Hora exacta en Lima: "jue 11 set 12:04" (segundos y zona en el title)
const LIMA = {timeZone: "America/Lima"};
// Fechas del tooltip del mapa: "cuándo ocurrió" y "cuándo se reportó", siempre en hora de Lima
const limaParts = d => Object.fromEntries(new Intl.DateTimeFormat("en-CA", {...LIMA, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23"}).formatToParts(d).map(p => [p.type, p.value]));
const fmtWhen = (iso, hhmm) => iso ? `${fmtDay(iso)}${hhmm ? " " + hhmm : ""}` : "—";
const fmtTs = ts => { if (!ts) return "—"; const p = limaParts(new Date(ts * 1000)); return fmtWhen(`${p.year}-${p.month}-${p.day}`, `${p.hour}:${p.minute}`); };
const fmtUtc = s => s ? fmtTs(Date.parse(s) / 1000) : "—";
// Segunda fecha abreviada si cae el mismo día que la primera: "lun 28 set 13:33 · recibido 13:41"
const sameDay = (a, b) => a !== "—" && b !== "—" && a.split(" ").slice(0, 3).join(" ") === b.split(" ").slice(0, 3).join(" ") ? b.split(" ")[3] || b : b;
const tipWhen = (a, al, b, bl) => `<span class="tw">${al} <b>${a}</b>${b ? ` · ${bl} <b>${sameDay(a, b)}</b>` : ""}</span>`;
const indeciRep = i => { const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(i.fecha || ""); return m ? fmtWhen(`${m[3]}-${m[2].padStart(2, "0")}-${m[1].padStart(2, "0")}`, i.hora) : fmtTs(i.ts); };
const indeciWhen = i => tipWhen(i.ocurrencia ? fmtWhen(i.ocurrencia, i.ocurrencia_hora) : "sin dato", "ocurrió", indeciRep(i), "reportado");
const stamp = ts => {
  if (!ts) return "—";
  const d = new Date(ts * 1000), p = Object.fromEntries(new Intl.DateTimeFormat("es-PE", {...LIMA, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23"}).formatToParts(d).map(x => [x.type, x.value]));
  return `${p.weekday.replace(".", "")} ${p.day} ${p.month.replace(".", "").replace("sept", "set")} ${p.hour}:${p.minute}`;
};
const stampFull = ts => ts ? new Date(ts * 1000).toLocaleString("es-PE", {...LIMA, dateStyle: "full", timeStyle: "medium"}) + " (hora de Lima)" : "";
const when = ts => ts ? `<time datetime="${new Date(ts * 1000).toISOString()}" title="${stampFull(ts)}">${stamp(ts)}</time> <span class="muted">· ${ago(ts)}</span>` : "—";
const h_m = ts => ts ? stamp(ts).split(" ").pop() : "—";
const every = s => s < 3600 ? `cada ${s / 60} min` : `cada ${s / 3600} h`;
const STATUS = { ok: "Al día", actualizando: "Actualizando", atrasada: "Atrasada", fallando: "Fallando", sin_acceso: "Sin acceso", pendiente: "Pendiente" };

let D = null, V = null, H = [], lastSig = "";   // D = snapshot completo · V = vista filtrada por región
const REP = {lista: [], filtro: "sin_verificar", firma: "", mostrar: 20};   // reportes de corresponsales (cargarReportes)
const POR_PAGINA = 20;   // borradores de IA y reportes de corresponsales: los más recientes primero, y "Ver más"
const verMas = (mostrados, total) => total > mostrados
  ? `<li class="mas"><button type="button" class="more" data-mas>Ver ${Math.min(POR_PAGINA, total - mostrados)} más · ${total} en total</button></li>` : "";

/* ── filtro por región ──────────────────────────────────────────── */
const REG_SECTIONS = ["mapa", "senamhi", "indeci-sec", "vias-sec", "mar-sec", "obras-sec", "dee-sec", "recursos-sec", "sinpad-sec", "bom-sec", "com-sec", "igp-sec", "serfor-sec", "ing-sec"];
const plural = (n, one, many) => `${nf.format(n)} ${n === 1 ? one : many}`;
const regName = code => title(D?.regions?.find(r => r.id === code)?.n || "");
function makeView() {
  const R = state.region;
  if (!R) return D;
  const inR = p => typeof p === "string" && p.startsWith(R);
  const pick = obj => Object.fromEntries(Object.entries(obj).filter(([k]) => inR(k)));
  return {...D,
    levelsByDay: Object.fromEntries(Object.entries(D.levelsByDay).map(([d, cells]) => [d, pick(cells)])),
    avisos: D.avisos.filter(a => a.regs?.includes(R)), uv: pick(D.uv),
    pronostico: D.pronostico.filter(p => p.reg === R), hidro: D.hidro.filter(h => h.reg === R),
    indeci: D.indeci.filter(i => i.reg === R), sismos: D.sismos.filter(s => s[9] === R),
    focos: D.focos.filter(f => f[5] === R), alertas: D.alertas.filter(a => a.reg === R), zonas: D.zonas.filter(z => z.reg === R), vias: (D.vias || []).filter(v => v.reg === R),
    bomberos: (D.bomberos || []).filter(b => b.reg === R),
    obras: (D.obras || []).filter(o => o.reg === R),
    recursos: D.recursos ? {...D.recursos, puntos: D.recursos.puntos.filter(r => r.reg === R), maquinaria: D.recursos.maquinaria.filter(r => r.reg === R)} : null,
    mar: {...(D.mar || {}), avisos: (D.mar?.avisos || []).filter(a => (a.regs || []).includes(R)), puntos: (D.mar?.puntos || []).filter(p => p.reg === R)},
    bomberosMedicas: {...(D.bomberosMedicas || {}), total: D.bomberosMedicas?.por_reg?.[R] || 0, atendiendo: null},
    comunicados: (D.comunicados || []).filter(c => c.regs?.includes(R)),
    fotosDia: (D.fotosDia || []).filter(f => f.reg === R),
    danos: D.danos ? {...D.danos, ...(D.danos.por_reg[R] || {totales: {}, nuevos: {}, seguimiento: {}, n_eventos: 0, n_nuevos: 0}),
      eventos: D.danos.eventos.filter(e => e.por_reg && e.por_reg[R]).map(e => ({...e, totales: e.por_reg[R]}))} : null,
    indeciUnlocated: D.indeci.filter(i => i.reg === R && i.clase === "reporte" && !i.prov).length};
}
function setRegion(code, {zoom: doZoom = true, prov = null} = {}) {
  state.region = code || null;
  state.sel = prov;   // elegir un departamento (o todo el Perú) quita la provincia marcada
  writeHash(); loadLatest();
  $("#region").value = state.region || "";
  renderAll();
  if (doZoom && mapReady) {
    const f = state.region && V.geo.deps.features.find(f => f.properties.id === state.region);
    f ? zoomToFeature(f) : zoomTo(d3.zoomIdentity);
  }
}
const provName = id => title(D?.geo?.provs?.features?.find(f => f.properties.id === id)?.properties.n || "");
function renderRegionBar() {
  const sel = $("#region");
  // Un solo selector: departamento (filtra el tablero) o provincia (filtra por su departamento y la marca en el mapa).
  // Lo elegido es también lo que imprime la ficha PDF.
  if (sel.options.length <= 1) {
    const byDep = d3.group(D.geo.provs.features.map(f => f.properties), p => p.id.slice(0, 2));
    sel.insertAdjacentHTML("beforeend", D.regions.map(r => `<optgroup label="${esc(title(r.n))}">`
      + `<option value="${r.id}">${esc(title(r.n))} · todo el departamento</option>`
      + (byDep.get(r.id) || []).sort((a, b) => a.n.localeCompare(b.n)).map(p => `<option value="${p.id}">${esc(title(p.n))}</option>`).join("")
      + `</optgroup>`).join(""));
  }
  const R = state.region, P = state.sel && R && state.sel.startsWith(R) ? state.sel : null;
  sel.value = P || R || "";
  document.body.classList.toggle("filtered", !!R);
  $("#reg-clear").hidden = !R;
  const lv = Object.values(V.levelsByDay[D.snapshot] || {});
  $("#reg-summary").innerHTML = R
    ? `${P ? `Provincia <b>${esc(provName(P))}</b> marcada en el mapa · ` : ""}Mostrando solo <b>${esc(regName(R))}</b> · ${plural(lv.length, "provincia bajo aviso", "provincias bajo aviso")} hoy · ${plural(V.indeci.filter(i => i.clase === "reporte").length, "reporte INDECI", "reportes INDECI")} · ${plural(V.alertas.length, "alerta de incendio", "alertas de incendio")}. El estado de las fuentes y ENFEN son nacionales.`
    : `Mostrando todo el Perú. Elija un departamento o una provincia para filtrar el tablero y descargar su ficha PDF.`;
  updateFichaLinks();
  const lugarTxt = P ? provName(P) : R ? regName(R) : "";
  $("#ia-briefing").textContent = lugarTxt ? `Generar briefing de turno · ${lugarTxt}` : "Generar briefing de turno · todo el Perú";
  $("#ia-briefing").title = lugarTxt ? `Solo con los datos de ${lugarTxt} (ENFEN es nacional)` : "Con los datos de todo el país";
  REG_SECTIONS.forEach(id => {
    const h2 = document.querySelector(`#${id} h2`); if (!h2) return;
    let tag = h2.querySelector(".reg-tag");
    if (!tag) { tag = document.createElement("span"); tag.className = "reg-tag"; h2.append(tag); }
    tag.textContent = R ? ` · ${regName(R)}` : "";
  });
}
function updateFichaLinks() {
  const id = $("#region").value;
  const nr = document.querySelector("a.nav-out"); if (nr) nr.href = id ? `/resumen?lugar=${id}` : "/resumen";
  document.querySelectorAll("a.nav-inc").forEach(a => { a.href = id ? `/incendios?lugar=${id}` : "/incendios"; });
  document.querySelectorAll("a.nav-dan").forEach(a => { a.href = id ? `/danos?lugar=${id}` : "/danos"; });
  for (const [el, href] of [[$("#ficha-dl"), `/ficha/${id}.pdf`], [$("#ficha-ver"), `/ficha/${id}`]]) {
    if (id) { el.href = href; el.removeAttribute("aria-disabled"); el.removeAttribute("title"); if (el.id === "ficha-dl") el.setAttribute("download", ""); }
    else { el.removeAttribute("href"); el.setAttribute("aria-disabled", "true"); }
  }
}
$("#ficha-dl").addEventListener("click", () => toast("Generando la ficha PDF… (unos segundos)"));
$("#region").addEventListener("change", e => {
  const v = e.target.value;
  if (v.length === 4) { setRegion(v.slice(0, 2), {prov: v}); showProvince(); }
  else setRegion(v);
});
$("#reg-clear").addEventListener("click", () => setRegion(null));
const tip = $("#tip");
const showTip = (e, html) => { tip.innerHTML = html; tip.hidden = false; moveTip(e); };
// Abajo a la derecha del cursor; si no cabe, del otro lado (p. ej. con el mapa fijado en la esquina inferior derecha).
const moveTip = e => {
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let x = e.clientX + 14, y = e.clientY + 14;
  if (x + w > innerWidth - 8) x = Math.max(8, e.clientX - w - 14);
  if (y + h > innerHeight - 8) y = Math.max(8, e.clientY - h - 14);
  tip.style.left = x + "px"; tip.style.top = y + "px";
};
const hideTip = () => tip.hidden = true;
addEventListener("scroll", hideTip, {passive: true});

function seg(el, items, cur, onpick) {
  el.innerHTML = items.map(([v, l]) => `<button type="button" data-v="${v}" aria-pressed="${String(v) === String(cur)}">${l}</button>`).join("");
  el.onclick = e => { const b = e.target.closest("button"); if (b) onpick(b.dataset.v); };
}

/* ── carga y sondeo ─────────────────────────────────────────────── */
async function loadSnapshot() {
  const r = await fetch("/api/snapshot", {cache: "no-store"});
  if (!r.ok) throw new Error(`snapshot HTTP ${r.status}`);
  const data = await r.json();
  H = data.health; D = data;
  renderAll();
}
const sigOf = hs => hs.map(h => `${h.id}:${h.last_ok}`).join("|");
async function poll() {
  try {
    const r = await fetch("/api/health", {cache: "no-store"});
    if (r.status === 403) { location.reload(); return; }   // la licencia venció mientras el tablero estaba abierto
    const j = await r.json();
    H = j.sources;
    const sig = sigOf(H);
    if (sig !== lastSig) { lastSig = sig; await loadSnapshot(); loadLatest(); } else { renderHealth(); if (LAT.req) renderLatest(); }
    cargarReportes();
    $("#conn").className = "conn on"; $("#conn").textContent = "En vivo";
  } catch (e) {
    $("#conn").className = "conn off"; $("#conn").textContent = "Sin conexión con el colector";
  }
}
async function refresh(source, btn) {
  if (btn) { btn.disabled = true; btn.classList.add("spin"); }
  try {
    const r = await fetch(`/api/refresh/${source}`, {method: "POST"});
    const j = await r.json();
    const skipped = Object.entries(j).filter(([, v]) => !v.accepted);
    toast(source === "all"
      ? `Actualizando ${Object.keys(j).length - skipped.length} fuentes${skipped.length ? ` · ${skipped.length} omitidas (en curso o recién actualizadas)` : ""}`
      : j[source].accepted ? `Actualizando ${source}` : `No se actualizó ${source}: ${j[source].reason}`);
  } catch { toast("El colector no respondió"); }
  setTimeout(poll, 1500);
  if (btn) setTimeout(() => { btn.disabled = false; btn.classList.remove("spin"); }, 4000);
}
let toastT;
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toastT); toastT = setTimeout(() => t.hidden = true, 4200); }
$("#refresh-all").addEventListener("click", e => refresh("all", e.currentTarget));

/* ── estado de fuentes (se re-pinta en cada sondeo) ───────────────── */
// Línea de fuente bajo cada panel: estado, último éxito y frecuencia (se refresca con cada sondeo de /api/health).
function renderSrcLines() {
  document.querySelectorAll("[data-srcline]").forEach(el => {
    const h = H.find(x => x.id === el.dataset.srcline);
    el.innerHTML = h ? `<i class="dot ${h.status}"></i>${esc(STATUS[h.status] || h.status)} · actualizado ${ago(h.last_ok)} · ${every(h.interval_s)}${h.aviso ? ` · <span class="aviso">${esc(h.aviso)}</span>` : ""}` : "";
    el.title = h ? `${h.org} · ${h.name}${h.last_ok ? " · " + stampFull(h.last_ok) : ""}` : "";
  });
}
function renderHealth() {
  renderSrcLines();
  const ok = H.filter(h => h.status === "ok").length;
  $("#m-src").textContent = `${ok} de ${H.length}`;
  const newest = Math.max(...H.map(h => h.last_ok || 0));
  $("#m-built").innerHTML = newest ? `${stamp(newest)} · ${ago(newest)}` : "—";
  $("#m-built").title = stampFull(newest);
  $("#board").innerHTML = H.map(h => `<tr>
    <td class="org">${esc(h.org)}</td>
    <td class="pick" data-pick="${h.id}" title="Ver lo último recibido de esta fuente">${esc(h.name)}<div class="muted sub" title="En la hoja: ${esc(h.sheet)}">${esc(h.via)} · ${esc(h.geo)}</div></td>
    <td><span class="chip st-${h.status}">${STATUS[h.status] || h.status}</span></td>
    <td class="num when">${when(h.last_ok)}${h.failures && h.last_error ? `<div class="sub ${h.status === "fallando" ? "bad" : "warn"}">último error: ${stamp(h.last_error)} · ${plural(h.failures, "intento fallido", "intentos fallidos seguidos")}</div>` : ""}<div class="muted sub">${every(h.interval_s)} · próxima ${h.next_run ? stamp(h.next_run).split(" ").pop() : "—"} (${until(h.next_run)})</div></td>
    <td class="num">${h.items != null ? nf.format(h.items) : "—"}</td>
    <td class="msg ${!h.failures ? "muted" : h.status === "fallando" ? "bad" : "warn"}">${h.aviso ? `<b>${esc(h.aviso)}</b><br>` : ""}${esc(h.message || "")}</td>
    <td><button type="button" class="rbtn" data-src="${h.id}" aria-label="Actualizar ${esc(h.name)}" ${h.status === "actualizando" ? "disabled" : ""}>↻</button></td></tr>`).join("");
}
$("#board").addEventListener("click", e => {
  const b = e.target.closest(".rbtn"); if (b) return refresh(b.dataset.src, b);
  const p = e.target.closest("[data-pick]"); if (p) setLatSource(p.dataset.pick, true);
});

/* ── mapa ───────────────────────────────────────────────────────── */
const svg = d3.select("#map"), W = 640, H_ = 860;
svg.attr("viewBox", `0 0 ${W} ${H_}`);
let proj, path, gGeo, gProv, gMar, gDep, gPts, zoom, zt = d3.zoomIdentity;
const P = ll => zt.apply(proj(ll));
const placePoints = () => gPts.selectAll(".pt").attr("transform", function () { const [x, y] = P(this.__ll); return `translate(${x},${y})`; });
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const zoomTo = t => svg.transition().duration(reduceMotion ? 0 : 450).call(zoom.transform, t);
function zoomToFeature(f) {
  const [[x0, y0], [x1, y1]] = path.bounds(f);
  const k = Math.min(14, .86 / Math.max((x1 - x0) / W, (y1 - y0) / H_));
  zoomTo(d3.zoomIdentity.translate(W / 2, H_ / 2).scale(k).translate(-(x0 + x1) / 2, -(y0 + y1) / 2));
}
function initMap() {
  proj = d3.geoIdentity().reflectY(true).fitExtent([[16,16],[W-16,H_-16]], V.geo.deps);
  path = d3.geoPath(proj);
  gGeo = svg.append("g"); gProv = gGeo.append("g"); gMar = gGeo.append("g").attr("class", "mar"); gDep = gGeo.append("g"); gPts = svg.append("g");
  // tramas de los avisos de la DHN: oleaje en diagonal, viento en la diagonal opuesta; color = nivel
  const defs = svg.append("defs");
  for (const [t, ang] of [["o", 45], ["v", -45]]) for (const n of [2, 3, 4])
    defs.append("pattern").attr("id", `mar-${t}${n}`).attr("width", 5).attr("height", 5).attr("patternUnits", "userSpaceOnUse").attr("patternTransform", `rotate(${ang})`)
      // franjas claras alternadas con el color del nivel: se distinguen aunque la provincia ya esté pintada por SENAMHI
      .call(p => { p.append("rect").attr("width", 5).attr("height", 5).attr("style", "fill:var(--surface);fill-opacity:.85"); p.append("line").attr("x1", 1).attr("y1", 0).attr("x2", 1).attr("y2", 5).attr("style", `stroke:var(--n${n});stroke-width:2.6`); p.append("line").attr("x1", 3.6).attr("y1", 0).attr("x2", 3.6).attr("y2", 5).attr("style", "stroke:var(--ink);stroke-width:.5;stroke-opacity:.5"); });
  // zoom hasta nivel de calle (×60): los partes de bomberos en Lima están a cuadras unos de otros
  zoom = d3.zoom().scaleExtent([1, 60]).translateExtent([[-W * .15, -H_ * .15], [W * 1.15, H_ * 1.15]])
    .filter(e => e.type === "wheel" ? (e.ctrlKey || e.metaKey) : e.type === "touchstart" ? e.touches.length > 1 : !e.button)
    .on("zoom", e => {
      zt = e.transform; gGeo.attr("transform", zt); svg.classed("zoom-ciudades", zt.k >= 1.5).classed("zoom-ramales", zt.k >= 2.2);
      if (state.layers.ciudades && ciudadesDetalle() !== _detalleAntes) { renderPoints(); renderLayers(); }   // automático: entra o sale el detalle
      else { placePoints(); rotularCiudades(); }
      hideTip();
    });
  svg.call(zoom).on("dblclick.zoom", null);
  document.querySelector(".zoom").addEventListener("click", e => {
    const z = e.target.closest("button")?.dataset.z; if (!z) return;
    if (z === "reset") return zoomTo(d3.zoomIdentity);
    svg.transition().duration(reduceMotion ? 0 : 250).call(zoom.scaleBy, z === "in" ? 1.7 : 1 / 1.7);
  });
  gProv.selectAll("path").data(V.geo.provs.features).join("path").attr("class","prov").attr("d", path)
    .on("mousemove", (e, f) => showTip(e, provTip(f.properties))).on("mouseleave", hideTip)
    .on("click", (e, f) => { state.sel = f.properties.id; renderMap(); renderRail(); showProvince(); });
  gDep.selectAll("path").data(V.geo.deps.features).join("path").attr("class","dep").attr("d", path);
  $("#layers").addEventListener("change", e => { state.layers[e.target.dataset.l] = e.target.checked; if (e.target.dataset.l === "temperatura") renderMap(); renderPoints(); });
  // Todas / Ninguna: prende o apaga de una vez todas las capas puntuales (la capa base de avisos o UV no cambia)
  document.querySelector(".layers-all").addEventListener("click", e => {
    const b = e.target.closest("button[data-all]"); if (!b) return;
    for (const l of layerDefs) state.layers[l.id] = b.dataset.all === "1";
    renderLayers(); renderMap(); renderPoints();
  });
}

const state = { mode: "avisos", day: null, uvDay: 0, sel: null, region: (location.hash.match(/r=(\d{2})/) || [])[1] || null, latSource: (location.hash.match(/f=([a-z_]+)/) || [])[1] || null,
  olaH: 0, marZona: null, marH: "24", tempDia: 0, tempModo: "tmax", ciudadesModo: "auto",
  layers: { reportes: true, obras: true, recursos: false, oleaje: true, viento: false, olas: false, temperatura: false, rios: false, carreteras: false, ciudades: true, bomberos: true, indeci: true, alertas: true, sismos7: true, sismos: true, sismos365: false, hidro: true, vias: true, zonas: false, focos: false } };
let avisoDays = [], uvDays = [];
const uvBins = [3, 6, 8, 11];
const uvColor = v => v == null ? css("--land") : css(["--uv0","--uv1","--uv2","--uv3","--uv4"][d3.bisectRight(uvBins, v)]);
// Ventana corrida en horas (7 días = 168 h) hasta la hora del snapshot, en hora de Lima; no fechas de calendario.
const sismoT = s => Date.parse(`${s[0]}T${s[1]}:00-05:00`);
function recentSismos(dias = 30) { const fin = Date.parse(V.built.replace(" ", "T") + ":00-05:00"); return V.sismos.filter(s => sismoT(s) > fin - dias * 864e5); }
const sismosAnio = () => V.sismos.filter(s => s[0].startsWith(V.snapshot.slice(0, 4)));   // gráfico y tabla: año en curso
// Ciudades de referencia para orientarse. Nivel 1 siempre; nivel 2 (otras capitales y ciudades grandes) al acercar.
// [nombre, lon, lat, nivel, etiqueta a la izquierda]
const CIUDADES = [
  ["Lima", -77.043, -12.046, 1], ["Arequipa", -71.537, -16.409, 1], ["Trujillo", -79.029, -8.112, 1], ["Chiclayo", -79.841, -6.771, 1],
  ["Piura", -80.633, -5.195, 1], ["Iquitos", -73.247, -3.744, 1], ["Cusco", -71.968, -13.532, 1], ["Huancayo", -75.205, -12.065, 1],
  ["Pucallpa", -74.554, -8.379, 1], ["Tacna", -70.254, -18.007, 1], ["Puno", -70.020, -15.840, 1], ["Puerto Maldonado", -69.189, -12.593, 1, true],
  ["Tumbes", -80.452, -3.567, 2], ["Sullana", -80.685, -4.904, 2], ["Cajamarca", -78.500, -7.162, 2], ["Chachapoyas", -77.869, -6.232, 2],
  ["Moyobamba", -76.972, -6.034, 2, true], ["Tarapoto", -76.373, -6.483, 2], ["Jaén", -78.809, -5.708, 2], ["Chimbote", -78.578, -9.075, 2],
  ["Huaraz", -77.528, -9.528, 2], ["Huánuco", -76.242, -9.931, 2], ["Tingo María", -75.999, -9.295, 2], ["Cerro de Pasco", -76.256, -10.686, 2],
  ["Ica", -75.729, -14.068, 2], ["Pisco", -76.203, -13.710, 2, true], ["Ayacucho", -74.224, -13.159, 2], ["Huancavelica", -74.975, -12.786, 2, true],
  ["Abancay", -72.881, -13.634, 2], ["Juliaca", -70.133, -15.500, 2, true], ["Moquegua", -70.936, -17.196, 2], ["Ilo", -71.338, -17.639, 2, true],
  ["Yurimaguas", -76.092, -5.900, 2],
];
// Ríos: ref/rios.geojson (Natural Earth + ANA), dibujados con los límites para que acompañen el zoom
let RIOS = null, riosPedido = false;
function dibujarRios(ver) {
  let g = gGeo.select("g.rios");
  if (!ver) { g.remove(); return; }
  if (!RIOS) { if (!riosPedido) { riosPedido = true; fetch("/api/rios").then(r => r.json()).then(d => { RIOS = d; renderLayers(); renderPoints(); }).catch(() => { riosPedido = false; }); } return; }
  if (!g.empty()) return;
  g = gGeo.insert("g", () => gDep.node()).attr("class", "rios");
  g.selectAll("path").data(RIOS.features).join("path").attr("d", path).attr("class", f => `rio r${f.properties.r}`)
    .on("mousemove", (e, f) => f.properties.n && showTip(e, `<b>Río ${esc(f.properties.n)}</b><br><span style="opacity:.7">${esc(f.properties.f)}</span>`)).on("mouseleave", hideTip);
}
// Automático: detalle a partir de este acercamiento (el mismo en que aparecen los grados de temperatura)
const ZOOM_DETALLE = 1.5;
const ciudadesDetalle = () => state.ciudadesModo === "detalle" || (state.ciudadesModo === "auto" && zt.k >= ZOOM_DETALLE);
let _detalleAntes = false;
// Carreteras: Red Vial Nacional del MTC (ref/vias.geojson). Ejes longitudinales gruesos, transversales medianas y ramales
// finos (estos solo al acercar). Al pasar el mouse: la ruta y las emergencias viales de PROVIAS que tiene hoy.
const RUTA_NOMBRE = {"PE-1N": "Panamericana Norte", "PE-1S": "Panamericana Sur", "PE-3N": "Longitudinal de la Sierra Norte",
  "PE-3S": "Longitudinal de la Sierra Sur", "PE-5N": "Longitudinal de la Selva Norte", "PE-5S": "Longitudinal de la Selva Sur", "PE-22": "Carretera Central"};
let VIAS = null, viasPedido = false;
function dibujarCarreteras(ver) {
  let g = gGeo.select("g.carreteras");
  if (!ver) { g.remove(); return; }
  if (!VIAS) { if (!viasPedido) { viasPedido = true; fetch("/api/carreteras").then(r => r.json()).then(d => { VIAS = d; renderLayers(); renderPoints(); }).catch(() => { viasPedido = false; }); } return; }
  if (!g.empty()) return;
  g = gGeo.insert("g", () => gDep.node()).attr("class", "carreteras");
  const datos = VIAS.features.slice().sort((a, b) => b.properties.r - a.properties.r);
  // estilo de mapa de carreteras: borde oscuro debajo y línea clara encima, para que se lean sobre cualquier color de fondo
  g.append("g").selectAll("path").data(datos).join("path").attr("d", path).attr("class", f => `via-borde v${f.properties.r}`);
  g.append("g").selectAll("path").data(datos).join("path").attr("d", path).attr("class", f => `via v${f.properties.r}`)
    .on("mousemove", (e, f) => {
      const r = f.properties.ruta, em = (V.vias || []).filter(v => v.ruta === r);
      showTip(e, `<b>Ruta ${esc(r)}</b>${RUTA_NOMBRE[r] ? ` · ${RUTA_NOMBRE[r]}` : ""}<br>${["", "Eje longitudinal", "Transversal", "Ramal"][f.properties.r]} de la Red Vial Nacional`
        + (em.length ? `<br><b>${em.length} emergencia${em.length > 1 ? "s" : ""} vial${em.length > 1 ? "es" : ""} PROVIAS</b> en esta ruta` : "") + `<br><span style="opacity:.7">MTC · red al 31/12/2014</span>`);
    }).on("mouseleave", hideTip);
}
// Ciudades del mapa: las 33 de referencia y las 276 del pronóstico de SENAMHI (ubicadas en ref/ciudades_senamhi.json).
// Todas llevan punto; el nombre se muestra si cabe sin pisar otro, primero las de referencia: al acercar aparecen más.
let _ciudades = null;
function ciudadesMapa() {
  const pron = V?.pronostico || [];
  if (_ciudades && _ciudades.src === pron) return _ciudades.list;
  const base = CIUDADES.map(([n, lon, lat, t, izq]) => ({n, lon, lat, t, izq}));
  const vistas = new Set(), extra = [];
  for (const p of pron) {
    const k = `${p.ciudad}|${p.departamento}`;
    if (p.lon == null || vistas.has(k)) continue;
    vistas.add(k);
    if (base.some(b => Math.abs(b.lon - p.lon) < .06 && Math.abs(b.lat - p.lat) < .06)) continue;   // ya está como ciudad de referencia
    extra.push({n: title(p.ciudad.split("/")[0].trim()), dpto: title(p.departamento), lon: p.lon, lat: p.lat, t: 3, key: k, reg: p.reg});
  }
  _ciudades = {src: pron, list: [...base, ...extra]};
  return _ciudades.list;
}
function rotularCiudades() {
  if (!gPts) return;
  const nodos = gPts.selectAll(".ciudad").nodes().sort((a, b) => a.__d.t - b.__d.t), puestas = [];
  for (const n of nodos) {
    const txt = n.querySelector("text"), d = n.__d, [x, y] = P(n.__ll);
    if (txt.__w == null) { txt.style.display = ""; txt.__w = txt.getComputedTextLength(); }
    const h = d.t === 1 ? 16 : d.t === 2 ? 14 : 11, x0 = d.izq ? x - 7 - txt.__w : x + 5, x1 = d.izq ? x - 5 : x + 7 + txt.__w;
    const libre = x1 > 0 && x0 < W && y > 0 && y < H_ && !puestas.some(b => x0 < b[2] && x1 > b[0] && y - h / 2 < b[3] && y + h / 2 > b[1]);
    txt.style.display = libre ? "" : "none";
    if (libre) puestas.push([x0 - 2, y - h / 2, x1 + 2, y + h / 2]);
  }
}
// Bomberos: color por tipo de emergencia; más grande y con borde si sigue en atención
const BOM_T = [["Incendio", "--fuego"], ["Materiales Peligrosos (Incidente)", "--n3"], ["Rescate", "--hidro"], ["Accidente Vehicular", "--ink-2"], ["Servicio Especial", "--ink-3"]];
const bomColor = c => css((BOM_T.find(([t]) => t === c) || [, "--ink-3"])[1]);
const bomCorto = c => c === "Materiales Peligrosos (Incidente)" ? "Materiales peligrosos" : c ? c[0] + c.slice(1).toLowerCase() : "—";
const bomLL = b => b.lon != null ? [b.lon, b.lat] : V.provCentroids[b.prov];
// Mar (DHN): nivel por provincia costera el día elegido, según los avisos vigentes de oleaje o de viento
const MAR_PAL = {oleaje: {2: "ligero", 3: "moderado", 4: "fuerte"}, viento: {2: "brisa fresca", 3: "brisa fuerte", 4: "viento fuerte"}};
const OLA_V = ["próximas 24 h", "de 24 a 48 h", "de 48 a 72 h"];
function marDia() { return state.day || V.snapshot; }
function marProvs(tipo, dia = marDia()) {
  const out = {};
  for (const a of (D.mar?.avisos || []).filter(a => a.tipo === tipo))
    for (const [p, n] of Object.entries(a.provs_por_dia?.[dia] || {})) if (!out[p] || n > out[p].n) out[p] = {n, a};
  return out;
}
// Obras de prevención de la ANA: color por estado; las barreras contra huaicos, en estrella
const OBRA_E = [["Vencida", "--n4"], ["En Ejecución", "--sismo"], ["Por Ejecutar", "--ink-3"], ["Concluida", "--ok"]];
const obraColor = e => css((OBRA_E.find(([t]) => t === e) || [, "--ink-3"])[1]);
const obraNombre = e => ({"En Ejecución": "en ejecución", "Por Ejecutar": "por ejecutar", Vencida: "vencida", Concluida: "concluida"})[e] || e;
// Recursos declarados ante el FEN (INDECI): color y símbolo por tipo; la maquinaria, un triángulo por provincia
const REC_T = {maquinaria: ["--geo", "symbolTriangle"], albergue: ["--ok", "symbolSquare"], zona_segura: ["--n1", "symbolCircle"],
  helipunto: ["--sismo", "symbolWye"], agua: ["--hidro", "symbolCircle"], refugio_ie: ["--uv3", "symbolSquare"]};
const recColor = t => css((REC_T[t] || ["--ink-3"])[0]);
const recSym = t => d3[(REC_T[t] || [, "symbolCircle"])[1]];
const recTipo = t => (D?.recursos?.tipos || {})[t] || t;
const REC_N = {albergue: ["albergue", "albergues"], zona_segura: ["zona segura", "zonas seguras"], helipunto: ["helipunto", "helipuntos"],
  agua: ["punto de agua", "puntos de agua"], refugio_ie: ["II.EE. refugio", "II.EE. refugio"], maquinaria: ["máquina", "máquinas"]};
const recCuenta = (t, k) => `${nf.format(k)} ${(REC_N[t] || [t, t])[k === 1 ? 0 : 1]}`;
function recMaqProv() {   // maquinaria de distrito y provincia sumada por provincia (lo departamental no se puede ubicar)
  return d3.rollups((V.recursos?.maquinaria || []).filter(m => m.prov && V.provCentroids[m.prov]), v => ({n: d3.sum(v, m => m.cantidad), items: v}), m => m.prov)
    .map(([prov, x]) => ({prov, ...x})).sort((a, b) => b.n - a.n);
}
const layerDefs = [
  { id: "indeci", label: "Emergencias INDECI", sw: `<i class="sw sq" style="background:var(--indeci)"></i>`, n: () => new Set(V.indeci.filter(i => i.prov).map(i => i.prov)).size, unit: "prov." },
  { id: "alertas", label: "Incendios forestales (SERFOR)", sw: `<i class="sw" style="background:var(--fuego);outline:1px solid var(--ink)"></i>`, n: () => incendios().length },
  { id: "focos", label: "Focos de calor 24 h", sw: `<i class="sw" style="background:var(--fuego);opacity:.45"></i>`, n: () => V.focos.length },
  { id: "sismos7", label: "Sismos · 7 días", sw: `<i class="sw" style="background:var(--sismo);opacity:.55;border:2px solid var(--sismo)"></i>`, n: () => recentSismos(7).length },
  { id: "sismos", label: "Sismos · 30 días", sw: `<i class="sw" style="border:2px solid var(--sismo)"></i>`, n: () => recentSismos(30).length },
  { id: "sismos365", label: "Sismos · 12 meses", sw: `<i class="sw" style="border:1px solid var(--sismo);opacity:.6"></i>`, n: () => recentSismos(365).length },
  { id: "hidro", label: "Estaciones hidrológicas", sw: `<i class="sw" style="background:var(--hidro)"></i>`, n: () => V.hidro.length },
  { id: "vias", label: "Emergencias viales PROVIAS", sw: `<i class="sw" style="background:var(--n2);transform:rotate(45deg);border-radius:1px;outline:1.5px solid var(--ink)"></i>`, n: () => V.vias.length },
  { id: "reportes", label: "Reportes de corresponsales", sw: `<i class="sw" style="background:var(--brand);border:1.5px solid var(--ink);border-radius:2px"></i>`, n: () => REP.lista.filter(r => r.lat != null && r.estado !== "descartado" && (!state.region || r.reg === state.region)).length },
  { id: "recursos", label: "Recursos declarados · INDECI", sw: `<i class="sw" style="background:var(--ok);border-radius:1px;border:1.5px solid var(--ink)"></i>`, n: () => (V.recursos?.puntos || []).length + recMaqProv().length },
  { id: "obras", label: "Obras de prevención · ANA", sw: `<i class="sw" style="background:var(--sismo);border:1.5px solid var(--ink)"></i>`, n: () => (V.obras || []).length },
  { id: "oleaje", label: "Oleaje · avisos DHN", sw: `<i class="sw sq" style="background:repeating-linear-gradient(45deg,var(--n3) 0 2px,transparent 2px 4px)"></i>`, n: () => Object.keys(marProvs("oleaje")).filter(p => !state.region || p.startsWith(state.region)).length, unit: "prov." },
  { id: "viento", label: "Viento · avisos DHN", sw: `<i class="sw sq" style="background:repeating-linear-gradient(-45deg,var(--n2) 0 2px,transparent 2px 4px)"></i>`, n: () => Object.keys(marProvs("viento")).filter(p => !state.region || p.startsWith(state.region)).length, unit: "prov." },
  { id: "olas", label: "Altura de ola · Open-Meteo", sw: `<i class="sw" style="background:var(--hidro);opacity:.75"></i>`, n: () => (V.mar?.puntos || []).length, unit: "puntos" },
  { id: "bomberos", label: "Bomberos · 24 h (CGBVP)", sw: `<i class="sw" style="width:12px;height:12px;border-radius:0;clip-path:polygon(35% 0,65% 0,65% 35%,100% 35%,100% 65%,65% 65%,65% 100%,35% 100%,35% 65%,0 65%,0 35%,35% 35%);background:var(--fuego)"></i>`, n: () => V.bomberos.length },
  { id: "temperatura", label: "Temperatura · pronóstico SENAMHI", sw: `<i class="sw" style="background:linear-gradient(90deg,#2C6FB7,#E9E7E0,#C8372D)"></i>`, n: () => tempPuntos().length, unit: "ciudades" },
  { id: "carreteras", label: "Carreteras · Red Vial Nacional", sw: `<i class="sw" style="background:#FBF8F1;border:1.5px solid #2A241E;border-radius:2px;height:5px;transform:translateY(2px)"></i>`, n: () => VIAS ? VIAS.features.length : 141, unit: "rutas" },
  { id: "rios", label: "Ríos principales", sw: `<i class="sw" style="background:none;border:0;border-top:2.5px solid #2E86C1;border-radius:0;height:0;transform:translateY(3px)"></i>`, n: () => RIOS ? new Set(RIOS.features.map(f => f.properties.n).filter(Boolean)).size : 94, unit: "ríos" },
  { id: "ciudades", label: "Ciudades (nombres)", sw: `<i class="sw" style="background:var(--surface);border:2px solid var(--ink);transform:scale(.8)"></i>`, n: () => ciudadesDetalle() ? ciudadesMapa().length : CIUDADES.length },
  { id: "zonas", label: "Zonas críticas INGEMMET (color = nivel)", sw: `<i class="sw tri"></i>`, n: () => V.zonas.length },
];
// Un incendio = un reporte PIF de SERFOR (CODREP): agrupa todas las detecciones satelitales del mismo fuego. Cuántas
// detecciones reúne y en cuántos días es la mejor medida de su tamaño que publica SERFOR (no informa hectáreas).
let _inc = {src: null, list: []};
function incendios() {
  if (_inc.src === V.alertas) return _inc.list;
  const list = d3.groups(V.alertas, a => a.cod || a._key).map(([cod, al]) => {
    const fechas = al.map(a => a.fecha).filter(Boolean).sort(), ult = d3.greatest(al, a => `${a.fecha} ${a.hora}`);
    return {cod, n: al.length, lon: d3.mean(al, a => a.lon), lat: d3.mean(al, a => a.lat), estado: ult.estado, ini: fechas[0], fin: fechas[fechas.length - 1],
      dias: new Set(fechas).size, cob: d3.greatest(d3.rollups(al, v => v.length, a => a.cob || "—"), d => d[1])[0],
      lugares: [...new Set(al.map(a => `${title(a.dist)}, ${title(a.prov)}`))], dep: title(ult.dep), ult};
  }).sort((a, b) => b.n - a.n);   // los grandes primero: los chicos quedan encima y se pueden señalar
  _inc = {src: V.alertas, list};
  return list;
}
const incR = n => Math.min(18, 3 + Math.sqrt(n) * 1.15);
// Temperatura: pronóstico por ciudad de SENAMHI. Escala divergente (azul frío, gris al centro, rojo caliente), distinta
// para máximas y mínimas porque sus rangos no se parecen.
// La escala sigue el rango del día elegido (del 5 % más frío al 5 % más caliente, centro en la mediana), así el frío y el
// calor se distinguen siempre; la leyenda muestra los grados de cada extremo.
const tempDom = () => {
  const v = tempPuntos().map(p => p[state.tempModo]).sort(d3.ascending);
  if (v.length < 5) return state.tempModo === "tmax" ? [6, 21, 36] : [-10, 6, 22];
  const lo = Math.floor(d3.quantileSorted(v, .05)), hi = Math.ceil(d3.quantileSorted(v, .95)), me = Math.round(d3.quantileSorted(v, .5));
  return [lo, Math.min(Math.max(me, lo + 1), hi - 1), hi];
};
const tempColor = v => d3.scaleLinear().domain(tempDom()).range(["#2C6FB7", "#E9E7E0", "#C8372D"]).clamp(true)(v);
const tempDias = () => [...new Set((V?.pronostico || []).map(p => p.dia))].slice(0, 3);
// Temperatura por provincia: promedio ponderado (1/distancia²) de las 4 ciudades del pronóstico más cercanas a su
// centro; si una ciudad está casi en el centro, manda ella. Así el mapa se pinta entero con la "forma" del calor y del frío.
let _tempProv = null;
function tempProv() {
  const pts = tempPuntos(), m = state.tempModo, clave = `${state.tempDia}|${m}`;
  if (_tempProv && _tempProv.clave === clave && _tempProv.src === V.pronostico) return _tempProv.val;
  const val = {};
  for (const [id, c] of Object.entries(V.provCentroids || {})) {
    const cos = Math.cos(c[1] * Math.PI / 180);
    const cerca = pts.map(p => [Math.hypot((p.lon - c[0]) * cos, p.lat - c[1]), p[m]]).sort((a, b) => a[0] - b[0]).slice(0, 4);
    if (!cerca.length || cerca[0][0] > 2.5) continue;            // a más de ~250 km de toda ciudad: sin dato
    if (cerca[0][0] < .03) { val[id] = cerca[0][1]; continue; }
    const w = cerca.map(([d]) => 1 / (d * d));
    val[id] = d3.sum(cerca, (x, i) => x[1] * w[i]) / d3.sum(w);
  }
  _tempProv = {clave, src: V.pronostico, val};
  return val;
}
const tempPuntos = () => { const d = tempDias()[state.tempDia]; return (V?.pronostico || []).filter(p => p.dia === d && p.lon != null && p[state.tempModo] != null); };
function renderLayers() {
  $("#layers").innerHTML = layerDefs.map(l => `<label><input type="checkbox" data-l="${l.id}" ${state.layers[l.id] ? "checked" : ""}>${l.sw}<span>${l.label}</span><span class="muted num">${nf.format(l.n())}${l.unit ? " " + l.unit : ""}</span></label>`).map((h, i) => layerDefs[i].id === "alertas"
    ? h + `<p class="lnote">Cada círculo es un incendio; su tamaño, las detecciones satelitales que reúne. Lleno: activo · claro: controlado · aro: extinguido.</p>`
    : layerDefs[i].id === "recursos" ? h + `<p class="lnote">${Object.entries(REC_T).map(([t, [c]]) => `<span class="recsw" style="--c:var(${c})"></span>${esc((REC_N[t] || [t, t])[1])}`).join(" · ")}. La maquinaria se suma por provincia (triángulo, tamaño según unidades). Declarado, no necesariamente disponible.</p>`
    : layerDefs[i].id === "obras" ? h + `<p class="lnote">Limpieza de cauces y barreras contra huaicos (★). Azul: en ejecución · gris: por ejecutar · rojo: plazo vencido · verde: concluida.</p>`
    : layerDefs[i].id === "oleaje" ? h + `<p class="lnote">Trama sobre las provincias costeras con aviso el día elegido: amarillo ligero, naranja moderado, rojo fuerte.</p>`
    : layerDefs[i].id === "ciudades" ? h + `<div class="lnote olah" id="ciu-ctl">${[["auto", "Automático"], ["principales", "Principales"], ["detalle", "Detalle"]].map(([v, t]) => `<button type="button" data-c="${v}" aria-pressed="${state.ciudadesModo === v}">${t}</button>`).join("")}<span>Automático: las 33 principales en todo el país y, al acercar el mapa, también las 276 ciudades del pronóstico. Los nombres aparecen donde caben.</span></div>`
    : layerDefs[i].id === "carreteras" ? h + `<p class="lnote">Red Vial Nacional del MTC (une las capitales de departamento). Gruesas: los tres ejes longitudinales (Panamericana, Sierra, Selva); medianas: transversales; finas, al acercar: ramales. Pase el mouse por una ruta para ver su código y las emergencias PROVIAS que tiene.</p>`
    : layerDefs[i].id === "rios" ? h + `<p class="lnote">Ríos principales: Natural Earth, ANA y OpenStreetMap. Pase el mouse por un río para ver su nombre.</p>`
    : layerDefs[i].id === "temperatura" ? h + `<div class="lnote olah" id="temp-ctl">${tempDias().map((d, k) => `<button type="button" data-d="${k}" aria-pressed="${state.tempDia === k}">${esc(d.replace(/^(\p{L}+),\s*(\d+) de (\p{L}+)/u, (m, a, b) => `${a.slice(0, 3)} ${+b}`))}</button>`).join("")}`
      + `<button type="button" data-m="tmax" aria-pressed="${state.tempModo === "tmax"}">Máximas</button><button type="button" data-m="tmin" aria-pressed="${state.tempModo === "tmin"}">Mínimas</button>`
      + `<span class="tleg"><i style="background:linear-gradient(90deg,${tempDom().map(v => tempColor(v)).join(",")})"></i>${tempDom().map(v => `<b>${v}°</b>`).join("")}</span>`
      + `<span>Pronóstico por ciudad de SENAMHI. Acerque el mapa para ver los grados; clic en una ciudad para su ficha.</span></div>`
    : layerDefs[i].id === "olas" ? h + `<div class="lnote olah" id="ola-h">${OLA_V.map((t, k) => `<button type="button" data-h="${k}" aria-pressed="${state.olaH === k}">${k === 0 ? "0–24 h" : k === 1 ? "24–48 h" : "48–72 h"}</button>`).join("")}<span>Modelo global, referencial: no es la DHN.</span></div>` : h).join("");
  const cc = $("#ciu-ctl"); if (cc) cc.onclick = e => { const b = e.target.closest("button"); if (!b) return; state.ciudadesModo = b.dataset.c; renderLayers(); renderPoints(); };
  const tc = $("#temp-ctl"); if (tc) tc.onclick = e => { const b = e.target.closest("button"); if (!b) return; if (b.dataset.d) state.tempDia = +b.dataset.d; if (b.dataset.m) state.tempModo = b.dataset.m; renderLayers(); renderMap(); renderPoints(); };
  const oh = $("#ola-h"); if (oh) oh.onclick = e => { const b = e.target.closest("button"); if (!b) return; state.olaH = +b.dataset.h; renderLayers(); renderPoints(); renderMar(); };
}
function provTip(p) {
  if (state.layers.temperatura) { const t = tempProv()[p.id]; return `<b>${title(p.n)}</b> · ${title(p.d)}<br>${t != null ? `Temperatura ${state.tempModo === "tmax" ? "máxima" : "mínima"} estimada: <b>${Math.round(t)} °C</b>` : "Sin dato de temperatura"}<br><span style="opacity:.7">De las ciudades del pronóstico de SENAMHI más cercanas</span>`; }
  if (state.mode === "uv") { const u = V.uv[p.id]; return `<b>${title(p.n)}</b> · ${title(p.d)}<br>${u ? `UV ${u.v[state.uvDay]} (pico ${u.h[state.uvDay]})` : "Sin zona UV"}`; }
  const c = (V.levelsByDay[state.day] || {})[p.id];
  const mar = ["oleaje", "viento"].filter(t => state.layers[t]).map(t => [t, marProvs(t)[p.id]]).filter(([, v]) => v)
    .map(([t, v]) => `<br>${t === "oleaje" ? "Oleaje" : "Viento"} (DHN): <b>${MAR_PAL[t][v.n]}</b> · aviso N° ${esc(v.a.numero)}`).join("");
  return `<b>${title(p.n)}</b> · ${title(p.d)}<br>${c ? `Nivel ${c.m} · avisos ${c.a.map(x => "#" + x[0]).join(", ")}` : "Sin aviso nivel 2+"}${mar}`;
}
function renderControls() {
  seg($("#mode"), [["avisos","Avisos SENAMHI"],["uv","Índice UV"]], state.mode, v => { state.mode = v; renderMapAll(); });
  if (state.mode === "avisos") seg($("#days"), avisoDays.map(d => [d, fmtDay(d)]), state.day, v => { state.day = v; renderMapAll(); });
  else seg($("#days"), uvDays.map((d, i) => [i, fmtDay(d)]), state.uvDay, v => { state.uvDay = +v; renderMapAll(); });
}
function renderMap() {
  const lv = V.levelsByDay[state.day] || {};
  const tp = state.layers.temperatura ? tempProv() : null;
  gProv.selectAll("path").attr("fill", f => {
    if (tp) return tp[f.properties.id] != null ? tempColor(tp[f.properties.id]) : css("--land");
    if (state.mode === "uv") { const u = V.uv[f.properties.id]; return uvColor(u ? u.v[state.uvDay] : null); }
    const c = lv[f.properties.id]; return c ? css("--n" + c.m) : css("--land");
  }).classed("sel", f => f.properties.id === state.sel)
    .classed("out", f => !!state.region && !f.properties.id.startsWith(state.region));
  gDep.selectAll("path").classed("reg", f => f.properties.id === state.region);
  $("#legend").innerHTML = tp
    ? `<span class="tleg-map"><i style="background:linear-gradient(90deg,${d3.range(0, 1.01, .1).map(t => tempColor(tempDom()[0] + t * (tempDom()[2] - tempDom()[0]))).join(",")})"></i>${tempDom().map(v => `<b>${v} °C</b>`).join("")}</span>`
      + `<span class="muted">Temperatura ${state.tempModo === "tmax" ? "máxima" : "mínima"} pronosticada · ${esc(tempDias()[state.tempDia] || "")} · cada provincia toma las ciudades del pronóstico más cercanas</span>`
    : state.mode === "uv"
    ? ["0–2 bajo","3–5 moderado","6–7 alto","8–10 muy alto","11+ extremo"].map((l, i) => `<span><i style="background:var(--uv${i})"></i>${l}</span>`).join("") + `<span class="muted">Zonas UV sin provincia: ${V.uvUnmatched.length}</span>`
    : [2,3,4].map(n => `<span><i style="background:var(--n${n})"></i>Nivel ${n} · ${["","","amarillo","naranja","rojo"][n]}</span>`).join("") + `<span><i style="background:var(--land)"></i>Sin aviso o nivel 1</span>`;
}
// ── MIDIS · contexto social ────────────────────────────────────────────────────
// MIDIS solo responde por distrito, así que la provincia es la suma de los suyos. Se pide al
// abrir la provincia (no en el snapshot) y queda cacheado aquí y en el servidor 30 días.
const midisProv = {};

async function loadMidis(id) {
  if (midisProv[id]) return;                       // ya cargado, cargando o fallido
  midisProv[id] = {cargando: true};
  try {
    const r = await fetch(`/api/midis/provincia/${id}`, {cache: "no-store"});
    midisProv[id] = r.ok ? await r.json() : {error: `HTTP ${r.status}`};
  } catch (e) {
    midisProv[id] = {error: e.message};
  }
  const node = document.querySelector(`[data-midis="${id}"]`);
  if (node) node.outerHTML = midisBlock(id);       // solo si esa provincia sigue seleccionada
}

function midisBlock(id) {
  const m = midisProv[id];
  const head = `<p class="eyebrow">Contexto social · MIDIS · REDInforma</p>`;
  if (!m || m.cargando) {
    return `<div class="pdetail midis" data-midis="${id}">${head}
      <p class="muted">Consultando distrito por distrito en REDInforma…</p></div>`;
  }
  if (m.error) {
    return `<div class="pdetail midis">${head}<p class="muted">No disponible: ${esc(m.error)}</p></div>`;
  }
  const f = v => (v === null || v === undefined) ? "—" : Math.round(v).toLocaleString("es-PE");
  const sh = (a, b) => (a && b) ? `${Math.round(a / b * 100)} %` : "—";
  const R = m.resumen, D = m.distritos || [];
  const prog = (m.programas || []).slice(0, 5);
  return `<div class="pdetail midis">${head}
    <h3>${esc(title(m.provincia || ""))} <span class="muted" style="font-weight:400">· ${m.n_con_datos} de ${m.n_distritos} distritos</span></h3>
    <dl>
      <dt>Población</dt><dd class="num"><b>${f(R.poblacion_total)}</b> · 0-5 años ${f(R.pob_0_5)} · 65+ ${f(R.pob_65_mas)}</dd>
      <dt>Viviendas</dt><dd class="num">${f(R.viviendas_total)} · agua ${sh(R.viv_agua, R.viviendas_total)} · luz ${sh(R.viv_electricidad, R.viviendas_total)} · saneamiento ${sh(R.viv_saneamiento, R.viviendas_total)}</dd>
      <dt>Servicios</dt><dd class="num">${f(R.ee_salud)} establecimientos de salud · ${f(R.iiee)} II.EE.</dd>
      ${m.pobreza ? `<dt>Pobreza</dt><dd class="num">${m.pobreza.min} %–${m.pobreza.max} % según el distrito <span class="muted">(mayor: ${esc(title(m.pobreza.mayor.distrito))})</span></dd>` : ""}
    </dl>
    ${prog.length ? `<table class="mini"><thead><tr><th>Programa social</th><th class="num">Usuarios</th></tr></thead><tbody>
      ${prog.map(p => `<tr><td>${esc(p.programa)}</td><td class="num">${f(p.valor)}</td></tr>`).join("")}</tbody></table>` : ""}
    ${D.length ? `<details><summary>Ver los ${D.length} distritos</summary>
      <table class="mini"><thead><tr><th>Distrito</th><th class="num">Población</th><th class="num">0-5</th><th class="num">Agua</th><th class="num">Pobreza</th></tr></thead><tbody>
      ${D.map(x => `<tr><td>${esc(title(x.distrito || ""))}</td><td class="num">${f(x.poblacion_total)}</td><td class="num">${f(x.pob_0_5)}</td><td class="num">${sh(x.viv_agua, x.viviendas_total)}</td><td class="num">${x.pobreza_pct == null ? "—" : x.pobreza_pct + " %"}</td></tr>`).join("")}
      </tbody></table></details>` : ""}
    <p class="note">Totales sumando distritos; los porcentajes y tasas no se suman. Datos de 2017-2021.
      <a href="${esc(m.enlace)}" target="_blank" rel="noopener">Ver en REDInforma</a> ·
      <a href="/api/midis/provincia/${id}" target="_blank" rel="noopener">JSON</a></p></div>`;
}

/* ── navegación entre secciones, en la barra fija ─────────────────── */
function initJumpbar() {
  const bar = $("#jumpbar");
  if (!bar) return;
  const links = [...bar.querySelectorAll("a")];
  const byId = new Map(links.map(a => [a.getAttribute("href").slice(1), a]));
  const secs = links.map(a => document.getElementById(a.getAttribute("href").slice(1))).filter(Boolean);
  if (!secs.length) return;

  const mark = id => {
    links.forEach(a => a.removeAttribute("aria-current"));
    byId.get(id)?.setAttribute("aria-current", "true");
  };

  // Un reanclaje en curso tiene que dejar de mandar en cuanto empieza otro salto o el usuario
  // se mueve por su cuenta; si no, sigue tirando de la página hacia el destino anterior.
  let jumpSeq = 0, quietUntil = 0;
  const cederControl = () => { jumpSeq++; quietUntil = 0; };
  ["wheel", "touchstart", "keydown"].forEach(ev => addEventListener(ev, cederControl, {passive: true}));

  // La barra fija crece al envolverse en pantallas angostas, así que su alto se mide, no se fija.
  const barH = () => (document.querySelector(".regbar")?.getBoundingClientRect().height || 104) + 8;
  const syncBarH = () => document.documentElement.style.setProperty("--barH", barH() + "px");
  syncBarH();
  addEventListener("resize", syncBarH);
  // La barra crece cuando la navegación se envuelve en varias filas, y eso pasa después de la
  // primera medición: sin observarla, --barH se queda corto y las secciones aterrizan debajo.
  const rb = document.querySelector(".regbar");
  if (rb && window.ResizeObserver) new ResizeObserver(syncBarH).observe(rb);

  // El salto nativo por ancla aterriza mal: la página mide ~40.000 px y las tablas y gráficos
  // siguen llenándose después de cargar, así que el destino se corre mientras el navegador va
  // hacia él (y a veces se pasa hasta el final). Se salta a mano y se corrige hasta que el
  // título quede justo debajo de la barra.
  // scroll-margin-top (var --barH) hace que el navegador deje el título debajo de la barra.
  // Aun así hay que volver a anclar: la página sigue creciendo mientras se baja, así que el
  // destino se mueve. Se reancla hasta que el título se quede quieto tres veces seguidas.
  const goTo = id => {
    const el = document.getElementById(id);
    if (!el) return;
    syncBarH();                 // el alto de la barra puede haber cambiado desde el último salto
    const seq = ++jumpSeq;      // este salto manda hasta que empiece otro o el usuario se mueva
    quietUntil = performance.now() + 4300;
    el.scrollIntoView({block: "start", behavior: reduceMotion ? "auto" : "smooth"});
    const t0 = performance.now();
    let stable = 0;
    const settle = () => {
      if (seq !== jumpSeq) return;
      if (Math.abs(el.getBoundingClientRect().top - barH()) <= 6) {
        // El observador solo avisa al cruzar un umbral: su último aviso durante el
        // desplazamiento se queda pegado y marca la sección vecina. Al llegar se remarca.
        if (++stable >= 3) return mark(id);
      } else {
        stable = 0;
        el.scrollIntoView({block: "start", behavior: "auto"});
      }
      if (performance.now() - t0 < 4000) setTimeout(settle, 160);
      else mark(id);
    };
    setTimeout(settle, reduceMotion ? 50 : 600);
  };

  bar.addEventListener("click", e => {
    const a = e.target.closest("a[href^='#']");
    if (!a) return;
    e.preventDefault();
    const id = a.getAttribute("href").slice(1);
    history.replaceState(null, "", "#" + id);
    mark(id);
    goTo(id);
  });

  // Marca la sección más alta que esté entrando en pantalla, por debajo de la barra fija.
  const io = new IntersectionObserver(entries => {
    if (performance.now() < quietUntil) return;   // durante un salto manda la sección elegida
    const vis = entries.filter(e => e.isIntersecting)
      .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
    if (vis) mark(vis.target.id);
  }, {rootMargin: `-${Math.round(barH())}px 0px -55% 0px`});
  secs.forEach(s => io.observe(s));
}
if (document.readyState === "loading") addEventListener("DOMContentLoaded", initJumpbar);
else initJumpbar();

function showProvince() {   // lleva el bloque de la provincia al tope del panel lateral (sin mover la página)
  const side = $("#side"), el = $("#pdetail");
  if (side && el) {
    side.scrollTo({top: el.offsetTop - side.offsetTop - 8, behavior: reduceMotion ? "auto" : "smooth"});
    el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");   // señal de que el contenido cambió
  }
  if (D) renderRegionBar();   // el selector de lugar y la ficha PDF siguen a la provincia elegida en el mapa
}

/* ── ficha de detalle ───────────────────────────────────────────── */
const ORG = { igp: "IGP · Centro Sismológico Nacional", serfor: "SERFOR · Monitoreo satelital", ingemmet: "INGEMMET · Perú Alerta",
  senamhi_hidro: "SENAMHI · Hidrología", senamhi_avisos: "SENAMHI · Avisos meteorológicos", senamhi_uv: "SENAMHI · Índice UV",
  senamhi_pronostico: "SENAMHI · Pronóstico por ciudad", corresponsales: "Reporte de corresponsal", indeci: "INDECI · COEN", firms: "NASA FIRMS",
  enfen: "ENFEN · Comisión Multisectorial", com_pnp: "PNP · Comunicados (gob.pe)", com_provias: "PROVIAS Nacional · Notas de prensa (gob.pe)",
  provias: "PROVIAS Nacional · Emergencias viales", bomberos: "CGBVP · Bomberos · Emergencias 24 horas", dhn_avisos: "DHN · Avisos especiales de oleaje y viento",
  olas_openmeteo: "Open-Meteo Marine · modelo referencial", ana_obras: "ANA · Obras de prevención (puntos críticos 2026)", indeci_recursos: "INDECI · Recursos declarados ante el FEN", sinpad_historial: "INDECI · SINPAD (historial)", declaratorias: "PCM · Estados de emergencia (El Peruano)", com_mtc: "MTC · Notas de prensa (gob.pe)", com_mininter: "MININTER · Notas de prensa (gob.pe)" };
const refId = r => r && `${r.source}|${r.kind}|${r.key}`;
let detailReq = 0;
function closeDetail() {
  state.pick = null;
  $("#detail").hidden = true;
  $("#detail").innerHTML = "";
  markPicked();
  if (lastTrigger && document.contains(lastTrigger)) lastTrigger.focus({preventScroll: true});
  lastTrigger = null;
}
function markPicked() { gPts?.selectAll(".pt").classed("picked", function () { return this.__ref && refId(this.__ref) === refId(state.pick); }); }
let lastTrigger = null;        // elemento que abrió la ficha, para devolverle el foco al cerrar

function openDetail(ref, fresh = false) {
  lastTrigger = document.activeElement;
  state.pick = ref; markPicked();
  const box = $("#detail"), n = ++detailReq;
  box.hidden = false;
  box.innerHTML = `<div class="dh"><div><div class="src-org">${esc(ORG[ref.source] || ref.source)}</div><h3>Consultando la fuente…</h3></div><button type="button" class="x" aria-label="Cerrar ficha">×</button></div>`;
  box.scrollTop = 0;
  box.focus({preventScroll: true});   // el cajón ya está a la vista: no hay que mover la página
  fetch(`/api/detail?source=${encodeURIComponent(ref.source)}&kind=${encodeURIComponent(ref.kind)}&key=${encodeURIComponent(ref.key)}${fresh ? "&fresh=true" : ""}`)
    .then(r => r.ok ? r.json() : Promise.reject(new Error(r.status === 404 ? "El registro ya no está en la base (la fuente lo retiró)." : `HTTP ${r.status}`)))
    .then(d => { if (n === detailReq) renderDetail(d); })
    .catch(e => { if (n === detailReq) box.innerHTML = `<div class="dh"><div><div class="src-org">${esc(ORG[ref.source] || ref.source)}</div><h3>No se pudo abrir la ficha</h3></div><button type="button" class="x" aria-label="Cerrar ficha">×</button></div><div class="err">${esc(e.message)}</div>`; });
}
const fmtVal = v => v == null || v === "" ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v);
const isUrl = v => typeof v === "string" && /^https?:\/\//.test(v);
function renderDetail(d) {
  const fields = Object.entries(d.fields || {});
  $("#detail").innerHTML = `
    <div class="dh"><div><div class="src-org">${esc(ORG[d.source] || d.source)}${d.current ? "" : " · ya no vigente en la fuente"}</div>
      <h3>${d.level ? lvlChip(d.level) + " " : ""}${esc(d.title)}</h3>${d.subtitle ? `<div class="sub">${esc(d.subtitle)}</div>` : ""}</div>
      <button type="button" class="x" aria-label="Cerrar ficha">×</button></div>
    ${d.error ? `<div class="err">${esc(d.error)}. Se muestra el dato guardado.</div>` : ""}
    ${d.links?.length ? `<div class="links">${d.links.map(l => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)}</a>`).join("")}</div>` : ""}
    ${iaBox({source: d.source, kind: d.kind, key: d.key})}
    <dl>${(d.facts || []).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(fmtVal(v))}</dd>`).join("")}</dl>
    ${d.text ? `<div class="body">${esc(d.text)}</div>` : ""}
    ${d.fotos?.length ? `<div><div class="gal">${d.fotos.map((f, i) => phHtml(f, i)).join("")}</div><p class="credit">${d.fotos.length} foto${d.fotos.length > 1 ? "s" : ""} del anexo fotográfico del reporte · Crédito: INDECI / COER</p></div>` : ""}
    ${d.mapa ? `<div class="mapa"><button type="button" class="ph" data-mapa><img src="${esc(d.mapa.url)}" alt="Mapa de ubicación" loading="lazy"></button><span>Mapa de ubicación del reporte. ${esc(d.mapa.nota)}</span></div>` : ""}
    ${d.sat ? `<div class="satbox" id="satbox"></div>` : ""}
    ${d.images?.length ? `<div class="imgs">${d.images.map((i, n) => `<button type="button" data-img="${n}" aria-label="Ampliar: ${esc(i.label)}"><img src="${esc(i.url)}" alt="${esc(i.label)}" loading="lazy"><span>${esc(i.label)}${i.fecha ? ` · ${esc(i.fecha)}` : ""}</span></button>`).join("")}</div>` : ""}
    ${d.pdf_text ? `<details><summary>Texto del reporte PDF (ubicación, daños, contexto)</summary><pre>${esc(d.pdf_text)}</pre></details>` : ""}
    ${fields.length ? `<details><summary>Todos los campos (${fields.length})</summary><table>${fields.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${isUrl(v) ? `<a href="${esc(v)}" target="_blank" rel="noopener">${esc(v)}</a>` : esc(fmtVal(v))}</td></tr>`).join("")}</table></details>` : ""}
    <div class="meta"><span>Recibido ${when(d.first_seen)}</span>${d.fetched_at ? `<span>Fuente consultada ${when(d.fetched_at)}</span>` : ""}<button type="button" data-fresh>Volver a consultar la fuente</button><span class="mono">${esc(d.key)}</span></div>`;
  $("#detail [data-fresh]").onclick = () => openDetail({source: d.source, kind: d.kind, key: d.key}, true);
  $("#detail .imgs")?.addEventListener("click", e => {
    const b = e.target.closest("[data-img]"); if (!b) return;
    openLightbox(d.images.map(i => ({url: i.url, caption: i.label, fecha: i.fecha, credit: i.credit || `Imagen: ${ORG[d.source] || d.source}`})), +b.dataset.img);
  });
  if (d.sat) loadSat({...d.sat, base: d.sat.date, layer: "auto"});
  const credit = "Foto: INDECI / COER — anexo fotográfico del reporte";
  $("#detail .gal")?.addEventListener("click", e => { const b = e.target.closest(".ph"); if (b) openLightbox(d.fotos.map(f => ({...f, credit})), +b.dataset.i); });
  $("#detail [data-mapa]")?.addEventListener("click", () => openLightbox([{...d.mapa, caption: "Mapa de ubicación del reporte", credit: d.mapa.nota}]));
}
function openIndeciGroup(prov) {
  const items = V.indeci.filter(i => i.prov === prov), p = V.geo.provs.features.find(f => f.properties.id === prov)?.properties;
  state.pick = null; markPicked();
  const box = $("#detail"); box.hidden = false; box.scrollTop = 0; box.focus({preventScroll: true});
  box.innerHTML = `<div class="dh"><div><div class="src-org">${ORG.indeci} · últimas ${V.indeciWindowH} h</div><h3>${items.length} reporte${items.length > 1 ? "s" : ""} en ${esc(title(p?.n))}</h3><div class="sub">${esc(title(p?.d))} · elija uno para ver la ficha completa</div></div><button type="button" class="x" aria-label="Cerrar ficha">×</button></div>
    <ul class="plist">${items.map(i => `<li><button type="button" data-key="${esc(i._key)}"><span class="t">${esc(title(i.evento || i.titulo))} — ${esc(title(i.distrito))}</span><span class="s">${esc(title(i.tipo))} N° ${esc(i.num)} · ocurrió ${i.ocurrencia ? fmtWhen(i.ocurrencia, i.ocurrencia_hora) : "sin dato"} · reportado ${indeciRep(i)}${i.seguimiento ? " · seguimiento" : ""}</span></button></li>`).join("")}</ul>`;
  box.querySelectorAll(".plist button").forEach(b => b.onclick = () => openDetail({source: "indeci", kind: "item", key: b.dataset.key}));
}
$("#detail").addEventListener("click", e => { if (e.target.closest(".x")) closeDetail(); });
addEventListener("keydown", e => { if (e.key === "Escape" && state.pick !== undefined && !$("#detail").hidden) closeDetail(); });

function renderPoints() {
  gPts.selectAll("*").remove();
  const L = state.layers;
  const on = (sel, html, ref) => sel.on("mousemove", (e, d) => showTip(e, html(d) + `<br><span style="opacity:.7">Clic para ver la ficha completa</span>`)).on("mouseleave", hideTip)
    .each(function (d) { this.__ref = ref(d); })
    .on("click", (e, d) => { e.stopPropagation(); hideTip(); const r = ref(d); r.group ? openIndeciGroup(r.group) : openDetail(r); });
  const layer = (data, tag, ll) => gPts.append("g").selectAll(tag).data(data).join(tag).attr("class", "pt").each(function (d) { this.__ll = ll(d); });
  if (L.focos) layer(V.focos, "circle", d => [d[0], d[1]]).attr("r", 1.6).attr("fill", css("--fuego")).attr("opacity", .45)
    .attr("stroke", "transparent").attr("stroke-width", 5)
    .call(on, d => `<b>Foco de calor</b><br>${title(d[3])} · ${title(d[2])}` + tipWhen(d[6] ? fmtWhen(...d[6].split(" ")) : "—", "detección satelital"), d => ({source: "serfor", kind: "foco", key: String(d[4])}));
  // Contorno oscuro: el color de la condición de tránsito (verde/amarillo/rojo, como el visor oficial) se confunde con
  // los niveles de aviso del mapa base si no lleva borde.
  if (L.vias) layer(V.vias.filter(v => v.lon != null), "path", d => [d.lon, d.lat]).attr("d", d3.symbol(d3.symbolDiamond, d => d.transito_cod === "03" ? 110 : 64))
    .attr("fill", d => viaColor(d.transito_cod)).attr("stroke", css("--ink")).attr("stroke-width", 1.4)
    .call(on, d => `<b>${esc(d.transito)}</b><br>${esc(cap(d.tipo))}<br>${esc(d.ruta)} · ${esc(d.tramo)} · ${esc(d.sector)}<br><span style="opacity:.7">km ${esc(d.km_ini)}${d.puente ? " · puente" : ""}</span>` + tipWhen(d.fecha ? fmtWhen(d.fecha) : "—", "desde", d.dias != null ? `${d.dias} días` : "", ""), d => ({source: "provias", kind: "emergencia", key: d._key}));
  if (L.zonas) layer(V.zonas.slice().sort((a, b) => lvlNum(a.nivel) - lvlNum(b.nivel)), "path", d => [d.lon, d.lat])   // nivel 4 encima
    .attr("d", d => d3.symbol(d3.symbolTriangle, lvlNum(d.nivel) >= 4 ? 60 : 38)())
    .attr("fill", d => css(`--n${Math.min(4, Math.max(2, lvlNum(d.nivel)))}`)).attr("stroke", css("--ink")).attr("stroke-width", 1.1)
    .call(on, d => `<b>Zona crítica · ${esc(d.nivel)}</b><br>${esc(d.paraje)} — ${esc(d.distrito)}, ${esc(d.provincia)}<br>${esc(d.peligros_g)}<br><span style="opacity:.7">Expuesto: ${esc(d.elemento)}</span>` + tipWhen(fmtTs(d._first_seen), "en alerta en la Mesa desde"), d => ({source: "ingemmet", kind: "zona_alerta", key: d._key}));
  // 12 meses debajo: anillos finos y tenues, para ver dónde se acumulan; encima los de 30 y 7 días
  if (L.sismos365) layer(recentSismos(365), "circle", d => [d[5], d[4]]).attr("r", d => Math.max(2.5, (d[2] - 2.5) * 3.2))
    .attr("fill", "none").attr("stroke", css("--sismo")).attr("stroke-width", .8).attr("stroke-opacity", .55)
    .call(on, d => `<b>Sismo M${d[2]}</b><br>${esc(d[6])}<br>Prof. ${d[3]} km${d[7] ? " · " + esc(d[7]) : ""}` + tipWhen(fmtWhen(d[0], d[1]), "ocurrió", d[10] && d[10] - Date.parse(`${d[0]}T${d[1]}:00-05:00`) / 1000 < 86400 && fmtTs(d[10]), "recibido"), d => ({source: "igp", kind: "sismo", key: d[8]}));
  if (L.sismos) layer(recentSismos(30), "circle", d => [d[5], d[4]]).attr("r", d => Math.max(2.5, (d[2] - 2.5) * 3.2))
    .attr("fill", "none").attr("stroke", css("--sismo")).attr("stroke-width", 1.6)
    .call(on, d => `<b>Sismo M${d[2]}</b><br>${esc(d[6])}<br>Prof. ${d[3]} km${d[7] ? " · " + esc(d[7]) : ""}` + tipWhen(fmtWhen(d[0], d[1]), "ocurrió", d[10] && d[10] - Date.parse(`${d[0]}T${d[1]}:00-05:00`) / 1000 < 86400 && fmtTs(d[10]), "recibido"), d => ({source: "igp", kind: "sismo", key: d[8]}));
  if (L.sismos7) layer(recentSismos(7), "circle", d => [d[5], d[4]]).attr("r", d => Math.max(2.5, (d[2] - 2.5) * 3.2))
    .attr("fill", css("--sismo")).attr("fill-opacity", .35).attr("stroke", css("--sismo")).attr("stroke-width", 1.6)
    .call(on, d => `<b>Sismo M${d[2]}</b><br>${esc(d[6])}<br>Prof. ${d[3]} km${d[7] ? " · " + esc(d[7]) : ""}` + tipWhen(fmtWhen(d[0], d[1]), "ocurrió", d[10] && d[10] - Date.parse(`${d[0]}T${d[1]}:00-05:00`) / 1000 < 86400 && fmtTs(d[10]), "recibido"), d => ({source: "igp", kind: "sismo", key: d[8]}));
  if (L.alertas) layer(incendios(), "circle", d => [d.lon, d.lat]).attr("r", d => incR(d.n))
    .attr("fill", css("--fuego")).attr("fill-opacity", d => ({Extinguido: 0, Controlado: .35})[d.estado] ?? .85)
    .attr("stroke", d => d.estado === "Extinguido" ? css("--fuego") : css("--ink")).attr("stroke-width", d => d.estado === "Extinguido" ? 1.4 : 1)
    .call(on, d => `<b>Incendio forestal · ${esc(d.estado)}</b><br>${esc(d.lugares.slice(0, 2).join(" · "))}${d.lugares.length > 2 ? ` y ${d.lugares.length - 2} distritos más` : ""} · ${esc(d.dep)}`
      + `<br><b>${nf.format(d.n)}</b> ${d.n > 1 ? "detecciones satelitales" : "detección satelital"} en ${d.dias} día${d.dias > 1 ? "s" : ""}${d.ini && d.ini !== d.fin ? ` (${fmtDay(d.ini)} → ${fmtDay(d.fin)})` : ""}`
      + `<br><span style="opacity:.7">${esc(d.cob)}${d.cod.startsWith("PIF") ? ` · ${esc(d.cod)}` : ""}</span>` + tipWhen(fmtWhen(d.ult.fecha, d.ult.hora), "última alerta SERFOR"), d => ({source: "serfor", kind: "alerta", key: d.ult._key}));
  if (L.hidro) layer(V.hidro, "circle", d => [d.lon, d.lat]).attr("r", 6)
    .attr("fill", d => css("--n" + Math.min(4, lvlNum(d.color_text)))).attr("stroke", css("--hidro")).attr("stroke-width", 2.4)
    .call(on, d => `<b>${esc(d.titulo)}</b><br>${esc(d.color_text)}<br>${title(d.nom_distrito)}, ${title(d.nom_provincia)} · ${title(d.nom_departamento)}` + tipWhen(fmtUtc(d.fecha_hora), "aviso emitido", fmtTs(d._first_seen), "recibido"), d => ({source: "senamhi_hidro", kind: "aviso_estacion", key: d._key}));
  if (L.indeci) layer([...d3.group(V.indeci.filter(i => i.prov), i => i.prov)], "rect", ([p]) => V.provCentroids[p])
    .attr("x", -5).attr("y", -5).attr("width", 10).attr("height", 10).attr("fill", css("--indeci")).attr("stroke", css("--surface")).attr("stroke-width", 1.2)
    .call(on, ([p, items]) => `<b>INDECI · ${items.length} reporte${items.length > 1 ? "s" : ""}</b>` + items.slice(0, 5).map(i => `<span class="ti">${esc(title(i.evento))} — ${esc(title(i.distrito))}${i.seguimiento ? ` <span class="tseg">seguimiento</span>` : ""}${indeciWhen(i)}</span>`).join("") + (items.length > 5 ? `<span class="ti">y ${items.length - 5} más</span>` : "") + `<span style="opacity:.7">Ubicado en la provincia (centroide)</span>`, ([p]) => ({group: p, source: "indeci", kind: "group", key: p}));
  if (L.bomberos) layer(V.bomberos.filter(bomLL), "path", bomLL)
    .attr("d", d3.symbol(d3.symbolCross, d => d.estado === "Atendiendo" ? 95 : 48))
    .attr("fill", d => bomColor(d.categoria)).attr("fill-opacity", d => d.ubicacion === "coordenadas" ? 1 : .55)
    .attr("stroke", d => d.estado === "Atendiendo" ? css("--ink") : css("--surface")).attr("stroke-width", d => d.estado === "Atendiendo" ? 1.3 : .7)
    .call(on, d => `<b>${esc(bomCorto(d.categoria))}${d.estado === "Atendiendo" ? " · en atención" : ""}</b><br>${esc(d.detalle || "")}<br>${esc(d.direccion || "")} — ${esc(d.distrito || "")}`
      + `<br><span style="opacity:.7">${(d.unidades || []).length} unidad${(d.unidades || []).length === 1 ? "" : "es"}${d.ubicacion === "distrito" ? " · ubicado en el centro de la provincia" : ""}</span>`
      + tipWhen(fmtWhen(d.fecha, d.hora), "parte CGBVP"), d => ({source: "bomberos", kind: "parte", key: d._key}));
  // Mar: trama sobre las provincias costeras (en el grupo del mapa, bajo los límites; no captura el mouse: el clic sigue
  // eligiendo la provincia, y la información del aviso va en su ventanita y en su ficha)
  gMar.selectAll("*").remove();
  const provF = Object.fromEntries(V.geo.provs.features.map(f => [f.properties.id, f]));
  for (const [tipo, t] of [["viento", "v"], ["oleaje", "o"]]) if (L[tipo]) {
    const pv = marProvs(tipo);
    gMar.append("g").selectAll("path").data(Object.entries(pv).filter(([p]) => provF[p])).join("path").attr("d", ([p]) => path(provF[p]))
      .attr("fill", ([, v]) => `url(#mar-${t}${v.n})`).attr("stroke", css("--ink")).attr("stroke-width", 1)
      .attr("vector-effect", "non-scaling-stroke").attr("pointer-events", "none")
      .classed("out", ([p]) => !!state.region && !p.startsWith(state.region));
  }
  if (L.olas) {
    const k = state.olaH, ps = (V.mar?.puntos || []).filter(p => p.ventanas?.[k]);
    const g = layer(ps, "g", p => [p.lon, p.lat]);
    g.append("circle").attr("r", p => 4 + p.ventanas[k].ola_max_m * 2.2).attr("fill", css("--hidro")).attr("fill-opacity", .55).attr("stroke", css("--ink")).attr("stroke-width", .8);
    g.append("text").attr("class", "olatxt").attr("x", p => -(8 + p.ventanas[k].ola_max_m * 2.2)).attr("dy", ".35em").attr("text-anchor", "end").text(p => `${p.ventanas[k].ola_max_m.toFixed(1)} m`);
    g.call(on, p => { const v = p.ventanas[k]; return `<b>${esc(p.nombre)} · ${v.ola_max_m} m</b><br>ola máxima ${OLA_V[k]} (a las ${v.hora_max.slice(11)} del ${v.hora_max.slice(8, 10)}/${v.hora_max.slice(5, 7)})<br>de ${v.direccion}° · período ${v.periodo_s} s${v.mar_de_fondo_max_m != null ? ` · mar de fondo ${v.mar_de_fondo_max_m} m` : ""}<br><span style="opacity:.7">Open-Meteo Marine · modelo referencial, no es la DHN</span>`; },
      p => ({source: "olas_openmeteo", kind: "punto", key: p._key}));
  }
  if (L.obras) layer([...(V.obras || [])].sort((a, b) => (a.estado === "Por Ejecutar") - (b.estado === "Por Ejecutar")).reverse(), "path", o => [o.lon, o.lat])
    .attr("d", d3.symbol().type(o => o.tipo === "barrera" ? d3.symbolStar : d3.symbolCircle).size(o => o.tipo === "barrera" ? 120 : o.nivel_peligro === "MUY ALTO" ? 46 : 30))
    .attr("fill", o => o.estado === "Por Ejecutar" ? css("--surface") : obraColor(o.estado)).attr("stroke", o => o.estado === "Por Ejecutar" ? css("--ink-3") : css("--ink"))
    .attr("stroke-width", o => o.estado === "Por Ejecutar" ? 1.3 : .8)
    .call(on, o => `<b>${o.tipo === "barrera" ? "Barrera contra huaicos" : "Limpieza de cauce"} · ${esc(o.rio)}</b><br>${esc(o.sector)} — ${esc(title(o.dist))}`
      + `<br>${obraNombre(o.estado)} · avance ${o.avance_km_pct ?? 0} %${o.tipo === "barrera" ? " de la barrera" : " del tramo"}${o.fin ? ` · fin ${fmtDM(o.fin)}` : ""}`
      + `<br><span style="opacity:.7">peligro ${esc((o.nivel_peligro || "").toLowerCase())} · ${esc(o.responsable || "")}</span>` + tipWhen(o.fec_avance ? fmtDay(o.fec_avance) : "—", "último avance ANA"),
      o => ({source: "ana_obras", kind: "obra", key: o._key}));
  if (L.recursos) {
    layer(recMaqProv(), "path", d => V.provCentroids[d.prov]).attr("d", d3.symbol(d3.symbolTriangle, d => 40 + Math.sqrt(d.n) * 22))
      .attr("fill", recColor("maquinaria")).attr("fill-opacity", .8).attr("stroke", css("--ink")).attr("stroke-width", .9)
      .call(on, d => `<b>Maquinaria declarada · ${nf.format(d.n)} unidades</b><br>${esc(provName(d.prov))}`
        + d.items.slice(0, 4).map(m => `<span class="ti">${esc(m.entidad)}${m.nivel === "distrito" ? ` — ${esc(title(m.dist))}` : ""}: ${nf.format(m.cantidad)}</span>`).join("")
        + (d.items.length > 4 ? `<span class="ti">y ${d.items.length - 4} registros más</span>` : "") + `<span style="opacity:.7">Ubicado en el centro de la provincia</span>`,
        d => ({source: "indeci_recursos", kind: "recurso", key: d.items[0]._key}));
    layer(V.recursos?.puntos || [], "path", r => [r.lon, r.lat]).attr("d", d3.symbol().type(r => recSym(r.tipo)).size(r => r.tipo === "albergue" && r.aforo >= 500 ? 95 : 52))
      .attr("fill", r => recColor(r.tipo)).attr("stroke", css("--ink")).attr("stroke-width", 1)
      .call(on, r => `<b>${esc(r.etiqueta)} · ${esc(title(r.nombre))}</b><br>${esc(title(r.dist || ""))}${r.dist ? ", " : ""}${esc(provName(r.prov))}`
        + (r.aforo ? `<br>aforo ${nf.format(r.aforo)} personas${r.carpas ? ` · ${r.carpas} carpas` : ""}` : "") + `<br><span style="opacity:.7">${esc(r.entidad)} · declarado ante el FEN</span>`,
        r => ({source: "indeci_recursos", kind: "recurso", key: r._key}));
  }
  if (L.reportes) layer(REP.lista.filter(r => r.lat != null && r.estado !== "descartado" && (!state.region || r.reg === state.region)), "path", r => [r.lon, r.lat])
    .attr("d", d3.symbol(d3.symbolSquare, 70)).attr("fill", r => r.estado === "verificado" ? css("--ok") : css("--brand")).attr("stroke", css("--ink")).attr("stroke-width", 1.2)
    .call(on, r => `<b>Reporte de corresponsal · ${esc(r.tipo_nombre)}</b> · ${r.estado === "verificado" ? "verificado" : "sin verificar"}<br>${esc((r.descripcion || "").slice(0, 140))}<br><span style="opacity:.7">${esc(r.lugar || r.departamento || "")}</span>` + tipWhen(when(r._first_seen), "recibido"),
      r => ({source: "corresponsales", kind: "reporte", key: r._key}));
  dibujarRios(L.rios); dibujarCarreteras(L.carreteras); _detalleAntes = ciudadesDetalle();
  if (L.temperatura) {
    const m = state.tempModo, g = layer(tempPuntos(), "g", p => [p.lon, p.lat]);
    g.append("circle").attr("r", 6).attr("fill", p => tempColor(p[m])).attr("stroke", css("--ink")).attr("stroke-width", 1);
    g.append("text").attr("class", "temptxt").attr("x", 0).attr("dy", "1.55em").attr("text-anchor", "middle").text(p => `${p[m]}°`);   // debajo: el nombre de la ciudad va a la derecha
    g.call(on, p => `<b>${esc(title(p.ciudad))}</b> · ${esc(title(p.departamento))}<br>${esc(p.dia)}: máx. <b>${p.tmax} °C</b> · mín. <b>${p.tmin} °C</b><br><span style="opacity:.75">${esc(p.descripcion || "")}</span>`,
      p => ({source: "senamhi_pronostico", kind: "ciudad", key: `${p.ciudad}|${p.departamento}`}));
  }
  // Encima de todo y sin capturar el mouse: orientan pero no tapan los clics sobre los puntos
  if (L.ciudades) {
    const lista = ciudadesMapa().filter(d => d.t < 3 || (ciudadesDetalle() && (!state.region || d.reg === state.region)));
    const c = gPts.append("g").attr("class", "ciudades").selectAll("g").data(lista).join("g").attr("class", d => `pt ciudad t${d.t}`).each(function (d) { this.__ll = [d.lon, d.lat]; this.__d = d; });
    c.append("circle").attr("r", d => d.t === 3 ? 2.4 : 3.4);
    c.append("text").attr("x", d => d.izq ? -6 : 6).attr("dy", ".35em").attr("text-anchor", d => d.izq ? "end" : "start").text(d => d.n);
    c.filter(d => d.key).select("circle").call(on, d => `<b>${esc(d.n)}</b> · ${esc(d.dpto)}<br><span style="opacity:.75">Ciudad del pronóstico de SENAMHI</span>`,
      d => ({source: "senamhi_pronostico", kind: "ciudad", key: d.key}));
  }
  placePoints(); rotularCiudades(); markPicked();
}
function renderRail() {
  let html = "", provHtml = "";
  if (state.mode === "avisos") {
    const vals = Object.values(V.levelsByDay[state.day] || {});
    const act = V.avisos.filter(a => a.dias.includes(state.day));
    html += `<div><p class="eyebrow">${state.day ? fmtDay(state.day) : "Sin avisos"}</p><h3>Provincias por nivel</h3><div class="counts">${[4,3,2].map(n => `<div><b class="num">${vals.filter(c => c.m === n).length}</b><small>${lvlChip(n)} ${["","","amarillo","naranja","rojo"][n]}</small></div>`).join("")}</div></div>`;
    html += `<div><h3>Avisos que cubren el día</h3><ul class="alist">${act.map(a => `<li class="clk" data-aviso="${esc(a.key)}" tabindex="0" role="button">${lvlChip(lvlNum(a.nivel))}<span class="t">#${a.nro} ${esc(title(a.titulo))}</span><span class="s">${fmtDay(a.inicio)} → ${fmtDay(a.fin)} · ${a.estado}</span></li>`).join("") || "<li class='muted'>Ninguno</li>"}</ul></div>`;
  } else {
    const top = Object.entries(V.uv).sort((a, b) => b[1].v[state.uvDay] - a[1].v[state.uvDay]).slice(0, 8);
    const pn = Object.fromEntries(V.geo.provs.features.map(f => [f.properties.id, f.properties]));
    html += `<div><p class="eyebrow">${uvDays[state.uvDay] ? fmtDay(uvDays[state.uvDay]) : ""} · pronóstico</p><h3>Índice UV más alto</h3><ul class="alist">${top.map(([c, u]) => `<li><span class="lvl" style="background:${uvColor(u.v[state.uvDay])};color:${u.v[state.uvDay] >= 8 ? "#fff" : "#15140F"}">${u.v[state.uvDay]}</span><span class="t">${title(pn[c]?.n)} <span class="muted mono">${c}</span></span><span class="s">${title(pn[c]?.d)} · pico ${u.h[state.uvDay]}</span></li>`).join("")}</ul></div>`;
  }
  const sel = state.sel && V.geo.provs.features.find(f => f.properties.id === state.sel);

  if (sel) {
    const p = sel.properties, c = (V.levelsByDay[state.day] || {})[p.id], u = V.uv[p.id];
    const ind = V.indeci.filter(i => i.prov === p.id), al = V.alertas.filter(a => String(a.ubigeo || "").startsWith(p.id));
    const zn = V.zonas.filter(z => title(z.provincia) === title(p.n));
    provHtml = `<div class="pdetail" id="pdetail"><p class="eyebrow">Provincia seleccionada · <span class="mono">${p.id}</span></p><h3>${title(p.n)}, ${title(p.d)}</h3>
      <div class="fbtns"><a class="zbtn primary" href="/ficha/${p.id}.pdf" download>Descargar ficha PDF</a>
      <a class="zbtn" href="/ficha/${p.id}" target="_blank" rel="noopener">Ver ficha</a>
      <a class="zbtn" href="/ficha/${p.id.slice(0, 2)}.pdf" download title="Ficha de todo el departamento">PDF de ${title(p.d)}</a>
      <a class="zbtn" href="/api/provincia/${p.id}" target="_blank" rel="noopener">JSON</a></div><dl>
      <dt>Aviso ${state.day ? fmtDay(state.day) : ""}</dt><dd>${c ? `${lvlChip(c.m)} ${c.a.map(x => { const a = V.avisos.find(v => v.nro === x[0]); return a ? `<a href="#" data-aviso="${esc(a.key)}">#${x[0]}</a> (N${x[1]})` : `#${x[0]} (N${x[1]})`; }).join(", ")}` : "Sin aviso nivel 2+"}</dd>
      <dt>UV</dt><dd class="num">${u ? u.v.map((v, i) => `${v} (${fmtDay(u.dias[i])})`).join(" · ") : "—"}</dd>
      <dt>INDECI 24 h</dt><dd>${ind.length ? ind.map(i => `${esc(title(i.evento))} — ${esc(title(i.distrito))}`).join("<br>") : "—"}</dd>
      <dt>Incendios</dt><dd>${al.length ? d3.rollups(al, v => v.length, a => a.estado).map(([k, n]) => `${n} ${k.toLowerCase()}`).join(", ") : "—"}</dd>
      <dt>Zonas críticas</dt><dd>${zn.length || "—"}</dd>
      <dt>Obras ANA</dt><dd>${(() => { const os = (V.obras || []).filter(o => o.prov === p.id); return os.length ? d3.rollups(os, v => v.length, o => o.estado).map(([e, k]) => `${k} ${obraNombre(e)}${k > 1 && e !== "En Ejecución" && e !== "Por Ejecutar" ? "s" : ""}`).join(", ") : "—"; })()}</dd>
      <dt>Recursos INDECI</dt><dd>${(() => { const ms = (V.recursos?.maquinaria || []).filter(m => m.prov === p.id), ps = (V.recursos?.puntos || []).filter(r => r.prov === p.id);
        const partes = [ms.length ? `${nf.format(d3.sum(ms, m => m.cantidad))} máquinas` : "", ...d3.rollups(ps, v => v.length, r => r.tipo).map(([t, k]) => recCuenta(t, k))].filter(Boolean);
        return partes.length ? `${partes.join(" · ")} <a href="#recursos-sec" class="muted">ver</a>` : "—"; })()}</dd>
      <dt>Emergencia</dt><dd>${(() => { const e = D.declaratorias?.por_prov?.[p.id]; if (!D.declaratorias) return "—"; if (!e) return "Sin declaratoria por desastre vigente";
        const dias = deeDias(e.hasta); return `${e.n}${e.total ? ` de ${e.total}` : ""} distritos · hasta ${fmtDay(e.hasta)} ${deeChip(dias)} <a href="#" data-dee="${esc(e.decretos[0] || "")}" class="muted">DS ${esc(e.decretos.join(", "))}</a>`; })()}</dd>
      <dt>SINPAD</dt><dd>${(() => { const s = D.sinpad?.por_prov?.[p.id]; if (!s) return "—"; const pk = d3.greatest(Object.entries(s), ([, v]) => v[2]);
        return pk && pk[1][2] ? `peor año ${pk[0]}: ${nf.format(pk[1][2])} damnificados <span class="muted">(corte ${fmtDay(D.sinpad.fecha_corte)})</span>` : "—"; })()}</dd>
      <dt>Mar (DHN)</dt><dd>${(() => { const r = ["oleaje", "viento"].map(t => [t, marProvs(t)[p.id]]).filter(([, v]) => v); return r.length ? r.map(([t, v]) => `${t} ${MAR_PAL[t][v.n]} <a href="#" data-mar="${esc(v.a._key)}">N° ${esc(v.a.numero)}</a>`).join(" · ") : "—"; })()}</dd>
      <dt>Vías (PROVIAS)</dt><dd>${(() => { const vs = V.vias.filter(v => v.prov === p.id); return vs.length ? `${vs.length}: ${[["03", "interrumpida"], ["02", "restringida"], ["04", "por confirmar"]].map(([c, t]) => [vs.filter(v => v.transito_cod === c).length, t]).filter(([n]) => n).map(([n, t]) => `${n} ${t}${n > 1 ? "s" : ""}`).join(", ")}` : "—"; })()}</dd>
      <dt>Daños ${D.danos?.dias || 7} días</dt><dd>${D.danos?.por_prov?.[p.id] ? `${danosChips(D.danos.por_prov[p.id].totales, 4)} <span class="muted">(${D.danos.por_prov[p.id].n_eventos} eventos)</span>` : "—"}</dd></dl>
      ${midisBlock(p.id)}
      <button type="button" class="zbtn" data-dep="${p.id.slice(0, 2)}">Acercar a ${title(p.d)}</button></div>`;
  } else provHtml = `<p class="note">Seleccione una provincia en el mapa para ver lo que dice cada fuente sobre ella y exportar su ficha en PDF.</p>`;
  $("#rail").innerHTML = provHtml + html;
  $("#rail").querySelectorAll("[data-midis]").forEach(n => loadMidis(n.dataset.midis));
  $("#rail").querySelectorAll("[data-mar]").forEach(el => el.addEventListener("click", e => { e.preventDefault(); openDetail({source: "dhn_avisos", kind: "aviso_dhn", key: el.dataset.mar}); }));
  $("#rail").querySelectorAll("[data-aviso]").forEach(el => {
    const go = e => { e.preventDefault(); openDetail({source: "senamhi_avisos", kind: "aviso", key: el.dataset.aviso}); };
    el.addEventListener("click", go);
    el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") go(e); });
  });
  $("#rail").querySelector("button.zbtn")?.addEventListener("click", e => {
    const dep = V.geo.deps.features.find(f => f.properties.id === e.currentTarget.dataset.dep);
    if (dep) zoomToFeature(dep);
  });
}
function renderMapAll() { renderControls(); renderMap(); renderPoints(); renderRail(); }

/* ── secciones ──────────────────────────────────────────────────── */
const chart = (sel, w, h) => { const el = $(sel); el.innerHTML = ""; return d3.select(el).append("svg").attr("viewBox", `0 0 ${w} ${h}`).attr("width", "100%"); };
// Franja "Ahora": lo que hoy pide atención, cada cosa con enlace a su sección. Sigue el filtro de región.
function renderThesis() {
  const R = state.region, en = R ? ` en ${esc(regName(R))}` : "";
  const lv = Object.values(V.levelsByDay[V.snapshot] || {}), n4 = lv.filter(c => c.m === 4).length, n3 = lv.filter(c => c.m === 3).length;
  const rep = V.indeci.filter(i => i.clase === "reporte"), nuevos = rep.filter(i => !i.seguimiento);
  const topEv = d3.rollups(nuevos, v => v.length, i => title(i.evento)).sort((a, b) => b[1] - a[1]).slice(0, 2);
  const vi = V.vias.filter(v => v.transito_cod === "03").length, vc = V.vias.filter(v => v.transito_cod === "04").length;
  const bAct = V.bomberos.filter(b => b.estado === "Atendiendo"), bInc = bAct.filter(b => b.categoria === "Incendio").length;
  const fuegos = incendios().filter(f => f.estado !== "Extinguido"), grande = fuegos[0];
  const desde = Date.parse(V.built.replace(" ", "T") + ":00-05:00") - 864e5;
  const s24 = V.sismos.filter(s => sismoT(s) > desde).sort((a, b) => b[2] - a[2]), sm = s24[0];
  const enf = V.enfen?.ultimo_comunicado || {};
  const tiles = [
    ["#mapa", n4 + n3, `provincias${en} en aviso <b>naranja o rojo</b> hoy`, n4 ? `${n4} en rojo · ${lv.length} con algún aviso` : `${lv.length} con algún aviso SENAMHI`, n4 ? "--n4" : n3 ? "--n3" : null],
    ["#indeci-sec", nuevos.length, `emergencias nuevas${en} reportadas por INDECI en ${V.indeciWindowH} h`, topEv.length ? topEv.map(([e, k]) => `${esc(e)} (${k})`).join(" · ") : "sin reportes nuevos", null],
    ["#vias-sec", vi, `vías <b>interrumpidas</b>${en} (PROVIAS)`, `${vc} por confirmar · ${plural(V.vias.length, "emergencia vial", "emergencias viales")} en total`, vi ? "--n4" : null],
    ["#bom-sec", bAct.length, `emergencias que los bomberos atienden <b>ahora</b>${en}`, V.bomberos.length || !R ? `${bInc} incendio${bInc === 1 ? "" : "s"} · ${nf.format(V.bomberos.length)} partes en 24 h (Lima, Callao y costa sur)` : "la página del CGBVP no cubre esta región", bInc ? "--fuego" : null],
    ["#serfor-sec", fuegos.length, `incendios forestales <b>activos</b>${en} (SERFOR)`, grande ? `el mayor: ${esc(grande.lugares[0])} · ${nf.format(grande.n)} detecciones` : "ninguno sin extinguir", fuegos.length ? "--fuego" : null],
    (() => { const o = Object.entries(marProvs("oleaje", V.snapshot)).filter(([p]) => !R || p.startsWith(R)), n = d3.max(o, ([, v]) => v.n);
      const a = o.find(([, v]) => v.n === n)?.[1].a, w = Object.entries(marProvs("viento", V.snapshot)).filter(([p]) => !R || p.startsWith(R));
      return ["#mar-sec", n ? MAR_PAL.oleaje[n][0].toUpperCase() + MAR_PAL.oleaje[n].slice(1) : "—", `oleaje anómalo hoy${en} (DHN)`,
        (a ? `aviso N° ${esc(a.numero)} hasta el ${fmtDM(a.fin)} · ${o.length} prov. costeras` : "sin aviso de oleaje vigente") + (w.length ? ` · viento: ${MAR_PAL.viento[d3.max(w, ([, v]) => v.n)]}` : ""), n ? `--n${n}` : null]; })(),
    ["#igp-sec", sm ? `M${sm[2]}` : "—", `sismo más fuerte${en} en 24 h`, sm ? `${esc(sm[6])} · ${fmtWhen(sm[0], sm[1])}` : "ninguno registrado", sm && sm[2] >= 5 ? "--sismo" : null],
    ["#enfen-sec", enf.estado ? enf.estado.split(/\s+de\s+/i)[0] : "—", enf.estado ? esc(enf.estado.split(/\s+de\s+/i).slice(1).join(" de ") || enf.estado) + " (ENFEN)" : "estado de ENFEN sin leer", `comunicado N° ${enf.numero ?? "—"}${enf.fecha ? ` (${esc(enf.fecha)})` : ""} · nacional`, /alerta/i.test(enf.estado || "") ? "--n3" : null],
  ];
  $("#thesis").innerHTML = tiles.map(([href, big, txt, sub, col]) => `<a href="${href}"><div class="big num"${col ? ` style="color:var(${col})"` : ""}>${typeof big === "number" ? nf.format(big) : big}</div><p>${txt}</p><p class="sub">${sub}</p></a>`).join("");
  $("#m-snap").textContent = fmtDay(V.snapshot) + " " + V.snapshot.slice(0,4);
}
function renderEnfen() {
  const uc = V.enfen?.ultimo_comunicado || {};
  $("#enfen").innerHTML = `<div class="state"><span>Estado del sistema de alerta</span><strong>${esc(uc.estado || "Sin dato")}</strong><span>Comunicado oficial N° ${uc.numero ?? "—"}-${uc.anio ?? ""} · ${esc(uc.fecha || "")}</span></div>
    <div class="body">${(uc.resumen || "").split(/\n\s*\n/).slice(0, 3).map(p => `<p>${esc(p.replace(/\s*\n\s*/g, " "))}</p>`).join("")}
    ${uc.url ? `<p class="note"><a href="${esc(uc.url)}" target="_blank" rel="noopener">Abrir el PDF del comunicado</a></p>` : ""}</div>`;
}
function renderAvisos() {
  $("#avisos").innerHTML = V.avisos.map(a => `<tr class="clk" data-key="${esc(a.key)}" tabindex="0"><td class="num">${a.nro}</td><td>${lvlChip(lvlNum(a.nivel))}</td><td>${esc(title(a.titulo))}<div class="muted" style="font-size:12px">${a.dptos_texto.length} departamentos · ${a.estado}</div></td><td class="num" style="white-space:nowrap">${fmtDay(a.inicio)} → ${fmtDay(a.fin)}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">Sin avisos vigentes.</td></tr>`;
  const years = Object.keys(V.avisosHist), keys = ["AMARILLO","NARANJA","ROJO"];
  const w = 520, h = 34 * years.length + 34, m = {l: 44, r: 50, t: 6, b: 24};
  const max = d3.max(years, y => d3.sum(keys, k => V.avisosHist[y][k])) || 1;
  const x = d3.scaleLinear([0, max], [m.l, w - m.r]).nice(), y = d3.scaleBand(years, [m.t, h - m.b]).padding(.28);
  const s = chart("#hist", w, h);
  s.append("g").attr("class", "grid").selectAll("line").data(x.ticks(5)).join("line").attr("x1", d => x(d)).attr("x2", d => x(d)).attr("y1", m.t).attr("y2", h - m.b);
  s.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).ticks(5).tickSize(0).tickPadding(8)).select(".domain").remove();
  s.append("g").selectAll("text").data(years).join("text").attr("x", m.l - 8).attr("y", d => y(d) + y.bandwidth() / 2).attr("dy", ".35em").attr("text-anchor", "end").text(d => d);
  const col = {AMARILLO: css("--n2"), NARANJA: css("--n3"), ROJO: css("--n4")};
  s.append("g").selectAll("g").data(d3.stack().keys(keys)(years.map(yy => ({year: yy, ...V.avisosHist[yy]})))).join("g").attr("fill", d => col[d.key]).selectAll("rect").data(d => d).join("rect")
    .attr("x", d => x(d[0])).attr("width", d => x(d[1]) - x(d[0])).attr("y", d => y(d.data.year)).attr("height", y.bandwidth());
  s.append("g").selectAll("text").data(years).join("text").attr("class", "num").attr("x", d => x(d3.sum(keys, k => V.avisosHist[d][k])) + 6).attr("y", d => y(d) + y.bandwidth() / 2).attr("dy", ".35em").text(d => d3.sum(keys, k => V.avisosHist[d][k]));
  $("#hist-note").textContent = `Serie completa desde ${years[0] || "—"}: ${years.reduce((a, yy) => a + V.avisosHist[yy].ROJO, 0)} avisos rojos.`;
}
const kinds = {reporte: "Reportes", boletin_aviso_meteorologico: "Boletín aviso met.", boletin_sismico: "Boletín sísmico", boletin_aviso_corto_plazo: "Aviso corto plazo", boletin_monitoreo_peligros: "Monitoreo", otro: "Otros"};
let indKind = "reporte";
function renderIndeci() {
  const present = Object.keys(kinds).filter(k => V.indeci.some(i => i.clase === k));
  seg($("#ind-filters"), [["all", `Todo ${V.indeci.length}`], ...present.map(k => [k, `${kinds[k]} ${V.indeci.filter(i => i.clase === k).length}`])], indKind, v => { indKind = v; renderIndeci(); });
  const rows = V.indeci.filter(i => indKind === "all" || i.clase === indKind);
  $("#feed").innerHTML = rows.map(i => {
    const hr = new Date(i.pub).toLocaleTimeString("es-PE", {hour: "2-digit", minute: "2-digit", timeZone: "America/Lima"});
    const isNew = i._first_seen && Date.now() / 1000 - i._first_seen < 1800;
    if (i.clase === "reporte") return `<li><span class="hr">${hr}</span><div><div class="kind">${esc(title(i.tipo))} N° ${esc(i.num)}${i.seq ? ` · reporte ${esc(i.seq)}` : ""}${i.fotos ? `<span class="fcount">${i.fotos} foto${i.fotos > 1 ? "s" : ""}</span>` : ""}${isNew ? ` <span class="new">nuevo</span>` : ""}${i.seguimiento ? `<span class="segchip">Seguimiento · ocurrió ${esc(fmtDay(i.ocurrencia))}</span>` : ""}</div><div class="ev"><a href="${esc(i.link)}" target="_blank" rel="noopener">${esc(title(i.evento))}</a>${danosChips(i.danos)}</div><div class="loc">${esc(title(i.distrito))}${i.provincia ? ", " + esc(title(i.provincia)) : ""} · ${esc(title(i.dpto))}${i.prov ? ` <span class="mono muted">${i.prov}</span>` : ""}</div></div></li>`;
    return `<li><span class="hr">${hr}</span><div><div class="kind">${esc(kinds[i.clase] || i.clase)}${isNew ? ` <span class="new">nuevo</span>` : ""}</div><div class="loc"><a href="${esc(i.link)}" target="_blank" rel="noopener">${esc(i.titulo)}</a></div></div></li>`;
  }).join("") || `<li class="muted">Sin ítems en la ventana.</li>`;
  const rep = V.indeci.filter(i => i.clase === "reporte");
  const ev = d3.rollups(rep, v => v.length, i => title(i.evento).replace(/ De Magnitud.*/, "")).sort((a, b) => b[1] - a[1]);
  const w = 520, bh = 24, h = Math.max(ev.length * bh + 10, 30), m = {l: 230, r: 34};
  const x = d3.scaleLinear([0, d3.max(ev, d => d[1]) || 1], [m.l, w - m.r]);
  const g = chart("#ind-chart", w, h).selectAll("g").data(ev).join("g").attr("transform", (d, i) => `translate(0,${i * bh + 4})`);
  g.append("text").attr("x", m.l - 8).attr("y", bh / 2).attr("dy", ".35em").attr("text-anchor", "end").text(d => d[0]);
  g.append("rect").attr("x", m.l).attr("y", 4).attr("height", bh - 10).attr("width", d => x(d[1]) - m.l).attr("fill", css("--ink"));
  g.append("text").attr("class", "num").attr("x", d => x(d[1]) + 6).attr("y", bh / 2).attr("dy", ".35em").text(d => d[1]);
  $("#ind-note").textContent = `${rep.length} reportes en las últimas ${V.indeciWindowH} h. ${V.indeciUnlocated} sin provincia reconocida. Las cifras de daños están en el PDF de cada reporte.`;
}
function renderSismos() {
  const w = 560, h = 300, m = {l: 38, r: 14, t: 12, b: 28}, yr = V.snapshot.slice(0, 4);
  const x = d3.scaleTime([new Date(`${yr}-01-01T00:00`), new Date(V.snapshot + "T23:59")], [m.l, w - m.r]);
  const y = d3.scaleLinear([2.5, 7.5], [h - m.b, m.t]);
  const s = chart("#sismo-chart", w, h);
  s.append("g").attr("class", "grid").selectAll("line").data(y.ticks(5)).join("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", d => y(d)).attr("y2", d => y(d));
  s.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).ticks(d3.timeMonth.every(1)).tickFormat(d => MON[d.getMonth()]).tickSize(0).tickPadding(8)).select(".domain").remove();
  s.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(0).tickPadding(6).tickFormat(d => "M" + d)).select(".domain").remove();
  s.append("g").selectAll("circle").data(sismosAnio()).join("circle").attr("cx", d => x(new Date(d[0] + "T" + d[1]))).attr("cy", d => y(d[2]))
    .attr("r", d => Math.max(1.6, (d[2] - 2.5) * 1.8)).attr("fill", css("--sismo")).attr("fill-opacity", .28).attr("stroke", css("--sismo")).attr("stroke-width", .8)
    .style("cursor", "pointer")
    .on("mousemove", (e, d) => showTip(e, `<b>M${d[2]}</b> · ${fmtDay(d[0])} ${d[1]}<br>${esc(d[6])}<br><span style="opacity:.7">Clic para ver la ficha completa</span>`)).on("mouseleave", hideTip)
    .on("click", (e, d) => { hideTip(); openDetail({source: "igp", kind: "sismo", key: d[8]}); });
  // las etiquetas M6+ quedan encima de los puntos: que no se coman el clic
  s.append("g").style("pointer-events", "none").selectAll("text").data(sismosAnio().filter(d => d[2] >= 6)).join("text").attr("x", d => x(new Date(d[0] + "T" + d[1]))).attr("y", d => y(d[2]) - 12).attr("text-anchor", "middle").style("font-weight", 600).text(d => "M" + d[2]);
  renderSismosLista();
}
// Lista de sismos del año: todos por defecto (los más recientes primero, de a POR_PAGINA), con filtro por magnitud.
const SISMOS_MIN = [["0", "Todos"], ["4", "M4 o más"], ["5", "M5 o más"], ["6", "M6 o más"]];
let sismosMin = "0", sismosTope = POR_PAGINA;
function renderSismosLista() {
  const todos = sismosAnio();
  seg($("#sismos-f"), SISMOS_MIN.map(([v, l]) => [v, `${l} ${todos.filter(d => d[2] >= +v).length}`]), sismosMin,
    v => { sismosMin = v; sismosTope = POR_PAGINA; renderSismosLista(); });
  const lista = todos.filter(d => d[2] >= +sismosMin).sort((a, b) => (b[0] + b[1]).localeCompare(a[0] + a[1]));
  $("#sismos-t").textContent = sismosMin === "0" ? "Sismos del año" : `Sismos de magnitud ${sismosMin},0 o mayor`;
  $("#sismos").innerHTML = lista.slice(0, sismosTope).map(d => `<tr class="clk" tabindex="0" data-sismo="${esc(d[8])}" title="Ver la ficha completa"><td class="num" style="white-space:nowrap">${fmtDay(d[0])} <span class="muted">${d[1]}</span></td><td class="num">${d[2] >= 5 ? `<b>${d[2]}</b>` : d[2]}</td><td class="num">${d[3]} km</td><td>${esc(d[6])}</td></tr>`).join("")
    || `<tr><td colspan="4" class="muted">Ningún sismo con este filtro${state.region ? " en " + esc(regName(state.region)) : ""}.</td></tr>`;
  const resto = lista.length - sismosTope, mas = $("#sismos-more");
  mas.hidden = resto <= 0;
  mas.textContent = `Ver ${Math.min(POR_PAGINA, resto)} más · ${Math.min(sismosTope, lista.length)} de ${lista.length}`;
  mas.onclick = () => { sismosTope += POR_PAGINA; renderSismosLista(); };
  $("#sismos").querySelectorAll("[data-sismo]").forEach(tr => {
    const go = () => openDetail({source: "igp", kind: "sismo", key: tr.dataset.sismo});
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
  });
}
function renderSerfor() {
  const st = ["Alertado","Confirmado","Controlado","Extinguido"];
  const col = {Alertado: css("--n2"), Confirmado: css("--fuego"), Controlado: css("--hidro"), Extinguido: css("--ink-3")};
  const byDep = d3.rollups(V.alertas, v => Object.fromEntries(st.map(k => [k, v.filter(a => a.estado === k).length])), a => title(a.dep)).sort((a, b) => d3.sum(st, k => b[1][k]) - d3.sum(st, k => a[1][k])).slice(0, 10);
  const w = 520, bh = 24, h = byDep.length * bh + 40, m = {l: 110, r: 36};
  const x = d3.scaleLinear([0, d3.max(byDep, d => d3.sum(st, k => d[1][k])) || 1], [m.l, w - m.r]);
  const s = chart("#alert-chart", w, h);
  byDep.forEach(([dep, c], i) => {
    const g = s.append("g").attr("transform", `translate(0,${i * bh})`);
    g.append("text").attr("x", m.l - 8).attr("y", bh / 2).attr("dy", ".35em").attr("text-anchor", "end").text(dep);
    let acc = 0; st.forEach(k => { if (!c[k]) return; g.append("rect").attr("x", x(acc)).attr("y", 5).attr("height", bh - 10).attr("width", x(acc + c[k]) - x(acc)).attr("fill", col[k]); acc += c[k]; });
    g.append("text").attr("class", "num").attr("x", x(acc) + 6).attr("y", bh / 2).attr("dy", ".35em").text(acc);
  });
  const lg = s.append("g").attr("transform", `translate(${m.l},${byDep.length * bh + 18})`);
  st.filter(k => V.alertas.some(a => a.estado === k)).forEach((k, i) => { lg.append("rect").attr("x", i * 100).attr("y", -8).attr("width", 10).attr("height", 10).attr("fill", col[k]); lg.append("text").attr("x", i * 100 + 15).attr("y", 1).text(`${k} ${V.alertas.filter(a => a.estado === k).length}`); });
  const fd = d3.rollups(V.focos, v => v.length, f => title(f[2])).sort((a, b) => b[1] - a[1]).slice(0, 10);
  const h2 = fd.length * bh + 6, x2 = d3.scaleLinear([0, fd[0]?.[1] || 1], [m.l, w - m.r - 10]);
  const g2 = chart("#focos-chart", w, h2).selectAll("g").data(fd).join("g").attr("transform", (d, i) => `translate(0,${i * bh})`);
  g2.append("text").attr("x", m.l - 8).attr("y", bh / 2).attr("dy", ".35em").attr("text-anchor", "end").text(d => d[0]);
  g2.append("rect").attr("x", m.l).attr("y", 5).attr("height", bh - 10).attr("width", d => x2(d[1]) - m.l).attr("fill", css("--fuego")).attr("fill-opacity", .55);
  g2.append("text").attr("class", "num").attr("x", d => x2(d[1]) + 6).attr("y", bh / 2).attr("dy", ".35em").text(d => nf.format(d[1]));
}
// Emergencias viales (PROVIAS): condición de tránsito con el mismo color que el visor oficial.
const VIA_T = [["03", "Interrumpido"], ["02", "Restringido"], ["04", "Por confirmar"], ["01", "Normal"]];
const viaColor = c => ({"03": css("--n4"), "02": css("--n2"), "04": css("--ink-3"), "01": css("--ok")})[c] || css("--ink-3");
const cap = s => s ? s.charAt(0) + s.slice(1).toLowerCase() : "";
let viaF = "";
// ── Mar (DHN) ─────────────────────────────────────────────────────────────
const fmtDM = iso => iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}` : "";
function renderMar() {
  const M = V.mar || {}, hoy = V.snapshot, zonas = {norte: "Norte (Tumbes–Salaverry)", centro: "Centro (Salaverry–San Juan de Marcona)", sur: "Sur (San Juan de Marcona–Tacna)"};
  const vig = (M.avisos || []).filter(a => a.fin >= hoy), prev = (M.avisos || []).filter(a => a.fin < hoy);
  const chip = (t, n) => n ? `<span class="lvl" style="background:var(--n${n});color:${n === 4 ? "#fff" : "#15140F"}">${esc(MAR_PAL[t][n])}</span>` : `<span class="muted">—</span>`;
  $("#mar-vig").innerHTML = vig.map(a => {
    const dias = Object.keys(a.provs_por_dia || {}).sort();
    const cuerpo = a.tipo === "oleaje"
      ? `<div class="scroll"><table class="mar-t"><thead><tr><th>Tramo</th>${dias.map(d => `<th class="num">${fmtDay(d)}</th>`).join("")}</tr></thead><tbody>${Object.entries(zonas).map(([z, nom]) => {
          const zz = a.zonas?.[z]; return `<tr><td>${esc(nom)}<div class="muted" style="font-size:12px">${esc(zz?.texto || "sin oleaje anómalo")}</div></td>${dias.map(d => `<td class="num">${chip("oleaje", zz?.por_dia?.[d])}</td>`).join("")}</tr>`; }).join("")}</tbody></table></div>`
      : `<p>${chip("viento", a.nivel)}${a.nudos ? ` hasta ${a.nudos} nudos` : ""} · ${(a.provs || []).length} provincias costeras: ${esc([...new Set((a.provs || []).map(p => provName(p)))].join(", "))}</p>`;
    return `<div class="panel mar-av"><div class="mar-h"><div><span class="eyebrow">${a.tipo === "oleaje" ? "Oleaje" : "Viento"} · vigente</span><h3>Aviso especial N° ${esc(a.numero)}</h3><p class="muted">${esc(a.validez)}${a.emitido ? ` · emitido el ${fmtDM(a.emitido)}` : ""}</p></div>
      <div class="fbtns"><button type="button" class="zbtn" data-mar="${esc(a._key)}">Ver aviso completo</button>${a.pdf ? `<a class="zbtn" href="${esc(a.pdf)}" target="_blank" rel="noopener">PDF</a>` : ""}</div></div>${cuerpo}</div>`;
  }).join("") || `<p class="muted">Sin avisos especiales de oleaje ni de viento vigentes${state.region ? ` en ${esc(regName(state.region))}` : ""}.</p>`;
  $("#mar-prev").innerHTML = prev.slice(0, 12).map(a => `<li class="clk" data-mar="${esc(a._key)}" tabindex="0"><span class="t">${a.tipo === "oleaje" ? "Oleaje" : "Viento"} N° ${esc(a.numero)}</span><span class="s">${esc(a.validez)}</span></li>`).join("") || "<li class='muted'>Ninguno en los últimos 30 días</li>";
  // mapas del modelo de la DHN
  const pr = M.pronosticos || [];
  if (!state.marZona || !pr.some(p => p._key === state.marZona)) state.marZona = (pr.find(p => p.nombre.startsWith("Bahía Callao")) || pr[0])?._key;
  const P = pr.find(p => p._key === state.marZona);
  $("#mar-zona").innerHTML = [["zona", "Zonas"], ["bahia", "Bahías"]].map(([t, l]) => `<optgroup label="${l}">${pr.filter(p => p.tipo === t).map(p => `<option value="${esc(p._key)}"${p._key === state.marZona ? " selected" : ""}>${esc(p.nombre)}</option>`).join("")}</optgroup>`).join("");
  seg($("#mar-h"), [["0", "Condición inicial"], ["24", "+24 h"], ["48", "+48 h"], ["72", "+72 h"]], state.marH, v => { state.marH = v; renderMar(); });
  if (P) {
    // el modelo corre a las 00 UTC (19:00 del día anterior en Lima); la imagen de +h vale para esa hora más h
    const g = new Date(P.generado * 1000), ini = Date.UTC(g.getUTCFullYear(), g.getUTCMonth(), g.getUTCDate());
    const valida = new Date(ini + (+state.marH) * 36e5);
    $("#mar-img").innerHTML = `<a href="${esc(P.imagenes[state.marH])}" target="_blank" rel="noopener"><img src="${esc(P.imagenes[state.marH])}" alt="Altura significante de ola, ${esc(P.nombre)}, ${state.marH === "0" ? "condición inicial" : "+" + state.marH + " h"}" loading="lazy"></a>`;
    $("#mar-cap").textContent = `${P.nombre} · ${state.marH === "0" ? "condición inicial" : `pronóstico a ${state.marH} h`}: válido para el ${valida.toLocaleString("es-PE", {timeZone: "America/Lima", weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"})} (hora de Lima). Modelo de olas de la DHN, corrida del ${new Date(ini).toLocaleDateString("es-PE", {timeZone: "UTC", day: "numeric", month: "short"})} 00 UTC. Clic en la imagen para verla en grande.`;
  } else { $("#mar-img").innerHTML = `<p class="muted">Aún no hay mapas del modelo de la DHN.</p>`; $("#mar-cap").textContent = ""; }
  // Open-Meteo, por separado
  const pts = M.puntos || [], mx = d3.max(pts, p => d3.max(p.ventanas || [], v => v?.ola_max_m)) || 1;
  const celda = v => v ? `<td class="num"><span class="olabar" style="--w:${Math.round(v.ola_max_m / mx * 100)}%"></span><b>${v.ola_max_m.toFixed(1)}</b> m <span class="muted" title="dirección de donde viene la ola">${v.direccion}°</span></td>` : `<td class="num muted">—</td>`;
  $("#mar-om").innerHTML = pts.map(p => `<tr class="clk${state.olaH !== null ? "" : ""}" data-key="${esc(p._key)}" tabindex="0"><td>${esc(p.nombre)}<div class="muted" style="font-size:12px">${esc(provName(p.prov))}</div></td>${(p.ventanas || []).map(celda).join("")}</tr>`).join("")
    || `<tr><td colspan="4" class="muted">Sin puntos de costa${state.region ? ` en ${esc(regName(state.region))}` : ""}.</td></tr>`;
}
$("#mar-zona").addEventListener("change", e => { state.marZona = e.target.value; renderMar(); });
["#mar-vig", "#mar-prev"].forEach(id => $(id).addEventListener("click", e => { const b = e.target.closest("[data-mar]"); if (b) { e.preventDefault(); openDetail({source: "dhn_avisos", kind: "aviso_dhn", key: b.dataset.mar}); } }));
$("#mar-om").addEventListener("click", e => { const tr = e.target.closest("tr[data-key]"); if (tr) openDetail({source: "olas_openmeteo", kind: "punto", key: tr.dataset.key}); });
// ── Obras de prevención (ANA) ────────────────────────────────────────────
let obraF = "";
function renderObras() {
  const os = V.obras || [], n = f => os.filter(f).length, sol = v => `S/ ${nf.format(Math.round((v || 0) / 1e6))} millones`;
  const sinRep = o => o.estado === "En Ejecución" && o.dias_sin_reporte != null && o.dias_sin_reporte >= 7;
  $("#obras-cnt").innerHTML = [[n(o => o.estado === "En Ejecución"), "en ejecución", "--sismo"], [n(o => o.estado === "Vencida"), "con el plazo vencido", "--n4"],
    [n(o => o.estado === "Por Ejecutar"), "por ejecutar", "--ink-3"], [n(o => o.estado === "Concluida"), "concluidas", "--ok"],
    [n(sinRep), "en ejecución sin reportar avance hace 7 días o más", "--n3"], [n(o => o.tipo === "barrera"), "barreras contra huaicos", "--ink"],
    [sol(d3.sum(os, o => o.costo)), "de presupuesto", "--ink"]]
    .map(([k, t, v]) => `<div><div class="big num" style="color:var(${v})">${typeof k === "number" ? nf.format(k) : k}</div><p>${esc(t)}</p></div>`).join("");
  const filtros = [["", `Todas (${os.length})`], ["En Ejecución", `En ejecución (${n(o => o.estado === "En Ejecución")})`], ["Vencida", `Vencidas (${n(o => o.estado === "Vencida")})`],
    ["sinrep", `Sin reporte ≥ 7 días (${n(sinRep)})`], ["Por Ejecutar", `Por ejecutar (${n(o => o.estado === "Por Ejecutar")})`], ["Concluida", `Concluidas (${n(o => o.estado === "Concluida")})`],
    ["barrera", `Barreras (${n(o => o.tipo === "barrera")})`]];
  seg($("#obras-f"), filtros, obraF, v => { obraF = v; renderObras(); });
  const q = ($("#oq").value || "").toLowerCase();
  const orden = {Vencida: 0, "En Ejecución": 1, "Por Ejecutar": 2, Concluida: 3};
  const rows = os.filter(o => (!obraF || (obraF === "sinrep" ? sinRep(o) : obraF === "barrera" ? o.tipo === "barrera" : o.estado === obraF))
      && (!q || [o.rio, o.sector, o.dist, o.responsable, provName(o.prov)].join(" ").toLowerCase().includes(q)))
    .sort((a, b) => (orden[a.estado] ?? 9) - (orden[b.estado] ?? 9) || (a.dias_para_fin ?? 9e9) - (b.dias_para_fin ?? 9e9));
  const barra = v => `<span class="obar"><i style="width:${Math.min(100, v || 0)}%"></i></span>`;
  $("#obras").innerHTML = rows.slice(0, 300).map(o => `<tr class="clk" data-key="${esc(o._key)}" tabindex="0">
    <td><b>${esc(o.rio)}</b> · ${esc(o.sector)}${o.tipo === "barrera" ? ' <span class="muted">· barrera ★</span>' : ""}<div class="muted" style="font-size:12px">${esc(title(o.dist))}, ${esc(provName(o.prov) || "")} · peligro ${esc((o.nivel_peligro || "").toLowerCase())}</div></td>
    <td><span class="tr" style="--c:${obraColor(o.estado)}">${obraNombre(o.estado)}</span></td>
    <td class="num" style="white-space:nowrap">${barra(o.avance_km_pct)} ${o.avance_km_pct ?? 0} %${o.avance_7d ? `<div class="muted" style="font-size:12px">+${o.avance_7d} en 7 días</div>` : ""}</td>
    <td class="num" style="white-space:nowrap">${o.fin ? fmtDay(o.fin) : "—"}${o.dias_para_fin != null && o.estado !== "Concluida" ? `<div class="muted" style="font-size:12px${o.dias_para_fin < 0 ? ";color:var(--n4)" : ""}">${o.dias_para_fin < 0 ? `vencida hace ${-o.dias_para_fin} d` : `faltan ${o.dias_para_fin} d`}</div>` : ""}</td>
    <td class="num" style="white-space:nowrap">${o.fec_avance ? fmtDay(o.fec_avance) : "—"}${o.dias_sin_reporte != null ? `<div class="muted" style="font-size:12px${sinRep(o) ? ";color:var(--n3)" : ""}">hace ${o.dias_sin_reporte} d</div>` : ""}</td>
    <td class="muted" style="font-size:12px">${esc(o.responsable || "")}</td></tr>`).join("")
    || `<tr><td colspan="6" class="muted">${q || obraF ? "Ninguna obra coincide." : state.region ? `La ANA no tiene obras priorizadas en ${esc(regName(state.region))}.` : "Sin datos."}</td></tr>`;
  $("#obras-note").textContent = `${Math.min(rows.length, 300)} de ${rows.length} obras${rows.length > 300 ? " (afine con el filtro o la búsqueda)" : ""}. El avance es el físico del tramo (km) o de la barrera instalada (m²). "Vencida": pasó su fecha de fin sin llegar al 100 % (solo para las obras con fecha de fin publicada). "+X en 7 días": lo que avanzó desde hace una semana, según los reportes que la Mesa va guardando desde su activación. Clic en una obra para ver su ficha técnica y sus fotos.`;
}
$("#oq").addEventListener("input", () => D && renderObras());
$("#obras").addEventListener("click", e => { const tr = e.target.closest("tr[data-key]"); if (tr) openDetail({source: "ana_obras", kind: "obra", key: tr.dataset.key}); });
// ── Recursos declarados ante el FEN (INDECI · geoservicios) ─────────────────
let recF = "";
function renderRecursos() {
  const R = V.recursos || {puntos: [], maquinaria: []}, ps = R.puntos, ms = R.maquinaria;
  const enLugar = state.region ? ` en ${esc(regName(state.region))}` : "";
  const nT = t => ps.filter(r => r.tipo === t).length, aforo = t => d3.sum(ps.filter(r => r.tipo === t), r => r.aforo || 0);
  $("#rec-src").textContent = `geosinpad.indeci.gob.pe · capas ${R.fechas ? `del ${fmtDay(R.fechas[0])} al ${fmtDay(R.fechas[1])}` : "sin fecha"}`;
  $("#rec-cnt").innerHTML = [[d3.sum(ms, m => m.cantidad), "máquinas y vehículos declarados" + enLugar, "--geo"],
    [nT("albergue"), `albergues${aforo("albergue") ? ` · aforo ${nf.format(aforo("albergue"))}` : ""}`, "--ok"], [nT("zona_segura"), "zonas seguras", "--n1"],
    [nT("helipunto"), "helipuntos", "--sismo"], [nT("agua"), "puntos de agua (EPS, pozos)", "--hidro"], [nT("refugio_ie"), "II.EE. refugio", "--uv3"]]
    .map(([k, t, v]) => `<div><div class="big num" style="color:var(${v})">${nf.format(k)}</div><p>${t}</p></div>`).join("");
  const filas = [...ms.map(m => ({...m, tipo: "maquinaria", etiqueta: "Maquinaria", nombre: m.entidad})), ...ps];
  const filtros = [["", `Todos (${filas.length})`], ...Object.keys(REC_T).map(t => [t, `${recTipo(t)} (${filas.filter(r => r.tipo === t).length})`]).filter(([t]) => filas.some(r => r.tipo === t))];
  seg($("#rec-f"), filtros, recF, v => { recF = v; renderRecursos(); });
  $("#rec-mapa").setAttribute("aria-pressed", String(!!state.layers.recursos));
  const q = ($("#rq").value || "").toLowerCase();
  const lugar = r => r.nivel === "departamento" ? `todo ${title(r.dep || regName(r.reg))}` : [title(r.dist || ""), provName(r.prov) || title(r.prov_nombre || "")].filter(Boolean).join(", ");
  const rows = filas.filter(r => (!recF || r.tipo === recF) && (!q || [r.nombre, r.dist, r.prov_nombre, provName(r.prov), r.entidad, r.etiqueta, ...(r.detalle || []).map(d => d[0])].join(" ").toLowerCase().includes(q)))
    .sort((a, b) => (a.tipo === "maquinaria" ? -1 : 0) - (b.tipo === "maquinaria" ? -1 : 0) || (b.cantidad || 0) - (a.cantidad || 0) || (b.aforo || 0) - (a.aforo || 0));
  $("#rec").innerHTML = rows.slice(0, 300).map(r => `<tr class="clk" data-key="${esc(r._key)}" tabindex="0">
    <td><b>${esc(r.tipo === "maquinaria" ? `Maquinaria de ${r.entidad}` : title(r.nombre))}</b>${r.tipo === "maquinaria" && r.detalle?.length ? `<div class="muted" style="font-size:12px">${r.detalle.slice(0, 4).map(([t, k]) => `${nf.format(k)} ${esc(t)}`).join(" · ")}</div>` : ""}</td>
    <td><span class="recsw" style="--c:var(${(REC_T[r.tipo] || ["--ink-3"])[0]})"></span>${esc(r.tipo === "maquinaria" ? `por ${r.nivel}` : r.etiqueta)}</td>
    <td>${esc(lugar(r))}</td>
    <td class="num">${r.tipo === "maquinaria" ? nf.format(r.cantidad) : r.aforo ? `${nf.format(r.aforo)} pers.${r.carpas ? `<div class="muted" style="font-size:12px">${r.carpas} carpas</div>` : ""}` : "—"}</td>
    <td class="muted" style="font-size:12px">${esc(r.entidad || "")}${r.fecha_capa ? `<div>capa del ${fmtDay(r.fecha_capa)}</div>` : ""}</td></tr>`).join("")
    || `<tr><td colspan="5" class="muted">${q || recF ? "Ningún recurso coincide." : `Los geoservicios del INDECI no registran recursos declarados${enLugar}.`}</td></tr>`;
  $("#rec-note").textContent = `${Math.min(rows.length, 300)} de ${rows.length} registros${rows.length > 300 ? " (afine con el filtro o la búsqueda)" : ""}. La maquinaria viene por distrito (Vivienda), por provincia (gobiernos regionales) o para todo el departamento (MINDEF, MTC, Vivienda), según cómo la publica cada entidad. Albergues, zonas seguras y helipuntos solo existen para Piura y Lima/Callao en estas capas. Clic en una fila para ver el detalle y la capa de origen.`;
}
$("#rq").addEventListener("input", () => D && renderRecursos());
$("#rec").addEventListener("click", e => { const tr = e.target.closest("tr[data-key]"); if (tr) openDetail({source: "indeci_recursos", kind: "recurso", key: tr.dataset.key}); });
$("#rec-mapa").addEventListener("click", () => {
  state.layers.recursos = !state.layers.recursos; renderLayers(); renderPoints(); renderRecursos();
  if (state.layers.recursos) $("#mapa").scrollIntoView({behavior: "smooth", block: "start"});
});
// ── Estados de emergencia (decretos de la PCM en El Peruano, fuente declaratorias) ──
const deeDias = iso => iso ? Math.round((new Date(iso + "T12:00:00") - new Date(new Date().toLocaleDateString("en-CA", {timeZone: "America/Lima"}) + "T12:00:00")) / 864e5) : null;
const deeChip = d => d == null ? "" : `<span class="dee-dias ${d <= (D.declaratorias?.pronto_dias || 15) ? "pronto" : ""}">${d < 0 ? "vencida" : d === 0 ? "vence hoy" : `${d} día${d === 1 ? "" : "s"}`}</span>`;
function renderDeclaratorias() {
  const E = D.declaratorias, R = state.region;
  if (!E) { $("#dee-vig").innerHTML = `<tr><td colspan="4" class="muted">Las declaratorias aún no se han leído.</td></tr>`; $("#dee-cnt").innerHTML = ""; return; }
  const enLugar = R ? ` en ${esc(regName(R))}` : "";
  const vig = E.vigentes.map(v => ({...v, nR: R ? (v.por_reg[R] || 0) : v.n})).filter(v => v.nR);
  const nDist = R ? (E.por_reg[R]?.n || 0) : E.n_distritos;
  const prox = vig.reduce((m, v) => v.dias != null && (m == null || v.dias < m.dias) ? v : m, null);
  $("#dee-src").textContent = `El Peruano · leído hasta el ${E.leido_hasta ? fmtDay(E.leido_hasta) : "—"}`;
  $("#dee-cnt").innerHTML = [[nDist, `distritos con estado de emergencia por desastre${enLugar}`, nDist ? "--n4" : "--ok"],
    [vig.length, vig.length === 1 ? "decreto vigente" : "decretos vigentes", "--ink-2"],
    [prox ? prox.dias : "—", prox ? `días para que venza el DS ${esc(prox.numero)} (${fmtDay(prox.fin)})` : "sin vencimientos", prox && prox.dias <= E.pronto_dias ? "--n4" : "--n2"]]
    .map(([k, t2, c]) => `<div><div class="big num" style="color:var(${c})">${typeof k === "number" ? nf.format(k) : k}</div><p>${t2}</p></div>`).join("");
  $("#dee-vig-t").textContent = `Decretos vigentes${R ? ` en ${regName(R)}` : ""}`;
  $("#dee-vig").innerHTML = vig.map(v => `<tr class="clk" data-dee="${esc(v.numero)}" tabindex="0">
      <td><b>DS ${esc(v.numero)}</b><div class="muted" style="font-size:12px">${v.tipo === "prorroga" ? `prórroga${v.prorroga_de.length ? ` del ${esc(v.prorroga_de.join(", "))}` : ""}` : "declaratoria"} · publicado ${fmtDay(v.publicado)}</div></td>
      <td>${esc(v.causa || "")}${v.fenomeno ? `<div class="muted" style="font-size:12px">${esc(v.fenomeno)}</div>` : ""}</td>
      <td class="num">${nf.format(v.nR)}${R && v.n !== v.nR ? `<div class="muted" style="font-size:12px">de ${nf.format(v.n)}</div>` : ""}</td>
      <td>${v.fin ? fmtDay(v.fin) : "—"} ${deeChip(v.dias)}</td></tr>`).join("")
    || `<tr><td colspan="4" class="muted">Ningún decreto de emergencia por desastre vigente${enLugar}.</td></tr>`;
  $("#dee-vig-note").textContent = `Vencen en ${E.pronto_dias} días o menos: en rojo. El plazo corre desde el día que indica el decreto (o el siguiente a su publicación). Clic en un decreto para ver sus distritos y el texto oficial.`;
  // cobertura: departamentos (nacional) o provincias del departamento elegido
  const filas = R ? Object.entries(E.por_prov).filter(([p]) => p.startsWith(R)).map(([k, x]) => ({k, ...x, nom: provName(k) || k}))
                  : Object.entries(E.por_reg).map(([k, x]) => ({k, ...x, nom: regName(k) || k}));
  filas.sort((a, b) => (a.hasta || "").localeCompare(b.hasta || "") || b.n - a.n);
  $("#dee-cob-t").textContent = R ? `Provincias de ${regName(R)}` : "Departamentos";
  $("#dee-cob-h").innerHTML = `<tr><th>${R ? "Provincia" : "Departamento"}</th><th class="num">Distritos</th><th>Cubierto hasta</th></tr>`;
  $("#dee-cob").innerHTML = filas.map(f => `<tr class="clk" ${R ? `data-prov="${f.k}"` : `data-reg="${f.k}"`} tabindex="0"><td>${esc(f.nom)}</td>
      <td class="num">${nf.format(f.n)}${f.total ? `<span class="muted"> de ${f.total}</span>` : ""}</td><td>${f.hasta ? fmtDay(f.hasta) : "—"} ${deeChip(deeDias(f.hasta))}</td></tr>`).join("")
    || `<tr><td colspan="3" class="muted">Sin cobertura vigente${enLugar}.</td></tr>`;
  $("#dee-cob-note").textContent = R ? "Distritos cubiertos de cada provincia y la fecha en que vence la primera de sus declaratorias. Clic para marcarla en el mapa."
                                     : "Clic en un departamento para filtrar el tablero. «Cubierto hasta» es el primer vencimiento entre sus distritos.";
  const cr = E.crimen.filter(c => !R || (c.regs || []).includes(R));
  $("#dee-cr-t").textContent = `Por criminalidad u orden interno · ${cr.length} decreto${cr.length === 1 ? "" : "s"} en los últimos 60 días${enLugar}`;
  $("#dee-cr").innerHTML = cr.map(c => `<li data-dee="${esc(c.key)}"><b>DS ${esc(c.numero || "")}</b> <span class="muted">· ${fmtDay(c.publicado)}</span><div style="font-size:13px">${esc(c.sumilla || "")}</div></li>`).join("")
    || `<li class="muted">Ninguno${enLugar}.</li>`;
}
["#dee-vig", "#dee-cr", "#rail"].forEach(sel => $(sel).addEventListener("click", e => {
  const el = e.target.closest("[data-dee]"); if (!el || !el.dataset.dee) return;
  e.preventDefault(); openDetail({source: "declaratorias", kind: "decreto", key: el.dataset.dee});
}));
$("#dee-cob").addEventListener("click", e => {
  const tr = e.target.closest("tr"); if (!tr) return;
  if (tr.dataset.reg) setRegion(tr.dataset.reg);
  else if (tr.dataset.prov) { state.sel = tr.dataset.prov; renderAll(); $("#mapa").scrollIntoView({behavior: "smooth", block: "start"}); }
});
// ── Historial SINPAD por año ───────────────────────────────────────────────
const SP_M = [[2, "Damnificados"], [3, "Afectados"], [0, "Emergencias"], [5, "Viv. destruidas"], [4, "Fallecidos"]];
let spM = 2;
function renderSinpad() {
  const S = D.sinpad;
  if (!S) { $("#sp-chart").innerHTML = `<p class="muted">El historial aún no se ha descargado.</p>`; return; }
  const R = state.region, serie = R ? (S.por_reg[R] || {}) : S.nacional, corte = S.fecha_corte, yC = corte.slice(0, 4);
  const nombre = R ? regName(R) : "Perú";
  seg($("#sp-m"), SP_M.map(([i, t]) => [i, t]), spM, v => { spM = +v; renderSinpad(); });
  const anios = Object.keys(serie).sort(), vals = anios.map(y => serie[y][spM]);
  const pico = anios[d3.maxIndex(vals)];
  const etiqueta = SP_M.find(([i]) => i === spM)[1].toLowerCase();
  $("#sp-title").textContent = `${nombre} · ${etiqueta} por año`;
  $("#sp-src").textContent = `Copia del SINPAD · corte ${fmtDay(corte)} ${yC}`;
  const w = 560, h = 260, m = {l: 50, r: 10, t: 22, b: 26};
  const x = d3.scaleBand(anios, [m.l, w - m.r]).padding(.18), y = d3.scaleLinear([0, d3.max(vals) || 1], [h - m.b, m.t]).nice();
  const s = chart("#sp-chart", w, h);
  s.append("defs").append("pattern").attr("id", "sp-hatch").attr("width", 5).attr("height", 5).attr("patternUnits", "userSpaceOnUse").attr("patternTransform", "rotate(45)")
    .append("rect").attr("width", 2.2).attr("height", 5).attr("fill", css("--ink-3"));
  s.append("g").attr("class", "grid").selectAll("line").data(y.ticks(4)).join("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", d => y(d)).attr("y2", d => y(d));
  s.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).tickValues(anios.filter((a, i) => i % 2 === 0 || a === pico || a === yC)).tickFormat(a => "'" + a.slice(2)).tickSize(0).tickPadding(8)).select(".domain").remove();
  s.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickSize(0).tickPadding(6).tickFormat(d3.format("~s"))).select(".domain").remove();
  s.append("g").selectAll("rect").data(anios).join("rect").attr("class", a => `spbar${a === pico ? " pico" : ""}${a === yC ? " parcial" : ""}`)
    .attr("x", a => x(a)).attr("width", x.bandwidth()).attr("y", a => y(serie[a][spM])).attr("height", a => y(0) - y(serie[a][spM]))
    .on("mousemove", (e, a) => { const v = serie[a]; showTip(e, `<b>${a}${a === yC ? ` · hasta el ${fmtDay(corte)}` : ""}</b><br>${nf.format(v[0])} emergencias (${nf.format(v[1])} por lluvias, huaicos, inundaciones u oleaje)<br>${nf.format(v[2])} damnificados · ${nf.format(v[3])} afectados<br>${nf.format(v[4])} fallecidos · ${nf.format(v[5])} viviendas destruidas · ${nf.format(v[6])} afectadas`); })
    .on("mouseleave", hideTip);
  s.append("text").attr("class", "splbl pico").attr("x", x(pico) + x.bandwidth() / 2).attr("y", y(serie[pico][spM]) - 6).attr("text-anchor", "middle").text(d3.format(".3~s")(serie[pico][spM]));
  if (serie[yC]) s.append("text").attr("class", "splbl").attr("x", x(yC) + x.bandwidth() / 2).attr("y", y(serie[yC][spM]) - 6).attr("text-anchor", "middle").text(d3.format(".3~s")(serie[yC][spM]));
  const tot = d3.sum(vals);
  $("#sp-note").innerHTML = `En rojo, el año con más ${esc(etiqueta)} (${pico}: ${nf.format(serie[pico][spM])}). ${yC} va rayado: llega solo hasta el ${fmtDay(corte)}, la fecha de corte de la copia que publica el INDECI; lo posterior está en los reportes de emergencia. Total ${anios[0]}–${yC}: ${nf.format(tot)}.`;
  // tabla: departamentos (vista nacional) o provincias (vista de un departamento), por la métrica elegida en el año pico y en el actual
  const fuente = R ? Object.entries(S.por_prov).filter(([p]) => p.startsWith(R)) : Object.entries(S.por_reg);
  const nom = R ? p => provName(p) || p : r => regName(r) || r;
  const filas = fuente.map(([k, s2]) => ({k, pico: s2[pico]?.[spM] || 0, act: s2[yC]?.[spM] || 0, tot: d3.sum(Object.values(s2), v => v[spM])})).sort((a, b) => b.tot - a.tot).slice(0, 12);
  $("#sp-tab-t").textContent = `${R ? "Provincias" : "Departamentos"} con más ${etiqueta}`;
  $("#sp-thead").innerHTML = `<tr><th>${R ? "Provincia" : "Departamento"}</th><th class="num">${anios[0]}–${yC}</th><th class="num">${pico}</th><th class="num">${yC}*</th></tr>`;
  $("#sp-tab").innerHTML = filas.map(f => `<tr${R ? ` class="clk" data-prov="${f.k}" tabindex="0"` : ` class="clk" data-reg="${f.k}" tabindex="0"`}><td>${esc(nom(f.k))}</td><td class="num"><b>${nf.format(f.tot)}</b></td><td class="num">${nf.format(f.pico)}</td><td class="num">${f.act ? nf.format(f.act) : "—"}</td></tr>`).join("")
    || `<tr><td colspan="4" class="muted">Sin registros.</td></tr>`;
  $("#sp-tab-note").textContent = `* ${yC} hasta el ${fmtDay(corte)}. ${R ? "Clic en una provincia para marcarla en el mapa." : "Clic en un departamento para filtrar el tablero."} Cifras de la ficha EDAN de cada emergencia en el SINPAD; los registros anteriores a ${S.desde} no se muestran por escasos.`;
}
$("#sp-tab").addEventListener("click", e => {
  const tr = e.target.closest("tr"); if (!tr) return;
  if (tr.dataset.reg) setRegion(tr.dataset.reg);
  else if (tr.dataset.prov) { state.sel = tr.dataset.prov; renderAll(); $("#mapa").scrollIntoView({behavior: "smooth", block: "start"}); }
});
// ── Nuestros corresponsales: reportes enviados desde /reportar (ver mesa/reportes.py) ──
async function cargarReportes() {
  try {
    const r = await fetch("/api/reportes", {cache: "no-store"}); if (!r.ok) return;
    const l = await r.json(), firma = l.map(x => x._key + x.estado).join();
    if (firma === REP.firma) return;
    REP.lista = l; REP.firma = firma; renderReportes(); if (D) { renderLayers(); renderPoints(); }
  } catch { /* sin conexión: se reintenta en el próximo sondeo */ }
}
function renderReportes() {
  const L_ = REP.lista.filter(r => !state.region || r.reg === state.region), n = f => L_.filter(f).length;
  const filtros = [["sin_verificar", "Sin verificar", r => r.estado === "sin_verificar"], ["medio", "De medios", r => r.quien === "medio" && r.estado !== "descartado"],
    ["verificado", "Verificados", r => r.estado === "verificado"], ["descartado", "Descartados", r => r.estado === "descartado"]];
  $("#ciud-f").innerHTML = filtros.map(([k, t, f]) => `<button type="button" data-f="${k}" aria-pressed="${REP.filtro === k}">${t} <span class="n">${n(f)}</span></button>`).join("");
  const sel = L_.filter(filtros.find(([k]) => k === REP.filtro)[2]);
  $("#ciud").innerHTML = sel.slice(0, REP.mostrar).map(r => {
    const m = r.medio || {}, p = r.privado || {}, a = r.archivo || {}, rev = r.revision || {};
    const quien = r.quien === "medio" ? `<span class="medio">Medio: ${esc(m.nombre || "—")}${m.canal ? ` · ${esc(m.canal)}` : ""}${m.detalle ? ` · ${esc(m.detalle)}` : ""}</span>` : r.quien === "autoridad" ? `autoridad · ${esc(r.autoridad || "")}` : "ciudadano/a";
    const url = `/api/reportes/${encodeURIComponent(r.id)}/archivo`;
    return `<li class="${r.estado}" data-key="${esc(r._key)}"><div class="ch"><span class="tipo">${esc(r.tipo_nombre)}</span><span class="muted">${ago(r._first_seen)}${a.tipo ? (a.tipo.startsWith("video") ? " · con video" : " · con foto") : ""} · N° ${esc(r.id)}</span></div>
      <p class="t">“${esc(r.descripcion)}”</p>
      ${a.tipo?.startsWith("image/") ? `<div class="media"><a href="${url}" target="_blank" rel="noopener"><img src="${url}" alt="Foto del reporte" loading="lazy"></a></div>` : a.tipo?.startsWith("video/") ? `<div class="media"><video src="${url}" controls preload="none"></video></div>` : ""}
      <p class="who">Reporta <b>${esc(r.nombre)}</b> · ${quien} · nombre al aire: ${r.nombre_al_aire ? "sí" : "no"}</p>
      <p class="s">${esc([r.departamento, r.lugar].filter(Boolean).join(" · ") || "sin lugar")}${r.lat != null ? " · con ubicación del teléfono" : ""} · personas afectadas: ${esc(r.afectados || "—")} · teléfono para verificar: <a class="tel" href="tel:${esc(p.telefono || "")}">${esc(p.telefono || "—")}</a></p>
      ${(r.cruce || []).length ? `<p class="cruce">Cruce automático: ${r.cruce.map(esc).join(" · ")}</p>` : ""}
      ${rev.por ? `<p class="rev">${r.estado === "verificado" ? "Verificado" : r.estado === "descartado" ? "Descartado" : "Revisado"} por ${esc(rev.por)}${rev.nota ? `: ${esc(rev.nota)}` : ""}</p>` : ""}
      <div class="acc">${r.estado !== "verificado" ? `<button type="button" data-acc="verificado">Verificar</button>` : ""}${r.estado !== "descartado" ? `<button type="button" data-acc="descartado">Descartar</button>` : `<button type="button" data-acc="sin_verificar">Reabrir</button>`}
        <button type="button" data-acc="ficha">${r.estado === "verificado" ? "Redactar con IA" : "Ver ficha"}</button></div></li>`;
  }).join("") + verMas(Math.min(REP.mostrar, sel.length), sel.length)
    || `<li class="muted" style="border:0;background:none">${REP.filtro === "sin_verificar" ? "No hay reportes pendientes de verificar." : "Ninguno."}</li>`;
  $("#ciud-note").textContent = `${L_.length} reportes${state.region ? ` en ${regName(state.region)}` : ""}. El teléfono, la ubicación exacta y el nombre (si no aceptó que se mencione) no salen de este tablero.`;
}
$("#ciud-f").addEventListener("click", e => { const b = e.target.closest("[data-f]"); if (b) { REP.filtro = b.dataset.f; REP.mostrar = POR_PAGINA; renderReportes(); } });
$("#ciud").addEventListener("click", async e => {
  if (e.target.closest("[data-mas]")) { REP.mostrar += POR_PAGINA; return renderReportes(); }
  const b = e.target.closest("[data-acc]"), li = e.target.closest("li[data-key]"); if (!b || !li) return;
  if (b.dataset.acc === "ficha") return openDetail({source: "corresponsales", kind: "reporte", key: li.dataset.key});
  const actor = iaFirmado(); if (!actor) return;
  const nota = b.dataset.acc === "verificado" ? prompt("¿Cómo se verificó? (p. ej.: llamé a quien reporta, coincide con INDECI)") : b.dataset.acc === "descartado" ? prompt("Motivo para descartar (opcional):") : "";
  if (nota === null) return;
  try {
    const r = await fetch(`/api/reportes/${encodeURIComponent(li.dataset.key)}/estado`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({estado: b.dataset.acc, actor, nota})});
    if (!r.ok) throw new Error((await r.json()).detail);
    REP.firma = ""; await cargarReportes(); toast(b.dataset.acc === "verificado" ? "Reporte verificado" : b.dataset.acc === "descartado" ? "Reporte descartado" : "Reporte reabierto");
  } catch (err) { toast(`No se pudo guardar: ${err.message}`); }
});
// Enlace público del formulario: el de la versión en línea. Una Mesa instalada comparte la bandeja del servidor.
(async () => {
  let srv = null;
  try { srv = (await (await fetch("/api/relay/estado")).json()).servidor || null; } catch {}
  const url = `${(srv || location.origin).replace(/\/$/, "")}/reportar`; $("#ciud-url").value = url;
  $("#ciud-url-nota").textContent = srv ? "Este enlace es público. Los reportes llegan a la versión en línea y esta Mesa los trae: todas las Mesas ven la misma bandeja y la verificación se comparte."
    : /^(localhost|127\.|\[::1\])/.test(location.hostname) ? "Esta Mesa corre en este equipo: el enlace solo funciona aquí." : "Este enlace es público: no pide la clave del tablero.";
  $("#ciud-copiar").onclick = async () => { try { await navigator.clipboard.writeText(url); toast("Enlace copiado"); } catch { $("#ciud-url").select(); } };
})();
let bomF = "", bomPer = "24";
const bomMin = m => m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min` : `${m} min`;
const bomT0 = b => Date.parse(`${b.fecha}T${b.hora}:00-05:00`);
function bomDur(b) {
  if (b.duracion_min != null) return `duró ${bomMin(b.duracion_min)}`;
  if (b.duracion_max_min != null) return `cerró en menos de ${bomMin(b.duracion_max_min)}`;
  return b.estado === "Atendiendo" && b.fecha ? `hace ${bomMin(Math.max(0, Math.round((Date.now() - bomT0(b)) / 6e4)))}` : "";
}
// 7 / 30 días: se piden aparte (no van en el snapshot) y se guardan unos minutos
const bomHist = {};
async function bomHistorial(dias) {
  const c = bomHist[dias];
  if (c && Date.now() - c.t < 5 * 6e4) return c.data;
  const data = await (await fetch(`/api/bomberos/historial?dias=${dias}`)).json();
  bomHist[dias] = {t: Date.now(), data};
  return data;
}
async function renderBomHist() {
  const dias = +bomPer, h = await bomHistorial(dias);
  if (String(dias) !== bomPer) return;
  const I = Object.fromEntries(h.campos.map((c, i) => [c, i])), R = state.region;
  const ps = h.partes.filter(r => !R || r[I.reg] === R), med = h.medicas.filter(m => !R || m[1] === R);
  const n = c => ps.filter(r => r[I.categoria] === c).length;
  const durs = ps.filter(r => r[I.categoria] === "Incendio" && r[I.duracion_min] != null).map(r => r[I.duracion_min]);
  const esc_ = ps.filter(r => r[I.escalo]).length;
  $("#bomh-cnt").innerHTML = [[ps.length, "partes (sin médicas)", "--ink"], ...BOM_T.filter(([c]) => n(c)).map(([c, v]) => [n(c), bomCorto(c).toLowerCase(), v]),
    [med.length, "emergencias médicas", "--ink-3"], [esc_, "sumaron unidades en el camino", "--n3"],
    [durs.length ? bomMin(Math.round(d3.median(durs))) : "—", `duración típica de un incendio (mediana de ${durs.length})`, "--fuego"]]
    .map(([k, t, v]) => `<div><div class="big num" style="color:var(${v})">${typeof k === "number" ? nf.format(k) : k}</div><p>${esc(t)}</p></div>`).join("");
  // partes por día, apilados por tipo
  const diasLista = d3.timeDays(new Date(h.desde_fecha + "T12:00:00"), d3.timeDay.offset(new Date(V.snapshot + "T12:00:00"), 1)).map(d => d3.timeFormat("%Y-%m-%d")(d));
  const tipos = BOM_T.map(([c]) => c), w = 640, hh = 190, m = {l: 30, r: 8, t: 8, b: 24};
  const rows = diasLista.map(d => Object.fromEntries([["d", d], ...tipos.map(t => [t, ps.filter(r => r[I.fecha] === d && r[I.categoria] === t).length])]));
  const st = d3.stack().keys(tipos)(rows), x = d3.scaleBand(diasLista, [m.l, w - m.r]).padding(.18);
  const y = d3.scaleLinear([0, d3.max(rows, r => d3.sum(tipos, t => r[t])) || 1], [hh - m.b, m.t]).nice();
  const svg = chart("#bomh-dias", w, hh);
  svg.append("g").attr("class", "grid").selectAll("line").data(y.ticks(4)).join("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", d => y(d)).attr("y2", d => y(d));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickSize(0).tickPadding(6)).select(".domain").remove();
  const cada = Math.ceil(diasLista.length / 10);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${hh - m.b})`).call(d3.axisBottom(x).tickValues(diasLista.filter((_, i) => i % cada === 0)).tickFormat(d => fmtDay(d).replace(/^\S+ /, "")).tickSize(0).tickPadding(6)).select(".domain").remove();
  svg.append("g").selectAll("g").data(st).join("g").attr("fill", s => bomColor(s.key)).selectAll("rect").data(s => s).join("rect")
    .attr("x", d => x(d.data.d)).attr("width", x.bandwidth()).attr("y", d => y(d[1])).attr("height", d => y(d[0]) - y(d[1]))
    .append("title").text(function (d) { return `${fmtDay(d.data.d)} · ${bomCorto(this.parentNode.parentNode.__data__.key)}: ${d[1] - d[0]}`; });
  $("#bomh-leg").innerHTML = BOM_T.map(([c]) => `<span><i style="background:${bomColor(c)}"></i>${esc(bomCorto(c))}</span>`).join("");
  // distritos
  const md = d3.rollup(med, v => v.length, m => m[3]);
  const dist = d3.rollups(ps, v => v, r => r[I.distrito] || "Sin distrito").map(([k, v]) => ({k, v})).sort((a, b) => b.v.length - a.v.length).slice(0, 12);
  const cnt = (v, c) => v.filter(r => r[I.categoria] === c).length || "";
  $("#bomh-dist").innerHTML = dist.map(({k, v}) => `<tr><td>${esc(k)}</td><td class="num"><b>${v.length}</b></td><td class="num">${cnt(v, "Incendio")}</td><td class="num">${cnt(v, "Accidente Vehicular")}</td><td class="num">${cnt(v, "Rescate")}</td><td class="num">${cnt(v, "Materiales Peligrosos (Incidente)")}</td><td class="num muted">${md.get(k) || ""}</td></tr>`).join("")
    || `<tr><td colspan="7" class="muted">Sin partes en el periodo.</td></tr>`;
  const fila = (r, val) => `<tr class="clk" data-key="${esc(r[I.key])}" tabindex="0"><td><span class="tr" style="--c:${bomColor(r[I.categoria])}">${esc(bomCorto(r[I.categoria]))}</span><div class="muted" style="font-size:12px">${esc(r[I.detalle] || "")}</div></td><td>${esc(r[I.distrito] || "—")}<div class="muted" style="font-size:12px">${r[I.fecha] ? fmtDay(r[I.fecha]) : ""} ${esc(r[I.hora] || "")}</div></td><td class="num"><b>${val}</b></td></tr>`;
  $("#bomh-dur").innerHTML = ps.filter(r => r[I.duracion_min] != null).sort((a, b) => b[I.duracion_min] - a[I.duracion_min]).slice(0, 8).map(r => fila(r, bomMin(r[I.duracion_min]))).join("")
    || `<tr><td colspan="3" class="muted">Aún no hay partes con duración medida.</td></tr>`;
  $("#bomh-esc").innerHTML = ps.filter(r => r[I.unidades_total]).sort((a, b) => b[I.unidades_total] - a[I.unidades_total]).slice(0, 8).map(r => fila(r, `${r[I.unidades_total]}${r[I.escalo] ? ' <span class="bom-up">↑</span>' : ""}`)).join("")
    || `<tr><td colspan="3" class="muted">Sin datos de unidades.</td></tr>`;
  const corto = h.registro_desde && h.registro_desde > h.desde_fecha;
  $("#bomh-note").textContent = `${dias === 7 ? "Últimos 7 días" : "Últimos 30 días"}${R ? ` · ${regName(R)}` : ""}. `
    + (corto ? `Esta Mesa registra los partes desde el ${fmtDay(h.registro_desde)}: los días anteriores no tienen datos (el CGBVP no publica historial). ` : "")
    + `La duración va desde la llamada hasta que la Mesa vio el parte cerrado (se consulta cada 10 min: hasta 10 min de más); los que ya llegaron cerrados no se cuentan. "Sumaron unidades" son los partes a los que se les agregaron unidades después de que la Mesa los vio. Las médicas solo se cuentan. Clic en un parte para ver su ficha.`;
}
["#bomh-dur", "#bomh-esc"].forEach(id => $(id).addEventListener("click", e => { const tr = e.target.closest("tr[data-key]"); if (tr) openDetail({source: "bomberos", kind: "parte", key: tr.dataset.key}); }));
function renderBomberos() {
  seg($("#bom-per"), [["24", "Últimas 24 horas"], ["7", "7 días"], ["30", "30 días"]], bomPer, v => { bomPer = v; renderBomberos(); });
  $("#bom-24").hidden = bomPer !== "24"; $("#bom-hist").hidden = bomPer === "24";
  if (bomPer !== "24") { renderBomHist().catch(e => { $("#bomh-note").textContent = `No se pudo cargar el historial: ${e.message}`; }); return; }
  const bs = V.bomberos, M = V.bomberosMedicas || {}, n = c => bs.filter(b => b.categoria === c).length;
  const act = bs.filter(b => b.estado === "Atendiendo").length;
  $("#bom-cnt").innerHTML = [[act, "en atención ahora", "--ink"], ...BOM_T.filter(([c]) => n(c)).map(([c, v]) => [n(c), bomCorto(c).toLowerCase(), v])]
    .map(([k, t, v]) => `<div><div class="big num" style="color:var(${v})">${k}</div><p>${esc(t)}</p></div>`).join("")
    + `<div><div class="big num" style="color:var(--ink-3)">${M.total ?? 0}</div><p>emergencias médicas <span class="muted">(solo el número)</span></p></div>`;
  seg($("#bom-f"), [["", `Todas (${bs.length})`], ["act", `En atención (${act})`], ...BOM_T.filter(([c]) => n(c)).map(([c]) => [c, `${bomCorto(c)} (${n(c)})`])], bomF, v => { bomF = v; renderBomberos(); });
  filterBomberos();
}
function filterBomberos() {
  const q = ($("#bq").value || "").toLowerCase();
  const rows = V.bomberos.filter(b => (!bomF || (bomF === "act" ? b.estado === "Atendiendo" : b.categoria === bomF))
    && (!q || [b.categoria, b.detalle, b.direccion, b.distrito, ...(b.unidades || [])].join(" ").toLowerCase().includes(q)));
  $("#bom").innerHTML = rows.map(b => `<tr class="clk" data-key="${esc(b._key)}" tabindex="0">
    <td class="num" style="white-space:nowrap">${esc(b.hora || "")}<div class="muted" style="font-size:12px">${b.fecha ? fmtDay(b.fecha) : ""}</div></td>
    <td><span class="tr" style="--c:${bomColor(b.categoria)}">${esc(bomCorto(b.categoria))}</span><div class="muted" style="font-size:12px">${esc(b.detalle || "")}</div></td>
    <td>${esc(b.direccion || "—")}<div class="muted" style="font-size:12px">${esc(b.distrito || "")}</div></td>
    <td>${b.estado === "Atendiendo" ? "<b>En atención</b>" : esc(b.estado)}<div class="muted" style="font-size:12px">${bomDur(b)}</div></td>
    <td class="muted" style="font-size:12px">${esc((b.unidades || []).join(", "))}${b.escalo ? ` <span class="bom-up" title="Unidades que se sumaron después de que la Mesa vio el parte">+${b.unidades_total - b.unidades_iniciales}</span>` : ""}</td></tr>`).join("")
    || `<tr><td colspan="5" class="muted">${q || bomF ? "Ningún parte coincide." : state.region ? `Sin partes de bomberos en ${esc(regName(state.region))} en las últimas 24 horas. La página del CGBVP cubre Lima, Callao y parte de la costa sur.` : "Sin partes registrados."}</td></tr>`;
  const M = V.bomberosMedicas || {};
  $("#bom-note").textContent = `${rows.length} de ${V.bomberos.length} partes de las últimas 24 horas, sin contar ${M.total ?? 0} emergencias médicas, que se muestran solo como número porque su dirección suele ser la de una vivienda. La página del CGBVP cubre Lima, Callao y parte de la costa sur (Cañete, Chincha, Pisco, Ica), Huacho y algo de Ayacucho; no publica el resto del país. Clic en una fila para ver el parte.`;
}
$("#bq").addEventListener("input", () => D && filterBomberos());
$("#bom").addEventListener("click", e => { const tr = e.target.closest("tr[data-key]"); if (tr) openDetail({source: "bomberos", kind: "parte", key: tr.dataset.key}); });
$("#bom").addEventListener("keydown", e => { const tr = e.target.closest("tr[data-key]"); if (tr && e.key === "Enter") openDetail({source: "bomberos", kind: "parte", key: tr.dataset.key}); });
function renderVias() {
  const vs = V.vias, n = c => vs.filter(v => v.transito_cod === c).length;
  $("#vias-cnt").innerHTML = VIA_T.map(([c, t]) => `<div><div class="big num" style="color:${viaColor(c)}">${n(c)}</div><p>tránsito ${t.toLowerCase()}</p></div>`).join("");
  const ev = D.viasEventos || [];
  $("#vias-ev").hidden = !ev.length;
  $("#vias-ev").textContent = ev.length ? `Evento activo según PROVIAS: ${ev.join(" · ")}` : "";
  const VF = (D.viasFotos || []).filter(f => !state.region || f.reg === state.region).map(f => ({
    url: f.url, caption: f.caption ? (f.caption === f.caption.toUpperCase() ? cap(f.caption) : f.caption) : cap(f.tipo), reg: f.reg, fecha: f.fecha ? fmtDay(f.fecha) : null, shotLabel: "Avance del",
    meta: `${f.transito.replace("Tránsito ", "")} · ${f.ruta} · ${cap(f.sector)}`, ref: {source: "provias", kind: "emergencia", key: f.key},
    credit: "Foto: PROVIAS Nacional — visor de emergencias viales"}));
  $("#vias-strip").innerHTML = VF.map((f, i) => phHtml(f, i, {reg: !state.region})).join("")
    || `<p class="muted" style="margin:0">${state.region ? `Sin fotos de emergencias viales en ${esc(regName(state.region))}.` : "Aún no hay fotos: el colector las va revisando de a 40 emergencias cada 10 min."}</p>`;
  $("#vias-strip").onclick = e => { const b = e.target.closest(".ph"); if (b) openLightbox(VF, +b.dataset.i); };
  $("#vstrip-note").textContent = VF.length ? `${VF.length} fotos de ${new Set(VF.map(f => f.ref.key)).size} emergencias, de lo más reciente a lo más antiguo · hasta 3 por emergencia · la fecha es la del avance que reportó PROVIAS. Crédito: PROVIAS Nacional.` : "";
  seg($("#vias-f"), [["", `Todas (${vs.length})`], ...VIA_T.filter(([c]) => n(c)).map(([c, t]) => [c, `${t} (${n(c)})`])], viaF, v => { viaF = v; renderVias(); });
  filterVias();
}
function filterVias() {
  const q = ($("#vq").value || "").toLowerCase();
  const pn = Object.fromEntries(V.geo.provs.features.map(f => [f.properties.id, f.properties]));
  const orden = {"03": 0, "04": 1, "02": 2, "01": 3};
  const rows = V.vias.filter(v => (!viaF || v.transito_cod === viaF)
      && (!q || [v.ruta, v.tramo, v.sector, v.tipo, pn[v.prov]?.n, pn[v.prov]?.d].join(" ").toLowerCase().includes(q)))
    .sort((a, b) => (orden[a.transito_cod] ?? 9) - (orden[b.transito_cod] ?? 9) || (b.fecha || "").localeCompare(a.fecha || ""));
  $("#vias").innerHTML = rows.map(v => `<tr class="clk" data-key="${esc(v._key)}" tabindex="0"><td><span class="tr" style="--c:${viaColor(v.transito_cod)}">${esc(v.transito.replace("Tránsito ", ""))}</span></td>
    <td>${esc(cap(v.tipo))}${v.puente ? ' <span class="muted">· puente</span>' : ""}</td>
    <td><b>${esc(v.ruta)}</b> · ${esc(v.tramo)}<div class="muted" style="font-size:12px">sector ${esc(v.sector)} · km ${esc(v.km_ini)}</div></td>
    <td>${v.prov ? `${esc(title(pn[v.prov]?.n))} <span class="muted">· ${esc(title(pn[v.prov]?.d))}</span>` : esc(regName(v.reg)) || "—"}</td>
    <td class="num" style="white-space:nowrap">${v.fecha ? fmtDay(v.fecha) : "—"}${v.dias != null ? `<div class="muted" style="font-size:12px">${v.dias} días</div>` : ""}</td></tr>`).join("")
    || `<tr><td colspan="5" class="muted">${q || viaF ? "Ninguna emergencia coincide." : state.region ? `Sin emergencias viales en ${esc(regName(state.region))}.` : "Sin emergencias viales registradas."}</td></tr>`;
  $("#vias-note").textContent = `${rows.length} de ${V.vias.length} emergencias. Clic en una fila para ver su cronología, contratista y fotos. La condición de tránsito es la del mapa de PROVIAS; "por confirmar" aún no fue validada.`;
}
$("#vq").addEventListener("input", () => D && filterVias());
$("#vias").addEventListener("click", e => { const r = e.target.closest("tr[data-key]"); if (r) openDetail({source: "provias", kind: "emergencia", key: r.dataset.key}); });
$("#vias").addEventListener("keydown", e => { const r = e.target.closest("tr[data-key]"); if (r && e.key === "Enter") openDetail({source: "provias", kind: "emergencia", key: r.dataset.key}); });
function renderZonas() {
  const q = ($("#zq").value || "").toLowerCase();
  const rows = V.zonas.filter(z => !q || [z.region, z.provincia, z.distrito, z.peligros_g, z.paraje].join(" ").toLowerCase().includes(q));
  $("#zonas").innerHTML = rows.map(z => `<tr class="clk" data-key="${esc(z._key)}" tabindex="0" title="Ver la ficha y el punto en el mapa satelital"><td>${lvlChip(lvlNum(z.nivel))}</td><td>${esc(z.region)}</td><td>${esc(z.provincia)} · ${esc(z.distrito)}</td><td>${esc(z.paraje)}</td><td>${esc(z.peligros_g)}</td><td class="muted">${esc(z.elemento)}</td></tr>`).join("");
  const avs = [...new Set(V.zonas.map(z => z.nro_aviso))];
  $("#zonas-note").textContent = `${rows.length} de ${V.zonas.length} zonas${avs.length ? `, del cruce de INGEMMET con el aviso SENAMHI #${avs.join(", #")}` : ""}.`;
}
$("#zq").addEventListener("input", () => D && renderZonas());
$("#zonas").addEventListener("click", e => { const tr = e.target.closest("tr[data-key]"); if (tr) openDetail({source: "ingemmet", kind: "zona_alerta", key: tr.dataset.key}); });
$("#zonas").addEventListener("keydown", e => { if (e.key === "Enter") e.target.closest("tr[data-key]")?.click(); });
let pd = [], pdays = [];
function renderPron() {
  pd = d3.groups(V.pronostico, p => p.ciudad + "|" + p.departamento);
  pdays = [...new Set(V.pronostico.map(p => p.dia))].slice(0, 3);
  pdays.forEach((d, i) => $("#pd" + i).textContent = d.replace(/^(\p{L}+),\s*(\d+) de (\p{L}+)/u, (m, a, b) => `${a.slice(0,3)} ${+b}`));
  filterPron();
  $("#hidro").innerHTML = V.hidro.map(h => `<tr class="clk" data-key="${esc(h._key)}" tabindex="0"><td>${lvlChip(Math.min(4, lvlNum(h.color_text)))}</td><td><b>${esc(title(h.nom_estacion))}</b><div class="muted" style="font-size:12px">${esc(title(h.titulo))}</div></td><td>${esc(title(h.nom_distrito))}, ${esc(title(h.nom_provincia))}<div class="muted" style="font-size:12px">${esc(title(h.nom_departamento))} · cuenca ${esc(title(h.nom_cuenca))}</div></td></tr>`).join("") || `<tr><td colspan="3" class="muted">Sin avisos hidrológicos vigentes.</td></tr>`;
  uvdays = uvDays.slice(0, 3);
  [0, 1, 2].forEach(i => { $("#uvd" + i).textContent = uvdays[i] ? fmtDay(uvdays[i]) : ""; $("#uvd" + i).hidden = !uvdays[i]; });   // SENAMHI publica 2 o 3 días
  filterUv();
  $("#uv-note").textContent = `Escala SENAMHI: 0–2 bajo · 3–5 moderado · 6–7 alto · 8–10 muy alto · 11+ extremo. Entre paréntesis, la hora pico. También como capa “Índice UV” del mapa. ${V.uvUnmatched.length ? `${V.uvUnmatched.length} zonas UV usan códigos que no son provincias INEI y no aparecen aquí.` : ""}`;
}
let uvdays = [];
const uvChip = v => v == null ? "—" : `<span class="lvl" style="background:${uvColor(v)};color:${v >= 8 ? "#fff" : "#15140F"}">${v}</span>`;
function filterUv() {
  const q = ($("#uvq").value || "").toLowerCase();
  const pn = Object.fromEntries(V.geo.provs.features.map(f => [f.properties.id, f.properties]));
  const rows = Object.entries(V.uv).map(([c, u]) => ({c, u, n: title(pn[c]?.n), d: title(pn[c]?.d)}))
    .filter(r => !q || `${r.n} ${r.d}`.toLowerCase().includes(q))
    .sort((a, b) => (b.u.v[0] ?? -1) - (a.u.v[0] ?? -1) || a.n.localeCompare(b.n));
  $("#uvt").innerHTML = rows.map(r => `<tr class="clk" data-key="${esc(r.u.key)}" tabindex="0"><td>${esc(r.n)}</td><td class="muted">${esc(r.d)}</td>${uvdays.map((_, i) => `<td class="num">${uvChip(r.u.v[i])}${r.u.h[i] ? ` <span class="muted">(${esc(r.u.h[i])})</span>` : ""}</td>`).join("")}</tr>`).join("")
    || `<tr><td colspan="5" class="muted">${q ? "Ninguna provincia coincide." : "Sin datos de índice UV."}</td></tr>`;
}
$("#uvq").addEventListener("input", () => D && filterUv());
function filterPron() {
  const q = ($("#pq").value || "").toLowerCase();
  $("#pron").innerHTML = pd.filter(([k]) => !q || k.toLowerCase().includes(q)).map(([k, v]) => {
    const [c, dp] = k.split("|"), by = Object.fromEntries(v.map(p => [p.dia, p]));
    return `<tr class="clk" data-key="${esc(k)}" tabindex="0"><td>${esc(title(c))}</td><td class="muted">${esc(title(dp))}</td>${pdays.map(d => by[d] ? `<td class="num" title="${esc(by[d].descripcion)}">${by[d].tmax}° <span class="muted">${by[d].tmin}°</span></td>` : "<td>—</td>").join("")}</tr>`;
  }).join("");
}
$("#pq").addEventListener("input", () => D && filterPron());
// Cada fila de la sección SENAMHI abre su ficha (con datos completos y redacción con IA)
[["#avisos", "senamhi_avisos", "aviso"], ["#hidro", "senamhi_hidro", "aviso_estacion"], ["#uvt", "senamhi_uv", "uv_zona"], ["#pron", "senamhi_pronostico", "ciudad"]]
  .forEach(([sel, source, kind]) => {
    $(sel).addEventListener("click", e => { const tr = e.target.closest("tr[data-key]"); if (tr) openDetail({source, kind, key: tr.dataset.key}); });
    $(sel).addEventListener("keydown", e => { if (e.key === "Enter") e.target.closest("tr[data-key]")?.click(); });
  });

/* ── últimos datos recibidos (por fuente) ───────────────────────── */
const SHORT = {senamhi_avisos: "SENAMHI avisos", senamhi_uv: "SENAMHI UV", senamhi_pronostico: "SENAMHI pronóstico", senamhi_hidro: "SENAMHI hidrología",
  indeci: "INDECI", indeci_fotos: "INDECI fotos", igp: "IGP sismos", serfor: "SERFOR incendios", ingemmet: "INGEMMET", enfen: "ENFEN", firms: "NASA FIRMS",
  com_pnp: "PNP comunicados", com_provias: "PROVIAS notas", com_mtc: "MTC notas", com_mininter: "MININTER notas",
  provias: "PROVIAS vías", bomberos: "Bomberos", dhn_avisos: "DHN avisos", dhn_pronosticos: "DHN pronósticos", olas_openmeteo: "Open-Meteo olas", ana_obras: "ANA obras", indeci_recursos: "INDECI recursos", sinpad_historial: "SINPAD historial", declaratorias: "Estados de emergencia"};
const LAT = {rows: [], counts: {}, more: false, next: null, req: 0};
function writeHash() {
  const p = [state.region && `r=${state.region}`, state.latSource && `f=${state.latSource}`].filter(Boolean).join("&");
  history.replaceState(null, "", p ? `#${p}` : location.pathname);
}
const fmtEvent = s => {
  if (!s) return "";
  const m = String(s).match(/^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?/);
  return m ? `${fmtDay(m[1])}${m[2] ? " " + m[2] : ""}${/UTC/.test(s) ? " UTC" : ""}` : String(s);
};
async function loadLatest(append = false) {
  const n = ++LAT.req;
  const q = new URLSearchParams({limit: 50});
  if (state.latSource) q.set("source", state.latSource);
  if (state.region) q.set("region", state.region);
  if (append && LAT.next) q.set("before", LAT.next);
  q.set("seguimientos", state.latSeg ? "true" : "false");
  try {
    const d = await (await fetch(`/api/latest?${q}`, {cache: "no-store"})).json();
    if (n !== LAT.req) return;
    LAT.rows = append ? LAT.rows.concat(d.rows) : d.rows; LAT.counts = d.counts24h || {}; LAT.more = d.more; LAT.next = d.next_before;
    LAT.ocultos = (append ? LAT.ocultos || 0 : 0) + (d.seguimientos_ocultos || 0);
    renderLatest();
  } catch { $("#latest").innerHTML = `<li class="empty">El colector no respondió.</li>`; }
}
function setLatSource(src, scroll = false) {
  state.latSource = src || null; writeHash(); loadLatest();
  if (scroll) $("#latest-sec").scrollIntoView({behavior: reduceMotion ? "auto" : "smooth"});
}
function renderLatest() {
  const hs = Object.fromEntries(H.map(h => [h.id, h]));
  const ids = H.filter(h => !h.internal).map(h => h.id).filter(id => id !== "indeci_fotos");
  $("#lat-src").innerHTML = [["", "Todas", Object.values(LAT.counts).reduce((a, b) => a + b, 0)], ...ids.map(id => [id, SHORT[id] || id, LAT.counts[id]])]
    .map(([id, label, n]) => `<button type="button" role="tab" data-src="${id}" aria-pressed="${(state.latSource || "") === id}" title="${id ? esc(hs[id]?.name || "") + " · " + (STATUS[hs[id]?.status] || "") : "Todas las fuentes"}">
      ${id ? `<i class="dot ${hs[id]?.status || ""}"></i>` : ""}${esc(label)}${n != null ? `<span class="n ${n ? "hot" : ""}" title="nuevos en 24 h">${nf.format(n)}</span>` : ""}</button>`).join("");
  const s = state.latSource && hs[state.latSource];
  $("#lat-head").hidden = !s;
  if (s) $("#lat-head").innerHTML = `<span><b>${esc(s.org)} · ${esc(s.name)}</b></span>
      <span><span class="chip st-${s.status}">${STATUS[s.status] || s.status}</span> · última lectura ${when(s.last_run)} · último éxito ${when(s.last_ok)} · ${every(s.interval_s)}, próxima ${h_m(s.next_run)} (${until(s.next_run)})</span>
      <button type="button" class="rbtn" data-src="${s.id}" aria-label="Actualizar ${esc(s.name)}">↻</button>
      <span class="msg ${!s.failures ? "" : s.status === "fallando" ? "bad" : "warn"}">${esc(s.message || "")} · ${plural(LAT.counts[s.id] || 0, "registro nuevo", "registros nuevos")} en 24 h${state.region ? ` en ${esc(regName(state.region))}` : ""}.</span>`;
  $("#latest").innerHTML = LAT.rows.map((r, i) => `<li><button type="button" class="row ${r.detail || r.group ? "" : "nod"}" data-i="${i}">
      <span class="rt">${ago(r.received)}<small>${new Date(r.received * 1000).toLocaleString("es-PE", {timeZone: "America/Lima", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit"})}</small></span>
      <span class="src">${esc(SHORT[r.source] || r.source)}${r.imagen ? `<img class="lthumb" src="${esc(r.imagen)}" alt="" loading="lazy">` : ""}</span>
      <span class="body"><span class="tt">${r.level ? lvlChip(r.level) : ""}${esc(r.title)}${danosChips(r.danos)}${r.backfill ? `<span class="bf" title="Llegó en la carga inicial del colector: es histórico, no nuevo">carga inicial</span>` : ""}${r.seguimiento ? `<span class="segchip" title="Reporte nuevo sobre un evento que ocurrió hace ${r.dias_desde_evento} días">Seguimiento · evento de hace ${r.dias_desde_evento} días</span>` : ""}</span>
        ${r.subtitle ? `<span class="ss" style="display:block">${esc(r.subtitle)}</span>` : ""}
        <span class="mm" style="display:block">${[r.place, r.event_time && `${r.kind === "noticia" || r.kind === "comunicado" ? "publicado" : r.kind === "aviso" ? "emitido" : "ocurrido"} ${fmtEvent(r.event_time)}`, r.ocurrencia && r.reportado ? `reportado ${fmtEvent(r.reportado)}` : "", r.group ? "ver cada uno →" : ""].filter(Boolean).map(esc).join(" · ")}</span></span></button></li>`).join("")
    || `<li class="empty">${`Sin registros en los últimos 7 días${state.region ? ` para ${esc(regName(state.region))}` : ""}.`}</li>`;
  $("#lat-more").hidden = !LAT.more;
  const soloIndeci = !state.latSource || state.latSource === "indeci";
  $("#lat-seg").hidden = !soloIndeci;
  $("#lat-seg").innerHTML = `<label><input type="checkbox" id="lat-seg-cb" ${state.latSeg ? "checked" : ""}> Incluir seguimientos de eventos antiguos</label>
    <span class="muted">${state.latSeg ? "Se muestran marcados como «Seguimiento»." : LAT.ocultos ? `${LAT.ocultos} reporte${LAT.ocultos > 1 ? "s" : ""} INDECI oculto${LAT.ocultos > 1 ? "s" : ""}: siguen eventos ocurridos hace más de 7 días.` : ""}</span>`;
  $("#lat-seg-cb").onchange = e => { state.latSeg = e.target.checked; loadLatest(); };
}
$("#lat-src").addEventListener("click", e => { const b = e.target.closest("button"); if (b) setLatSource(b.dataset.src); });
$("#lat-head").addEventListener("click", e => { const b = e.target.closest(".rbtn"); if (b) { refresh(b.dataset.src, b); setTimeout(() => loadLatest(), 6000); } });
$("#lat-more").addEventListener("click", () => loadLatest(true));
$("#latest").addEventListener("click", e => {
  const b = e.target.closest(".row"); if (!b) return;
  const r = LAT.rows[+b.dataset.i];
  if (r.group) return setLatSource(r.source);
  if (!r.detail) return;
  openDetail({source: r.source, kind: r.kind, key: r.key});   // la ficha se abre donde está el usuario
});

/* ── fotos: visor, tira del día, galería en la ficha ─────────────── */
const LB = {list: [], i: 0};
const fotoTitle = f => f.caption || `${title(f.evento || "Emergencia")}${f.distrito ? " — " + title(f.distrito) : ""}`;
function lbShow() {
  const f = LB.list[LB.i];
  $("#lb-img").src = f.url; $("#lb-img").alt = fotoTitle(f);
  $("#lb-cap").innerHTML = `${esc(fotoTitle(f))}<span class="cr">${esc(f.credit || "Foto: INDECI / COER — anexo fotográfico del reporte")}${f.fecha ? " · " + esc(f.fecha) : ""} · ${LB.i + 1} de ${LB.list.length}</span>`
    + (f.ref || f.key ? `<button type="button" data-open>${f.abrir || (f.ref ? "Ver la ficha de la emergencia" : "Ver el reporte completo")}</button>` : "");
  $("#lb").querySelectorAll(".lb-nav").forEach(b => b.hidden = LB.list.length < 2);
}
function openLightbox(list, i = 0) { LB.list = list; LB.i = i; lbShow(); $("#lb").hidden = false; $("#lb .lb-x").focus(); }
function closeLightbox() { $("#lb").hidden = true; $("#lb-img").removeAttribute("src"); }
$("#lb").addEventListener("click", e => {
  if (e.target.closest(".lb-x") || e.target === $("#lb")) return closeLightbox();
  const nav = e.target.closest(".lb-nav");
  if (nav) { LB.i = (LB.i + (nav.classList.contains("next") ? 1 : -1) + LB.list.length) % LB.list.length; return lbShow(); }
  const open = e.target.closest("[data-open]");
  if (open) { const f = LB.list[LB.i]; closeLightbox(); openDetail(f.ref || {source: "indeci", kind: "item", key: f.key}); }
});
addEventListener("keydown", e => {
  if ($("#lb").hidden) return;
  if (e.key === "Escape") { e.stopImmediatePropagation(); closeLightbox(); }
  if (e.key === "ArrowRight" || e.key === "ArrowLeft") { LB.i = (LB.i + (e.key === "ArrowRight" ? 1 : -1) + LB.list.length) % LB.list.length; lbShow(); }
}, true);
/* `when` marca cuándo llegó el reporte: es lo que ordena la tira. La fecha del pie de foto es
   otra cosa (cuándo se tomó la imagen) y por eso se rotula aparte, para que una foto de agosto
   en un reporte de hoy no parezca contenido viejo. */
const phHtml = (f, i, {reg = false, when = false} = {}) => `<button type="button" class="ph" data-i="${i}" aria-label="Ampliar: ${esc(fotoTitle(f))}">
  ${reg && f.reg ? `<span class="reg">${esc(regName(f.reg))}</span>` : ""}${when && f.ts ? `<span class="when">${esc(ago(f.ts))}</span>` : ""}<img src="${esc(f.url)}" alt="" loading="lazy">
  <span class="cap"><b>${esc(fotoTitle(f))}</b>
    ${(m => m ? `<span class="meta">${esc(m)}</span>` : "")(f.meta || [f.dpto && title(f.dpto), f.tipo && f.num ? `${title(f.tipo)} N° ${f.num}` : ""].filter(Boolean).join(" · "))}
    ${f.fecha ? `<span class="shot">${esc(f.shotLabel || "Foto tomada el")} ${esc(f.fecha)}</span>` : ""}</span></button>`;
function renderStrip() {
  const F = V.fotosDia || [];
  $("#ind-strip").innerHTML = F.map((f, i) => phHtml(f, i, {reg: !state.region, when: true})).join("")
    || `<p class="muted" style="margin:0">${state.region ? `Sin fotos de campo de ${esc(regName(state.region))} en las últimas ${V.indeciWindowH} h.` : "Aún no hay fotos procesadas."}</p>`;
  $("#ind-strip").onclick = e => { const b = e.target.closest(".ph"); if (b) openLightbox(F, +b.dataset.i); };
  const pend = V.indeci.filter(i => i.clase === "reporte" && i.fotos == null).length;
  $("#strip-note").textContent = `${F.length} fotos de ${new Set(F.map(f => f.key)).size} reportes recibidos en las últimas ${V.indeciWindowH} h, de la más reciente a la más antigua según cuándo se tomó`
    + ` · un reporte de hoy puede traer fotos de semanas atrás: quedan al final${pend ? ` · ${pend} reportes aún en cola` : ""}. Crédito: INDECI / COER.`;
}

/* ── vista satelital (NASA GIBS vía el colector) ─────────────────── */
let satReq = 0;
const addDays = (iso, n) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
async function loadSat(S) {
  const box = $("#satbox"); if (!box) return;
  const n = ++satReq, today = new Date().toLocaleDateString("en-CA", {timeZone: "America/Lima"});
  box.innerHTML = `<div class="sathead"><b>Vista satelital</b><span class="muted">buscando imagen de ${fmtDay(S.date)}…</span></div><div class="satimg loading"></div>`;
  try {
    const q = new URLSearchParams({lat: S.lat, lon: S.lon, date: S.date, km: S.km, layer: S.layer});
    const r = await (await fetch(`/api/sat?${q}`)).json();
    if (n !== satReq || !$("#satbox")) return;
    const shown = r.date || S.date;
    const note = r.url ? (r.date !== S.date ? `No hay imagen del ${fmtDay(S.date)}; se muestra la del ${fmtDay(r.date)}.` : "") : `Sin imagen disponible entre ${fmtDay(addDays(S.date, -3))} y ${fmtDay(S.date)} (aún no procesada o sin pasada).`;
    box.innerHTML = `<div class="sathead"><b>Vista satelital</b><span class="muted">${r.url ? `${esc(r.label)} · ${fmtDay(r.date)} · ${S.km * 2} km de lado` : ""}</span></div>
      ${r.url ? `<button type="button" class="satimg" aria-label="Ampliar la vista satelital"><img src="${esc(r.url)}" alt="Imagen satelital del ${esc(r.date)}"><i class="cross"></i></button>` : `<div class="satimg empty">Sin imagen</div>`}
      <div class="satctl">
        <button type="button" data-d="-1">◀ día anterior</button>
        <button type="button" data-d="1" ${shown >= today ? "disabled" : ""}>día siguiente ▶</button>
        <span class="seg">${[["auto", "Auto"], ["viirs", "VIIRS"], ["modis", "MODIS"]].map(([v, l]) => `<button type="button" data-l="${v}" aria-pressed="${S.layer === v}">${l}</button>`).join("")}</span>
        <a href="${esc(r.worldview)}" target="_blank" rel="noopener">Abrir en NASA Worldview ↗</a>
      </div>
      <p class="credit">${note ? esc(note) + " " : ""}Puntos rojos: focos de calor detectados por el satélite ese día. ${r.url ? esc(r.credit) : "NASA GIBS"} · la cruz marca el punto del registro.</p>`;
    box.querySelectorAll("[data-d]").forEach(b => b.onclick = () => loadSat({...S, date: addDays(shown, +b.dataset.d)}));
    box.querySelectorAll("[data-l]").forEach(b => b.onclick = () => loadSat({...S, layer: b.dataset.l}));
    box.querySelector("button.satimg")?.addEventListener("click", () => openLightbox([{url: r.url, caption: `Vista satelital · ${fmtDay(r.date)} · ${S.km * 2} km de lado`, credit: r.credit}]));
  } catch { if (n === satReq && $("#satbox")) box.innerHTML = `<div class="sathead"><b>Vista satelital</b></div><p class="credit">NASA GIBS no respondió.</p>`; }
}

/* ── daños INDECI ───────────────────────────────────────────────── */
const VIDA = new Set(["personas_fallecidas", "personas_desaparecidas", "personas_heridas"]);
const SHORTD = {personas_fallecidas: "fallecidos", personas_desaparecidas: "desaparecidos", personas_heridas: "heridos",
  personas_damnificadas: "damnificados", personas_afectadas: "afectados", viviendas_colapsadas: "viv. colapsadas",
  viviendas_inhabitables: "viv. inhabitables", viviendas_afectadas: "viv. afectadas", cobertura_natural_destruida_ha: "ha cobertura destruida",
  cobertura_natural_perdida_ha: "ha cobertura perdida", cobertura_natural_afectada_ha: "ha cobertura afectada", cultivo_perdido_ha: "ha cultivo perdido",
  cultivo_afectado_ha: "ha cultivo afectado", vias_afectadas_km: "km de vía afectada", vias_destruidas_km: "km de vía destruida"};
const fmtN = v => typeof v === "number" && !Number.isInteger(v) ? v.toLocaleString("es-PE", {maximumFractionDigits: 1}) : nf.format(v);
function danosChips(tot, max = 3) {
  if (!tot) return "";
  const order = (D.danos?.orden || Object.keys(tot)).filter(k => tot[k]);
  const pick = order.filter(k => SHORTD[k]).slice(0, max);
  return pick.length ? `<span class="dchips">${pick.map(k => `<span class="${VIDA.has(k) ? "vida" : ""}">${fmtN(tot[k])} ${SHORTD[k]}</span>`).join("")}</span>` : "";
}
function danosFigs(tot) {
  const order = (D.danos?.orden || []).filter(k => tot && tot[k]);
  return order.length ? `<div class="dfig">${order.map(k => `<div class="${VIDA.has(k) ? "vida" : ""}"><b>${fmtN(tot[k])}</b><span>${esc(D.danos.labels[k])}</span></div>`).join("")}</div>`
    : `<p class="muted" style="margin:0">Sin cifras de daños.</p>`;
}
/* La lista se ordena por lo más reciente (como llega de INDECI) o por magnitud humana.
   El ámbito separa lo ocurrido en la ventana de lo que viene de antes y sigue reportando. */
let danosOrden = "recientes";     // recientes | afectados
let danosAmbito = "todos";        // todos | nuevos | seguimiento
let danosTope = 10;
const danosPersonas = e => (e.totales.personas_damnificadas || 0) + (e.totales.personas_afectadas || 0);

function renderDanos() {
  const X = V.danos, box = $("#danos");
  if (!X) { box.hidden = true; return; }
  box.hidden = false;
  const nSeg = X.n_eventos - X.n_nuevos;
  const enAmbito = X.eventos.filter(e => danosAmbito === "todos" || (danosAmbito === "nuevos" ? e.nuevo : !e.nuevo));
  const lista = [...enAmbito].sort(danosOrden === "afectados"
    ? (a, b) => danosPersonas(b) - danosPersonas(a)
    : (a, b) => (b.ts || 0) - (a.ts || 0));
  const vistos = lista.slice(0, danosTope);
  box.innerHTML = `<div class="danos-head"><h3 style="margin:0">Daños reportados · últimos ${X.dias} días${state.region ? ` · ${esc(regName(state.region))}` : ""}</h3>
      <span class="note" style="margin:0">${X.n_eventos} eventos con cifras · se cuenta el reporte más reciente de cada evento (sin duplicar actualizaciones)</span></div>
    <div class="danos-cols">
      <div class="danos-col"><h4><b>${X.n_nuevos}</b> ${X.n_nuevos === 1 ? "evento ocurrido" : "eventos ocurridos"} en estos ${X.dias} días · desde ${fmtDay(X.desde)}</h4>${danosFigs(X.nuevos)}</div>
      <div class="danos-col"><h4><b>${nSeg}</b> ${nSeg === 1 ? "evento anterior que siguió reportando" : "eventos anteriores que siguieron reportando"} en estos ${X.dias} días · cifras acumuladas del evento</h4>${danosFigs(X.seguimiento)}</div>
    </div>
    <p class="note" style="margin:8px 0 0">Las dos columnas cuentan solo reportes recibidos en los últimos ${X.dias} días. La diferencia es cuándo ocurrió el evento: los de la derecha son de antes y siguen actualizándose, por eso sus cifras son acumuladas desde que empezaron.</p>
    <div class="danos-tools">
      <span class="tl">Ordenar</span><div class="filters" id="danos-orden"></div>
      <span class="tl">Mostrar</span><div class="filters" id="danos-ambito"></div>
    </div>
    ${vistos.length ? `<ul class="devents">${vistos.map(e => `<li><button type="button" data-key="${esc(e.item_key)}">
      <span class="t">${esc(title(e.evento))} — ${esc(title(e.distrito))}${e.multi_region ? ` <span class="muted">(${Object.keys(e.por_reg).length} regiones)</span>` : ""}</span>
      <span class="n">${danosChips(e.totales, 3)}</span>
      <span class="s">${e.nuevo ? "Ocurrió en estos días" : "Viene de antes"} · último reporte ${ago(e.ts)} · ocurrió ${e.ocurrencia ? fmtDay(e.ocurrencia) : "—"} · ${esc(title(e.tipo))} N° ${esc(e.num)}${e.seq ? ` (reporte ${esc(e.seq)})` : ""}${e.actualizado ? ` · cifras al ${esc(e.actualizado)}` : ""}</span></button></li>`).join("")}</ul>` : `<p class="muted" style="margin:12px 0 0">Sin eventos con cifras en este filtro.</p>`}
    ${lista.length > vistos.length ? `<button type="button" class="dmore">Ver ${Math.min(10, lista.length - vistos.length)} más · ${lista.length} en total</button>` : ""}`;
  seg($("#danos-orden"), [["recientes", "Más recientes"], ["afectados", "Más afectados"]], danosOrden,
    v => { danosOrden = v; danosTope = 10; renderDanos(); });
  seg($("#danos-ambito"), [["todos", `Todos ${X.n_eventos}`], ["nuevos", `Ocurridos en ${X.dias} días ${X.n_nuevos}`], ["seguimiento", `Vienen de antes ${nSeg}`]], danosAmbito,
    v => { danosAmbito = v; danosTope = 10; renderDanos(); });
  box.querySelector(".dmore")?.addEventListener("click", () => { danosTope += 10; renderDanos(); });
  // Ya no se lleva al usuario al mapa: la ficha se abre en el cajón fijo, donde esté leyendo.
  box.querySelectorAll(".devents button").forEach(b => b.onclick = () => openDetail({source: "indeci", kind: "item", key: b.dataset.key}));
}

/* ── comunicados gob.pe ─────────────────────────────────────────── */
const INST = {pnp: "PNP", pvn: "PROVIAS", mtc: "MTC", mininter: "MININTER"};
let comInst = "all";
const COM_PASO = 10;              // comunicados visibles de entrada y por cada "Ver más"
let comTope = COM_PASO, comClave = "";
function renderComunicados() {
  const C = V.comunicados || [];
  seg($("#com-filters"), [["all", `Todas ${C.length}`], ...Object.entries(INST).map(([k, l]) => [k, `${l} ${C.filter(c => c.inst === k).length}`])], comInst, v => { comInst = v; renderComunicados(); });
  const todas = C.filter(c => comInst === "all" || c.inst === comInst);
  // al cambiar institución o región se vuelve a la primera tanda; las recargas del snapshot la conservan
  const clave = `${comInst}|${state.region || ""}`;
  if (clave !== comClave) { comClave = clave; comTope = COM_PASO; }
  const rows = todas.slice(0, comTope);
  const boot = d3.min(C, c => c._first_seen) || 0;   // el primer lote leído no cuenta como "nuevo"
  // Tira de imágenes de los comunicados (los del filtro elegido): se recorren una tras otra en grande, como las fotos de campo
  const FC = todas.filter(c => c.imagen).map(c => ({url: c.imagen, caption: c.titulo, fecha: c.fecha ? fmtDay(c.fecha) : c.fecha_texto, shotLabel: "Publicado el",
    meta: c.inst_nombre, credit: `Imagen: ${c.inst_nombre} — gob.pe`, abrir: "Ver el comunicado", ref: {source: c.source || "com_" + c.inst, kind: "noticia", key: c._key}}));
  $("#com-fotos-w").hidden = !FC.length;
  $("#com-fotos").innerHTML = FC.map((f, i) => phHtml(f, i)).join("");
  $("#com-fotos").onclick = e => { const b = e.target.closest(".ph"); if (b) openLightbox(FC, +b.dataset.i); };
  $("#com-fotos-note").textContent = FC.length ? `${FC.length} imágenes, de la más reciente a la más antigua · clic para verlas en grande y pasar con las flechas` : "";
  const iFoto = Object.fromEntries(FC.map((f, i) => [f.ref.key, i]));
  $("#com").innerHTML = rows.map(c => {
    const isNew = c._first_seen > boot + 120 && Date.now() / 1000 - c._first_seen < 3600;
    return `<li class="${c.imagen ? "withimg" : ""}"><span class="hr">${c.fecha ? fmtDay(c.fecha) : esc(c.fecha_texto)}</span>${c.imagen ? `<button type="button" class="cthumb" data-foto="${iFoto[c._key]}" aria-label="Ver la imagen en grande"><img src="${esc(c.imagen)}" alt="" loading="lazy"></button>` : ""}<div>
      <div class="inst">${esc(c.inst_nombre)}${isNew ? ` <span class="new">nuevo</span>` : ""}</div>
      <div class="ev"><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.titulo)}</a></div>
      ${c.descripcion && c.descripcion !== c.titulo ? `<div class="d">${esc(c.descripcion)}</div>` : ""}
      ${iaBox({source: c.source || "com_" + c.inst, kind: "noticia", key: c._key}, {compacto: true})}
      <details data-key="${esc(c._key)}" data-src="${esc(c.source || "com_" + c.inst)}"><summary>Leer el texto completo</summary><div class="txt">Consultando gob.pe…</div></details></div></li>`;
  }).join("") || `<li class="muted">${state.region ? `Ningún comunicado de los últimos 14 días menciona ${esc(regName(state.region))}.` : "Sin comunicados en los últimos 14 días."}</li>`;
  const resto = todas.length - rows.length;
  $("#com-more").hidden = resto <= 0;
  $("#com-more").textContent = `Ver ${Math.min(COM_PASO, resto)} más · ${rows.length} de ${todas.length}`;
  $("#com-more").onclick = () => { comTope += COM_PASO; renderComunicados(); };
  $("#com").querySelectorAll("[data-foto]").forEach(b => b.onclick = () => openLightbox(FC, +b.dataset.foto));
  $("#com").querySelectorAll("details").forEach(dt => dt.addEventListener("toggle", () => {
    if (!dt.open || dt.dataset.loaded) return;
    dt.dataset.loaded = "1";
    fetch(`/api/detail?source=${encodeURIComponent(dt.dataset.src)}&kind=noticia&key=${encodeURIComponent(dt.dataset.key)}`).then(r => r.json()).then(d => {
      const pdfs = (d.links || []).filter(l => /PDF/.test(l.label));
      dt.querySelector(".txt").innerHTML = (d.text ? esc(d.text) : `<span class="muted">${esc(d.error || "La publicación no tiene texto adicional.")}</span>`)
        + (pdfs.length ? `<p>${pdfs.map(l => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)} ↗</a>`).join(" · ")}</p>` : "");
    }).catch(() => { dt.querySelector(".txt").textContent = "El colector no respondió."; });
  }));
  $("#com-note").textContent = (state.region ? `Con región elegida se muestran los que nombran a ${regName(state.region)} en el título o la descripción (detección por texto; los comunicados no traen ubicación). ` : "") + `Comunicados y notas de los últimos 14 días. gob.pe entrega solo las 9 más recientes por institución; el colector consulta cada 15 min y conserva el historial. Las notas de PROVIAS son hoy la única vía a sus emergencias viales (su servidor no responde).`;
}

/* ── orquestación ───────────────────────────────────────────────── */
let mapReady = false;
function renderAll() {
  V = makeView();
  const first = !mapReady;
  if (first) { initMap(); mapReady = true; }
  avisoDays = Object.keys(D.levelsByDay).sort();
  if (!state.day || !avisoDays.includes(state.day)) state.day = avisoDays.includes(D.snapshot) ? D.snapshot : avisoDays[0];
  uvDays = (Object.values(D.uv)[0] || {}).dias || [];
  if (state.uvDay >= uvDays.length) state.uvDay = 0;
  renderRegionBar(); renderHealth(); renderThesis(); renderLayers(); renderMapAll();
  if (first && state.region) { const f = D.geo.deps.features.find(f => f.properties.id === state.region); if (f) zoomToFeature(f); }
  renderEnfen(); renderAvisos(); renderIndeci(); renderDanos(); renderStrip(); renderComunicados(); renderSismos(); renderSerfor(); renderZonas(); renderVias(); renderMar(); renderObras(); renderDeclaratorias(); renderRecursos(); renderSinpad(); renderBomberos(); renderReportes(); renderPron();
  $("#f-built").textContent = `Snapshot ${V.built} · armado en ${V.buildMs} ms`;
}
loadSnapshot().then(() => { lastSig = sigOf(H); loadLatest(); cargarReportes(); }).catch(() => { $("#conn").className = "conn off"; $("#conn").textContent = "Sin conexión con el colector"; });
setInterval(poll, 15000);
setInterval(() => H.length && renderHealth(), 30000);


/* ── asistente de IA: borradores verificados ───────────────────────────
   El servidor hace todo lo que importa (verificar cifras, bloquear, citar, exigir aprobación,
   registrar). Aquí solo se muestra y se firma cada acción con el nombre de quien la hace. */
const IA = {estado: null, filtro: "", borradores: [], mostrar: 20};
const IA_ESTADOS = {borrador: "Por revisar", aprobado: "Aprobado", bloqueado: "Bloqueado", descartado: "Descartado"};
const iaActor = () => { try { return localStorage.getItem("mesa.firma") || ""; } catch { return ""; } };
function iaPedirFirma() {
  const n = (prompt("¿Con qué nombre firma? Cada acción del asistente queda registrada a su nombre.", iaActor()) || "").trim();
  if (n) { try { localStorage.setItem("mesa.firma", n); } catch { /* sin almacenamiento: se pedirá de nuevo */ } }
  iaFirma();
  return n;
}
const iaFirmado = () => iaActor() || iaPedirFirma();
function iaFirma() {
  const a = iaActor();
  $("#ia-firma").innerHTML = a ? `Firmando como <b>${esc(a)}</b> · <button type="button" id="ia-cambiar">cambiar</button>` : `<button type="button" id="ia-cambiar">Indicar mi nombre</button>`;
  $("#ia-cambiar").onclick = iaPedirFirma;
}
async function iaPost(url, body) {
  const r = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const txt = await r.text();
  if (!r.ok) { let m = txt; try { m = JSON.parse(txt).detail || txt; } catch { /* texto plano */ } throw new Error(m); }
  try { return JSON.parse(txt); } catch { return txt; }
}
function iaBox(ref, {compacto = false} = {}) {
  const E = IA.estado;
  if (!E) return "";
  const tipos = Object.entries(E.tipos).filter(([, t]) => t.alcances.includes("registro"));
  if (compacto) return `<div class="iarow" data-ref="${esc(JSON.stringify(ref))}"><span class="lbl">Redactar con IA</span>
    ${tipos.map(([k, t]) => `<button type="button" class="iabtn sec" data-ia-gen="${k}" ${E.habilitado ? "" : `disabled title="Falta la clave de la API de Claude en este servidor"`}>${esc(t.nombre)}</button>`).join("")}</div>`;
  return `<div class="iabox" data-ref="${esc(JSON.stringify(ref))}"><span class="lbl">Redactar con IA · a partir de este registro</span>
    <div class="btns">${tipos.map(([k, t]) => `<button type="button" class="iabtn sec" data-ia-gen="${k}" ${E.habilitado ? "" : "disabled"}>${esc(t.nombre)}</button>`).join("")}</div>
    ${E.habilitado ? "" : `<span class="muted" style="font-size:12px">El asistente no está configurado en este servidor (falta la clave de la API de Claude).</span>`}</div>`;
}
async function iaGenerar(tipo, ref) {
  const actor = iaFirmado(); if (!actor) return;
  const box = $("#detail"), n = ++detailReq, nombre = IA.estado.tipos[tipo].nombre;
  box.hidden = false; box.scrollTop = 0;
  box.innerHTML = `<div class="dh"><div><div class="src-org">Asistente de IA · ${esc(nombre)}</div><h3>Redactando y verificando cifras…</h3></div><button type="button" class="x" aria-label="Cerrar ficha">×</button></div>
    <p class="ia-wait">El modelo redacta solo con los datos de la fuente. Luego cada cifra se compara con esos datos; si alguna no coincide se reintenta una vez. Suele tardar entre 10 y 60 segundos.</p>`;
  try {
    const lugar = !ref && $("#region").value || undefined;   // briefing del lugar elegido; sin lugar, nacional
    const b = await iaPost("/api/ia/borradores", {tipo, alcance: ref ? "registro" : "briefing", ...(ref || {}), lugar, actor});
    iaCargar();
    if (n === detailReq) iaMostrar(b);
  } catch (e) {
    if (n === detailReq) box.innerHTML = `<div class="dh"><div><div class="src-org">Asistente de IA</div><h3>No se pudo redactar</h3></div><button type="button" class="x" aria-label="Cerrar ficha">×</button></div><div class="err">${esc(e.message)}</div>`;
  }
}
const IA_ACC = {generado: "Generado", bloqueado: "Bloqueado por verificación", editado: "Editado", aprobado: "Aprobado", descartado: "Descartado", entregado: "Texto copiado", error: "Error"};
function iaMostrar(b) {
  const box = $("#detail"), V_ = b.verificacion || {}, edit = b.estado === "borrador";
  const lim = {Titular: 90, "Zócalo": 60, X: 260};
  const cif = (V_.cifras || []);
  const ver = b.estado === "bloqueado"
    ? `<div class="ia-ver mal"><b>Bloqueado: el texto no se entrega.</b> Tras dos intentos seguía teniendo cifras que no están en los datos de la fuente${V_.no_encontradas?.length ? `: <span class="cif">${V_.no_encontradas.map(esc).join(", ")}</span>` : ""}${V_.en_letras?.length ? `; números en letras: <span class="cif">${V_.en_letras.map(esc).join(", ")}</span>` : ""}. Descártelo y vuelva a intentarlo.</div>`
    : V_.ok
      ? `<div class="ia-ver"><b>Cifras verificadas.</b> ${cif.length === 1 ? "La cifra del texto está en los datos de la fuente." : cif.length ? `Las ${cif.length} cifras del texto están en los datos de la fuente.` : "El texto no contiene cifras."}</div>`
      : `<div class="ia-ver mal"><b>Cifras sin respaldo en los datos</b> (introducidas al editar): <span class="cif">${[...(V_.no_encontradas || []), ...(V_.en_letras || [])].map(esc).join(", ")}</span>. Para aprobar tendrá que confirmarlo bajo su responsabilidad.</div>`;
  box.hidden = false; box.scrollTop = 0; box.focus({preventScroll: true});
  box.innerHTML = `<div class="dh"><div><div class="src-org">Asistente de IA · ${esc(b.tipo_nombre)} · <span class="iachip ${b.estado}">${IA_ESTADOS[b.estado]}</span></div>
      <h3>${esc(b.titulo || "")}</h3><div class="sub">Borrador N° ${b.id} · pedido por ${esc(b.autor || "")} · ${when(b.creado)}${b.aprobado_por ? ` · aprobado por ${esc(b.aprobado_por)}` : ""}</div></div>
      <button type="button" class="x" aria-label="Cerrar ficha">×</button></div>
    ${ver}
    ${V_.advertencia_modelo ? `<div class="err" style="background:var(--warn-bg);color:var(--warn)">Advertencia del modelo: ${esc(V_.advertencia_modelo)}</div>` : ""}
    ${b.contenido ? b.contenido.map((pz, i) => `<div class="ia-pieza"><label for="iap${i}">${esc(pz.etiqueta)}</label>
      <textarea id="iap${i}" data-et="${esc(pz.etiqueta)}" ${edit ? "" : "readonly"} rows="${Math.min(12, Math.max(2, Math.ceil(pz.texto.length / 70)))}">${esc(pz.texto)}</textarea>
      <span class="len ${lim[pz.etiqueta] && pz.texto.length > lim[pz.etiqueta] ? "over" : ""}" data-max="${lim[pz.etiqueta] || ""}">${pz.texto.length}${lim[pz.etiqueta] ? ` / ${lim[pz.etiqueta]}` : ""} caracteres</span></div>`).join("") : ""}
    <div class="ia-cita"><b>Fuente:</b> ${(b.cita || []).map(c => `${esc(c.institucion)} · ${esc(c.documento || "")}${c.hora_dato ? ` · ${esc(c.hora_dato)}` : ""}${c.url ? ` · <a href="${esc(c.url)}" target="_blank" rel="noopener">ver original</a>` : ""}`).join("<br>")}</div>
    <div class="ia-acc">
      ${edit ? `<button type="button" class="iabtn sec" data-ia-acc="editar">Guardar cambios</button><button type="button" class="iabtn ok" data-ia-acc="aprobar">Aprobar</button>` : ""}
      ${b.estado === "aprobado" ? `<button type="button" class="iabtn ok" data-ia-acc="copiar">Copiar texto con fuente</button>` : ""}
      ${["borrador", "bloqueado"].includes(b.estado) ? `<button type="button" class="iabtn bad" data-ia-acc="descartar">Descartar</button>` : ""}
    </div>
    <details class="ia-log"><summary>Registro de actividad (${b.auditoria.length})</summary><ol>${b.auditoria.map(a => `<li>${stamp(a.ts)} · <b>${esc(IA_ACC[a.accion] || a.accion)}</b> · ${esc(a.actor || "")}${a.detalle?.confirmado_por_editor ? " · confirmó cifras sin verificar: " + esc(a.detalle.cifras_no_verificadas.join(", ")) : ""}${a.detalle?.motivo ? " · " + esc(a.detalle.motivo) : ""}</li>`).join("")}</ol></details>
    <details class="ia-log" data-ia-dossier><summary>Datos exactos que recibió el modelo</summary><pre>Cargando…</pre></details>`;
  box.querySelectorAll(".ia-pieza textarea").forEach(t => t.addEventListener("input", () => {
    const len = t.parentElement.querySelector(".len"), mx = +len.dataset.max || 0;
    len.textContent = `${t.value.length}${mx ? ` / ${mx}` : ""} caracteres`; len.classList.toggle("over", !!mx && t.value.length > mx);
  }));
  box.querySelector("[data-ia-dossier]").addEventListener("toggle", async e => {
    if (!e.target.open || e.target.dataset.cargado) return;
    e.target.dataset.cargado = 1;
    const d = await (await fetch(`/api/ia/borradores/${b.id}?dossier=true`)).json();
    e.target.querySelector("pre").textContent = JSON.stringify(d.dossier, null, 1);
  });
  box.querySelectorAll("[data-ia-acc]").forEach(btn => btn.onclick = () => iaAccion(b, btn.dataset.iaAcc, btn));
}
async function iaAccion(b, acc, btn) {
  const actor = iaFirmado(); if (!actor) return;
  const piezas = [...$("#detail").querySelectorAll(".ia-pieza textarea")].map(t => ({etiqueta: t.dataset.et, texto: t.value}));
  const cambiado = b.contenido && piezas.some((p, i) => p.texto !== b.contenido[i]?.texto);
  btn.disabled = true;
  try {
    let r;
    if (acc === "editar") r = await iaPost(`/api/ia/borradores/${b.id}/editar`, {piezas, actor});
    else if (acc === "aprobar") {
      if (cambiado) b = await iaPost(`/api/ia/borradores/${b.id}/editar`, {piezas, actor});   // se aprueba lo que se ve
      let confirmar = false;
      if (!b.verificacion.ok) {
        confirmar = confirm(`Estas cifras no están en los datos de la fuente: ${[...b.verificacion.no_encontradas, ...b.verificacion.en_letras].join(", ")}.\n\n¿Aprueba el texto bajo su responsabilidad? Quedará registrado.`);
        if (!confirmar) { iaMostrar(b); return; }
      }
      r = await iaPost(`/api/ia/borradores/${b.id}/aprobar`, {actor, confirmar_cifras: confirmar});
      toast("Borrador aprobado.");
    } else if (acc === "descartar") {
      const motivo = prompt("Motivo del descarte (opcional):", "");
      if (motivo === null) { btn.disabled = false; return; }
      r = await iaPost(`/api/ia/borradores/${b.id}/descartar`, {actor, motivo});
    } else if (acc === "copiar") {
      const texto = await iaPost(`/api/ia/borradores/${b.id}/texto`, {actor});
      await navigator.clipboard.writeText(texto);
      toast("Texto copiado, con la línea de fuente.");
      r = await (await fetch(`/api/ia/borradores/${b.id}`)).json();
    }
    iaMostrar(r); iaCargar();
  } catch (e) {
    toast(e.message); btn.disabled = false;
  }
}
async function iaCargar() {
  try {
    IA.borradores = await (await fetch(`/api/ia/borradores?limit=100${IA.filtro ? "&estado=" + IA.filtro : ""}`)).json();
  } catch { IA.borradores = []; }
  const cuenta = e => IA.borradores.filter(b => b.estado === e).length;
  seg($("#ia-filtro"), [["", "Todos"], ["borrador", "Por revisar"], ["aprobado", "Aprobados"], ["bloqueado", "Bloqueados"], ["descartado", "Descartados"]],
    IA.filtro, v => { IA.filtro = v; IA.mostrar = POR_PAGINA; iaCargar(); });
  iaPintar();
}
function iaPintar() {
  $("#ia-list").innerHTML = IA.borradores.slice(0, IA.mostrar).map(b => `<li><button type="button" data-bid="${b.id}"><span class="iachip ${b.estado}">${IA_ESTADOS[b.estado]}</span>
      <span class="t">${esc(b.tipo_nombre)} · ${esc(b.titulo || "")}</span>
      <span class="s">N° ${b.id} · ${esc(b.autor || "")} · ${ago(b.creado)}${b.aprobado_por ? ` · aprobado por ${esc(b.aprobado_por)}` : ""}</span></button></li>`).join("")
    + verMas(Math.min(IA.mostrar, IA.borradores.length), IA.borradores.length)
    || `<li class="muted" style="padding:10px 2px">Aún no hay borradores${IA.filtro ? " en este estado" : ""}.</li>`;
  $("#ia-list").querySelectorAll("[data-bid]").forEach(el => el.onclick = async () => {
    iaMostrar(await (await fetch(`/api/ia/borradores/${el.dataset.bid}`)).json());
  });
  const mas = $("#ia-list").querySelector("[data-mas]");
  if (mas) mas.onclick = () => { IA.mostrar += POR_PAGINA; iaPintar(); };
}
async function iaInit() {
  try { IA.estado = await (await fetch("/api/ia/estado")).json(); } catch { IA.estado = null; }
  const E = IA.estado;
  $("#ia-estado").textContent = !E ? "No se pudo consultar el asistente."
    : E.habilitado ? `Modelo: ${E.modelo}.` : "El asistente no está configurado en este servidor: falta la clave de la API de Claude. Los borradores existentes se pueden revisar.";
  $("#ia-briefing").disabled = !E?.habilitado;
  $("#ia-briefing").onclick = () => iaGenerar("briefing", null);
  iaFirma(); iaCargar();
  try { if (V?.comunicados) renderComunicados(); } catch { /* el snapshot aún no cargó: se dibuja con él */ }
}
$("#com").addEventListener("click", e => {
  const g = e.target.closest("[data-ia-gen]");
  if (g) iaGenerar(g.dataset.iaGen, JSON.parse(g.closest("[data-ref]").dataset.ref));
});
$("#detail").addEventListener("click", e => {
  const g = e.target.closest("[data-ia-gen]");
  if (g) iaGenerar(g.dataset.iaGen, JSON.parse(g.closest("[data-ref]").dataset.ref));
});
iaInit();

/* ── tema claro / oscuro ────────────────────────────────────────────
   Sin elección guardada sigue al sistema. El mapa y los gráficos leen los colores al dibujarse: se redibuja todo. */
const oscuroSistema = matchMedia("(prefers-color-scheme: dark)");
const temaEfectivo = () => document.documentElement.dataset.theme || (oscuroSistema.matches ? "dark" : "light");
function pintarBotonTema() {
  const otro = temaEfectivo() === "dark" ? "claro" : "oscuro";
  $("#tema").textContent = `Tema ${otro}`;
  $("#tema").title = `Cambiar a tema ${otro}`;
}
$("#tema").onclick = () => {
  const t = temaEfectivo() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("mesa.tema", t); } catch { /* sin almacenamiento: dura hasta recargar */ }
  pintarBotonTema();
  if (D) renderAll();
};
oscuroSistema.addEventListener("change", () => {
  if (document.documentElement.dataset.theme) return;   // elección guardada: el sistema no la cambia
  pintarBotonTema();
  if (D) renderAll();
});
pintarBotonTema();

/* ── mapa fijo al bajar ─────────────────────────────────────────────
   Con la opción activa, al pasar la sección del mapa su columna se muda a una ventana en la esquina
   (mismo SVG: zoom, capas y clics siguen funcionando) y vuelve a su lugar al subir. No en pantallas angostas. */
const mapCol = document.querySelector(".maprow .mapcol"), mapPh = document.createElement("div");
mapPh.className = "mapcol mapph";
mapPh.textContent = "El mapa está en la ventana de la esquina mientras recorre la página.";
const anchoMapa = matchMedia("(min-width: 901px)");
let mapaFijo = (() => { try { return localStorage.getItem("mesa.mapaFijo") === "1"; } catch { return false; } })();
let mapaPasado = false;   // la sección del mapa quedó arriba, fuera de la pantalla
function ubicarMapa() {
  const acoplar = mapaFijo && mapaPasado && anchoMapa.matches;
  if (acoplar && mapCol.parentNode !== $("#mapdock .dock-body")) { mapCol.replaceWith(mapPh); $("#mapdock .dock-body").append(mapCol); }
  if (!acoplar && mapPh.isConnected) mapPh.replaceWith(mapCol);
  $("#mapdock").hidden = !acoplar;
  $("#map-pin").setAttribute("aria-pressed", String(mapaFijo));
  $("#map-pin").textContent = mapaFijo ? "Mapa fijo al bajar ✓" : "Fijar mapa al bajar";
}
function fijarMapa(v) {
  mapaFijo = v;
  try { localStorage.setItem("mesa.mapaFijo", v ? "1" : "0"); } catch { /* sin almacenamiento: dura hasta recargar */ }
  ubicarMapa();
}
new IntersectionObserver(([e]) => { mapaPasado = !e.isIntersecting && e.boundingClientRect.top < 0; ubicarMapa(); },
  {rootMargin: "-140px 0px 0px 0px"}).observe(document.querySelector(".maprow"));
anchoMapa.addEventListener("change", ubicarMapa);
$("#map-pin").onclick = () => fijarMapa(!mapaFijo);
$("#dock-soltar").onclick = () => fijarMapa(false);
$("#dock-ir").onclick = () => $("#mapa").scrollIntoView({behavior: reduceMotion ? "auto" : "smooth"});
ubicarMapa();

/* ── aviso de licencia (app instalada): desde 10 días antes del vencimiento ─────────────── */
fetch("/api/licencia", {cache: "no-store"}).then(r => r.json()).then(e => {
  if (!e.requerida || !e.aviso) return;
  const f = new Date(e.vence + "T12:00:00").toLocaleDateString("es-PE", {day: "numeric", month: "long"});
  $("#lic-aviso").hidden = false;
  $("#lic-aviso").innerHTML = `La licencia de la Mesa vence el ${esc(f)} (${e.dias === 0 ? "hoy" : e.dias === 1 ? "mañana" : `en ${e.dias} días`}).`
    + (e.contacto ? ` Para renovarla: ${esc(e.contacto)}.` : "") + ` <a href="/licencia">Ingresar un código nuevo</a>`;
}).catch(() => {});
