import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse
from unittest.mock import patch
from assistant.browser import SchoolSession, BrowserIssue
from test_browser_session import FixtureHandler
from browser_support import browser_available


COURSE_TABLE = '''<table><thead><tr><th>课程名称</th><th>课程代码</th><th>教学班代码</th>
<th>授课教师</th><th>上课时间</th><th>学分</th><th>已选/上限</th><th>操作</th></tr></thead>
<tbody><tr data-lesson-id="31"><td>本地测试选修课</td><td>ART31</td><td>ART31-01</td>
<td>本地教师</td><td>1-16周 星期二 7-8节</td><td>2</td><td>28/30</td>
<td><button onclick="window.enrollClicks=(window.enrollClicks||0)+1">选课</button></td></tr></tbody></table>'''


class SelectionFixture(FixtureHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        self.server.requests.append(path)
        if path == '/student/for-std/course-select':
            self.send_response(302)
            self.send_header('Location', '/course-selection/entry/' + self.server.scenario)
            self.end_headers()
            return
        if path.endswith('/catalog'):
            self.server.catalog_requests += 1
            body = COURSE_TABLE if self.server.scenario != 'empty' else '''<table><thead><tr>
            <th>课程名称</th><th>操作</th></tr></thead><tbody><tr><td colspan="2">暂无数据</td></tr></tbody></table>'''
            if self.server.scenario == 'school-shape':
                body=(Path(__file__).parent/'fixtures/cupk_selection_columns.html').read_text(encoding='utf-8')
            elif self.server.scenario == 'split':
                head, tail = COURSE_TABLE.split('</thead>')
                body = '<div class="el-table">' + head + '</thead></table><table>' + tail + '</div>'
            elif self.server.scenario == 'grid':
                body = COURSE_TABLE.replace('<table>', '<div role="grid">').replace('</table>', '</div>').replace('<thead>', '').replace('</thead>', '').replace('<tbody>', '').replace('</tbody>', '').replace('<tr', '<div role="row"').replace('</tr>', '</div>').replace('<th>', '<span role="columnheader">').replace('</th>', '</span>').replace('<td>', '<span role="gridcell">').replace('</td>', '</span>')
            elif self.server.scenario == 'iframe' and '/inner/' not in path:
                body = '<iframe style="width:1200px;height:300px" src="/course-selection/turn/local/inner/catalog"></iframe>'
            elif self.server.scenario.startswith('paged'):
                body = '<div data-semester-id="92" id="scope" data-batch-id="local"></div><div id="list"></div>' + '''<div class="el-pagination"><span id="total">共3条</span><ul class="el-pager"><li class="number active" id="page">1</li></ul><button class="btn-next" onclick="nextPage()">下一页</button></div><script>
                let number=1;const template=''' + json.dumps(COURSE_TABLE) + ''';
                const scenario=''' + json.dumps(self.server.scenario) + ''';
                function draw(){document.getElementById('list').innerHTML=template.replace('data-lesson-id="31"','data-lesson-id="'+(30+number)+'"').replace('ART31-01','ART31-0'+number);document.getElementById('page').textContent=number;document.querySelector('.btn-next').disabled=number===3;}
                function nextPage(){setTimeout(()=>{number++;if(scenario==='paged-scope')document.getElementById('scope').dataset.batchId='other';draw();},150);}
                if(scenario==='paged-mismatch')document.getElementById('total').textContent='共4条';draw();</script>'''
        elif path.startswith('/course-selection/entry/'):
            scenario = self.server.scenario
            if scenario == 'disabled':
                body = '<button disabled>开始选课</button>'
            elif scenario == 'multiple':
                body = '<button>开始选课</button><button>开始选课</button>'
            elif scenario == 'none':
                body = '<h2>暂无选课批次</h2>'
            elif scenario == 'spa':
                body = '<div id="content"><button onclick="enter()">开始选课</button></div>' + '''<script>
                function enter(){window.enterClicks=(window.enterClicks||0)+1;
                document.getElementById('content').innerHTML='<table><thead><tr><th>课程名称</th><th>操作</th></tr></thead><tbody><tr><td>加载中</td></tr></tbody></table>';
                setTimeout(()=>{document.getElementById('content').innerHTML=''' + json.dumps(COURSE_TABLE) + '''},300);}
                </script>'''
            else:
                target = ' target="_blank"' if scenario == 'popup' else ''
                body = '<button style="display:none">开始选课</button><a' + target + ' href="/course-selection/turn/local/catalog">开始选课</a>'
        else:
            return super().do_GET()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body.encode())


