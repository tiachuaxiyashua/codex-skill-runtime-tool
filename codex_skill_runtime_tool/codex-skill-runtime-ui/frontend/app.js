const state = {
  mode: "new",
  sessions: [],
  skills: [],
  skillListings: [],
  capabilities: [],
  jobs: [],
  plugins: [],
  projects: [],
  currentProjectId: "",
  currentProject: null,
  showDiagnostics: false,
  selectedSkillGroup: "__all__",
  selectedSession: null,
  detail: null,
  selectedNodeId: null,
  lastEventCount: 0,
  memoryScope: "project",
  memory: null,
  selectedMemoryPath: "",
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
  updateSaveRootHint(data.default_save_root || "");
  if (!$("target-workspace").value && data.target_workspace) {
    $("target-workspace").placeholder = `通常留空；默认 ${data.target_workspace}`;
  }
  const chips = [
    ["默认工作区", data.target_workspace],
    ["当前项目", (state.currentProject && state.currentProject.name) || (data.current_project && data.current_project.name) || "-"],
    ["保存根目录", (state.currentProject && state.currentProject.save_root) || data.default_save_root],
    ["配置", data.runtime_env],
    ["API", data.codex_base_url || "未配置"],
    ["运行中", String(running)],
  ];
  $("health-strip").innerHTML = chips
    .map(([name, value]) => `<span class="health-chip"><strong>${escapeHtml(name)}</strong> ${escapeHtml(shortPath(value))}</span>`)
    .join("");
  state.jobs = data.jobs || [];
  renderJobs();
}

async function loadProjects() {
  try {
    const data = await api("/api/projects");
    state.projects = Array.isArray(data.projects) ? data.projects : [];
    state.currentProjectId = data.current_project_id || (data.current_project && data.current_project.id) || "";
    state.currentProject = data.current_project || state.projects.find((item) => item.id === state.currentProjectId) || state.projects[0] || null;
    renderProjects();
    return true;
  } catch (error) {
    state.projects = [];
    state.currentProjectId = "";
    state.currentProject = null;
    $("project-select").innerHTML = `<option value="__new__">新建项目...</option>`;
    $("project-select").value = "__new__";
    $("project-name").value = "";
    $("project-save-root").value = "";
    $("project-hint").textContent = `项目接口不可用，请重启 runtime：${error.message}`;
    updateSaveRootHint("");
    return false;
  }
}

function renderProjects() {
  const current = state.currentProject || {};
  $("project-select").innerHTML =
    state.projects
      .map((project) => `<option value="${escapeAttr(project.id || "")}">${escapeHtml(project.name || project.id || "未命名项目")}</option>`)
      .join("") + `<option value="__new__">新建项目...</option>`;
  $("project-select").value = current.id || "__new__";
  $("project-name").value = current.name || "";
  $("project-save-root").value = current.save_root || "";
  $("project-hint").textContent = current.save_root
    ? `当前项目历史只显示保存根目录下的任务：${shortPath(current.save_root)}`
    : "历史任务默认只显示当前项目。";
  updateSaveRootHint("");
}

function updateSaveRootHint(defaultRoot) {
  const creatingProject = $("project-select").value === "__new__" && !state.currentProjectId;
  if (creatingProject) {
    $("save-root").placeholder = "请先在左侧保存项目；也可以在这里临时填写本次任务保存根目录。";
    $("save-root-hint").textContent = "正在新建项目：保存前不会显示任何历史任务。";
    return;
  }
  const root = state.currentProject && state.currentProject.save_root ? state.currentProject.save_root : defaultRoot;
  $("save-root").placeholder = root
    ? `通常留空，使用当前项目保存根目录：${root}`
    : "通常留空，使用当前项目保存根目录。每个新任务都会自动创建独立子文件夹。";
  $("save-root-hint").textContent = root ? `当前项目默认：${root}` : "当前项目还没有保存根目录，请先在左侧项目管理中设置。";
}

