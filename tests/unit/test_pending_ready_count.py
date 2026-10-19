import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_navigation_and_list_use_actionable_count_not_total_submissions():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for live portal function regression')
    app = Path('src/agico_kb/portal/app.js').read_text('utf-8')
    functions = [
        app[app.index('function updatePendingCount('):app.index('function schedulePendingRefresh(')],
        app[app.index('async function refreshPendingState('):app.index('async function fileSha256(')],
        app[app.index('async function loadList('):app.index('function switchTab(')],
    ]
    harness = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const code=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{hidden:false,textContent:'',setAttribute(k,v){this[k]=v;}});return elements.get(id);};
let data={total:20,reviewable_total:0,items:[],has_more:false};
const context={$ ,state:{authenticated:true,tab:'library',generation:1,listRequest:0,offset:0},
api:async()=>data,updateReviewNavigation:()=>true,schedulePendingRefresh:()=>{},
window:{agicoDocumentPreview:{isOpen:()=>false}},message:()=>{},rows:()=>{},empty:()=>{}};
vm.createContext(context);vm.runInContext(code.join('\n'),context);
(async()=>{
 await context.refreshPendingState();
 assert.equal($('pending-count').textContent,0);
 assert.equal($('pending-count').hidden,true);
 data={...data,reviewable_total:3};
 await context.refreshPendingState();
 assert.equal($('pending-count').textContent,3);
 assert.match($('nav-pending')['aria-label'],/3.*可审核/);
 context.state.tab='pending';
 await context.loadList();
 assert.equal($('pending-count').textContent,3);
 assert.match($('list-caption').textContent,/待审核.*3/);
 assert.match($('list-caption').textContent,/全部提交.*20/);
 data={...data,reviewable_total:0};
 await context.loadList();
 assert.equal($('pending-count').hidden,true);
 assert.match($('list-caption').textContent,/待审核.*0/);
 process.stdout.write('ready counts passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run([node, '-e', harness], input=json.dumps(functions),
                            text=True, encoding='utf-8', capture_output=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr
