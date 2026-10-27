"""Write-only provider keys. No credential retrieval or historical versions."""
import os
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from . import accounts
from .errors import KBError
from .provider_config import no_links
from .provider_catalog import MODELS, PLATFORMS, EFFORTS, ordered_models, reasoning_effort as read_effort, request_effort

Profile = Literal['primary', 'fallback']
ENDPOINTS = {'primary': 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
             'fallback': 'https://cephalon.cloud/user-center/v1/model/chat/completions'}


class Replacement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    api_key: SecretStr = Field(min_length=8, max_length=4096)

    @field_validator('api_key', mode='before')
    @classmethod
    def safe_key(cls, value):
        if not isinstance(value, str) or not value.strip() or any(ord(c) < 33 or ord(c) > 126 for c in value.strip()):
            raise ValueError('密钥不能为空，且不能包含空格、换行或控制字符。')
        return value.strip()


class ModelSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str = Field(min_length=1, max_length=80)
    reasoning_effort: Literal['low','medium','high'] | None = None


def directory(settings):
    folder = settings.provider_keys_directory.absolute()
    no_links(folder)
    source = Path(__file__).resolve().parents[2]
    if folder.is_relative_to(settings.storage_root.absolute()) or folder.is_relative_to(source):
        raise ValueError('API keys must be stored outside source and original-file storage')
    return folder


def _legacy(settings, profile):
    return settings.vision_config_file if profile == 'primary' else settings.vision_fallback_config_file


def configured_models(settings, profile):
    metadata = directory(settings) / 'models.json'
    if metadata.is_file():
        no_links(metadata)
        models = json.loads(metadata.read_text(encoding='utf-8')).get(profile)
        if isinstance(models, list) and models and all(model in {'glm-5.3-flash', 'glm-5.3-flashx'} for model in models):
            return tuple(dict.fromkeys(models))
    return MODELS[profile]


