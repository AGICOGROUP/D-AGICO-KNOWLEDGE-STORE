/* Personal account sessions use HttpOnly cookies; no password/session credential in storage. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let me = null, invitation = "", oauthRequest = "", busy = false;
  let authSettings = {};
  const callbacks = {signedIn:async()=>{},signedOut:()=>{}};
  const note = (id,text="",error=false) => { $(id).textContent=text; $(id).classList.toggle("error",error); };
  function headers(method="GET") { return me && !["GET","HEAD","OPTIONS"].includes(method.toUpperCase()) ? {"X-CSRF-Token":me.csrf_token} : {}; }
  async function response(path,options={}) {
    const method=(options.method||"GET").toUpperCase();
    const requestHeaders={...headers(method),...options.headers};
    if(options.body && typeof options.body!=="string" && !(options.body instanceof Blob)) {
      options={...options,body:JSON.stringify(options.body)};requestHeaders["Content-Type"]="application/json";
    }
    return fetch(path,{...options,headers:requestHeaders,credentials:"same-origin",cache:"no-store",redirect:"error",signal:options.signal||AbortSignal.timeout(60000)});
  }
  async function request(path,options={}) {
    let result;
    try { result=await response(path,options); }
    catch { throw new Error("网络连接中断，请稍后重试。"); }
    const body=await result.json().catch(()=>({}));
    if(!result.ok) { const error=new Error(body.error?.message||(result.status===401?"登录已失效，请重新登录。":"操作未完成，请稍后重试。"));error.status=result.status;throw error; }
    return body;
  }
  function managementVisibility() {
    $("manage-access").hidden=!me || !["superadmin","manager"].includes(me.role) || me.must_change_password || (me.auth_mode==="dingtalk"&&me.role!=="superadmin");
    $("change-password").hidden=!me || me.must_change_password || me.auth_provider==="dingtalk";
    if($("dingtalk-bind"))$("dingtalk-bind").hidden=!(me?.auth_mode==="dingtalk"&&me.auth_provider==="local"&&!me.must_change_password);
  }
  function loginMode() {
    const ding=authSettings.auth_mode==="dingtalk";
    $("dingtalk-entry").hidden=!ding;$("auth-form").hidden=ding;$("local-auth-help").hidden=ding;
    $("maintenance-login").hidden=!authSettings.maintenance_login;
    $("dingtalk-bind").hidden=!(ding&&me?.auth_provider==="local");
    invitation="";$("auth-email").readOnly=false;$("auth-password").minLength=1;
    $("auth-password").autocomplete="current-password";$("auth-confirm").required=false;
    $("auth-confirm-field").hidden=true;$("auth-heading").textContent="登录企业知识库";
    $("auth-help").textContent=ding?"员工身份和部门归属由公司钉钉统一提供。":"使用已激活的邮箱账号登录。新成员请向所属事业部管理员索取邀请链接。";
    $("auth-password-label").textContent="密码";$("login-button").textContent="登录";
    $("login-button").disabled=false;$("invite-back").hidden=true;
  }
  function openLogin() { if(busy)return; if(!invitation)loginMode(); if(!$("auth-dialog").open)$("auth-dialog").showModal();$("auth-email").focus(); }
  function openPassword() {
    $("password-form").reset();note("password-message");
    $("password-help").textContent=me?.must_change_password?"首次登录请修改初始密码。新密码至少 8 位。":"新密码至少 8 位；修改后其他登录会话和 AI 授权将失效。";
    $("close-password").hidden=!!me?.must_change_password;
    if(!$("password-dialog").open)$("password-dialog").showModal();$("password-current").focus();
  }
  async function consent() {
    if(!oauthRequest || !me || me.must_change_password)return;
    try {
      const info=await request(`/v1/auth/oauth-request/${encodeURIComponent(oauthRequest)}`);
      $("oauth-client-name").textContent=info.client_name;
      $("oauth-account-name").textContent=me.email;
      $("oauth-dialog").showModal();
    } catch(error){note("list-message",error.message,true);}
  }
  async function acceptAccount(value) {
    me=value;managementVisibility();window.dispatchEvent(new Event("agico:account-changed"));
    if(me.must_change_password) { $("auth-dialog").close();openPassword();return; }
    $("auth-dialog").close();await callbacks.signedIn(me);await consent();
  }
  async function dingLogin(bind=false) {
    if(busy)return;if(bind){loginMode();$("auth-dialog").showModal();}busy=true;$("dingtalk-login").disabled=true;note("auth-message","正在确认钉钉身份…");
    try{await acceptAccount(await window.agicoDingTalk.login(request,bind));note("auth-message");}
    catch(error){note("auth-message",error.message,true);}
    finally{busy=false;$("dingtalk-login").disabled=false;}
  }
  $("dingtalk-login").addEventListener("click",()=>dingLogin());
  $("dingtalk-bind").addEventListener("click",()=>dingLogin(true));
  $("maintenance-login").addEventListener("click",()=>{$("auth-form").hidden=false;$("auth-email").focus();});
  async function logout() {
    try { if(me)await request("/v1/auth/logout",{method:"POST"}); }
    catch(error) { if(error.status!==401){note("list-message",error.message,true);return;} }
    me=null;$("password-dialog").close();$("auth-dialog").close();$("oauth-dialog").close();
    managementVisibility();callbacks.signedOut();window.dispatchEvent(new Event("agico:account-logout"));
  }
  $("auth-form").addEventListener("submit",async(event)=>{
    event.preventDefault();if(busy)return;busy=true;$("login-button").disabled=true;note("auth-message","正在验证…");
    const password=$("auth-password").value;
    try {
      if(invitation && password!==$("auth-confirm").value)throw new Error("两次输入的密码不一致。");
      const value=await request(invitation?"/v1/auth/activate":"/v1/auth/login",{method:"POST",body:invitation?
        {invitation,password,password_confirm:$("auth-confirm").value}:{email:$("auth-email").value,password}});
      if(invitation){invitation="";history.replaceState(null,"",location.pathname+location.search);}
      await acceptAccount(value);note("auth-message");
    } catch(error){note("auth-message",error.message,true);}
    finally{busy=false;$("login-button").disabled=false;$("auth-password").value="";$("auth-confirm").value="";}
  });
  $("close-auth").addEventListener("click",()=>$("auth-dialog").close());
  $("invite-back").addEventListener("click",()=>{history.replaceState(null,"",location.pathname+location.search);loginMode();note("auth-message");});
  $("auth-dialog").addEventListener("cancel",event=>{if(busy)event.preventDefault();});
  $("auth-dialog").addEventListener("close",()=>{$("auth-password").value="";$("auth-confirm").value="";});
  $("password-dialog").addEventListener("cancel",event=>{if(me?.must_change_password || busy)event.preventDefault();});
  $("close-password").addEventListener("click",()=>$("password-dialog").close());
  $("change-password").addEventListener("click",openPassword);
  $("password-logout").addEventListener("click",logout);
  $("password-form").addEventListener("submit",async(event)=>{
    event.preventDefault();if(busy)return;busy=true;$("password-save").disabled=true;note("password-message");
    try{
      const password=$("password-new").value;
      if(password!==$("password-confirm").value)throw new Error("两次输入的密码不一致。");
      const value=await request("/v1/auth/change-password",{method:"POST",body:{current_password:$("password-current").value,password,password_confirm:password}});
      $("password-dialog").close();$("password-form").reset();await acceptAccount(value);
    }catch(error){note("password-message",error.message,true);}
    finally{busy=false;$("password-save").disabled=false;}
  });
  for(const [id,approved] of [["oauth-approve",true],["oauth-deny",false]]) $(id).addEventListener("click",async()=>{
    if(busy)return;busy=true;$("oauth-approve").disabled=$("oauth-deny").disabled=true;note("oauth-message");
    try { const value=await request("/v1/auth/oauth-consent",{method:"POST",body:{request:oauthRequest,approved}});location.assign(value.redirect); }
    catch(error){note("oauth-message",error.message,true);busy=false;$("oauth-approve").disabled=$("oauth-deny").disabled=false;}
  });
  window.agicoAccount={request,response,headers,openLogin,logout,get current(){return me;},
    async initialize(signedIn,signedOut) {
      callbacks.signedIn=signedIn;callbacks.signedOut=signedOut;
      try{authSettings=await request("/v1/auth/settings");window.agicoDingTalk.configure(authSettings);}
      catch(error){note("list-message",error.message,true);return;}
      // Erase credentials saved by the retired shared-code interface.
      for(const key of ["agico.portal.accessToken.v1","agico.portal.adminToken.v1"])try{localStorage.removeItem(key);}catch{}
      invitation=new URLSearchParams(location.hash.slice(1)).get("invite")||"";
      oauthRequest=new URLSearchParams(location.search).get("oauth")||"";
      managementVisibility();
      if(invitation&&authSettings.auth_mode==="dingtalk"){invitation="";history.replaceState(null,"",location.pathname+location.search);}
      if(invitation) {
        try {
          const info=await request("/v1/auth/invitation",{method:"POST",body:{invitation}});
          $("auth-email").value=info.email;$("auth-email").readOnly=true;
          $("auth-heading").textContent=info.purpose==="reset"?"重新设置密码":"设置初始密码";
          $("auth-help").textContent=`账号：${info.email}。此链接绑定已登记的权限与事业部，仅可使用一次。`;
          $("auth-password-label").textContent="设置密码（至少 8 位）";$("auth-password").minLength=8;$("auth-password").autocomplete="new-password";
          $("auth-confirm-field").hidden=false;$("auth-confirm").required=true;$("invite-back").hidden=false;
          $("login-button").textContent=info.purpose==="reset"?"设置新密码":"设置密码并加入";
          $("auth-dialog").showModal();$("auth-password").focus();
        }catch(error){loginMode();note("auth-message",error.message,true);$("auth-dialog").showModal();}
        return;
      }
      try{await acceptAccount(await request("/v1/auth/me"));}
      catch(error){if(error.status===401){if(oauthRequest||authSettings.auth_mode==="dingtalk")openLogin();
        if(authSettings.auth_mode==="dingtalk"&&window.agicoDingTalk.inside())await dingLogin();
      }else note("list-message",error.message,true);}
    }
  };
})();
