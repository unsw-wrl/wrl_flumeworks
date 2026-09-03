"use strict";

const $ = id => document.getElementById(id);
const SIDEBAR_STORAGE_KEY = "flumeworks.sidebarCollapsed";
let bootstrap = null, modelDesignReady = false, loadedModelProject = "", lastModelDesignSignature = "", modelSyncBusy = false, captureSequence = 0;
let conditionDisplayMode = "prototype", panelProjectIdentity = null;
let conditionEditMode = false, conditionDraft = [], conditionDraftInitial = "", conditionDraftSequence = 0;
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

function linkedAep(condition) {
  if (condition.aep_percent !== null && condition.aep_percent !== undefined) return Number(condition.aep_percent);
  const ari = Number(condition.ari_years);
  return Number.isFinite(ari) && ari > 0 ? 100 / ari : null;
}

function linkedAri(condition) {
  if (condition.ari_years !== null && condition.ari_years !== undefined) return Number(condition.ari_years);
  const aep = Number(condition.aep_percent);
  return Number.isFinite(aep) && aep > 0 ? 100 / aep : null;
}

function probabilityText(value, suffix) {
  if (value === null || !Number.isFinite(value)) return "—";
  return `${Number.isInteger(value) ? value.toFixed(0) : value.toFixed(2)}${suffix}`;
}

function probabilityInputValue(value) {
  return Number.isFinite(value) && value > 0 ? String(Number(value.toFixed(6))) : "";
}

const CONDITION_FIELDS = ["condition_number", "target_hs_m", "target_tp_s", "water_level_m_ahd", "wave_stats_depth_m_ahd", "aep_percent", "ari_years", "notes"];

function draftCondition(condition = {}) {
  return {
    _key: condition._key || `condition-${condition.id ?? `new-${++conditionDraftSequence}`}`,
    condition_number: String(condition.condition_number ?? ""),
    target_hs_m: condition.target_hs_m ?? null,
    target_tp_s: condition.target_tp_s ?? null,
    water_level_m_ahd: condition.water_level_m_ahd ?? null,
    wave_stats_depth_m_ahd: condition.wave_stats_depth_m_ahd ?? null,
    aep_percent: linkedAep(condition),
    ari_years: linkedAri(condition),
    notes: String(condition.notes ?? ""),
  };
}

function draftSignature(conditions = conditionDraft) {
  return JSON.stringify(conditions.map(condition => Object.fromEntries(CONDITION_FIELDS.map(field => [field, condition[field]]))));
}

function conditionDraftDirty() { return draftSignature() !== conditionDraftInitial; }

function resetConditionEditing() {
  conditionEditMode = false; conditionDraft = []; conditionDraftInitial = "";
}

function beginConditionEditing() {
  if (!bootstrap?.currentProject) return;
  conditionDisplayMode = "prototype";
  conditionDraft = (bootstrap.currentProject.designConditions || []).map(condition => draftCondition(condition));
  conditionDraftInitial = draftSignature(); conditionEditMode = true; renderCurrentProject();
}

function cancelConditionEditing() {
  if (conditionDraftDirty() && !window.confirm("Cancel editing? Any unsaved condition changes will be lost.")) return;
  resetConditionEditing(); renderCurrentProject(); setStatus("Condition changes were not saved.");
}

function addConditionDraftRow() {
  const row = draftCondition(); conditionDraft.push(row); renderCurrentProject();
  document.querySelector(`[data-condition-key="${row._key}"] [data-field="condition_number"]`)?.focus();
}

function moveConditionDraft(index, offset) {
  const destination = index + offset;
  if (destination < 0 || destination >= conditionDraft.length) return;
  const [condition] = conditionDraft.splice(index, 1); conditionDraft.splice(destination, 0, condition); renderCurrentProject();
}

function removeConditionDraft(index) {
  conditionDraft.splice(index, 1); renderCurrentProject();
}

