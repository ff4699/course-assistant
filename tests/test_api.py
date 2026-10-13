import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from playwright.async_api import Error as PlaywrightError
from assistant.app import create_app


HEADERS = {'X-Course-Client': 'local'}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(Path(self.directory.name)))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def test_demo_boots_without_school_browser(self):
        result = self.client.get('/api/state').json()
        self.assertEqual(len(result['catalog']), 9)
        self.assertFalse(result['session']['browser_running'])

    def test_retry_limit_is_ten_default_three_and_persists(self):
        self.assertEqual(self.client.get('/api/state').json()['plan']['retries'], 3)
        for retries in (0, 10):
            self.assertEqual(self.client.put('/api/plan', headers=HEADERS, json={'retries': retries}).status_code, 200)
            self.assertEqual(self.client.get('/api/state').json()['plan']['retries'], retries)
        for retries in (-1, 11):
            self.assertEqual(self.client.put('/api/plan', headers=HEADERS, json={'retries': retries}).status_code, 422)

    def test_auto_sync_requires_verified_school_semester(self):
        response = self.client.put('/api/timetable/settings', headers=HEADERS, json={'enabled': True})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.client.get('/api/state?mode=school').json()['sync']['config']['enabled'])

    def test_failed_sync_blocks_clearance_even_with_complete_cached_timetable(self):
        self.client.post('/api/school/import', headers=HEADERS, json={'coverage': 'complete',
            'catalog': [{'id': 'school:a', 'name': '课程', 'schedule_known': True}]})
        self.client.put('/api/plan?mode=school', headers=HEADERS, json={'course_ids': ['school:a']})
        self.client.app.state.sync.meta['state'] = 'waiting_login'
        result = self.client.get('/api/state?mode=school').json()
        self.assertFalse(result['analysis']['can_run'])
        self.assertTrue(any(issue['kind'] == 'freshness' for issue in result['analysis']['issues']))

    def test_school_execution_disabled_and_modes_do_not_mix(self):
        result = self.client.get('/api/state?mode=school').json()
        self.assertEqual(result['catalog'], [])
        self.assertEqual(self.client.post('/api/run/start?mode=school', headers=HEADERS).status_code, 409)

    def test_conflict_plan_cannot_start(self):
        self.client.put('/api/plan', headers=HEADERS, json={'course_ids': ['demo:ai']})
        self.assertEqual(self.client.post('/api/run/start', headers=HEADERS).status_code, 422)

    def test_cross_origin_write_rejected(self):
        self.assertEqual(self.client.post('/api/demo/reset').status_code, 403)
        result = self.client.post('/api/demo/reset', headers={**HEADERS, 'Origin': 'https://external.example'})
        self.assertEqual(result.status_code, 403)

    def test_login_validation_does_not_echo_secret(self):
        result = self.client.post('/api/browser/login', headers=HEADERS,
                                  json={'username': 'test', 'password': 'sensitive-test-value' * 15, 'captcha': 'test'})
        self.assertEqual(result.status_code, 422)
        self.assertNotIn('sensitive-test-value', result.text)

    def test_browser_failure_does_not_echo_upstream_auth_details(self):
        with patch.object(self.client.app.state.school, 'connect', side_effect=PlaywrightError('Authorization: sensitive-test-cookie')):
            result = self.client.post('/api/browser/connect', headers=HEADERS)
        self.assertEqual(result.status_code, 503)
        self.assertNotIn('sensitive-test-cookie', result.text)

    def test_import_unknown_timetable_not_marked_safe(self):
        result = self.client.post('/api/school/import', headers=HEADERS,
                                  json={'catalog': [{'id': 'school:c1', 'name': '课程'}]})
        self.assertEqual(result.status_code, 200)
        self.client.put('/api/plan?mode=school', headers=HEADERS, json={'course_ids': ['school:c1']})
        state = self.client.get('/api/state?mode=school').json()
        self.assertFalse(state['catalog'][0]['schedule_known'])
        self.assertFalse(state['analysis']['can_run'])

    def test_incomplete_existing_list_never_claims_conflict_clearance(self):
        self.client.post('/api/school/import', headers=HEADERS, json={
            'coverage': 'scheduled_only', 'catalog': [{'id': 'school:a', 'name': '课程',
                'meetings': [{'weekday': 2, 'start': 1, 'end': 2, 'weeks': [1]}]}]})
        self.client.put('/api/plan?mode=school', headers=HEADERS, json={'course_ids': ['school:a']})
        result = self.client.get('/api/state?mode=school').json()
        self.assertFalse(result['analysis']['can_run'])
        self.assertTrue(any(issue['kind']=='coverage' for issue in result['analysis']['issues']))

    def test_export_excludes_auth_material(self):
        result = self.client.get('/api/export').json()
        self.assertEqual(set(result), {'catalog', 'existing', 'plan', 'coverage', 'run', 'calendar'})

    def test_unknown_target_and_duplicate_ids_rejected(self):
        self.assertEqual(self.client.put('/api/plan', headers=HEADERS, json={'course_ids': ['missing']}).status_code, 422)
        self.assertEqual(self.client.post('/api/school/import', headers=HEADERS,
                                         json={'catalog': [{'id': 'dup', 'name': '一'}, {'id': 'dup', 'name': '二'}]}).status_code, 422)