async function changeProject(projectId) {
  if (projectId === "__new__") {
    state.currentProject = null;
    state.currentProjectId = "";
    state.sessions = [];
    $("project-name").value = "";
    $("project-save-root").value = "";
    $("project-hint").textContent = "填写项目名称后点保存；保存路径可留空，系统会自动创建。";
    state.selectedSession = null;
    state.detail = null;
    renderSessions();
    renderDetail();
    return;
  }
  await api("/api/projects/current", {
    method: "POST",
    body: JSON.stringify({ id: projectId }),
  });
  await loadProjects();
  state.selectedSession = null;
  state.detail = null;
  await loadSessions();
  await loadHealth();
}

async function saveProject() {
  const selected = $("project-select").value;
  const name = $("project-name").value.trim();
  const saveRoot = $("project-save-root").value.trim();
  if (!name) {
    $("project-hint").textContent = "请先填写项目名称。";
    $("project-name").focus();
    return;
  }
  $("project-hint").textContent = saveRoot ? "正在保存项目设置..." : "正在保存项目设置，系统会自动创建项目文件夹...";
  const result = await api("/api/projects", {
    method: "POST",
    body: JSON.stringify({
      id: selected === "__new__" ? "" : selected,
      name,
      save_root: saveRoot,
      make_current: true,
    }),
  });
  if (!result.ok) {
    $("project-hint").textContent = result.error || "保存失败";
    return;
  }
  state.projects = Array.isArray(result.projects) ? result.projects : [];
  state.currentProjectId = result.current_project_id || "";
  state.currentProject = result.current_project || state.projects.find((item) => item.id === state.currentProjectId) || null;
  renderProjects();
  state.selectedSession = null;
  state.detail = null;
  await loadSessions();
  await loadHealth();
}

async function loadSkills() {
  $("skill-count").textContent = "正在加载 skill...";
  $("skill-select").innerHTML = `<option value="">正在加载 skill...</option>`;
  $("command-select").innerHTML = `<option value="">等待 skill 列表</option>`;
  $("command-description").textContent = "首次加载需要读取本地 skill 仓库，可能需要十几秒。";
  try {
    const data = await api("/api/skills");
    if (!data.ok) {
      $("skill-count").textContent = "加载失败";
      $("skill-select").innerHTML = `<option value="">skill 加载失败</option>`;
      $("command-select").innerHTML = `<option value="">无法读取命令</option>`;
      $("skill-list").innerHTML = `<div class="empty">${escapeHtml(data.stderr || data.stdout || "无法读取 skill")}</div>`;
      $("command-description").textContent = data.stderr || data.stdout || "无法读取 skill。";
      return;
    }
    state.skills = Array.isArray(data.skills) ? data.skills : [];
    state.skillListings = Array.isArray(data.skill_listings) ? data.skill_listings : [];
    state.plugins = Array.isArray(data.plugins) ? data.plugins : [];
    renderSkills();
    renderPlugins();
  } catch (error) {
    $("skill-count").textContent = "加载失败";
    $("skill-select").innerHTML = `<option value="">skill 加载失败</option>`;
    $("command-select").innerHTML = `<option value="">无法读取命令</option>`;
    $("skill-list").innerHTML = `<div class="empty">${escapeHtml(error.message || "无法读取 skill")}</div>`;
    $("command-description").textContent = `无法读取 skill：${error.message || error}`;
  }
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

async function loadSessions() {
  if (!state.currentProjectId && $("project-select").value === "__new__") {
    state.sessions = [];
    renderSessions();
    return;
  }
  const params = new URLSearchParams();
  if (state.currentProjectId) params.set("project", state.currentProjectId);
  if (state.showDiagnostics) params.set("diagnostics", "1");
  const data = await api(`/api/sessions?${params.toString()}`);
  state.sessions = data.sessions || [];
  if (state.selectedSession && !state.sessions.some((session) => session.id === state.selectedSession)) {
    state.selectedSession = null;
    state.detail = null;
    state.selectedNodeId = null;
    renderDetail();
  }
  renderSessions();
  if (!state.selectedSession && state.sessions.length) {
    await selectSession(state.sessions[0].id);
  }
}

async function loadDetail({ refreshMemory = false } = {}) {
  if (!state.selectedSession) return;
  const data = await api(`/api/sessions/${encodeURIComponent(state.selectedSession)}`);
  state.detail = data;
  renderDetail();
  if (refreshMemory) await loadMemory();
}

async function loadMemory() {
  const suffix = state.selectedSession ? `?session=${encodeURIComponent(state.selectedSession)}` : "";
  const data = await api(`/api/memory${suffix}`);
  state.memory = data;
  renderMemory();
}

function renderSkills() {
  const filter = $("skill-filter").value.trim().toLowerCase();
  const visible = state.skills.filter((skill) => skillMatchesFilter(skill, filter)).slice(0, 160);
  $("skill-count").textContent = `${state.skills.length} 个 skill`;
  renderSkillGroupSelect();
  renderCommandSelect();
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
      chooseSkillCommand((item.dataset.command || "").replace(/^\/+/, ""));
      $("run-arguments").focus();
    });
  });
}

