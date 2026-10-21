/* Project feedback is private to its author and superadministrators. */
(() => {
  "use strict";
  const $=id=>document.getElementById(id);
  const kinds={problem:"使用问题",request:"功能需求",suggestion:"改进建议"};
  const statuses={new:"待查看",planned:"已采纳",working:"处理中",done:"已完成",declined:"暂不采纳"};
  let offset=0,loadedOffset=0,hasMore=false,sequence=0,lifecycle=0,submitting=false;
  const node=(tag,text,cls="")=>{const el=document.createElement(tag);el.textContent=text;el.className=cls;return el;};
  const api=(path,options)=>window.agicoAccount.request(path,options);
  const note=(id,text="",error=false)=>{$(id).textContent=text;$(id).classList.toggle("error",error);};
  const active=()=>!$("feedback-view").hidden&&!!window.agicoAccount.current;
  const date=value=>new Date(value).toLocaleString("zh-CN",{hour12:false});
  function pages(){
    $("feedback-prev").disabled=offset===0;$("feedback-next").disabled=!hasMore;
    $("feedback-page").textContent=`第 ${Math.floor(offset/20)+1} 页`;
  }
  function row(item,admin){
    const article=node("article","","feedback-item");
    const details=node("details","","feedback-details");
    const summary=node("summary","","feedback-summary");
    const title=node("span",item.title,"feedback-item-title");
    summary.append(title,node("span",statuses[item.status],`feedback-status feedback-status-${item.status}`));
    details.append(summary);
    article.append(node("p",`${kinds[item.kind]} · #${item.id} · ${date(item.created_at)}${admin?" · "+item.author:""}`,"feedback-item-meta"));
    if(item.area)details.append(node("p",`相关功能：${item.area}`,"feedback-item-meta"));
    details.append(node("p",item.description,"feedback-description"));
    if(item.reply){
      const reply=node("div","","feedback-reply");reply.append(node("strong","开发人员回复"),node("p",item.reply),node("small",date(item.updated_at)));
      details.append(reply);
    }
    if(admin){
      const form=document.createElement("form");form.className="feedback-followup";
      const status=document.createElement("select");status.setAttribute("aria-label",`处理状态：${item.title}`);
      for(const [value,label] of Object.entries(statuses)){const option=node("option",label);option.value=value;status.append(option);}status.value=item.status;
      const reply=document.createElement("textarea");reply.rows=3;reply.maxLength=4000;reply.value=item.reply;reply.placeholder="填写回复或处理说明";reply.setAttribute("aria-label",`开发人员回复：${item.title}`);
      const save=node("button","保存跟进","button secondary");save.type="submit";
      const message=node("p","","message");message.setAttribute("role","status");
      form.append(status,reply,save,message);details.append(form);
      form.addEventListener("submit",async(event)=>{
        event.preventDefault();if(save.disabled)return;save.disabled=true;message.textContent="";
        const current=sequence;
        try{
          await api(`/v1/feedback/${item.id}`,{method:"PATCH",body:{status:status.value,reply:reply.value.trim(),expected_revision:item.revision}});
          if(current!==sequence||!active())return;
          await load();note("feedback-list-message","处理状态和回复已保存。");
        }catch(error){if(current===sequence&&active()){message.textContent=error.message;message.classList.add("error");}}
        finally{save.disabled=false;}
      });
    }
    article.prepend(details);return article;
  }
  async function load(){
    if(!active())return;
    const current=++sequence;
    const query=new URLSearchParams({limit:"20",offset:String(offset),scope:$("feedback-filter-scope").value});
    if($("feedback-filter-kind").value)query.set("kind",$("feedback-filter-kind").value);
    if($("feedback-filter-status").value)query.set("status",$("feedback-filter-status").value);
    $("feedback-list").setAttribute("aria-busy","true");note("feedback-list-message");
    $("feedback-prev").disabled=$("feedback-next").disabled=true;
    try{
      const data=await api(`/v1/feedback?${query}`);if(current!==sequence||!active())return;
      hasMore=data.has_more;loadedOffset=offset;$("feedback-filter-scope").hidden=!data.can_manage;
      $("feedback-history-heading").textContent=data.scope==="all"?"全部反馈":"我的反馈";
      $("feedback-privacy").textContent=data.can_manage?"展开反馈查看详情，可回复并更新处理状态。":"你的反馈仅自己和开发人员可见。";
      $("feedback-counts").replaceChildren(node("span",`共 ${data.counts.total} 条`),node("span",`待查看 ${data.counts.new}`),node("span",`已完成 ${data.counts.done}`));
      $("feedback-list").replaceChildren();
      if(!data.items.length){
        const empty=node("div","","feedback-empty");empty.append(node("strong","暂无符合条件的反馈"),node("p","可以提交新的问题或建议，也可调整筛选条件。"));$("feedback-list").append(empty);
      }
      for(const item of data.items)$("feedback-list").append(row(item,data.can_manage));pages();
    }catch(error){if(current===sequence&&active()){offset=loadedOffset;note("feedback-list-message",error.message,true);}}
    finally{if(current===sequence){$("feedback-list").removeAttribute("aria-busy");pages();}}
  }
  $("feedback-form").addEventListener("submit",async(event)=>{
    event.preventDefault();if(submitting||!active())return;
    submitting=true;$("feedback-fields").disabled=true;note("feedback-message");
    const current=lifecycle;
    try{
      const result=await api("/v1/feedback",{method:"POST",body:{kind:$("feedback-kind").value,title:$("feedback-title").value.trim(),area:$("feedback-area").value,description:$("feedback-description").value.trim()}});
      if(current!==lifecycle||!window.agicoAccount.current)return;
      $("feedback-form").reset();offset=0;$("feedback-filter-kind").value=$("feedback-filter-status").value="";
      note("feedback-message",`反馈 #${result.id} 已提交，可在右侧查看处理进展。`);await load();
    }catch(error){if(current===lifecycle&&active())note("feedback-message",error.message,true);}
    finally{if(current===lifecycle){submitting=false;$("feedback-fields").disabled=false;}}
  });
  for(const id of ["feedback-filter-kind","feedback-filter-status","feedback-filter-scope"])$(id).addEventListener("change",()=>{offset=0;load();});
  $("feedback-refresh").addEventListener("click",load);
  $("feedback-prev").addEventListener("click",()=>{offset=Math.max(0,offset-20);load();});
  $("feedback-next").addEventListener("click",()=>{if(hasMore){offset+=20;load();}});
  function clear(){sequence++;lifecycle++;offset=loadedOffset=0;hasMore=false;submitting=false;$("feedback-fields").disabled=false;$("feedback-list").replaceChildren();$("feedback-counts").replaceChildren();$("feedback-form").reset();note("feedback-message");note("feedback-list-message");}
  window.addEventListener("agico:disconnect",clear);
  window.agicoFeedback={open(){offset=0;$("feedback-filter-kind").value=$("feedback-filter-status").value="";$("feedback-filter-scope").value="all";load();}};
})();
