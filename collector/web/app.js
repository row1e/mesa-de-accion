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

/* ── filtro por región ──────────────────────────────────────────── */
const REG_SECTIONS = ["mapa", "senamhi", "indeci-sec", "com-sec", "sidpol-sec", "igp-sec", "serfor-sec", "ing-sec", "pron-sec"];
const plural = (n, one, many) => `${nf.format(n)} ${n === 1 ? one : many}`;
const regName = code => title(D?.regions?.find(r => r.id === code)?.n || "");
function makeView() {
  const R = state.region;
  if (!R) return D;
  const inR = p => typeof p === "string" && p.startsWith(R);
  const pick = obj => Object.fromEntries(Object.entries(obj).filter(([k]) => inR(k)));
  let sidpol = D.sidpol;
  if (sidpol) {
    const provs = pick(sidpol.provs), national = Object.fromEntries(sidpol.mods.map(m => [m, sidpol.months.map((_, i) => d3.sum(Object.values(provs), r => r[m][i]))]));
    sidpol = {...sidpol, provs, national};
  }
  return {...D,
    levelsByDay: Object.fromEntries(Object.entries(D.levelsByDay).map(([d, cells]) => [d, pick(cells)])),
    avisos: D.avisos.filter(a => a.regs?.includes(R)), uv: pick(D.uv),
    pronostico: D.pronostico.filter(p => p.reg === R), hidro: D.hidro.filter(h => h.reg === R),
    indeci: D.indeci.filter(i => i.reg === R), sismos: D.sismos.filter(s => s[9] === R),
    focos: D.focos.filter(f => f[5] === R), alertas: D.alertas.filter(a => a.reg === R), zonas: D.zonas.filter(z => z.reg === R),
    comunicados: (D.comunicados || []).filter(c => c.regs?.includes(R)), sidpol,
    fotosDia: (D.fotosDia || []).filter(f => f.reg === R),
    danos: D.danos ? {...D.danos, ...(D.danos.por_reg[R] || {totales: {}, nuevos: {}, seguimiento: {}, n_eventos: 0, n_nuevos: 0}),
      eventos: D.danos.eventos.filter(e => e.por_reg && e.por_reg[R]).map(e => ({...e, totales: e.por_reg[R]}))} : null,
    indeciUnlocated: D.indeci.filter(i => i.reg === R && i.clase === "reporte" && !i.prov).length};
}
function setRegion(code, {zoom: doZoom = true} = {}) {
  state.region = code || null;
  if (state.sel && state.region && !state.sel.startsWith(state.region)) state.sel = null;
  writeHash(); loadLatest();
  $("#region").value = state.region || "";
  renderAll();
  if (doZoom && mapReady) {
    const f = state.region && V.geo.deps.features.find(f => f.properties.id === state.region);
    f ? zoomToFeature(f) : zoomTo(d3.zoomIdentity);
  }
}
function renderRegionBar() {
  const sel = $("#region");
  if (sel.options.length <= 1) sel.insertAdjacentHTML("beforeend", D.regions.map(r => `<option value="${r.id}">${esc(title(r.n))}</option>`).join(""));
  sel.value = state.region || "";
  const R = state.region;
  document.body.classList.toggle("filtered", !!R);
  $("#reg-clear").hidden = !R;
  const lv = Object.values(V.levelsByDay[D.snapshot] || {});
  $("#reg-summary").innerHTML = R
    ? `Mostrando solo <b>${esc(regName(R))}</b> · ${plural(lv.length, "provincia bajo aviso", "provincias bajo aviso")} hoy · ${plural(V.indeci.filter(i => i.clase === "reporte").length, "reporte INDECI", "reportes INDECI")} · ${plural(V.alertas.length, "alerta de incendio", "alertas de incendio")}. El estado de las fuentes y ENFEN son nacionales.`
    : `Mostrando las 25 regiones. Elija una para filtrar mapa, avisos, emergencias, denuncias y comunicados.`;
  renderFichaPicker();
  REG_SECTIONS.forEach(id => {
    const h2 = document.querySelector(`#${id} h2`); if (!h2) return;
    let tag = h2.querySelector(".reg-tag");
    if (!tag) { tag = document.createElement("span"); tag.className = "reg-tag"; h2.append(tag); }
    tag.textContent = R ? ` · ${regName(R)}` : "";
  });
}
function renderFichaPicker() {
  const sel = $("#ficha-prov"), feats = D.geo.provs.features.filter(f => !state.region || f.properties.id.startsWith(state.region));
  const byDep = d3.groups(feats.map(f => f.properties).sort((a, b) => a.d.localeCompare(b.d) || a.n.localeCompare(b.n)), p => p.d);
  const opt = p => `<option value="${p.id}">${esc(title(p.n))}</option>`;
  const depOpt = r => `<option value="${r.id}">${esc(title(r.n))} · departamento completo</option>`;
  const regs = D.regions.filter(r => !state.region || r.id === state.region);
  sel.innerHTML = `<option value="">Elegir departamento o provincia…</option>`
    + `<optgroup label="Departamentos">${regs.map(depOpt).join("")}</optgroup>`
    + byDep.map(([d, ps]) => `<optgroup label="Provincias · ${esc(title(d))}">${ps.map(opt).join("")}</optgroup>`).join("");
  sel.value = state.sel && feats.some(f => f.properties.id === state.sel) ? state.sel : (state.region || "");
  updateFichaLinks();
}
function updateFichaLinks() {
  const id = $("#ficha-prov").value;
  for (const [el, href] of [[$("#ficha-dl"), `/ficha/${id}.pdf`], [$("#ficha-ver"), `/ficha/${id}`]]) {
    if (id) { el.href = href; el.removeAttribute("aria-disabled"); if (el.id === "ficha-dl") el.setAttribute("download", ""); }
    else { el.removeAttribute("href"); el.setAttribute("aria-disabled", "true"); }
  }
}
$("#ficha-prov").addEventListener("change", e => {
  updateFichaLinks();
  if (e.target.value.length === 4) { state.sel = e.target.value; renderMap(); renderRail(); }
});
$("#ficha-dl").addEventListener("click", () => toast("Generando la ficha PDF… (unos segundos)"));
$("#region").addEventListener("change", e => setRegion(e.target.value));
$("#reg-clear").addEventListener("click", () => setRegion(null));
const tip = $("#tip");
const showTip = (e, html) => { tip.innerHTML = html; tip.hidden = false; moveTip(e); };
const moveTip = e => { const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8); tip.style.left = x + "px"; tip.style.top = (e.clientY + 14) + "px"; };
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
    const j = await r.json();
    H = j.sources;
    const sig = sigOf(H);
    if (sig !== lastSig) { lastSig = sig; await loadSnapshot(); loadLatest(); } else { renderHealth(); if (LAT.req) renderLatest(); }
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
function renderHealth() {
  const ok = H.filter(h => h.status === "ok").length;
  $("#m-src").textContent = `${ok} de ${H.length}`;
  const newest = Math.max(...H.map(h => h.last_ok || 0));
  $("#m-built").innerHTML = newest ? `${stamp(newest)} · ${ago(newest)}` : "—";
  $("#m-built").title = stampFull(newest);
  $("#board").innerHTML = H.map(h => `<tr>
    <td class="org">${esc(h.org)}</td>
    <td class="pick" data-pick="${h.id}" title="Ver lo último recibido de esta fuente">${esc(h.name)}<div class="muted sub" title="En la hoja: ${esc(h.sheet)}">${esc(h.via)} · ${esc(h.geo)}</div></td>
    <td><span class="chip st-${h.status}">${STATUS[h.status] || h.status}</span></td>
    <td class="num when">${when(h.last_ok)}${h.failures && h.last_error ? `<div class="sub bad">último error: ${stamp(h.last_error)}</div>` : ""}<div class="muted sub">${every(h.interval_s)} · próxima ${h.next_run ? stamp(h.next_run).split(" ").pop() : "—"} (${until(h.next_run)})</div></td>
    <td class="num">${h.items != null ? nf.format(h.items) : "—"}</td>
    <td class="msg ${h.failures ? "bad" : "muted"}">${esc(h.message || "")}</td>
    <td><button type="button" class="rbtn" data-src="${h.id}" aria-label="Actualizar ${esc(h.name)}" ${h.status === "actualizando" ? "disabled" : ""}>↻</button></td></tr>`).join("");
}
$("#board").addEventListener("click", e => {
  const b = e.target.closest(".rbtn"); if (b) return refresh(b.dataset.src, b);
  const p = e.target.closest("[data-pick]"); if (p) setLatSource(p.dataset.pick, true);
});