function skillMatchesFilter(skill, filter) {
  if (!filter) return true;
  const listing = skillListing(skill);
  return (
    skill.toLowerCase().includes(filter) ||
    String((listing && listing.description) || "").toLowerCase().includes(filter) ||
    String((listing && listing.agent) || "").toLowerCase().includes(filter)
  );
}

function skillListing(skill) {
  return state.skillListings.find((item) => item.name === skill) || {};
}

function skillGroupId(skill) {
  const text = String(skill || "");
  if (text.includes(":")) return text.split(":", 1)[0];
  return "__builtin__";
}

function skillGroupLabel(groupId, count) {
  if (groupId === "__all__") return `全部 Skill (${count})`;
  if (groupId === "__builtin__") return `通用内置 (${count})`;
  return `${groupId} (${count})`;
}

function skillGroups() {
  const groups = new Map();
  state.skills.forEach((skill) => {
    const group = skillGroupId(skill);
    groups.set(group, (groups.get(group) || 0) + 1);
  });
  return Array.from(groups.entries())
    .map(([id, count]) => ({ id, count }))
    .sort((a, b) => {
      if (a.id === "__builtin__") return 1;
      if (b.id === "__builtin__") return -1;
      return a.id.localeCompare(b.id, "zh-Hans-CN");
    });
}

function renderSkillGroupSelect() {
  const groups = skillGroups();
  if (!state.skills.length) {
    $("skill-select").innerHTML = `<option value="">没有加载到 skill</option>`;
    $("skill-select").value = "";
    return;
  }
  const validGroups = new Set(["__all__", ...groups.map((group) => group.id)]);
  if (!validGroups.has(state.selectedSkillGroup)) state.selectedSkillGroup = "__all__";
  $("skill-select").innerHTML =
    `<option value="__all__">${escapeHtml(skillGroupLabel("__all__", state.skills.length))}</option>` +
    groups.map((group) => `<option value="${escapeAttr(group.id)}">${escapeHtml(skillGroupLabel(group.id, group.count))}</option>`).join("");
  $("skill-select").value = state.selectedSkillGroup;
}

function commandsForSelectedGroup() {
  const group = state.selectedSkillGroup || "__all__";
  return state.skills
    .filter((skill) => group === "__all__" || skillGroupId(skill) === group)
    .sort((a, b) => a.localeCompare(b, "zh-Hans-CN"));
}

function renderCommandSelect(preferredSkill = "") {
  const commands = commandsForSelectedGroup();
  const current = preferredSkill || commandNameFromInvocation($("run-command").value);
  if (!commands.length) {
    $("command-select").innerHTML = `<option value="">当前 Skill 包没有可执行命令</option>`;
    $("command-select").value = "";
    renderCommandDescription("");
    return;
  }
  $("command-select").innerHTML =
    `<option value="">选择一个命令...</option>` +
    commands.map((skill) => `<option value="${escapeAttr(skill)}">/${escapeHtml(skill)}</option>`).join("");
  $("command-select").value = commands.includes(current) ? current : "";
  renderCommandDescription($("command-select").value || current);
}

