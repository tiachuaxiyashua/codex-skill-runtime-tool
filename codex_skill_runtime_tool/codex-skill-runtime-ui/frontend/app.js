const state = {
  mode: "new",
  sessions: [],
  skills: [],
  skillListings: [],
  capabilities: [],
  jobs: [],
  plugins: [],
  services: [],
  modelConfig: null,
  projects: [],
  currentProjectId: "",
  currentProject: null,
  skillsLoaded: false,
  skillLoadError: "",
  showDiagnostics: false,
  selectedSkillGroup: "__all__",
  selectedSession: null,
  detail: null,
  selectedNodeId: null,
  localConversation: [],
  localProcess: [],
  lastEventCount: 0,
  memoryScope: "project",
  memory: null,
  selectedMemoryPath: "",
  shutdownSent: false,
  activeView: "chat",
  activeRunJobId: "",
  activeQuestion: null,
};

const $ = (id) => document.getElementById(id);
const query = new URLSearchParams(window.location.search);

function shouldShutdownOnClose() {
  const value = String(query.get("shutdown_on_close") || "").toLowerCase();
  return value === "1" || value === "true" || value === "yes";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const contentType = response.headers.get("Content-Type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof data === "object" && data && data.error ? data.error : `${response.status} ${response.statusText}`;
    const error = new Error(message);
    error.data = data;
    throw error;
  }
  return data;
}

function sendUiHeartbeat(closing = false) {
  const payload = JSON.stringify({
    page_id: state.pageId,
    closing,
    visible: document.visibilityState !== "hidden",
    at: new Date().toISOString(),
  });
  if (navigator.sendBeacon) {
    const blob = new Blob([payload], { type: "application/json" });
    if (navigator.sendBeacon("/api/ui/heartbeat", blob)) return;
  }
  fetch("/api/ui/heartbeat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: payload,
    keepalive: true,
  }).catch(() => {});
}

function requestUiShutdown() {
  if (state.shutdownSent) return;
  state.shutdownSent = true;
  const payload = JSON.stringify({
    page_id: state.pageId,
    source: "browser-close",
    at: new Date().toISOString(),
  });
  if (navigator.sendBeacon) {
    const blob = new Blob([payload], { type: "application/json" });
    if (navigator.sendBeacon("/api/ui/shutdown", blob)) return;
  }
  fetch("/api/ui/shutdown", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: payload,
    keepalive: true,
  }).catch(() => {});
}

