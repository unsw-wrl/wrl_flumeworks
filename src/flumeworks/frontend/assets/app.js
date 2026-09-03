"use strict";

const $=id=>document.getElementById(id);
let bootstrap=null;

function setStatus(message,kind=""){
  $("status").textContent=message;
  $("status").className="status"+(kind?` ${kind}`:"");
}

async function api(path,options={}){
  const response=await fetch(path,{headers:{"Content-Type":"application/json",...(options.headers||{})},...options});
  let payload={};try{payload=await response.json();}catch{}
  if(!response.ok)throw new Error(payload.detail||`Request failed with HTTP ${response.status}`);
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

function renderProjects(){
  const root=$("projectList");root.replaceChildren();
  if(!bootstrap.projects.length){const empty=document.createElement("p");empty.className="empty";empty.textContent="No FlumeWorks projects found yet.";root.appendChild(empty);return;}
  for(const project of bootstrap.projects){
    const row=document.createElement("div");row.className="project-row";
    const info=document.createElement("div"),name=document.createElement("strong"),meta=document.createElement("small"),button=document.createElement("button");
    name.textContent=project.name;meta.textContent=[project.project_number,project.facility_name].filter(Boolean).join(" · ");
    button.type="button";button.className="secondary";button.textContent="Open";button.addEventListener("click",()=>openProject(project.uuid));
    info.append(name,meta);row.append(info,button);root.appendChild(row);
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

function renderBootstrap(){
  $("appVersion").textContent=`Version ${bootstrap.application.version}`;$("gitCommit").textContent=`Commit ${bootstrap.application.gitCommit}`;$("projectRoot").textContent=bootstrap.projectRoot;
  const facilities=$("facilitySelect");facilities.replaceChildren();
  for(const facility of bootstrap.facilities){const option=document.createElement("option");option.value=facility.id;option.textContent=facility.name;facilities.appendChild(option);}
  if(bootstrap.modelDesignUrl&&$("modelDesignFrame").src!==bootstrap.modelDesignUrl)$("modelDesignFrame").src=bootstrap.modelDesignUrl;
  renderProjects();renderCurrentProject();
}

async function refresh(message=""){
  bootstrap=await api("/api/bootstrap");renderBootstrap();if(message)setStatus(message,"success");else setStatus("FlumeWorks is ready.","success");
}

async function openProject(projectUuid){
  try{const payload=await api("/api/projects/open",{method:"POST",body:JSON.stringify({project_uuid:projectUuid})});bootstrap.currentProject=payload.currentProject;renderCurrentProject();setStatus(`Opened ${payload.currentProject.project.name}.`,"success");}
  catch(error){setStatus(error.message,"error");}
}

document.querySelectorAll(".nav-item").forEach(button=>button.addEventListener("click",()=>showView(button.dataset.view)));
$("refreshProjects").addEventListener("click",()=>refresh("Project list refreshed.").catch(error=>setStatus(error.message,"error")));
$("createProjectForm").addEventListener("submit",async event=>{
  event.preventDefault();const form=event.currentTarget,data=new FormData(form);
  const request={name:String(data.get("name")||""),project_number:String(data.get("project_number")||""),facility:String(data.get("facility")||""),description:String(data.get("description")||"")};
  try{const payload=await api("/api/projects",{method:"POST",body:JSON.stringify(request)});bootstrap.projects=payload.projects;bootstrap.currentProject=payload.currentProject;renderProjects();renderCurrentProject();form.reset();setStatus(`Created ${payload.currentProject.project.name} without modifying any existing project files.`,"success");}
  catch(error){setStatus(error.message,"error");}
});
$("conditionForm").addEventListener("submit",async event=>{
  event.preventDefault();const form=event.currentTarget,data=new FormData(form);
  const request={condition_number:String(data.get("condition_number")||""),target_hs_m:numberOrNull(form,"target_hs_m"),target_tp_s:numberOrNull(form,"target_tp_s"),water_level_m_ahd:numberOrNull(form,"water_level_m_ahd"),aep_percent:numberOrNull(form,"aep_percent"),ari_years:numberOrNull(form,"ari_years"),notes:String(data.get("notes")||"")};
  try{const payload=await api("/api/design-conditions",{method:"POST",body:JSON.stringify(request)});bootstrap.currentProject=payload.currentProject;renderCurrentProject();form.reset();setStatus(`Added design condition ${payload.condition.condition_number}.`,"success");}
  catch(error){setStatus(error.message,"error");}
});

refresh().catch(error=>setStatus(`FlumeWorks could not start: ${error.message}`,"error"));