function updateDraftField(index, field, input) {
  const condition = conditionDraft[index]; if (!condition) return;
  condition[field] = field === "condition_number" || field === "notes" ? input.value : input.value === "" ? null : Number(input.value);
  if ((field === "aep_percent" || field === "ari_years") && condition[field] !== null && Number.isFinite(condition[field]) && condition[field] > 0) {
    const partner = field === "aep_percent" ? "ari_years" : "aep_percent", value = 100 / condition[field];
    condition[partner] = value;
    const partnerInput = input.closest("tr")?.querySelector(`[data-field="${partner}"]`);
    if (partnerInput) partnerInput.value = probabilityInputValue(value);
  } else if (field === "aep_percent" || field === "ari_years") {
    const partner = field === "aep_percent" ? "ari_years" : "aep_percent";
    condition[partner] = null;
    const partnerInput = input.closest("tr")?.querySelector(`[data-field="${partner}"]`);
    if (partnerInput) partnerInput.value = "";
  }
}

function validateConditionDraft() {
  const seen = new Set();
  for (let index = 0; index < conditionDraft.length; index++) {
    const condition = conditionDraft[index], row = index + 1, number = condition.condition_number.trim();
    if (!number) return {message: `Row ${row}: Condition ID is required.`, index, field: "condition_number"};
    if (seen.has(number.toLowerCase())) return {message: `Row ${row}: Condition ID ${number} is duplicated.`, index, field: "condition_number"};
    seen.add(number.toLowerCase());
    if (!Number.isFinite(condition.target_hs_m) || condition.target_hs_m < 0) return {message: `Row ${row}: Target Hs must be zero or greater.`, index, field: "target_hs_m"};
    if (!Number.isFinite(condition.target_tp_s) || condition.target_tp_s <= 0) return {message: `Row ${row}: Target Tp must be greater than zero.`, index, field: "target_tp_s"};
    if (!Number.isFinite(condition.water_level_m_ahd)) return {message: `Row ${row}: Water level is required.`, index, field: "water_level_m_ahd"};
    if (!Number.isFinite(condition.wave_stats_depth_m_ahd)) return {message: `Row ${row}: Wave stats depth is required.`, index, field: "wave_stats_depth_m_ahd"};
    if (condition.water_level_m_ahd <= condition.wave_stats_depth_m_ahd) return {message: `Row ${row}: Water level must be above the wave-stats depth.`, index, field: "water_level_m_ahd"};
    if (condition.aep_percent !== null && (!Number.isFinite(condition.aep_percent) || condition.aep_percent <= 0 || condition.aep_percent > 100)) return {message: `Row ${row}: AEP must be greater than 0% and no more than 100%.`, index, field: "aep_percent"};
    if (condition.ari_years !== null && (!Number.isFinite(condition.ari_years) || condition.ari_years < 1)) return {message: `Row ${row}: ARI must be at least 1 year.`, index, field: "ari_years"};
  }
  return null;
}

async function saveConditionDraft() {
  const invalid = validateConditionDraft();
  if (invalid) {
    setStatus(invalid.message, "error");
    document.querySelectorAll("#conditionRows tr")[invalid.index]?.querySelector(`[data-field="${invalid.field}"]`)?.focus();
    return;
  }
  if (!conditionDraftDirty()) { resetConditionEditing(); renderCurrentProject(); setStatus("No condition changes to save."); return; }
  if (!window.confirm("Save all changes to the design wave conditions?")) return;
  try {
    await persistModelDesign();
    const conditions = conditionDraft.map(condition => Object.fromEntries(CONDITION_FIELDS.map(field => [field, condition[field]])));
    const payload = await api("/api/design-conditions", {method: "PUT", body: JSON.stringify({conditions})});
    bootstrap.currentProject = payload.currentProject; resetConditionEditing(); renderCurrentProject(); loadModelDesignForCurrentProject(true);
    setStatus(`Saved ${payload.savedCount} design condition${payload.savedCount === 1 ? "" : "s"}. Save the project when ready.`, "success");
  } catch (error) { setStatus(error.message, "error"); }
}

function setConditionDisplay(mode) {
  if (conditionEditMode || (mode === "model" && !projectScale())) return;
  conditionDisplayMode = mode;
  renderCurrentProject();
}

