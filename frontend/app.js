/* AI Workplace Command Centre — real backend data only, no simulated activity. */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtT = (iso) => { try { return new Date(iso).toLocaleTimeString(); } catch { return iso || ""; } };
let TOKEN = localStorage.getItem("wp_token") || "";
let CUR_VIEW = "command", LAST_EV = 0, CACHE = {agents:[], projects:[], tasks:[]};

async function api(path, opts = {}) {
  const h = {"Content-Type": "application/json"};
  if (TOKEN) h["Authorization"] = "Bearer " + TOKEN;
  const r = await fetch(path, {...opts, headers: {...h, ...(opts.headers || {})}});
  if (r.status === 401 || r.status === 403) { showBanner("API auth failed — click “Set API token” and enter WORKPLACE_TOKEN."); throw new Error("auth"); }
  const t = await r.text();
  let j = null; try { j = JSON.parse(t); } catch {}
  if (!r.ok) throw new Error((j && (j.detail || j.error)) || t.slice(0, 300));
  return j;
}
function showBanner(msg) { const b = $("auth-banner"); b.textContent = msg; b.classList.remove("hidden"); }
$("token-btn").onclick = () => { const v = prompt("Bearer token (WORKPLACE_TOKEN). Leave empty for open local mode:", TOKEN); if (v !== null) { TOKEN = v.trim(); localStorage.setItem("wp_token", TOKEN); location.reload(); } };

// ---------- nav ----------
document.querySelectorAll("#nav button").forEach((b) => b.onclick = () => {
  CUR_VIEW = b.dataset.view;
  document.querySelectorAll("#nav button").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  $("view-" + CUR_VIEW).classList.remove("hidden");
  refresh(true);
});

