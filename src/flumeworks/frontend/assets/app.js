"use strict";

const $=id=>document.getElementById(id);
const SIDEBAR_STORAGE_KEY="flumeworks.sidebarCollapsed";
let bootstrap=null,modelDesignReady=false,loadedModelProject="",lastModelDesignSignature="",modelSyncBusy=false,captureSequence=0;
const captureRequests=new Map();

function setSidebarCollapsed(collapsed){
  const shell=document.querySelector(".app-shell"),button=$("sidebarToggle");
  shell.classList.toggle("sidebar-collapsed",collapsed);
  button.textContent=collapsed?"›":"‹";
  button.setAttribute("aria-expanded",String(!collapsed));
  button.setAttribute("aria-label",collapsed?"Expand sidebar":"Collapse sidebar");
  button.title=collapsed?"Expand sidebar":"Collapse sidebar";
  try{localStorage.setItem(SIDEBAR_STORAGE_KEY,String(collapsed));}catch{}
}

function setStatus(message,kind=""){
  $("status").textContent=message;
  $("status").className="status"+(kind?` ${kind}`:"");
}

async function api(path,options={}){
  const response=await fetch(path,{headers:{"Content-Type":"application/json",...(options.headers||{})},...options});
  let payload={};try{payload=await response.json();}catch{}
  if(!response.ok){
    const detail=payload.detail,message=typeof detail==="string"?detail:detail&&typeof detail.message==="string"?detail.message:`Request failed with HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

function textOrDash(value,suffix=""){
  return value===null||value===undefined||value===""?"—":`${value}${suffix}`;
}

function numberOrNull(form,name){
  const value=String(new FormData(form).get(name)||"").trim();
  return value===""?null:Number(value);
}

function showView(name){
  document.querySelectorAll(".workspace").forEach(view=>view.classList.toggle("active",view.id===`${name.replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase())}View`));
  document.querySelectorAll(".nav-item").forEach(button=>{const active=button.dataset.view===name;button.classList.toggle("active",active);button.setAttribute("aria-selected",String(active));});
}

async function nativePath(method,...args){
  if(window.pywebview&&window.pywebview.api&&typeof window.pywebview.api[method]==="function")return window.pywebview.api[method](...args);
  const promptText=method==="choose_open_project"?"Enter the full path of a .flumeworks project:":"Enter the full path for the new .flumeworks project:";
  return window.prompt(promptText)||null;
}

function renderProjects(){
  const root=$("projectList"),projects=bootstrap.recentProjects||[];root.replaceChildren();
  if(!projects.length){const empty=document.createElement("p");empty.className="empty";empty.textContent="No recent FlumeWorks projects on this computer.";root.appendChild(empty);return;}
  for(const project of projects){
    const row=document.createElement("div");row.className="project-row"+(project.available?"":" unavailable");
    const info=document.createElement("div"),name=document.createElement("strong"),meta=document.createElement("small"),path=document.createElement("small"),button=document.createElement("button");
    name.textContent=project.name||"FlumeWorks project";meta.textContent=[project.project_number,project.facility_name,!project.available?"Location unavailable":""].filter(Boolean).join(" · ");
    path.className="path";path.textContent=project.path||"";path.title=project.path||"";
    button.type="button";button.className="secondary";button.textContent="Open";button.disabled=!project.available;button.addEventListener("click",()=>openProject(project.path));
    info.append(name,meta,path);row.append(info,button);root.appendChild(row);
  }
}

function renderCurrentProject(){
  const current=bootstrap.currentProject,visible=Boolean(current&&current.project);
  $("currentProjectCard").hidden=!visible;$("conditionCard").hidden=!visible;
  if(!visible){$("activeProjectBadge").textContent="No project open";return;}
  const project=current.project,conditions=current.designConditions||[];
  $("activeProjectBadge").textContent=project.project_number||project.name;
  $("currentProjectName").textContent=project.name;$("currentFacility").textContent=project.facility_name;
  $("currentProjectNumber").textContent=textOrDash(project.project_number);$("currentDatabase").textContent=project.database_path;
  $("currentDatabase").title=project.database_path;$("currentUpdated").textContent=new Date(project.updated_at).toLocaleString();
  $("currentDescription").textContent=project.description||"No project description.";
  $("saveState").textContent=current.dirty?"Unsaved changes":"Saved";$("saveState").classList.toggle("unsaved",Boolean(current.dirty));
  $("conditionCount").textContent=`${conditions.length} condition${conditions.length===1?"":"s"}`;
  const rows=$("conditionRows");rows.replaceChildren();$("conditionEmpty").hidden=conditions.length>0;
  for(const condition of conditions){
    const row=document.createElement("tr");
    for(const value of [condition.condition_number,textOrDash(condition.target_hs_m," m"),textOrDash(condition.target_tp_s," s"),textOrDash(condition.water_level_m_ahd," m"),textOrDash(condition.aep_percent,"%"),textOrDash(condition.ari_years," yr")]){
      const cell=document.createElement("td");cell.textContent=value;row.appendChild(cell);
    }
    rows.appendChild(row);
  }
}

function modelFrame(){return $("modelDesignFrame").contentWindow;}
function modelOrigin(){return new URL(bootstrap.modelDesignUrl).origin;}
function modelDesignSignature(state){if(!state)return"";const canonical={...state};delete canonical.exportedAt;return JSON.stringify(canonical);}

function loadModelDesignForCurrentProject(force=false){
  if(!bootstrap||!modelDesignReady)return;
  const current=bootstrap.currentProject,identity=current&&current.project?current.project.uuid:"__none__";
  if(!force&&loadedModelProject===identity)return;
  const state=current?current.modelDesignState:null;
  modelFrame().postMessage({type:"flumeworks:load-model-design",state},modelOrigin());
  loadedModelProject=identity;lastModelDesignSignature=modelDesignSignature(state);
}

function captureModelDesign(timeout=4000){
  if(!modelDesignReady)return Promise.resolve(null);
  const requestId=`capture-${Date.now()}-${++captureSequence}`;
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{captureRequests.delete(requestId);reject(new Error("Model Design did not respond in time."));},timeout);
    captureRequests.set(requestId,{resolve:value=>{clearTimeout(timer);resolve(value);}});
    modelFrame().postMessage({type:"flumeworks:capture-model-design",requestId},modelOrigin());
  });
}

async function persistModelDesign(){
  if(!bootstrap.currentProject)return;
  const state=await captureModelDesign();if(!state)return;
  const signature=modelDesignSignature(state);if(signature===lastModelDesignSignature)return;
  const payload=await api("/api/model-design",{method:"PUT",body:JSON.stringify({state})});
  bootstrap.currentProject=payload.currentProject;lastModelDesignSignature=signature;renderCurrentProject();
}

function renderBootstrap(){
  $("appVersion").textContent=`Version ${bootstrap.application.version}`;$("gitCommit").textContent=`Commit ${bootstrap.application.gitCommit}`;
  const facilities=$("facilitySelect");facilities.replaceChildren();
  for(const facility of bootstrap.facilities){const option=document.createElement("option");option.value=facility.id;option.textContent=facility.name;facilities.appendChild(option);}
  if(bootstrap.modelDesignUrl&&$("modelDesignFrame").src!==bootstrap.modelDesignUrl)$("modelDesignFrame").src=bootstrap.modelDesignUrl;
  renderProjects();renderCurrentProject();loadModelDesignForCurrentProject();
}

async function refresh(message=""){
  bootstrap=await api("/api/bootstrap");renderBootstrap();if(message)setStatus(message,"success");else setStatus("FlumeWorks is ready.","success");
}

async function openProject(sourcePath){
  if(!sourcePath)return;
  try{
    const payload=await api("/api/projects/open",{method:"POST",body:JSON.stringify({source_path:sourcePath})});
    bootstrap.currentProject=payload.currentProject;bootstrap.recentProjects=payload.recentProjects;loadedModelProject="";renderProjects();renderCurrentProject();loadModelDesignForCurrentProject(true);setStatus(`Opened ${payload.currentProject.project.name} with an exclusive edit lock.`,"success");
  }catch(error){setStatus(error.message,"error");}
}

async function saveProject(){
  try{await persistModelDesign();const payload=await api("/api/projects/save",{method:"POST"});bootstrap.currentProject=payload.currentProject;bootstrap.recentProjects=payload.recentProjects;renderProjects();renderCurrentProject();setStatus("Project saved to its .flumeworks file.","success");}
  catch(error){setStatus(error.message,"error");}
}

document.querySelectorAll(".nav-item").forEach(button=>button.addEventListener("click",()=>showView(button.dataset.view)));
$("sidebarToggle").addEventListener("click",()=>setSidebarCollapsed(!document.querySelector(".app-shell").classList.contains("sidebar-collapsed")));
try{setSidebarCollapsed(localStorage.getItem(SIDEBAR_STORAGE_KEY)==="true");}catch{setSidebarCollapsed(false);}

$("refreshProjects").addEventListener("click",()=>refresh("Recent project locations refreshed.").catch(error=>setStatus(error.message,"error")));
$("chooseNewProjectPath").addEventListener("click",async()=>{
  const form=$("createProjectForm"),data=new FormData(form),path=await nativePath("choose_new_project",String(data.get("project_number")||""),String(data.get("name")||""));if(path)$("newProjectPath").value=path;
});
$("openProjectFile").addEventListener("click",async()=>openProject(await nativePath("choose_open_project")));
$("createProjectForm").addEventListener("submit",async event=>{
  event.preventDefault();const form=event.currentTarget,data=new FormData(form);
  const request={destination_path:String(data.get("destination_path")||""),name:String(data.get("name")||""),project_number:String(data.get("project_number")||""),facility:String(data.get("facility")||""),description:String(data.get("description")||"")};
  try{const payload=await api("/api/projects",{method:"POST",body:JSON.stringify(request)});bootstrap.recentProjects=payload.recentProjects;bootstrap.currentProject=payload.currentProject;loadedModelProject="";renderProjects();renderCurrentProject();loadModelDesignForCurrentProject(true);form.reset();setStatus(`Created ${payload.currentProject.project.name}. The project is locked for this FlumeWorks session.`,"success");}
  catch(error){setStatus(error.message,"error");}
});
$("conditionForm").addEventListener("submit",async event=>{
  event.preventDefault();const form=event.currentTarget,data=new FormData(form);
  const request={condition_number:String(data.get("condition_number")||""),target_hs_m:numberOrNull(form,"target_hs_m"),target_tp_s:numberOrNull(form,"target_tp_s"),water_level_m_ahd:numberOrNull(form,"water_level_m_ahd"),aep_percent:numberOrNull(form,"aep_percent"),ari_years:numberOrNull(form,"ari_years"),notes:String(data.get("notes")||"")};
  try{const payload=await api("/api/design-conditions",{method:"POST",body:JSON.stringify(request)});bootstrap.currentProject=payload.currentProject;renderCurrentProject();form.reset();setStatus(`Added design condition ${payload.condition.condition_number}. Save the project when ready.`,"success");}
  catch(error){setStatus(error.message,"error");}
});
$("saveProject").addEventListener("click",saveProject);
$("backupProject").addEventListener("click",async()=>{
  try{await persistModelDesign();const payload=await api("/api/projects/backup",{method:"POST"});bootstrap.currentProject=payload.currentProject;renderCurrentProject();setStatus(`Backup created: ${payload.backupPath}`,"success");}
  catch(error){setStatus(error.message,"error");}
});
$("closeProject").addEventListener("click",async()=>{
  try{await persistModelDesign();const payload=await api("/api/projects/close",{method:"POST"});bootstrap.currentProject=null;bootstrap.recentProjects=payload.recentProjects;loadedModelProject="";renderProjects();renderCurrentProject();loadModelDesignForCurrentProject(true);setStatus("Project saved and closed. Its edit lock was released.","success");}
  catch(error){setStatus(error.message,"error");}
});

window.addEventListener("message",event=>{
  if(!bootstrap||event.source!==modelFrame()||event.origin!==new URL(bootstrap.modelDesignUrl).origin||!event.data)return;
  if(event.data.type==="flumeworks:model-design-ready"){modelDesignReady=true;loadedModelProject="";loadModelDesignForCurrentProject(true);}
  if(event.data.type==="flumeworks:model-design-state"){
    const request=captureRequests.get(event.data.requestId);if(request){captureRequests.delete(event.data.requestId);request.resolve(event.data.state);}
  }
  if(event.data.type==="flumeworks:model-design-error")setStatus(`Model Design: ${event.data.message}`,"error");
});

setInterval(async()=>{
  if(modelSyncBusy||!bootstrap||!bootstrap.currentProject||!modelDesignReady)return;
  modelSyncBusy=true;try{await persistModelDesign();}catch(error){setStatus(`Model Design could not be added to the local working copy: ${error.message}`,"error");}finally{modelSyncBusy=false;}
},2500);

refresh().catch(error=>setStatus(`FlumeWorks could not start: ${error.message}`,"error"));
