import importlib.util,json,hashlib
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('company_import',Path(__file__).resolve().parents[2]/'scripts/import-company-data.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

@pytest.mark.parametrize('damage',['changed','missing','traversal'])
def test_new_migration_payload_is_rejected_before_restore(tmp_path,damage):
    names={'database/manifest.json','database/database.dump','database/uv.lock','config/dingtalk.json','source-invariants.json','runtime-patch.json'}
    files={}
    for name in names:
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'new-synthetic-'+name.encode())
        files[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    if damage=='changed':(tmp_path/'database/database.dump').write_bytes(b'changed synthetic bytes')
    elif damage=='missing':(tmp_path/'database/database.dump').unlink()
    else:files['../escape.txt']='0'*64
    (tmp_path/'manifest.json').write_text(json.dumps({'format':1,'migration_id':'20261026_12345678','files':files}))
    with pytest.raises(ValueError):module.validate_payload(tmp_path)

def test_fingerprint_preserves_rows_independent_of_order():
    assert module.row_fingerprint([(2,'a'),(1,'b')])==module.row_fingerprint([(1,'b'),(2,'a')])
    assert module.row_fingerprint([(1,'b')])!=module.row_fingerprint([(1,'c')])
