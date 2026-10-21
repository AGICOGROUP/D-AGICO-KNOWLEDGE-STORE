/* Server-enforced account hierarchy; invitations contain no user passwords. */
(() => {
  "use strict";
  const $=id=>document.getElementById(id);
  const roles={superadmin:"开发人员",manager:"管理员",user:"普通用户"};
  const statuses={invited:"等待设置密码",active:"已启用",disabled:"已停用"};
  let catalog=null,offset=0,hasMore=false,busy=false,issued=null,editing=null,generation=0;
  const node=(tag,text,cls="")=>{const value=document.createElement(tag);value.textContent=text;value.className=cls;return value;};
  const note=(text="",error=false)=>{$("admin-message").textContent=text;$("admin-message").classList.toggle("error",error);};
  const api=(path,options)=>window.agicoAccount.request(path,options);
  const defaults=()=>new Set(catalog?.default_visible_organizations||["general-group","company-common"]);
  const ding=()=>window.agicoAccount.current?.auth_mode==="dingtalk";
  function lock(value){busy=value;$("account-fields").disabled=value;for(const id of ["account-refresh","account-prev","account-next"])$(id).disabled=value;
    for(const button of $("account-list").querySelectorAll("button"))button.disabled=value;
    if(!value){$("account-prev").disabled=offset===0;$("account-next").disabled=!hasMore;}}
  function selected(){return [...$("account-org-options").querySelectorAll("input:checked")].map(value=>value.value);}
  function scope(){const all=$("account-role").value==="superadmin";$("account-orgs").hidden=all;
    $("account-scope-note").textContent=all?"开发人员可管理全部事业部及所有账号。":window.agicoAccount.current?.role==="manager"?"通用国际集团、公共知识库全员默认可见；只能登记和管理自己事业部内的子用户。":"通用国际集团、公共知识库全员默认可见；其他事业部请明确选择。";}
  function resetForm(){editing=null;$("account-invite-form").reset();$("account-email").readOnly=false;
    $("account-form-title").textContent="登记并邀请";$("account-submit").textContent="登记并生成邀请链接";$("account-cancel-edit").hidden=true;
    $("account-fields").hidden=ding();if(ding())$("account-form-title").textContent="员工由钉钉管理；请在右侧选择账号编辑资料权限。";
    const manager=window.agicoAccount.current?.role==="manager";
    $("account-role").replaceChildren();
    for(const value of manager?["user","manager"]:["user","manager","superadmin"]){const option=node("option",roles[value]);option.value=value;$("account-role").append(option);}
    $("account-role").value="user";
    const specific=catalog?.organizations.filter(org=>!defaults().has(org.id))||[];
    for(const box of $("account-org-options").querySelectorAll("input"))box.checked=defaults().has(box.value)||(manager&&specific.length===1);
    scope();}
  function clearIssued(){issued=null;$("account-issued").hidden=true;$("account-share-link").value="";}
  function sharing(){
    if(!issued)return;
    try{
      const base=new URL($("account-share-base").value);
      if(!["http:","https:"].includes(base.protocol)||base.username||base.password)throw new Error();
      base.pathname="/";base.search="";base.hash="invite="+encodeURIComponent(issued.invitation);
      $("account-share-link").value=base.href;$("account-copy-invite").disabled=false;
      $("account-share-note").textContent=["127.0.0.1","localhost","[::1]"].includes(base.hostname)?"这是本机地址，只有这台电脑能打开。邀请其他同事时，请填写他们可访问的知识库地址。":"链接绑定对方邮箱，仅供该受邀者设置密码；有效期内只能使用一次。";
    }catch{$("account-share-link").value="";$("account-copy-invite").disabled=true;$("account-share-note").textContent="请填写完整的知识库访问地址。";}
  }
  async function showIssued(value){
    issued=value;let base=location.origin;
    try{base=(await api("/v1/accounts/entrypoint")).url;}catch{}
    $("account-share-base").value=base;$("account-expiry").textContent=`受邀邮箱：${value.email} · 有效至 ${new Date(value.expires_at).toLocaleString("zh-CN",{hour12:false})}`;
    $("account-issued").hidden=false;sharing();
  }
  async function loadList(){
    const current=generation;
    const data=await api(`/v1/accounts?tree=true&limit=20&offset=${offset}`);if(current!==generation)return;
    hasMore=data.has_more;$("account-list").replaceChildren();
    if(!data.items.length)$("account-list").append(node("p","还没有登记其他账号。","help"));
    function renderAccount(account){
      const row=node("article","","account-row");row.dataset.accountId=account.principal_id;
      row.append(node("h4",account.display_name||account.email),node("p",`${roles[account.role]} · ${statuses[account.status]}`),node("p",account.organizations.map(org=>org.name).join("、")));
      if(account.role==="superadmin")row.append(node("p","最高管理层 · 开发人员并列","account-owner"));
      else if(account.creator_email)row.append(node("p",`登记人：${account.creator_email}`,"account-owner"));
      else row.append(node("p","历史账号 · 未记录登记人","account-owner"));
      const actions=node("div","","account-action-row");
      function action(label,handler){const button=node("button",label,"text-button");button.type="button";button.addEventListener("click",handler);actions.append(button);return button;}
      if(!ding()&&account.can_manage&&account.status!=="disabled")action(account.status==="invited"?"重发邀请":"生成密码重置链接",async()=>{
        if(busy)return;lock(true);note("");
        try{await showIssued(await api(`/v1/accounts/${encodeURIComponent(account.principal_id)}/invitation`,{method:"POST"}));note("新链接已生成，之前未使用的链接已撤销。");}catch(error){note(error.message,true);}finally{lock(false);}
      });
      if(account.can_manage)action("编辑权限",()=>{
        if(busy)return;editing=account;$("account-fields").hidden=false;$("account-form-title").textContent="编辑账号权限";$("account-email").value=account.email;$("account-email").readOnly=true;
        $("account-role").value=account.role;for(const box of $("account-org-options").querySelectorAll("input"))box.checked=defaults().has(box.value)||account.organizations.some(org=>org.id===box.value);
        $("account-submit").textContent="保存权限";$("account-cancel-edit").hidden=false;scope();$("account-role").focus();
      });
      if(account.can_manage)action(account.status==="disabled"?"启用":"停用",async()=>{
        if(busy)return;lock(true);note("");
        try{await api(`/v1/accounts/${encodeURIComponent(account.principal_id)}`,{method:"PATCH",body:{role:account.role,organizations:account.organizations.map(org=>org.id),active:account.status==="disabled"}});await loadList();note(account.status==="disabled"?"账号已启用。":"账号已停用，其网页登录和 AI 授权均已撤销。");}catch(error){note(error.message,true);}finally{lock(false);}
      });
      row.append(actions);
      if(account.children?.length){
        const branch=node("details","","account-children");branch.open=account.role!=="superadmin";
        branch.append(node("summary",`子账号（${account.children.length}）`));
        const children=node("div","","account-child-list");
        for(const child of account.children)children.append(renderAccount(child));
        branch.append(children);row.append(branch);
      }else if(account.role!=="user")row.append(node("p","暂无子账号","account-owner"));
      return row;
    }
    for(const account of data.items)$("account-list").append(renderAccount(account));
    $("account-page").textContent=`第 ${Math.floor(offset/20)+1} 页 · 共 ${data.total_accounts} 个账号`;
  }
  $("manage-access").addEventListener("click",async()=>{
    const user=window.agicoAccount.current;if(!user||!["superadmin","manager"].includes(user.role))return;
    generation++;offset=0;note("");clearIssued();$("admin-dialog").showModal();lock(true);
    try{
      catalog=await api("/v1/catalog");$("admin-identity").textContent=`${user.email} · ${roles[user.role]}`;$("account-org-options").replaceChildren();
      for(const org of catalog.organizations){const label=node("label","","division-option");const box=document.createElement("input");box.type="checkbox";box.value=org.id;if(defaults().has(org.id)){box.checked=true;box.disabled=true;}label.append(box,node("span",org.name+(defaults().has(org.id)?" · 全员可见":"")));$("account-org-options").append(label);}
      resetForm();await loadList();
    }catch(error){note(error.message,true);}finally{lock(false);}
  });
  $("close-admin").addEventListener("click",()=>$("admin-dialog").close());
  $("admin-dialog").addEventListener("close",()=>{generation++;clearIssued();$("account-email").value="";});
  $("account-role").addEventListener("change",scope);$("account-cancel-edit").addEventListener("click",resetForm);
  $("account-invite-form").addEventListener("submit",async(event)=>{
    event.preventDefault();if(busy)return;lock(true);note("");
    try{
      const body={role:$("account-role").value,organizations:selected()};
      if(editing){await api(`/v1/accounts/${encodeURIComponent(editing.principal_id)}`,{method:"PATCH",body:{...body,active:editing.status!=="disabled"}});note("账号权限已保存，已有会话与 AI 授权已撤销，用户需重新登录。");}
      else{await showIssued(await api("/v1/accounts/invitations",{method:"POST",body:{...body,email:$("account-email").value}}));note("邮箱已登记，请将邀请链接发送给对方设置初始密码。");}
      resetForm();await loadList();
    }catch(error){note(error.message,true);}finally{lock(false);}
  });
  $("account-share-base").addEventListener("input",sharing);
  $("account-copy-invite").addEventListener("click",async()=>{if(!issued)return;sharing();if($("account-copy-invite").disabled)return;
    try{await navigator.clipboard.writeText($("account-share-link").value);note("邀请链接已复制。");}
    catch{$("account-share-link").focus();$("account-share-link").select();note("已选中链接，请手动复制。");}
  });
  for(const [id,change] of [["account-refresh",0],["account-prev",-20],["account-next",20]])$(id).addEventListener("click",async()=>{if(busy)return;offset=Math.max(0,offset+change);lock(true);try{await loadList();}catch(error){note(error.message,true);}finally{lock(false);}});
  window.addEventListener("agico:account-logout",()=>{$("admin-dialog").close();clearIssued();$("account-list").replaceChildren();$("account-org-options").replaceChildren();generation++;});
})();
