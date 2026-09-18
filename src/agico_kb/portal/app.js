/* Same-origin portal. Validated credentials are remembered in this browser. */
"use strict";
const $ = (id) => document.getElementById(id);
const state = {token: "", catalog: null, limit: 0, files: [], attempt: null, busy: false, tab: "library", offset: 0, next: null, generation: 0, listRequest: 0};
const credentialKey = "agico.portal.accessToken.v1";
let connecting = false;
function savedToken() { try { return localStorage.getItem(credentialKey) || ""; } catch { return ""; } }
function storeToken(token) {
  try { if (token) localStorage.setItem(credentialKey, token); else localStorage.removeItem(credentialKey); return true; }
  catch { return false; }
}
const statuses = {queued: "等待解析", processing: "正在解析", ready: "解析完成", partial: "部分解析 · 请核对原件", stored_only: "仅保存原件", failed: "解析失败 · 原件保留"};
function message(id, text = "", error = false) { $(id).textContent = text; $(id).classList.toggle("error", error); }
function size(bytes) { return bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1048576).toFixed(1)} MB`; }
function orgName(id) { return state.catalog?.organizations.find((o) => o.id === id)?.name || id; }
function categoryName(id) { return state.catalog?.categories.find((c) => c.id === id)?.name || "其他资料"; }
function element(tag, cls, text) { const el = document.createElement(tag); if (cls) el.className = cls; if (text !== undefined) el.textContent = text; return el; }
function errorText(body, status) {
  if (status === 401) return "访问码无效或已过期，请重新连接。";
  if (status === 403) return "当前账号没有这项操作的权限。";
  if (status === 404) return "文件不存在，或你没有访问权限。";
  if (status === 413) return "文件超过允许的大小，请选择较小的文件。";
  if (status === 422) return body?.error?.message || "提交信息不符合要求，请检查文件名和表单内容。";
  return body?.error?.message || `请求未完成（${status}），请稍后重试。`;
}
async function api(path, options = {}, token = state.token) {
  const headers = {Authorization: `Bearer ${token}`, ...options.headers};
  if (options.body && typeof options.body !== "string") { options.body = JSON.stringify(options.body); headers["Content-Type"] = "application/json"; }
  let response;
  try { response = await fetch(path, {...options, headers, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(60000)}); }
  catch { throw new Error("网络连接中断或请求超时，请检查连接后重试。"); }
  if (!response.ok) { const body = await response.json().catch(() => ({})); const error = new Error(errorText(body, response.status)); error.status = response.status; throw error; }
  return response.json();
}
function openAuth() { if (connecting) return; $("auth-dialog").showModal(); $("access-token").focus(); }
function resetFile() {
  state.files = []; state.attempt = null;
  $("reset-attempt").hidden = true;
  $("upload-form").reset(); $("optional-fields").open = false; $("selected-files").replaceChildren(); $("file-label").textContent = "点击选择多个文件，或拖到这里";
  $("file-help").textContent = `支持批量选择或拖入文件 · 单个文件最大 ${size(state.limit)}`;
  $("division-summary").textContent = "尚未选择事业部";
}
function disconnect() {
  window.dispatchEvent(new Event("agico:disconnect"));
  storeToken("");
  $("agent-button").disabled = true; $("agent-dialog").close(); clearAgentOutput();
  state.generation++; state.listRequest++; state.token = ""; state.catalog = null;
  state.offset = 0; state.next = null; resetFile();
  $("upload-fields").disabled = true; $("submit-button").disabled = true;
  for (const id of ["search-button", "filter-org", "filter-category", "refresh", "previous", "next"]) $(id).disabled = true;
  $("division-options").replaceChildren(element("p", "connect-hint", "连接后，显示你可提交资料的事业部。"));
  $("filter-org").replaceChildren(new Option("全部可见事业部", ""));
  $("filter-category").replaceChildren(new Option("全部资料类型", ""));
  $("connection").textContent = "尚未连接"; $("connection").classList.remove("online");
  $("account-button").textContent = "连接知识库 ↗";
  $("query").value = ""; message("upload-message"); message("list-message");
  empty("已断开连接", "重新输入访问码，即可继续使用。");
}
function catalogUI(catalog) {
  $("division-options").replaceChildren();
  $("filter-org").replaceChildren(new Option("全部可见事业部", ""));
  for (const org of catalog.organizations) {
    const label = element("label", "division-option");
    const radio = element("input"); radio.type = "radio"; radio.name = "organization"; radio.value = org.id; radio.required = true;
    radio.addEventListener("change", () => { $("division-summary").textContent = `将提交至：${org.name}`; });
    label.append(radio, element("span", "", org.name)); $("division-options").append(label);
    $("filter-org").add(new Option(org.name, org.id));
  }
  if (!catalog.organizations.length) $("division-options").append(element("p", "connect-hint", "当前账号没有事业部权限，请联系管理员。"));
  $("category").replaceChildren(new Option("不填写", "")); $("filter-category").replaceChildren(new Option("全部资料类型", ""));
  for (const c of catalog.categories) { $("category").add(new Option(c.name, c.id)); $("filter-category").add(new Option(c.name, c.id)); }
  $("category").value = "";
}
$("account-button").addEventListener("click", () => { if (state.token) disconnect(); else openAuth(); });
$("empty-connect").addEventListener("click", openAuth);
$("close-auth").addEventListener("click", () => $("auth-dialog").close());
$("auth-dialog").addEventListener("close", () => { $("access-token").value = ""; });
$("auth-dialog").addEventListener("cancel", (e) => { if ($("login-button").disabled) e.preventDefault(); });
async function connect(token, automatic = false) {
  if (connecting) return;
  connecting = true;
  $("login-button").disabled = true; $("close-auth").disabled = true; $("account-button").disabled = true;
  if (automatic) $("connection").textContent = "正在自动连接…";
  else message("auth-message", "正在验证访问权限…");
  try {
    const [session, catalog] = await Promise.all([api("/v1/portal-session", {}, token), api("/v1/catalog", {}, token)]);
    $("agent-button").disabled = false;
    state.token = token; state.catalog = catalog; state.limit = session.max_upload_bytes; state.generation++;
    const remembered = storeToken(token);
    catalogUI(catalog); resetFile(); $("category").value = "";
    $("upload-fields").disabled = !catalog.organizations.length; $("submit-button").disabled = !catalog.organizations.length;
    for (const id of ["search-button", "filter-org", "filter-category", "refresh"]) $(id).disabled = false;
    $("connection").textContent = `已连接 · ${session.identity}`; $("connection").classList.add("online");
    $("account-button").textContent = "断开连接"; $("auth-dialog").close(); message("auth-message");
    await loadList();
    if (!remembered) message("list-message", "已连接，但浏览器不允许保存访问码，关闭页面后需要重新输入。", true);
    if (!automatic) $("file-input").focus();
  } catch (error) {
    if (automatic) {
      $("connection").textContent = "尚未连接";
      if (error.status === 401) {
        storeToken("");
        message("list-message", "保存的访问码已失效，请点击连接知识库，重新填写。", true);
      } else {
        message("list-message", "暂时无法自动连接，已保留访问码。请在服务恢复后刷新页面重试。", true);
      }
    } else message("auth-message", error.message, true);
  } finally {
    connecting = false; $("login-button").disabled = false; $("close-auth").disabled = false; $("account-button").disabled = false;
  }
}
$("auth-form").addEventListener("submit", (event) => {
  event.preventDefault(); const token = $("access-token").value.trim(); if (token) connect(token);
});
function renderFiles(items = null) {
  const entries = items || state.attempt?.items || state.files.map((file) => ({file}));
  $("selected-files").replaceChildren();
  for (const [index, item] of entries.entries()) {
    const row = element("div", "selected-file");
    const info = element("div", "selected-file-info");
    info.append(element("strong", "", item.file.name));
    const detail = item.versionId ? "已提交 · 待审核" : item.error ? item.error : item.status || size(item.file.size);
    info.append(element("span", item.error ? "file-error" : "", detail)); row.append(info);
    if (!state.attempt && !items) {
      const remove = element("button", "text-button", "移除"); remove.type = "button";
      remove.setAttribute("aria-label", `移除 ${item.file.name}`);
      remove.addEventListener("click", () => { state.files.splice(index, 1); renderFiles(); }); row.append(remove);
    }
    $("selected-files").append(row);
  }
  $("file-label").textContent = state.files.length ? `已选择 ${state.files.length} 份文件 · 点击继续添加` : "点击选择多个文件，或拖到这里";
}
function chooseFiles(files) {
  if (state.busy || state.attempt || !state.token) return;
  const rejected = [];
  for (const file of files) {
    if (!file.size || file.size > state.limit) { rejected.push(`${file.name}：文件不能为空或超过 ${size(state.limit)}`); continue; }
    if ([...file.name].length > 240 || /[\\/:\x00-\x1f]/.test(file.name) || [".", ".."].includes(file.name)) {
      rejected.push(`${file.name}：文件名最多 240 个字符，不能包含路径符号或控制字符`); continue;
    }
    // Files from different folders may share names, sizes and timestamps.
    state.files.push(file);
  }
  $("file-input").value = ""; renderFiles();
  message("upload-message", rejected.length ? rejected.join("\n") : "", !!rejected.length);
}
$("file-input").addEventListener("change", () => chooseFiles(Array.from($("file-input").files)));
for (const ev of ["dragenter", "dragover"]) $("dropzone").addEventListener(ev, (e) => { e.preventDefault(); if (state.token && !state.attempt && !state.busy) $("dropzone").classList.add("dragging"); });
for (const ev of ["dragleave", "drop"]) $("dropzone").addEventListener(ev, (e) => { e.preventDefault(); $("dropzone").classList.remove("dragging"); });
$("dropzone").addEventListener("drop", (e) => chooseFiles(Array.from(e.dataTransfer.files)));
function putFile(id, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(); xhr.open("PUT", `/v1/uploads/${encodeURIComponent(id)}/content`);
    xhr.setRequestHeader("Authorization", `Bearer ${state.token}`); xhr.timeout = 300000;
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => { let body = {}; try { body = JSON.parse(xhr.responseText); } catch { /* Non-JSON proxy errors. */ } if (xhr.status >= 200 && xhr.status < 300) resolve(body); else reject(new Error(errorText(body, xhr.status))); };
    xhr.onerror = xhr.ontimeout = () => reject(new Error("上传中断，可重试此文件。")); xhr.send(file);
  });
}
$("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault(); if (state.busy || !state.token) return;
  if (!state.attempt) {
    if (!state.files.length) { message("upload-message", "请先选择要上传的文件。", true); $("file-input").focus(); return; }
    const org = document.querySelector('input[name="organization"]:checked');
    if (!org) { message("upload-message", "提交前请选择所属事业部。", true); $("division-options").querySelector("input")?.focus(); return; }
    state.attempt = {
      items: state.files.map((file) => ({key: crypto.randomUUID(), file})),
      body: {organization_id: org.value, ...($("category").value ? {category_id: $("category").value} : {}), ...($("model").value.trim() ? {model: $("model").value.trim()} : {}), visibility: "department"}
    };
  }
  const attempt = state.attempt;
  state.busy = true; $("upload-fields").disabled = true; $("submit-button").disabled = true; $("account-button").disabled = true; $("reset-attempt").hidden = true;
  $("upload-progress").hidden = false; $("upload-progress").value = 0; $("submit-button").textContent = "正在批量提交…";
  let processed = attempt.items.filter((item) => item.versionId).length;
  try {
    for (const item of attempt.items) {
      if (item.versionId) continue;
      item.error = ""; item.status = "正在上传…"; renderFiles();
      message("upload-message", `正在提交 ${processed + 1} / ${attempt.items.length}：${item.file.name}`);
      try {
        if (!item.uploadId) {
          const prepared = await api("/v1/uploads", {method: "POST", headers: {"Idempotency-Key": item.key}, body: {filename: item.file.name, size: item.file.size}});
          item.uploadId = prepared.upload_id;
        }
        if (!item.uploaded) {
          await putFile(item.uploadId, item.file, (fraction) => { $("upload-progress").value = (processed + fraction) / attempt.items.length * 100; });
          item.uploaded = true;
        }
        const result = await api("/v1/submissions", {method: "POST", body: {...attempt.body, title: item.file.name, upload_id: item.uploadId}});
        item.versionId = result.version_id;
      } catch (error) { item.error = error.message; }
      processed++; $("upload-progress").value = processed / attempt.items.length * 100; renderFiles();
    }
    const completed = attempt.items.filter((item) => item.versionId).length;
    const failed = attempt.items.length - completed;
    if (!failed) {
      resetFile(); $("category").value = ""; $("upload-fields").disabled = false; renderFiles(attempt.items);
      message("upload-message", `${completed} 份文件已提交至「${orgName(attempt.body.organization_id)}」。后台正在处理，审核发布后可供查找。`);
      $("submit-button").textContent = "提交到知识库 →";
    } else {
      message("upload-message", `已提交 ${completed} 份，${failed} 份未确认完成。重试只处理未完成项，已成功的文件不会重复提交。\n若要重新选择，请先检查待审核列表，避免重复提交。`, true);
      $("submit-button").textContent = `重试未完成的 ${failed} 份 →`; $("reset-attempt").hidden = false;
    }
    switchTab("pending");
  } finally { state.busy = false; $("submit-button").disabled = false; $("account-button").disabled = false; $("upload-progress").hidden = true; }
});
$("reset-attempt").addEventListener("click", () => {
  if (state.busy) return;
  resetFile(); $("category").value = ""; $("upload-fields").disabled = !state.token;
  $("submit-button").textContent = "提交到知识库 →";
  message("upload-message", "可以重新选择文件。服务器上已有的资料不会删除，请先检查待审核列表，避免重复提交。");
});
function empty(title, detail) { const box = element("div", "empty"); box.append(element("span", "empty-art", "▤"), element("h3", "", title), element("p", "", detail)); $("results").replaceChildren(box); }
async function download(item, button) {
  button.disabled = true; const generation = state.generation;
  try {
    const info = await api(`/v1/versions/${encodeURIComponent(item.version_id)}`);
    const response = await fetch(`/v1/versions/${encodeURIComponent(item.version_id)}/content`, {headers: {Authorization: `Bearer ${state.token}`}, redirect: "error", cache: "no-store", signal: AbortSignal.timeout(300000)});
    if (!response.ok) throw new Error(errorText({}, response.status));
    const blob = await response.blob(); if (generation !== state.generation) return;
    if (blob.size !== info.size) throw new Error("下载文件不完整，请重试。");
    const url = URL.createObjectURL(blob); const a = element("a"); a.href = url; a.download = info.filename; a.click(); setTimeout(() => URL.revokeObjectURL(url), 30000);
    message("list-message", "已开始保存原文件。");
  } catch (error) { if (generation === state.generation) message("list-message", error.message || "下载失败，请重试。", true); }
  finally { button.disabled = false; }
}
function rows(items) {
  $("results").replaceChildren();
  for (const item of items) {
    const row = element("article", "file-row"); const info = element("div", "file-info");
    const ext = (item.filename || item.title).split(".").pop().slice(0, 5).toUpperCase();
    info.append(element("h3", "file-name", item.title), element("p", "file-meta", `${orgName(item.organization_id)}${item.category_id ? " / " + categoryName(item.category_id) : ""}${item.size ? " · " + size(item.size) : ""}`));
    const status = element("span", "status-tag", `${state.tab === "pending" ? "待审核 · " : ""}${statuses[item.processing_status] || "已发布"}`);
    status.classList.toggle("warning", ["partial", "failed", "stored_only"].includes(item.processing_status)); info.append(status);
    const button = element("button", "text-button", "下载 ↓"); button.type = "button"; button.setAttribute("aria-label", `下载 ${item.title}`); button.addEventListener("click", () => download(item, button));
    row.append(element("span", "file-badge", ext || "FILE"), info, button); $("results").append(row);
  }
}
async function loadList() {
  if (!state.token) return;
  const request = ++state.listRequest; const generation = state.generation; message("list-message");
  $("results").setAttribute("aria-busy", "true"); $("previous").disabled = $("next").disabled = true;
  try {
    const pending = state.tab === "pending";
    const data = pending ? await api(`/v1/submissions?limit=20&offset=${state.offset}`) : await api("/v1/search", {method: "POST", body: {mode: "files", query: $("query").value.trim(), organization_id: $("filter-org").value || null, category_id: $("filter-category").value || null, limit: 20, offset: state.offset}});
    if (request !== state.listRequest || generation !== state.generation) return;
    state.next = data.has_more ? data.next_offset : null;
    if (data.items.length) rows(data.items); else empty(pending ? "暂无待审核资料" : "没有找到符合条件的资料", pending ? "上传完成后，可在这里查看处理状态。" : "试试其他关键词或筛选条件；仅显示已发布且有权访问的资料。");
    $("list-caption").textContent = `${pending ? "有权查看的待审核提交" : "已发布 · 当前页"} · ${data.items.length} 份`;
    $("page-label").textContent = `第 ${Math.floor(state.offset / 20) + 1} 页`;
    $("previous").disabled = state.offset === 0; $("next").disabled = state.next === null || (!pending && state.next > 1000);
    if (data.warnings?.length) message("list-message", data.warnings.join("；"));
  } catch (error) { if (request === state.listRequest && generation === state.generation) { empty("暂时无法获取资料", "请检查连接后点击刷新。"); message("list-message", error.message, true); } }
  finally { if (request === state.listRequest) $("results").setAttribute("aria-busy", "false"); }
}
function switchTab(tab) {
  state.tab = tab; state.offset = 0;
  for (const name of ["library", "pending"]) { $("tab-" + name).setAttribute("aria-selected", String(tab === name)); $("tab-" + name).tabIndex = tab === name ? 0 : -1; }
  $("list-panel").setAttribute("aria-labelledby", "tab-" + tab); $("search-form").hidden = tab === "pending"; loadList();
}
for (const tab of ["library", "pending"]) {
  $("tab-" + tab).addEventListener("click", () => switchTab(tab));
  $("tab-" + tab).addEventListener("keydown", (e) => { if (["ArrowLeft", "ArrowRight"].includes(e.key)) { e.preventDefault(); const next = tab === "library" ? "pending" : "library"; switchTab(next); $("tab-" + next).focus(); } });
}
$("search-form").addEventListener("submit", (e) => { e.preventDefault(); state.offset = 0; loadList(); });
for (const id of ["filter-org", "filter-category"]) $(id).addEventListener("change", () => { state.offset = 0; loadList(); });
$("refresh").addEventListener("click", loadList);
$("previous").addEventListener("click", () => { state.offset = Math.max(0, state.offset - 20); loadList(); });
$("next").addEventListener("click", () => { if (state.next !== null) { state.offset = state.next; loadList(); } });
window.addEventListener("beforeunload", (e) => { if (state.busy || state.attempt) { e.preventDefault(); e.returnValue = ""; } });

function clearAgentOutput() {
  $("agent-manual").value = ""; $("agent-manual").hidden = true; $("agent-manual-label").hidden = true; message("agent-message");
}
function agentConfig() {
  const url = new URL("/mcp", location.origin).href;
  const authorization = `Bearer ${state.token}`;
  if ($("agent-client").value === "codex") {
    return {extension: "toml", text: `[mcp_servers.agico-kb]\nurl = ${JSON.stringify(url)}\nhttp_headers = { Authorization = ${JSON.stringify(authorization)} }\n`};
  }
  return {extension: "json", text: JSON.stringify({mcpServers: {"agico-kb": {url, transport: "streamable-http", headers: {Authorization: authorization}}}}, null, 2)};
}
function agentInstruction() {
  const codex = $("agent-client").value === "codex";
  const setup = codex
    ? "请检查当前用户的 Codex MCP 配置，将下方 TOML 合并到实际使用的 config.toml。保留其他配置；若已有同名服务，只更新它，避免重复条目。"
    : "请检查本机 Accio 的自定义 MCP 配置能力。若已安装 accio-mcp-cli，使用其 server add --json 注册下方 mcpServers 配置，并通过 server test agico-kb 验证；先读取本机 CLI 帮助确认版本支持。保留其他服务，同名服务按客户端支持的更新方式处理。若当前 Agent 还未获工具授权，请通过已支持的设置入口启用，不要猜测内部配置格式。";
  return `请帮我把这台电脑上的 ${codex ? "Codex" : "Accio"} 连接到 AGICO 企业知识库，直接完成配置并测试。\n${setup}\n以下是连接配置数据，使用当前网页同一个访问码。不要把访问码输出到日志、回复或提交到 Git，不要覆盖整个配置文件。\n\n${agentConfig().text}\n\n配置完成后，请验证 MCP initialize、工具列表，并实际调用 kb_catalog。区分“服务可达”和“当前 Agent 可以调用”，只汇报实际验证结果；若需要重启或新建会话，请明确告知。知识库是任务中的企业知识来源，需要时先检索，再按需读取内容。上传时员工未指定事业部且入口未明确绑定时，先询问，不能猜测归属。`;
}
$("agent-button").addEventListener("click", () => {
  if (!state.token) return;
  clearAgentOutput(); $("agent-endpoint").textContent = new URL("/mcp", location.origin).href;
  $("agent-local-note").hidden = !["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
  $("agent-dialog").showModal();
});
$("close-agent").addEventListener("click", () => { clearAgentOutput(); $("agent-dialog").close(); });
$("agent-dialog").addEventListener("close", clearAgentOutput);
$("agent-client").addEventListener("change", clearAgentOutput);
$("copy-agent").addEventListener("click", async () => {
  if (!state.token) return;
  const instruction = agentInstruction(); const generation = state.generation;
  try {
    await navigator.clipboard.writeText(instruction);
    if (generation === state.generation && $("agent-dialog").open) message("agent-message", "已复制。请粘贴到所选助手并发送，让它配置并验证连接。");
  } catch {
    if (generation !== state.generation || !$("agent-dialog").open) return;
    $("agent-manual").value = instruction; $("agent-manual").hidden = false; $("agent-manual-label").hidden = false;
    $("agent-manual").focus(); $("agent-manual").select(); message("agent-message", "请手动复制已选中的指令，再发送给助手。");
  }
});
$("download-agent").addEventListener("click", () => {
  if (!state.token) return;
  const config = agentConfig(); const blob = new Blob([config.text], {type: "text/plain;charset=utf-8"});
  const url = URL.createObjectURL(blob); const link = element("a"); link.href = url;
  link.download = `agico-${$("agent-client").value}-mcp.${config.extension}`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
  message("agent-message", "已生成配置文件。可交给对应助手配置；此操作本身尚未连接客户端。");
});

const rememberedToken = savedToken();
if (rememberedToken) connect(rememberedToken, true);