/* ── mapa ───────────────────────────────────────────────────────── */
const svg = d3.select("#map"), W = 640, H_ = 860;
svg.attr("viewBox", `0 0 ${W} ${H_}`);
let proj, path, gGeo, gProv, gDep, gPts, zoom, zt = d3.zoomIdentity;
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
  gGeo = svg.append("g"); gProv = gGeo.append("g"); gDep = gGeo.append("g"); gPts = svg.append("g");
  zoom = d3.zoom().scaleExtent([1, 14]).translateExtent([[-W * .15, -H_ * .15], [W * 1.15, H_ * 1.15]])
    .filter(e => e.type === "wheel" ? (e.ctrlKey || e.metaKey) : e.type === "touchstart" ? e.touches.length > 1 : !e.button)
    .on("zoom", e => { zt = e.transform; gGeo.attr("transform", zt); placePoints(); hideTip(); });
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
  $("#layers").addEventListener("change", e => { state.layers[e.target.dataset.l] = e.target.checked; renderPoints(); });
}

const state = { mode: "avisos", day: null, uvDay: 0, sel: null, region: (location.hash.match(/r=(\d{2})/) || [])[1] || null, latSource: (location.hash.match(/f=([a-z_]+)/) || [])[1] || null,
  layers: { indeci: true, alertas: true, sismos: true, hidro: true, zonas: false, focos: false } };
let avisoDays = [], uvDays = [];
const uvBins = [3, 6, 8, 11];
const uvColor = v => v == null ? css("--land") : css(["--uv0","--uv1","--uv2","--uv3","--uv4"][d3.bisectRight(uvBins, v)]);
function recentSismos() { const lim = d3.timeDay.offset(new Date(V.snapshot + "T12:00:00"), -30).toISOString().slice(0,10); return V.sismos.filter(s => s[0] >= lim); }
const layerDefs = [
  { id: "indeci", label: "Emergencias INDECI", sw: `<i class="sw sq" style="background:var(--indeci)"></i>`, n: () => new Set(V.indeci.filter(i => i.prov).map(i => i.prov)).size, unit: "prov." },
  { id: "alertas", label: "Alertas de incendio", sw: `<i class="sw" style="background:var(--fuego)"></i>`, n: () => V.alertas.length },
  { id: "sismos", label: "Sismos · 30 días", sw: `<i class="sw" style="border:2px solid var(--sismo)"></i>`, n: () => recentSismos().length },
  { id: "hidro", label: "Estaciones hidrológicas", sw: `<i class="sw" style="background:var(--hidro)"></i>`, n: () => V.hidro.length },
  { id: "zonas", label: "Zonas críticas INGEMMET", sw: `<i class="sw tri"></i>`, n: () => V.zonas.length },
  { id: "focos", label: "Focos de calor 24 h", sw: `<i class="sw" style="background:var(--fuego);opacity:.45"></i>`, n: () => V.focos.length },
];
function renderLayers() {
  $("#layers").innerHTML = layerDefs.map(l => `<label><input type="checkbox" data-l="${l.id}" ${state.layers[l.id] ? "checked" : ""}>${l.sw}<span>${l.label}</span><span class="muted num">${nf.format(l.n())}${l.unit ? " " + l.unit : ""}</span></label>`).join("");
}
function provTip(p) {
  if (state.mode === "uv") { const u = V.uv[p.id]; return `<b>${title(p.n)}</b> · ${title(p.d)}<br>${u ? `UV ${u.v[state.uvDay]} (pico ${u.h[state.uvDay]})` : "Sin zona UV"}`; }
  const c = (V.levelsByDay[state.day] || {})[p.id];
  return `<b>${title(p.n)}</b> · ${title(p.d)}<br>${c ? `Nivel ${c.m} · avisos ${c.a.map(x => "#" + x[0]).join(", ")}` : "Sin aviso nivel 2+"}`;
}
function renderControls() {
  seg($("#mode"), [["avisos","Avisos SENAMHI"],["uv","Índice UV"]], state.mode, v => { state.mode = v; renderMapAll(); });
  if (state.mode === "avisos") seg($("#days"), avisoDays.map(d => [d, fmtDay(d)]), state.day, v => { state.day = v; renderMapAll(); });
  else seg($("#days"), uvDays.map((d, i) => [i, fmtDay(d)]), state.uvDay, v => { state.uvDay = +v; renderMapAll(); });
}
function renderMap() {
  const lv = V.levelsByDay[state.day] || {};
  gProv.selectAll("path").attr("fill", f => {
    if (state.mode === "uv") { const u = V.uv[f.properties.id]; return uvColor(u ? u.v[state.uvDay] : null); }
    const c = lv[f.properties.id]; return c ? css("--n" + c.m) : css("--land");
  }).classed("sel", f => f.properties.id === state.sel)
    .classed("out", f => !!state.region && !f.properties.id.startsWith(state.region));
  gDep.selectAll("path").classed("reg", f => f.properties.id === state.region);
  $("#legend").innerHTML = state.mode === "uv"
    ? ["0–2 bajo","3–5 moderado","6–7 alto","8–10 muy alto","11+ extremo"].map((l, i) => `<span><i style="background:var(--uv${i})"></i>${l}</span>`).join("") + `<span class="muted">Zonas UV sin provincia: ${V.uvUnmatched.length}</span>`
    : [2,3,4].map(n => `<span><i style="background:var(--n${n})"></i>Nivel ${n} · ${["","","amarillo","naranja","rojo"][n]}</span>`).join("") + `<span><i style="background:var(--land)"></i>Sin aviso o nivel 1</span>`;
}
function showProvince() {   // lleva el bloque de la provincia al tope del panel lateral (sin mover la página)
  const side = $("#side"), el = $("#pdetail");
  if (side && el) side.scrollTo({top: el.offsetTop - side.offsetTop - 8, behavior: reduceMotion ? "auto" : "smooth"});
  if ($("#ficha-prov") && state.sel) { $("#ficha-prov").value = state.sel; updateFichaLinks(); }
}

