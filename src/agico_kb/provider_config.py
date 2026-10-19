"""Portable private API configuration; excludes database/account/domain settings."""
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
VISUAL_MODELS = {'glm-5.3-flash','glm-5.3-flashx'}
NAMING_MODELS = VISUAL_MODELS | {'deepseek-v4-flash'}


def no_links(path):
    for item in (path,*path.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError('Private configuration cannot use reparse paths')


def validate_bundle(bundle):
    if not isinstance(bundle,dict) or set(bundle)!= {'version','runtime','profiles'} or bundle['version']!=1:
        raise ValueError('Unsupported private provider configuration')
    runtime,profiles=bundle['runtime'],bundle['profiles']
    if (not isinstance(runtime,dict) or set(runtime)-RUNTIME_KEYS or
            any(not isinstance(value,str) for value in runtime.values()) or
            not isinstance(profiles,dict) or 'primary' not in profiles or set(profiles)-PROFILE_KEYS.keys()):
        raise ValueError('Invalid private provider configuration fields')
    for text in profiles.values():
        if not isinstance(text,str):
            raise TypeError('Invalid provider credential format')
        lines=text.splitlines()
        if len(lines)!=2 or not lines[0].startswith('api=') or not lines[0][4:].strip() or not lines[1].startswith('model='):
            raise ValueError('Invalid provider credential format')
        models=lines[1][6:].split('/')
        if not models or any(model not in VISUAL_MODELS for model in models):
            raise ValueError('Non-visual model cannot enter the visual provider profile')
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
    profiles={}
    for name,key in PROFILE_KEYS.items():
        if runtime.get(key):
            secret,models=_credential_models(Path(runtime[key]))
            profiles[name]='api='+secret+'\nmodel='+ '/'.join(models)+'\n'
    bundle=validate_bundle({'version':1,'runtime':{key:runtime[key] for key in RUNTIME_KEYS if key in runtime},
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
    targets={name:config_root/('provider-'+name+'.txt') for name in bundle['profiles']}
    for path in targets.values():
        no_links(path)
        if path.exists() and not path.is_file():
            raise ValueError('Provider credential target is not a file')
    # Only approved API fields are imported. Database/identities/domains retain
    # their target values, and no old workstation absolute paths are transferred.
    runtime.update(bundle['runtime'])
    for name,key in PROFILE_KEYS.items():
        if name in targets:
            targets[name].write_text(bundle['profiles'][name],encoding='utf-8')
            runtime[key]=str(targets[name].absolute())
        else:
            runtime.pop(key,None)
    runtime_path.write_text(json.dumps(runtime,ensure_ascii=False,indent=2),encoding='utf-8')
    return True


def build_private_package(source_archive,provider_file,target):
    provider_file,target=Path(provider_file).absolute(),Path(target).absolute()
    no_links(provider_file); no_links(target)
    if target.parent!=provider_file.parent or target==provider_file:
        raise ValueError('Private package must stay in its protected configuration directory')
    provider=provider_file.read_bytes()
    validate_bundle(json.loads(provider))
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
    print('Private provider configuration exported. Keep it out of Git and public artifacts.')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