def _write(settings, profile, key, models, *, reasoning_effort=None):
    from .credential_crypto import encrypt
    folder = directory(settings)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / (profile + '.txt')
    no_links(target)
    effort = reasoning_effort if reasoning_effort is not None else read_effort(target)
    if effort not in EFFORTS:
        raise ValueError('Unsupported reasoning level')
    encrypted = encrypt(target, key)
    handle, name = tempfile.mkstemp(prefix='.' + profile + '-', dir=folder)
    temporary = Path(name)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write('api=' + encrypted + '\nmodel=' + '/'.join(models) + '\nreasoning_effort=' + effort + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def supported_models(settings, profile):
    return MODELS[profile]


def select_model(settings, profile, model, *, reasoning_effort=None):
    from .vision import _credential_models
    if profile not in MODELS or model not in supported_models(settings, profile):
        raise ValueError('Unsupported model')
    if reasoning_effort is not None and reasoning_effort not in EFFORTS:
        raise ValueError('Unsupported reasoning level')
    path = settings.provider_profile(profile)
    if not path:
        raise ValueError('Configure the provider key before choosing a model')
    key, models = _credential_models(path)
    if (models != ordered_models(profile, model) or
            reasoning_effort is not None and reasoning_effort != read_effort(path)):
        return _write(settings, profile, key, ordered_models(profile, model), reasoning_effort=reasoning_effort)
    return path


def replace_key(settings, profile, key):
    from .vision import _credential_models
    if profile not in MODELS:
        raise ValueError('Unknown API profile')
    legacy = _legacy(settings, profile)
    canonical = directory(settings) / (profile + '.txt')
    if legacy and legacy.is_file() and legacy.absolute() != canonical:
        raise ValueError('Legacy keys require the one-time operator migration before editing')
    models = configured_models(settings, profile)
    if canonical.exists():
        _, models = _credential_models(canonical)
    return _write(settings, profile, key, ordered_models(profile, models[0]))


def migrate_legacy(settings):
    """Operator-only upgrade: preserve one active value, remove the old live file."""
    from .vision import _credential_models
    for profile in MODELS:
        source = _legacy(settings, profile)
        target = directory(settings) / (profile + '.txt')
        if not source or not source.is_file() or source.absolute() == target:
            continue
        no_links(source)
        if source.absolute().is_relative_to(settings.storage_root.absolute()):
            raise ValueError('Refusing to migrate credentials from original-file storage')
        if not target.exists():
            key, models = _credential_models(source)
            _write(settings, profile, key, models)
        _credential_models(target)  # Do not remove the old file unless the active profile is valid.
        source.unlink()
    # Canonical files created by the previous version are upgraded in place.
    from .credential_crypto import PREFIX
    for profile in MODELS:
        target = directory(settings) / (profile + '.txt')
        if target.is_file():
            key, models = _credential_models(target)
            content = target.read_text(encoding='utf-8-sig')
            if (not content.startswith('api=' + PREFIX) or models != ordered_models(profile, models[0])
                    or 'reasoning_effort=' not in content):
                _write(settings, profile, key, ordered_models(profile, models[0]))
    from .credential_crypto import _cipher
    directory(settings).mkdir(parents=True, exist_ok=True, mode=0o700)
    _cipher(directory(settings)/'primary.txt', create=True)


def status(settings):
    from .vision import _credential_models
    from . import provider_health
    profiles = {}
    for name in MODELS:
        path = settings.provider_profile(name)
        configured = False
        models = supported_models(settings, name)
        preferred = models[0]
        paused = {}
        switched_until = None
        masked = ''
        if path:
            try:
                key, ordered = _credential_models(path)
                masked = key[:3] + '*'*12 + key[-4:] if len(key) >= 12 else '*'*12
                preferred = ordered[0]
                configured = True
                paused = provider_health.cooldowns(settings, provider_health.scope(path, ENDPOINTS[name]))
                if name == 'primary':
                    switched_until = provider_health.failover_until(settings, provider_health.scope(path, ENDPOINTS[name]))
            except (OSError, ValueError):
                pass
        eligible = tuple(dict.fromkeys((preferred, *models)))
        active = next((model for model in eligible if model not in paused), None) if configured else None
        if switched_until:
            active = None
        until = switched_until or paused.get(preferred)
        profiles[name] = {'configured': configured, 'updated_at':
            datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if configured else None,
            'encrypted': bool(configured and 'api=encrypted-fernet:' in path.read_text(encoding='utf-8-sig')),
            'masked_key': masked, 'platform': PLATFORMS[name],
            'reasoning_effort': read_effort(path),
            'models': list(models), 'selected_model': preferred,
            'active_model': active, 'temporary_until': datetime.fromtimestamp(until, timezone.utc).isoformat() if until else None}
    return {'profiles': profiles}


def developer(conn, request):
    principal = accounts.require_current_session(conn, request)
    if principal.account_role != 'superadmin':
        raise KBError('FORBIDDEN', '只有开发人员可以配置 API。', 403)
    return principal


def provider_request(method, url, **kwargs):
    with httpx.Client(timeout=httpx.Timeout(12, connect=5), follow_redirects=False, trust_env=False) as client:
        return client.request(method, url, **kwargs)


def probe(profile, key, settings):
    from .vision import _credential_models
    model = configured_models(settings, profile)[0]
    configured = settings.provider_profile(profile)
    if configured:
        try:
            _, models = _credential_models(configured)
            model = models[0]
        except (OSError, ValueError):
            pass
    try:
        response = provider_request('POST', ENDPOINTS[profile], headers={'Authorization': 'Bearer ' + key},
            json={'model': model, 'reasoning_effort': request_effort(configured), 'thinking': {'type':'enabled'},
                  'messages': [{'role': 'user', 'content': 'Reply OK.'}], 'max_tokens': 128})
        if response.status_code == 200 and isinstance(response.json().get('choices'), list) and response.json()['choices']:
            return {'ok': True, 'message': '连接成功，当前模型可调用。'}
    except (httpx.HTTPError, ValueError, AttributeError):
        pass
    return {'ok': False, 'message': '连接未通过，请检查密钥、线路余额或稍后重试。'}


def secure_transport(request):
    if request.url.scheme != 'https' and request.url.hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise KBError('HTTPS_REQUIRED', '请通过 HTTPS 或本机入口配置密钥。', 400)


def reply(value):
    return JSONResponse(value, headers={'Cache-Control': 'no-store'})


def router(db):
    routes = APIRouter()

    @routes.get('/v1/settings/api-keys')
    def current(request: Request):
        with db.connection() as conn:
            developer(conn, request)
            return reply(status(db.settings))

    @routes.put('/v1/settings/api-keys/{profile}')
    def replace(profile: Profile, body: Replacement, request: Request):
        secure_transport(request)
        with db.connection(write=True) as conn:
            accounts.management_actor(conn, request)
            principal = developer(conn, request)
            try:
                replace_key(db.settings, profile, body.api_key.get_secret_value())
            except (OSError, ValueError):
                raise KBError('API_KEY_SAVE_FAILED', '密钥未保存，请联系维护人员检查配置目录。', 503) from None
            accounts.audit(conn, principal.id, principal.id, 'api_key_replaced', profile=profile)
            return reply(status(db.settings))

    @routes.post('/v1/settings/api-keys/{profile}/test')
    def test(profile: Profile, request: Request, body: Replacement | None = None):
        secure_transport(request)
        with db.connection() as conn:
            developer(conn, request)
        if body is None:
            from .vision import _credentials
            path = db.settings.provider_profile(profile)
            if not path:
                raise KBError('API_KEY_REQUIRED', '请先设置该线路的密钥。', 422)
            try:
                key, _ = _credentials(path)
            except (OSError, ValueError):
                raise KBError('API_CONFIG_UNAVAILABLE', '该线路配置暂不可读，请联系维护人员检查。', 503) from None
        else:
            key = body.api_key.get_secret_value()
        result = probe(profile, key, db.settings)
        with db.connection() as conn:
            developer(conn, request)
        return reply(result)

    @routes.put('/v1/settings/api-keys/{profile}/model')
    def choose_model(profile: Profile, body: ModelSelection, request: Request):
        secure_transport(request)
        with db.connection(write=True) as conn:
            accounts.management_actor(conn, request)
            principal = developer(conn, request)
            try:
                select_model(db.settings, profile, body.model, reasoning_effort=body.reasoning_effort)
            except ValueError:
                raise KBError('INVALID_MODEL', '请选择该线路已支持的模型，并先保存密钥。', 422) from None
            except OSError:
                raise KBError('MODEL_SAVE_FAILED', '模型未保存，请稍后重试。', 503) from None
            accounts.audit(conn, principal.id, principal.id, 'api_model_selected', profile=profile, model=body.model,
                           reasoning_effort=read_effort(db.settings.provider_profile(profile)))
            return reply(status(db.settings))

    return routes


def main():
    """One-time operator upgrade, without printing or exporting credentials."""
    import argparse
    import sys
    from .config import Settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--migrate', action='store_true', required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    args = parser.parse_args()
    try:
        runtime_path = args.runtime.absolute()
        no_links(runtime_path)
        runtime = json.loads(runtime_path.read_text(encoding='utf-8-sig'))
        settings = Settings('operator-only', Path(runtime['AGICO_KB_STORAGE_ROOT']),
            api_keys_dir=Path(runtime.get('AGICO_KB_API_KEYS_DIR', runtime_path.parent / 'api-keys')),
            vision_config_file=Path(runtime["AGICO_KB_VISION_CONFIG_FILE"]) if runtime.get("AGICO_KB_VISION_CONFIG_FILE") else None,
            vision_fallback_config_file=Path(runtime["AGICO_KB_VISION_FALLBACK_CONFIG_FILE"]) if runtime.get("AGICO_KB_VISION_FALLBACK_CONFIG_FILE") else None)
        for profile in MODELS:
            source = _legacy(settings, profile)
            if source and source.is_file() and source.absolute().parent not in {runtime_path.parent, directory(settings)}:
                raise ValueError('Refusing to move credentials owned by another installation')
        directory(settings).mkdir(parents=True, exist_ok=True, mode=0o700)
        migrate_legacy(settings)
        runtime['AGICO_KB_API_KEYS_DIR'] = str(directory(settings))
        for profile, field in [('primary','AGICO_KB_VISION_CONFIG_FILE'),('fallback','AGICO_KB_VISION_FALLBACK_CONFIG_FILE')]:
            target = directory(settings) / (profile + '.txt')
            if target.is_file():
                runtime[field] = str(target)
            else:
                runtime.pop(field, None)
        # Runtime file already has an operator-only ACL: preserve it in place.
        runtime_path.write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        print('API key migration failed; configuration values withheld.', file=sys.stderr)
        return 1
    print('API key storage prepared. No account or document data changed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