function tableInput(condition, index, field, label, options = {}) {
  const input = document.createElement("input");
  input.type = options.type || "number"; input.dataset.field = field; input.setAttribute("aria-label", `${label}, row ${index + 1}`);
  if (options.min !== undefined) input.min = options.min; if (options.max !== undefined) input.max = options.max; if (options.step !== undefined) input.step = options.step;
  if (options.maxLength) input.maxLength = options.maxLength;
  const value = condition[field]; input.value = value === null || value === undefined ? "" : field === "aep_percent" || field === "ari_years" ? probabilityInputValue(Number(value)) : String(value);
  input.addEventListener("input", () => updateDraftField(index, field, input));
  return input;
}

function iconButton(kind, label, disabled = false) {
  const button = document.createElement("button"); button.type = "button"; button.className = `condition-icon-button ${kind}`; button.disabled = disabled; button.setAttribute("aria-label", label); button.title = label;
  if (kind === "delete") button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13M10 11v5m4-5v5"/></svg>';
  else button.textContent = kind === "up" ? "↑" : "↓";
  return button;
}

function renderConditionRows(conditions, scale) {
  const model = conditionDisplayMode === "model";
  $("hsHeading").innerHTML = model ? "Target H<sub>s</sub> (mm)" : "Target H<sub>s</sub> (m)";
  $("tpHeading").innerHTML = model ? "Target T<sub>p</sub> — model (s)" : "Target T<sub>p</sub> — prototype (s)";
  $("waterHeading").textContent = model ? "Water level (mm)" : "Water level (m AHD)";
  $("depthHeading").textContent = model ? "Wave stats depth (mm)" : "Wave stats depth (m AHD)";
  const rows = $("conditionRows"); rows.replaceChildren(); $("conditionEmpty").hidden = conditions.length > 0;
  conditions.forEach((condition, index) => {
    const row = document.createElement("tr"); row.dataset.conditionKey = condition._key || `condition-${condition.id}`; row.classList.toggle("editable-row", conditionEditMode);
    if (conditionEditMode) {
      const inputs = [
        tableInput(condition, index, "condition_number", "Condition ID", {type: "text", maxLength: 100}),
        tableInput(condition, index, "target_hs_m", "Target Hs", {min: "0", step: "any"}),
        tableInput(condition, index, "target_tp_s", "Target Tp", {min: "0.000001", step: "any"}),
        tableInput(condition, index, "water_level_m_ahd", "Water level", {step: "any"}),
        tableInput(condition, index, "wave_stats_depth_m_ahd", "Wave stats depth", {step: "any"}),
        tableInput(condition, index, "aep_percent", "AEP", {min: "0.000001", max: "100", step: "any"}),
        tableInput(condition, index, "ari_years", "ARI", {min: "1", step: "any"}),
        tableInput(condition, index, "notes", "Notes", {type: "text", maxLength: 4000}),
      ];
      inputs.forEach(input => { const cell = document.createElement("td"); cell.appendChild(input); row.appendChild(cell); });
      const conditionLabel = condition.condition_number || `row ${index + 1}`;
      const actions = document.createElement("td"), up = iconButton("up", `Move condition ${conditionLabel} up`, index === 0), down = iconButton("down", `Move condition ${conditionLabel} down`, index === conditions.length - 1), remove = iconButton("delete", `Delete condition ${conditionLabel}`);
      up.addEventListener("click", () => moveConditionDraft(index, -1)); down.addEventListener("click", () => moveConditionDraft(index, 1)); remove.addEventListener("click", () => removeConditionDraft(index));
      actions.className = "condition-row-actions"; actions.append(up, down, remove); row.appendChild(actions);
    } else {
      const values = [condition.condition_number, conditionLength(condition.target_hs_m, scale), conditionPeriod(condition.target_tp_s, scale), conditionLength(condition.water_level_m_ahd, scale), conditionLength(condition.wave_stats_depth_m_ahd, scale), probabilityText(linkedAep(condition), "%"), probabilityText(linkedAri(condition), " yr"), textOrDash(condition.notes)];
      for (const value of values) { const cell = document.createElement("td"); cell.textContent = value; row.appendChild(cell); }
    }
    rows.appendChild(row);
  });
  $("conditionActionsHeading").hidden = !conditionEditMode;
  $("conditionEditActions").hidden = !conditionEditMode; $("addConditionRow").hidden = !conditionEditMode;
  $("editConditions").hidden = conditionEditMode; $("loadWaveConditionsCsv").disabled = conditionEditMode;
  $("conditionCard").classList.toggle("editing-conditions", conditionEditMode);
}

