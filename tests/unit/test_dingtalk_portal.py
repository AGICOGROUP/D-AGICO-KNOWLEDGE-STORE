import shutil
import subprocess
from pathlib import Path

import pytest


def test_adapter_uses_server_company_and_never_posts_client_roles():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for portal contract verification')
    source = Path('src/agico_kb/portal/dingtalk.js').read_text(encoding='utf-8')
    harness = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const code=require('node:fs').readFileSync(0,'utf8');
const calls=[];const context={window:{dd:{env:{platform:'pc'},ready:f=>f(),
 runtime:{permission:{requestAuthCode:options=>{assert.equal(options.corpId,'server-corp');options.onSuccess({code:'fresh-code'});}}}}},
 navigator:{userAgent:'DingTalk PC'},setTimeout,clearTimeout,document:{},Error};
vm.createContext(context);vm.runInContext(code,context);
(async()=>{
 const adapter=context.window.agicoDingTalk;
 adapter.configure({auth_mode:'dingtalk',corp_id:'server-corp'});
 const result=await adapter.login(async(path,options)=>{calls.push([path,options]);return path.endsWith('challenge')?{challenge:'server-challenge'}:{role:'user'};});
 assert.equal(result.role,'user');assert.equal(calls[0][0],'/v1/auth/dingtalk/challenge');
 assert.deepEqual(JSON.parse(JSON.stringify(calls[1][1].body)),{code:'fresh-code',challenge:'server-challenge'});
 context.window.dd.env.platform='notInDingTalk';context.navigator.userAgent='Chrome';
 await assert.rejects(adapter.login(async()=>{throw Error('must not call API outside DingTalk')}),/钉钉/);
 process.stdout.write('DingTalk adapter passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run([node, '-e', harness], input=source, capture_output=True,
                            text=True, encoding='utf-8', timeout=10)
    assert result.returncode == 0, result.stderr


def test_login_ui_has_explicit_retry_and_binding():
    html = Path('src/agico_kb/portal/index.html').read_text('utf-8')
    account = Path('src/agico_kb/portal/account.js').read_text('utf-8')
    assert 'dingtalk-login' in html and 'dingtalk-bind' in html
    assert '/v1/auth/settings' in account
    assert 'auth-mode-help' in html
