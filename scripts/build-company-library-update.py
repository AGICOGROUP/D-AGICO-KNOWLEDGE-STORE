"""Build an allowlisted, self-contained maintenance script; no private files."""
import base64
import hashlib
import io
import json
from pathlib import Path
import zipfile

FILES = (
    'src/agico_kb/accounts.py','src/agico_kb/dingtalk_identity.py',
    'src/agico_kb/db.py','src/agico_kb/operations.py','src/agico_kb/library_maintenance.py',
    'src/agico_kb/migrations/008_access_extensions.sql',
    'src/agico_kb/portal/admin.js','src/agico_kb/portal/account.js',
    'scripts/company-library-maintenance.py','scripts/configure-server-services.ps1',
)


def build(root,target):
    content={name:(root/name).read_bytes() for name in FILES}
    manifest={'format':1,'release':'20261027-ACCESS1','files':{name:hashlib.sha256(value).hexdigest() for name,value in content.items()}}
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,value in content.items():archive.writestr(name,value)
        archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False))
    payload=buffer.getvalue()
    template=(root/'scripts/update-company-library.ps1').read_text('utf-8')
    result=template.replace('RELEASE_PAYLOAD_BASE64',base64.b64encode(payload).decode('ascii')).replace('RELEASE_PAYLOAD_SHA256',hashlib.sha256(payload).hexdigest())
    target.parent.mkdir(parents=True,exist_ok=True)
    # BOM keeps the script readable in Windows PowerShell 5.1 on a Chinese server.
    target.write_text(result,encoding='utf-8-sig')
    return {'path':str(target),'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}


if __name__=='__main__':
    root=Path(__file__).resolve().parent.parent
    target=root/'output/deploy/20261027/update-company-library-20261027.ps1'
    print(json.dumps(build(root,target)))
