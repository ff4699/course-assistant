import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from assistant.activity import Activity
from assistant.browser import BrowserIssue, SchoolSession
from assistant.calendar import default_calendar, merge_calendar
from assistant.models import Course, Meeting, Plan, analyze_plan
from assistant.schedule import normalize_print_data, parse_schedule_text, merge_all_course_rows, merge_schedule_sources
from assistant.store import Store
from assistant.sync import TimetableSync


class TimetableTests(unittest.TestCase):
    def test_official_image_times_and_server_override(self):
        calendar = default_calendar()
        self.assertEqual(len(calendar['time_slots']), 12)
        self.assertEqual(calendar['time_slots'][0], {'number': 1, 'start': '09:30', 'end': '10:15'})
        self.assertEqual(calendar['time_slots'][5]['start'], '16:00')
        self.assertEqual(calendar['time_slots'][-1]['end'], '22:55')
        self.assertEqual(default_calendar('2026-2027-2')['start_date'], '2027-03-01')
        updated = merge_calendar({'startDate': '2026-09-07', 'endDate': '2027-01-17'}, [
            {'indexNo': 1, 'startTime': 945, 'endTime': 1030},
            {'indexNo': 2, 'startTime': 2500, 'endTime': 2600}])
        self.assertEqual(updated['start_date'], '2026-09-07')
        self.assertEqual(updated['total_weeks'], 19)
        self.assertEqual(updated['time_slots'][0]['start'], '09:45')
        self.assertEqual(updated['time_slots'][1]['start'], '10:20')

    def test_text_parser_keeps_multiple_segments_and_week_parity(self):
        slots, known = parse_schedule_text('1-16周(单) 星期二 3-4节 教学楼;2,4,6周 星期四 7～8节')
        self.assertTrue(known)
        self.assertEqual(len(slots), 2)
        self.assertEqual(slots[0].weeks, list(range(1, 17, 2)))
        self.assertEqual(slots[1].weeks, [2, 4, 6])
        self.assertEqual((slots[1].weekday, slots[1].start, slots[1].end), (4, 7, 8))
        _, known = parse_schedule_text('星期二 3-4节')
        self.assertFalse(known)
        _, known = parse_schedule_text('1-16周 星期二 3-4节;另外时间待定')
        self.assertFalse(known)

    def test_complete_list_keeps_unknown_and_no_fixed_time_courses(self):
        lessons = [
            {'id': 11, 'code': 'A01', 'course': {'nameZh': '必修课'},
             'teacherAssignmentList': [{'person': {'nameZh': '陈老师'}}],
             'scheduleText': {'dateTimePlacePersonText': {'textZh': '1-16周 星期一 1-2节'}}},
            {'id': 12, 'code': 'B01', 'course': {'nameZh': '尚未排课'}},
            {'id': 13, 'code': 'C01', 'course': {'nameZh': '无固定时间'}, 'noFixedTime': True}]
        result = normalize_print_data({'studentTableVms': [{'lessons': lessons}]}, '62', full_semester=True)
        self.assertEqual(result['coverage'], 'complete')
        self.assertEqual(len(result['courses']), 3)
        a, b, c = result['courses']
        self.assertEqual(a.teachers, ['陈老师'])
        self.assertTrue(a.schedule_known)
        self.assertFalse(b.schedule_known)
        self.assertTrue(c.schedule_known)
        self.assertEqual(c.meetings, [])
        self.assertEqual(normalize_print_data({'studentTableVms': [{'lessons': lessons}]}, '62')['coverage'], 'scheduled_only')

    def test_full_list_missing_id_does_not_claim_complete(self):
        data = {'studentTableVms': [{'allCourses': [{'courseName': '课一', 'lessonId': 1}, {'courseName': '课二'}]}]}
        result = normalize_print_data(data, '62')
        self.assertEqual(len(result['courses']), 2)
        self.assertEqual(result['coverage'], 'scheduled_only')
        self.assertIsNone(result['courses'][1].lesson_id)

    def test_merge_activities_and_all_course_list_without_duplicate(self):
        data = {'studentTableVms': [{'activities': [{'lessonCode': 'A01', 'courseName': '课一',
            'weekday': 1, 'startUnit': 1, 'endUnit': 2, 'weekIndexes': [1, 3]}],
            'lessons': [{'id': 11, 'code': 'A01', 'course': {'nameZh': '课一'}}]}]}
        result = normalize_print_data(data, '62', full_semester=True)
        self.assertEqual(result['coverage'], 'complete')
        self.assertEqual(len(result['courses']), 1)
        self.assertEqual(result['courses'][0].lesson_id, '11')
        self.assertEqual(result['courses'][0].meetings[0].weeks, [1, 3])

    def test_dom_supplement_requires_count_and_matching_identities(self):
        result = {'courses': [], 'coverage': 'scheduled_only', 'time_slots': []}
        rows = [{'name': '课程', 'lesson_id': '11', 'no_fixed_time': True}]
        full = merge_all_course_rows(result, rows, '62', total=1)
        self.assertEqual(full['coverage'], 'complete')
        self.assertTrue(full['courses'][0].schedule_known)
        self.assertEqual(merge_all_course_rows(result, rows, '62', total=2)['coverage'], 'scheduled_only')
        self.assertEqual(merge_all_course_rows(result, rows + rows, '62', total=2)['coverage'], 'scheduled_only')

    def test_full_response_cannot_hide_scheduled_courses(self):
        old = Course(id='school:11', name='已有', lesson_id='11', source='school',
                     meetings=[Meeting(weekday=1, start=1, end=2, weeks=[1])])
        scheduled = {'courses': [old], 'coverage': 'scheduled_only', 'time_slots': []}
        full = {'courses': [], 'coverage': 'complete', 'time_slots': []}
        result = merge_schedule_sources(scheduled, full)
        self.assertEqual(result['coverage'], 'scheduled_only')
        self.assertEqual(result['courses'], [old])
        new = old.model_copy(update={'meetings': [], 'schedule_known': False}, deep=True)
        result = merge_schedule_sources(scheduled, {**full, 'courses': [new]})
        self.assertEqual(result['coverage'], 'complete')
        self.assertEqual(result['courses'][0].meetings, old.meetings)
        self.assertTrue(result['courses'][0].schedule_known)

    def test_preselected_pair_and_existing_conflicts_are_distinguished(self):
        slot = Meeting(weekday=1, start=1, end=2, weeks=[1])
        old = Course(id='old', name='必修', meetings=[slot])
        a, b = Course(id='a', name='预选一', meetings=[slot]), Course(id='b', name='预选二', meetings=[slot])
        result = analyze_plan(Plan(course_ids=['a', 'b']), [a, b], [old])
        self.assertFalse(result['can_run'])
        self.assertEqual([i['scope'] for i in result['issues'] if i['kind'] == 'conflict'].count('existing'), 2)
        self.assertEqual([i['scope'] for i in result['issues'] if i['kind'] == 'conflict'].count('plan'), 1)

    def test_same_enrolled_lesson_is_not_a_self_conflict(self):
        slot = Meeting(weekday=1, start=1, end=2, weeks=[1])
        a = Course(id='catalog:1', name='课', lesson_id='11', meetings=[slot], source='school')
        b = a.model_copy(update={'id': 'schedule:1'})
        self.assertTrue(analyze_plan(Plan(course_ids=[a.id]), [a], [b])['can_run'])

    def test_two_planned_courses_and_one_existing_report_three_pairs_not_three_plans(self):
        slot=Meeting(weekday=1,start=10,end=11,weeks=[7,8])
        a,b=Course(id='a',name='计划一',meetings=[slot]),Course(id='b',name='计划二',meetings=[slot])
        old=Course(id='old',name='已有课',meetings=[slot])
        analysis=analyze_plan(Plan(course_ids=['a','b']),[a,b],[old])
        self.assertEqual(analysis['conflict_summary'],{'pairs':3,'plan_pairs':1,'existing_pairs':2,'planned_courses':2,'existing_courses':1})

    def test_duplicate_existing_lesson_and_time_fragments_do_not_inflate_conflicts(self):
        slot=Meeting(weekday=1,start=10,end=11,weeks=[7,8])
        old=Course(id='old',lesson_id='11',semester='fixture',source='school',name='已有课',meetings=[slot,slot])
        duplicate=old.model_copy(update={'id':'duplicate'})
        a=Course(id='a',name='计划课',semester='fixture',source='school',meetings=[slot,slot])
        analysis=analyze_plan(Plan(course_ids=['a']),[a],[old,duplicate])
        issues=[i for i in analysis['issues'] if i['kind']=='conflict']
        self.assertEqual(len(issues),1)
        self.assertEqual(issues[0]['overlap'],[slot.model_dump()])
        self.assertEqual(analysis['conflict_summary']['existing_courses'],1)


class SyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / 'state.db')
        self.school = SimpleNamespace(status={'state': 'connected'}, context=object(), lock=asyncio.Lock(), activity=Activity())
        self.sync = TimetableSync(self.store, self.school)
        self.courses = [Course(id='school:11', name='课程', lesson_id='11', source='school', semester='62')]
        async def schedule(semester):
            return {'courses': self.courses, 'coverage': 'complete', 'calendar': default_calendar()}
        self.school.schedule = schedule

    async def asyncTearDown(self):
        await self.sync.close()
        self.store.close()
        self.directory.cleanup()

    async def test_initial_sync_and_updates_are_single_shared_snapshot(self):
        result = await self.sync.sync('62')
        self.assertEqual(result['course_count'], 1)
        self.assertTrue(result['config']['enabled'])
        self.assertEqual(result['config']['interval'], 60)
        self.courses.append(Course(id='school:12', name='新课', source='school'))
        result = await self.sync.sync('62')
        self.assertEqual(result['changes']['added'], 1)
        self.assertEqual(len(self.store.get('school:existing')), 2)

    async def test_network_failure_preserves_courses_and_last_success(self):
        old = await self.sync.sync('62')
        async def fail(semester):
            raise RuntimeError('Authorization: must-not-display')
        self.school.schedule = fail
        with self.assertRaises(BrowserIssue):
            await self.sync.sync('62')
        result = self.sync.public()
        self.assertEqual(result['last_success'], old['last_success'])
        self.assertEqual(len(self.store.get('school:existing')), 1)
        self.assertNotIn('must-not-display', result['message'])
        self.assertEqual(result['activity']['state'], 'failed')

    async def test_partial_sync_never_replaces_complete_snapshot(self):
        old = await self.sync.sync('62')
        async def partial(semester):
            return {'courses': [], 'coverage': 'scheduled_only'}
        self.school.schedule = partial
        result = await self.sync.sync('62')
        self.assertEqual(result['state'], 'partial')
        self.assertEqual(result['last_success'], old['last_success'])
        self.assertEqual(self.store.get('school:coverage'), 'complete')
        self.assertEqual(len(self.store.get('school:existing')), 1)

    async def test_expired_session_pauses_without_school_requests(self):
        await self.sync.sync('62')
        async def no_request(semester):
            self.fail('Invalid session must not make an automatic school request')
        self.school.schedule = no_request
        self.school.status['state'] = 'manual_required'
        self.sync.next_due = time.monotonic() - 1
        await self.sync.tick()
        self.assertEqual(self.sync.meta['state'], 'waiting_login')
        self.assertEqual(self.sync.activity.data['state'], 'waiting_user')
        self.assertEqual(len(self.store.get('school:existing')), 1)

    async def test_duplicate_sync_does_not_create_second_request(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def hold(semester):
            entered.set()
            await release.wait()
            return {'courses': self.courses, 'coverage': 'complete'}
        self.school.schedule = hold
        first = asyncio.create_task(self.sync.sync('62'))
        await entered.wait()
        with self.assertRaises(BrowserIssue):
            await self.sync.sync('62')
        release.set()
        await first

    async def test_file_import_disables_live_updates_until_normal_school_sync(self):
        await self.sync.sync('62')
        self.sync.record_import(3, 'complete')
        self.assertFalse(self.sync.config['enabled'])
        self.assertIsNone(self.sync.config['semester'])
        with self.assertRaises(BrowserIssue):
            self.sync.configure(True)


class BrowserViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_viewer_works_while_login_operation_holds_browser_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            school = SchoolSession(Path(temp))
            captured = {}
            class Page:
                def is_closed(self): return False
                def locator(self, selector):
                    captured['mask_selector'] = selector
                    return object()
                async def screenshot(self, **kwargs):
                    captured.update(kwargs)
                    return b'fixture-png'
            school.context = object()
            school.page = Page()
            async with school.lock:
                result = await asyncio.wait_for(school.view('selection'), timeout=0.5)
            self.assertEqual(result, b'fixture-png')
            self.assertEqual(captured['mask_selector'], 'input[type="password"]')
            self.assertIn('mask', captured)
