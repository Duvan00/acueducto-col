"use strict";
/* Interfaz de la plataforma. Todo cálculo se hace en el servidor (POST /api/calcular);
   la interfaz sólo arma la solicitud y presenta resultados con su trazabilidad. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (x, d = 0) => (x === null || x === undefined || Number.isNaN(x)) ? "—" : Number(x).toLocaleString("es-CO", { minimumFractionDigits: d, maximumFractionDigits: d });
const ETQ = {
  censo_oficial_dane: "Censo DANE", proyeccion_oficial_dane: "Proyección DANE", oficial_otra_entidad: "Oficial otra entidad",
  estimacion_tecnica: "Estimación técnica", informacion_campo: "Campo", pendiente_verificacion: "Pendiente verificación", sin_dato: "Sin dato", demo: "Ficticio",
};
const AREA = { total: "Total", cabecera: "Cabecera", centros_poblados_rural_disperso: "C. poblados y rural disperso" };
const METODOS = { aritmetico: "Aritmético", geometrico: "Geométrico", exponencial: "Exponencial", dane_oficial: "Proyección oficial DANE" };
const COLOR = { aritmetico: "#2a6f97", geometrico: "#c9184a", exponencial: "#6a994e", dane_oficial: "#7b2cbf" };
const badge = (c) => `<span class="badge b-${esc(c)}" title="${esc(ETQ[c] || c)}">${esc(ETQ[c] || c)}</span>`;

const S = {
  req: {
    proyecto: { nombre: "", codigo: "", entidad: "", elaboro: "", descripcion: "", tipo: "urbano" },
    localidades: [], anio_inicial: new Date().getFullYear(), periodo_diseno: 25, criterio_tasa: "promedio",
    caudales: { altitud_msnm: null, dotacion_neta: null, perdidas_pct: 25, k1: null, k2: null, k1_por_macromedicion: false,
                k2_por_macromedicion: false, regimen: "general", justificaciones: {} },
    demandas: [], etapas: [], justificacion_periodo: "",
  },
  res: null, foco: 0, proyectoId: null, mpio: null, graficas: {},
};

async function api(path, opts = {}) {
  const r = await fetch("/api" + path, { headers: { "content-type": "application/json" }, ...opts });
  if (!r.ok) {
    let m = r.statusText;
    try { const j = await r.json(); m = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(m);
  }
  return r.headers.get("content-type")?.includes("json") ? r.json() : r.blob();
}
const debounce = (f, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => f(...a), ms); }; };
const estado = (t, err) => { const e = $("#estado"); e.textContent = t; e.style.color = err ? "var(--crit)" : ""; };
function guardarLocal() { try { localStorage.setItem("acueducto.req", JSON.stringify(S.req)); } catch {} }

/* ---------------------------------------------------------------- territorio */

async function cargarDeptos() {
  const d = await api("/departamentos");
  $("#selDepto").innerHTML = `<option value="">— Seleccione —</option>` +
    d.map((x) => `<option value="${esc(x.codigo)}">${esc(x.codigo)} · ${esc(x.nombre)}${x.es_demostracion ? " ⚠" : ""}</option>`).join("");
}
$("#selDepto").addEventListener("change", async (e) => {
  const s = $("#selMpio");
  s.disabled = !e.target.value;
  if (!e.target.value) return;
  const m = await api("/municipios?dpto=" + encodeURIComponent(e.target.value));
  s.innerHTML = `<option value="">— Seleccione —</option>` + m.map((x) => `<option value="${x.id}" data-cod="${esc(x.codigo)}">${esc(x.codigo)} · ${esc(x.nombre)}</option>`).join("");
});
$("#selMpio").addEventListener("change", async (e) => {
  if (!e.target.value) return;
  const u = await api("/unidades/" + e.target.value);
  S.mpio = u;
  const chips = [
    `<button class="chip" data-add="${u.id}" data-area="total">+ ${esc(u.nombre)} (total)</button>`,
    `<button class="chip" data-add="${u.id}" data-area="cabecera">+ Cabecera (área DANE)</button>`,
    `<button class="chip" data-add="${u.id}" data-area="centros_poblados_rural_disperso">+ C. poblados y rural disperso</button>`,
  ].concat(u.hijos.map((h) => `<button class="chip" data-add="${h.id}" data-area="total" title="${esc(h.tipo)} ${esc(h.codigo)}">+ ${esc(h.nombre)}</button>`));
  $("#hijosMpio").innerHTML = chips.join("") + (u.hijos.length ? "" : `<p class="ayuda">Sin centros poblados ni veredas cargados para este municipio. Importe la DIVIPOLA de centros poblados o la capa de veredas.</p>`);
  verDemografia(u.id);
  refrescarMapa();
});
$("#hijosMpio").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-add]");
  if (b) agregarLocalidad(+b.dataset.add, b.dataset.area);
});

const buscar = debounce(async () => {
  const q = $("#buscador").value.trim();
  const ul = $("#resultados");
  if (q.length < 2) { ul.innerHTML = ""; return; }
  const cod = $("#selMpio").selectedOptions[0]?.dataset.cod;
  const r = await api(`/buscar?q=${encodeURIComponent(q)}${cod ? "&municipio=" + cod : ""}`);
  ul.innerHTML = r.length ? r.map((x) => `<li data-id="${x.id}"><span>${esc(x.nombre)}<br><small>${esc(x.tipo)} · ${esc(x.codigo)} · ${esc(x.municipio || "")} ${esc(x.departamento || "")}</small></span>${badge(x.es_demostracion ? "demo" : x.clasificacion)}</li>`).join("")
    : `<li><small>Sin resultados.</small></li>`;
}, 250);
$("#buscador").addEventListener("input", buscar);
$("#resultados").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-id]");
  if (li) { agregarLocalidad(+li.dataset.id, "total"); $("#resultados").innerHTML = ""; $("#buscador").value = ""; }
});

