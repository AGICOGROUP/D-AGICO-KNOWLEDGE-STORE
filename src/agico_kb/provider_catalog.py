"""Approved per-platform models, task capabilities and three UI effort levels."""
PRIMARY_MODELS = ('glm-5.3-flashx', 'glm-5.3-flash')
VISUAL_MODELS = (*PRIMARY_MODELS, 'deepseek-v4.1-flash-1017', 'deepseek-v4.1-flash')
MODELS = {'primary': PRIMARY_MODELS, 'fallback': VISUAL_MODELS}
PLATFORMS = {'primary': 'bigmodel.cn', 'fallback': 'cephalon.cloud'}
EFFORTS = {'low': 'low', 'medium': 'high', 'high': 'max'}


def ordered_models(profile, selected):
    # Preserve the selected supported model during upgrades; retire the old alias.
    if selected == 'deepseek-v4-flash':
        selected = 'deepseek-v4.1-flash'
    selected = selected if selected in MODELS[profile] else MODELS[profile][0]
    return (selected, *(item for item in MODELS[profile] if item != selected))


def reasoning_effort(path):
    if path and path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines()[2:]:
            if line.startswith('reasoning_effort='):
                value = line.split('=', 1)[1]
                if value not in EFFORTS:
                    raise ValueError('Unsupported stored reasoning level')
                return value
    return 'low'


def request_effort(path):
    # GLM 5.3 / DeepSeek expose low, high, max. Map to three distinct UI tiers.
    return EFFORTS[reasoning_effort(path)]
