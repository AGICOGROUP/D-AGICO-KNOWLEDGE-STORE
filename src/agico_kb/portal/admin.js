/* Separate administrator authentication; employee credentials never grant administration. */
(() => {
  "use strict";
  const el = (id) => document.getElementById(id);
  const key = "agico.portal.adminToken.v1";
  let token = "", busy = false, generation = 0, offset = 0, hasMore = false, requestId = null, issued = null, listSequence = 0;
  const note = (text = "", error = false) => { el("admin-message").textContent = text; el("admin-message").classList.toggle("error", error); };
  const textNode = (tag, text, cls = "") => { const node = document.createElement(tag); node.textContent = text; node.className = cls; return node; };
  const save = (value) => { try { if(value) localStorage.setItem(key,value); else localStorage.removeItem(key); return true; } catch { return false; } };
  const remembered = () => { try { return localStorage.getItem(key) || ""; } catch { return ""; } };
  const date = (value) => value ? new Date(value).toLocaleString("zh-CN", {hour12:false}) : "未设置";
  function lock(value) {
    busy = value;
    for (const id of ["admin-login-button","close-admin","admin-logout","admin-create-fields","admin-refresh","admin-prev","admin-next"]) el(id).disabled = value;
    if (!value) { el("admin-prev").disabled = offset===0; el("admin-next").disabled = !hasMore; }
    for (const button of el("admin-access-list").querySelectorAll("button")) button.disabled = value;
  }
  function clearIssued() { issued = null; el("admin-share").value = ""; el("admin-issued").hidden = true; }
  function reset() {
    token = ""; generation++; listSequence++; offset = 0; hasMore = false; requestId = null;
    clearIssued(); el("admin-token").value = ""; el("admin-access-list").replaceChildren(); el("admin-org-options").replaceChildren();
    el("admin-create-form").reset(); el("admin-workspace").hidden = true; el("admin-login").hidden = false;
  }
  async function api(path, options = {}, credential = token) {
    let response;
    try { response = await fetch(path, {...options, headers:{Authorization:`Bearer ${credential}`, ...(options.body ? {"Content-Type":"application/json"}: {})}, cache:"no-store", redirect:"error", signal:AbortSignal.timeout(30000)}); }
    catch { throw new Error("网络中断或超时。生成结果可能已保存，请先刷新列表；重试原表单不会重复签发。"); }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(response.status===401 ? "管理员访问码无效或已过期，请重新登录。" : body.error?.message || "操作未完成，请检查输入或稍后重试。");
      error.status = response.status;
      if ((response.status===401 || response.status===403) && credential===token) { save(""); reset(); }
      throw error;
    }
    return body;
  }
  async function login(credential) {
    lock(true);note("正在验证管理员权限…");
    try {
      const session = await api("/v1/admin/session", {}, credential);
      token = credential; generation++; const saved = save(token);
      el("admin-token").value = ""; el("admin-identity").textContent = `管理员：${session.identity}`;
      el("admin-login").hidden = true; el("admin-workspace").hidden = false;
      el("admin-org-options").replaceChildren();
      for (const org of session.organizations) {
        const label = textNode("label", "", "division-option"); const input = document.createElement("input");
        input.type="checkbox";input.name="access-org";input.value=org.id;input.dataset.name=org.name;
        label.append(input,textNode("span",org.name));el("admin-org-options").append(label);
      }
      note(saved ? "" : "浏览器禁止保存管理员访问码，关闭后需要重新填写。"); await loadList();
    } catch (error) { if ([401,403].includes(error.status)) save(""); note(error.message,true); }
    finally { lock(false); }
  }
  async function loadList() {
    const ticket=++listSequence, authGeneration=generation;
    const result=await api(`/v1/admin/access?limit=20&offset=${offset}`);
    if(ticket!==listSequence || authGeneration!==generation) return;
    hasMore=result.has_more;el("admin-access-list").replaceChildren();
    if(!result.items.length) el("admin-access-list").append(textNode("p","暂无访问码，先在左侧新建。","help"));
    for(const item of result.items) {
      const row=textNode("article","","access-row");row.append(textNode("h4",item.name));
      row.append(textNode("p",(item.organizations || []).map(o=>o.name).join("、")));
      row.append(textNode("p",`创建：${date(item.created_at)}\n到期：${date(item.expires_at)}`));
      row.append(textNode("span",{active:"有效",expired:"已过期",revoked:"已停用"}[item.status],"status-tag"));
      if(item.status!=="revoked") {
        const button=textNode("button","停用","text-button");button.type="button";button.disabled=busy;
        button.addEventListener("click",async()=>{
          if(busy || !confirm(`停用「${item.name}」？所有使用此访问码的人都将失去访问权限。`)) return;
          lock(true);note("");
          try { await api(`/v1/admin/access/${encodeURIComponent(item.id)}/revoke`,{method:"POST"});if(issued?.id===item.id)clearIssued();await loadList();note("已停用，此访问码不能继续访问知识库。"); }
          catch(error){note(error.message,true);} finally{lock(false);}
        });row.append(button);
      }
      el("admin-access-list").append(row);
    }
    el("admin-page").textContent=`第 ${Math.floor(offset/20)+1} 页`;
    el("admin-prev").disabled=busy || offset===0;el("admin-next").disabled=busy || !hasMore;
  }
  function sharing() {
    if(!issued)return;
    try {
      const url=new URL(el("share-address").value.trim());
      if(!["http:","https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname!=="/") throw new Error();
      el("admin-share").value=`企业知识库：${url.origin}/\n访问码：${issued.token}\n用途：${issued.name}\n可访问：${issued.orgNames.join("、")}\n有效至：${date(issued.expires_at)}\n\n打开网页输入访问码即可使用。需要连接 Accio 或 Codex 时，点击“连接 AI 助手”，使用同一访问码。`;
      const local=["localhost","127.0.0.1","[::1]"].includes(url.hostname);
      el("share-address-note").textContent=local ? "当前地址仅限本机。跨城市测试请在公网接通后填写 https://kb.agicogroup.com/。" : "请确认同事能够访问该地址；此处填写地址不会自动配置域名或公网入口。";
      el("admin-copy").disabled=false;
    } catch {el("admin-share").value="";el("admin-copy").disabled=true;el("share-address-note").textContent="请输入完整的网页根地址，例如 https://kb.agicogroup.com/，不要包含访问码或其他参数。";}
  }
  el("manage-access").addEventListener("click",()=>{
    reset();note("");el("admin-dialog").showModal();const value=remembered();if(value)login(value);else el("admin-token").focus();
  });
  el("close-admin").addEventListener("click",()=>{if(!busy){reset();el("admin-dialog").close();}});
  el("admin-dialog").addEventListener("cancel",e=>{if(busy)e.preventDefault();});
  el("admin-dialog").addEventListener("close",reset);
  el("admin-logout").addEventListener("click",()=>{if(!busy){save("");reset();note("已退出管理并清除本浏览器的管理员访问码。");}});
  el("admin-login").addEventListener("submit",e=>{e.preventDefault();if(!busy)login(el("admin-token").value.trim());});
  el("admin-create-form").addEventListener("submit",async e=>{
    e.preventDefault();if(busy || !token)return;
    const selected=[...el("admin-org-options").querySelectorAll("input:checked")];
    if(!selected.length){note("请至少选择一个事业部。",true);return;}
    const name=el("access-name").value.trim();if(!name){note("请填写用途或团队名称。",true);return;}
    requestId ||= crypto.randomUUID();
    const body={request_id:requestId,name,organizations:selected.map(i=>i.value),days:Number(el("access-days").value)};
    lock(true);clearIssued();note("正在生成…");
    try {
      const result=await api("/v1/admin/access",{method:"POST",body:JSON.stringify(body)});
      issued={...result,name,orgNames:selected.map(i=>i.dataset.name)};requestId=null;
      el("admin-issued").hidden=false;el("share-address").value=location.origin+"/";sharing();
      el("admin-create-form").reset();offset=0;
      note("已生成。请复制分享说明，原访问码仅本次显示。");
      try{await loadList();}catch(error){note(`访问码已生成，请先保存。列表刷新失败：${error.message}`,true);}
    } catch(error){
      note(error.message,true);if(error.status===409)requestId=null;
      if(token)try{await loadList();}catch{/* Keep the original creation error. */}
    } finally{lock(false);}
  });
  for(const [id,change] of [["admin-refresh",0],["admin-prev",-20],["admin-next",20]])el(id).addEventListener("click",async()=>{
    if(busy)return;offset=Math.max(0,offset+change);lock(true);note("");try{await loadList();}catch(error){note(error.message,true);}finally{lock(false);}
  });
  el("share-address").addEventListener("input",sharing);
  el("admin-copy").addEventListener("click",async()=>{
    if(!issued)return;
    try{await navigator.clipboard.writeText(el("admin-share").value);note("已复制，可以发给同事了。");}
    catch{el("admin-share").focus();el("admin-share").select();note("浏览器不支持自动复制，已选中分享说明，请手动复制。");}
  });
})();
