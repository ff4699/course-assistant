import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
import httpx
from assistant.browser import SchoolSession,BrowserIssue,catalog_identity
from assistant.catalog import normalize_school_data
from test_school_catalog import school_data

class HttpxCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.school=SchoolSession(Path(self.temp.name))
        data=school_data();data.update(batch='1',semester='2')
        self.school.catalog_snapshot=normalize_school_data(data,{'tables':[{'headers':['课程信息']}],'pagination':{'filtered':False}},catalog_identity)
        self.school.fields=['课程信息']
        self.school.catalog_request_template={'url':'http://127.0.0.1/course-selection-api/api/v1/student/course-select/query-lesson/1/999','method':'POST','headers':{'Authorization':'synthetic-fixture-auth'},'body':json.dumps({'turnId':1,'studentId':999,'semesterId':2,'pageNo':1,'pageSize':2}),'batch':'1','semester':'2'}
    async def asyncTearDown(self):self.temp.cleanup()
    def payload(self,page=1,total=4):
        data=school_data();lessons=data['lessons']
        if page==2:
            for lesson in lessons:lesson['id']+=2;lesson['code']+='B'
        return {'result':0,'data':{'lessons':lessons,'pageInfo':{'currentPage':page,'totalRows':total,'totalPages':2}}}
    async def test_observed_request_paginates_without_changing_identity_and_keeps_unknown_counts(self):
        post=AsyncMock(side_effect=[httpx.Response(200,json=self.payload(1)),httpx.Response(200,json=self.payload(2))])
        client=httpx.AsyncClient();client.post=post;pages=[]
        with patch('assistant.browser.HOSTS',{'127.0.0.1'}),patch('assistant.browser.httpx.AsyncClient',return_value=client):result=await self.school._read_catalog_httpx(lambda p:pages.append(p))
        self.assertEqual(len(result['courses']),4);self.assertEqual(result['coverage'],'complete');self.assertEqual(result['source'],'school_httpx')
        calls=post.call_args_list;self.assertEqual([c.kwargs['json']['pageNo'] for c in calls],[1,2])
        self.assertTrue(all(c.kwargs['json']['studentId']==999 and c.kwargs['json']['turnId']==1 for c in calls))
        self.assertTrue(all(c.enrolled_count is None for c in result['courses'][2:]))
    async def test_failed_second_page_preserves_first_and_never_echoes_auth(self):
        client=httpx.AsyncClient();client.post=AsyncMock(side_effect=[httpx.Response(200,json=self.payload(1)),RuntimeError('synthetic-fixture-auth')]);pages=[]
        with patch('assistant.browser.HOSTS',{'127.0.0.1'}),patch('assistant.browser.httpx.AsyncClient',return_value=client):
            with self.assertRaises(BrowserIssue) as error:await self.school._read_catalog_httpx(lambda p:pages.append(p))
        self.assertEqual(len(pages),1);self.assertNotIn('synthetic-fixture-auth',str(error.exception))
    async def test_different_scope_and_filtered_query_rejected_before_http(self):
        self.school.catalog_request_template['semester']='3'
        with self.assertRaisesRegex(BrowserIssue,'不一致'):await self.school._read_catalog_httpx(lambda _:None)
        self.school.catalog_request_template['semester']='2';self.school.catalog_snapshot['pagination']['filtered']=True
        with patch('assistant.browser.HOSTS',{'127.0.0.1'}),self.assertRaisesRegex(BrowserIssue,'清空筛选'):await self.school._read_catalog_httpx(lambda _:None)
        self.school.catalog_snapshot['pagination']['filtered']=False
        body=json.loads(self.school.catalog_request_template['body']);body['canSelect']=1
        self.school.catalog_request_template['body']=json.dumps(body)
        with patch('assistant.browser.HOSTS',{'127.0.0.1'}),self.assertRaisesRegex(BrowserIssue,'清空筛选'):await self.school._read_catalog_httpx(lambda _:None)

    async def test_auth_failure_blocks_stale_dom_until_normal_query_succeeds(self):
        client=httpx.AsyncClient();client.post=AsyncMock(return_value=httpx.Response(401))
        with patch('assistant.browser.HOSTS',{'127.0.0.1'}),patch('assistant.browser.httpx.AsyncClient',return_value=client):
            with self.assertRaisesRegex(BrowserIssue,'失效'):await self.school._read_catalog_httpx(lambda _:None)
        self.assertTrue((await self.school._selection_view())['auth_required'])
        response=AsyncMock();response.ok=True;response.request=AsyncMock()
        response.request.method='POST';response.request.url=self.school.catalog_request_template['url']
        response.request.post_data=self.school.catalog_request_template['body']
        response.request.all_headers.return_value={};response.json.return_value=self.payload(1)
        await self.school._capture_catalog_request(response)
        self.assertFalse(self.school.selection_auth_failed)