function renderCurrentProject() {
  const current = bootstrap.currentProject, visible = Boolean(current && current.project);
  $("currentProjectCard").hidden = !visible; $("conditionCard").hidden = !visible;
  const identity = visible ? current.project.uuid : "__none__";
  if (identity !== panelProjectIdentity) { $("createProjectPanel").open = !visible; $("openProjectPanel").open = !visible; panelProjectIdentity = identity; }
  if (!visible) { $("activeProjectBadge").textContent = "No project open"; resetConditionEditing(); return; }
  const project = current.project, conditions = current.designConditions || [], scale = projectScale();
  $("activeProjectBadge").textContent = project.project_number || project.name; $("currentProjectName").textContent = project.name; $("currentFacility").textContent = project.facility_name;
  $("currentProjectNumber").textContent = textOrDash(project.project_number); $("currentDatabase").textContent = project.database_path; $("currentDatabase").title = project.database_path;
  $("currentUpdated").textContent = new Date(project.updated_at).toLocaleString(); $("currentDescription").textContent = project.description || "No project description.";
  $("saveState").textContent = current.dirty ? "Unsaved changes" : "Saved"; $("saveState").classList.toggle("unsaved", Boolean(current.dirty));
  const displayedConditions = conditionEditMode ? conditionDraft : conditions;
  $("conditionCount").textContent = `${displayedConditions.length} condition${displayedConditions.length === 1 ? "" : "s"}`;
  $("conditionSource").textContent = project.wave_conditions_filename ? `Source: ${project.wave_conditions_filename}` : "Entered directly in FlumeWorks";
  $("projectScaleInput").value = scale ?? ""; $("projectScaleSummary").textContent = scale ? `1:${scale} · time scale 1:${Math.sqrt(scale).toFixed(2)}` : "No project scale set.";
  $("showPrototypeConditions").disabled = conditionEditMode; $("showModelConditions").disabled = conditionEditMode || !scale; if (!scale && conditionDisplayMode === "model") conditionDisplayMode = "prototype";
  $("showPrototypeConditions").classList.toggle("active", conditionDisplayMode === "prototype"); $("showModelConditions").classList.toggle("active", conditionDisplayMode === "model");
  renderConditionRows(displayedConditions, scale || 1);
}

function modelFrame() { return $("modelDesignFrame").contentWindow; }
function modelOrigin() { return new URL(bootstrap.modelDesignUrl).origin; }
function modelDesignSignature(state) { if (!state) return ""; const canonical = {...state}; delete canonical.exportedAt; return JSON.stringify(canonical); }

