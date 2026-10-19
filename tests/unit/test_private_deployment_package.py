import hashlib
import json
import zipfile

from agico_kb.provider_config import build_private_package


def test_private_package_carries_config_with_verified_source_only(tmp_path):
    source=tmp_path/'source.zip'
    code=b'fresh deployment test source'
    with zipfile.ZipFile(source,'w') as archive:
        archive.writestr('setup.ps1',code)
        archive.writestr('source-manifest.json',json.dumps({'setup.ps1':hashlib.sha256(code).hexdigest()}))
    private=tmp_path/'private'; private.mkdir()
    config=private/'providers.json'
    config.write_text(json.dumps({'version':1,'runtime':{},'profiles':{
        'primary':'api=fresh-test-key\nmodel=glm-5.3-flash\n'}}))
    target=private/'deployment.zip'
    build_private_package(source,config,target)
    with zipfile.ZipFile(target) as archive:
        assert archive.read('setup.ps1')==code
        assert archive.read('.deployment-private/providers.json')==config.read_bytes()
    with zipfile.ZipFile(source) as archive:
        assert '.deployment-private/providers.json' not in archive.namelist()
