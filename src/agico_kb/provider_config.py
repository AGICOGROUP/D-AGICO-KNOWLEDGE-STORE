"""Portable provider metadata. Deployment packages never carry API or DingTalk keys."""
import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

RUNTIME_KEYS = {
    'AGICO_KB_VISION_API_URL', 'AGICO_KB_VISION_FALLBACK_API_URL',
    'AGICO_KB_VISION_FALLBACK_MODEL', 'AGICO_KB_VISION_PDF_MODEL',
    'AGICO_KB_VISION_MAX_CALLS_PER_DAY', 'AGICO_KB_VISION_MAX_IMAGES_PER_FILE',
    'AGICO_KB_NAMING_FALLBACK_MODELS',
}
PROFILE_KEYS = {'primary':'AGICO_KB_VISION_CONFIG_FILE',
                'fallback':'AGICO_KB_VISION_FALLBACK_CONFIG_FILE'}
from .provider_catalog import VISUAL_MODELS as SUPPORTED_VISUAL_MODELS
VISUAL_MODELS = set(SUPPORTED_VISUAL_MODELS)
NAMING_MODELS = VISUAL_MODELS | {'deepseek-v4-flash','deepseek-v4.1-flash','deepseek-v4.1-flash-1017'}


def no_links(path):
    for item in (path,*path.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError('Private configuration cannot use reparse paths')


def validate_bundle(bundle):
    if not isinstance(bundle,dict) or set(bundle)!= {'version','runtime','profiles'} or bundle['version']!=2:
        raise ValueError('Unsupported private provider configuration')
    runtime,profiles=bundle['runtime'],bundle['profiles']
    if (not isinstance(runtime,dict) or set(runtime)-RUNTIME_KEYS or
            any(not isinstance(value,str) for value in runtime.values()) or
            not isinstance(profiles,dict) or 'primary' not in profiles or set(profiles)-PROFILE_KEYS.keys()):
        raise ValueError('Invalid private provider configuration fields')
    from .provider_catalog import MODELS
    for profile,models in profiles.items():
        if not isinstance(models,list) or not models or any(model not in MODELS[profile] for model in models):
            raise ValueError('Invalid visual model metadata')
    models=runtime.get('AGICO_KB_NAMING_FALLBACK_MODELS','')
    if models and any(model.strip() not in NAMING_MODELS for model in models.split(',')):
        raise ValueError('Unsupported naming model')
    endpoints={'AGICO_KB_VISION_API_URL':'https://open.bigmodel.cn/api/paas/v4/chat/completions',
               'AGICO_KB_VISION_FALLBACK_API_URL':'https://cephalon.cloud/user-center/v1/model/chat/completions'}
    if any(key in runtime and runtime[key]!=value for key,value in endpoints.items()):
        raise ValueError('Provider endpoint differs from the approved route')
    return bundle


def export_providers(runtime_path,target):
    from .vision import _credential_models
    runtime=json.loads(Path(runtime_path).read_text(encoding='utf-8-sig'))
    profiles={'primary':['glm-5.3-flash'], 'fallback':['glm-5.3-flashx','glm-5.3-flash']}
    for name,key in PROFILE_KEYS.items():
        managed=Path(runtime.get('AGICO_KB_API_KEYS_DIR', Path(runtime_path).parent/'api-keys'))/(name+'.txt')
        path=managed if managed.is_file() else Path(runtime[key]) if runtime.get(key) else None
        if path and path.is_file():
            _,models=_credential_models(path)
            profiles[name]=list(models)
    bundle=validate_bundle({'version':2,'runtime':{key:runtime[key] for key in RUNTIME_KEYS if key in runtime},
                            'profiles':profiles})
    target=Path(target).absolute(); no_links(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(bundle,ensure_ascii=False,indent=2),encoding='utf-8')


def import_providers(app_root,data_root):
    source=Path(app_root)/'.deployment-private/providers.json'
    if not source.exists():
        return False
    no_links(source)
    bundle=validate_bundle(json.loads(source.read_text(encoding='utf-8-sig')))
    config_root=Path(data_root)/'config'
    runtime_path=config_root/'runtime.json'
    no_links(runtime_path)
    runtime=json.loads(runtime_path.read_text(encoding='utf-8-sig'))
    key_dir=Path(runtime.get('AGICO_KB_API_KEYS_DIR', config_root/'api-keys')).absolute()
    no_links(key_dir)
    key_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    models_file=key_dir/'models.json'
    no_links(models_file)
    if not models_file.exists():
        models_file.write_text(json.dumps(bundle['profiles'],ensure_ascii=False),encoding='utf-8')
    runtime.update(bundle['runtime'])
    runtime['AGICO_KB_API_KEYS_DIR']=str(key_dir)
    runtime_path.write_text(json.dumps(runtime,ensure_ascii=False,indent=2),encoding='utf-8')
    return True


def build_private_package(source_archive,provider_file,target):
    provider_file,target=Path(provider_file).absolute(),Path(target).absolute()
    no_links(provider_file); no_links(target)
    if target.parent!=provider_file.parent or target==provider_file:
        raise ValueError('Private package must stay in its protected configuration directory')
    bundle=json.loads(provider_file.read_bytes())
    # Older operator files may contain credentials. Extract model metadata only;
    # never copy their bytes into a new package.
    if isinstance(bundle,dict) and bundle.get('version')==1:
        profiles={}
        for name,value in bundle.get('profiles',{}).items():
            lines=value.splitlines() if isinstance(value,str) else []
            if len(lines)!=2 or not lines[0].startswith('api=') or not lines[1].startswith('model='):
                raise ValueError('Invalid legacy provider profile')
            profiles[name]=lines[1][6:].split('/')
        bundle={**bundle,'version':2,'profiles':profiles}
    provider=json.dumps(validate_bundle(bundle),ensure_ascii=False,indent=2).encode('utf-8')
    with zipfile.ZipFile(source_archive) as source:
        manifest=json.loads(source.read('source-manifest.json'))
        if set(source.namelist())!=set(manifest)|{'source-manifest.json'}:
            raise ValueError('Unexpected source package members')
        for name,digest in manifest.items():
            if name.startswith(('/', '.deployment-private/')) or '..' in name.split('/') or '\\' in name:
                raise ValueError('Unsafe source package path')
            if hashlib.sha256(source.read(name)).hexdigest()!=digest:
                raise ValueError('Source package checksum mismatch')
        with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as package:
            for name in source.namelist():
                package.writestr(name,source.read(name))
            package.writestr('.deployment-private/providers.json',provider)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--target',type=Path,required=True)
    args=parser.parse_args()
    try:
        export_providers(args.runtime,args.target)
    except Exception:  # noqa: BLE001 - credential-bearing configuration must never be printed
        print('Private provider export failed; no configuration values emitted.',file=sys.stderr)
        return 1
    print('Provider metadata exported without keys. Enter keys through the developer portal after deployment.')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
