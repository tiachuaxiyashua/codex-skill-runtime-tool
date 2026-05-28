const state = {
  sessions: [],
  skills: [],
  skillListings: [],
  capabilities: [],
  jobs: [],
  plugins: [],
  selectedSession: null,
  detail: null,
  selectedNodeId: null,
  lastEventCount: 0,
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function statusClass(value) {
  return String(value || "unknown").replace(/[^a-zA-Z0-9_-]/g, "_");
}

function fileUrl(path) {
  return `/api/file?path=${encodeURIComponent(path)}`;
}

async function loadHealth() {
  const data = await api("/api/health");
  const running = (data.jobs || []).filter((item) => ["starting", "running", "cancel_requested"].includes(item.status)).length;
  if (!$("target-workspace").value && data.target_workspace) $("target-workspace").placeholder = data.target_workspace;
  const chips = [
    ["target", data.target_workspace],
    ["env", data.runtime_env],
    ["api", data.codex_base_url || "未配置"],
    ["cap", String(data.capabilities || 0)],
    ["running", String(running)],
  ];
  $("health-strip").innerHTML = chips
    .map(([name, value]) => `<span class="health-chip">${name}: ${escapeHtml(shortPath(value))}</span>`)
    .join("");
  state.jobs = data.jobs || [];
  renderJobs();
}

async function loadSkills() {
  const data = await api("/api/skills");
  if (!data.ok) {
    $("skill-count").textContent = "加载失败";
    $("skill-list").innerHTML = `<div class="empty">${escapeHtml(data.stderr || data.stdout || "无法读取 skill")}</div>`;
    return;
  }
  state.skills = Array.isArray(data.skills) ? data.skills : [];
  state.skillListings = Array.isArray(data.skill_listings) ? data.skill_listings : [];
  state.plugins = Array.isArray(data.plugins) ? data.plugins : [];
  renderSkills();
  renderPlugins();
}

async function loadCapabilities() {
  const data = await api("/api/capabilities");
  state.capabilities = data.capabilities || [];
  renderCapabilities();
}

async function loadJobs() {
  const data = await api("/api/jobs");
  state.jobs = data.jobs || [];
  renderJobs();
}

function renderSkills() {
  const filter = $("skill-filter").value.trim().toLowerCase();
  const visible = state.skills.filter((skill) => !filter || skill.toLowerCase().includes(filter)).slice(0, 160);
  $("skill-count").textContent = `${state.skills.length} 个`;
  $("skill-select").innerHTML =
    `<option value="">手动输入 slash command</option>` +
    visible.map((skill) => `<option value="/${escapeAttr(skill)}">/${escapeHtml(skill)}</option>`).join("");
  $("skill-list").innerHTML =
    visible
      .map((skill) => {
        const listing = state.skillListings.find((item) => item.name === skill) || {};
        const description = listing.description ? `<span>${escapeHtml(listing.description)}</span>` : "";
        return `<button class="skill-chip" data-command="/${escapeAttr(skill)}"><strong>/${escapeHtml(skill)}</strong>${description}</button>`;
      })
      .join("") || `<div class="empty">没有匹配的 skill</div>`;
  document.querySelectorAll(".skill-chip").forEach((item) => {
    item.addEventListener("click", () => {
      $("run-command").value = item.dataset.command || "";
      $("run-arguments").focus();
    });
  });
}

async function loadSessions() {
  const data = await api("/api/sessions");
  state.sessions = data.sessions || [];
  renderSessions();
  if (!state.selectedSession && state.sessions.length) {
    await selectSession(state.sessions[0].id);
  }
}

function renderSessions() {
  $("session-list").innerHTML =
    state.sessions
      .map((session) => {
        const active = state.selectedSession === session.id ? "active" : "";
        const agents = Array.isArray(session.current_agents) ? session.current_agents.length : 0;
        const note = session.summary && session.summary.notes ? session.summary.notes : "";
        return `
          <div class="session-item ${active}" data-session="${escapeAttr(session.id)}">
            <strong>${escapeHtml(session.label || session.id)}</strong>
            <div class="session-meta">
              <span class="pill ${statusClass(session.status)}">${escapeHtml(session.status)}</span>
              <span>${escapeHtml(session.updated_at || "")}</span>
            </div>
            <div class="session-meta">
              <span>${escapeHtml(session.current_skill || "无活动 skill")}</span>
              <span>agents ${agents}</span>
            </div>
            ${note ? `<div class="session-note">${escapeHtml(note)}</div>` : ""}
          </div>`;
      })
      .join("") || `<div class="empty">暂无历史任务</div>`;
  document.querySelectorAll(".session-item").forEach((item) => {
    item.addEventListener("click", () => selectSession(item.dataset.session));
  });
}

async function selectSession(sessionId) {
  state.selectedSession = sessionId;
  state.selectedNodeId = null;
  renderSessions();
  await loadDetail();
}

async function loadDetail() {
  if (!state.selectedSession) return;
  const data = await api(`/api/sessions/${encodeURIComponent(state.selectedSession)}`);
  state.detail = data;
  renderDetail();
}

function renderDetail() {
  const detail = state.detail || {};
  const sessionState = detail.state || {};
  $("session-title").textContent = detail.id || "未选择任务";
  $("current-state").textContent = sessionState.status || "unknown";
  $("current-skill").textContent = sessionState.current_skill || "-";
  const agents = Array.isArray(sessionState.current_agents) ? sessionState.current_agents : [];
  $("current-agent").textContent = agents.length ? agents.map((agent) => agent.name).join(", ") : "-";
  $("parallel-count").textContent = String(agents.length);
  renderQuestion(detail);
  renderTree(detail.tree || {});
  renderLanes(detail.tree || {}, agents);
  renderTimeline(detail.events || []);
  renderArtifacts(detail.artifacts || {});
  renderFiles(detail.files || []);
  renderSelectedNode();
}

function renderQuestion(detail) {
  const pending = detail.pending_answer && detail.pending_answer.status === "answered" ? null : detail.pending_question;
  const banner = $("question-banner");
  if (!pending || !pending.question) {
    banner.classList.add("hidden");
    return;
  }
  banner.classList.remove("hidden");
  $("question-text").textContent = pending.question || "";
  const options = Array.isArray(pending.options) ? pending.options : [];
  $("question-options").innerHTML = options
    .map((option) => `<button class="option-button" data-answer="${escapeAttr(String(option))}">${escapeHtml(String(option))}</button>`)
    .join("");
  document.querySelectorAll(".option-button").forEach((button) => {
    button.addEventListener("click", () => {
      $("answer-input").value = button.dataset.answer || "";
    });
  });
}

function renderTree(tree) {
  const nodes = Array.isArray(tree.nodes) ? tree.nodes : [];
  const byParent = new Map();
  nodes.forEach((node) => {
    const parent = node.parent_id || "";
    if (!byParent.has(parent)) byParent.set(parent, []);
    byParent.get(parent).push(node);
  });
  const rootId = tree.root_node_id || (nodes[0] && nodes[0].id);
  const roots = rootId ? nodes.filter((node) => node.id === rootId) : byParent.get("") || [];
  const rows = [];
  const walk = (node, depth) => {
    rows.push(treeNodeHtml(node, depth));
    (byParent.get(node.id) || []).forEach((child) => walk(child, depth + 1));
  };
  roots.forEach((node) => walk(node, 0));
  $("task-tree").innerHTML = rows.join("") || `<div class="empty">暂无任务树</div>`;
  document.querySelectorAll(".tree-node").forEach((item) => {
    item.addEventListener("click", () => {
      state.selectedNodeId = item.dataset.nodeId;
      renderTree(state.detail.tree || {});
      renderSelectedNode();
    });
  });
}

function treeNodeHtml(node, depth) {
  const selected = state.selectedNodeId === node.id ? "selected" : "";
  const display = node.display_name || node.name || node.id;
  const type = node.type || "node";
  return `
    <div class="tree-node ${selected}" data-node-id="${escapeAttr(node.id)}" style="--depth:${depth * 14}px">
      <div class="tree-line">
        <span class="pill ${statusClass(node.status)}">${escapeHtml(node.status || "unknown")}</span>
        <span class="node-name">${escapeHtml(type)} / ${escapeHtml(display)}</span>
      </div>
    </div>`;
}

function renderLanes(tree, activeAgents) {
  const nodes = Array.isArray(tree.nodes) ? tree.nodes : [];
  const agents = nodes.filter((node) => node.type === "agent" || node.type === "parallel_group");
  const rows = agents.slice(-24).map((agent) => laneHtml(agent));
  if (!rows.length && activeAgents.length) {
    activeAgents.forEach((agent) => rows.push(laneHtml({ display_name: agent.name, status: agent.status, metadata: { purpose: agent.current_action } })));
  }
  $("agent-lanes").innerHTML = rows.join("") || `<div class="empty">暂无 agent 记录</div>`;
}

function laneHtml(agent) {
  const status = agent.status || "unknown";
  const action = (agent.metadata && (agent.metadata.purpose || agent.metadata.current_action)) || "";
  return `
    <div class="lane">
      <div class="lane-head">
        <strong>${escapeHtml(agent.display_name || agent.name || "agent")}</strong>
        <span class="pill ${statusClass(status)}">${escapeHtml(status)}</span>
      </div>
      <div class="tiny-muted">${escapeHtml(action)}</div>
      <div class="lane-track"><div class="lane-progress ${statusClass(status)}"></div></div>
    </div>`;
}

function renderTimeline(events) {
  const newClass = events.length !== state.lastEventCount ? "new" : "";
  state.lastEventCount = events.length;
  $("timeline").innerHTML =
    events
      .slice()
      .reverse()
      .map((event) => {
        const data = event.data || {};
        const suffix = data.returncode !== undefined ? ` returncode=${data.returncode}` : "";
        return `
          <div class="timeline-item ${newClass}">
            <div><strong>${escapeHtml(event.message || "")}</strong></div>
            <div class="event-type">${escapeHtml(event.timestamp || "")} / ${escapeHtml(event.type || "")}${escapeHtml(suffix)}</div>
          </div>`;
      })
      .join("") || `<div class="empty">暂无事件</div>`;
}

function renderArtifacts(data) {
  const artifacts = Array.isArray(data.artifacts) ? data.artifacts : [];
  $("artifacts").innerHTML = artifacts.length
    ? `<div class="artifact-grid">${artifacts.map(artifactHtml).join("")}</div>`
    : `<div class="empty">暂无产物</div>`;
}

function artifactHtml(item) {
  const path = item.path || "";
  const type = item.type || "file";
  let preview = "";
  if (type === "image") {
    preview = `<img src="${fileUrl(path)}" alt="">`;
  } else if (type === "audio") {
    preview = `<audio controls src="${fileUrl(path)}"></audio>`;
  }
  return `
    <div class="artifact-card">
      ${preview}
      <strong>${escapeHtml(type)}</strong>
      <div>${escapeHtml(shortPath(path))}</div>
      <div class="tiny-muted">${escapeHtml(item.created_by_agent || "")}</div>
    </div>`;
}

function renderFiles(files) {
  $("files").innerHTML =
    files
      .map((file) => `<div class="file-row" data-path="${escapeAttr(file.path)}">${escapeHtml(file.relative)} / ${file.bytes} bytes</div>`)
      .join("") || `<div class="empty">暂无证据文件</div>`;
  document.querySelectorAll(".file-row").forEach((row) => {
    row.addEventListener("click", () => previewFile(row.dataset.path));
  });
}

async function previewFile(path) {
  const response = await fetch(fileUrl(path));
  const text = await response.text();
  $("node-detail").innerHTML = `<h3>${escapeHtml(shortPath(path))}</h3><pre>${escapeHtml(text.slice(0, 50000))}</pre>`;
}

function renderSelectedNode() {
  if (!state.selectedNodeId || !state.detail) return;
  const nodes = (state.detail.tree && state.detail.tree.nodes) || [];
  const node = nodes.find((item) => item.id === state.selectedNodeId);
  if (!node) return;
  $("node-detail").innerHTML = `
    <h3>${escapeHtml(node.display_name || node.name || node.id)}</h3>
    <p><span class="pill ${statusClass(node.status)}">${escapeHtml(node.status || "unknown")}</span></p>
    <pre>${escapeHtml(JSON.stringify(node, null, 2))}</pre>
    ${evidenceLinks(node.evidence || {})}`;
}

function evidenceLinks(evidence) {
  const links = Object.entries(evidence)
    .filter(([, value]) => typeof value === "string" && value)
    .map(([key, value]) => `<button class="ghost evidence-link" data-path="${escapeAttr(value)}">${escapeHtml(key)}</button>`)
    .join(" ");
  setTimeout(() => {
    document.querySelectorAll(".evidence-link").forEach((button) => button.addEventListener("click", () => previewFile(button.dataset.path)));
  }, 0);
  return links ? `<div>${links}</div>` : "";
}

function renderCapabilities() {
  $("capabilities").innerHTML =
    state.capabilities
      .map((item) => `
        <div class="mini-row">
          <strong>${escapeHtml(item.name)}</strong>
          <span>${escapeHtml(item.status || "")}</span>
          <div class="tiny-muted">${escapeHtml(shortPath(item.endpoint || item.source || ""))}</div>
        </div>`)
      .join("") || `<div class="empty">暂无 capability</div>`;
}

function renderJobs() {
  $("jobs").innerHTML =
    state.jobs
      .slice(0, 20)
      .map((job) => {
        const canCancel = ["starting", "running", "cancel_requested"].includes(job.status);
        return `
          <div class="mini-row">
            <strong>${escapeHtml(job.operation || job.id)}</strong>
            <span class="pill ${statusClass(job.status)}">${escapeHtml(job.status || "")}</span>
            <div class="tiny-muted">${escapeHtml(job.started_at || "")} pid=${escapeHtml(job.pid || "")}</div>
            ${canCancel ? `<button class="ghost cancel-job" data-job="${escapeAttr(job.id)}">停止</button>` : ""}
          </div>`;
      })
      .join("") || `<div class="empty">暂无 job</div>`;
  document.querySelectorAll(".cancel-job").forEach((button) => {
    button.addEventListener("click", () => cancelJob(button.dataset.job));
  });
}

function renderPlugins() {
  $("plugins").innerHTML =
    state.plugins
      .map((plugin) => `
        <div class="mini-row">
          <strong>${escapeHtml(plugin.name)}</strong>
          <span>${plugin.enabled ? "启用" : "停用"}</span>
          <div class="tiny-muted">${escapeHtml(shortPath(plugin.root || ""))}</div>
          <button class="ghost plugin-toggle" data-name="${escapeAttr(plugin.name)}" data-root="${escapeAttr(plugin.root || "")}" data-enabled="${plugin.enabled ? "0" : "1"}">${plugin.enabled ? "停用" : "启用"}</button>
        </div>`)
      .join("") || `<div class="empty">暂无插件</div>`;
  document.querySelectorAll(".plugin-toggle").forEach((button) => {
    button.addEventListener("click", () => togglePlugin(button.dataset.name, button.dataset.root, button.dataset.enabled === "1"));
  });
}

async function startRun() {
  const invocation = $("run-command").value.trim();
  const args = $("run-arguments").value;
  if (!invocation) {
    $("run-result").textContent = "请输入 slash command";
    return;
  }
  const result = await api("/api/run", {
    method: "POST",
    body: JSON.stringify({
      invocation,
      arguments: args,
      target_workspace: $("target-workspace").value.trim(),
      strict_tools: $("strict-tools").checked,
      qa: $("qa-mode").value,
      max_steps: $("max-steps").value,
    }),
  });
  $("run-result").textContent = result.ok ? `已启动 job=${result.job_id || result.process_id} pid=${result.pid}` : result.error || "启动失败";
  setTimeout(tick, 1200);
}

async function resumeCurrent() {
  if (!state.selectedSession) return;
  const prompt = $("resume-prompt").value;
  const result = await api("/api/resume", {
    method: "POST",
    body: JSON.stringify({ session: state.selectedSession, prompt, target_workspace: $("target-workspace").value.trim() }),
  });
  $("resume-result").textContent = result.ok ? `已启动 resume job=${result.job_id || result.process_id}` : result.error || "resume 失败";
  setTimeout(tick, 1200);
}

async function answerCurrent() {
  if (!state.selectedSession) return;
  const answer = $("answer-input").value.trim();
  if (!answer) return;
  const result = await api("/api/answer", {
    method: "POST",
    body: JSON.stringify({ session: state.selectedSession, answer, target_workspace: $("target-workspace").value.trim() }),
  });
  $("resume-result").textContent = result.ok ? `已提交 answer job=${result.job_id || result.process_id}` : result.error || "answer 失败";
  setTimeout(tick, 1200);
}

async function cancelJob(jobId) {
  if (!jobId) return;
  await api(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST", body: "{}" });
  await loadJobs();
}

async function togglePlugin(name, root, enabled) {
  await api("/api/plugin", {
    method: "POST",
    body: JSON.stringify({ name, root, enabled }),
  });
  await loadSkills();
}

function shortPath(value) {
  const text = String(value || "");
  if (text.length < 70) return text;
  return "..." + text.slice(-67);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/'/g, "&#39;");
}

async function tick() {
  try {
    await loadHealth();
    await loadSessions();
    await loadJobs();
    if (state.selectedSession) await loadDetail();
  } catch (error) {
    console.error(error);
  }
}

$("refresh-button").addEventListener("click", tick);
$("run-button").addEventListener("click", startRun);
$("resume-button").addEventListener("click", resumeCurrent);
$("answer-button").addEventListener("click", answerCurrent);
$("skill-filter").addEventListener("input", renderSkills);
$("skill-select").addEventListener("change", () => {
  const selected = $("skill-select").value;
  if (selected) $("run-command").value = selected;
});

loadSkills().catch(console.error);
loadCapabilities().catch(console.error);
tick();
setInterval(tick, 2500);