function chooseSkillCommand(skill) {
  if (!skill) {
    $("command-select").value = "";
    $("run-command").value = "";
    renderCommandDescription("");
    return;
  }
  const group = skillGroupId(skill);
  state.selectedSkillGroup = group;
  renderSkillGroupSelect();
  renderCommandSelect(skill);
  $("command-select").value = skill;
  $("run-command").value = `/${skill}`;
  renderCommandDescription(skill);
  setMode("new");
}

function commandNameFromInvocation(value) {
  return String(value || "").trim().replace(/^\/+/, "");
}

function renderCommandDescription(skill) {
  const command = commandNameFromInvocation(skill);
  const listing = command ? skillListing(command) : {};
  if (!command) {
    $("command-description").textContent = state.skills.length
      ? "先选择 Skill 包，再从命令列表选择具体命令；也可以在右侧手动输入 slash command。"
      : "正在等待 skill 列表加载。";
    return;
  }
  const parts = [];
  if (listing.agent) parts.push(`默认 agent：${listing.agent}`);
  if (listing.source) parts.push(`来源：${listing.source}`);
  if (listing.description) parts.push(listing.description);
  $("command-description").textContent = parts.join(" ｜ ") || `将执行 /${command}`;
}

function renderSessions() {
  $("session-list").innerHTML =
    state.sessions
      .slice(0, 100)
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
              <span>agent ${agents}</span>
            </div>
            ${session.workspace ? `<div class="session-note">${escapeHtml(shortPath(session.workspace))}</div>` : ""}
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
  await loadDetail({ refreshMemory: true });
}

function renderDetail() {
  const detail = state.detail || {};
  const sessionState = detail.state || {};
  $("session-title").textContent = detail.id || "未选择任务";
  $("workspace-path").textContent = detail.workspace_path || sessionState.root || "未选择文件夹";
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
  renderFileTrees(detail);
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
      $("answer-input").focus();
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
      .join("") || `<div class="empty">暂无日志</div>`;
}

function renderArtifacts(data) {
  const artifacts = Array.isArray(data.artifacts) ? data.artifacts : [];
  const workspace = state.detail && state.detail.workspace_path ? state.detail.workspace_path : "";
  const tree = artifactTree(artifacts, workspace);
  $("artifacts").innerHTML = artifacts.length
    ? `<div class="artifact-tree">${fileTreeHtml(tree, 0, true)}</div>`
    : `<div class="empty">暂无产物</div>`;
  document.querySelectorAll("#artifacts .file-node-button").forEach((button) => {
    button.addEventListener("click", () => previewFile(button.dataset.path));
  });
}

function artifactTree(artifacts, workspace) {
  const root = { name: "产物", type: "directory", children: [], file_count: artifacts.length };
  const groups = new Map();
  artifacts.forEach((artifact) => {
    const path = artifact.path || "";
    if (!path) return;
    const type = artifact.type || "file";
    const groupName = artifactGroupName(type);
    if (!groups.has(groupName)) {
      const group = { name: groupName, type: "directory", children: [] };
      groups.set(groupName, group);
      root.children.push(group);
    }
    const group = groups.get(groupName);
    const segments = artifactPathSegments(path, workspace);
    insertArtifactNode(group, segments, {
      name: segments[segments.length - 1] || path,
      path,
      type: "file",
      bytes: artifact.bytes || 0,
      artifact_type: type,
      updated_at: artifact.created_at || "",
    });
  });
  root.children.sort((a, b) => a.name.localeCompare(b.name, "zh-Hans-CN"));
  return root;
}

function artifactGroupName(type) {
  if (type === "image") return "图片";
  if (type === "audio") return "音频";
  if (type === "document") return "文档";
  return "其他";
}

function artifactPathSegments(path, workspace) {
  const normalized = String(path || "").replace(/\\/g, "/");
  const workspaceNorm = String(workspace || "").replace(/\\/g, "/").replace(/\/+$/, "");
  let relative = normalized;
  if (workspaceNorm && normalized.toLowerCase().startsWith(`${workspaceNorm.toLowerCase()}/`)) {
    relative = normalized.slice(workspaceNorm.length + 1);
  } else {
    const parts = normalized.split("/").filter(Boolean);
    relative = parts.slice(-3).join("/");
  }
  const segments = relative.split("/").filter(Boolean);
  return segments.length ? segments : [normalized || "artifact"];
}

