import shutil
import subprocess
from pathlib import Path

import pytest


def test_manager_edits_extensions_with_fixed_base_and_uses_dedicated_endpoint():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for interactive personnel editor test')
    harness = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.checked=false;this.disabled=false;this.value='';this.hidden=false;this.dataset={};this.classList={toggle(){}};}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=[...children];}
 addEventListener(event,fn){this.events[event]=fn;}
 querySelectorAll(selector){const all=this.children.flatMap(c=>[c,...c.querySelectorAll('*')]);return all.filter(c=>selector==='*'||selector==='button'&&c.tag==='button'||selector==='input'&&c.tag==='input'||selector==='input:checked'&&c.tag==='input'&&c.checked);}
 focus(){} reset(){} showModal(){} close(){}
}
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const roleLabel=new Element('label'),orgs=[{id:'general-group',name:'集团'},{id:'baiste',name:'百斯特'},{id:'xingyuan',name:'星源'},{id:'company-common',name:'公共'}];
const calls=[];let extras=[];
const context={document:{getElementById:$,createElement:tag=>new Element(tag),querySelector:()=>roleLabel},
 window:{addEventListener(){},agicoAccount:{current:{role:'manager',auth_mode:'dingtalk',email:'manager@example.test'},
 request:async(path,options)=>{
 if(path==='/v1/catalog')return {organizations:orgs.filter(o=>o.id!=='xingyuan'),default_visible_organizations:['general-group','company-common']};
 if(path.startsWith('/v1/accounts?'))return {has_more:false,total_accounts:1,access_organizations:orgs,items:[{principal_id:'staff',email:'staff@example.test',display_name:'合成员工',role:'user',status:'active',organizations:orgs.filter(o=>o.id!=='xingyuan'||extras.includes(o.id)),synchronized_organizations:['baiste'],extra_organizations:extras,can_manage:false,can_edit_access:true,children:[]}]};
 calls.push([path,options]);extras=options.body.extra_organizations;return {extra_organizations:extras};
 }}},URL,location:{origin:'http://testserver'},navigator:{clipboard:{writeText:async()=>{}}},Set,Date,encodeURIComponent};
vm.createContext(context);vm.runInContext(require('node:fs').readFileSync(0,'utf8'),context);
(async()=>{
 await $('manage-access').events.click();
 const edit=()=>$('account-list').querySelectorAll('button').find(b=>b.textContent==='编辑扩展访问');
 assert.ok(edit());assert.equal($('account-list').querySelectorAll('button').length,1,'No role/status actions for unrelated staff');
 edit().events.click();
 let boxes=$('account-org-options').querySelectorAll('input');
 assert.equal(boxes.length,4,'Extra division choices are not restricted to manager own catalog');
 assert.ok(boxes.find(b=>b.value==='baiste').disabled);assert.ok(boxes.find(b=>b.value==='baiste').checked);
 assert.ok(roleLabel.hidden);assert.equal($('account-submit').textContent,'保存扩展访问');
 boxes.find(b=>b.value==='xingyuan').checked=true;
 await $('account-invite-form').events.submit({preventDefault(){}});
 assert.equal(calls[0][0],'/v1/accounts/staff/access');assert.deepEqual(JSON.parse(JSON.stringify(calls[0][1].body)),{extra_organizations:['xingyuan']});
 assert.match($('admin-message').textContent,/立即生效/);
 edit().events.click();boxes=$('account-org-options').querySelectorAll('input');
 assert.ok(boxes.find(b=>b.value==='xingyuan').checked);boxes.find(b=>b.value==='xingyuan').checked=false;
 await $('account-invite-form').events.submit({preventDefault(){}});
 assert.deepEqual(JSON.parse(JSON.stringify(calls[1][1].body)),{extra_organizations:[]});
 process.stdout.write('manager extension editor passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run([node,'-e',harness],input=Path('src/agico_kb/portal/admin.js').read_text('utf-8'),
                            capture_output=True,text=True,encoding='utf-8',timeout=10)
    assert result.returncode == 0, result.stderr
