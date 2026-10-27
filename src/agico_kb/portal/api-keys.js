/* Provider settings: masked identity only; full credentials remain server-side. */
(() => {
  "use strict";
  const $ = id => document.getElementById(id), profiles = ["primary", "fallback"];
  const dialog = $("api-keys-dialog"), levels = ["low", "medium", "high"], labels = ["低", "中", "高"];
  const api = (path, options) => window.agicoAccount.request(path, options);
  const state = new Map(), busy = new Set();
  let generation = 0, controller = null;
  const allowed = () => window.agicoAccount.current?.role === "superadmin" && !window.agicoAccount.current?.must_change_password;
  const note = (name, text = "", error = false) => { const el = $("api-key-message-" + name); el.textContent = text; el.classList.toggle("error", error); };
  function selected(name) { return $("api-key-models-" + name).querySelector("input:checked")?.value; }
  function effort(name) { return levels[Number($("api-effort-" + name).value)]; }
  function dirty(name) { const value = state.get(name); return value && (selected(name) !== value.selected_model || effort(name) !== value.reasoning_effort); }
  function controls(name) {
    const value = state.get(name), working = busy.has(name), editing = !$("api-key-form-" + name).hidden;
    $("api-options-" + name).disabled = working || !value?.configured;
    $("api-options-save-" + name).disabled = working || !value?.configured || !dirty(name);
    $("api-key-test-" + name).disabled = working || editing || !value?.configured;
    $("api-key-change-" + name).disabled = working || !value;
    $("api-key-form-" + name).querySelectorAll("button,input").forEach(el => el.disabled = working);
  }
  function closeEditor(name) {
    $("api-key-" + name).value = ""; $("api-key-form-" + name).hidden = true;
    $("api-key-display-" + name).hidden = false;
  }
  function syncEffort(name) {
    const index = Number($("api-effort-" + name).value);
    $("api-effort-value-" + name).textContent = labels[index];
    $("api-effort-" + name).setAttribute("aria-valuetext", labels[index]);
    controls(name);
  }
  function render(name, value, preserveDraft = false) {
    const draft = preserveDraft && dirty(name) ? {model:selected(name), effort:effort(name)} : null;
    state.set(name, value);
    $("api-platform-" + name).textContent = value.platform;
    $("api-key-status-" + name).textContent = value.configured ? "已配置" : "未配置";
    $("api-key-status-" + name).dataset.configured = String(value.configured);
    $("api-key-mask-" + name).textContent = value.masked_key || "未配置密钥";
    $("api-key-change-" + name).textContent = value.configured ? "更换" : "设置密钥";
    const options = $("api-key-models-" + name); options.replaceChildren();
    for (const model of value.models) {
      const label = document.createElement("label"); label.className = "api-model-option";
      const input = document.createElement("input"); input.type = "radio"; input.name = "api-model-" + name; input.value = model;
      input.checked = model === (draft?.model || value.selected_model);
      const text = document.createElement("span"); text.className = "api-model-name"; text.textContent = model;
      label.append(input, text);
      options.append(label);
    }
    $("api-effort-" + name).value = String(Math.max(0, levels.indexOf(draft?.effort || value.reasoning_effort)));
    syncEffort(name);
    $("api-key-route-" + name).textContent = !value.configured ? "先设置密钥，再选择模型。"
      : value.temporary_until ? (value.active_model ? "临时使用 " + value.active_model : "此线路暂不可用，将尝试其他线路")
        + " · " + new Date(value.temporary_until).toLocaleTimeString("zh-CN", {hour12:false}) + " 后重试首选"
      : "设置保存后，后续任务自动使用。";
    controls(name);
  }
  function reset() {
    generation++; controller?.abort(); controller = null; state.clear();
    for (const name of profiles) {
      closeEditor(name); $("api-key-mask-" + name).textContent = "未配置密钥";
      $("api-key-models-" + name).replaceChildren(); $("api-key-status-" + name).textContent = "读取中";
      $("api-key-status-" + name).dataset.configured = "false"; note(name); controls(name);
    }
    $("api-keys-message").textContent = "";
  }
  function visibility() {
    $("manage-api-keys").hidden = !allowed();
    if (!allowed()) { if (dialog.open) dialog.close(); reset(); }
  }
  $("manage-api-keys").addEventListener("click", async () => {
    if (!allowed()) return;
    reset(); dialog.showModal(); const epoch = generation; controller = new AbortController();
    try {
      const data = await api("/v1/settings/api-keys", {signal:controller.signal});
      if (epoch === generation && dialog.open && allowed()) for (const name of profiles) render(name, data.profiles[name]);
    } catch (error) { if (epoch === generation) $("api-keys-message").textContent = error.message; }
  });
  for (const name of profiles) {
    async function action(kind) {
      if (busy.has(name) || !allowed() || !dialog.open) return;
      const key = $("api-key-" + name).value.trim();
      if (kind === "key" && !key) { note(name, "请输入新密钥；留空不会修改当前密钥。", true); return; }
      const epoch = generation; busy.add(name); controls(name);
      note(name, kind === "test" ? "正在测试已保存的配置…" : "正在保存…");
      try {
        const path = `/v1/settings/api-keys/${name}${kind === "test" ? "/test" : kind === "options" ? "/model" : ""}`;
        const options = kind === "test" ? {method:"POST"} : {method:"PUT",body:kind === "key" ? {api_key:key}
          : {model:selected(name), reasoning_effort:effort(name)}};
        const data = await api(path, options);
        if (epoch !== generation || !dialog.open || !allowed()) return;
        if (kind === "test") note(name, data.message, !data.ok);
        else {
          if (kind === "key") closeEditor(name);
          render(name, data.profiles[name], kind === "key");
          note(name, kind === "key" ? "新密钥已加密保存，并替换旧密钥。" : "设置已保存。");
        }
      } catch (error) { if (epoch === generation && dialog.open) note(name, error.message, true); }
      finally { busy.delete(name); if (epoch === generation) controls(name); }
    }
    $("api-key-change-" + name).addEventListener("click", () => {
      if (busy.has(name) || !allowed()) return;
      $("api-key-display-" + name).hidden = true; $("api-key-form-" + name).hidden = false;
      $("api-key-" + name).value = ""; note(name); controls(name); $("api-key-" + name).focus();
    });
    $("api-key-cancel-" + name).addEventListener("click", () => { closeEditor(name); note(name); controls(name); });
    $("api-key-form-" + name).addEventListener("submit", event => { event.preventDefault(); action("key"); });
    $("api-options-form-" + name).addEventListener("submit", event => { event.preventDefault(); action("options"); });
    $("api-key-test-" + name).addEventListener("click", () => action("test"));
    $("api-effort-" + name).addEventListener("input", () => { syncEffort(name); note(name, "更改尚未保存。"); });
    $("api-key-models-" + name).addEventListener("change", () => { controls(name); note(name, "更改尚未保存。"); });
  }
  $("close-api-keys").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", reset);
  window.addEventListener("agico:account-changed", visibility);
  window.addEventListener("agico:account-logout", visibility);
  visibility();
})();