async function agregarLocalidad(id, area) {
  if (S.req.localidades.some((l) => l.unidad_id === id && l.area === area)) { estado("Ya está en el sistema"); return; }
  const u = await api("/unidades/" + id);
  const vereda = ["vereda", "sector_rural", "caserio"].includes(u.tipo);
  S.req.localidades.push({
    unidad_id: id, nombre: null, _nombre: u.nombre, _tipo: u.tipo, _codigo: u.codigo, area, metodo: "geometrico",
    cobertura_pct: 100, anio_ingreso: null, poblacion_base: null, anio_base: null, poblacion_base_soporte: "",
    tasa_manual: null, tasa_referencia: vereda ? "municipio_area" : "ninguna", justificacion: "", no_comparables: [], excluir_anios: [],
  });
  S.foco = S.req.localidades.length - 1;
  renderLocs();
  verDemografia(id);
  recalcular();
}

function renderLocs() {
  $("#nLoc").textContent = S.req.localidades.length;
  $("#listaLoc").innerHTML = S.req.localidades.map((l, i) => `
    <div class="loc ${i === S.foco ? "foco" : ""}" data-i="${i}">
      <header data-foco="${i}"><span><b>${esc(l._nombre)}</b><br><small>${esc(l._tipo)} · ${esc(l._codigo)} · área ${esc(AREA[l.area])}</small></span>
        <button class="quitar" data-quitar="${i}" title="Quitar">×</button></header>
      <div class="fila">
        <label>Método <select data-k="metodo">${Object.entries(METODOS).map(([k, v]) => `<option value="${k}" ${l.metodo === k ? "selected" : ""}>${v}</option>`).join("")}</select></label>
        <label>Cobertura % <input type="number" min="0" max="100" data-k="cobertura_pct" value="${l.cobertura_pct}"></label>
        <label>Año ingreso <input type="number" data-k="anio_ingreso" value="${l.anio_ingreso ?? ""}" placeholder="inicio"></label>
      </div>
      <details ${l.poblacion_base !== null || l.tasa_manual !== null ? "open" : ""}><summary>Ajustes demográficos sustentados</summary>
        <div class="fila">
          <label>Año base <input type="number" data-k="anio_base" value="${l.anio_base ?? ""}" placeholder="último censo"></label>
          <label>Población base <input type="number" data-k="poblacion_base" value="${l.poblacion_base ?? ""}" placeholder="censal"></label>
        </div>
        <label>Soporte de la población base <input data-k="poblacion_base_soporte" value="${esc(l.poblacion_base_soporte)}" placeholder="Censo local, acta, SISBEN…"></label>
        <div class="fila">
          <label>Tasa manual <input type="number" step="0.0001" data-k="tasa_manual" value="${l.tasa_manual ?? ""}" placeholder="geom./exp.: 0,012; aritm.: hab/año"></label>
          <label>Tasa de referencia <select data-k="tasa_referencia"><option value="ninguna">Propia</option><option value="municipio_area" ${l.tasa_referencia === "municipio_area" ? "selected" : ""}>Área del municipio</option></select></label>
        </div>
        <label>Justificación <input data-k="justificacion" value="${esc(l.justificacion)}"></label>
      </details>
    </div>`).join("") || `<p class="ayuda">Agregue localidades con el buscador, los botones del municipio o el mapa.</p>`;
}
$("#listaLoc").addEventListener("input", (e) => {
  const k = e.target.dataset.k;
  if (!k) return;
  const l = S.req.localidades[+e.target.closest(".loc").dataset.i];
  const numeros = ["cobertura_pct", "anio_ingreso", "anio_base", "poblacion_base", "tasa_manual"];
  l[k] = numeros.includes(k) ? (e.target.value === "" ? null : +e.target.value) : e.target.value;
  if (k === "cobertura_pct" && l[k] === null) l[k] = 100;
  recalcular();
});
$("#listaLoc").addEventListener("click", (e) => {
  const q = e.target.closest("[data-quitar]");
  if (q) { S.req.localidades.splice(+q.dataset.quitar, 1); S.foco = 0; renderLocs(); recalcular(); return; }
  const f = e.target.closest("[data-foco]");
  if (f) { S.foco = +f.dataset.foco; renderLocs(); verDemografia(S.req.localidades[S.foco].unidad_id); }
});

/* ---------------------------------------------------------------- demografía */