/* ── ficha de detalle ───────────────────────────────────────────── */
const ORG = { igp: "IGP · Centro Sismológico Nacional", serfor: "SERFOR · Monitoreo satelital", ingemmet: "INGEMMET · Perú Alerta",
  senamhi_hidro: "SENAMHI · Hidrología", senamhi_avisos: "SENAMHI · Avisos meteorológicos", indeci: "INDECI · COEN", firms: "NASA FIRMS",
  enfen: "ENFEN · Comisión Multisectorial", com_pnp: "PNP · Comunicados (gob.pe)", com_provias: "PROVIAS Nacional · Notas de prensa (gob.pe)",
  com_mtc: "MTC · Notas de prensa (gob.pe)", com_mininter: "MININTER · Notas de prensa (gob.pe)" };
const refId = r => r && `${r.source}|${r.kind}|${r.key}`;
let detailReq = 0;
function closeDetail() { state.pick = null; $("#detail").hidden = true; $("#detail").innerHTML = ""; markPicked(); }
function markPicked() { gPts?.selectAll(".pt").classed("picked", function () { return this.__ref && refId(this.__ref) === refId(state.pick); }); }
function openDetail(ref, fresh = false) {
  state.pick = ref; markPicked();
  const box = $("#detail"), n = ++detailReq;
  box.hidden = false;
  box.innerHTML = `<div class="dh"><div><div class="src-org">${esc(ORG[ref.source] || ref.source)}</div><h3>Consultando la fuente…</h3></div><button type="button" class="x" aria-label="Cerrar ficha">×</button></div>`;
  $("#side").scrollTop = 0;
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
    <dl>${(d.facts || []).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(fmtVal(v))}</dd>`).join("")}</dl>
    ${d.text ? `<div class="body">${esc(d.text)}</div>` : ""}
    ${d.fotos?.length ? `<div><div class="gal">${d.fotos.map((f, i) => phHtml(f, i)).join("")}</div><p class="credit">${d.fotos.length} foto${d.fotos.length > 1 ? "s" : ""} del anexo fotográfico del reporte · Crédito: INDECI / COER</p></div>` : ""}
    ${d.mapa ? `<div class="mapa"><button type="button" class="ph" data-mapa><img src="${esc(d.mapa.url)}" alt="Mapa de ubicación" loading="lazy"></button><span>Mapa de ubicación del reporte. ${esc(d.mapa.nota)}</span></div>` : ""}
    ${d.sat ? `<div class="satbox" id="satbox"></div>` : ""}
    ${d.images?.length ? `<div class="imgs">${d.images.map(i => `<a href="${esc(i.url)}" target="_blank" rel="noopener"><img src="${esc(i.url)}" alt="${esc(i.label)}" loading="lazy"><span>${esc(i.label)}</span></a>`).join("")}</div>` : ""}
    ${d.pdf_text ? `<details><summary>Texto del reporte PDF (ubicación, daños, contexto)</summary><pre>${esc(d.pdf_text)}</pre></details>` : ""}
    ${fields.length ? `<details><summary>Todos los campos (${fields.length})</summary><table>${fields.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${isUrl(v) ? `<a href="${esc(v)}" target="_blank" rel="noopener">${esc(v)}</a>` : esc(fmtVal(v))}</td></tr>`).join("")}</table></details>` : ""}
    <div class="meta"><span>Recibido ${when(d.first_seen)}</span>${d.fetched_at ? `<span>Fuente consultada ${when(d.fetched_at)}</span>` : ""}<button type="button" data-fresh>Volver a consultar la fuente</button><span class="mono">${esc(d.key)}</span></div>`;
  $("#detail [data-fresh]").onclick = () => openDetail({source: d.source, kind: d.kind, key: d.key}, true);
  if (d.sat) loadSat({...d.sat, base: d.sat.date, layer: "auto"});
  const credit = "Foto: INDECI / COER — anexo fotográfico del reporte";
  $("#detail .gal")?.addEventListener("click", e => { const b = e.target.closest(".ph"); if (b) openLightbox(d.fotos.map(f => ({...f, credit})), +b.dataset.i); });
  $("#detail [data-mapa]")?.addEventListener("click", () => openLightbox([{...d.mapa, caption: "Mapa de ubicación del reporte", credit: d.mapa.nota}]));
}
function openIndeciGroup(prov) {
  const items = V.indeci.filter(i => i.prov === prov), p = V.geo.provs.features.find(f => f.properties.id === prov)?.properties;
  state.pick = null; markPicked();
  const box = $("#detail"); box.hidden = false; $("#side").scrollTop = 0;
  box.innerHTML = `<div class="dh"><div><div class="src-org">${ORG.indeci} · últimas ${V.indeciWindowH} h</div><h3>${items.length} reporte${items.length > 1 ? "s" : ""} en ${esc(title(p?.n))}</h3><div class="sub">${esc(title(p?.d))} · elija uno para ver la ficha completa</div></div><button type="button" class="x" aria-label="Cerrar ficha">×</button></div>
    <ul class="plist">${items.map(i => `<li><button type="button" data-key="${esc(i._key)}"><span class="t">${esc(title(i.evento || i.titulo))} — ${esc(title(i.distrito))}</span><span class="s">${esc(title(i.tipo))} N° ${esc(i.num)} · ${new Date(i.pub).toLocaleString("es-PE", {timeZone: "America/Lima", dateStyle: "short", timeStyle: "short"})}</span></button></li>`).join("")}</ul>`;
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
    .call(on, d => `<b>Foco de calor</b><br>${title(d[3])} · ${title(d[2])}`, d => ({source: "serfor", kind: "foco", key: String(d[4])}));
  if (L.zonas) layer(V.zonas, "path", d => [d.lon, d.lat]).attr("d", d3.symbol(d3.symbolTriangle, 34))
    .attr("fill", css("--geo")).attr("stroke", css("--surface")).attr("stroke-width", .6)
    .call(on, d => `<b>Zona crítica · ${esc(d.nivel)}</b><br>${esc(d.paraje)} — ${esc(d.distrito)}, ${esc(d.provincia)}<br>${esc(d.peligros_g)}<br><span style="opacity:.7">Expuesto: ${esc(d.elemento)}</span>`, d => ({source: "ingemmet", kind: "zona_alerta", key: d._key}));
  if (L.sismos) layer(recentSismos(), "circle", d => [d[5], d[4]]).attr("r", d => Math.max(2.5, (d[2] - 2.5) * 3.2))
    .attr("fill", "none").attr("stroke", css("--sismo")).attr("stroke-width", 1.6)
    .call(on, d => `<b>Sismo M${d[2]}</b> · ${fmtDay(d[0])} ${d[1]}<br>${esc(d[6])}<br>Prof. ${d[3]} km${d[7] ? " · " + esc(d[7]) : ""}`, d => ({source: "igp", kind: "sismo", key: d[8]}));
  if (L.alertas) layer(V.alertas, "circle", d => [d.lon, d.lat]).attr("r", 3.6)
    .attr("fill", d => d.estado === "Extinguido" ? css("--surface") : css("--fuego")).attr("stroke", css("--fuego")).attr("stroke-width", 1.2)
    .call(on, d => `<b>Incendio forestal · ${esc(d.estado)}</b><br>${title(d.dist)}, ${title(d.prov)} · ${title(d.dep)}<br>${esc(d.fecha)} ${esc(d.hora || "")} · ${esc(d.cod || "")}`, d => ({source: "serfor", kind: "alerta", key: d._key}));
  if (L.hidro) layer(V.hidro, "circle", d => [d.lon, d.lat]).attr("r", 6)
    .attr("fill", d => css("--n" + Math.min(4, lvlNum(d.color_text)))).attr("stroke", css("--hidro")).attr("stroke-width", 2.4)
    .call(on, d => `<b>${esc(d.titulo)}</b><br>${esc(d.color_text)} · ${esc(d.fecha_hora?.slice(0,10))}<br>${title(d.nom_distrito)}, ${title(d.nom_provincia)} · ${title(d.nom_departamento)}`, d => ({source: "senamhi_hidro", kind: "aviso_estacion", key: d._key}));
  if (L.indeci) layer([...d3.group(V.indeci.filter(i => i.prov), i => i.prov)], "rect", ([p]) => V.provCentroids[p])
    .attr("x", -5).attr("y", -5).attr("width", 10).attr("height", 10).attr("fill", css("--indeci")).attr("stroke", css("--surface")).attr("stroke-width", 1.2)
    .call(on, ([p, items]) => `<b>INDECI · ${items.length} reporte${items.length > 1 ? "s" : ""}</b><br>` + items.slice(0, 5).map(i => `${esc(title(i.evento))} — ${esc(title(i.distrito))}`).join("<br>") + `<br><span style="opacity:.7">Ubicado en la provincia (centroide)</span>`, ([p]) => ({group: p, source: "indeci", kind: "group", key: p}));
  placePoints(); markPicked();
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
      <dt>Daños ${D.danos?.dias || 7} días</dt><dd>${D.danos?.por_prov?.[p.id] ? `${danosChips(D.danos.por_prov[p.id].totales, 4)} <span class="muted">(${D.danos.por_prov[p.id].n_eventos} eventos)</span>` : "—"}</dd></dl>
      ${sidpolBlock(p.id)}
      <button type="button" class="zbtn" data-dep="${p.id.slice(0, 2)}">Acercar a ${title(p.d)}</button></div>`;
  } else provHtml = `<p class="note">Seleccione una provincia en el mapa para ver lo que dice cada fuente sobre ella y exportar su ficha en PDF.</p>`;
  $("#rail").innerHTML = provHtml + html;
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
function renderThesis() {
  const today = V.levelsByDay[V.snapshot] || {}, vals = Object.values(today);
  const reps = V.indeci.filter(i => i.clase === "reporte").length;
  $("#thesis").innerHTML = [
    [`${vals.length}`, `provincias${state.region ? ` de ${esc(regName(state.region))}` : ""} bajo aviso SENAMHI (nivel 2 o más) hoy, ${vals.filter(c => c.m === 4).length} en nivel 4 rojo.`],
    [`${reps}`, `reportes de emergencia INDECI en las últimas ${V.indeciWindowH} h, con distrito y provincia.`],
    [nf.format(V.focos.length), `focos de calor en 24 h y ${V.alertas.length} alertas de incendio forestal con estado y ubigeo distrital (SERFOR).`],
    [V.enfen?.ultimo_comunicado?.estado ? "Alerta" : "—", `ENFEN: ${esc(V.enfen?.ultimo_comunicado?.estado || "sin dato")} (comunicado N° ${V.enfen?.ultimo_comunicado?.numero ?? "—"}).`],
  ].map(([b,p]) => `<div><div class="big num">${b}</div><p>${p}</p></div>`).join("");
  $("#m-snap").textContent = fmtDay(V.snapshot) + " " + V.snapshot.slice(0,4);
}
function renderEnfen() {
  const uc = V.enfen?.ultimo_comunicado || {};
  $("#enfen").innerHTML = `<div class="state"><span>Estado del sistema de alerta</span><strong>${esc(uc.estado || "Sin dato")}</strong><span>Comunicado oficial N° ${uc.numero ?? "—"}-${uc.anio ?? ""} · ${esc(uc.fecha || "")}</span></div>
    <div class="body">${(uc.resumen || "").split(/\n\s*\n/).slice(0, 3).map(p => `<p>${esc(p.replace(/\s*\n\s*/g, " "))}</p>`).join("")}
    ${uc.url ? `<p class="note"><a href="${esc(uc.url)}" target="_blank" rel="noopener">Abrir el PDF del comunicado</a></p>` : ""}</div>`;
}
function renderAvisos() {
  $("#avisos").innerHTML = V.avisos.map(a => `<tr><td class="num">${a.nro}</td><td>${lvlChip(lvlNum(a.nivel))}</td><td>${esc(title(a.titulo))}<div class="muted" style="font-size:12px">${a.dptos_texto.length} departamentos · ${a.estado}</div></td><td class="num" style="white-space:nowrap">${fmtDay(a.inicio)} → ${fmtDay(a.fin)}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">Sin avisos vigentes.</td></tr>`;
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
    if (i.clase === "reporte") return `<li><span class="hr">${hr}</span><div><div class="kind">${esc(title(i.tipo))} N° ${esc(i.num)}${i.seq ? ` · reporte ${esc(i.seq)}` : ""}${i.fotos ? `<span class="fcount">${i.fotos} foto${i.fotos > 1 ? "s" : ""}</span>` : ""}${isNew ? ` <span class="new">nuevo</span>` : ""}</div><div class="ev"><a href="${esc(i.link)}" target="_blank" rel="noopener">${esc(title(i.evento))}</a>${danosChips(i.danos)}</div><div class="loc">${esc(title(i.distrito))}${i.provincia ? ", " + esc(title(i.provincia)) : ""} · ${esc(title(i.dpto))}${i.prov ? ` <span class="mono muted">${i.prov}</span>` : ""}</div></div></li>`;
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
  s.append("g").selectAll("circle").data(V.sismos).join("circle").attr("cx", d => x(new Date(d[0] + "T" + d[1]))).attr("cy", d => y(d[2]))
    .attr("r", d => Math.max(1.6, (d[2] - 2.5) * 1.8)).attr("fill", css("--sismo")).attr("fill-opacity", .28).attr("stroke", css("--sismo")).attr("stroke-width", .8)
    .on("mousemove", (e, d) => showTip(e, `<b>M${d[2]}</b> · ${fmtDay(d[0])} ${d[1]}<br>${esc(d[6])}`)).on("mouseleave", hideTip);
  s.append("g").selectAll("text").data(V.sismos.filter(d => d[2] >= 6)).join("text").attr("x", d => x(new Date(d[0] + "T" + d[1]))).attr("y", d => y(d[2]) - 12).attr("text-anchor", "middle").style("font-weight", 600).text(d => "M" + d[2]);
  $("#sismos").innerHTML = V.sismos.filter(d => d[2] >= 5).sort((a, b) => (b[0] + b[1]).localeCompare(a[0] + a[1])).map(d => `<tr><td class="num" style="white-space:nowrap">${fmtDay(d[0])} <span class="muted">${d[1]}</span></td><td class="num"><b>${d[2]}</b></td><td class="num">${d[3]} km</td><td>${esc(d[6])}</td></tr>`).join("");
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
function renderZonas() {
  const q = ($("#zq").value || "").toLowerCase();
  const rows = V.zonas.filter(z => !q || [z.region, z.provincia, z.distrito, z.peligros_g, z.paraje].join(" ").toLowerCase().includes(q));
  $("#zonas").innerHTML = rows.map(z => `<tr><td>${lvlChip(lvlNum(z.nivel))}</td><td>${esc(z.region)}</td><td>${esc(z.provincia)} · ${esc(z.distrito)}</td><td>${esc(z.paraje)}</td><td>${esc(z.peligros_g)}</td><td class="muted">${esc(z.elemento)}</td></tr>`).join("");
  const avs = [...new Set(V.zonas.map(z => z.nro_aviso))];
  $("#zonas-note").textContent = `${rows.length} de ${V.zonas.length} zonas${avs.length ? `, del cruce de INGEMMET con el aviso SENAMHI #${avs.join(", #")}` : ""}.`;
}
$("#zq").addEventListener("input", () => D && renderZonas());
let pd = [], pdays = [];
function renderPron() {
  pd = d3.groups(V.pronostico, p => p.ciudad + "|" + p.departamento);
  pdays = [...new Set(V.pronostico.map(p => p.dia))].slice(0, 3);
  pdays.forEach((d, i) => $("#pd" + i).textContent = d.replace(/^(\w+),\s*(\d+) de (\w+)/, (m, a, b) => `${a.slice(0,3)} ${b}`));
  filterPron();
  $("#hidro").innerHTML = V.hidro.map(h => `<tr><td>${lvlChip(Math.min(4, lvlNum(h.color_text)))}</td><td><b>${esc(title(h.nom_estacion))}</b><div class="muted" style="font-size:12px">${esc(title(h.titulo))}</div></td><td>${esc(title(h.nom_distrito))}, ${esc(title(h.nom_provincia))}<div class="muted" style="font-size:12px">${esc(title(h.nom_departamento))} · cuenca ${esc(title(h.nom_cuenca))}</div></td></tr>`).join("") || `<tr><td colspan="3" class="muted">Sin avisos hidrológicos vigentes.</td></tr>`;
  $("#uv-note").textContent = `Índice UV: ${Object.keys(V.uv).length} provincias con valor (capa “Índice UV” del mapa). ${V.uvUnmatched.length} zonas UV usan códigos que no son provincias INEI.`;
}
function filterPron() {
  const q = ($("#pq").value || "").toLowerCase();
  $("#pron").innerHTML = pd.filter(([k]) => !q || k.toLowerCase().includes(q)).map(([k, v]) => {
    const [c, dp] = k.split("|"), by = Object.fromEntries(v.map(p => [p.dia, p]));
    return `<tr><td>${esc(title(c))}</td><td class="muted">${esc(title(dp))}</td>${pdays.map(d => by[d] ? `<td class="num" title="${esc(by[d].descripcion)}">${by[d].tmax}° <span class="muted">${by[d].tmin}°</span></td>` : "<td>—</td>").join("")}</tr>`;
  }).join("");
}
$("#pq").addEventListener("input", () => D && filterPron());

/* ── últimos datos recibidos (por fuente) ───────────────────────── */
const SHORT = {senamhi_avisos: "SENAMHI avisos", senamhi_uv: "SENAMHI UV", senamhi_pronostico: "SENAMHI pronóstico", senamhi_hidro: "SENAMHI hidrología",
  indeci: "INDECI", indeci_fotos: "INDECI fotos", igp: "IGP sismos", serfor: "SERFOR incendios", ingemmet: "INGEMMET", enfen: "ENFEN", firms: "NASA FIRMS",
  com_pnp: "PNP comunicados", com_provias: "PROVIAS notas", com_mtc: "MTC notas", com_mininter: "MININTER notas",
  sidpol: "SIDPOL denuncias", provias: "PROVIAS servidor"};
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
  try {
    const d = await (await fetch(`/api/latest?${q}`, {cache: "no-store"})).json();
    if (n !== LAT.req) return;
    LAT.rows = append ? LAT.rows.concat(d.rows) : d.rows; LAT.counts = d.counts24h || {}; LAT.more = d.more; LAT.next = d.next_before;
    renderLatest();
  } catch { $("#latest").innerHTML = `<li class="empty">El colector no respondió.</li>`; }
}
function setLatSource(src, scroll = false) {
  state.latSource = src || null; writeHash(); loadLatest();
  if (scroll) $("#latest-sec").scrollIntoView({behavior: reduceMotion ? "auto" : "smooth"});
}
function renderLatest() {
  const hs = Object.fromEntries(H.map(h => [h.id, h]));
  const ids = H.map(h => h.id).filter(id => id !== "indeci_fotos");
  $("#lat-src").innerHTML = [["", "Todas", Object.values(LAT.counts).reduce((a, b) => a + b, 0)], ...ids.map(id => [id, SHORT[id] || id, LAT.counts[id]])]
    .map(([id, label, n]) => `<button type="button" role="tab" data-src="${id}" aria-pressed="${(state.latSource || "") === id}" title="${id ? esc(hs[id]?.name || "") + " · " + (STATUS[hs[id]?.status] || "") : "Todas las fuentes"}">
      ${id ? `<i class="dot ${hs[id]?.status || ""}"></i>` : ""}${esc(label)}${n != null ? `<span class="n ${n ? "hot" : ""}" title="nuevos en 24 h">${nf.format(n)}</span>` : ""}</button>`).join("");
  const s = state.latSource && hs[state.latSource];
  $("#lat-head").hidden = !s;
  if (s) $("#lat-head").innerHTML = `<span><b>${esc(s.org)} · ${esc(s.name)}</b></span>
      <span><span class="chip st-${s.status}">${STATUS[s.status] || s.status}</span> · última lectura ${when(s.last_run)} · último éxito ${when(s.last_ok)} · ${every(s.interval_s)}, próxima ${h_m(s.next_run)} (${until(s.next_run)})</span>
      <button type="button" class="rbtn" data-src="${s.id}" aria-label="Actualizar ${esc(s.name)}">↻</button>
      <span class="msg ${s.failures ? "bad" : ""}">${esc(s.message || "")} · ${plural(LAT.counts[s.id] || 0, "registro nuevo", "registros nuevos")} en 24 h${state.region ? ` en ${esc(regName(state.region))}` : ""}.</span>`;
  $("#latest").innerHTML = LAT.rows.map((r, i) => `<li><button type="button" class="row ${r.detail || r.group ? "" : "nod"}" data-i="${i}">
      <span class="rt">${ago(r.received)}<small>${new Date(r.received * 1000).toLocaleString("es-PE", {timeZone: "America/Lima", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit"})}</small></span>
      <span class="src">${esc(SHORT[r.source] || r.source)}${r.imagen ? `<img class="lthumb" src="${esc(r.imagen)}" alt="" loading="lazy">` : ""}</span>
      <span class="body"><span class="tt">${r.level ? lvlChip(r.level) : ""}${esc(r.title)}${danosChips(r.danos)}${r.backfill ? `<span class="bf" title="Llegó en la carga inicial del colector: es histórico, no nuevo">carga inicial</span>` : ""}</span>
        ${r.subtitle ? `<span class="ss" style="display:block">${esc(r.subtitle)}</span>` : ""}
        <span class="mm" style="display:block">${[r.place, r.event_time && `${r.kind === "noticia" || r.kind === "comunicado" ? "publicado" : r.kind === "aviso" ? "emitido" : "ocurrido"} ${fmtEvent(r.event_time)}`, r.group ? "ver cada uno →" : ""].filter(Boolean).map(esc).join(" · ")}</span></span></button></li>`).join("")
    || `<li class="empty">${state.latSource === "provias" ? "El servidor de emergencias viales de PROVIAS no acepta conexión. Sus notas de prensa llegan por “PROVIAS notas”." : `Sin registros en los últimos 7 días${state.region ? ` para ${esc(regName(state.region))}` : ""}.`}</li>`;
  $("#lat-more").hidden = !LAT.more;
}
$("#lat-src").addEventListener("click", e => { const b = e.target.closest("button"); if (b) setLatSource(b.dataset.src); });
$("#lat-head").addEventListener("click", e => { const b = e.target.closest(".rbtn"); if (b) { refresh(b.dataset.src, b); setTimeout(() => loadLatest(), 6000); } });
$("#lat-more").addEventListener("click", () => loadLatest(true));
$("#latest").addEventListener("click", e => {
  const b = e.target.closest(".row"); if (!b) return;
  const r = LAT.rows[+b.dataset.i];
  if (r.group) return setLatSource(r.source);
  if (!r.detail) return;
  $("#mapa").scrollIntoView({behavior: reduceMotion ? "auto" : "smooth"});
  openDetail({source: r.source, kind: r.kind, key: r.key});
});

/* ── fotos: visor, tira del día, galería en la ficha ─────────────── */
const LB = {list: [], i: 0};
const fotoTitle = f => f.caption || `${title(f.evento || "Emergencia")}${f.distrito ? " — " + title(f.distrito) : ""}`;
function lbShow() {
  const f = LB.list[LB.i];
  $("#lb-img").src = f.url; $("#lb-img").alt = fotoTitle(f);
  $("#lb-cap").innerHTML = `${esc(fotoTitle(f))}<span class="cr">${esc(f.credit || "Foto: INDECI / COER — anexo fotográfico del reporte")}${f.fecha ? " · " + esc(f.fecha) : ""} · ${LB.i + 1} de ${LB.list.length}</span>`
    + (f.key ? `<button type="button" data-open="${esc(f.key)}">Ver el reporte completo</button>` : "");
  $("#lb").querySelectorAll(".lb-nav").forEach(b => b.hidden = LB.list.length < 2);
}
function openLightbox(list, i = 0) { LB.list = list; LB.i = i; lbShow(); $("#lb").hidden = false; $("#lb .lb-x").focus(); }
function closeLightbox() { $("#lb").hidden = true; $("#lb-img").removeAttribute("src"); }
$("#lb").addEventListener("click", e => {
  if (e.target.closest(".lb-x") || e.target === $("#lb")) return closeLightbox();
  const nav = e.target.closest(".lb-nav");
  if (nav) { LB.i = (LB.i + (nav.classList.contains("next") ? 1 : -1) + LB.list.length) % LB.list.length; return lbShow(); }
  const open = e.target.closest("[data-open]");
  if (open) { closeLightbox(); $("#mapa").scrollIntoView({behavior: reduceMotion ? "auto" : "smooth"}); openDetail({source: "indeci", kind: "item", key: open.dataset.open}); }
});
addEventListener("keydown", e => {
  if ($("#lb").hidden) return;
  if (e.key === "Escape") { e.stopImmediatePropagation(); closeLightbox(); }
  if (e.key === "ArrowRight" || e.key === "ArrowLeft") { LB.i = (LB.i + (e.key === "ArrowRight" ? 1 : -1) + LB.list.length) % LB.list.length; lbShow(); }
}, true);
const phHtml = (f, i, {reg = false} = {}) => `<button type="button" class="ph" data-i="${i}" aria-label="Ampliar: ${esc(fotoTitle(f))}">
  ${reg && f.reg ? `<span class="reg">${esc(regName(f.reg))}</span>` : ""}<img src="${esc(f.url)}" alt="" loading="lazy">
  <span class="cap"><b>${esc(fotoTitle(f))}</b>${esc([f.fecha, f.tipo && f.num ? `${title(f.tipo)} N° ${f.num}` : ""].filter(Boolean).join(" · "))}</span></button>`;
function renderStrip() {
  const F = V.fotosDia || [];
  $("#ind-strip").innerHTML = F.map((f, i) => phHtml(f, i, {reg: !state.region})).join("")
    || `<p class="muted" style="margin:0">${state.region ? `Sin fotos de campo de ${esc(regName(state.region))} en las últimas ${V.indeciWindowH} h.` : "Aún no hay fotos procesadas."}</p>`;
  $("#ind-strip").onclick = e => { const b = e.target.closest(".ph"); if (b) openLightbox(F, +b.dataset.i); };
  const pend = V.indeci.filter(i => i.clase === "reporte" && i.fotos == null).length;
  $("#strip-note").textContent = `${F.length} fotos de ${new Set(F.map(f => f.key)).size} reportes · extraídas del anexo de cada PDF${pend ? ` · ${pend} reportes aún en cola` : ""}. Crédito: INDECI / COER.`;
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
  cultivo_afectado_ha: "ha cultivo afectado", vias_afectadas_m: "m de vía afectada", vias_destruidas_m: "m de vía destruida"};
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
function renderDanos() {
  const X = V.danos, box = $("#danos");
  if (!X) { box.hidden = true; return; }
  box.hidden = false;
  const top = [...X.eventos].sort((a, b) => ((b.totales.personas_damnificadas || 0) + (b.totales.personas_afectadas || 0)) - ((a.totales.personas_damnificadas || 0) + (a.totales.personas_afectadas || 0))).slice(0, 8);
  box.innerHTML = `<div class="danos-head"><h3 style="margin:0">Daños reportados · últimos ${X.dias} días${state.region ? ` · ${esc(regName(state.region))}` : ""}</h3>
      <span class="note" style="margin:0">${X.n_eventos} eventos con cifras · se cuenta el reporte más reciente de cada evento (sin duplicar actualizaciones)</span></div>
    <div class="danos-cols">
      <div class="danos-col"><h4><b>${X.n_nuevos}</b> ${X.n_nuevos === 1 ? "evento nuevo" : "eventos nuevos"} · ocurridos desde ${fmtDay(X.desde)}</h4>${danosFigs(X.nuevos)}</div>
      <div class="danos-col"><h4><b>${X.n_eventos - X.n_nuevos}</b> ${X.n_eventos - X.n_nuevos === 1 ? "evento anterior" : "eventos anteriores"} aún con reportes · cifras acumuladas</h4>${danosFigs(X.seguimiento)}</div>
    </div>
    ${top.length ? `<ul class="devents">${top.map(e => `<li><button type="button" data-key="${esc(e.item_key)}">
      <span class="t">${esc(title(e.evento))} — ${esc(title(e.distrito))}${e.multi_region ? ` <span class="muted">(${Object.keys(e.por_reg).length} regiones)</span>` : ""}</span>
      <span class="n">${danosChips(e.totales, 3)}</span>
      <span class="s">${e.nuevo ? "Nuevo" : "En seguimiento"} · ocurrió ${e.ocurrencia ? fmtDay(e.ocurrencia) : "—"} · ${esc(title(e.tipo))} N° ${esc(e.num)}${e.seq ? ` (reporte ${esc(e.seq)})` : ""}${e.actualizado ? ` · cifras al ${esc(e.actualizado)}` : ""}</span></button></li>`).join("")}</ul>` : ""}`;
  box.querySelectorAll(".devents button").forEach(b => b.onclick = () => { $("#mapa").scrollIntoView({behavior: reduceMotion ? "auto" : "smooth"}); openDetail({source: "indeci", kind: "item", key: b.dataset.key}); });
}

/* ── comunicados gob.pe ─────────────────────────────────────────── */
const INST = {pnp: "PNP", pvn: "PROVIAS", mtc: "MTC", mininter: "MININTER"};
let comInst = "all";
function renderComunicados() {
  const C = V.comunicados || [];
  seg($("#com-filters"), [["all", `Todas ${C.length}`], ...Object.entries(INST).map(([k, l]) => [k, `${l} ${C.filter(c => c.inst === k).length}`])], comInst, v => { comInst = v; renderComunicados(); });
  const rows = C.filter(c => comInst === "all" || c.inst === comInst);
  const boot = d3.min(C, c => c._first_seen) || 0;   // el primer lote leído no cuenta como "nuevo"
  $("#com").innerHTML = rows.map(c => {
    const isNew = c._first_seen > boot + 120 && Date.now() / 1000 - c._first_seen < 3600;
    return `<li class="${c.imagen ? "withimg" : ""}"><span class="hr">${c.fecha ? fmtDay(c.fecha) : esc(c.fecha_texto)}</span>${c.imagen ? `<a class="cthumb" href="${esc(c.url)}" target="_blank" rel="noopener" tabindex="-1"><img src="${esc(c.imagen)}" alt="" loading="lazy"></a>` : ""}<div>
      <div class="inst">${esc(c.inst_nombre)}${isNew ? ` <span class="new">nuevo</span>` : ""}</div>
      <div class="ev"><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.titulo)}</a></div>
      ${c.descripcion && c.descripcion !== c.titulo ? `<div class="d">${esc(c.descripcion)}</div>` : ""}
      <details data-key="${esc(c._key)}" data-src="${esc(c.source || "com_" + c.inst)}"><summary>Leer el texto completo</summary><div class="txt">Consultando gob.pe…</div></details></div></li>`;
  }).join("") || `<li class="muted">${state.region ? `Ningún comunicado de los últimos 14 días menciona ${esc(regName(state.region))}.` : "Sin comunicados en los últimos 14 días."}</li>`;
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

/* ── SIDPOL ─────────────────────────────────────────────────────── */
const MONTH = ym => { const [y, m] = ym.split("-"); return `${MON[+m - 1]} ${y}`; };
const sumRow = (row, mods) => row ? mods.reduce((acc, m) => acc.map((v, i) => v + (row[m]?.[i] || 0)), Array(V.sidpol.months.length).fill(0)) : null;
function spark(vals, w = 110, h = 26) {
  if (!vals?.length) return "";
  const x = d3.scaleLinear([0, vals.length - 1], [2, w - 3]), y = d3.scaleLinear([0, d3.max(vals) || 1], [h - 2, 3]);
  const line = d3.line((d, i) => x(i), d => y(d))(vals), area = d3.area((d, i) => x(i), h - 2, d => y(d))(vals);
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path class="a" d="${area}"/><path class="l" d="${line}"/><circle cx="${x(vals.length - 1)}" cy="${y(vals.at(-1))}" r="2.4"/></svg>`;
}
const delta = (now, before) => { if (!before) return `<span class="muted">—</span>`; const p = Math.round((now - before) / before * 100); return `<span class="delta ${p > 0 ? "up" : p < 0 ? "down" : ""}">${p > 0 ? "+" : ""}${p} %</span>`; };
let sidMod = "Total";
function renderSidpol() {
  const S = V.sidpol;
  if (!S) { $("#sid-chart").innerHTML = `<p class="muted">Aún no se descargó el dataset.</p>`; return; }
  const mods = S.mods, last = S.months.length - 1, sel = sidMod === "Total" ? mods : [sidMod];
  seg($("#sid-mods"), [["Total", "Total"], ...mods.map(m => [m, m.replace(" e integrantes", "")])], sidMod, v => { sidMod = v; renderSidpol(); });
  const nat = sumRow(S.national, sel);
  $("#sid-h3").textContent = `${sidMod === "Total" ? "Denuncias" : sidMod} por mes · ${state.region ? esc(regName(state.region)) : "todo el país"}`;
  const w = 540, h = 230, m = {l: 48, r: 14, t: 14, b: 26};
  const x = d3.scaleBand(S.months, [m.l, w - m.r]).padding(.18), y = d3.scaleLinear([0, d3.max(nat) || 1], [h - m.b, m.t]).nice();
  const s = chart("#sid-chart", w, h);
  s.append("g").attr("class", "grid").selectAll("line").data(y.ticks(4)).join("line").attr("x1", m.l).attr("x2", w - m.r).attr("y1", d => y(d)).attr("y2", d => y(d));
  s.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickSize(0).tickPadding(6).tickFormat(d => nf.format(d))).select(".domain").remove();
  s.append("g").attr("class", "axis").attr("transform", `translate(0,${h - m.b})`).call(d3.axisBottom(x).tickSize(0).tickPadding(7).tickFormat((d, i) => i % 2 === 0 || i === last ? MONTH(d).replace(/ 20/, " ’") : "")).select(".domain").remove();
  s.append("g").selectAll("rect").data(nat).join("rect").attr("x", (d, i) => x(S.months[i])).attr("width", x.bandwidth())
    .attr("y", d => y(d)).attr("height", d => y(0) - y(d)).attr("fill", (d, i) => i === last ? css("--ink") : i === 0 ? css("--ink-3") : css("--line"))
    .on("mousemove", (e, d) => showTip(e, `<b>${MONTH(S.months[nat.indexOf(d)])}</b><br>${nf.format(d)} denuncias`)).on("mouseleave", hideTip);
  s.append("text").attr("class", "num").attr("x", x(S.months[last]) + x.bandwidth() / 2).attr("y", y(nat[last]) - 5).attr("text-anchor", "middle").style("font-weight", 650).text(nf.format(nat[last]));
  $("#sid-note").innerHTML = `${MONTH(S.periodo)}: ${nf.format(nat[last])} denuncias${sidMod === "Total" ? "" : ` de ${esc(sidMod.toLowerCase())}`}, ${delta(nat[last], nat[0])} frente a ${MONTH(S.months[0])} (barra gris). Son denuncias registradas en el SIDPOL, no delitos ocurridos. <a href="${esc(S.dataset)}" target="_blank" rel="noopener">Dataset ↗</a>`;
  const pn = Object.fromEntries(V.geo.provs.features.map(f => [f.properties.id, f.properties]));
  const rows = Object.entries(S.provs).map(([c, r]) => [c, sumRow(r, sel)]).sort((a, b) => b[1][last] - a[1][last]).slice(0, 25);
  $("#sid-top-h3").textContent = `Provincias con más denuncias${sidMod === "Total" ? "" : " de " + sidMod.toLowerCase()}`;
  $("#sid-th-n").textContent = MONTH(S.periodo);
  $("#sid-top").innerHTML = rows.map(([c, v]) => `<tr><td>${esc(title(pn[c]?.n))} <span class="muted">· ${esc(title(pn[c]?.d))}</span></td><td class="num"><b>${nf.format(v[last])}</b></td><td class="num">${delta(v[last], v[0])}</td><td>${spark(v)}</td></tr>`).join("");
}
function sidpolBlock(code) {
  const S = V.sidpol, r = S?.provs[code];
  if (!r) return "";
  const last = S.months.length - 1, tot = sumRow(r, S.mods);
  const byMod = S.mods.map(m => [m, r[m][last], r[m][0]]).filter(x => x[1] || x[2]).sort((a, b) => b[1] - a[1]);
  return `<div class="sidblk"><p class="eyebrow">Denuncias policiales · ${MONTH(S.periodo)} · SIDPOL</p>
    <div class="row"><div><span class="big num">${nf.format(tot[last])}</span> ${delta(tot[last], tot[0])} <span class="muted" style="font-size:12px">vs. ${MONTH(S.months[0])}</span></div>${spark(tot, 120, 30)}</div>
    <table>${byMod.map(([m, n, b]) => `<tr><td>${esc(m.replace(" e integrantes", ""))}</td><td class="num"><b>${nf.format(n)}</b></td><td class="num">${delta(n, b)}</td><td>${spark(r[m], 70, 16)}</td></tr>`).join("")}</table></div>`;
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
  renderEnfen(); renderAvisos(); renderIndeci(); renderDanos(); renderStrip(); renderComunicados(); renderSidpol(); renderSismos(); renderSerfor(); renderZonas(); renderPron();
  $("#f-built").textContent = `Snapshot ${V.built} · armado en ${V.buildMs} ms`;
}
loadSnapshot().then(() => { lastSig = sigOf(H); loadLatest(); }).catch(() => { $("#conn").className = "conn off"; $("#conn").textContent = "Sin conexión con el colector"; });
setInterval(poll, 15000);
setInterval(() => H.length && renderHealth(), 30000);