// ---------- command ----------
async function loadStats() {
  const s = await api("/api/stats");
  const cards = [["Agents", s.agents_total], ["Working", s.agents_working],
    ["Projects", s.projects_active], ["Running tasks", s.tasks_running],
    ["Completed", s.tasks_completed], ["Approvals", s.approvals_pending],
    ["Tokens", s.tokens_total], ["Cost (est)", "$" + Number(s.cost_usd_total).toFixed(4)],
    ["Workers", s.workers_alive]];
  $("stats").innerHTML = cards.map(([k, v]) => `<div class="stat"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join("");
  const pill = $("nav-apr");
  pill.textContent = s.approvals_pending; pill.classList.toggle("hidden", !s.approvals_pending);
  $("mode-line").textContent = `mode: supervised · provider default: mock`;
  return s;
}
async function loadLiveAgents() {
  const [agents, tasks] = await Promise.all([api("/api/agents"), api("/api/tasks?limit=500")]);
  CACHE.agents = agents; CACHE.tasks = tasks;
  const live = {};
  tasks.forEach((t) => { if (!["completed","failed","cancelled"].includes(t.status)) {
    (live[t.agent_id || "unassigned"] = live[t.agent_id || "unassigned"] || []).push(t); } });
  const byId = Object.fromEntries(agents.map((a) => [a.id, a]));
  let html = "";
  agents.forEach((a) => {
    const mine = live[a.id] || [];
    const cur = mine.sort((x, y) => String(y.updated_at).localeCompare(String(x.updated_at)))[0];
    const done = tasks.filter((t) => t.agent_id === a.id && t.status === "completed").length;
    html += `<div class="live-card" onclick="openAgent('${a.id}')">
      <div class="hd"><span class="nm">${esc(a.avatar)} ${esc(a.name)}</span>
      <span class="st ${esc(a.status)}">${esc(a.status)}</span></div>
      <div class="small muted">${esc(a.role)} · ${esc(a.specialty)} · ${esc(a.provider)}/${esc(a.model)}</div>
      ${cur ? `<div class="small">▶ <b>${esc(cur.title)}</b> <span class="st ${esc(cur.status)}">${esc(cur.status)}</span></div>
      <div class="small muted">${esc(cur.current_stage || "")} ${esc(cur.current_tool ? "· tool: " + cur.current_tool : "")}</div>
      <div class="small muted">${esc(cur.last_action || "")}</div>
      <div class="small muted">updated ${fmtT(cur.updated_at)} · done ${done} · open ${mine.length}</div>
      <div class="bar"><i style="width:${Math.round((cur.progress || 0) * 100)}%"></i></div>`
      : `<div class="small muted">idle · completed ${done}</div>`}
    </div>`;
  });
  $("live-agents").innerHTML = html || `<div class="muted">No agents yet — create one in the Agents tab.</div>`;
  if (live["unassigned"]?.length) $("live-agents").innerHTML += `<div class="muted small">⚠ ${live["unassigned"].length} unassigned active task(s)</div>`;
}
async function loadEvents() {
  const evs = await api(`/api/task-events?since=${Math.max(0, LAST_EV - 60)}&limit=60`);
  if (evs.length) LAST_EV = Math.max(LAST_EV, ...evs.map((e) => e.id));
  $("events").innerHTML = evs.slice(-25).reverse().map(evHtml).join("") || `<div class="muted">No events yet.</div>`;
}
function evHtml(e) {
  return `<div class="ev"><time>${fmtT(e.created_at)}</time><b>${esc(e.event_type)}</b> ${esc(e.message || "")}
    ${e.task_id ? `<span class="muted">[${esc(String(e.task_id).slice(0, 13))}]</span>` : ""}</div>`;
}
async function loadProjectOptions() {
  const ps = CACHE.projects.length ? CACHE.projects : await api("/api/projects");
  CACHE.projects = ps;
  $("obj-project").innerHTML = ps.map((p) => `<option value="${p.id}">${esc(p.name)} (${esc(p.status)})</option>`).join("");
  const gp = $("graph-project");
  const prev = gp.value;
  gp.innerHTML = ps.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
  if ([...gp.options].some((o) => o.value === prev)) gp.value = prev;
}
async function loadGraph() {
  const pid = $("graph-project").value || $("obj-project").value;
  if (!pid) { $("graph").innerHTML = `<div class="muted">No project selected.</div>`; return; }
  const p = await api(`/api/projects/${pid}`);
  const tasks = p.tasks || [];
  if (!tasks.length) { $("graph").innerHTML = `<div class="muted">No tasks in this project yet.</div>`; return; }
  const byId = Object.fromEntries(tasks.map((t) => [t.id, t]));
  const depth = {};
  const d = (t, seen = new Set()) => {
    if (depth[t.id] !== undefined) return depth[t.id];
    if (seen.has(t.id)) return 0;
    seen.add(t.id);
    const ds = (t.deps || []).map((x) => byId[x]).filter(Boolean);
    depth[t.id] = ds.length ? 1 + Math.max(...ds.map((x) => d(x, new Set(seen)))) : 0;
    return depth[t.id];
  };
  tasks.forEach((t) => d(t));
  const cols = {};
  tasks.forEach((t) => { (cols[depth[t.id]] = cols[depth[t.id]] || []).push(t); });
  const W = 230, H = 66, colW = 250;
  const maxRows = Math.max(...Object.values(cols).map((c) => c.length));
  const svgW = Object.keys(cols).length * colW + 40, svgH = maxRows * H + 40;
  const pos = {};
  Object.entries(cols).forEach(([c, arr]) => arr.forEach((t, i) => pos[t.id] = [20 + c * colW, 20 + i * H]));
  const color = (s) => ({completed:"#34c77b",failed:"#e5534b",blocked:"#e5534b",running:"#4f8cff",verification_running:"#4f8cff",waiting_input:"#f0a744",review_required:"#a371f7",queued:"#9aa4b5",assigned:"#9aa4b5",cancelled:"#555" }[s] || "#9aa4b5");
  let s = `<svg width="${svgW}" height="${svgH}">`;
  tasks.forEach((t) => (t.deps || []).forEach((dep) => {
    if (!pos[dep]) return;
    const [x1, y1] = pos[dep], [x2, y2] = pos[t.id];
    s += `<line x1="${x1 + W}" y1="${y1 + 22}" x2="${x2}" y2="${y2 + 22}" stroke="#4f8cff" stroke-width="2"/>`;
  }));
  tasks.forEach((t) => {
    const [x, y] = pos[t.id];
    s += `<g onclick="openTask('${t.id}')" style="cursor:pointer">
      <rect x="${x}" y="${y}" width="${W}" height="44" rx="8" fill="#161b24" stroke="${color(t.status)}" stroke-width="2"/>
      <text x="${x + 8}" y="${y + 18}" fill="#e6e9ef" font-size="11">${esc(t.title.slice(0, 30))}</text>
      <text x="${x + 8}" y="${y + 34}" fill="${color(t.status)}" font-size="10">${esc(t.status)}${t.agent_id ? " · " + esc((CACHE.agents.find((a) => a.id === t.agent_id)?.name) || "?") : ""}</text></g>`;
  });
  $("graph").innerHTML = s + "</svg>";
}
$("graph-project").onchange = loadGraph;
$("obj-run").onclick = async () => {
  const pid = $("obj-project").value, obj = $("obj-text").value.trim();
  if (!pid || !obj) { $("obj-result").textContent = "Select a project and describe an objective."; return; }
  $("obj-result").textContent = "Planning…";
  try {
    const plan = await api("/api/orchestrate", {method: "POST", body: JSON.stringify({project_id: pid, objective: obj})});
    $("obj-result").textContent = `Planned ${plan.tasks.length} task(s), team of ${plan.team.length} agent(s): ${plan.tasks.map((t) => t.title).join(" → ")}`;
    $("obj-text").value = "";
    refresh(true);
  } catch (e) { $("obj-result").textContent = "Error: " + e.message; }
};

// ---------- agents ----------
async function loadAgentFactory() {
  const [tpls, provs, agents] = await Promise.all([api("/api/agent-templates"), api("/api/providers"), api("/api/agents")]);
  CACHE.agents = agents;
  $("agent-template").innerHTML = `<option value="">— custom —</option>` + tpls.map((t) => `<option value="${t.key}">${esc(t.name)} — ${esc(t.specialty)}</option>`).join("");
  $("agent-provider").innerHTML = provs.map((p) => `<option value="${p.provider}">${esc(p.provider)}${p.key_configured ? "" : " (no key)"}</option>`).join("");
  $("agent-list").innerHTML = agents.map((a) => `<div class="agent-row" onclick="openAgent('${a.id}')">
    <span class="av">${esc(a.avatar || "🤖")}</span>
    <span><b>${esc(a.name)}</b><br><span class="muted small">${esc(a.role)} · ${esc(a.team)} · ${esc(a.provider)}/${esc(a.model)}</span></span>
    <span class="st ${esc(a.status)}" style="margin-left:auto">${esc(a.status)}</span></div>`).join("")
    || `<div class="muted">No agents. Use a template or “starter team”.</div>`;
}
$("agent-template").onchange = async () => {
  const k = $("agent-template").value;
  if (!k) return;
  const t = (await api("/api/agent-templates")).find((x) => x.key === k);
  if (!t) return;
  $("agent-name").value = t.name; $("agent-role").value = t.role + " " + t.specialty;
  $("agent-team").value = t.team; $("agent-instructions").value = t.instructions;
  $("agent-tools").value = t.tools.join(", ");
};
$("agent-create").onclick = async () => {
  const tools = $("agent-tools").value.split(",").map((s) => s.trim()).filter(Boolean);
  const budgets = {};
  if ($("agent-budget").value) budgets.max_cost_usd = parseFloat($("agent-budget").value);
  const body = {name: $("agent-name").value || "New agent", role: $("agent-role").value,
    team: $("agent-team").value || "general", system_instructions: $("agent-instructions").value,
    provider: $("agent-provider").value, model: $("agent-model").value || "mock-small",
    allowed_tools: tools, max_concurrent: parseInt($("agent-conc").value) || 2, budgets,
    template: $("agent-template").value || null};
  if (body.template && !$("agent-name").value) body.name = "x";
  try {
    const a = await api("/api/agents", {method: "POST", body: JSON.stringify(body)});
    $("agent-msg").textContent = `Created ${a.name} (${a.id})`;
    loadAgentFactory();
  } catch (e) { $("agent-msg").textContent = "Error: " + e.message; }
};
$("starter-team").onclick = async () => {
  const r = await api("/api/setup/starter-team", {method: "POST"});
  $("agent-msg").textContent = `Starter team ready (${r.agents_created} new agents).`;
  loadAgentFactory(); loadProjectOptions();
};
window.openAgent = async (aid) => {
  const a = await api(`/api/agents/${aid}`);
  $("modal-body").innerHTML = `
    <h2>${esc(a.avatar)} ${esc(a.name)} <span class="st ${esc(a.status)}">${esc(a.status)}</span></h2>
    <div class="kv">
      <dt>Role</dt><dd>${esc(a.role)} — ${esc(a.specialty)} (${esc(a.team)})</dd>
      <dt>Model</dt><dd>${esc(a.provider)} / ${esc(a.model)}</dd>
      <dt>Tools</dt><dd>${esc((a.allowed_tools || []).join(", ") || "all non-consequential")}</dd>
      <dt>Budgets</dt><dd>${esc(JSON.stringify(a.budgets))}</dd>
      <dt>Usage</dt><dd>${a.usage.tokens} tokens · $${Number(a.usage.cost_usd).toFixed(4)} · ${a.usage.tasks} tasks</dd>
      <dt>Instructions</dt><dd>${esc(a.system_instructions || "—")}</dd>
    </div>
    <div class="tabs">
      <button onclick="agentCtl('${aid}','resume')">▶ Resume</button>
      <button onclick="agentCtl('${aid}','pause')">⏸ Pause</button>
      <button onclick="agentCtl('${aid}','stop')" class="danger">⏹ Stop (+cancel tasks)</button>
    </div>
    <h2>Live tasks</h2>
    ${(a.live || []).map((t) => `<div class="ev" onclick="openTask('${t.id}')" style="cursor:pointer">
      <b>${esc(t.title)}</b> <span class="st ${esc(t.status)}">${esc(t.status)}</span><br>
      <span class="muted">${esc(t.current_stage || "")} ${esc(t.last_action || "")} · updated ${fmtT(t.updated_at)}</span>
      <div class="bar"><i style="width:${Math.round((t.progress || 0) * 100)}%"></i></div></div>`).join("") || `<div class="muted">No live tasks.</div>`}
    <h2>Recent history</h2>
    ${(a.recent_tasks || []).slice(0, 8).map((t) => `<div class="ev" onclick="openTask('${t.id}')" style="cursor:pointer">
      <b>${esc(t.title)}</b> <span class="st ${esc(t.status)}">${esc(t.status)}</span>
      <span class="muted">${fmtT(t.updated_at)}</span></div>`).join("")}
    <p><button onclick="closeModal()">Close</button></p>`;
  $("modal").classList.remove("hidden");
};
window.agentCtl = async (aid, op) => {
  await api(`/api/agents/${aid}/${op}`, {method: "POST"});
  openAgent(aid); refresh(true);
};

// ---------- tasks ----------
window.openTask = async (tid) => {
  const t = await api(`/api/tasks/${tid}`);
  const an = CACHE.agents.find((a) => a.id === t.agent_id);
  const vr = t.verification_result || {};
  $("modal-body").innerHTML = `
    <h2>${esc(t.title)} <span class="st ${esc(t.status)}">${esc(t.status)}</span></h2>
    <div class="kv">
      <dt>Agent</dt><dd>${esc(an ? an.name : t.agent_id || "unassigned")}</dd>
      <dt>Stage</dt><dd>${esc(t.current_stage || "—")} ${esc(t.current_tool ? "(tool: " + t.current_tool + ")" : "")}</dd>
      <dt>Last action</dt><dd>${esc(t.last_action || "—")}</dd>
      <dt>Progress</dt><dd>${Math.round((t.progress || 0) * 100)}% · attempt ${t.attempt}/${t.max_retries}</dd>
      <dt>Deps</dt><dd>${esc((t.deps || []).join(", ") || "none")}</dd>
      <dt>Cost</dt><dd>${t.tokens_used} tokens · $${Number(t.cost_usd || 0).toFixed(4)} · ${t.runtime_ms}ms</dd>
      <dt>Error</dt><dd>${esc(t.error || "—")}</dd>
    </div>
    <h2>Objective</h2><pre>${esc(t.objective || "")}</pre>
    <h2>Output</h2><pre>${esc(t.output || "(no output yet)")}</pre>
    ${t.acceptance_tests?.length ? `<h2>Acceptance (${vr.passed ?? 0}/${(vr.results || []).length} passed)</h2>
      <pre>${esc(JSON.stringify(vr.results || [], null, 1))}</pre>` : ""}
    <h2>Events</h2>${(t.events || []).map(evHtml).join("") || `<div class="muted">none</div>`}
    <div class="tabs">
      <button onclick="taskCtl('${tid}','cancel')">Cancel</button>
      <button onclick="taskCtl('${tid}','retry')">Retry</button>
      ${t.status === "review_required" ? `<button onclick="reviewCtl('${tid}','approve')">✓ Approve</button>
        <button onclick="reviewCtl('${tid}','changes')">✎ Request changes</button>
        <button onclick="reviewCtl('${tid}','reject')" class="danger">✗ Reject</button>` : ""}
      <button onclick="closeModal()">Close</button>
    </div>`;
  $("modal").classList.remove("hidden");
};
window.taskCtl = async (tid, op) => { await api(`/api/tasks/${tid}/${op}`, {method: "POST"}); openTask(tid); refresh(true); };
window.reviewCtl = async (tid, d) => {
  const note = prompt("Review note:", "") || "";
  await api(`/api/tasks/${tid}/review`, {method: "POST", body: JSON.stringify({decision: d, note})});
  openTask(tid); refresh(true);
};
window.closeModal = () => $("modal").classList.add("hidden");
$("modal").onclick = (e) => { if (e.target.id === "modal") closeModal(); };

// ---------- projects ----------
$("proj-create").onclick = async () => {
  const name = $("proj-name").value.trim();
  if (!name) return;
  await api("/api/projects", {method: "POST", body: JSON.stringify({name, type: $("proj-type").value, objectives: $("proj-obj").value})});
  $("proj-name").value = ""; $("proj-obj").value = "";
  loadProjects();
};
async function loadProjects() {
  const ps = await api("/api/projects");
  CACHE.projects = ps;
  $("project-list").innerHTML = ps.map((p) => {
    const tc = p.task_counts || {};
    const open = ["queued","assigned","running","waiting_input","blocked","review_required","verification_running"].reduce((n, k) => n + (tc[k] || 0), 0);
    return `<div class="proj-card" onclick="openProject('${p.id}')">
      <b>${esc(p.name)}</b> <span class="st">${esc(p.type)}</span> <span class="st ${esc(p.status)}">${esc(p.status)}</span>
      <div class="small muted">${esc((p.objectives || "").slice(0, 80))}</div>
      <div class="small">open ${open} · done ${tc.completed || 0} · failed ${tc.failed || 0}</div></div>`;
  }).join("") || `<div class="muted">No projects yet.</div>`;
}
window.openProject = async (pid) => {
  const p = await api(`/api/projects/${pid}`);
  const cols = ["queued", "assigned", "running", "waiting_input/blocked", "review_required", "completed", "failed"];
  const group = (t) => (t.status === "waiting_input" || t.status === "blocked") ? "waiting_input/blocked"
    : (t.status === "verification_running" ? "running" : (["queued","assigned","running","review_required","completed","failed"].includes(t.status) ? t.status : "queued"));
  const an = Object.fromEntries(CACHE.agents.map((a) => [a.id, a.name]));
  $("project-detail").classList.remove("hidden");
  $("project-detail").innerHTML = `
    <h2>${esc(p.name)} <span class="st">${esc(p.type)}</span> <span class="st ${esc(p.status)}">${esc(p.status)}</span></h2>
    <div class="small muted">${esc(p.objectives || "")}</div>
    <div class="tabs">
      <button onclick="projCtl('${pid}','resume')">▶ Resume</button>
      <button onclick="projCtl('${pid}','pause')">⏸ Pause</button>
      <button onclick="projCtl('${pid}','archive')">📦 Archive</button>
      <button onclick="exportProject('${pid}')">⬇ Export JSON</button>
      <button onclick="newTask('${pid}')">＋ New task</button>
      <button onclick="closeProject()">Close</button>
    </div>
    <div class="board">${cols.map((c) => `<div class="bcol"><h3>${c}</h3>
      ${p.tasks.filter((t) => group(t) === c).map((t) => `<div class="bcard" onclick="openTask('${t.id}')">
        <b>${esc(t.title)}</b><br><span class="muted">${esc(an[t.agent_id] || "—")} · ${Math.round((t.progress || 0) * 100)}%</span></div>`).join("")}
    </div>`).join("")}</div>
    <h2>Artifacts (${(p.artifacts || []).length})</h2>
    ${(p.artifacts || []).map((a) => `<div class="ev"><b>${esc(a.name)}</b> <span class="muted">${esc(a.kind)} · ${a.size_bytes}B · ${fmtT(a.created_at)}</span></div>`).join("") || `<div class="muted">none</div>`}`;
  $("project-detail").scrollIntoView();
};
window.closeProject = () => $("project-detail").classList.add("hidden");
window.projCtl = async (pid, op) => { await api(`/api/projects/${pid}/${op}`, {method: "POST"}); openProject(pid); loadProjects(); };
window.exportProject = async (pid) => {
  const p = await api(`/api/projects/${pid}/export`);
  const blob = new Blob([JSON.stringify(p, null, 1)], {type: "application/json"});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = `project-${pid}.json`; a.click();
};
window.newTask = async (pid) => {
  const title = prompt("Task title:"); if (!title) return;
  const objective = prompt("Objective/instructions:", "") || "";
  await api("/api/tasks", {method: "POST", body: JSON.stringify({project_id: pid, title, objective})});
  openProject(pid);
};

// ---------- approvals ----------
async function loadApprovals() {
  const list = await api("/api/approvals?status=pending");
  $("approval-list").innerHTML = list.map((a) => `<div class="apr">
    <b>${esc(a.action)}</b> <span class="st waiting_input">${esc(a.risk)}</span>
    <div class="small muted">task ${esc(a.task_id || "—")} · requested ${fmtT(a.requested_at)}</div>
    <pre>${esc(JSON.stringify(a.detail, null, 1))}</pre>
    <button onclick="decideApr('${a.id}','approve')">✓ Approve</button>
    <button onclick="decideApr('${a.id}','reject')" class="danger">✗ Reject</button></div>`).join("")
    || `<div class="muted">No pending approvals. 🎉</div>`;
}
window.decideApr = async (aid, d) => {
  await api(`/api/approvals/${aid}/decide`, {method: "POST", body: JSON.stringify({decision: d})});
  loadApprovals(); refresh(true);
};

// ---------- memory ----------
async function loadMemory() {
  const scope = $("mem-scope").value, q = $("mem-q").value;
  const list = await api(`/api/memory?${scope ? "scope=" + scope + "&" : ""}query=${encodeURIComponent(q)}&limit=100`);
  const an = Object.fromEntries(CACHE.agents.map((a) => [a.id, a.name]));
  $("memory-list").innerHTML = list.map((m) => `<div class="mem">
    <b>[${esc(m.scope)}${m.scope_id ? ":" + esc(an[m.scope_id] || m.scope_id.slice(0, 12)) : ""}]</b>
    <b>${esc(m.key)}</b> <span class="st">${esc(m.type)}</span>
    ${m.verified ? `<span class="st completed">verified</span>` : `<span class="st waiting_input">unverified</span>`}
    <div class="small">${esc(m.value)}</div>
    <div class="small muted">origin: ${esc(m.origin || "—")} · ${fmtT(m.updated_at)}
    <button class="small danger" onclick="delMem('${m.id}')">delete</button></div></div>`).join("")
    || `<div class="muted">No memories match.</div>`;
}
$("mem-search").onclick = loadMemory; $("mem-q").onkeydown = (e) => { if (e.key === "Enter") loadMemory(); };
$("mem-add").onclick = async () => {
  await api("/api/memory", {method: "POST", body: JSON.stringify({scope: $("mem-scope").value || "global",
    key: $("mem-key").value, value: $("mem-val").value, type: $("mem-type").value,
    verified: $("mem-verified").checked, origin: "user"})});
  $("mem-key").value = ""; $("mem-val").value = ""; loadMemory();
};
window.delMem = async (mid) => { await api(`/api/memory/${mid}`, {method: "DELETE"}); loadMemory(); };

// ---------- providers ----------
async function loadProviders() {
  const list = await api("/api/providers");
  $("provider-list").innerHTML = list.map((p) => `<div class="prov">
    <b>${esc(p.provider)}</b> ${p.is_default ? `<span class="st completed">default</span>` : ""}
    <span class="st">${esc(p.key_configured ? "key configured" : "no key")}</span>
    <div class="small muted">model: ${esc(p.model)} · caps: ${esc(Object.entries(p.capabilities || {}).filter(([k, v]) => v).map(([k]) => k).join(", "))}</div>
    <div class="small muted">${esc(p.note || "")}</div>
    <div class="tabs"><button onclick="testProv('${p.provider}')">Test connection</button>
    <button onclick="setDefaultProv('${p.id}')">Set default</button></div>
    <div class="small" id="prov-${p.provider}"></div></div>`).join("");
}
window.testProv = async (name) => {
  $(`prov-${name}`).textContent = "testing…";
  const r = await api("/api/providers/test", {method: "POST", body: JSON.stringify({provider: name})});
  $(`prov-${name}`).textContent = r.ok ? `✓ OK — reply: ${r.reply_preview}` : `✗ ${r.kind}: ${r.error}`;
};
window.setDefaultProv = async (id) => { await api(`/api/providers/${id}`, {method: "PATCH", body: JSON.stringify({is_default: true})}); loadProviders(); };

// ---------- logs ----------
async function loadLogs() {
  const [evs, audit] = await Promise.all([api("/api/task-events?since=0&limit=150"), api("/api/audit?limit=150")]);
  $("log-events").innerHTML = evs.slice(-120).reverse().map(evHtml).join("");
  $("log-audit").innerHTML = audit.map((a) => `<div class="ev"><time>${fmtT(a.created_at)}</time>
    <b>${esc(a.actor)}</b> ${esc(a.action)} <span class="muted">${esc(a.resource_id || "")}</span><br>
    <span class="muted">${esc(JSON.stringify(a.detail).slice(0, 220))}</span></div>`).join("");
}

// ---------- refresh + realtime ----------
async function refresh(full = false) {
  try {
    await loadStats();
    if (CUR_VIEW === "command") {
      await Promise.all([loadLiveAgents(), loadEvents(), loadProjectOptions()]);
      await loadGraph();
    }
    else if (CUR_VIEW === "agents") await loadAgentFactory();
    else if (CUR_VIEW === "projects") await loadProjects();
    else if (CUR_VIEW === "approvals") await loadApprovals();
    else if (CUR_VIEW === "memory" && full) await loadMemory();
    else if (CUR_VIEW === "providers" && full) await loadProviders();
    else if (CUR_VIEW === "logs") await loadLogs();
    $("conn").textContent = "● live"; $("conn").classList.remove("off");
  } catch (e) {
    if (String(e.message) !== "auth") { $("conn").textContent = "● offline"; $("conn").classList.add("off"); }
  }
}
function startSSE() {
  try {
    const es = new EventSource("/api/events?since=" + LAST_EV);
    es.onmessage = () => { if (CUR_VIEW === "command") { loadEvents(); loadLiveAgents(); } };
    es.onerror = () => { es.close(); setTimeout(startSSE, 5000); };
  } catch { setTimeout(startSSE, 5000); }
}
(async () => {
  try {
    const h = await (await fetch("/health")).json();
    if (!h.auth_required) showBanner("Open local mode: no WORKPLACE_TOKEN set. Anyone with network access can control agents — set a token for shared deployments.");
    else if (!TOKEN) showBanner("Server requires auth — click “Set API token”.");
  } catch {}
  await refresh(true);
  startSSE();
  setInterval(() => refresh(false), 4000);
})();