function insertArtifactNode(parent, segments, fileNode) {
  if (segments.length <= 1) {
    parent.children.push(fileNode);
    parent.children.sort(fileTreeSort);
    return;
  }
  const [head, ...tail] = segments;
  let folder = parent.children.find((item) => item.type === "directory" && item.name === head);
  if (!folder) {
    folder = { name: head, type: "directory", children: [] };
    parent.children.push(folder);
    parent.children.sort(fileTreeSort);
  }
  insertArtifactNode(folder, tail, fileNode);
}

function fileTreeSort(a, b) {
  if (a.type !== b.type) return a.type === "directory" ? -1 : 1;
  return String(a.name || "").localeCompare(String(b.name || ""), "zh-Hans-CN");
}

function renderFileTrees(detail) {
  const trees = [];
  if (detail.workspace_file_tree && detail.workspace_file_tree.path) trees.push(detail.workspace_file_tree);
  if (detail.file_tree && detail.file_tree.path) trees.push(detail.file_tree);
  $("files").innerHTML = trees.length
    ? trees.map((tree) => fileTreeHtml(tree, 0, true)).join("")
    : `<div class="empty">暂无文件</div>`;
  document.querySelectorAll("#files .file-node-button").forEach((button) => {
    button.addEventListener("click", () => previewFile(button.dataset.path));
  });
}

function fileTreeHtml(node, depth, root = false) {
  if (!node || !node.name) return "";
  const children = Array.isArray(node.children) ? node.children : [];
  const name = root ? `${node.name}${node.file_count !== undefined ? ` (${node.file_count})` : ""}` : node.name;
  if (node.type === "file") {
    const icon = node.artifact_type ? artifactIcon(node.artifact_type) : "F";
    return `
      <button class="file-node-button" data-path="${escapeAttr(node.path || "")}" style="--depth:${depth * 14}px">
        <span class="file-icon">${escapeHtml(icon)}</span>
        <span>${escapeHtml(name)}</span>
        <small>${escapeHtml(formatBytes(node.bytes || 0))}</small>
      </button>`;
  }
  return `
    <details class="file-folder" ${root || depth < 2 ? "open" : ""} style="--depth:${depth * 14}px">
      <summary><span class="file-icon">D</span><span>${escapeHtml(name)}</span></summary>
      <div>${children.map((child) => fileTreeHtml(child, depth + 1)).join("") || `<div class="empty indented">空文件夹</div>`}</div>
    </details>`;
}

function artifactIcon(type) {
  if (type === "image") return "I";
  if (type === "audio") return "A";
  if (type === "document") return "D";
  return "F";
}

