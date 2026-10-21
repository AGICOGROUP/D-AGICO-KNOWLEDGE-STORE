"""Execute the real submit handler with secure and HTTP browser crypto shapes."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def submit_scenario(mode):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for browser upload regression")
    source = Path("src/agico_kb/portal/app.js").read_text(encoding="utf-8")
    helpers = source[source.index("async function fileSha256"):source.index("function errorText")]
    handler = source[source.index('$("upload-form").addEventListener'):source.index('$("reset-attempt").addEventListener')]
    harness = r"""
const vm=require('node:vm'),{webcrypto}=require('node:crypto');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const nodes=new Map(),calls=[],keys=[],messages=[];let listener,transfers=0,submissions=0;
const body=Buffer.from('测试数据 UPLOAD-HTTP-'+require('node:crypto').randomUUID());
const state={authenticated:true,files:[{name:'测试数据.txt',size:body.length,
 arrayBuffer:async()=>body.buffer.slice(body.byteOffset,body.byteOffset+body.byteLength)}],busy:false,collecting:false,
 attempt:null,session:{},catalog:{organizations:[{id:'baiste',name:'测试事业部'}]}};
const crypto=input.mode==='missing_crypto'?undefined:input.mode==='https'?webcrypto:
 {getRandomValues:webcrypto.getRandomValues.bind(webcrypto)};
const context={crypto,Uint8Array,Error,Math,performance:require('node:perf_hooks').performance,
 state,document:{querySelector:()=>({value:'baiste'})},
 $:id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',hidden:false,disabled:false,
  addEventListener:(name,fn)=>{if(id==='upload-form')listener=fn;},focus(){},querySelector:()=>({focus(){}})});
  return nodes.get(id);},
 message:(id,text,error)=>messages.push({id,text,error:!!error}),
 renderFiles(){},orgName:()=> '测试事业部',size:n=>n+' bytes',updateReviewNavigation:()=>false,
 resetFile(){state.files=[];state.attempt=null;},
 putFile:async(id,file)=>{transfers++;if(!(await file.arrayBuffer()).byteLength)throw Error('empty transfer');},
 api:async(path,options)=>{calls.push(path);if(path==='/v1/uploads'){
   keys.push(options.headers['Idempotency-Key']);return {upload_id:'fresh-upload'};}
  if(path==='/v1/submissions'){submissions++;if(input.mode==='retry'&&submissions===1)throw Error('temporary server error');
   return {version_id:'fresh-version'};}throw Error('unexpected endpoint');}};
context.globalThis=context;vm.createContext(context);vm.runInContext(input.helpers+'\n'+input.handler,context);
(async()=>{
 let error=null;try{await listener({preventDefault(){}});if(input.mode==='retry')await listener({preventDefault(){}});}
 catch(e){error=e.message;}
 const generated=[];if(typeof context.uploadIdempotencyKey==='function'&&crypto){
   for(let i=0;i<500;i++)generated.push(context.uploadIdempotencyKey());}
 process.stdout.write(JSON.stringify({error,calls,keys,messages,transfers,submissions,busy:state.busy,
  retryEnabled:!nodes.get('submit-button')?.disabled,generated}));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", harness],
                            input=json.dumps({"mode": mode, "helpers": helpers, "handler": handler}),
                            text=True, encoding="utf-8", capture_output=True, timeout=15, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("mode", ["http", "https"])
def test_upload_submit_completes_with_http_or_secure_context_crypto(mode):
    result = submit_scenario(mode)
    assert result["error"] is None, result
    assert result["calls"] == ["/v1/uploads", "/v1/submissions"]
    assert result["transfers"] == result["submissions"] == 1
    assert any("1 份文件已提交" in message["text"] for message in result["messages"])
    assert not result["busy"] and result["retryEnabled"]


def test_unavailable_crypto_is_reported_without_unhandled_rejection_or_network_request():
    result = submit_scenario("missing_crypto")
    assert result["error"] is None
    assert result["calls"] == []
    assert any(message["error"] and "上传标识" in message["text"] for message in result["messages"])
    assert not result["busy"] and result["retryEnabled"]


def test_submission_retry_does_not_prepare_or_transfer_file_again():
    result = submit_scenario("retry")
    assert result["error"] is None
    assert len(result["keys"]) == result["transfers"] == 1
    assert result["submissions"] == 2
    assert not result["busy"] and result["retryEnabled"]


def test_http_idempotency_keys_have_uuid_v4_shape_and_do_not_repeat():
    import re

    result = submit_scenario("http")
    generated = result["generated"]
    assert len(generated) == len(set(generated)) == 500
    assert all(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", value)
               for value in generated)
