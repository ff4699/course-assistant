import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from assistant.app import create_app
from assistant.models import Course


class CatalogSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(Path(self.temp.name)))
        self.client.__enter__()
        self.sync = self.client.app.state.catalog_sync
        self.store = self.client.app.state.store
        self.school = self.client.app.state.school

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def snapshot(self, ids=('31',), batch='local', semester='92'):
        return {'courses': [Course(id=f'school:catalog:{batch}:{i}', lesson_id=i,
                name='同名课程', teachers=['同一教师'], source='school', batch=batch,
                semester=semester, class_code='CLASS'+i) for i in ids],
                'batch': batch, 'semester': semester, 'evidence': 'page_dom', 'source': 'school_dom'}

    def test_status_poll_automatically_imports_and_exposes_separate_catalog_status(self):
        with patch.object(self.school, 'refresh_selection', AsyncMock(return_value=self.snapshot(('31', '32')))):
            result = self.client.get('/api/state?mode=school').json()
        self.assertEqual(len(result['catalog']), 2)
        self.assertEqual(result['catalog_sync']['current_count'], 2)
        self.assertEqual(result['catalog_sync']['coverage'], 'current_page')
        self.assertNotEqual(result['sync']['state'], 'success')
        self.assertFalse(result['capabilities']['live_submit'])

    def test_partial_refresh_preserves_other_page_choices_and_priority(self):
        self.sync.save_snapshot(self.snapshot(('31', '32')))
        chosen = ['school:catalog:local:32', 'school:catalog:local:31']
        self.store.put('school:plan', {'course_ids': chosen, 'retries': 7})
        newer = self.snapshot(('31', '33'))
        newer['courses'][0].enrolled_count = 29
        newer['courses'][0].capacity = 30
        self.sync.save_snapshot(newer)
        self.assertEqual(len(self.store.get('school:catalog')), 3)
        self.assertEqual(self.store.get('school:plan')['course_ids'], chosen)
        self.assertEqual(self.store.get('school:plan')['retries'], 7)
        self.assertEqual(self.sync.meta['current_count'], 2)

    def test_failed_or_empty_refresh_preserves_snapshot_and_success_time(self):
        self.sync.save_snapshot(self.snapshot())
        old = self.store.get('school:catalog')
        last = self.sync.meta['last_success']
        self.sync.failed('读取失败')
        self.assertEqual(self.store.get('school:catalog'), old)
        empty = self.snapshot(())
        self.sync.save_snapshot(empty)
        self.assertEqual(self.store.get('school:catalog'), old)
        self.assertEqual(self.sync.meta['last_success'], last)
        self.assertEqual(self.sync.meta['state'], 'empty')
        self.sync.save_snapshot(self.snapshot())
        self.assertIn('读取失败', self.sync.meta['message'])
        self.assertEqual(self.sync.meta['last_error'], '读取失败')

    def test_batch_change_is_not_merged_and_plan_is_not_deleted(self):
        self.sync.save_snapshot(self.snapshot())
        self.store.put('school:plan', {'course_ids': ['school:catalog:local:31']})
        self.sync.save_snapshot(self.snapshot(('31',), batch='second'))
        self.assertEqual([c['batch'] for c in self.store.get('school:catalog')], ['second'])
        self.assertEqual(self.store.get('school:plan')['course_ids'], ['school:catalog:local:31'])

    def test_catalog_and_metadata_survive_service_restart(self):
        self.sync.save_snapshot(self.snapshot())
        from assistant.catalog_sync import CatalogSync
        restored = CatalogSync(self.store, self.school)
        self.assertEqual(restored.meta['saved_count'], 1)
        self.assertEqual(len(self.store.get('school:catalog')), 1)

    def test_connect_automatically_imports_before_timetable_work(self):
        async def connected():
            self.school._set('connected', '已打开', selection_ready=True)
            return self.school.public_status()
        with patch.object(self.school, 'connect', connected), \
             patch.object(self.school, 'refresh_selection', AsyncMock(return_value=self.snapshot())), \
             patch.object(self.school, 'semesters', AsyncMock(return_value=[])):
            response = self.client.post('/api/browser/connect', headers={'X-Course-Client': 'local'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['catalog_sync']['state'], 'success')
        self.assertEqual(len(self.store.get('school:catalog')), 1)
