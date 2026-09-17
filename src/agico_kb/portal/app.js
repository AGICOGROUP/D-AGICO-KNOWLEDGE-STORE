/* Same-origin employee portal. Credentials live only in this page's memory. */
"use strict";
const $ = (id) => document.getElementById(id);
const state = {token: "", catalog: null, limit: 0, file: null, attempt: null, busy: false, tab: "library", offset: 0, next: null, generation: 0, listRequest: 0};
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
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(errorText(body, response.status)); }
  return response.json();
}
function openAuth() { $("auth-dialog").showModal(); $("access-token").focus(); }
function resetFile() {
  state.file = null; state.attempt = null;
  $("reset-attempt").hidden = true;
  $("upload-form").reset(); $("file-label").textContent = "点击选择，或将文件拖到这里";
  $("file-help").textContent = `支持 Office、PDF、图片、CAD 等 · 每次 1 份 · 最大 ${size(state.limit)}`;
  $("division-summary").textContent = "尚未选择事业部";
}
function disconnect() {
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
  $("category").replaceChildren(); $("filter-category").replaceChildren(new Option("全部资料类型", ""));
  for (const c of catalog.categories) { $("category").add(new Option(c.name, c.id)); $("filter-category").add(new Option(c.name, c.id)); }
  $("category").value = "other";
}
$("account-button").addEventListener("click", () => { if (state.token) disconnect(); else openAuth(); });
$("empty-connect").addEventListener("click", openAuth);
$("close-auth").addEventListener("click", () => $("auth-dialog").close());
$("auth-dialog").addEventListener("close", () => { $("access-token").value = ""; });
$("auth-dialog").addEventListener("cancel", (e) => { if ($("login-button").disabled) e.preventDefault(); });
$("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const token = $("access-token").value.trim(); if (!token) return;
  $("login-button").disabled = true; $("close-auth").disabled = true; message("auth-message", "正在验证访问权限…");
  try {
    const [session, catalog] = await Promise.all([api("/v1/portal-session", {}, token), api("/v1/catalog", {}, token)]);
    state.token = token; state.catalog = catalog; state.limit = session.max_upload_bytes; state.generation++;
    catalogUI(catalog); resetFile(); $("category").value = "other";
    $("upload-fields").disabled = !catalog.organizations.length; $("submit-button").disabled = !catalog.organizations.length;
    for (const id of ["search-button", "filter-org", "filter-category", "refresh"]) $(id).disabled = false;
    $("connection").textContent = `已连接 · ${session.identity}`; $("connection").classList.add("online");
    $("account-button").textContent = "断开连接"; $("auth-dialog").close(); message("auth-message");
    await loadList(); $("division-options").querySelector("input")?.focus();
  } catch (error) { message("auth-message", error.message, true); }
  finally { $("login-button").disabled = false; $("close-auth").disabled = false; }
});
function chooseFile(file) {
  if (state.busy || state.attempt || !state.token) return;
  if (!file || !file.size || file.size > state.limit) {
    state.file = null; $("file-input").value = ""; $("file-label").textContent = "点击选择，或将文件拖到这里";
    message("upload-message", `请选择非空文件，大小不超过 ${size(state.limit)}。`, true); return;
  }
  if ([...file.name].length > 240 || /[\\/:\x00-\x1f]/.test(file.name) || [".", ".."].includes(file.name)) {
    state.file = null; $("file-input").value = ""; $("file-label").textContent = "点击选择，或将文件拖到这里";
    message("upload-message", "文件名最多 240 个字符，不能包含路径符号或控制字符。请重命名后选择。", true); return;
  }
  state.file = file; $("title").value = file.name.slice(0, 300);
  $("file-label").textContent = file.name; $("file-help").textContent = `${size(file.size)} · 点击可重新选择`; message("upload-message");
}
$("file-input").addEventListener("change", () => chooseFile($("file-input").files[0]));
for (const ev of ["dragenter", "dragover"]) $("dropzone").addEventListener(ev, (e) => { e.preventDefault(); if (state.token && !state.attempt && !state.busy) $("dropzone").classList.add("dragging"); });
for (const ev of ["dragleave", "drop"]) $("dropzone").addEventListener(ev, (e) => { e.preventDefault(); $("dropzone").classList.remove("dragging"); });
$("dropzone").addEventListener("drop", (e) => {
  if (!state.token || state.busy || state.attempt) return;
  if (e.dataTransfer.files.length !== 1) { message("upload-message", "请每次选择一份文件。", true); return; }
  $("file-input").files = e.dataTransfer.files; chooseFile(e.dataTransfer.files[0]);
});
function putFile(id, file) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(); xhr.open("PUT", `/v1/uploads/${encodeURIComponent(id)}/content`);
    xhr.setRequestHeader("Authorization", `Bearer ${state.token}`); xhr.timeout = 300000;
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) { $("upload-progress").value = Math.round(e.loaded / e.total * 100); message("upload-message", `正在上传 · ${Math.round(e.loaded / e.total * 100)}%`); } };
    xhr.onload = () => { let body = {}; try { body = JSON.parse(xhr.responseText); } catch { /* Non-JSON proxy errors. */ } if (xhr.status >= 200 && xhr.status < 300) resolve(body); else reject(new Error(errorText(body, xhr.status))); };
    xhr.onerror = xhr.ontimeout = () => reject(new Error("上传中断，请点击重试。已填写的归属与文件会保留。")); xhr.send(file);
  });
}
$("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault(); if (state.busy || !state.token) return;
  if (!state.attempt) {
    const org = document.querySelector('input[name="organization"]:checked');
    if (!org) { message("upload-message", "请先选择所属事业部。", true); $("division-options").querySelector("input")?.focus(); return; }
    if (!state.file || !$("title").value.trim()) { message("upload-message", "请选择文件并填写资料名称。", true); return; }
    state.attempt = {key: crypto.randomUUID(), file: state.file, body: {organization_id: org.value, category_id: $("category").value, title: $("title").value.trim(), model: $("model").value.trim() || null, visibility: "department"}};
  }
  const attempt = state.attempt; const generation = state.generation;
  state.busy = true; $("upload-fields").disabled = true; $("submit-button").disabled = true; $("account-button").disabled = true;
  $("upload-progress").hidden = false; $("submit-button").textContent = "正在提交…";
  try {
    if (!attempt.uploadId) { message("upload-message", "正在准备上传…"); const prepared = await api("/v1/uploads", {method: "POST", headers: {"Idempotency-Key": attempt.key}, body: {filename: attempt.file.name, size: attempt.file.size}}); attempt.uploadId = prepared.upload_id; }
    if (!attempt.uploaded) { await putFile(attempt.uploadId, attempt.file); attempt.uploaded = true; }
    message("upload-message", "上传完成，正在登记资料…");
    const result = await api("/v1/submissions", {method: "POST", body: {...attempt.body, upload_id: attempt.uploadId}});
    resetFile(); $("category").value = "other"; $("upload-fields").disabled = false;
    message("upload-message", `已提交至「${orgName(attempt.body.organization_id)}」。\n资料进入待审核区，后台正在处理。`);
    $("submit-button").textContent = "提交到知识库 →";
    switchTab("pending"); pollStatus(result.version_id, generation, attempt.body.organization_id);
  } catch (error) {
    message("upload-message", `${error.message}\n归属和文件已锁定，重试会继续同一份提交。若要重新填写，请先查看待审核列表，确认之前是否已提交成功。`, true);
    $("submit-button").textContent = "重试本次提交 →";
    $("reset-attempt").hidden = false;
  } finally { state.busy = false; $("submit-button").disabled = false; $("account-button").disabled = false; $("upload-progress").hidden = true; }
});
$("reset-attempt").addEventListener("click", () => {
  if (state.busy) return;
  resetFile(); $("category").value = "other"; $("upload-fields").disabled = !state.token;
  $("submit-button").textContent = "提交到知识库 →";
  message("upload-message", "已清空表单。已上传或已提交的资料不会删除，请先检查待审核列表，避免重复提交。");
});
async function pollStatus(id, generation, org) {
  for (let i = 0; i < 20; i++) {
    await new Promise((r) => setTimeout(r, 3000));
    if (generation !== state.generation || state.busy || state.file) return;
    try {
      const item = await api(`/v1/versions/${encodeURIComponent(id)}`);
      if (generation !== state.generation || state.busy || state.file) return;
      message("upload-message", `已提交至「${orgName(org)}」 · ${statuses[item.processing_status] || item.processing_status}。\n审核发布后，其他有权限的同事即可查找。${item.warnings?.length ? "\n" + item.warnings.join("；") : ""}`);
      if (!["queued", "processing"].includes(item.processing_status)) { if (state.tab === "pending") await loadList(); return; }
    } catch { return; }
  }
}
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
