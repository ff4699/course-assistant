import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from assistant.consent import has_consent, save_consent, disable_password_storage


class ConsentTests(unittest.TestCase):
    def test_missing_corrupt_changed_or_wrong_version_requires_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            self.assertFalse(has_consent(folder))
            save_consent(folder)
            self.assertTrue(has_consent(folder))
            path = folder / 'consent.json'
            record = json.loads(path.read_text())
            self.assertEqual(set(record), {'version', 'document_sha256', 'accepted_at'})
            with patch('assistant.consent.agreement_text', return_value='changed privacy terms'):
                self.assertFalse(has_consent(folder))
            record['version'] = 'obsolete'
            path.write_text(json.dumps(record))
            self.assertFalse(has_consent(folder))
            path.write_text('{bad')
            self.assertFalse(has_consent(folder))

    def test_preferences_keep_authentication_and_disable_password_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            profile = folder / 'profile'
            path = profile / 'Default/Preferences'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'unrelated': {'preserve': True}, 'profile': {'name': 'test'}}))
            cookies = profile / 'Default/Cookies'
            cookies.write_bytes(b'fictional-cookie-fixture')
            disable_password_storage(profile)
            result = json.loads(path.read_text())
            self.assertFalse(result['credentials_enable_service'])
            self.assertFalse(result['profile']['password_manager_enabled'])
            self.assertFalse(result['autofill']['credit_card_enabled'])
            self.assertEqual(result['profile']['name'], 'test')
            self.assertTrue(result['unrelated']['preserve'])
            self.assertEqual(cookies.read_bytes(), b'fictional-cookie-fixture')

    def test_frozen_app_fails_before_creating_state_without_consent(self):
        from assistant.app import create_app
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / 'not-created'
            with patch('sys.frozen', True, create=True):
                with self.assertRaises(RuntimeError):
                    create_app(folder)
            self.assertFalse(folder.exists())
