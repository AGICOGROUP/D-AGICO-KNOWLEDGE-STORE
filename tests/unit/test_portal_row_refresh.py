import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_refresh_keeps_unchanged_rows_and_previews_while_updating_status_and_order():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for portal refresh regression')
    script = Path('src/agico_kb/portal/app.js').read_text('utf-8')
    start = script.find('function reconcileFileRows(')
    assert start >= 0, 'Periodic refresh must reconcile rows rather than destroy all thumbnails'
    code = script[start:script.index('function rows(', start)]
    harness = r"""
const vm=require('node:vm'), assert=require('node:assert/strict');
const code=JSON.parse(require('node:fs').readFileSync(0,'utf8')).code;
const context={};vm.createContext(context);vm.runInContext(code,context);
let mutations=0, builds=0, released=[];
const container={children:[],get firstElementChild(){return this.children[0]||null;},
  insertBefore(row,before){mutations++;row.remove();
    const index=before?this.children.indexOf(before):this.children.length;
    this.children.splice(index,0,row);row.parent=this;}};
function makeRow(status){return {status,parent:null,
  get nextElementSibling(){return this.parent?.children[this.parent.children.indexOf(this)+1]||null;},
  remove(){if(this.parent){mutations++;this.parent.children.splice(this.parent.children.indexOf(this),1);this.parent=null;}}};}
const signature=item=>JSON.stringify(item);
const build=(item,preview)=>{builds++;return {row:makeRow(item.status),preview:preview||{url:'fresh:'+item.version_id}};};
const release=preview=>released.push(preview.url);
let previous=new Map();
const fresh=[{version_id:'a',status:'processing'},{version_id:'b',status:'queued'}];
previous=context.reconcileFileRows(container,previous,fresh,signature,build,release);
const rowA=previous.get('a').row, thumbA=previous.get('a').preview, rowB=previous.get('b').row;
mutations=0;
previous=context.reconcileFileRows(container,previous,fresh,signature,build,release);
assert.equal(mutations,0,'No DOM changes for an unchanged refresh');
assert.equal(builds,2);assert.equal(previous.get('a').row,rowA);assert.equal(previous.get('a').preview,thumbA);
previous=context.reconcileFileRows(container,previous,[{version_id:'b',status:'ready'},fresh[0]],signature,build,release);
assert.equal(previous.get('b').row.status,'ready');assert.equal(previous.get('a').row,rowA);
assert.equal(previous.get('a').preview,thumbA);assert.equal(previous.get('b').preview.url,'fresh:b');
assert.equal(container.children[0],previous.get('b').row);assert.equal(container.children[1],rowA);
assert.equal(rowB.parent,null);assert.equal(released.length,0,'Status changes must not release image URLs');
previous=context.reconcileFileRows(container,previous,[fresh[0]],signature,build,release);
assert.deepEqual(released,['fresh:b']);assert.equal(container.children.length,1);
assert.equal(previous.get('a').row,rowA);
previous=context.reconcileFileRows(container,previous,[],signature,build,release);
assert.deepEqual(released,['fresh:b','fresh:a']);assert.equal(container.children.length,0);
process.stdout.write('refresh, status change, reorder and removal passed');
"""
    result = subprocess.run([node, '-e', harness], input=json.dumps({'code':code}),
                            text=True, encoding='utf-8', capture_output=True, check=True, timeout=10)
    assert 'passed' in result.stdout
