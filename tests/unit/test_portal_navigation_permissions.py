import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_navigation_and_pending_polling_follow_actual_permissions():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for portal permissions regression')
    app = Path('src/agico_kb/portal/app.js').read_text('utf-8')
    account = Path('src/agico_kb/portal/account.js').read_text('utf-8')
    polling = app[app.index('async function refreshPendingState('):app.index('async function fileSha256(')]
    visibility_start = app.find('function updateReviewNavigation(')
    visibility = app[visibility_start:app.index('function processingFinished(', visibility_start)] if visibility_start >= 0 else ''
    management = account[account.index('function managementVisibility('):account.index('function loginMode(')]
    harness = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const code=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const elements=new Map();const $=id=>{if(!elements.has(id))elements.set(id,{hidden:false});return elements.get(id);};
let calls=0,scheduled=0;
const context={$ ,state:{authenticated:true,session:{publisher_organizations:[]},tab:'library',generation:1},
 api:async()=>{calls++;return {total:5};},updatePendingCount:()=>{},loadList:async()=>{calls++;},
 schedulePendingRefresh:()=>{scheduled++;},me:null};
vm.createContext(context);vm.runInContext(code.polling+code.visibility+code.management,context);
(async()=>{
 await context.refreshPendingState();
 assert.equal(calls,0,'User without approval rights must not poll the review list');
 assert.equal(scheduled,0,'No review polling timer for a non-approver');
 const cases=[['user',[],false,false],['manager',[],true,false],
 ['manager',['baiste'],true,true],['superadmin',['baiste','xingyuan'],true,true]];
 for(const [role,orgs,manage,review] of cases){
   context.me={role,must_change_password:false};context.state.session={account_role:role,publisher_organizations:orgs};
   context.managementVisibility();context.updateReviewNavigation();
   assert.equal($('manage-access').hidden,!manage,role+' account management');
   assert.equal($('nav-pending').hidden,!review,role+' review visibility');
   assert.equal($('nav-upload').hidden,false,'Upload remains visible');
   context.me.auth_mode='dingtalk';context.managementVisibility();
   assert.equal($('manage-access').hidden,!manage,role+' DingTalk account management');
 }
 await context.refreshPendingState();assert.equal(calls,1);assert.equal(scheduled,1);
 context.me=null;context.state.authenticated=false;context.state.session=null;
 context.managementVisibility();context.updateReviewNavigation();
 assert.equal($('manage-access').hidden,true);assert.equal($('nav-pending').hidden,true);
 await context.refreshPendingState();assert.equal(calls,1);
 process.stdout.write('permission navigation passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, '-e', harness], input=json.dumps({
        'polling': polling, 'visibility': visibility, 'management': management,
    }), text=True, encoding='utf-8', capture_output=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    assert 'passed' in result.stdout