@unittest.skipUnless(browser_available(), '需要 Chromium 的入口浏览器测试')
class SelectionEntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.school = SchoolSession(Path(self.directory.name))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), SelectionFixture)
        self.server.expired = False
        self.server.scenario = 'link'
        self.server.requests, self.server.catalog_requests = [], 0
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.patches = [patch('assistant.browser.BASE', self.base), patch('assistant.browser.HOSTS', {'127.0.0.1'})]
        for p in self.patches: p.start()
        await self.school._ensure()
        await self.school.context.request.get(self.base + '/normal-login')
        self.context = self.school.context

    async def asyncTearDown(self):
        await self.school.close()
        for p in reversed(self.patches): p.stop()
        self.server.shutdown()
        self.server.server_close()
        self.directory.cleanup()

    async def assert_catalog_and_reuse(self):
        courses = await self.school.read_catalog()
        self.assertEqual(len(courses), 1)
        self.assertEqual(courses[0].lesson_id, '31')
        self.assertEqual(courses[0].remaining, 2)
        self.assertEqual(courses[0].teachers, ['本地教师'])
        self.assertTrue(self.school.status['selection_ready'])
        self.assertEqual(self.school.status['selection_stage'], 'catalog')
        self.assertIs(self.school.context, self.context)
        self.assertEqual(await self.school.page.evaluate('window.enrollClicks||0'), 0)
        current_url = self.school.page.url
        entry_requests = self.server.requests.count('/student/for-std/course-select')
        catalog_requests = self.server.catalog_requests
        session_requests = self.server.requests.count('/student/home')
        await self.school.read_catalog()
        self.assertEqual(self.school.page.url, current_url)
        self.assertEqual(self.server.requests.count('/student/home') - session_requests, 1)
        self.assertEqual(self.server.requests.count('/student/for-std/course-select'), entry_requests)
        self.assertEqual(self.server.catalog_requests, catalog_requests)
        self.assertIs(self.school.context, self.context)

    async def test_start_link_clicked_before_read_and_existing_list_reused(self):
        result = await self.school.connect()
        self.assertTrue(result['selection_ready'])
        self.assertIn('enter_selection', [s['stage'] for s in result['activity']['steps']])
        self.assertEqual(self.server.catalog_requests, 1)
        await self.assert_catalog_and_reuse()
        self.assertEqual(self.server.catalog_requests, 1)

    async def test_same_page_start_waits_for_course_rows(self):
        self.server.scenario = 'spa'
        await self.assert_catalog_and_reuse()
        self.assertEqual(await self.school.page.evaluate('window.enterClicks'), 1)

    async def test_start_popup_adopted_in_same_context(self):
        self.server.scenario = 'popup'
        origin = self.school.page
        await self.assert_catalog_and_reuse()
        self.assertIsNot(self.school.page, origin)
        self.assertFalse(origin.is_closed())
        self.assertEqual(self.server.catalog_requests, 1)

    async def test_disabled_start_pauses_without_marking_list_ready(self):
        self.server.scenario = 'disabled'
        result = await self.school.connect()
        self.assertEqual(result['state'], 'connected')
        self.assertFalse(result['selection_ready'])
        self.assertEqual(result['activity']['state'], 'waiting_user')
        with self.assertRaisesRegex(BrowserIssue, '暂不可用'):
            await self.school.read_catalog()
        self.assertEqual(self.server.catalog_requests, 0)

    async def test_multiple_start_buttons_require_a_batch_choice(self):
        self.server.scenario = 'multiple'
        with self.assertRaisesRegex(BrowserIssue, '多个.*开始选课'):
            await self.school.read_catalog()
        self.assertFalse(self.school.status['selection_ready'])
        self.assertEqual(self.server.catalog_requests, 0)

    async def test_no_batch_returns_an_explicit_waiting_state(self):
        self.server.scenario = 'none'
        result = await self.school.connect()
        self.assertFalse(result['selection_ready'])
        self.assertEqual(result['selection_stage'], 'not_open')
        self.assertIn('没有可用选课批次', result['message'])

    async def test_empty_course_list_is_entered_but_not_saved_as_catalog(self):
        self.server.scenario = 'empty'
        with self.assertRaisesRegex(BrowserIssue, '没有可选课程'):
            await self.school.read_catalog()
        self.assertEqual(self.school.status['selection_stage'], 'empty')
        self.assertEqual(self.server.catalog_requests, 1)

    async def test_split_header_and_body_share_detection_and_reader(self):
        self.server.scenario = 'split'
        await self.assert_catalog_and_reuse()

    async def test_sanitized_actual_school_merged_columns_are_detected(self):
        self.server.scenario='school-shape'
        courses=await self.school.read_catalog()
        self.assertEqual(len(courses),2)
        self.assertTrue(self.school.status['selection_ready'])
        self.assertTrue(all(c.schedule_known for c in courses))
        self.assertTrue(all(c.credits==2 for c in courses))
        self.assertEqual(len(courses[0].meetings),2)
        self.assertNotEqual(courses[0].id,courses[1].id)

    async def test_accessible_grid_share_detection_and_reader(self):
        self.server.scenario = 'grid'
        await self.assert_catalog_and_reuse()

    async def test_visible_selection_iframe_is_read_in_same_context(self):
        self.server.scenario = 'iframe'
        await self.assert_catalog_and_reuse()
        self.assertEqual(self.school.catalog_snapshot['evidence'], 'iframe_dom')

    async def test_manual_navigation_refreshes_cached_state_without_entry_click(self):
        await self.school._student_authenticated()
        self.school._set('connected', '等待进入', selection_ready=False)
        await self.school.page.goto(self.base + '/course-selection/turn/local/catalog')
        snapshot = await self.school.refresh_selection(force=True)
        self.assertEqual(len(snapshot['courses']), 1)
        self.assertTrue(self.school.public_status()['selection_ready'])
        self.assertEqual(self.server.requests.count('/student/for-std/course-select'), 0)
        self.assertIs(self.context, self.school.context)

    async def test_manual_new_tab_is_adopted_but_timetable_is_not(self):
        await self.school._student_authenticated()
        new_page = await self.context.new_page()
        await new_page.goto(self.base + '/course-selection/turn/local/catalog')
        self.assertIsNot(self.school.page, new_page)
        await self.school.refresh_selection(force=True)
        self.assertIs(self.school.page, new_page)
        await new_page.goto(self.base + '/student/for-std/course-table')
        self.assertIsNone(await self.school.refresh_selection(force=True))
        self.assertFalse(self.school.status['selection_ready'])

    async def test_session_expiry_clears_ready_without_destroying_context(self):
        await self.school.connect()
        self.server.expired = True
        self.school.last_auth_check = 0
        self.assertIsNone(await self.school.refresh_selection(force=True))
        self.assertFalse(self.school.status['selection_ready'])
        self.assertEqual(self.school.status['selection_stage'], 'auth_required')
        self.assertIs(self.school.context, self.context)

    async def test_ambiguous_live_tabs_do_not_merge_batches(self):
        await self.school.connect()
        new_page = await self.context.new_page()
        await new_page.goto(self.base + '/course-selection/turn/other/catalog')
        self.assertIsNone(await self.school.refresh_selection(force=True))
        self.assertEqual(self.school.status['selection_stage'], 'choose_batch')
        self.assertFalse(self.school.status['selection_ready'])

    async def test_count_changes_keep_identity_and_time_is_parsed(self):
        await self.school.connect()
        original = self.school.catalog_snapshot['courses'][0]
        await self.school.page.locator('td').nth(6).evaluate("e=>e.textContent='29/30'")
        changed = (await self.school.refresh_selection(force=True))['courses'][0]
        self.assertEqual(original.id, changed.id)
        self.assertEqual(changed.remaining, 1)
        self.assertTrue(changed.schedule_known)
        self.assertEqual(changed.meetings[0].weekday, 2)
        self.assertEqual(changed.semester, 'unverified')

    async def test_empty_without_verified_batch_does_not_claim_ready(self):
        await self.school._student_authenticated()
        await self.school.page.goto(self.base + '/course-selection/entry/plain')
        await self.school.page.set_content('<table><thead><tr><th>课程名称</th><th>操作</th></tr></thead><tbody><tr><td colspan="2">暂无数据</td></tr></tbody></table>')
        self.assertIsNone(await self.school.refresh_selection(force=True))
        self.assertFalse(self.school.status['selection_ready'])

    async def test_bulk_crawl_reads_all_pages_and_does_not_click_enrollment(self):
        self.server.scenario = 'paged'
        snapshots = []
        result = await self.school.read_all_catalog(lambda s: snapshots.append(s))
        self.assertEqual(result['coverage'], 'complete')
        self.assertEqual(result['pages_read'], 3)
        self.assertEqual(len(result['courses']), 3)
        self.assertEqual({c.page for c in result['courses']}, {1, 2, 3})
        self.assertEqual(len(snapshots), 3)
        self.assertTrue(all(c.semester=='92' for c in result['courses']))
        self.assertIs(self.school.context, self.context)
        self.assertEqual(await self.school.page.evaluate('window.enrollClicks||0'), 0)

    async def test_bulk_crawl_rejects_total_mismatch_and_keeps_read_pages(self):
        self.server.scenario = 'paged-mismatch'
        snapshots = []
        with self.assertRaisesRegex(BrowserIssue, '数量.*总数'):
            await self.school.read_all_catalog(lambda s: snapshots.append(s))
        self.assertEqual(len(snapshots), 3)
        self.assertEqual(self.school.activity.data['state'], 'failed')

    async def test_bulk_crawl_stops_on_batch_change_without_importing_other_batch(self):
        self.server.scenario = 'paged-scope'
        snapshots = []
        with self.assertRaisesRegex(BrowserIssue, '批次.*变化'):
            await self.school.read_all_catalog(lambda s: snapshots.append(s))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]['batch'], 'local')

    async def test_background_observer_imports_all_pages_after_manual_entry(self):
        from assistant.catalog_sync import CatalogSync
        from assistant.store import Store
        import asyncio
        self.server.scenario = 'paged'
        await self.school._student_authenticated()
        await self.school.page.goto(self.base + '/course-selection/turn/local/catalog')
        store = Store(Path(self.directory.name) / 'fixture.sqlite3')
        observer = CatalogSync(store, self.school)
        observer.start()
        try:
            for _ in range(120):
                if observer.meta.get('last_complete'):
                    break
                await asyncio.sleep(0.1)
            self.assertEqual(len(store.get('school:catalog', [])), 3)
            self.assertEqual(observer.meta.get('complete_count'), 3)
            self.assertEqual(self.school.status['selection_stage'], 'catalog')
        finally:
            await observer.close()
            store.close()
