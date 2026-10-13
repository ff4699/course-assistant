"""Local consent tied to the exact shipped document, before app initialization."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

AGREEMENT_VERSION = '1.0'
RESOURCE_ROOT = Path(__file__).resolve().parents[1]


def agreement_text():
    return (RESOURCE_ROOT / 'docs' / 'USER_AGREEMENT.md').read_text(encoding='utf-8')


def agreement_hash():
    return hashlib.sha256(agreement_text().encode('utf-8')).hexdigest()


def has_consent(state_dir):
    try:
        record = json.loads((Path(state_dir) / 'consent.json').read_text(encoding='utf-8'))
        return (record['version'] == AGREEMENT_VERSION
                and record['document_sha256'] == agreement_hash()
                and datetime.fromisoformat(record['accepted_at']).tzinfo is not None)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def save_consent(state_dir):
    folder = Path(state_dir)
    folder.mkdir(parents=True, exist_ok=True)
    record = {'version': AGREEMENT_VERSION, 'document_sha256': agreement_hash(),
              'accepted_at': datetime.now(timezone.utc).isoformat()}
    temporary = folder / 'consent.json.tmp'
    temporary.write_text(json.dumps(record, indent=2), encoding='utf-8')
    temporary.replace(folder / 'consent.json')


def disable_password_storage(profile):
    """Merge only password/autofill preferences, leaving authentication intact.

    Call before launching the owning Chromium, never while its Profile is open.
    """
    path = Path(profile) / 'Default' / 'Preferences'
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    settings['credentials_enable_service'] = False
    settings['credentials_enable_autosignin'] = False
    settings.setdefault('profile', {})['password_manager_enabled'] = False
    settings.setdefault('autofill', {}).update(profile_enabled=False, credit_card_enabled=False)
    temporary = path.with_suffix('.cupk.tmp')
    temporary.write_text(json.dumps(settings), encoding='utf-8')
    temporary.replace(path)
