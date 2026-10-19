import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_recursive_drop_reads_all_batches_retains_paths_and_reports_errors():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for browser directory API fixtures")
    script = Path("src/agico_kb/portal/folder-upload.js").read_text("utf-8")
    harness = r"""
const vm = require('node:vm');
const code = JSON.parse(require('node:fs').readFileSync(0,'utf8')).code;
const context={window:{},WeakMap}; vm.createContext(context); vm.runInContext(code,context);
const api=context.window.agicoFolderUpload;
function file(name, text='fresh', fail=false) {
  return {name,isFile:true,isDirectory:false,file(ok,error){
    if(fail)error(new Error('blocked'));else ok({name,size:text.length,text});
  }};
}
function dir(name,batches) {
  return {name,isFile:false,isDirectory:true,createReader(){let i=0;return {
    readEntries(ok){ok(batches[i++] || []);}
  }}};
}
(async()=>{
  let nested=file('same.txt','nested');
  for(let i=5;i>0;i--)nested=dir('level'+i,[[nested]]);
  const first=Array.from({length:100},(_,i)=>file(i+'.txt'));
  const root=dir('root',[first,[file('same.txt','root'),nested,file('denied.txt','',true)],[]]);
  const result=await api.collectDroppedFiles({items:[{kind:'file',webkitGetAsEntry:()=>root}],files:[]});
  const same=result.files.filter(x=>x.name==='same.txt');
  const loose={name:'loose.txt',size:9};
  const legacy=await api.collectDroppedFiles({items:[],files:[loose]});
  const mixed=await api.collectDroppedFiles({items:[{kind:'string'},{kind:'file',getAsFile:()=>loose}],files:[]});
  const selected={name:'picked.txt',webkitRelativePath:'picked/a/b/picked.txt'};
  process.stdout.write(JSON.stringify({count:result.files.length,paths:same.map(api.fileLabel),
    errors:result.errors,legacy:legacy.files.length,mixed:mixed.files.length,
    selected:api.fileLabel(selected)}));
})();
"""
    result = subprocess.run([node, "-e", harness], input=json.dumps({"code": script}),
                            text=True, encoding="utf-8", capture_output=True, check=True, timeout=10)
    data = json.loads(result.stdout)
    assert data["count"] == 102
    assert sorted(data["paths"]) == ["root/level1/level2/level3/level4/level5/same.txt", "root/same.txt"]
    assert len(data["errors"]) == 1 and "root/denied.txt" in data["errors"][0]
    assert data["legacy"] == data["mixed"] == 1
    assert data["selected"] == "picked/a/b/picked.txt"
