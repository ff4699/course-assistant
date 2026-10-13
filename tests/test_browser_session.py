import shutil
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from assistant.browser import SchoolSession, SemesterParser, catalog_identity, BrowserIssue
from browser_support import browser_available


class SemesterTests(unittest.TestCase):
    def test_options_from_real_page_not_hardcoded(self):
        parser = SemesterParser()
        parser.feed('<select id="allSemesters"><option value="all">全部</option><option value="92">2026-2027-1</option></select>')
        self.assertEqual(parser.semesters, [{'id': '92', 'name': '2026-2027-1'}])

    def test_catalog_key_uses_stable_class_fields(self):
        key = catalog_identity('turn', None, 'ART101', 'ART101-01', '摄影', '张老师', '周一')
        other = catalog_identity('turn', None, 'ART101', 'ART101-02', '摄影', '张老师', '周二')
        self.assertNotEqual(key, other)
        first = catalog_identity('turn', 'backend-1', 'ART101', '', '摄影', '张老师', '周一')
        second = catalog_identity('turn', 'backend-2', 'ART101', '', '摄影', '张老师', '周一')
        self.assertNotEqual(first, second)

    def test_ambiguous_class_without_identity_rejected(self):
        with self.assertRaises(BrowserIssue):
            catalog_identity('turn', None, 'ART101', '', '摄影', '张老师', '')


class FixtureHandler(BaseHTTPRequestHandler):
    # A normal local test login creates its own Cookie. Never a school credential.
    def do_GET(self):
        if self.path == '/normal-login':
            self.send_response(200)
            self.send_header('Set-Cookie', 'fixture_session=normal-local-login; Max-Age=3600; HttpOnly; Path=/')
            body = b'local login completed'
        elif self.path == '/student/home' and not self.server.expired and 'fixture_session=normal-local-login' in self.headers.get('Cookie', ''):
            self.send_response(200)
            body = '<html><title>首页</title><a href="/student/logout-emitter">退出</a></html>'.encode()
        else:
            self.send_response(200)
            body = b'<html><form id="login">login required</form></html>'
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class TimetableFixtureHandler(FixtureHandler):
    def do_GET(self):
        self.server.requests.append(self.path)
        content_type = 'application/json'
        if self.path == '/student/for-std/course-table':
            content_type = 'text/html'
            payload = '''<select id="semesters"><option value="92" selected>2026-2027-1</option></select>
            <script>fetch('/student/for-std/course-table/get-data?semesterId=92&dataId=local-fixture')</script>'''
        elif self.path.startswith('/student/for-std/course-table/get-data?'):
            payload = {'lessons': [
                {'id': 11, 'code': 'CLASS01', 'course': {'nameZh': '本地已排课'},
                 'teacherAssignmentList': [{'person': {'nameZh': '本地教师'}}]},
                {'id': 12, 'code': 'CLASS02', 'course': {'nameZh': '本地未排课'}}]}
        elif self.path == '/student/for-std/course-table/semester/92/print-data':
            payload = {'studentTableVms': [{'activities': [{'lessonId': 11, 'courseName': '本地已排课',
                'weekday': 1, 'startUnit': 1, 'endUnit': 2, 'weekIndexes': [1, 3, 5]}],
                'timeTableLayout': {'courseUnitList': [{'indexNo': 1, 'startTime': 935, 'endTime': 1020}]}}]}
        elif self.path == '/student/ws/semester/get/92':
            payload = {'startDate': '2026-08-31'}
        else:
            return super().do_GET()
        self.send_response(200)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.end_headers()
        self.wfile.write((payload if isinstance(payload, str) else json.dumps(payload)).encode())


@unittest.skipUnless(browser_available(), '需要本机 Chromium 的持久化 Profile 测试')
class PersistentBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_timetable_page_response_import_and_visual_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            server = ThreadingHTTPServer(('127.0.0.1', 0), TimetableFixtureHandler)
            server.expired, server.requests = False, []
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f'http://127.0.0.1:{server.server_port}'
            school = SchoolSession(Path(directory))
            try:
                with patch('assistant.browser.BASE', base), patch('assistant.browser.HOSTS', {'127.0.0.1'}):
                    await school._ensure()
                    await school.context.request.get(base + '/normal-login')
                    await school.page.goto(base + '/selection-fixture')
                    original_page = school.page
                    semesters = await school.semesters()
                    self.assertEqual(semesters[0]['id'], '92')
                    self.assertEqual(school.current_semester, '92')
                    result = await school.schedule('92')
                    self.assertEqual(result['coverage'], 'complete')
                    self.assertEqual(len(result['courses']), 2)
                    self.assertEqual(result['courses'][0].meetings[0].weeks, [1, 3, 5])
                    self.assertEqual(result['courses'][0].teachers, ['本地教师'])
                    self.assertFalse(result['courses'][1].schedule_known)
                    self.assertEqual(result['calendar']['time_slots'][0]['start'], '09:35')
                    self.assertIs(school.page, original_page)
                    self.assertTrue(school.page.url.endswith('/selection-fixture'))
                    frame = await school.view('timetable')
                    self.assertTrue(frame.startswith(b'\x89PNG'))
                    self.assertEqual(server.requests.count('/normal-login'), 1)
                    with self.assertRaises(BrowserIssue):
                        await school.schedule('777')
            finally:
                await school.close()
                server.shutdown()
                server.server_close()

    async def test_reuse_restart_restore_and_expiry(self):
        directory = tempfile.TemporaryDirectory()
        server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
        server.expired = False
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'
        first = SchoolSession(Path(directory.name))
        second = None
        try:
            with patch('assistant.browser.BASE', base):
                await first._ensure()
                context = first.context
                await first._ensure()
                self.assertIs(first.context, context)
                self.assertFalse(await first._student_authenticated())
                await first.context.request.get(base + '/normal-login')
                self.assertTrue(await first._student_authenticated())
                await first.close()
                self.assertTrue(first.profile.exists())
                second = SchoolSession(Path(directory.name))
                await second._ensure()
                self.assertTrue(await second._student_authenticated())
                server.expired = True
                self.assertFalse(await second._student_authenticated())
        finally:
            await first.close()
            if second: await second.close()
            server.shutdown()
            server.server_close()
            directory.cleanup()
