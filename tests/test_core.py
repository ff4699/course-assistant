import asyncio
import tempfile
import unittest
from pathlib import Path
from assistant.demo import DemoAdapter, Snapshot, Submission, sample_courses, required_courses
from assistant.engine import Engine
from assistant.models import Course, Meeting, Plan, analyze_plan, conflicts
from assistant.schedule import normalize_print_data
from assistant.store import Store


def course(ident, day=1, start=1, end=2, weeks=None):
    return Course(id=ident, name=ident, meetings=[Meeting(weekday=day, start=start,
                  end=end, weeks=weeks or [1, 2, 3])])


class ConflictTests(unittest.TestCase):
    def test_same_time_different_weeks_is_not_conflict(self):
        self.assertEqual(conflicts(course('a', weeks=[1, 3, 5]), course('b', weeks=[2, 4, 6])), [])

    def test_partial_week_overlap_and_boundary_period(self):
        result = conflicts(course('a', start=2, end=4, weeks=[1, 3]), course('b', start=4, end=6, weeks=[3, 5]))
        self.assertEqual(result, [{'weekday': 1, 'start': 4, 'end': 4, 'weeks': [3]}])

    def test_second_meeting_is_checked(self):
        first = course('a', day=2)
        first.meetings.append(Meeting(weekday=4, start=5, end=6, weeks=[1]))
        self.assertTrue(conflicts(first, course('b', day=4, start=6, end=7)))

    def test_conflict_with_preassigned_required_course(self):
        plan = Plan(course_ids=['demo:ai'])
        self.assertFalse(analyze_plan(plan, sample_courses(), required_courses())['can_run'])

    def test_unknown_existing_schedule_blocks_conflict_clearance(self):
        old = Course(id='old', name='unknown', schedule_known=False)
        self.assertFalse(analyze_plan(Plan(course_ids=['a']), [course('a')], [old])['can_run'])

    def test_unique_targets_and_limit(self):
        with self.assertRaises(ValueError): Plan(course_ids=['a', 'a'])
        with self.assertRaises(ValueError): Plan(course_ids=[str(i) for i in range(6)])

    def test_start_time_requires_valid_timezone(self):
        with self.assertRaises(ValueError): Plan(start_at='2026-10-12T09:00')
        with self.assertRaises(ValueError): Plan(start_at='invalid')