async function previewFile(path) {
  if (!path) return;
  const response = await fetch(fileUrl(path));
  const contentType = response.headers.get("Content-Type") || "";
  if (contentType.startsWith("image/")) {
    $("node-detail").innerHTML = `<h3>${escapeHtml(shortPath(path))}</h3><img class="preview-image" src="${fileUrl(path)}" alt="">`;
    return;
  }
  if (contentType.startsWith("audio/")) {
    $("node-detail").innerHTML = `<h3>${escapeHtml(shortPath(path))}</h3><audio controls src="${fileUrl(path)}"></audio>`;
    return;
  }
  const text = await response.text();
  $("node-detail").innerHTML = `<h3>${escapeHtml(shortPath(path))}</h3><pre>${escapeHtml(text.slice(0, 80000))}</pre>`;
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

function renderMemory() {
  const docs = memoryDocsForScope();
  const help = memoryScopeHelp(state.memoryScope);
  $("memory-scope-help").innerHTML = `
    <strong>${escapeHtml(help.title)}</strong>
    <span>${escapeHtml(help.description)}</span>
  `;
  $("memory-list").innerHTML = docs.length
    ? docs
        .map((doc) => {
          const active = state.selectedMemoryPath === doc.path ? "active" : "";
          const missing = doc.exists ? "" : "missing";
          return `
            <button class="memory-item ${active} ${missing}" data-path="${escapeAttr(doc.path || "")}">
              <strong>${escapeHtml(doc.title || doc.name || "memory")}</strong>
              <span>${escapeHtml(doc.exists ? `${formatBytes(doc.bytes || 0)} ${doc.updated_at || ""}` : "尚未创建")}</span>
            </button>`;
        })
        .join("")
    : `<div class="empty">暂无记忆文件</div>`;
  document.querySelectorAll(".memory-item").forEach((button) => {
    button.addEventListener("click", () => selectMemoryFile(button.dataset.path));
  });
}

function memoryScopeHelp(scope) {
  if (scope === "project") {
    return {
      title: "项目记忆",
      description: "存放全局风格、项目笔记、资产清单等会影响整个项目的资料。界面标题是中文，文件内容保持原样。",
    };
  }
  if (scope === "durable") {
    return {
      title: "长期主题记忆",
      description: "runtime 从长期工作中整理出的主题经验和索引，用于跨任务找回上下文。",
    };
  }
  if (scope === "agent") {
    return {
      title: "Agent 记忆",
      description: "某类 agent 或角色长期积累的偏好、约束和工作记录。",
    };
  }
  return {
    title: "当前任务摘要",
    description: "当前会话的滚动摘要、压缩摘要和状态文件，用于异常关闭后继续追踪任务。",
  };
}

function memoryDocsForScope() {
  const memory = state.memory || {};
  if (state.memoryScope === "project") {
    const project = memory.project_memory || {};
    return [project.style, project.notes, project.assets].filter(Boolean);
  }
  if (state.memoryScope === "durable") {
    const durable = memory.durable_memory || {};
    return [durable.overview, ...(durable.topics || [])].filter(Boolean);
  }
  if (state.memoryScope === "agent") {
    return ((memory.agent_memory || {}).items || []).filter(Boolean);
  }
  const session = memory.session_memory || {};
  return [session.summary, session.compact, session.state].filter(Boolean);
}

async function selectMemoryFile(path) {
  if (!path) return;
  state.selectedMemoryPath = path;
  renderMemory();
  const data = await api(`/api/memory/file?path=${encodeURIComponent(path)}`);
  if (!data.ok) {
    $("memory-title").textContent = "读取失败";
    $("memory-path").textContent = path;
    $("memory-content").value = data.error || "";
    return;
  }
  const file = data.file || {};
  $("memory-title").textContent = file.title || file.name || "记忆文件";
  $("memory-path").textContent = file.path || "";
  $("memory-content").value = file.content || "";
  $("memory-result").textContent = file.exists ? "" : "该记忆文件尚未创建，保存后会创建。";
}

async function saveMemoryFile() {
  if (!state.selectedMemoryPath) {
    $("memory-result").textContent = "请先选择一个记忆文件";
    return;
  }
  $("memory-result").textContent = "正在保存...";
  const result = await api("/api/memory/file", {
    method: "POST",
    body: JSON.stringify({ path: state.selectedMemoryPath, content: $("memory-content").value }),
  });
  $("memory-result").textContent = result.ok ? `已保存 ${formatBytes(result.bytes || 0)}` : result.error || "保存失败";
  await loadMemory();
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
            ${job.metadata && job.metadata.target_workspace ? `<div class="tiny-muted">${escapeHtml(shortPath(job.metadata.target_workspace))}</div>` : ""}
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
    $("run-result").textContent = "请先选择或输入一个 Skill 命令";
    $("run-command").focus();
    return;
  }
  $("run-result").textContent = "正在启动...";
  const result = await api("/api/run", {
    method: "POST",
    body: JSON.stringify({
      invocation,
      arguments: args,
      project_id: state.currentProjectId,
      save_root: $("save-root").value.trim(),
      strict_tools: $("strict-tools").checked,
      qa: $("qa-mode").value,
      max_steps: $("max-steps").value,
    }),
  });
  if (result.ok) {
    $("run-result").textContent = `已启动，任务文件夹：${result.task_workspace || result.target_workspace || result.job_id}`;
  } else {
    $("run-result").textContent = result.error || "启动失败";
  }
  setTimeout(tick, 1200);
}

