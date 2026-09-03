"use strict";

const $ = id => document.getElementById(id);
const SIDEBAR_STORAGE_KEY = "flumeworks.sidebarCollapsed";
let bootstrap = null, modelDesignReady = false, loadedModelProject = "", lastModelDesignSignature = "", modelSyncBusy = false, captureSequence = 0;
let conditionDisplayMode = "prototype", panelProjectIdentity = null;
const captureRequests = new Map();

function setSidebarCollapsed(collapsed) {
  const shell = document.querySelector(".app-shell"), button = $("sidebarToggle");
  shell.classList.toggle("sidebar-collapsed", collapsed);
  button.textContent = collapsed ? "›" : "‹";
  button.setAttribute("aria-expanded", String(!collapsed));
  button.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
  button.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
  try { localStorage.setItem(SIDEBAR_STORAGE_KEY, String(collapsed)); } catch {}
}

function setStatus(message, kind = "") {
  $("status").textContent = message;
  $("status").className = "status" + (kind ? ` ${kind}` : "");
}

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json", ...(options.headers || {})}, ...options});
  let payload = {};
  try { payload = await response.json(); } catch {}
  if (!response.ok) {
    const detail = payload.detail;
    const message = typeof detail === "string" ? detail : detail && typeof detail.message === "string" ? detail.message : `Request failed with HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

function textOrDash(value, suffix = "") { return value === null || value === undefined || value === "" ? "—" : `${value}${suffix}`; }
function numberOrNull(form, name) { const value = String(new FormData(form).get(name) || "").trim(); return value === "" ? null : Number(value); }

function showView(name) {
  document.querySelectorAll(".workspace").forEach(view => view.classList.toggle("active", view.id === `${name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())}View`));
  document.querySelectorAll(".nav-item").forEach(button => { const active = button.dataset.view === name; button.classList.toggle("active", active); button.setAttribute("aria-selected", String(active)); });
}

async function nativePath(method, ...args) {
  if (window.pywebview && window.pywebview.api && typeof window.pywebview.api[method] === "function") return window.pywebview.api[method](...args);
  const promptText = method === "choose_open_project" ? "Enter the full path of a .flumeworks project:" : "Enter the full path for the new .flumeworks project:";
  return window.prompt(promptText) || null;
}

function renderProjects() {
  const root = $("projectList"), projects = bootstrap.recentProjects || [];
  root.replaceChildren();
  if (!projects.length) {
    const empty = document.createElement("p"); empty.className = "empty"; empty.textContent = "No recent FlumeWorks projects on this computer."; root.appendChild(empty); return;
  }
  for (const project of projects) {
    const row = document.createElement("div"); row.className = "project-row" + (project.available ? "" : " unavailable");
    const info = document.createElement("div"), name = document.createElement("strong"), meta = document.createElement("small"), path = document.createElement("small"), button = document.createElement("button");
    name.textContent = project.name || "FlumeWorks project";
    meta.textContent = [project.project_number, project.facility_name, !project.available ? "Location unavailable" : ""].filter(Boolean).join(" · ");
    path.className = "path"; path.textContent = project.path || ""; path.title = project.path || "";
    button.type = "button"; button.className = "secondary"; button.textContent = "Open"; button.disabled = !project.available; button.addEventListener("click", () => openProject(project.path));
    info.append(name, meta, path); row.append(info, button); root.appendChild(row);
  }
}

function projectScale() {
  const scale = Number(bootstrap?.currentProject?.project?.model_scale_denominator);
  return Number.isFinite(scale) && scale > 0 ? scale : null;
}

function conditionLength(value, scale) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return conditionDisplayMode === "model" ? `${(number * 1000 / scale).toFixed(0)} mm` : `${number.toFixed(2)} m`;
}

function conditionPeriod(value, scale) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return `${(conditionDisplayMode === "model" ? number / Math.sqrt(scale) : number).toFixed(2)} s`;
}

function setConditionDisplay(mode) {
  if (mode === "model" && !projectScale()) return;
  conditionDisplayMode = mode;
  renderCurrentProject();
}

function closeConditionEditor() {
  const form = $("conditionForm"); form.reset(); form.hidden = true;
  $("conditionEditorTitle").textContent = "Add design condition"; $("saveCondition").textContent = "Add condition";
}

function openConditionEditor() {
  closeConditionEditor(); $("conditionForm").hidden = false; $("conditionForm").elements.namedItem("condition_number").focus();
}

function editCondition(condition) {
  const form = $("conditionForm"); form.reset();
  form.elements.namedItem("condition_id").value = condition.id;
  for (const name of ["condition_number", "target_hs_m", "target_tp_s", "water_level_m_ahd", "wave_stats_depth_m_ahd", "aep_percent", "ari_years", "notes"]) {
    const input = form.elements.namedItem(name); if (input) input.value = condition[name] ?? "";
  }
  $("conditionEditorTitle").textContent = `Edit condition ${condition.condition_number}`; $("saveCondition").textContent = "Update condition"; form.hidden = false;
  form.elements.namedItem("condition_number").focus(); form.scrollIntoView({behavior: "smooth", block: "nearest"});
}

async function deleteCondition(condition) {
  if (!window.confirm(`Delete design condition ${condition.condition_number}?`)) return;
  try {
    await persistModelDesign();
    const payload = await api(`/api/design-conditions/${condition.id}`, {method: "DELETE"});
    bootstrap.currentProject = payload.currentProject; closeConditionEditor(); renderCurrentProject(); loadModelDesignForCurrentProject(true);
    setStatus(`Deleted design condition ${condition.condition_number}. Save the project when ready.`, "success");
  } catch (error) { setStatus(error.message, "error"); }
}

function renderConditionRows(conditions, scale) {
  const model = conditionDisplayMode === "model";
  $("hsHeading").textContent = model ? "Target Hs (mm)" : "Target Hs (m)";
  $("tpHeading").textContent = model ? "Target Tp — model (s)" : "Target Tp — prototype (s)";
  $("waterHeading").textContent = model ? "Water level (mm)" : "Water level (m AHD)";
  $("depthHeading").textContent = model ? "Wave stats depth (mm)" : "Wave stats depth (m AHD)";
  const rows = $("conditionRows"); rows.replaceChildren(); $("conditionEmpty").hidden = conditions.length > 0;
  for (const condition of conditions) {
    const row = document.createElement("tr");
    const values = [condition.condition_number, conditionLength(condition.target_hs_m, scale), conditionPeriod(condition.target_tp_s, scale), conditionLength(condition.water_level_m_ahd, scale), conditionLength(condition.wave_stats_depth_m_ahd, scale), textOrDash(condition.aep_percent, "%"), textOrDash(condition.ari_years, " yr")];
    for (const value of values) { const cell = document.createElement("td"); cell.textContent = value; row.appendChild(cell); }
    const actions = document.createElement("td"), edit = document.createElement("button"), remove = document.createElement("button");
    edit.type = remove.type = "button"; edit.className = "table-action"; edit.textContent = "Edit"; edit.addEventListener("click", () => editCondition(condition));
    remove.className = "table-action danger"; remove.textContent = "Delete"; remove.addEventListener("click", () => deleteCondition(condition));
    actions.append(edit, remove); row.appendChild(actions); rows.appendChild(row);
  }
}

function renderCurrentProject() {
  const current = bootstrap.currentProject, visible = Boolean(current && current.project);
  $("currentProjectCard").hidden = !visible; $("conditionCard").hidden = !visible;
  const identity = visible ? current.project.uuid : "__none__";
  if (identity !== panelProjectIdentity) { $("createProjectPanel").open = !visible; $("openProjectPanel").open = !visible; panelProjectIdentity = identity; }
  if (!visible) { $("activeProjectBadge").textContent = "No project open"; closeConditionEditor(); return; }
  const project = current.project, conditions = current.designConditions || [], scale = projectScale();
  $("activeProjectBadge").textContent = project.project_number || project.name; $("currentProjectName").textContent = project.name; $("currentFacility").textContent = project.facility_name;
  $("currentProjectNumber").textContent = textOrDash(project.project_number); $("currentDatabase").textContent = project.database_path; $("currentDatabase").title = project.database_path;
  $("currentUpdated").textContent = new Date(project.updated_at).toLocaleString(); $("currentDescription").textContent = project.description || "No project description.";
  $("saveState").textContent = current.dirty ? "Unsaved changes" : "Saved"; $("saveState").classList.toggle("unsaved", Boolean(current.dirty));
  $("conditionCount").textContent = `${conditions.length} condition${conditions.length === 1 ? "" : "s"}`;
  $("conditionSource").textContent = project.wave_conditions_filename ? `Source: ${project.wave_conditions_filename}` : "Entered directly in FlumeWorks";
  $("projectScaleInput").value = scale ?? ""; $("projectScaleSummary").textContent = scale ? `1:${scale} · time scale 1:${Math.sqrt(scale).toFixed(2)}` : "No project scale set.";
  $("showModelConditions").disabled = !scale; if (!scale && conditionDisplayMode === "model") conditionDisplayMode = "prototype";
  $("showPrototypeConditions").classList.toggle("active", conditionDisplayMode === "prototype"); $("showModelConditions").classList.toggle("active", conditionDisplayMode === "model");
  renderConditionRows(conditions, scale || 1);
}

function modelFrame() { return $("modelDesignFrame").contentWindow; }
function modelOrigin() { return new URL(bootstrap.modelDesignUrl).origin; }
function modelDesignSignature(state) { if (!state) return ""; const canonical = {...state}; delete canonical.exportedAt; return JSON.stringify(canonical); }

function sharedWaveConditions() {
  const current = bootstrap?.currentProject; if (!current) return [];
  return (current.designConditions || []).filter(condition => [condition.water_level_m_ahd, condition.wave_stats_depth_m_ahd, condition.target_hs_m, condition.target_tp_s].every(value => value !== null && value !== undefined && Number.isFinite(Number(value)))).map((condition, index) => ({
    conditionId: String(condition.condition_number), ari: condition.ari_years === null || condition.ari_years === undefined ? "" : String(condition.ari_years), waterLevel: Number(condition.water_level_m_ahd), statsDepth: Number(condition.wave_stats_depth_m_ahd), waveHeight: Number(condition.target_hs_m), period: Number(condition.target_tp_s), sourceRow: index + 2,
  }));
}

function loadModelDesignForCurrentProject(force = false) {
  if (!bootstrap || !modelDesignReady) return;
  const current = bootstrap.currentProject, identity = current && current.project ? current.project.uuid : "__none__";
  if (!force && loadedModelProject === identity) return;
  const state = current ? current.modelDesignState : null;
  modelFrame().postMessage({type: "flumeworks:load-model-design", state, sharedWaveConditions: current ? sharedWaveConditions() : null, waveConditionsFilename: current?.project?.wave_conditions_filename || "FlumeWorks design conditions"}, modelOrigin());
  loadedModelProject = identity; lastModelDesignSignature = modelDesignSignature(state);
}

function captureModelDesign(timeout = 4000) {
  if (!modelDesignReady) return Promise.resolve(null);
  const requestId = `capture-${Date.now()}-${++captureSequence}`;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { captureRequests.delete(requestId); reject(new Error("Model Design did not respond in time.")); }, timeout);
    captureRequests.set(requestId, {resolve: value => { clearTimeout(timer); resolve(value); }}); modelFrame().postMessage({type: "flumeworks:capture-model-design", requestId}, modelOrigin());
  });
}

async function persistModelDesign() {
  if (!bootstrap.currentProject) return;
  const state = await captureModelDesign(); if (!state) return;
  const signature = modelDesignSignature(state); if (signature === lastModelDesignSignature) return;
  const payload = await api("/api/model-design", {method: "PUT", body: JSON.stringify({state})});
  bootstrap.currentProject = payload.currentProject; lastModelDesignSignature = signature; renderCurrentProject();
}

function renderBootstrap() {
  $("appVersion").textContent = `Version ${bootstrap.application.version}`; $("gitCommit").textContent = `Commit ${bootstrap.application.gitCommit}`;
  const facilities = $("facilitySelect"); facilities.replaceChildren();
  for (const facility of bootstrap.facilities) { const option = document.createElement("option"); option.value = facility.id; option.textContent = facility.name; facilities.appendChild(option); }
  if (bootstrap.modelDesignUrl && $("modelDesignFrame").src !== bootstrap.modelDesignUrl) $("modelDesignFrame").src = bootstrap.modelDesignUrl;
  renderProjects(); renderCurrentProject(); loadModelDesignForCurrentProject();
}

async function refresh(message = "") { bootstrap = await api("/api/bootstrap"); renderBootstrap(); setStatus(message || "FlumeWorks is ready.", "success"); }

async function openProject(sourcePath) {
  if (!sourcePath) return;
  try {
    const payload = await api("/api/projects/open", {method: "POST", body: JSON.stringify({source_path: sourcePath})});
    bootstrap.currentProject = payload.currentProject; bootstrap.recentProjects = payload.recentProjects; loadedModelProject = ""; closeConditionEditor(); renderProjects(); renderCurrentProject(); loadModelDesignForCurrentProject(true);
    setStatus(`Opened ${payload.currentProject.project.name} with an exclusive edit lock.`, "success");
  } catch (error) { setStatus(error.message, "error"); }
}

async function saveProject() {
  try {
    await persistModelDesign(); const payload = await api("/api/projects/save", {method: "POST"}); bootstrap.currentProject = payload.currentProject; bootstrap.recentProjects = payload.recentProjects; renderProjects(); renderCurrentProject(); setStatus("Project saved to its .flumeworks file.", "success");
  } catch (error) { setStatus(error.message, "error"); }
}

function parseCsvRows(text) {
  text = String(text).replace(/^\uFEFF/, ""); const rows = []; let row = [], field = "", quoted = false;
  for (let index = 0; index < text.length; index++) {
    const character = text[index];
    if (quoted) { if (character === '"' && text[index + 1] === '"') { field += '"'; index++; } else if (character === '"') quoted = false; else field += character; }
    else if (character === '"' && field.length === 0) quoted = true;
    else if (character === ",") { row.push(field); field = ""; }
    else if (character === "\r" || character === "\n") { if (character === "\r" && text[index + 1] === "\n") index++; row.push(field); rows.push(row); row = []; field = ""; }
    else field += character;
  }
  if (quoted) throw new Error("The CSV contains an unterminated quoted field.");
  if (field.length || row.length) { row.push(field); rows.push(row); }
  return rows.filter(cells => cells.some(value => value.trim() !== ""));
}

function parseDesignConditionsCsv(text) {
  const rows = parseCsvRows(text); if (!rows.length) throw new Error("The wave-condition CSV is empty.");
  const normalise = value => value.replace(/^\uFEFF/, "").toLowerCase().replace(/[\s_-]+/g, " ").trim().replace(/[^a-z0-9 ]+/g, "");
  const headers = rows[0].map(normalise);
  const required = (label, predicate) => { const index = headers.findIndex(predicate); if (index < 0) throw new Error(`The CSV is missing ${label}. Found: ${rows[0].join(" | ")}`); return index; };
  const columns = {id: required("Condition ID", value => value.includes("condition") && value.includes("id")), water: required("Water level", value => value.includes("water") && value.includes("level")), depth: required("Wave stats depth", value => value.includes("wave") && value.includes("depth")), height: required("Wave height (Hm0)", value => value.includes("height") && value.includes("hm0")), period: required("Wave period Tp", value => value.includes("period") && value.includes("tp")), ari: headers.findIndex(value => value === "ari" || (value.includes("average recurrence") && value.includes("interval"))), aep: headers.findIndex(value => value === "aep" || value.includes("annual exceedance probability"))};
  const numeric = value => Number(String(value).trim().replace(/\u2212/g, "-").replace(/[,\s]/g, ""));
  const optionalNumber = value => String(value ?? "").trim() === "" ? null : numeric(value);
  const conditions = [], invalid = [];
  for (let index = 1; index < rows.length; index++) {
    const cells = rows[index], conditionNumber = String(cells[columns.id] ?? "").trim();
    const water = numeric(cells[columns.water] ?? ""), depth = numeric(cells[columns.depth] ?? ""), height = numeric(cells[columns.height] ?? ""), period = numeric(cells[columns.period] ?? "");
    const ari = columns.ari >= 0 ? optionalNumber(cells[columns.ari]) : null, aep = columns.aep >= 0 ? optionalNumber(cells[columns.aep]) : null;
    if (!conditionNumber || ![water, depth, height, period].every(Number.isFinite) || height < 0 || period <= 0 || water <= depth || (ari !== null && (!Number.isFinite(ari) || ari <= 0)) || (aep !== null && (!Number.isFinite(aep) || aep <= 0 || aep > 100))) invalid.push(index + 1);
    else conditions.push({condition_number: conditionNumber, target_hs_m: height, target_tp_s: period, water_level_m_ahd: water, wave_stats_depth_m_ahd: depth, aep_percent: aep, ari_years: ari, notes: ""});
  }
  if (invalid.length) throw new Error(`The CSV has invalid data on row${invalid.length === 1 ? "" : "s"} ${invalid.join(", ")}. The existing table was not changed.`);
  if (!conditions.length) throw new Error("The wave-condition CSV contains no data rows.");
  return conditions;
}

document.querySelectorAll(".nav-item").forEach(button => button.addEventListener("click", () => showView(button.dataset.view)));
$("sidebarToggle").addEventListener("click", () => setSidebarCollapsed(!document.querySelector(".app-shell").classList.contains("sidebar-collapsed")));
try { setSidebarCollapsed(localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true"); } catch { setSidebarCollapsed(false); }

$("refreshProjects").addEventListener("click", () => refresh("Recent project locations refreshed.").catch(error => setStatus(error.message, "error")));
$("chooseNewProjectPath").addEventListener("click", async () => { const form = $("createProjectForm"), data = new FormData(form), path = await nativePath("choose_new_project", String(data.get("project_number") || ""), String(data.get("name") || "")); if (path) $("newProjectPath").value = path; });
$("openProjectFile").addEventListener("click", async () => openProject(await nativePath("choose_open_project")));
$("createProjectForm").addEventListener("submit", async event => {
  event.preventDefault(); const form = event.currentTarget, data = new FormData(form);
  const request = {destination_path: String(data.get("destination_path") || ""), name: String(data.get("name") || ""), project_number: String(data.get("project_number") || ""), facility: String(data.get("facility") || ""), description: String(data.get("description") || "")};
  try {
    const payload = await api("/api/projects", {method: "POST", body: JSON.stringify(request)}); bootstrap.recentProjects = payload.recentProjects; bootstrap.currentProject = payload.currentProject; loadedModelProject = ""; renderProjects(); renderCurrentProject(); loadModelDesignForCurrentProject(true); form.reset();
    setStatus(`Created ${payload.currentProject.project.name}. The project is locked for this FlumeWorks session.`, "success");
  } catch (error) { setStatus(error.message, "error"); }
});

$("projectScaleForm").addEventListener("submit", async event => {
  event.preventDefault(); const denominator = numberOrNull(event.currentTarget, "denominator");
  try { const payload = await api("/api/project-scale", {method: "PUT", body: JSON.stringify({denominator})}); bootstrap.currentProject = payload.currentProject; renderCurrentProject(); setStatus(denominator ? `Project scale set to 1:${denominator}.` : "Project scale cleared.", "success"); }
  catch (error) { setStatus(error.message, "error"); }
});
$("clearProjectScale").addEventListener("click", async () => {
  try { const payload = await api("/api/project-scale", {method: "PUT", body: JSON.stringify({denominator: null})}); bootstrap.currentProject = payload.currentProject; renderCurrentProject(); setStatus("Project scale cleared. Prototype values remain available.", "success"); }
  catch (error) { setStatus(error.message, "error"); }
});

$("addCondition").addEventListener("click", openConditionEditor); $("cancelConditionEdit").addEventListener("click", closeConditionEditor);
$("showPrototypeConditions").addEventListener("click", () => setConditionDisplay("prototype")); $("showModelConditions").addEventListener("click", () => setConditionDisplay("model"));
$("conditionForm").addEventListener("submit", async event => {
  event.preventDefault(); const form = event.currentTarget, data = new FormData(form), conditionId = String(data.get("condition_id") || "").trim();
  const request = {condition_number: String(data.get("condition_number") || ""), target_hs_m: numberOrNull(form, "target_hs_m"), target_tp_s: numberOrNull(form, "target_tp_s"), water_level_m_ahd: numberOrNull(form, "water_level_m_ahd"), wave_stats_depth_m_ahd: numberOrNull(form, "wave_stats_depth_m_ahd"), aep_percent: numberOrNull(form, "aep_percent"), ari_years: numberOrNull(form, "ari_years"), notes: String(data.get("notes") || "")};
  if (request.water_level_m_ahd <= request.wave_stats_depth_m_ahd) { setStatus("Water level must be above the wave-stats depth.", "error"); return; }
  try {
    await persistModelDesign(); const path = conditionId ? `/api/design-conditions/${conditionId}` : "/api/design-conditions";
    const payload = await api(path, {method: conditionId ? "PUT" : "POST", body: JSON.stringify(request)}); bootstrap.currentProject = payload.currentProject; closeConditionEditor(); renderCurrentProject(); loadModelDesignForCurrentProject(true);
    setStatus(`${conditionId ? "Updated" : "Added"} design condition ${payload.condition.condition_number}. Save the project when ready.`, "success");
  } catch (error) { setStatus(error.message, "error"); }
});

$("loadWaveConditionsCsv").addEventListener("click", () => $("waveConditionsCsv").click());
$("waveConditionsCsv").addEventListener("change", async event => {
  const file = event.target.files[0]; event.target.value = ""; if (!file) return;
  try {
    const conditions = parseDesignConditionsCsv(await file.text()); await persistModelDesign();
    const payload = await api("/api/design-conditions/import", {method: "PUT", body: JSON.stringify({source_filename: file.name, conditions})}); bootstrap.currentProject = payload.currentProject; closeConditionEditor(); renderCurrentProject(); loadModelDesignForCurrentProject(true);
    setStatus(`Loaded ${payload.importedCount} design conditions from ${file.name}. The previous condition table was replaced.`, "success");
  } catch (error) { setStatus(error.message, "error"); }
});

document.querySelectorAll(".info-button").forEach(button => button.addEventListener("click", event => {
  event.stopPropagation(); const target = $(button.getAttribute("aria-controls")), open = target.hidden;
  document.querySelectorAll(".info-popover").forEach(popover => { popover.hidden = true; }); document.querySelectorAll(".info-button").forEach(item => item.setAttribute("aria-expanded", "false"));
  target.hidden = !open; button.setAttribute("aria-expanded", String(open));
}));
document.addEventListener("click", () => { document.querySelectorAll(".info-popover").forEach(popover => { popover.hidden = true; }); document.querySelectorAll(".info-button").forEach(item => item.setAttribute("aria-expanded", "false")); });
document.querySelectorAll(".info-popover").forEach(popover => popover.addEventListener("click", event => event.stopPropagation()));

$("saveProject").addEventListener("click", saveProject);
$("backupProject").addEventListener("click", async () => {
  try { await persistModelDesign(); const payload = await api("/api/projects/backup", {method: "POST"}); bootstrap.currentProject = payload.currentProject; renderCurrentProject(); setStatus(`Backup created: ${payload.backupPath}`, "success"); }
  catch (error) { setStatus(error.message, "error"); }
});
$("closeProject").addEventListener("click", async () => {
  try {
    await persistModelDesign(); const payload = await api("/api/projects/close", {method: "POST"}); bootstrap.currentProject = null; bootstrap.recentProjects = payload.recentProjects; loadedModelProject = ""; renderProjects(); renderCurrentProject(); loadModelDesignForCurrentProject(true); setStatus("Project saved and closed. Its edit lock was released.", "success");
  } catch (error) { setStatus(error.message, "error"); }
});

window.addEventListener("message", event => {
  if (!bootstrap || event.source !== modelFrame() || event.origin !== new URL(bootstrap.modelDesignUrl).origin || !event.data) return;
  if (event.data.type === "flumeworks:model-design-ready") { modelDesignReady = true; loadedModelProject = ""; loadModelDesignForCurrentProject(true); }
  if (event.data.type === "flumeworks:model-design-state") { const request = captureRequests.get(event.data.requestId); if (request) { captureRequests.delete(event.data.requestId); request.resolve(event.data.state); } }
  if (event.data.type === "flumeworks:model-design-error") setStatus(`Model Design: ${event.data.message}`, "error");
});

setInterval(async () => {
  if (modelSyncBusy || !bootstrap || !bootstrap.currentProject || !modelDesignReady) return;
  modelSyncBusy = true;
  try { await persistModelDesign(); } catch (error) { setStatus(`Model Design could not be added to the local working copy: ${error.message}`, "error"); } finally { modelSyncBusy = false; }
}, 2500);

refresh().catch(error => setStatus(`FlumeWorks could not start: ${error.message}`, "error"));
