"""Run the normal worker for an independent API's source, database and originals.

Use a Windows scheduled task with a startup trigger, restart on failure and
IgnoreNew multiple-instance policy. Credentials stay in the protected JSON file.
"""
import argparse
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger(__name__)


def configure(app_root, config_file):
    app_root, config_file = Path(app_root).resolve(), Path(config_file).resolve()
    config = json.loads(config_file.read_text(encoding='utf-8-sig'))
    required = {'AGICO_KB_DATABASE_URL', 'AGICO_KB_STORAGE_ROOT', 'AGICO_KB_MODEL_CACHE',
                'AGICO_KB_MODEL_OFFLINE', 'AGICO_KB_EXPECTED_MODEL_IDENTITY'}
    if not isinstance(config, dict) or not required <= config.keys():
        raise ValueError('Incomplete instance configuration')
    if any(not key.startswith('AGICO_KB_') or not isinstance(value, str)
           for key, value in config.items()):
        raise ValueError('Invalid instance configuration')
    source = app_root / 'src'
    if (not source.is_dir() or config['AGICO_KB_MODEL_OFFLINE'] != 'true'
            or not config['AGICO_KB_EXPECTED_MODEL_IDENTITY']):
        raise ValueError('Source and provisioned offline model are required')
    originals = Path(config['AGICO_KB_STORAGE_ROOT'])
    if not originals.is_absolute() or not originals.is_dir():
        raise ValueError('Originals directory is unavailable')
    if (originals / 'RESTORE_INCOMPLETE').exists():
        raise ValueError('Restore is incomplete')
    for key in list(os.environ):
        if key.startswith('AGICO_KB_') and key not in config:
            del os.environ[key]
    os.environ.update(config)
    # Parsing and naming spawn new interpreters; sys.path alone is insufficient.
    os.environ['PYTHONPATH'] = str(source)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    sys.path.insert(0, str(source))
    os.chdir(app_root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--log', type=Path, required=True)
    args = parser.parse_args()
    handler = RotatingFileHandler(args.log.resolve(), maxBytes=5 * 1024 * 1024,
                                  backupCount=3, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    for name in ('httpx', 'httpcore'):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        configure(args.app_root, args.config)
    except (OSError, ValueError, TypeError):
        logger.error('Instance worker configuration unavailable; inspect protected config and paths.')
        return 1
    logger.info('Instance worker starting with explicit API instance configuration.')
    sys.argv = ['agico_kb.worker']
    from agico_kb.worker import main as worker_main
    worker_main()
    return 0


if __name__ == '__main__':
    sys.exit(main())