async function resumeCurrent() {
  if (!state.selectedSession) {
    $("resume-result").textContent = "请先在左侧选择一个历史任务";
    return;
  }
  const prompt = $("resume-prompt").value || $("run-arguments").value;
  $("resume-result").textContent = "正在继续...";
  const result = await api("/api/resume", {
    method: "POST",
    body: JSON.stringify({ session: state.selectedSession, prompt, target_workspace: $("target-workspace").value.trim() }),
  });
  $("resume-result").textContent = result.ok ? `已启动 resume job=${result.job_id || result.process_id}` : result.error || "继续失败";
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
  $("resume-result").textContent = result.ok ? `已提交回答 job=${result.job_id || result.process_id}` : result.error || "提交回答失败";
  $("answer-input").value = "";
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

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
  $("run-button").classList.toggle("primary", mode === "new");
  $("run-button").classList.toggle("secondary", mode !== "new");
  $("resume-button").classList.toggle("primary", mode === "resume");
  $("resume-button").classList.toggle("secondary", mode !== "resume");
  if (mode === "new") {
    $("run-arguments").placeholder = "像聊天一样描述目标。需求不完整也可以，runtime 或 skill 会在需要时暂停提问。";
    $("run-arguments").focus();
  } else {
    $("run-arguments").placeholder = "可选：补充当前任务接下来要做什么。留空则按已有上下文继续。";
    $("resume-prompt").focus();
  }
}

function shortPath(value) {
  const text = String(value || "");
  if (text.length < 78) return text;
  return "..." + text.slice(-75);
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
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

function bindEvents() {
  $("refresh-button").addEventListener("click", tick);
  $("project-save-button").addEventListener("click", () => saveProject().catch(console.error));
  $("project-new-button").addEventListener("click", () => changeProject("__new__").catch(console.error));
  $("project-select").addEventListener("change", () => changeProject($("project-select").value).catch(console.error));
  $("show-diagnostics").addEventListener("change", async () => {
    state.showDiagnostics = $("show-diagnostics").checked;
    state.selectedSession = null;
    state.detail = null;
    state.selectedNodeId = null;
    renderDetail();
    await loadSessions();
  });
  $("run-button").addEventListener("click", startRun);
  $("resume-button").addEventListener("click", resumeCurrent);
  $("answer-button").addEventListener("click", answerCurrent);
  $("memory-save-button").addEventListener("click", saveMemoryFile);
  $("skill-filter").addEventListener("input", renderSkills);
  $("skill-select").addEventListener("change", () => {
    state.selectedSkillGroup = $("skill-select").value || "__all__";
    renderCommandSelect();
    setMode("new");
  });
  $("command-select").addEventListener("change", () => {
    const selected = $("command-select").value;
    chooseSkillCommand(selected);
    if (selected) $("run-arguments").focus();
  });
  $("run-command").addEventListener("input", () => {
    renderCommandDescription($("run-command").value);
  });
  document.querySelectorAll(".mode-tab").forEach((button) => {
    button.addEventListener("click", () => setMode(button.dataset.mode || "new"));
  });
  document.querySelectorAll(".memory-tab").forEach((button) => {
    button.addEventListener("click", () => {
      state.memoryScope = button.dataset.memoryScope || "project";
      state.selectedMemoryPath = "";
      document.querySelectorAll(".memory-tab").forEach((item) => item.classList.toggle("active", item === button));
      $("memory-title").textContent = "未选择记忆文件";
      $("memory-path").textContent = "";
      $("memory-content").value = "";
      renderMemory();
    });
  });
  $("run-arguments").addEventListener("keydown", (event) => {
    if (event.ctrlKey && event.key === "Enter") {
      event.preventDefault();
      state.mode === "resume" ? resumeCurrent() : startRun();
    }
  });
}

async function boot() {
  bindEvents();
  setMode("new");
  await Promise.allSettled([loadProjects(), loadSkills(), loadCapabilities(), loadMemory()]);
  await tick();
  setInterval(tick, 2500);
}

boot().catch(console.error);
