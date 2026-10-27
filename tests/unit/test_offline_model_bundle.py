import hashlib
import json
from uuid import uuid4

import pytest

from agico_kb import server_setup


def bundle(tmp_path):
    root=tmp_path/'models';root.mkdir()
    path=root/'ocr/fresh.onnx';path.parent.mkdir();path.write_bytes(uuid4().bytes)
    manifest={'version':1,'embedding_model':'synthetic-model',
              'files':{'ocr/fresh.onnx':hashlib.sha256(path.read_bytes()).hexdigest()}}
    (root/'agico-offline-models.json').write_text(json.dumps(manifest),encoding='utf-8')
    return root,path,manifest


def test_valid_offline_bundle_is_verified_without_network(tmp_path):
    root,_,_=bundle(tmp_path)
    assert server_setup.verified_offline_models(root,'synthetic-model') is True
    assert server_setup.verified_offline_models(tmp_path/'absent','synthetic-model') is False


@pytest.mark.parametrize('damage',['missing','changed','traversal','wrong-model'])
def test_invalid_offline_bundle_fails_instead_of_using_network(tmp_path,damage):
    root,path,manifest=bundle(tmp_path)
    if damage=='missing':path.unlink()
    elif damage=='changed':path.write_bytes(uuid4().bytes)
    elif damage=='traversal':manifest['files']={'../outside.onnx':'0'*64}
    else:manifest['embedding_model']='different-model'
    (root/'agico-offline-models.json').write_text(json.dumps(manifest),encoding='utf-8')
    with pytest.raises(ValueError,match='Offline model'):
        server_setup.verified_offline_models(root,'synthetic-model')