class ScheduleTests(unittest.TestCase):
    def payload(self, activities):
        return {'studentTableVms': [{'activities': activities, 'timeTableLayout': {'courseUnitList': []}}]}

    def activity(self, **extra):
        return {'courseName': '测试课程', 'teachers': ['教师'], 'weekday': 2,
                'startUnit': 3, 'endUnit': 4, 'weekIndexes': [1, 3, 5], **extra}

    def test_preserves_lesson_id_and_all_time_segments(self):
        data = self.payload([self.activity(lessonId=27), self.activity(lessonId=27, weekday=4)])
        result = normalize_print_data(data, '12')
        self.assertEqual(result['courses'][0].lesson_id, '27')
        self.assertEqual(len(result['courses'][0].meetings), 2)
        self.assertEqual(result['coverage'], 'scheduled_only')

    def test_missing_identity_is_not_fabricated(self):
        result = normalize_print_data(self.payload([self.activity()]), '12')
        self.assertIsNone(result['courses'][0].lesson_id)

    def test_missing_weeks_are_unknown(self):
        result = normalize_print_data(self.payload([self.activity(weekIndexes=None)]), '12')
        self.assertFalse(result['courses'][0].schedule_known)

    def test_multiple_students_rejected(self):
        with self.assertRaises(ValueError): normalize_print_data({'studentTableVms': [{}, {}]}, '12')


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / 'state.db')
        self.adapter = DemoAdapter(self.store, latency=0.01)
        self.engine = Engine(self.store, self.adapter)

    async def asyncTearDown(self):
        await self.engine.stop()
        if self.engine.active: await self.engine.task
        self.store.close()
        self.directory.cleanup()

    async def execute(self, ids, **kwargs):
        await self.engine.start(Plan(course_ids=ids, retry_delay=0.5, **kwargs), required_courses())
        await self.engine.task
        return {t['id']: t for t in self.engine.run_state['targets']}

    async def test_scarcity_before_personal_priority_and_only_one_submitter(self):
        result = await self.execute(['demo:photo-a', 'demo:music', 'demo:film', 'demo:speech'])
        self.assertEqual(self.adapter.order[0], 'demo:music')
        self.assertEqual(self.adapter.max_concurrent_submissions, 1)
        self.assertTrue(all(t['status'] == 'succeeded' for t in result.values()))

    async def test_personal_priority_can_override_scarcity(self):
        await self.execute(['demo:photo-a', 'demo:music'], scarcity_first=False)
        self.assertEqual(self.adapter.order[0], 'demo:photo-a')

    async def test_timeout_confirmed_from_timetable_is_not_retried(self):
        result = await self.execute(['demo:speech'])
        self.assertEqual(result['demo:speech']['evidence'], '课表')
        self.assertEqual(self.adapter.calls['demo:speech'], 1)

    async def test_full_course_has_no_submission(self):
        result = await self.execute(['demo:ecology'])
        self.assertEqual(result['demo:ecology']['status'], 'full')
        self.assertEqual(self.adapter.order, [])

    async def test_default_retries_means_four_total_attempts(self):
        calls = 0
        async def busy(target):
            nonlocal calls
            calls += 1
            return Submission('retryable', 'busy')
        self.adapter.submit = busy
        result = await self.execute(['demo:photo-a'])
        self.assertEqual(calls, 4)
        self.assertEqual(result['demo:photo-a']['status'], 'exhausted')

    async def test_unknown_without_confirmation_never_blindly_retries(self):
        calls = 0
        async def unknown(target):
            nonlocal calls
            calls += 1
            return Submission('unknown', 'timeout')
        self.adapter.submit = unknown
        result = await self.execute(['demo:photo-a'])
        self.assertEqual(calls, 1)
        self.assertEqual(result['demo:photo-a']['status'], 'unconfirmed')

    async def test_stop_confirms_inflight_and_prevents_next_submit(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def hold(target):
            self.adapter.order.append(target.id)
            entered.set()
            await release.wait()
            self.adapter.selected.add(target.id)
            return Submission('accepted', 'accepted')
        self.adapter.submit = hold
        await self.engine.start(Plan(course_ids=['demo:music', 'demo:photo-a']), required_courses())
        await entered.wait()
        await self.engine.stop()
        release.set()
        await self.engine.task
        self.assertEqual(len(self.adapter.order), 1)
        self.assertEqual(self.engine.run_state['targets'][0]['status'], 'succeeded')
        self.assertEqual(self.engine.run_state['phase'], 'stopped')

    async def test_double_start_creates_one_task(self):
        async def hold(target):
            await asyncio.sleep(0.2)
            return Submission('unknown', 'pending')
        self.adapter.submit = hold
        plan = Plan(course_ids=['demo:music'])
        results = await asyncio.gather(self.engine.start(plan, required_courses()),
                                       self.engine.start(plan, required_courses()), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, ValueError) for r in results), 1)
        await self.engine.task

    async def test_restart_reconciles_accepted_inflight(self):
        self.store.put('demo:run', {'phase': 'running', 'events': [], 'targets': [
            {'id': 'demo:speech', 'status': 'submitting', 'priority': 1, 'attempts': 1, 'reason': 'pending'}]})
        self.adapter.selected.add('demo:speech')
        recovered = Engine(self.store, self.adapter)
        await recovered.reconcile()
        self.assertEqual(recovered.run_state['targets'][0]['status'], 'succeeded')
        self.assertEqual(self.adapter.order, [])

    async def test_timing_records_responses_confirmation_and_timetable_update(self):
        result=await self.execute(['demo:film','demo:speech'])
        self.assertEqual(len(result['demo:film']['attempt_records']),2)
        self.assertEqual(len(result['demo:speech']['attempt_records']),1)
        self.assertGreater(self.engine.run_state['elapsed_ms'],0)
        self.assertTrue(self.engine.run_state['timetable_updated'])
        for t in result.values():
            self.assertGreaterEqual(t['submission_ms'],0)
            self.assertGreaterEqual(t['verification_ms'],0)
            self.assertIsNotNone(t['confirmed_at'])

    async def test_resume_only_unfinished_preserves_attempts_and_successes(self):
        first=self.adapter.submit
        entered=asyncio.Event()
        async def hold(course):
            response=await first(course);entered.set();return response
        self.adapter.submit=hold
        plan=Plan(course_ids=['demo:photo-a','demo:music'],scarcity_first=False)
        await self.engine.start(plan,required_courses())
        await entered.wait();await self.engine.stop();await self.engine.task
        before=self.engine.run_state['elapsed_ms']
        await self.engine.resume(plan,required_courses());await self.engine.task
        self.assertEqual(self.adapter.calls['demo:photo-a'],1)
        self.assertEqual(self.adapter.calls['demo:music'],1)
        self.assertTrue(all(t['status']=='succeeded' for t in self.engine.run_state['targets']))
        self.assertGreater(self.engine.run_state['elapsed_ms'],before)
        self.assertEqual(self.engine.run_state['resume_count'],1)

    async def test_timetable_sync_failure_does_not_revoke_confirmed_success(self):
        async def failed():raise RuntimeError('upstream failed')
        self.engine.on_success=failed
        result=await self.execute(['demo:music'])
        self.assertEqual(result['demo:music']['status'],'succeeded')
        self.assertFalse(self.engine.run_state['timetable_updated'])
        self.assertTrue(any('课表更新待重试' in e['message'] for e in self.engine.run_state['events']))
