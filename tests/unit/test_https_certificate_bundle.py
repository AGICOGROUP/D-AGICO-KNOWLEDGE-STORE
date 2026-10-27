import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import zipfile

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

spec=importlib.util.spec_from_file_location('cert_import',Path(__file__).resolve().parents[2]/'scripts/prepare-https-certificate.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

@pytest.mark.parametrize('case',['valid','wildcard','wrong-host','wrong-key','expired'])
def test_fresh_certificate_bundle(tmp_path,case):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name='*.agicogroup.com' if case=='wildcard' else ('other.agicogroup.com' if case=='wrong-host' else 'zsk.agicogroup.com')
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,name)])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(days=2)).not_valid_after(now+timedelta(days=-1 if case=='expired' else 1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]),critical=False).sign(key,hashes.SHA256()))
    if case=='wrong-key':key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    archive=tmp_path/'new-test-only.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('nested/test.pem',cert.public_bytes(serialization.Encoding.PEM))
        z.writestr('nested/test.key',key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    if case in {'valid','wildcard'}:
        result=module.inspect_bundle(archive,'zsk.agicogroup.com')
        assert result[0].serial_number==cert.serial_number
    else:
        with pytest.raises(ValueError):module.inspect_bundle(archive,'zsk.agicogroup.com')

def test_cli_imports_to_separate_data_directory_and_preserves_existing_tls(tmp_path):
    import ctypes,json,os,subprocess,sys
    if os.name!='nt' or not ctypes.windll.shell32.IsUserAnAdmin():
        pytest.skip('Administrator Windows required for ACL integration')
    test_fresh_certificate_bundle(tmp_path,'valid')
    root=tmp_path/'data';config=root/'config';config.mkdir(parents=True)
    (config/'deployment.json').write_text(json.dumps({'data_root':str(root),'stage':'installed'}))
    command=[sys.executable,str(Path(module.__file__)), '--archive',str(tmp_path/'new-test-only.zip'),'--data-root',str(root)]
    result=subprocess.run(command,capture_output=True,text=True)
    assert result.returncode==0,result.stdout
    payload=json.loads(result.stdout)
    assert payload['private_key_match'] is True
    assert (config/'tls/private.key').is_file()
    before=(config/'tls/private.key').read_bytes()
    rerun=subprocess.run(command,capture_output=True,text=True)
    assert rerun.returncode==1
    assert (config/'tls/private.key').read_bytes()==before
