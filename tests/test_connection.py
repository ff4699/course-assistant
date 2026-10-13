import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from pydantic import ValidationError
from assistant.app import create_app
from assistant.connection import ConnectionSettings, load_connection


SETTINGS = {'base_url':'https://eams.example.test','login_url':'https://login.example.test/auth',
            'portal_url':'https://portal.example.test'}


class ConnectionTests(unittest.TestCase):
    def test_default_does_not_connect_to_any_institution(self):
        with tempfile.TemporaryDirectory() as temp, TestClient(create_app(Path(temp))) as client:
            self.assertEqual(client.get('/api/browser/settings').json()['base_url'],'')
            school=client.app.state.school
            with patch.object(school,'_ensure',new_callable=AsyncMock) as launch:
                result=client.post('/api/browser/connect',headers={'X-Course-Client':'local'}).json()
                self.assertIn('连接设置',result['message'])
                launch.assert_not_called()

    def test_validated_local_settings_survive_restart_without_touching_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with TestClient(create_app(root)) as client:
                profile=root/'browser-profile';(profile/'test-session').write_text('keep')
                self.assertEqual(client.put('/api/browser/settings',json=SETTINGS).status_code,403)
                result=client.put('/api/browser/settings',json=SETTINGS,headers={'X-Course-Client':'local'})
                self.assertEqual(result.status_code,200)
                self.assertEqual(client.app.state.school.count_host,'eams.example.test')
                self.assertEqual((profile/'test-session').read_text(),'keep')
                school=client.app.state.school
                school.context=object();school.context_closed=False
                self.assertEqual(client.put('/api/browser/settings',json=SETTINGS,headers={'X-Course-Client':'local'}).status_code,409)
                school.context=None
            self.assertEqual(load_connection(root).model_dump(),SETTINGS)

    def test_invalid_or_credential_containing_urls_rejected(self):
        for base in ['http://example.test','https://user:password@example.test','https://example.test/student',
                     'https://example.test?token=secret','https://example.test/#fragment']:
            with self.subTest(base=base),self.assertRaises(ValidationError):
                ConnectionSettings(**{**SETTINGS,'base_url':base})