async function verDemografia(id) {
  if (!id) return;
  const [u, d] = await Promise.all([api("/unidades/" + id), api(`/unidades/${id}/demografia`)]);
  const jer = u.ancestros.filter((a) => a.tipo !== "pais").map((a) => esc(a.nombre)).join(" › ");
  const filasC = d.censos.map((c) => `<tr><td>${c.anio}</td><td>${AREA[c.area]}</td><td class="num">${fmt(c.poblacion)}</td><td class="num">${fmt(c.hombres)}</td><td class="num">${fmt(c.mujeres)}</td>
     <td class="num">${fmt(c.viviendas)}</td><td class="num">${fmt(c.hogares)}</td><td class="num">${fmt(c.personas_por_vivienda, 2)}</td><td>${badge(c.clasificacion)}</td><td><small>${esc(c.fuente)}</small></td></tr>`).join("");
  const proy = Object.entries(d.proyecciones_oficiales);
  const anios = proy.length ? Object.keys(proy[0][1].valores).map(Number).filter((a, i, arr) => a % 5 === 0 || i === arr.length - 1) : [];
  const filasP = proy.map(([area, p]) => `<tr><td>${AREA[area]}</td>${anios.map((a) => `<td class="num">${fmt(p.valores[a])}</td>`).join("")}</tr>`).join("");
  const otros = d.otros.map((o) => `<tr><td>${o.anio}</td><td>${esc(o.variable)}</td><td class="num">${fmt(o.valor, o.variable.startsWith("personas") ? 2 : 0)}</td><td>${badge(o.clasificacion)}</td><td><small>${esc(o.fuente)}${o.soporte ? " — " + esc(o.soporte) : ""}</small></td></tr>`).join("");
  const rural = ["vereda", "sector_rural", "caserio", "centro_poblado", "corregimiento", "inspeccion_policia"].includes(u.tipo);
  $("#demoCont").className = "";
  $("#demoCont").innerHTML = `
    <div class="panel">
      <h3>${esc(u.nombre)} ${u.es_demostracion ? '<span class="badge b-demo">FICTICIO</span>' : badge(u.clasificacion)}</h3>
      <dl class="kv"><dt>Tipo</dt><dd>${esc(u.tipo)}</dd><dt>Código</dt><dd>${esc(u.codigo)} (${esc(u.sistema_codigo)})</dd>
        <dt>Jerarquía</dt><dd>${jer || "—"}</dd><dt>Clase</dt><dd>${esc(u.clase || "—")}</dd>
        <dt>Altitud</dt><dd>${u.altitud_msnm ? fmt(u.altitud_msnm) + " m s.n.m. (" + esc(u.altitud_fuente || "s.f.") + ")" : "—"}</dd>
        <dt>Coordenadas</dt><dd>${u.lat ? fmt(u.lat, 5) + ", " + fmt(u.lon, 5) : "—"}</dd><dt>Área</dt><dd>${u.area_km2 ? fmt(u.area_km2, 2) + " km²" : "Sin cartografía"}</dd></dl>
      ${u.tipo === "vereda" ? `<div class="adv advertencia">El DANE no publica población oficial por vereda. Las cifras veredales aquí mostradas son información de campo o estimaciones técnicas, y los límites veredales no coinciden necesariamente con los sectores censales.</div>` : ""}
    </div>
    <div class="panel"><h3>Censos</h3>${filasC ? `<div class="scroll"><table class="tabla"><thead><tr><th>Año</th><th>Área</th><th class="num">Población</th><th class="num">Hombres</th><th class="num">Mujeres</th><th class="num">Viviendas</th><th class="num">Hogares</th><th class="num">Hab/viv. ocup.</th><th>Tipo de dato</th><th>Fuente</th></tr></thead><tbody>${filasC}</tbody></table></div>` : `<p class="ayuda">No hay datos censales para esta unidad.</p>`}</div>
    <div class="panel"><h3>Proyecciones oficiales DANE</h3>${filasP ? `<p class="ayuda">${esc(proy[0][1].fuente)} · serie ${esc(proy[0][1].serie)}</p><div class="scroll"><table class="tabla"><thead><tr><th>Área</th>${anios.map((a) => `<th class="num">${a}</th>`).join("")}</tr></thead><tbody>${filasP}</tbody></table></div>` : `<p class="ayuda">El DANE publica proyecciones a nivel municipal por área; no existen para centros poblados ni veredas.</p>`}</div>
    <div class="panel"><h3>Otra información (campo, otras entidades, estimaciones)</h3>${otros ? `<table class="tabla"><thead><tr><th>Año</th><th>Variable</th><th class="num">Valor</th><th>Tipo</th><th>Fuente y soporte</th></tr></thead><tbody>${otros}</tbody></table>` : `<p class="ayuda">Sin registros.</p>`}
      <details><summary>Incorporar información de campo o de otra entidad</summary>
        <form id="fDato" class="fila">
          <label>Año <input name="anio" type="number" required value="${new Date().getFullYear()}"></label>
          <label>Variable <select name="variable"><option>poblacion</option><option>viviendas_ocupadas</option><option>viviendas</option><option>hogares</option><option>hombres</option><option>mujeres</option></select></label>
          <label>Valor <input name="valor" type="number" step="any" required></label>
          <label>Tipo <select name="clasificacion"><option value="informacion_campo">Información de campo</option><option value="oficial_otra_entidad">Oficial de otra entidad</option><option value="pendiente_verificacion">Pendiente de verificación</option></select></label>
          <label>Entidad / responsable <input name="entidad" value="Usuario"></label>
          <label class="ancho">Soporte (obligatorio) <input name="soporte" required placeholder="Acta, informe, base SISBEN, conteo sobre ortofoto…"></label>
          <button>Guardar dato</button>
        </form>
      </details>
    </div>
    ${rural ? `<div class="panel"><h3>Estimar población (sin dato censal individual)</h3>
      <p class="ayuda">Métodos técnicamente justificados. El resultado se guarda como <b>estimación técnica</b>, nunca como dato oficial.</p>
      <form id="fEst" class="fila">
        <label>Método <select name="metodo"><option value="viviendas">Viviendas ocupadas × hab/vivienda</option><option value="hogares">Hogares × personas/hogar</option></select></label>
        <label>Viviendas u hogares <input name="cantidad" type="number" step="any" required></label>
        <label>Fuente del conteo <input name="fcant" required placeholder="Conteo en campo 2025"></label>
        <label>Hab/vivienda o pers/hogar <input name="factor" type="number" step="0.01" required placeholder="del rural disperso CNPV 2018"></label>
        <label>Fuente del factor <input name="ffac" required value="CNPV 2018, centros poblados y rural disperso del municipio"></label>
        <label>Año <input name="anio" type="number" value="${new Date().getFullYear()}"></label>
        <button>Estimar y guardar</button>
      </form><div id="estRes"></div></div>` : ""}`;
  $("#fDato")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try {
      await api(`/unidades/${id}/datos`, { method: "POST", body: JSON.stringify({ ...f, anio: +f.anio, valor: +f.valor, producto: "Información incorporada por el usuario" }) });
      estado("Dato guardado"); verDemografia(id); recalcular();
    } catch (err) { estado(err.message, true); }
  });
  $("#fEst")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const body = f.metodo === "viviendas"
      ? { metodo: "viviendas", viviendas_ocupadas: +f.cantidad, ppv: +f.factor, fuente_viviendas: f.fcant, fuente_ppv: f.ffac }
      : { metodo: "hogares", hogares: +f.cantidad, pph: +f.factor, fuente_hogares: f.fcant, fuente_pph: f.ffac };
    try {
      const r = await api("/estimacion", { method: "POST", body: JSON.stringify({ ...body, unidad_id: id, anio: +f.anio, guardar: true }) });
      $("#estRes").innerHTML = `<div class="adv info">${badge("estimacion_tecnica")} ${fmt(r.poblacion)} habitantes — ${esc(r.metodologia)}</div>`;
      verDemografia(id); recalcular();
    } catch (err) { estado(err.message, true); }
  });
  // Sugerir PPV del área rural del municipio
  if (rural && $("#fEst")) {
    const m = u.ancestros.find((a) => a.tipo === "municipio");
    if (m) {
      const dm = await api(`/unidades/${m.id}/demografia`);
      const c = dm.censos.filter((x) => x.area === "centros_poblados_rural_disperso" && x.personas_por_vivienda).pop();
      if (c) $("#fEst [name=factor]").placeholder = `${fmt(c.personas_por_vivienda, 2)} (${c.anio}, ${ETQ[c.clasificacion]})`;
    }
  }
}

