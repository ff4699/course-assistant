import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from assistant.app import create_app
from assistant.counts import CountQueryError, ObservedCountClient, count_mapping, retry_seconds
from assistant.models import Course, Meeting
from assistant.seat_monitor import SeatMonitor, SeatSettings
from assistant.store import Store

HEADERS = {'X-CUPK-Client':'local'}
SCOPE = ('fictional-batch', 'fictional-semester')


def courses(number=5):
    return [Course(id=f'school:fixture{i}', lesson_id=str(100+i), name=f'测试课程{i}',
                   class_code=f'TEST{i}.01', source='school', batch=SCOPE[0], semester=SCOPE[1],
                   capacity=40, enrolled_count=20,
                   meetings=[Meeting(weekday=i%7+1,start=3,end=4,weeks=[1,2])]) for i in range(number)]


class CountTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await self.query.close()

    async def asyncSetUp(self):
        self.context = SimpleNamespace(cookies=self.cookies, remove_listener=lambda *a:None)
        self.school = SimpleNamespace(context=self.context, context_closed=False, selection_auth_failed=False,
                                      catalog_snapshot={'batch':SCOPE[0],'semester':SCOPE[1]},
                                      _set=lambda *a,**k:None)
        self.query = ObservedCountClient(self.school)
        self.query.context = self.context
        self.requests = []
        self.body = {str(100+i):f'{30+i}-0-0' for i in range(5)}
        self.status = 200
        self.extra_headers = {}
        request = SimpleNamespace(method='GET',url='https://eams.cupk.edu.cn/fixture/std-count?lessonIds=100,101,102,103,104',
                                  post_data=None,all_headers=self.headers)
        response = SimpleNamespace(request=request,ok=True,json=self.response_json)
        await self.query.capture(response)
        self.assertTrue(self.query.ready(SCOPE))

    async def cookies(self, url):
        return [{'name':'fictional-session','value':'memory-only-test-cookie','domain':'eams.cupk.edu.cn','path':'/'}]

    async def headers(self):
        return {'authorization':'memory-only-test-token', 'cookie':'outdated-cookie'}

    async def response_json(self):
        return dict(self.body)

    def transport(self, request):
        self.requests.append(request)
        return httpx.Response(self.status,json=self.body,headers=self.extra_headers)

    async def run_query(self, ids=None):
        original = httpx.AsyncClient
        with patch('assistant.counts.httpx.AsyncClient',side_effect=lambda **kw: original(transport=httpx.MockTransport(self.transport),**kw)):
            return await self.query.query(ids or ['100','101','102','103','104'],SCOPE)

    async def test_five_ids_one_request_actual_cookie_reused_and_suffix_not_interpreted(self):
        result = await self.run_query()
        self.assertEqual(len(self.requests),1)
        self.assertEqual(self.requests[0].url.params['lessonIds'],'100,101,102,103,104')
        self.assertEqual(result['counts']['100'],30)
        self.assertIn('memory-only-test-cookie',self.requests[0].headers['cookie'])
        self.assertNotIn('outdated-cookie',self.requests[0].headers['cookie'])

    async def test_incomplete_malformed_or_error_response_never_defaults_to_zero(self):
        for body in ({'100':'30-0-0'},{str(100+i):'unknown' for i in range(5)}, {'result':1,'data':dict(self.body)}):
            self.body = body
            self.query.next_allowed=0
            with self.assertRaises(CountQueryError):
                await self.run_query()

    async def test_expired_auth_invalidates_template_without_leaking_headers(self):
        self.status=401
        with self.assertRaises(CountQueryError) as caught:
            await self.run_query()
        self.assertEqual(caught.exception.kind,'waiting_login')
        self.assertIsNone(self.query.template)
        self.assertNotIn('memory-only-test-token',str(caught.exception))

    async def test_rate_limit_respected_across_all_counter_consumers(self):
        self.status=429;self.extra_headers={'Retry-After':'90'}
        with self.assertRaises(CountQueryError) as caught:
            await self.run_query()
        self.assertEqual(caught.exception.retry_after,90)
        self.status=200
        with self.assertRaises(CountQueryError) as caught:
            await self.run_query()
        self.assertEqual(caught.exception.kind,'cooldown')
        self.assertEqual(len(self.requests),1)

    async def test_whole_catalog_one_request_only_with_observed_batch_support(self):
        self.body={str(100+i):'7-0-0' for i in range(14)}
        self.query.template['observed_size']=14
        original=httpx.AsyncClient
        with patch('assistant.counts.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(self.transport),**kw)):
            result=await self.query.query_catalog(list(self.body),SCOPE)
        self.assertEqual(len(self.requests),1)
        self.assertEqual(len(result['counts']),14)
        with self.assertRaises(CountQueryError):
            await self.query.query_catalog([str(100+i) for i in range(15)],SCOPE)
        self.assertEqual(len(self.requests),1)

    async def test_scope_and_target_limit_prevent_unverified_requests(self):
        with self.assertRaises(CountQueryError):
            await self.query.query(['100'],('different-batch',SCOPE[1]))
        with self.assertRaises(CountQueryError):
            await self.query.query(['100','101','102','103','104','105'],SCOPE)
        self.assertEqual(self.requests,[])


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.directory.name)/'test.sqlite3')
        self.courses=courses()
        self.existing=[]
        self.calls=[]
        self.call_times=[]
        self.latency=0
        self.failure=None
        self.wait_event=None
        self.values={c.lesson_id:38-i for i,c in enumerate(self.courses)}
        self.monitor=SeatMonitor(self.store,'school',lambda:self.courses,lambda:self.existing,self.query,lambda:SCOPE)
        await self.monitor.configure(SeatSettings(targets=[{'class_code':c.class_code,'priority':i+1} for i,c in enumerate(self.courses)]))

    async def query(self, ids, scope):
        self.calls.append(list(ids))
        self.call_times.append(time.monotonic())
        if self.latency:
            await asyncio.sleep(self.latency)
        if self.wait_event:
            await self.wait_event.wait()
        if self.failure:
            raise self.failure
        return {'counts':{i:self.values[i] for i in ids},'source':'test_fixture','response_ms':140}

    async def asyncTearDown(self):
        if self.wait_event:
            self.wait_event.set()
        await self.monitor.close()
        self.store.close();self.directory.cleanup()

    async def test_mapping_remaining_and_sqlite_persistence(self):
        self.store.put('school:plan',{'course_ids':[self.courses[-1].id,self.courses[0].id]})
        snapshot=await self.monitor.refresh()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(len(self.calls[0]),5)
        self.assertEqual(snapshot['targets'][0]['remaining'],2)
        self.assertEqual(snapshot['targets'][0]['course']['remaining'],2)
        self.assertEqual(snapshot['targets'][0]['course']['lesson_id'],'100')
        again=SeatMonitor(self.store,'school',lambda:self.courses,lambda:self.existing,self.query,lambda:SCOPE)
        self.assertEqual(again.public()['targets'][0]['enrolled_count'],38)
        self.assertEqual(again.public()['meta']['phase'],'paused')
        self.assertEqual(self.store.get('school:plan')['course_ids'],[self.courses[-1].id,self.courses[0].id])

    async def test_inflight_manual_requests_never_overlap_or_change_configuration(self):
        self.wait_event=asyncio.Event()
        round_=asyncio.create_task(self.monitor.refresh())
        while not self.calls:await asyncio.sleep(0)
        with self.assertRaises(CountQueryError):await self.monitor.refresh()
        with self.assertRaises(CountQueryError):await self.monitor.configure(SeatSettings())
        await self.monitor.control('pause')
        self.wait_event.set();await round_
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.monitor.meta['phase'],'paused')

    async def test_failure_keeps_correct_data_and_strict_retry_after_survives_restart(self):
        await self.monitor.refresh()
        before=dict(self.monitor.results)
        self.monitor.next_allowed=0
        self.failure=CountQueryError('rate limit','rate_limited',90)
        result=await self.monitor.refresh()
        self.assertEqual(self.monitor.results,before)
        self.assertGreater(self.monitor.next_allowed-asyncio.get_running_loop().time(),89)
        again=SeatMonitor(self.store,'school',lambda:self.courses,lambda:self.existing,self.query,lambda:SCOPE)
        with self.assertRaises(CountQueryError):await again.refresh()
        self.assertIsNotNone(result['meta']['last_error'])

    async def test_stale_full_selected_conflict_and_dynamic_sorting(self):
        self.values.update({'100':40,'101':39,'102':30,'103':38,'104':35})
        self.existing=[self.courses[4].model_copy()]
        self.courses[3].meetings=self.courses[2].meetings
        self.courses[0].enrolled_count=40
        await self.monitor.refresh()
        result=self.monitor.public()
        self.assertEqual(result['targets'][0]['status'],'full')
        self.assertEqual(result['targets'][4]['status'],'selected')
        self.assertEqual(result['targets'][2]['status'],'conflict')
        self.assertEqual(result['recommendations'],[self.courses[1].id])
        self.monitor.results[self.courses[1].id]['updated_at']=(datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat()
        self.assertEqual(self.monitor.public()['recommendations'],[])

    async def test_priority_and_scarcity_strategies_do_not_rewrite_saved_order(self):
        await self.monitor.refresh()
        self.assertEqual(self.monitor.public()['recommendations'][0],self.courses[0].id)
        self.values['104']=39
        self.monitor.next_allowed=0
        await self.monitor.refresh()
        self.monitor.settings['strategy']='scarcity_first'
        self.assertEqual(self.monitor.public()['recommendations'][0],self.courses[4].id)
        self.assertEqual([r['priority'] for r in self.monitor.settings['targets']],[1,2,3,4,5])

    async def test_five_second_scheduler_and_pause_stop_have_one_worker(self):
        self.latency=.4
        await self.monitor.control('start')
        task=self.monitor.task
        await self.monitor.control('start')
        self.assertIs(task,self.monitor.task)
        while not self.calls:await asyncio.sleep(.01)
        await asyncio.sleep(5.2)
        self.assertEqual(len(self.calls),2)
        self.assertLess(self.call_times[1]-self.call_times[0],5.3)
        await self.monitor.control('pause')
        await asyncio.sleep(.2)
        self.assertEqual(len(self.calls),2)
        await self.monitor.control('stop')
        self.assertEqual(self.monitor.meta['phase'],'stopped')

    async def test_id_change_and_ambiguous_code_stop_without_using_stale_mapping(self):
        self.courses[0].lesson_id='999'
        snapshot=await self.monitor.refresh()
        self.assertEqual(len(self.calls),0)
        self.assertEqual(snapshot['meta']['phase'],'base_information')
        self.courses.append(self.courses[1].model_copy(update={'id':'duplicate'}))
        with self.assertRaises(CountQueryError):
            self.monitor.resolve(SeatSettings(targets=[{'class_code':'TEST1.01'}]).targets)

    async def test_five_failed_rounds_pause_and_keep_previous_counts(self):
        await self.monitor.refresh()
        before=dict(self.monitor.results)
        self.failure=CountQueryError('fictional network failure')
        self.monitor.meta['phase']='running'
        for _ in range(5):
            self.monitor.next_allowed=0
            await self.monitor.refresh()
        self.assertEqual(self.monitor.meta['phase'],'error')
        self.assertEqual(self.monitor.results,before)
        self.assertEqual(self.monitor.meta['failures'],5)

    async def test_shared_catalog_snapshot_keeps_original_timestamp_and_order(self):
        stamp=(datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat()
        self.store.put('school:catalog_seats',{'scope':list(SCOPE),'counts':dict(self.values),
                                           'updated_at':stamp,'source':'test_fixture'})
        settings=SeatSettings(targets=[{'class_code':c.class_code,'priority':5-i} for i,c in enumerate(self.courses)])
        snapshot=await self.monitor.configure(settings)
        self.assertEqual(snapshot['targets'][0]['updated_at'],stamp)
        self.assertTrue(snapshot['targets'][0]['stale'])
        self.assertEqual(snapshot['targets'][0]['base_priority'],5)
        self.assertEqual(self.calls,[])

    async def test_sse_style_snapshot_stream_publishes_updates(self):
        events=self.monitor.events()
        initial=await anext(events)
        self.assertIsNone(initial['meta']['last_success'])
        pending=asyncio.create_task(anext(events))
        await asyncio.sleep(0)
        await self.monitor.refresh()
        updated=await asyncio.wait_for(pending,1)
        self.assertEqual(updated['targets'][0]['remaining'],2)
        await events.aclose()


class SeatApiTests(unittest.TestCase):
    def test_configuration_limits_shared_database_and_existing_plan_independent(self):
        with tempfile.TemporaryDirectory() as temp:
            with TestClient(create_app(Path(temp))) as client:
                self.assertEqual(client.get('/api/seats?mode=demo').json()['settings']['interval'],5)
                client.put('/api/plan',headers=HEADERS,json={'course_ids':['demo:film']})
                settings={'targets':[{'class_code':'ART101-01','priority':2},{'class_code':'ART204-01','priority':1}]}
                self.assertEqual(client.put('/api/seats/settings?mode=demo',headers=HEADERS,json=settings).status_code,200)
                result=client.post('/api/seats/refresh?mode=demo',headers=HEADERS).json()
                self.assertEqual(result['targets'][0]['remaining'],2)
                self.assertEqual(result['recommendations'][0],'demo:music')
                self.assertFalse(result['submission_enabled'])
                self.assertEqual(client.get('/api/state').json()['plan']['course_ids'],['demo:film'])
                self.assertEqual(client.put('/api/seats/settings?mode=demo',headers=HEADERS,json={'interval':1}).status_code,422)
                self.assertEqual(client.put('/api/seats/settings?mode=demo',json=settings).status_code,403)
                catalog=client.post('/api/seats/catalog/refresh?mode=demo',headers=HEADERS).json()
                self.assertEqual(len(catalog['rows']),9)
                self.assertIsNotNone(catalog['updated_at'])


if __name__=='__main__':unittest.main()