function sharedWaveConditions() {
  const current = bootstrap?.currentProject; if (!current) return [];
  return (current.designConditions || []).filter(condition => [condition.water_level_m_ahd, condition.wave_stats_depth_m_ahd, condition.target_hs_m, condition.target_tp_s].every(value => value !== null && value !== undefined && Number.isFinite(Number(value)))).map((condition, index) => ({
    conditionId: String(condition.condition_number), ari: linkedAri(condition) === null ? "" : String(linkedAri(condition)), waterLevel: Number(condition.water_level_m_ahd), statsDepth: Number(condition.wave_stats_depth_m_ahd), waveHeight: Number(condition.target_hs_m), period: Number(condition.target_tp_s), sourceRow: index + 2,
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
  if (conditionEditMode && conditionDraftDirty() && !window.confirm("Open another project? Any unsaved condition changes will be lost.")) return;
  try {
    const payload = await api("/api/projects/open", {method: "POST", body: JSON.stringify({source_path: sourcePath})});
    bootstrap.currentProject = payload.currentProject; bootstrap.recentProjects = payload.recentProjects; loadedModelProject = ""; resetConditionEditing(); renderProjects(); renderCurrentProject(); loadModelDesignForCurrentProject(true);
    setStatus(`Opened ${payload.currentProject.project.name} with an exclusive edit lock.`, "success");
  } catch (error) { setStatus(error.message, "error"); }
}

async function saveProject() {
  if (conditionEditMode) { setStatus("Save or cancel the condition-table changes first.", "error"); return; }
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
    if (!conditionNumber || ![water, depth, height, period].every(Number.isFinite) || height < 0 || period <= 0 || water <= depth || (ari !== null && (!Number.isFinite(ari) || ari < 1)) || (aep !== null && (!Number.isFinite(aep) || aep <= 0 || aep > 100))) invalid.push(index + 1);
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
    const payload = await api("/api/projects", {method: "POST", body: JSON.stringify(request)}); bootstrap.recentProjects = payload.recentProjects; bootstrap.currentProject = payload.currentProject; loadedModelProject = ""; resetConditionEditing(); renderProjects(); renderCurrentProject(); loadModelDesignForCurrentProject(true); form.reset();
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

$("editConditions").addEventListener("click", beginConditionEditing); $("addConditionRow").addEventListener("click", addConditionDraftRow);
$("saveConditionChanges").addEventListener("click", saveConditionDraft); $("cancelConditionChanges").addEventListener("click", cancelConditionEditing);
$("showPrototypeConditions").addEventListener("click", () => setConditionDisplay("prototype")); $("showModelConditions").addEventListener("click", () => setConditionDisplay("model"));

$("loadWaveConditionsCsv").addEventListener("click", () => $("waveConditionsCsv").click());
$("waveConditionsCsv").addEventListener("change", async event => {
  const file = event.target.files[0]; event.target.value = ""; if (!file) return;
  try {
    const conditions = parseDesignConditionsCsv(await file.text()); await persistModelDesign();
    const payload = await api("/api/design-conditions/import", {method: "PUT", body: JSON.stringify({source_filename: file.name, conditions})}); bootstrap.currentProject = payload.currentProject; resetConditionEditing(); renderCurrentProject(); loadModelDesignForCurrentProject(true);
    setStatus(`Loaded ${payload.importedCount} design conditions from ${file.name}. The previous condition table was replaced.`, "success");
  } catch (error) { setStatus(error.message, "error"); }
});

function closeInfoPopovers() {
  document.querySelectorAll(".info-popover").forEach(popover => { popover.hidden = true; popover.classList.remove("table-info-popover"); popover.removeAttribute("style"); });
  document.querySelectorAll(".info-button").forEach(item => item.setAttribute("aria-expanded", "false"));
}

function positionTableInfoPopover(button, popover) {
  const buttonBox = button.getBoundingClientRect(), width = Math.min(390, window.innerWidth - 32);
  popover.classList.add("table-info-popover"); popover.style.width = `${width}px`; popover.style.left = `${Math.max(16, Math.min(buttonBox.right - width, window.innerWidth - width - 16))}px`;
  let top = buttonBox.bottom + 8;
  if (top + popover.offsetHeight > window.innerHeight - 16) top = Math.max(16, buttonBox.top - popover.offsetHeight - 8);
  popover.style.top = `${top}px`;
}

document.querySelectorAll(".info-button").forEach(button => button.addEventListener("click", event => {
  event.stopPropagation(); const target = $(button.getAttribute("aria-controls")), open = target.hidden;
  closeInfoPopovers();
  if (open) { target.hidden = false; button.setAttribute("aria-expanded", "true"); if (button.closest(".conditions-table")) positionTableInfoPopover(button, target); }
}));
document.addEventListener("click", closeInfoPopovers);
document.querySelector(".conditions-table").addEventListener("scroll", closeInfoPopovers);
window.addEventListener("resize", closeInfoPopovers);
document.querySelectorAll(".info-popover").forEach(popover => popover.addEventListener("click", event => event.stopPropagation()));

$("saveProject").addEventListener("click", saveProject);
$("backupProject").addEventListener("click", async () => {
  if (conditionEditMode) { setStatus("Save or cancel the condition-table changes first.", "error"); return; }
  try { await persistModelDesign(); const payload = await api("/api/projects/backup", {method: "POST"}); bootstrap.currentProject = payload.currentProject; renderCurrentProject(); setStatus(`Backup created: ${payload.backupPath}`, "success"); }
  catch (error) { setStatus(error.message, "error"); }
});
$("closeProject").addEventListener("click", async () => {
  if (conditionEditMode) { setStatus("Save or cancel the condition-table changes first.", "error"); return; }
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