function startUiHeartbeat() {
  state.pageId =
    (window.crypto && window.crypto.randomUUID && window.crypto.randomUUID()) ||
    `page-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  sendUiHeartbeat(false);
  setInterval(() => sendUiHeartbeat(false), 2000);
  window.addEventListener("pagehide", () => {
    sendUiHeartbeat(true);
    if (shouldShutdownOnClose()) requestUiShutdown();
  });
  window.addEventListener("beforeunload", () => {
    sendUiHeartbeat(true);
    if (shouldShutdownOnClose()) requestUiShutdown();
  });
}

function statusClass(value) {
  return String(value || "unknown").replace(/[^a-zA-Z0-9_-]/g, "_");
}

function switchWorkbenchView(view) {
  const next = view === "chat" ? "status" : view || "status";
  state.activeView = next;
  document.querySelectorAll(".workbench-pane").forEach((pane) => {
    pane.classList.toggle("active", pane.dataset.view === next);
  });
  document.querySelectorAll(".workbench-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.view === next);
  });
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
  state.services = Array.isArray(data.services) ? data.services : state.services;
  renderJobs();
  renderServices();
}

async function loadModelConfig() {
  const data = await api("/api/model-config");
  state.modelConfig = data;
  renderModelConfig();
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
    state.localConversation = [];
    state.localProcess = [];
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
  state.localConversation = [];
  state.localProcess = [];
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
  state.skillsLoaded = false;
  state.skillLoadError = "";
  $("skill-count").textContent = "正在加载 skill...";
  $("skill-select").innerHTML = `<option value="">正在加载 skill...</option>`;
  $("command-select").innerHTML = `<option value="">等待 skill 列表</option>`;
  $("command-description").textContent = "首次加载需要读取本地 skill 仓库，可能需要十几秒。";
  try {
    const data = await api("/api/skills");
    if (!data.ok) {
      state.skillsLoaded = true;
      state.skillLoadError = data.stderr || data.stdout || "无法读取 skill";
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
    state.skillsLoaded = true;
    renderSkills();
    renderPlugins();
  } catch (error) {
    state.skillsLoaded = true;
    state.skillLoadError = error.message || String(error);
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

async function loadServices() {
  const data = await api("/api/services");
  state.services = Array.isArray(data.services) ? data.services : [];
  renderServices();
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
  const querySession = String(query.get("session") || "").trim();
  if (state.selectedSession && state.selectedSession !== querySession && !state.sessions.some((session) => session.id === state.selectedSession)) {
    state.selectedSession = null;
    state.detail = null;
    state.selectedNodeId = null;
    renderDetail();
  }
  renderSessions();
}

async function loadDetail({ refreshMemory = false } = {}) {
  if (!state.selectedSession) return;
  const data = await api(`/api/sessions/${encodeURIComponent(state.selectedSession)}`);
  state.detail = data;
  renderDetail();
  if (refreshMemory) await loadMemory();
}

async function selectInitialSessionFromQuery() {
  const sessionId = String(query.get("session") || "").trim();
  if (!sessionId || state.selectedSession) return;
  state.selectedSession = sessionId;
  state.selectedNodeId = null;
  state.localConversation = [];
  state.localProcess = [];
  await loadDetail({ refreshMemory: true });
  renderSessions();
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
  if (groupId === "__all__") return `全部 Skill (${count}) - 仅浏览`;
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

function commandLeaf(skill) {
  const clean = commandNameFromInvocation(skill);
  if (!clean) return "";
  const parts = clean.split(":");
  return parts[parts.length - 1] || clean;
}

function commandAliasMatches(skill, alias) {
  const cleanSkill = commandNameFromInvocation(skill);
  const cleanAlias = commandNameFromInvocation(alias);
  if (!cleanSkill || !cleanAlias) return false;
  return cleanSkill === cleanAlias || commandLeaf(cleanSkill) === cleanAlias;
}

function sessionMatchesInvocation(session, invocation, workspace) {
  const cleanInvocation = commandNameFromInvocation(invocation);
  if (!cleanInvocation || !session) return false;
  const currentSkill = commandNameFromInvocation(session.current_skill);
  const label = String(session.label || "");
  const sessionWorkspace = String(session.workspace || "");
  return (
    currentSkill === cleanInvocation ||
    currentSkill.endsWith(`:${commandLeaf(cleanInvocation)}`) ||
    label.endsWith(cleanInvocation) ||
    label.endsWith(commandLeaf(cleanInvocation)) ||
    (workspace && sessionWorkspace === workspace)
  );
}

function resolveCommandName(command) {
  const clean = commandNameFromInvocation(command);
  if (!clean) return { ok: false, error: "缺少入口 skill。" };
  if (!state.skillsLoaded && !state.skills.length) {
    return { ok: false, error: "Skill 列表还在加载中，请稍等几秒后再发送。" };
  }
  if (state.skills.includes(clean)) return { ok: true, command: clean };
  const matches = state.skills.filter((skill) => commandAliasMatches(skill, clean));
  if (matches.length === 1) return { ok: true, command: matches[0] };
  if (matches.length > 1) {
    return {
      ok: false,
      error: `/${clean} 匹配到多个 skill：${matches.slice(0, 8).map((skill) => `/${skill}`).join("、")}。请从命令列表选择具体命令。`,
    };
  }
  if (state.skillLoadError) {
    return { ok: false, error: `Skill 列表加载失败，无法执行 /${clean}：${state.skillLoadError}` };
  }
  return { ok: false, error: `没有找到 /${clean}。请在 Skill 页搜索，或从“Skill 命令”下拉框选择。` };
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
  $("run-arguments").focus();
  const group = skillGroupId(skill);
  state.selectedSkillGroup = group;
  renderSkillGroupSelect();
  renderCommandSelect(skill);
  $("command-select").value = skill;
  $("run-command").value = `/${skill}`;
  autofillComposerWithInvocation(skill);
  renderCommandDescription(skill);
  setMode("new");
}

function autofillComposerWithInvocation(skill) {
  const input = $("run-arguments");
  const current = String(input.value || "").trim();
  const cleanSkill = String(skill || "").replace(/^\/+/, "");
  if (!cleanSkill) return;
  const currentBody = current.replace(/^\/\S+\s*/, "").trim();
  input.value = currentBody ? `/${cleanSkill} ${currentBody}` : `/${cleanSkill} `;
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}

function selectedInvocation() {
  const direct = commandNameFromInvocation($("run-command").value);
  if (direct) {
    const resolved = resolveCommandName(direct);
    return resolved.ok ? `/${resolved.command}` : "";
  }
  const selected = commandNameFromInvocation($("command-select").value);
  if (selected) {
    const resolved = resolveCommandName(selected);
    return resolved.ok ? `/${resolved.command}` : "";
  }
  const commands = commandsForSelectedGroup();
  if (commands.length === 1) return `/${commands[0]}`;
  return "";
}

function parseComposerRequest() {
  const raw = String($("run-arguments").value || "").trim();
  const selected = selectedInvocation();
  if (!raw) {
    return { invocation: selected, args: "", message: "" };
  }
  if (raw.startsWith("/")) {
    const [head, ...tail] = raw.split(/\s+/);
    const command = commandNameFromInvocation(head);
    const selectedCommand = commandNameFromInvocation(selected);
    if (selectedCommand && commandAliasMatches(selectedCommand, command)) {
      return {
        invocation: `/${selectedCommand}`,
        args: tail.join(" ").trim(),
        message: raw,
      };
    }
    const resolved = resolveCommandName(command);
    if (!resolved.ok) {
      return {
        invocation: "",
        args: tail.join(" ").trim(),
        message: raw,
        error: resolved.error,
      };
    }
    return {
      invocation: `/${resolved.command}`,
      args: tail.join(" ").trim(),
      message: raw,
    };
  }
  return {
    invocation: selected,
    args: raw,
    message: raw,
  };
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
  const activeSessions = state.sessions.filter((session) => ["starting", "running", "cancel_requested", "waiting_user"].includes(session.status));
  const historySessions = state.sessions.filter((session) => !activeSessions.includes(session));
  const rows = [...activeSessions, ...historySessions].slice(0, 100);
  $("session-list").innerHTML =
    rows
      .slice(0, 100)
      .map((session) => {
        const active = state.selectedSession === session.id ? "active" : "";
        const agents = Array.isArray(session.current_agents) ? session.current_agents.length : 0;
        const note = session.summary && session.summary.notes ? session.summary.notes : "";
        const canStop = ["starting", "running", "cancel_requested"].includes(session.status);
        const activeMarker = canStop ? `<span class="session-live-dot">运行中</span>` : `<span class="session-history-dot">历史</span>`;
        return `
          <div class="session-item ${active} ${statusClass(session.status)}" data-session="${escapeAttr(session.id)}">
            <strong>${activeMarker}${escapeHtml(session.label || session.id)}</strong>
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
            <div class="session-actions">
              <button class="ghost session-open" data-session="${escapeAttr(session.id)}">打开</button>
              <button class="ghost session-stop" data-session="${escapeAttr(session.id)}" ${canStop ? "" : "disabled"}>停止</button>
              <button class="ghost session-delete" data-session="${escapeAttr(session.id)}">删除</button>
            </div>
          </div>`;
      })
      .join("") || `<div class="empty">暂无历史任务</div>`;
  document.querySelectorAll(".session-item").forEach((item) => {
    item.addEventListener("click", () => selectSession(item.dataset.session));
  });
  document.querySelectorAll(".session-open").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      selectSession(button.dataset.session);
    });
  });
  document.querySelectorAll(".session-stop").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      stopSession(button.dataset.session);
    });
  });
  document.querySelectorAll(".session-delete").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteSession(button.dataset.session);
    });
  });
}

async function selectSession(sessionId, options = {}) {
  state.selectedSession = sessionId;
  state.selectedNodeId = null;
  state.localConversation = [];
  state.localProcess = [];
  renderSessions();
  await loadDetail({ refreshMemory: true });
  switchWorkbenchView(options.view || "status");
}

function renderDetail() {
  const detail = state.detail || {};
  const sessionState = detail.state || {};
  $("session-title").textContent = detail.id || "未选择任务";
  $("workspace-path").textContent = detail.workspace_path || sessionState.root || "未选择文件夹";
  const status = sessionState.status || sessionStatusFromDetail(detail);
  $("current-state").textContent = status || "unknown";
  $("current-skill").textContent = sessionState.current_skill || "-";
  const agents = Array.isArray(sessionState.current_agents) ? sessionState.current_agents : [];
  $("current-agent").textContent = agents.length ? agents.map((agent) => agent.name).join(", ") : "-";
  $("parallel-count").textContent = String(agents.length);
  renderCurrentControls(detail, status);
  renderQuestion(detail);
  renderConversation(detail);
  renderTree(detail.tree || {});
  renderLanes(detail.tree || {}, agents);
  renderTimeline(detail.events || [], detail);
  renderArtifacts(detail.artifacts || {});
  renderFileTrees(detail);
  renderSelectedNode();
}

function renderCurrentControls(detail, status) {
  const job = currentJobForDetail(detail);
  const running = job && isActiveJobStatus(job.status);
  $("current-stop-button").classList.toggle("hidden", !running);
  $("current-stop-button").disabled = !running || job.status === "cancel_requested";
  $("current-stop-button").dataset.job = running ? job.id : "";
  renderInlineStopButton(job);
  $("current-explain").textContent = currentStateExplanation(detail, status, job);
  const pending = pendingQuestion(detail);
  const needUser = $("current-need-user");
  if (pending && pending.question) {
    needUser.classList.remove("hidden");
    needUser.textContent = "等待你在底部对话框回答。";
  } else {
    needUser.classList.add("hidden");
    needUser.textContent = "";
  }
}

function sessionStatusFromDetail(detail) {
  const summary = detail.summary || {};
  if (summary.status) return summaryStatusForUi(summary.status);
  if (detail.id && currentJobForDetail(detail)) return "running";
  return "unknown";
}

function summaryStatusForUi(status) {
  const value = String(status || "").trim().toLowerCase().replace(/-/g, "_");
  if (["pass", "done", "ok", "success"].includes(value)) return "done";
  if (["answered", "resumed", "continued"].includes(value)) return "answered";
  if (["waiting_user", "waiting_for_user", "needs_user", "question"].includes(value)) return "waiting_user";
  if (["blocked", "cancelled", "canceled"].includes(value)) return value;
  if (["fail", "failed", "error"].includes(value)) return "failed";
  return value || "unknown";
}

function currentJobForDetail(detail) {
  if (!detail || !detail.id) return null;
  if (detail.active_job && detail.active_job.id) return detail.active_job;
  if (Array.isArray(detail.jobs)) {
    const active = detail.jobs.find((job) => isActiveJobStatus(job.status));
    if (active) return active;
  }
  const workspace = detail.workspace_path || (detail.state && detail.state.root) || "";
  return state.jobs.find((job) => {
    const metadata = job.metadata || {};
    return (
      (state.activeRunJobId && job.id === state.activeRunJobId && isActiveJobStatus(job.status)) ||
      metadata.session_id === detail.id ||
      (workspace && (metadata.target_workspace === workspace || metadata.task_workspace === workspace)) ||
      (metadata.target_workspace && String(detail.path || "").includes(String(metadata.target_workspace)))
    );
  }) || null;
}

function isActiveJobStatus(status) {
  return ["starting", "running", "cancel_requested"].includes(status);
}

function latestActiveRunJob() {
  if (state.activeRunJobId) {
    const job = state.jobs.find((item) => item.id === state.activeRunJobId);
    if (job && isActiveJobStatus(job.status)) return job;
  }
  return state.jobs.find((job) => job.operation === "run" && isActiveJobStatus(job.status)) || null;
}

function renderInlineStopButton(job = null) {
  const active = job && isActiveJobStatus(job.status) ? job : latestActiveRunJob();
  $("inline-stop-button").classList.toggle("hidden", !active);
  $("inline-stop-button").disabled = !active || active.status === "cancel_requested";
  $("inline-stop-button").dataset.job = active ? active.id : "";
}

function currentStateExplanation(detail, status, job) {
  if (!detail || !detail.id) return "还没有选择任务。左侧选择历史任务，或在对话框里发送一个新任务。";
  const pending = detail.pending_answer && detail.pending_answer.status === "answered" ? null : detail.pending_question;
  if (pending && pending.question) return "任务已暂停，正在等你回答。回答后会以同一个任务继续执行。";
  if (job && job.status === "cancel_requested") return "已请求停止，runtime 正在等待当前进程退出。";
  if (job && ["starting", "running"].includes(job.status)) return "任务正在执行。下方过程日志、任务树、Agent 状态和文件树会自动刷新。";
  if (status === "stale") return "这是历史任务留下的旧运行状态；当前没有对应的活动进程。可以继续该任务，或只查看历史产物。";
  if (status === "done" || status === "pass") return "任务已完成。可以查看产物、文件树、日志和记忆摘要。";
  if (status === "failed" || status === "error") return "任务失败或被中断。可以查看过程日志和 stderr/stdout 证据文件。";
  return "任务当前没有活动进程。可以继续当前任务，或者查看历史产物。";
}

function renderQuestion(detail) {
  const pending = pendingQuestion(detail);
  state.activeQuestion = pending;
  const banner = $("question-banner");
  if (banner) {
    banner.classList.add("hidden");
    if ($("question-text")) $("question-text").textContent = "";
    if ($("question-options")) $("question-options").innerHTML = "";
  }
}

function renderConversation(detail) {
  const history = $("conversation-history");
  const shouldStick = history.scrollHeight - history.scrollTop - history.clientHeight < 48;
  const previousScrollTop = history.scrollTop;
  const rows = conversationRowsWithActiveQuestion(detail, [...conversationRows(detail), ...state.localConversation.filter(isDialogueRow)]);
  updateExecutionLabel();
  if ($("conversation-mode-label")) $("conversation-mode-label").textContent = state.mode === "resume" ? "继续任务" : "新任务";
  $("conversation-history").innerHTML = rows.length
    ? rows.map(conversationRowHtml).join("")
    : `<div class="empty">还没有对话历史。直接描述目标即可；需要明确调用 skill 时，选择命令或输入 /skill:command。</div>`;
  if (shouldStick) {
    history.scrollTop = history.scrollHeight;
  } else {
    history.scrollTop = Math.min(previousScrollTop, Math.max(0, history.scrollHeight - history.clientHeight));
  }
  bindInlineQuestionOptions();
}

function conversationRowsWithActiveQuestion(detail, rows) {
  const active = state.activeQuestion || pendingQuestion(detail);
  if (!active || !active.question) return rows;
  const activeText = String(active.question || "").trim();
  const matchesActiveQuestion = (row) => {
    const text = String(row.text || "").trim();
    if (!text || !activeText) return false;
    if (row.kind === "question" && text === activeText) return true;
    const assistantLike = row.role === "assistant" || row.kind === "assistant_message";
    return assistantLike && (text.includes(activeText) || activeText.includes(text));
  };
  const exists = rows.some(matchesActiveQuestion);
  if (exists) {
    return rows.map((row) =>
      matchesActiveQuestion(row)
        ? { ...row, awaiting_user: true, options: active.options || row.options || [], source: active.source || row.source || "" }
        : row
    );
  }
  return [
    ...rows,
    {
      role: "assistant",
      kind: "question",
      title: "助手",
      text: active.question,
      at: active.created_at || "",
      status: "waiting_user",
      source: active.source || "",
      options: active.options || [],
    },
  ];
}

function bindInlineQuestionOptions() {
  document.querySelectorAll(".inline-question-option").forEach((button) => {
    button.addEventListener("click", () => {
      const value = button.dataset.answer || "";
      $("run-arguments").value = value;
      $("run-arguments").focus();
      setMode("resume");
    });
  });
}

function conversationRows(detail) {
  const conversationEvents = Array.isArray(detail.conversation_events) ? detail.conversation_events : [];
  if (conversationEvents.length) {
    return conversationEvents
      .map((event) => ({
        role: event.role || "runtime",
        kind: event.kind || "event",
        title: event.title || event.kind || "事件",
        text: event.text || "",
        at: event.timestamp || "",
        status: event.status || "",
        source: event.source || "",
        data: event.data || {},
      }))
      .filter(isDialogueRow);
  }
  if (Array.isArray(detail.history) && detail.history.length) {
    return detail.history
      .map((item) => ({
        role: item.role || "runtime",
        kind: item.kind || "",
        title: item.title || "消息",
        text: item.text || "",
        at: item.at || "",
      }))
      .filter(isDialogueRow);
  }
  const rows = [];
  const stateData = detail.state || {};
  const metadata = stateData.metadata || {};
  const initialPrompt = metadata.arguments || metadata.prompt || metadata.user_prompt || "";
  if (initialPrompt) {
    rows.push({ role: "你", title: "初始需求", text: initialPrompt, at: stateData.created_at || "" });
  }
  const transcript = Array.isArray(detail.transcript) ? detail.transcript : [];
  transcript.slice(-20).forEach((item) => {
    const role = item.role || item.type || "runtime";
    const text = item.text || item.content || item.message || "";
    if (text) rows.push({ role: roleLabel(role), title: transcriptTitle(role), text, at: item.timestamp || "" });
  });
  const pending = pendingQuestion(detail);
  if (pending && pending.question) {
    rows.push({ role: "runtime", kind: "question", title: "等待你回答", text: pending.question, at: pending.created_at || "", status: "waiting_user" });
  }
  return rows;
}

function pendingQuestion(detail) {
  if (!detail) return null;
  const pending = detail.pending_answer && detail.pending_answer.status === "answered" ? null : detail.pending_question;
  if (!pending || !pending.question) return null;
  return { ...pending, source: "pending" };
}

function isDialogueRow(row) {
  if (!row) return false;
  const role = String(row.role || "");
  const kind = String(row.kind || "");
  if (role === "user" || role === "你") return true;
  if (kind === "reasoning") return false;
  if (role === "assistant" || kind === "assistant_message") return true;
  if (kind === "question" || kind === "answer") return true;
  return false;
}

function isProcessRow(row) {
  if (!row || isDialogueRow(row)) return false;
  const role = String(row.role || "");
  const kind = String(row.kind || "");
  const processKinds = new Set([
    "runtime_process",
    "tool_call",
    "tool_result",
    "model_start",
    "model_finish",
    "model_stream",
    "job",
    "hook",
    "session",
    "memory",
    "skill",
    "plan",
    "bridge",
    "voice",
    "ide",
    "mcp",
    "summary",
    "reasoning",
    "error",
    "event",
  ]);
  if (processKinds.has(kind)) return true;
  return role === "runtime" || role === "tool" || role === "system";
}

function conversationRowHtml(row) {
  const own = row.role === "你" || row.role === "user";
  const kind = row.kind || "";
  const processKinds = new Set(["runtime_process", "tool_call", "tool_result", "model_start", "model_finish", "model_stream", "job", "hook", "memory", "skill", "plan", "bridge", "voice", "ide", "mcp", "session", "summary"]);
  const isQuestion = kind === "question";
  const isAssistant = !isQuestion && (row.role === "assistant" || kind === "assistant_message") && kind !== "reasoning";
  const isError = kind === "error";
  const isProcess = !own && !isAssistant && !isQuestion && !isError && (row.role === "runtime" || row.role === "tool" || processKinds.has(kind));
  const roleName = own ? "你" : isAssistant ? "助手" : row.role || "runtime";
  const status = row.status ? `<span class="pill ${statusClass(row.status)}">${escapeHtml(row.status)}</span>` : "";
  const rowClasses = [own ? "user" : isAssistant ? "assistant" : isQuestion ? "question" : "runtime", isProcess ? "process" : "", statusClass(kind), statusClass(row.status || "")].join(" ");
  if (isProcess) {
    const source = row.source ? `<div class="conversation-source">${escapeHtml(shortPath(row.source))}</div>` : "";
    const data = row.data && Object.keys(row.data).length
      ? `<details class="conversation-data"><summary>详情</summary><pre>${escapeHtml(JSON.stringify(row.data, null, 2))}</pre></details>`
      : "";
    const title = row.title || processTitle(row);
    const text = processPreviewText(row);
    return `
      <div class="conversation-row ${escapeAttr(rowClasses)}">
        <div class="process-line">
          <span class="process-dot"></span>
          <span class="process-title">${escapeHtml(title)}</span>
          ${status}
          <small>${escapeHtml(row.at || "")}</small>
        </div>
        ${text ? `<div class="conversation-text process-preview">${escapeHtml(text)}</div>` : ""}
        ${(source || data) ? `<details class="conversation-data process-details"><summary>查看原始过程</summary>${source}${data}</details>` : ""}
      </div>`;
  }
  if (isAssistant) {
    const kindLabel = kind === "assistant_message" ? "模型回复" : "模型消息";
    const options = Array.isArray(row.options) ? row.options : [];
    const optionHtml = options.length
      ? `<div class="inline-question-options">${options.map((option) => `<button class="inline-question-option" type="button" data-answer="${escapeAttr(String(option))}">${escapeHtml(String(option))}</button>`).join("")}</div>`
      : "";
    return `
      <div class="conversation-row ${escapeAttr(rowClasses)}">
        <div class="assistant-body${isQuestion ? " question-body" : ""}" data-kind="${escapeAttr(kindLabel)}">
          ${escapeHtml(row.text || "")}
          ${optionHtml}
        </div>
      </div>`;
  }
  if (isQuestion) {
    const options = Array.isArray(row.options) ? row.options : [];
    const optionHtml = options.length
      ? `<div class="inline-question-options">${options.map((option) => `<button class="inline-question-option" type="button" data-answer="${escapeAttr(String(option))}">${escapeHtml(String(option))}</button>`).join("")}</div>`
      : "";
    return `
      <div class="conversation-row ${escapeAttr(rowClasses)}">
        <div class="assistant-body question-body" data-kind="需要你回答">
          <div class="conversation-text">${escapeHtml(row.text || "")}</div>
          ${optionHtml}
        </div>
      </div>`;
  }
  return `
    <div class="conversation-row ${escapeAttr(rowClasses)}">
      <div class="conversation-meta">
        <div class="conversation-meta-left">
          <strong>${escapeHtml(row.title || roleName)}</strong>
          ${status}
        </div>
        <span>${escapeHtml(row.at || "")}</span>
      </div>
      <div class="conversation-text">${escapeHtml(row.text || "")}</div>
    </div>`;
}

function processTitle(row) {
  const kind = String(row.kind || "");
  if (kind === "job") return "任务运行";
  if (kind === "model_start") return "模型开始";
  if (kind === "model_finish") return "模型结束";
  if (kind === "model_stream") return "模型事件";
  if (kind === "summary") return "任务总结";
  if (kind === "session") return "会话状态";
  if (kind === "tool_call") return "工具调用";
  if (kind === "tool_result") return "工具结果";
  return row.title || "运行过程";
}

function processPreviewText(row) {
  const text = String(row.text || "").trim();
  if (!text) return "";
  if (row.kind === "summary") {
    return row.status ? `任务总结已保存，状态：${row.status}` : "任务总结已保存";
  }
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const useful = lines.filter((line) => !line.startsWith("{") && !line.startsWith("}") && !line.includes('"_'));
  const picked = useful.slice(0, 2).join(" ");
  const value = picked || lines.slice(0, 1).join(" ");
  return value.length > 220 ? `${value.slice(0, 220)}...` : value;
}

function roleLabel(role) {
  const value = String(role || "").toLowerCase();
  if (value === "user") return "你";
  if (value.includes("assistant")) return "assistant";
  return role || "runtime";
}

function transcriptTitle(role) {
  const value = String(role || "").toLowerCase();
  if (value === "user") return "你说";
  if (value.includes("assistant")) return "模型回复";
  if (value.includes("tool")) return "工具结果";
  return "运行记录";
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
      switchWorkbenchView("preview");
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

function renderTimeline(events, detail = state.detail) {
  const rows = timelineRows(events, detail);
  const newClass = rows.length !== state.lastEventCount ? "new" : "";
  state.lastEventCount = rows.length;
  $("timeline").innerHTML =
    rows
      .map((event) => {
        const data = event.data || {};
        const suffix = data.returncode !== undefined ? ` returncode=${data.returncode}` : "";
        const preview = event.preview ? `<div class="event-preview">${escapeHtml(event.preview)}</div>` : "";
        const detailHtml = data && Object.keys(data).length
          ? `<details class="event-data"><summary>详情</summary><pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre></details>`
          : "";
        return `
          <div class="timeline-item ${newClass}">
            <div><strong>${escapeHtml(event.message || "")}</strong></div>
            <div class="event-type">${escapeHtml(event.timestamp || "")} / ${escapeHtml(event.type || "")}${escapeHtml(suffix)}</div>
            ${preview}
            ${detailHtml}
          </div>`;
      })
      .join("") || `<div class="empty">暂无日志</div>`;
}

function timelineRows(events, detail) {
  const rows = [];
  const push = (row) => {
    if (!row || !row.message) return;
    rows.push({
      timestamp: row.timestamp || "",
      type: row.type || "event",
      message: row.message || "",
      preview: row.preview || "",
      data: row.data || {},
      source: row.source || "",
    });
  };
  (Array.isArray(events) ? events : []).forEach((event) => {
    push({
      timestamp: event.timestamp || "",
      type: event.type || "event",
      message: event.message || event.type || "运行事件",
      data: event.data || {},
      source: event.source || event.type || "",
    });
  });
  const conversationEvents = Array.isArray(detail && detail.conversation_events) ? detail.conversation_events : [];
  conversationEvents
    .map((event) => ({
      role: event.role || "runtime",
      kind: event.kind || "event",
      title: event.title || event.kind || "事件",
      text: event.text || "",
      at: event.timestamp || "",
      status: event.status || "",
      source: event.source || "",
      data: event.data || {},
    }))
    .filter(isProcessRow)
    .forEach((row) => {
      push({
        timestamp: row.at || "",
        type: row.kind || "event",
        message: processTitle(row),
        preview: processPreviewText(row),
        data: row.data || {},
        source: row.source || "",
      });
    });
  state.localProcess.filter(isProcessRow).forEach((row) => {
    push({
      timestamp: row.at || "",
      type: row.kind || "runtime_process",
      message: processTitle(row),
      preview: processPreviewText(row),
      data: row.data || {},
      source: row.source || "",
    });
  });
  return dedupeTimelineRows(rows)
    .sort((a, b) => `${b.timestamp}`.localeCompare(`${a.timestamp}`))
    .slice(0, 300);
}

function dedupeTimelineRows(rows) {
  const result = [];
  const seen = new Set();
  rows.forEach((row) => {
    const key = [row.timestamp || "", row.type || "", row.message || "", row.preview || "", row.source || ""].join("\u0000");
    if (seen.has(key)) return;
    seen.add(key);
    result.push(row);
  });
  return result;
}

function renderArtifacts(data) {
  const artifacts = Array.isArray(data.artifacts) ? data.artifacts : [];
  const workspace = state.detail && state.detail.workspace_path ? state.detail.workspace_path : "";
  const tree = artifactTree(artifacts, workspace);
  $("artifact-count-label").textContent = String(artifacts.length);
  $("artifacts").innerHTML = artifacts.length
    ? `<div class="artifact-tree">${fileTreeHtml(tree, 0, true)}</div>`
    : `<div class="empty">暂无产物。任务写入文件、图片、音频或报告后会出现在这里。</div>`;
  document.querySelectorAll("#artifacts .file-node-button").forEach((button) => {
    button.addEventListener("click", () => {
      switchWorkbenchView("preview");
      previewFile(button.dataset.path);
    });
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
  const total = trees.reduce((sum, tree) => sum + Number(tree.file_count || 0), 0);
  $("file-count-label").textContent = String(total);
  $("files").innerHTML = trees.length
    ? trees.map((tree) => fileTreeHtml(tree, 0, true)).join("")
    : `<div class="empty">暂无任务文件。任务启动后，每个新任务会有独立文件夹。</div>`;
  document.querySelectorAll("#files .file-node-button").forEach((button) => {
    button.addEventListener("click", () => {
      switchWorkbenchView("preview");
      previewFile(button.dataset.path);
    });
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
    document.querySelectorAll(".evidence-link").forEach((button) => button.addEventListener("click", () => {
      switchWorkbenchView("preview");
      previewFile(button.dataset.path);
    }));
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
  switchWorkbenchView("memory");
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

function renderServices() {
  $("services").innerHTML =
    state.services
      .map((service) => {
        const status = service.status || "stopped";
        const running = status === "running" || status === "starting";
        return `
          <div class="mini-row">
            <strong>${escapeHtml(service.label || service.id || "service")}</strong>
            <span class="pill ${statusClass(status)}">${escapeHtml(status)}</span>
            <div class="tiny-muted">${escapeHtml(service.description || "")}</div>
            <div class="tiny-muted">${escapeHtml(shortPath(service.endpoint || service.health_url || ""))}</div>
            <div class="service-actions">
              <button class="ghost service-start" data-id="${escapeAttr(service.id || "")}" ${running ? "disabled" : ""}>启动</button>
              <button class="ghost service-stop" data-id="${escapeAttr(service.id || "")}" ${running ? "" : "disabled"}>停止</button>
            </div>
          </div>`;
      })
      .join("") || `<div class="empty">暂无外部服务</div>`;
  document.querySelectorAll(".service-start").forEach((button) => {
    button.addEventListener("click", () => startService(button.dataset.id));
  });
  document.querySelectorAll(".service-stop").forEach((button) => {
    button.addEventListener("click", () => stopService(button.dataset.id));
  });
}

function renderModelConfig() {
  const data = state.modelConfig || {};
  const config = data.config || {};
  const presets = Array.isArray(data.presets) ? data.presets : [];
  const currentRows = [
    ["当前模型", config.model || "-"],
    ["Review 模型", config.review_model || "-"],
    ["Provider", config.provider || data.active_profile || "-"],
    ["Base URL", config.base_url || "未配置"],
    ["Wire API", config.wire_api || "-"],
    ["API key 文件", config.api_key_file || "未配置"],
    ["API key 文件存在", config.api_key_file_exists ? "是" : "否"],
    ["本地 provider", config.local_provider || "-"],
    ["Codex OSS", config.codex_oss ? "是" : "否"],
    ["Reasoning effort", config.reasoning_effort || "-"],
    ["上下文窗口", String(config.context_window || 0)],
    ["自动压缩阈值", String(config.auto_compact_token_limit || 0)],
    ["响应存储", config.disable_response_storage ? "已禁用" : "启用"],
    ["网络访问", config.network_access || "-"],
  ];
  $("model-current").innerHTML = currentRows
    .map(([label, value]) => `
      <div class="mini-row">
        <strong>${escapeHtml(label)}</strong>
        <div class="tiny-muted">${escapeHtml(value)}</div>
      </div>`)
    .join("");
  $("model-presets").innerHTML = presets
    .map(
      (preset) => `
        <div class="mini-row">
          <strong>${escapeHtml(preset.label || preset.id || "preset")}</strong>
          <div class="tiny-muted">${escapeHtml(preset.description || "")}</div>
          <button class="ghost model-apply-preset" data-preset="${escapeAttr(preset.id || "")}">应用预设</button>
        </div>`
    )
    .join("");
  document.querySelectorAll(".model-apply-preset").forEach((button) => {
    button.addEventListener("click", () => applyModelPreset(button.dataset.preset || ""));
  });
  if (!$("model-name").dataset.userEdited) $("model-name").value = config.model || "";
  if (!$("model-review").dataset.userEdited) $("model-review").value = config.review_model || "";
  if (!$("model-provider").dataset.userEdited) $("model-provider").value = config.provider || "";
  if (!$("model-base-url").dataset.userEdited) $("model-base-url").value = config.base_url || "";
  if (!$("model-wire-api").dataset.userEdited) $("model-wire-api").value = config.wire_api || "responses";
  if (!$("model-requires-auth").dataset.userEdited) $("model-requires-auth").checked = Boolean(config.requires_openai_auth);
  if (!$("model-codex-oss").dataset.userEdited) $("model-codex-oss").checked = Boolean(config.codex_oss);
  if (!$("model-local-provider").dataset.userEdited) $("model-local-provider").value = config.local_provider || "";
  if (!$("model-reasoning").dataset.userEdited) $("model-reasoning").value = config.reasoning_effort || "low";
  if (!$("model-network").dataset.userEdited) $("model-network").value = config.network_access || "enabled";
  if (!$("model-context-window").dataset.userEdited) $("model-context-window").value = String(config.context_window || "");
  if (!$("model-compact-limit").dataset.userEdited) $("model-compact-limit").value = String(config.auto_compact_token_limit || "");
  if (!$("model-disable-storage").dataset.userEdited) $("model-disable-storage").checked = Boolean(config.disable_response_storage ?? true);
  $("model-save-result").textContent = data.runtime_env ? `配置文件：${shortPath(data.runtime_env)}` : "";
}

function markModelInputEdited(id) {
  const el = $(id);
  if (el) el.dataset.userEdited = "1";
}

function clearModelInputEdits() {
  [
    "model-name",
    "model-review",
    "model-provider",
    "model-base-url",
    "model-wire-api",
    "model-requires-auth",
    "model-codex-oss",
    "model-local-provider",
    "model-reasoning",
    "model-network",
    "model-context-window",
    "model-compact-limit",
    "model-disable-storage",
  ].forEach((id) => {
    const el = $(id);
    if (el) delete el.dataset.userEdited;
  });
}

function modelConfigPayload() {
  return {
    model: $("model-name").value.trim(),
    review_model: $("model-review").value.trim(),
    provider: $("model-provider").value.trim(),
    base_url: $("model-base-url").value.trim(),
    wire_api: $("model-wire-api").value,
    requires_openai_auth: $("model-requires-auth").checked,
    codex_oss: $("model-codex-oss").checked,
    local_provider: $("model-local-provider").value,
    reasoning_effort: $("model-reasoning").value,
    network_access: $("model-network").value,
    context_window: Number($("model-context-window").value || 0),
    auto_compact_token_limit: Number($("model-compact-limit").value || 0),
    disable_response_storage: $("model-disable-storage").checked,
  };
}

async function saveModelConfig() {
  $("model-save-result").textContent = "正在保存...";
  const result = await api("/api/model-config", {
    method: "POST",
    body: JSON.stringify(modelConfigPayload()),
  });
  $("model-save-result").textContent = result.ok ? "已保存模型配置" : result.error || "保存失败";
  clearModelInputEdits();
  await loadHealth();
  await loadModelConfig();
}

async function applyModelPreset(presetId) {
  const data = state.modelConfig || {};
  const presets = Array.isArray(data.presets) ? data.presets : [];
  const preset = presets.find((item) => item.id === presetId);
  if (!preset) return;
  const values = preset.values || {};
  $("model-name").value = values.model || "";
  $("model-review").value = values.review_model || values.model || "";
  $("model-provider").value = values.provider || "";
  $("model-base-url").value = values.base_url || "";
  $("model-wire-api").value = values.wire_api || "responses";
  $("model-requires-auth").checked = Boolean(values.requires_openai_auth);
  $("model-codex-oss").checked = Boolean(values.codex_oss);
  $("model-local-provider").value = values.local_provider || "";
  $("model-reasoning").value = values.reasoning_effort || "low";
  $("model-network").value = values.network_access || "enabled";
  $("model-context-window").value = values.context_window ?? "";
  $("model-compact-limit").value = values.auto_compact_token_limit ?? "";
  $("model-disable-storage").checked = Boolean(values.disable_response_storage ?? true);
  ["model-name", "model-review", "model-provider", "model-base-url", "model-wire-api", "model-requires-auth", "model-codex-oss", "model-local-provider", "model-reasoning", "model-network", "model-context-window", "model-compact-limit", "model-disable-storage"].forEach(markModelInputEdited);
  $("model-save-result").textContent = `已应用预设：${preset.label || preset.id || ""}`;
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
  switchWorkbenchView("status");
  state.localConversation = [];
  state.localProcess = [];
  const parsed = parseComposerRequest();
  const invocation = parsed.invocation;
  const args = parsed.args;
  const message = parsed.message || args || "";
  const explicitInvocation = Boolean(invocation);
  if (parsed.error) {
    const messageText = parsed.error;
    $("run-result").textContent = messageText;
    appendRuntimeProcessMessage(messageText);
    return;
  }
  if (!message && !invocation) {
    const messageText = "请输入你想完成什么。可以直接自然语言描述，也可以输入 /skill:command。";
    $("run-result").textContent = messageText;
    appendRuntimeProcessMessage(messageText);
    return;
  }
  if (!message && invocation) {
    const messageText = "请在命令后补充任务内容，或直接用自然语言描述目标。";
    $("run-result").textContent = messageText;
    appendRuntimeProcessMessage(messageText);
    return;
  }
  $("run-result").textContent = explicitInvocation ? "正在启动任务..." : "Codex 正在思考...";
  renderLocalConversationDraft(message || `${invocation}${args ? ` ${args}` : ""}`, "你");
  try {
    const endpoint = explicitInvocation ? "/api/run" : "/api/chat";
    const result = await api(endpoint, {
      method: "POST",
      body: JSON.stringify({
        invocation,
        arguments: args,
        message,
        project_id: state.currentProjectId,
        save_root: $("save-root").value.trim(),
        strict_tools: $("strict-tools").checked,
        qa: $("qa-mode").value,
        max_steps: $("max-steps").value,
      }),
    });
    if (result.ok) {
      const workspace = result.task_workspace || result.target_workspace || "";
      const effectiveInvocation = result.invocation || invocation || "";
      state.activeRunJobId = result.job_id || result.process_id || "";
      if (state.activeRunJobId) renderInlineStopButton({ id: state.activeRunJobId, status: "running", operation: "run" });
      $("run-result").textContent = explicitInvocation ? `已启动，任务文件夹：${workspace || result.job_id}` : `已提交给 Codex，任务文件夹：${workspace || result.job_id}`;
      appendRuntimeProcessMessage(`${explicitInvocation ? `已启动：${effectiveInvocation || "-"}` : "Codex 正在生成真实回复"}
job=${result.job_id || result.process_id}
任务文件夹：${workspace || "-"}`);
      $("run-arguments").value = "";
      await loadJobs();
      await loadSessions();
      let target = state.sessions.find((session) => session.workspace === result.task_workspace || session.workspace === result.target_workspace);
      for (let i = 0; !target && i < 10; i++) {
        await new Promise((resolve) => setTimeout(resolve, 300));
        await loadSessions();
        target =
          state.sessions.find((session) => session.workspace === result.task_workspace || session.workspace === result.target_workspace) ||
          state.sessions.find((session) => session.status === "running" && sessionMatchesInvocation(session, effectiveInvocation, workspace));
      }
      if (!target) {
        target = state.sessions.find((session) => session.status === "running" && sessionMatchesInvocation(session, effectiveInvocation, workspace));
      }
      if (target) await selectSession(target.id, { view: "chat" });
    } else {
      $("run-result").textContent = result.error || "需要补充信息";
      appendRuntimeProcessMessage(result.error || "启动失败");
    }
  } catch (error) {
    const message = error.message || String(error);
    $("run-result").textContent = message;
    appendRuntimeProcessMessage(`启动失败：${message}`);
  }
  setTimeout(tick, 1200);
}

async function resumeCurrent() {
  switchWorkbenchView("status");
  state.localConversation = [];
  state.localProcess = [];
  if (!state.selectedSession) {
    $("resume-result").textContent = "请先在左侧选择一个历史任务";
    appendRuntimeProcessMessage("请先在左侧选择一个历史任务，再继续。若要开始新任务，请切回“新任务”并输入 /skill:command。")
    return;
  }
  const prompt = $("run-arguments").value || $("resume-prompt").value;
  $("resume-result").textContent = "正在继续...";
  renderLocalConversationDraft(prompt || "继续当前任务", "你");
  try {
    const result = await api("/api/resume", {
      method: "POST",
      body: JSON.stringify({ session: state.selectedSession, prompt, target_workspace: $("target-workspace").value.trim() }),
    });
    $("resume-result").textContent = result.ok ? `已启动 resume job=${result.job_id || result.process_id}` : result.error || "继续失败";
    appendRuntimeProcessMessage(result.ok ? `已继续当前任务：job=${result.job_id || result.process_id}` : result.error || "继续失败");
    if (result.ok) {
      $("run-arguments").value = "";
      await selectContinuationSession(result);
    }
  } catch (error) {
    const message = error.message || String(error);
    $("resume-result").textContent = message;
    appendRuntimeProcessMessage(`继续失败：${message}`);
  }
  setTimeout(tick, 1200);
}

function renderLocalConversationDraft(text, title) {
  if (!text) return;
  appendConversationRow({ role: title === "runtime" ? "runtime" : "user", title: title === "runtime" ? "runtime" : "你", text, at: new Date().toLocaleString() });
}

function appendRuntimeProcessMessage(text, extra = {}) {
  if (!text) return;
  const row = { role: "runtime", kind: "runtime_process", title: "runtime 过程", text, at: new Date().toLocaleString(), ...extra };
  state.localProcess.push(row);
  state.localProcess = state.localProcess.slice(-80);
  renderTimeline((state.detail && state.detail.events) || [], state.detail || {});
}

function updateExecutionLabel() {
  const label = $("selected-command-label");
  if (!label) return;
  const command = selectedInvocation();
  label.textContent = command ? command : "自然语言";
}

function appendConversationRow(row) {
  state.localConversation.push(row);
  state.localConversation = state.localConversation.slice(-40);
  if (!isDialogueRow(row)) return;
  const history = $("conversation-history");
  if (history.querySelectorAll(".conversation-row").length === 0) {
    history.innerHTML = "";
  }
  history.insertAdjacentHTML("beforeend", conversationRowHtml(row));
  history.scrollTop = history.scrollHeight;
}

async function answerCurrent() {
  if (!state.selectedSession) return;
  state.localConversation = [];
  const answer = ($("answer-input") ? $("answer-input").value : $("run-arguments").value).trim();
  if (!answer) return;
  renderLocalConversationDraft(answer, "你");
  try {
    const activeQuestion = state.activeQuestion || pendingQuestion(state.detail || {});
    const endpoint = activeQuestion && activeQuestion.source === "pending" ? "/api/answer" : "/api/resume";
    const payload = endpoint === "/api/answer"
      ? { session: state.selectedSession, answer, target_workspace: $("target-workspace").value.trim() }
      : { session: state.selectedSession, prompt: answer, target_workspace: $("target-workspace").value.trim() };
    const result = await api(endpoint, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const action = endpoint === "/api/answer" ? "已提交回答" : "已把你的回答发回模型继续";
    $("resume-result").textContent = result.ok ? `${action} job=${result.job_id || result.process_id}` : result.error || "提交回答失败";
    appendRuntimeProcessMessage(result.ok ? `${action}：job=${result.job_id || result.process_id}` : result.error || "提交回答失败");
    if ($("answer-input")) $("answer-input").value = "";
    $("run-arguments").value = "";
    if (result.ok) await selectContinuationSession(result);
  } catch (error) {
    const message = error.message || String(error);
    $("resume-result").textContent = message;
    appendRuntimeProcessMessage(`提交回答失败：${message}`);
  }
  setTimeout(tick, 1200);
}

async function selectContinuationSession(result) {
  const sourceSession = String((result && result.source_session) || state.selectedSession || "");
  const parentSessionId = String((result && result.parent_session_id) || "");
  const continuationWorkspace = String((result && result.continuation_workspace) || "");
  const jobId = String((result && (result.job_id || result.process_id)) || "");
  const targetWorkspace = String((result && result.target_workspace) || "");
  for (let i = 0; i < 16; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 300));
    await loadSessions();
    await loadJobs();
    const target =
      state.sessions.find((session) => session.id && parentSessionId && session.id !== parentSessionId && (
        session.summary?.command === "resume" && session.summary?.arguments === parentSessionId ||
        session.workspace === continuationWorkspace ||
        session.workspace === targetWorkspace
      )) ||
      state.sessions.find((session) => {
        const summary = session.summary || {};
        return summary.command === "resume" && summary.arguments === sourceSession;
      }) ||
      state.sessions.find((session) => targetWorkspace && session.workspace === targetWorkspace && session.id !== sourceSession && ["running", "waiting_user", "done", "answered"].includes(session.status)) ||
      state.sessions.find((session) => jobId && currentJobForSession(session, jobId));
    if (target && target.id) {
      await selectSession(target.id, { view: "chat" });
      return;
    }
  }
  if (sourceSession) await selectSession(sourceSession, { view: "chat" });
}

function currentJobForSession(session, jobId) {
  if (!session || !jobId) return false;
  return state.jobs.some((job) => {
    if (job.id !== jobId) return false;
    const metadata = job.metadata || {};
    return metadata.target_workspace && metadata.target_workspace === session.workspace;
  });
}

async function cancelJob(jobId) {
  if (!jobId) return;
  try {
    await api(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST", body: "{}" });
    appendRuntimeProcessMessage(`已请求停止 job=${jobId}`);
    await loadJobs();
    if (state.activeRunJobId === jobId) state.activeRunJobId = "";
    renderInlineStopButton();
    if (state.selectedSession) await loadDetail();
  } catch (error) {
    appendRuntimeProcessMessage(`停止失败：${error.message || error}`);
  }
}

async function stopSession(sessionId) {
  if (!sessionId) return;
  try {
    const result = await api(`/api/sessions/${encodeURIComponent(sessionId)}/stop`, { method: "POST", body: "{}" });
    appendRuntimeProcessMessage(result.stopped ? `已请求停止任务 ${sessionId}` : `任务 ${sessionId} 当前没有活动进程。`);
    await loadJobs();
    await loadSessions();
    if (state.selectedSession === sessionId) await loadDetail();
  } catch (error) {
    appendRuntimeProcessMessage(`停止任务失败：${error.message || error}`);
  }
}

async function deleteSession(sessionId) {
  if (!sessionId) return;
  const confirmed = window.confirm("删除这个历史任务？任务记录和对应任务文件夹会移动到 runtime 回收区。");
  if (!confirmed) return;
  const result = await api(`/api/sessions/${encodeURIComponent(sessionId)}/delete`, { method: "POST", body: "{}" });
  if (!result.ok) {
    window.alert(result.error || "删除失败");
    return;
  }
  if (state.selectedSession === sessionId) {
    state.selectedSession = null;
    state.detail = null;
    state.selectedNodeId = null;
    renderDetail();
    switchWorkbenchView("status");
  }
  await loadJobs();
  await loadSessions();
}

async function clearDiagnosticsHistory() {
  const confirmed = window.confirm("清空诊断/自测历史？这些记录会移动到 runtime 回收区。");
  if (!confirmed) return;
  const result = await api("/api/diagnostics/clear", { method: "POST", body: "{}" });
  if (!result.ok) {
    window.alert(result.error || "清空诊断历史失败");
    return;
  }
  await loadJobs();
  await loadSessions();
  if (state.selectedSession && !state.sessions.some((session) => session.id === state.selectedSession)) {
    state.selectedSession = null;
    state.detail = null;
    state.selectedNodeId = null;
    renderDetail();
  }
}

async function clearCurrentProjectHistory() {
  const name = state.currentProject && state.currentProject.name ? state.currentProject.name : "当前项目";
  const confirmed = window.confirm(`清空「${name}」的历史任务？任务记录和对应任务文件夹会移动到 runtime 回收区。`);
  if (!confirmed) return;
  const result = await api("/api/history/clear", {
    method: "POST",
    body: JSON.stringify({ project_id: state.currentProjectId, include_diagnostics: state.showDiagnostics }),
  });
  if (!result.ok) {
    window.alert(result.error || "清空历史失败");
    return;
  }
  state.selectedSession = null;
  state.detail = null;
  state.selectedNodeId = null;
  renderDetail();
  await loadJobs();
  await loadSessions();
  switchWorkbenchView("status");
}

async function togglePlugin(name, root, enabled) {
  await api("/api/plugin", {
    method: "POST",
    body: JSON.stringify({ name, root, enabled }),
  });
  await loadSkills();
}

async function startService(serviceId) {
  if (!serviceId) return;
  await api(`/api/services/${encodeURIComponent(serviceId)}/start`, { method: "POST", body: "{}" });
  await loadHealth();
  await loadServices();
}

async function stopService(serviceId) {
  if (!serviceId) return;
  await api(`/api/services/${encodeURIComponent(serviceId)}/stop`, { method: "POST", body: "{}" });
  await loadHealth();
  await loadServices();
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
    if ($("conversation-mode-label")) $("conversation-mode-label").textContent = "新任务";
    $("run-arguments").focus();
  } else {
    $("run-arguments").placeholder = "可选：补充当前任务接下来要做什么。留空则按已有上下文继续。";
    if ($("conversation-mode-label")) $("conversation-mode-label").textContent = "继续任务";
    $("run-arguments").focus();
  }
  updateExecutionLabel();
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
    await loadServices();
    await loadSessions();
    await loadJobs();
    renderInlineStopButton();
    if (state.selectedSession) await loadDetail();
  } catch (error) {
    console.error(error);
  }
}

function bindEvents() {
  document.querySelectorAll(".workbench-tab").forEach((button) => {
    button.addEventListener("click", () => switchWorkbenchView(button.dataset.view || "status"));
  });
  $("refresh-button").addEventListener("click", tick);
  $("history-clear-button").addEventListener("click", () => clearCurrentProjectHistory().catch(console.error));
  $("diagnostics-clear-button").addEventListener("click", () => clearDiagnosticsHistory().catch(console.error));
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
  if ($("answer-button")) $("answer-button").addEventListener("click", answerCurrent);
  $("execution-open-button").addEventListener("click", () => {
    const details = document.querySelector(".execution-options");
    if (details) details.open = true;
    $("skill-select").focus();
  });
  $("current-stop-button").addEventListener("click", () => cancelJob($("current-stop-button").dataset.job));
  $("inline-stop-button").addEventListener("click", () => cancelJob($("inline-stop-button").dataset.job));
  $("memory-save-button").addEventListener("click", saveMemoryFile);
  $("skill-filter").addEventListener("input", renderSkills);
  $("model-refresh-button").addEventListener("click", () => loadModelConfig().catch(console.error));
  $("model-save-button").addEventListener("click", () => saveModelConfig().catch(console.error));
  [
    "model-name",
    "model-review",
    "model-provider",
    "model-base-url",
    "model-wire-api",
    "model-requires-auth",
    "model-codex-oss",
    "model-local-provider",
    "model-reasoning",
    "model-network",
    "model-context-window",
    "model-compact-limit",
    "model-disable-storage",
  ].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener("input", () => markModelInputEdited(id));
    el.addEventListener("change", () => markModelInputEdited(id));
  });
  $("skill-select").addEventListener("change", () => {
    state.selectedSkillGroup = $("skill-select").value || "__all__";
    renderCommandSelect();
    updateExecutionLabel();
    switchWorkbenchView("skills");
    setMode("new");
  });
  $("command-select").addEventListener("change", () => {
    const selected = $("command-select").value;
    chooseSkillCommand(selected);
    if (selected) {
      $("run-arguments").focus();
      switchWorkbenchView("skills");
    }
  });
  $("run-command").addEventListener("input", () => {
    renderCommandDescription($("run-command").value);
    updateExecutionLabel();
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
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      state.mode === "resume" ? resumeCurrent() : startRun();
    }
  });
  $("run-arguments").addEventListener("input", updateExecutionLabel);
}

async function boot() {
  startUiHeartbeat();
  bindEvents();
  setMode("new");
  switchWorkbenchView("status");
  await Promise.allSettled([loadProjects(), loadSkills(), loadCapabilities(), loadServices(), loadMemory(), loadModelConfig()]);
  await tick();
  await selectInitialSessionFromQuery();
  setInterval(tick, 2500);
}

boot().catch(console.error);