/* ---------------------------------------------------------------- cálculo */

const recalcular = debounce(async () => {
  guardarLocal();
  if (!S.req.localidades.length) { S.res = null; renderResultados(); return; }
  estado("Calculando…");
  try {
    S.res = await api("/calcular", { method: "POST", body: JSON.stringify(S.req) });
    estado("Actualizado " + new Date().toLocaleTimeString("es-CO"));
  } catch (e) { S.res = null; estado(e.message, true); }
  renderResultados();
}, 350);

function renderResultados() {
  const r = S.res;
  $("#bannerDemo").hidden = !r?.es_demostracion;
  $("#anioFin").textContent = S.req.anio_inicial + S.req.periodo_diseno;
  renderProyeccion(r);
  renderCaudales(r);
  pintarMapaSistema();
}

const advHTML = (lista) => (lista || []).map((a) => `<div class="adv ${esc(a.nivel)}"><b>${esc(a.nivel.toUpperCase())}</b> · ${esc(a.mensaje)}</div>`).join("");

function renderProyeccion(r) {
  Object.values(S.graficas).forEach((g) => g.destroy());
  S.graficas = {};
  if (!r) { $("#proyCont").innerHTML = `<div class="vacio">Sin cálculo. Agregue localidades.</div>`; return; }
  const af = r.anio_final;
  $("#proyCont").innerHTML = r.localidades.map((l, i) => {
    const tasas = l.tasas.map((t) => `<tr><td>${t.periodo}</td><td class="num">${fmt(t.p_inicial)}</td><td class="num">${fmt(t.p_final)}</td><td class="num">${fmt(t.aritmetica, 1)}</td><td class="num">${fmt(t.geometrica * 100, 3)} %</td><td class="num">${fmt(t.exponencial * 100, 3)} %</td><td>${t.comparable ? "Sí" : "No"}</td></tr>`).join("");
    const comp = Object.entries(l.comparacion).map(([m, x]) => {
      const pf = x.serie.find((p) => p.anio === af);
      const pi = x.serie.find((p) => p.anio === r.anio_inicial);
      return `<tr ${m === l.metodo ? 'style="font-weight:700"' : ""}><td><label class="chk"><input type="radio" name="met${i}" value="${m}" ${m === l.metodo ? "checked" : ""}> ${METODOS[m]}</label></td>
        <td class="num">${x.tasa === null ? "—" : x.unidad_tasa === "hab/año" ? fmt(x.tasa, 1) + " hab/año" : fmt(x.tasa * 100, 3) + " %/año"}</td>
        <td class="num">${fmt(pi?.poblacion)}</td><td class="num">${fmt(pf?.poblacion)}</td><td>${pf ? badge(pf.clasificacion) : ""}</td></tr>`;
    }).join("") + (!l.comparacion[l.metodo] ? `<tr style="font-weight:700"><td>${METODOS[l.metodo]} (tasa adoptada)</td><td class="num">${l.seleccion.unidad_tasa === "hab/año" ? fmt(l.seleccion.tasa, 1) + " hab/año" : fmt(l.seleccion.tasa * 100, 3) + " %/año"}</td>
        <td class="num">${fmt(l.seleccion.serie.find((p) => p.anio === r.anio_inicial)?.poblacion)}</td><td class="num">${fmt(l.seleccion.serie.find((p) => p.anio === af)?.poblacion)}</td><td>${badge("estimacion_tecnica")}</td></tr>` : "") + Object.entries(l.errores_metodos).map(([m, e]) => `<tr><td>${METODOS[m]}</td><td colspan="4"><small>${esc(e)}</small></td></tr>`).join("");
    const s = l.seleccion;
    return `<div class="panel" data-loc="${i}">
      <h3>${esc(l.nombre)} <span class="pill">${METODOS[l.metodo]}</span></h3>
      <div class="grid2">
        <div>
          ${tasas ? `<table class="tabla"><thead><tr><th>Período</th><th class="num">P inicial</th><th class="num">P final</th><th class="num">ka</th><th class="num">r</th><th class="num">k</th><th>Comparable</th></tr></thead><tbody>${tasas}</tbody></table>` : `<p class="ayuda">Sin períodos intercensales${l.tasa_referencia !== null && l.tasa_referencia !== undefined ? " — se usa tasa de referencia del municipio: " + fmt(l.tasa_referencia * 100, 3) + " %/año" : ""}.</p>`}
          ${l.censos.length ? `<p class="ayuda">Marcar censos no comparables (cambios territoriales): ${l.censos.map((c) => `<label class="chk" style="display:inline-flex"><input type="checkbox" data-nocomp="${i}" value="${c.anio}" ${c.comparable ? "" : "checked"}> ${c.anio}</label>`).join(" ")}</p>` : ""}
          <table class="tabla" style="margin-top:.6rem"><thead><tr><th>Método</th><th class="num">Tasa</th><th class="num">${r.anio_inicial}</th><th class="num">${af}</th><th>Tipo</th></tr></thead><tbody>${comp}</tbody></table>
          <div class="recom"><b>Sugerencia:</b> ${l.recomendacion.metodo ? METODOS[l.recomendacion.metodo] : "—"}. ${esc((l.recomendacion.criterios || []).join(" "))}</div>
          <p class="ayuda"><b>Base:</b> ${fmt(s.poblacion_base)} hab en ${s.anio_base}${l.poblacion_base_soporte ? " — " + esc(l.poblacion_base_soporte) : ""}</p>
        </div>
        <div class="grafica"><canvas id="g${i}"></canvas></div>
      </div>
      <p class="ayuda"><b>Justificación registrada:</b> ${esc(l.justificacion)}</p>
      ${advHTML(s.advertencias)}
    </div>`;
  }).join("") + (r.localidades.length > 1 ? `<div class="panel"><h3>Consolidado del sistema</h3><div class="grafica"><canvas id="gSis"></canvas></div>${advHTML(r.sistema.advertencias)}</div>` : "");

  r.localidades.forEach((l, i) => {
    const ds = Object.entries(l.comparacion).map(([m, x]) => ({
      label: METODOS[m], data: x.serie.filter((p) => p.poblacion !== null).map((p) => ({ x: p.anio, y: p.poblacion })),
      borderColor: COLOR[m], backgroundColor: COLOR[m], borderWidth: m === l.metodo ? 3 : 1.3, borderDash: m === l.metodo ? [] : [5, 4], pointRadius: 0,
    }));
    if (!l.comparacion[l.metodo]) {
      ds.push({ label: METODOS[l.metodo] + " (tasa adoptada)", data: l.seleccion.serie.filter((p) => p.poblacion !== null).map((p) => ({ x: p.anio, y: p.poblacion })),
                borderColor: COLOR[l.metodo], backgroundColor: COLOR[l.metodo], borderWidth: 3, pointRadius: 0 });
      ds.push({ label: "Población base", type: "scatter", data: [{ x: l.seleccion.anio_base, y: l.seleccion.poblacion_base }], backgroundColor: "#2d6a2d", pointRadius: 5 });
    }
    ds.push({ label: "Censos", type: "scatter", data: l.censos.map((c) => ({ x: c.anio, y: c.poblacion })), backgroundColor: "#111", pointRadius: 4 });
    S.graficas["g" + i] = new Chart($("#g" + i), {
      type: "line", data: { datasets: ds },
      options: { maintainAspectRatio: false, parsing: false, scales: { x: { type: "linear", ticks: { callback: (v) => v } }, y: { ticks: { callback: (v) => fmt(v) } } },
                 plugins: { legend: { labels: { boxWidth: 12, font: { size: 11 } } }, tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.parsed.y)} hab (${c.parsed.x})` } } } },
    });
  });
  if (r.localidades.length > 1) {
    const paleta = ["#2a6f97", "#c9184a", "#6a994e", "#e09f3e", "#7b2cbf", "#33658a", "#9e2a2b"];
    S.graficas.sis = new Chart($("#gSis"), {
      type: "line",
      data: { datasets: r.localidades.map((l, i) => ({ label: l.nombre, data: l.serie_servida.map((p) => ({ x: p.anio, y: p.servida })), fill: i ? "-1" : "origin",
        backgroundColor: paleta[i % paleta.length] + "88", borderColor: paleta[i % paleta.length], pointRadius: 0 })) },
      options: { maintainAspectRatio: false, parsing: false, scales: { x: { type: "linear", ticks: { callback: (v) => v } }, y: { stacked: true, ticks: { callback: (v) => fmt(v) } } } },
    });
  }
}
$("#proyCont").addEventListener("change", (e) => {
  const p = e.target.closest("[data-loc]");
  if (!p) return;
  const l = S.req.localidades[+p.dataset.loc];
  if (e.target.type === "radio") { l.metodo = e.target.value; renderLocs(); recalcular(); }
  if (e.target.dataset.nocomp !== undefined) {
    const a = +e.target.value;
    l.no_comparables = e.target.checked ? [...new Set([...l.no_comparables, a])] : l.no_comparables.filter((x) => x !== a);
    recalcular();
  }
});

function renderCaudales(r) {
  if (!r) { $("#qCards").innerHTML = ""; $("#qPasos").innerHTML = ""; $("#qAdv").innerHTML = ""; return; }
  const q = r.caudales;
  $("#qPob").textContent = fmt(q.poblacion) + " hab (" + r.anio_final + ")";
  if (S.req.caudales.altitud_msnm === null) $("#qAlt").placeholder = q.altitud_msnm !== null ? `${fmt(q.altitud_msnm)} (promedio de las localidades)` : "obligatoria";
  $("#qDbr").textContent = fmt(q.dotacion_bruta, 2) + " L/hab·día";
  $("#qDot").placeholder = q.dotacion_neta_max ? `máx. ${q.dotacion_neta_max} (art. 43)` : "";
  $("#qK1").placeholder = `máx. ${q.k1}`;
  $("#qK2").placeholder = `máx. ${q.k2}`;
  if (S.req.caudales.k1 === null) $("#qK1").placeholder = `${q.k1} (máx. normativo)`;
  if (S.req.caudales.k2 === null) $("#qK2").placeholder = `${q.k2} (máx. normativo)`;
  $("#qCards").innerHTML = [["Caudal medio diario", "Qmd", q.qmd_lps, q.qmd_m3d], ["Caudal máximo diario", "QMD", q.QMD_lps, q.QMD_m3d], ["Caudal máximo horario", "QMH", q.QMH_lps, q.QMH_m3d]]
    .map(([t, s, l, m]) => `<div class="card"><div class="t">${t} (${s})</div><div class="v">${fmt(l, 2)} <small>L/s</small></div><div class="s">${fmt(m, 1)} m³/día</div></div>`).join("");
  $("#qCumple").innerHTML = `<div class="cumple ${q.cumple_normativa ? "si" : "no"}">${q.cumple_normativa ? "Parámetros dentro de los límites normativos" : "Hay parámetros que exceden los límites normativos"}</div>`;
  $("#qPasos").innerHTML = `<table class="tabla"><thead><tr><th>Paso</th><th>Símbolo</th><th class="num">Valor</th><th>Unidad</th><th>Fórmula y sustitución</th><th>Fundamento</th></tr></thead><tbody>${
    q.pasos.map((p) => `<tr><td>${esc(p.nombre)}</td><td>${esc(p.simbolo)}</td><td class="num">${fmt(p.valor, p.unidad === "hab" ? 0 : 3)}</td><td>${esc(p.unidad)}</td><td><span class="formula">${esc(p.formula)}</span>${p.sustitucion ? "<br><small>" + esc(p.sustitucion) + "</small>" : ""}</td><td><small>${esc(p.fundamento)}</small></td></tr>`).join("")}</tbody></table>`;
  $("#qAdv").innerHTML = advHTML(r.advertencias);
  const filasEt = $$("#tEt tbody tr");
  r.caudales_etapas.forEach((e, i) => {
    const tr = filasEt[i];
    if (tr) { tr.cells[3].textContent = fmt(e.poblacion_servida); tr.cells[4].textContent = fmt(e.QMD_lps, 2); tr.cells[5].textContent = fmt(e.QMH_lps, 2); }
  });
}

// Parámetros de caudal
const enlaces = [["#qAlt", "altitud_msnm", "n"], ["#qDot", "dotacion_neta", "n"], ["#qPer", "perdidas_pct", "n"], ["#qK1", "k1", "n"], ["#qK2", "k2", "n"],
                 ["#qK1m", "k1_por_macromedicion", "b"], ["#qK2m", "k2_por_macromedicion", "b"], ["#qReg", "regimen", "s"]];
enlaces.forEach(([sel, k, t]) => $(sel).addEventListener(t === "b" ? "change" : "input", (e) => {
  S.req.caudales[k] = t === "b" ? e.target.checked : t === "n" ? (e.target.value === "" ? (k === "perdidas_pct" ? 25 : null) : +e.target.value) : e.target.value;
  recalcular();
}));
$("#jDot").addEventListener("input", (e) => { S.req.caudales.justificaciones.dotacion_neta = e.target.value; recalcular(); });
$("#jK").addEventListener("input", (e) => { S.req.caudales.justificaciones.k1 = S.req.caudales.justificaciones.k2 = e.target.value; recalcular(); });
$("#anioIni").addEventListener("input", (e) => { if (e.target.value) { S.req.anio_inicial = +e.target.value; recalcular(); } });
$("#periodo").addEventListener("input", (e) => { if (e.target.value) { S.req.periodo_diseno = +e.target.value; recalcular(); } });
$("#criterio").addEventListener("change", (e) => { S.req.criterio_tasa = e.target.value; recalcular(); });
$("#justPeriodo").addEventListener("input", (e) => { S.req.justificacion_periodo = e.target.value; recalcular(); });
$("#proyNombre").addEventListener("input", (e) => { S.req.proyecto.nombre = e.target.value; guardarLocal(); });

// Demandas adicionales y etapas
function renderDemandas() {
  $("#tDem tbody").innerHTML = S.req.demandas.map((d, i) => `<tr data-i="${i}">
    <td><input data-k="tipo" value="${esc(d.tipo)}"></td><td><input type="number" step="0.001" min="0" data-k="caudal_lps" value="${d.caudal_lps}"></td>
    <td><input type="checkbox" data-k="incluye_perdidas" ${d.incluye_perdidas ? "checked" : ""}></td><td><input type="checkbox" data-k="afecta_k1" ${d.afecta_k1 ? "checked" : ""}></td>
    <td><input type="checkbox" data-k="afecta_k2" ${d.afecta_k2 ? "checked" : ""}></td><td><input type="checkbox" data-k="incluida_en_dotacion" ${d.incluida_en_dotacion ? "checked" : ""}></td>
    <td><input data-k="justificacion" value="${esc(d.justificacion)}" placeholder="Obligatoria"></td><td><button class="quitar" data-q="${i}">×</button></td></tr>`).join("");
}
$("#addDem").addEventListener("click", () => { S.req.demandas.push({ tipo: "institucional", caudal_lps: 0, justificacion: "", afecta_k1: true, afecta_k2: true, incluye_perdidas: false, incluida_en_dotacion: false }); renderDemandas(); recalcular(); });
$("#tDem").addEventListener("input", (e) => {
  const tr = e.target.closest("tr[data-i]"); if (!tr) return;
  const d = S.req.demandas[+tr.dataset.i], k = e.target.dataset.k;
  d[k] = e.target.type === "checkbox" ? e.target.checked : k === "caudal_lps" ? +e.target.value || 0 : e.target.value;
  recalcular();
});
$("#tDem").addEventListener("click", (e) => { const q = e.target.closest("[data-q]"); if (q) { S.req.demandas.splice(+q.dataset.q, 1); renderDemandas(); recalcular(); } });
function renderEtapas() {
  $("#tEt tbody").innerHTML = S.req.etapas.map((d, i) => `<tr data-i="${i}"><td><input data-k="nombre" value="${esc(d.nombre)}"></td>
    <td><input type="number" data-k="anio_inicio" value="${d.anio_inicio}"></td><td><input type="number" data-k="anio_fin" value="${d.anio_fin}"></td>
    <td class="num"></td><td class="num"></td><td class="num"></td><td><button class="quitar" data-q="${i}">×</button></td></tr>`).join("");
}
$("#addEt").addEventListener("click", () => {
  const ult = S.req.etapas.at(-1), ini = ult ? ult.anio_fin + 1 : S.req.anio_inicial;
  S.req.etapas.push({ nombre: `Etapa ${S.req.etapas.length + 1}`, anio_inicio: ini, anio_fin: Math.min(ini + 12, S.req.anio_inicial + S.req.periodo_diseno) });
  renderEtapas(); recalcular();
});
$("#tEt").addEventListener("input", (e) => {
  const tr = e.target.closest("tr[data-i]"); if (!tr) return;
  const d = S.req.etapas[+tr.dataset.i], k = e.target.dataset.k;
  d[k] = k === "nombre" ? e.target.value : +e.target.value;
  recalcular();
});
$("#tEt").addEventListener("click", (e) => { const q = e.target.closest("[data-q]"); if (q) { S.req.etapas.splice(+q.dataset.q, 1); renderEtapas(); recalcular(); } });

/* ---------------------------------------------------------------- mapa */

let mapa, capaUnid, capaSis, capaArea, dibujados;
function iniciarMapa() {
  if (mapa) return;
  mapa = L.map("mapa").setView([4.6, -74.1], 6);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap" }).addTo(mapa);
  capaUnid = L.geoJSON(null, {
    style: (f) => ({ color: f.properties.tipo === "vereda" ? "#2d6a2d" : f.properties.tipo === "municipio" ? "#0b5a8c" : "#a26100", weight: 1.5, fillOpacity: .08, dashArray: f.properties.es_demostracion ? "4 3" : null }),
    pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 5 }),
    onEachFeature: (f, ly) => {
      const p = f.properties;
      ly.bindPopup(`<b>${esc(p.nombre)}</b><br>${esc(p.tipo)} · ${esc(p.codigo)}<br>Población: ${p.pob ? fmt(p.pob) + " (" + p.pob_anio + ", " + esc(ETQ[p.pob_clasif]) + ")" : "sin dato"}${p.es_demostracion ? "<br><b>FICTICIO</b>" : ""}<br><button class="mini" onclick="agregarLocalidad(${p.id},'total')">Agregar al sistema</button>`);
    },
  }).addTo(mapa);
  capaSis = L.layerGroup().addTo(mapa);
  capaArea = L.geoJSON(null, { style: { color: "#c9184a", weight: 2, fillOpacity: .05 } }).addTo(mapa);
  dibujados = new L.FeatureGroup().addTo(mapa);
  mapa.addControl(new L.Control.Draw({ edit: { featureGroup: dibujados }, draw: { polygon: true, rectangle: true, polyline: false, circle: false, marker: false, circlemarker: false } }));
  mapa.on(L.Draw.Event.CREATED, (e) => { dibujados.clearLayers(); dibujados.addLayer(e.layer); localidadesEnArea(e.layer.toGeoJSON().geometry); });
}
async function refrescarMapa() {
  if (!mapa || !S.mpio) return;
  capaUnid.clearLayers();
  if (!$("#capaUnid").checked) return;
  const gj = await api(`/geo/unidades?municipio=${S.mpio.codigo}&tipos=municipio,cabecera_municipal,centro_poblado,corregimiento,inspeccion_policia,caserio,vereda,sector_rural`);
  capaUnid.addData(gj);
  if (gj.features.length) mapa.fitBounds(capaUnid.getBounds(), { maxZoom: 13 });
  else if (S.mpio.lat) mapa.setView([S.mpio.lat, S.mpio.lon], 11);
}
function pintarMapaSistema() {
  if (!mapa || !capaSis) return;
  capaSis.clearLayers();
  const r = S.res; if (!r) return;
  const qmdPorHab = r.caudales.QMD_lps / Math.max(r.sistema.poblacion_diseno, 1);
  r.localidades.forEach((l) => {
    if (!l.unidad?.lat) return;
    const p = l.serie_servida.at(-1)?.servida || 0;
    L.circleMarker([l.unidad.lat, l.unidad.lon], { radius: 6 + Math.sqrt(p) / 6, color: "#c9184a", fillOpacity: .45 })
      .bindTooltip(`${esc(l.nombre)}<br>Pob. servida ${r.anio_final}: ${fmt(p)}<br>QMD aprox.: ${fmt(p * qmdPorHab, 2)} L/s`).addTo(capaSis);
  });
}
async function localidadesEnArea(geom) {
  const r = await api("/geo/localidades_en_area", { method: "POST", body: JSON.stringify({ geometria: geom }) });
  const box = $("#enArea");
  box.hidden = false;
  box.innerHTML = `<h3>Localidades en el área de influencia (${r.length})</h3>${r.length ? `<table class="tabla"><thead><tr><th>Nombre</th><th>Tipo</th><th>Código</th><th class="num">Fracción de su área incluida</th><th></th></tr></thead><tbody>${
    r.map((x) => `<tr><td>${esc(x.nombre)}</td><td>${esc(x.tipo)}</td><td>${esc(x.codigo)}</td><td class="num">${x.fraccion === null ? "punto" : fmt(x.fraccion * 100, 1) + " %"}</td>
      <td><button class="mini" data-areaadd="${x.id}" data-frac="${x.fraccion ?? 1}">Agregar</button></td></tr>`).join("")}</tbody></table>
    <p class="ayuda">Al agregar una unidad parcialmente incluida se propone como cobertura la fracción de área; ajústela con el conteo real de viviendas beneficiarias.</p>` : `<p class="ayuda">No hay unidades cartografiadas en el área.</p>`}`;
}
$("#enArea").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-areaadd]"); if (!b) return;
  await agregarLocalidad(+b.dataset.areaadd, "total");
  const l = S.req.localidades.at(-1);
  const f = +b.dataset.frac;
  if (f < 0.999) { l.cobertura_pct = Math.round(f * 1000) / 10; l.justificacion = `Cobertura inicial = fracción de área dentro del área de influencia (${l.cobertura_pct} %); verificar con viviendas.`; renderLocs(); recalcular(); }
});
$("#capaUnid").addEventListener("change", refrescarMapa);
$("#archGeo").addEventListener("change", async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("archivo", f);
  try {
    const r = await fetch("/api/geo/leer", { method: "POST", body: fd });
    const gj = await r.json();
    if (!r.ok) throw new Error(gj.detail);
    capaArea.clearLayers(); capaArea.addData(gj);
    if (gj.advertencia) estado(gj.advertencia, true);
    if (capaArea.getBounds().isValid()) mapa.fitBounds(capaArea.getBounds());
    const pol = gj.features.find((x) => /Polygon/.test(x.geometry?.type));
    if (pol) localidadesEnArea(pol.geometry);
  } catch (err) { estado(err.message, true); }
});

/* ---------------------------------------------------------------- validaciones y fuentes */

async function verValidaciones() {
  const cod = S.mpio?.codigo;
  const v = await api("/validaciones" + (cod ? "?municipio=" + cod : ""));
  $("#valCont").innerHTML = `<p class="ayuda">${cod ? "Municipio " + esc(S.mpio.nombre) : "Toda la base"} — controles de consistencia, duplicidad, faltantes y diferencias territoriales.</p>` +
    v.map((x) => `<div class="panel"><h3>${esc(x.titulo)} <span class="pill">${x.n}</span> <span class="badge" style="background:var(--${x.nivel === "critica" ? "crit" : x.nivel === "advertencia" ? "adv" : "pri"})">${x.nivel}</span></h3>${
      x.n ? `<div class="scroll"><table class="tabla"><thead><tr>${Object.keys(x.filas[0]).map((k) => `<th>${esc(k)}</th>`).join("")}</tr></thead><tbody>${x.filas.map((f) => `<tr>${Object.values(f).map((c) => `<td>${esc(typeof c === "number" ? fmt(c, Number.isInteger(c) ? 0 : 2) : c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : `<p class="ayuda">Sin hallazgos.</p>`}</div>`).join("");
}
async function verFuentes() {
  const [of, fu, no] = await Promise.all([api("/fuentes_oficiales"), api("/fuentes"), api("/normativa")]);
  $("#fuentesCont").innerHTML = `
    <div class="panel"><h3>Inventario de fuentes oficiales</h3><p class="ayuda">${esc(of._nota)}</p><table class="tabla"><thead><tr><th>Producto</th><th>Entidad</th><th>Desagregación</th><th>Población</th><th>Integración</th><th>Advertencia</th></tr></thead><tbody>${
      of.fuentes.map((f) => `<tr><td><a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.producto)}</a></td><td>${esc(f.entidad)}</td><td>${esc(f.desagregacion)}</td><td>${f.contiene_poblacion ? "Sí" : "No"}</td><td>${esc(f.acceso)}</td><td><small>${esc(f.advertencia || "")}</small></td></tr>`).join("")}</tbody></table></div>
    <div class="panel"><h3>Fuentes cargadas en la base</h3><table class="tabla"><thead><tr><th>Entidad</th><th>Producto</th><th>Versión</th><th>Consulta</th><th>Cargas</th><th>Notas</th></tr></thead><tbody>${
      fu.map((f) => `<tr><td>${esc(f.entidad)}</td><td>${esc(f.producto)}</td><td>${esc(f.version || "")}</td><td>${esc(f.fecha_consulta || "")}</td><td class="num">${f.cargas}</td><td><small>${esc(f.notas || "")}</small></td></tr>`).join("")}</tbody></table></div>
    <div class="panel"><h3>Parámetros normativos aplicados</h3><table class="tabla"><thead><tr><th>Parámetro</th><th>Valor</th><th>Condición</th><th>Norma y artículo</th><th>Texto de referencia</th><th>Verificación</th></tr></thead><tbody>${
      no.map((p) => `<tr><td>${esc(p.clave)}</td><td>${fmt(p.valor, p.valor % 1 ? 2 : 0)} ${esc(p.unidad || "")}</td><td><small>${esc(JSON.stringify(p.condicion))}</small></td><td><small>${esc(p.norma)} — ${esc(p.articulo || "")}</small></td><td><small>${esc(p.texto_referencia || "")}</small></td><td>${esc(p.estado_verificacion)}</td></tr>`).join("")}</tbody></table></div>`;
}

/* ---------------------------------------------------------------- pestañas, exportación, proyectos */

$$(".tab").forEach((t) => t.addEventListener("click", () => {
  $$(".tab").forEach((x) => x.classList.toggle("activo", x === t));
  $$(".vista").forEach((v) => v.classList.toggle("activo", v.id === "v-" + t.dataset.tab));
  if (t.dataset.tab === "mapa") { iniciarMapa(); setTimeout(() => { mapa.invalidateSize(); refrescarMapa(); pintarMapaSistema(); }, 50); }
  if (t.dataset.tab === "validaciones") verValidaciones();
  if (t.dataset.tab === "fuentes") verFuentes();
  if (t.dataset.tab === "proyeccion" && S.res) renderProyeccion(S.res);
}));

$$("[data-exp]").forEach((b) => b.addEventListener("click", async () => {
  if (!S.req.localidades.length) { estado("Agregue al menos una localidad", true); return; }
  estado("Generando memoria…");
  try {
    const blob = await api("/memoria/" + b.dataset.exp, { method: "POST", body: JSON.stringify(S.req) });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `memoria_${(S.req.proyecto.nombre || "proyecto").replace(/\W+/g, "_")}.${b.dataset.exp}`;
    a.click();
    estado("Memoria generada");
  } catch (e) { estado(e.message, true); }
}));

$("#btnGuardar").addEventListener("click", async () => {
  try {
    const r = await api("/proyectos", { method: "POST", body: JSON.stringify({ id: S.proyectoId, calculo: S.req }) });
    S.proyectoId = r.id;
    estado("Proyecto guardado (#" + r.id + ")");
  } catch (e) { estado(e.message, true); }
});
$("#btnAbrir").addEventListener("click", async () => {
  const ps = await api("/proyectos");
  $("#dlgCont").innerHTML = `<h3>Proyectos guardados</h3>${ps.length ? `<table class="tabla"><tbody>${ps.map((p) => `<tr><td>#${p.id}</td><td>${esc(p.nombre)}</td><td>${esc(p.tipo)}</td><td>${new Date(p.actualizado_en).toLocaleString("es-CO")}</td><td><button class="mini" data-abrir="${p.id}" value="cancel">Abrir</button></td></tr>`).join("")}</tbody></table>` : "<p>No hay proyectos guardados.</p>"}`;
  $("#dlg").showModal();
});
$("#dlgCont").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-abrir]"); if (!b) return;
  const p = await api("/proyectos/" + b.dataset.abrir);
  S.proyectoId = p.id;
  cargarReq(p.parametros);
  $("#dlg").close();
});

function cargarReq(req) {
  S.req = { ...S.req, ...req, caudales: { ...S.req.caudales, ...req.caudales } };
  S.req.localidades.forEach((l) => { l._nombre ??= l.nombre || "Localidad " + l.unidad_id; l._tipo ??= ""; l._codigo ??= ""; });
  $("#proyNombre").value = S.req.proyecto.nombre || "";
  $("#anioIni").value = S.req.anio_inicial;
  $("#periodo").value = S.req.periodo_diseno;
  $("#criterio").value = S.req.criterio_tasa;
  $("#justPeriodo").value = S.req.justificacion_periodo || "";
  const c = S.req.caudales;
  $("#qAlt").value = c.altitud_msnm ?? ""; $("#qDot").value = c.dotacion_neta ?? ""; $("#qPer").value = c.perdidas_pct;
  $("#qK1").value = c.k1 ?? ""; $("#qK2").value = c.k2 ?? ""; $("#qK1m").checked = c.k1_por_macromedicion; $("#qK2m").checked = c.k2_por_macromedicion;
  $("#qReg").value = c.regimen; $("#jDot").value = c.justificaciones?.dotacion_neta || ""; $("#jK").value = c.justificaciones?.k1 || "";
  renderLocs(); renderDemandas(); renderEtapas(); recalcular();
}

(async function init() {
  await cargarDeptos();
  let previo = null;
  try { previo = JSON.parse(localStorage.getItem("acueducto.req") || "null"); } catch {}
  if (previo?.localidades) cargarReq(previo); else { $("#anioIni").value = S.req.anio_inicial; renderLocs(); }
  $("#anioFin").textContent = S.req.anio_inicial + S.req.periodo_diseno;
})();
