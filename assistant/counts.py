"""Replay only a normal-page observed std-count query. Authentication stays in RAM."""
import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import re
import time
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

import httpx


class CountQueryError(Exception):
    def __init__(self, message, kind='query_error', retry_after=0):
        super().__init__(message)
        self.kind, self.retry_after = kind, retry_after


def parse_count(value):
    if isinstance(value, str) and re.fullmatch(r'\d+-\d+-\d+', value):
        return int(value.split('-', 1)[0])
    raise CountQueryError('人数响应格式尚未核实，保留上次正确数据', 'invalid_response')


def count_mapping(payload, ids, path=None):
    if isinstance(payload, dict) and 'result' in payload and payload['result'] != 0:
        raise CountQueryError('学校人数查询未成功，保留上次正确数据', 'invalid_response')
    if path is None:
        # Learn the wrapper only from an observed successful normal-page response.
        choices = [(), ('data',)]
    else:
        choices = [tuple(path)]
    for candidate in choices:
        value = payload
        for field in candidate:
            value = value.get(field) if isinstance(value, dict) else None
        if not isinstance(value, dict) or not all(ident in value for ident in ids):
            continue
        try:
            counts = {ident: parse_count(value[ident]) for ident in ids}
        except CountQueryError:
            continue
        return counts, candidate
    raise CountQueryError('人数响应缺少目标教学班或格式变化，保留上次正确数据', 'incomplete_response')


def retry_seconds(value):
    try:
        return max(0, float(value))
    except (ValueError, TypeError):
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0


class ObservedCountClient:
    def __init__(self, school):
        self.school = school
        self.template = None
        self.context = None
        self.tasks = set()
        self.lock = asyncio.Lock()
        self.next_allowed = 0.0
        self.http_client = None

    def attach(self, context):
        if context is self.context:
            return
        if self.context is not None:
            self.context.remove_listener('response', self.observe)
        self.context, self.template = context, None
        if context is not None:
            context.on('response', self.observe)

    def observe(self, response):
        parsed = urlparse(response.url)
        if parsed.hostname != self.school.count_host or not re.search(r'(?:^|/)std-count(?:/|$)', parsed.path):
            return
        task = asyncio.create_task(self.capture(response))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def capture(self, response):
        try:
            request = response.request
            query = dict(parse_qsl(urlparse(request.url).query, keep_blank_values=True))
            location, values = 'query', query
            content_type = (await request.all_headers()).get('content-type', '')
            if 'lessonIds' not in values and request.method == 'POST':
                location = 'json' if 'application/json' in content_type else 'form'
                values = json.loads(request.post_data or '{}') if location == 'json' else dict(parse_qsl(request.post_data or '', keep_blank_values=True))
            encoded = values.get('lessonIds')
            if request.method not in {'GET', 'POST'} or not response.ok or not isinstance(encoded, str):
                return
            ids = encoded.split(',')
            if not ids or not all(re.fullmatch(r'\d+', ident) for ident in ids):
                return
            payload = await response.json()
            _, path = count_mapping(payload, ids)
            scope = getattr(self.school, 'catalog_request_template', None) or self.school.catalog_snapshot or {}
            if not scope.get('batch') or not scope.get('semester') or 'unverified' in (scope['batch'], scope['semester']):
                return
            if scope.get('lesson_ids') and not set(ids).issubset(scope['lesson_ids']):
                return
            old = self.template
            scope_key = (str(scope['batch']), str(scope['semester']))
            observed_size = max(len(ids), old.get('observed_size', 0) if old and old['scope'] == scope_key else 0)
            self.template = {'method': request.method, 'url': request.url,
                             'headers': await request.all_headers(), 'location': location,
                             'values': values, 'mapping_path': path,
                             'scope': scope_key, 'observed_size': observed_size}
            self.school.selection_auth_failed = False
        except Exception:
            pass  # Never expose request arguments, identity URLs, or authentication.

    def ready(self, scope):
        return bool(self.template and self.context is self.school.context
                    and not self.school.context_closed and not self.school.selection_auth_failed
                    and self.template['scope'] == tuple(scope))

    async def query(self, lesson_ids, scope):
        return await self._query(lesson_ids, scope, 5)

    async def query_catalog(self, lesson_ids, scope):
        limit = self.template.get('observed_size', 0) if self.template else 0
        if not limit or len(lesson_ids) > limit:
            raise CountQueryError('当前目录数量超过正常页面已实测的单批数量；请先在学校页面核实批量上限，未发送请求', 'batch_limit')
        return await self._query(lesson_ids, scope, limit)

    async def _query(self, lesson_ids, scope, limit):
        ids = [str(value) for value in lesson_ids]
        if not 1 <= len(ids) <= limit or len(set(ids)) != len(ids) or not all(re.fullmatch(r'\d+', v) for v in ids):
            raise CountQueryError('批量教学班ID无效或超过已核实范围', 'invalid_targets')
        if not self.ready(scope):
            raise CountQueryError('尚未捕获当前批次的正常std-count请求，请在已有学校窗口正常查询课程', 'waiting_api')
        if self.lock.locked():
            raise CountQueryError('当前批量请求尚未完成，不启动重叠查询', 'busy', 5)
        async with self.lock:
            if time.monotonic() < self.next_allowed:
                raise CountQueryError('批量查询仍在间隔或学校限流等待期', 'cooldown', self.next_allowed - time.monotonic())
            template = self.template
            parsed = urlparse(template['url'])
            if parsed.hostname != self.school.count_host or parsed.scheme != 'https' or not re.search(r'(?:^|/)std-count(?:/|$)', parsed.path):
                raise CountQueryError('未核实的查询地址，已停止', 'waiting_api')
            headers = {k:v for k,v in template['headers'].items() if k.lower() not in
                       {'host', 'content-length', 'connection', 'accept-encoding', 'cookie'}}
            # Current browser cookies replace the captured Cookie header; token headers
            # can only come from the actual normal request, never from disk or guessed API.
            cookies = await self.context.cookies(template['url'])
            jar = httpx.Cookies()
            for cookie in cookies:
                jar.set(cookie['name'], cookie['value'], domain=cookie.get('domain', ''), path=cookie.get('path', '/'))
            values = {**template['values'], 'lessonIds': ','.join(ids)}
            url = template['url']
            kwargs = {}
            if template['location'] == 'query':
                query = [(k, v) for k,v in parse_qsl(parsed.query, keep_blank_values=True) if k != 'lessonIds']
                query.append(('lessonIds', ','.join(ids)))
                url = urlunparse(parsed._replace(query=urlencode(query)))
            else:
                kwargs['json' if template['location'] == 'json' else 'data'] = values
            started = time.perf_counter()
            request_started = time.monotonic()
            self.next_allowed = request_started + 5
            try:
                if self.http_client is None:
                    self.http_client = httpx.AsyncClient(follow_redirects=False, timeout=10)
                # Reuse TCP/TLS, but never resurrect an old cookie after browser logout.
                self.http_client.cookies.clear()
                self.http_client.cookies.update(jar)
                response = await self.http_client.request(template['method'], url, headers=headers, **kwargs)
            except httpx.HTTPError:
                raise CountQueryError('人数查询网络异常，保留上次正确数据') from None
            if not self.ready(scope):
                raise CountQueryError('查询期间登录上下文或批次已变化，未更新数据', 'waiting_api')
            if response.status_code in {401, 403} or 300 <= response.status_code < 400:
                self.template = None
                self.school.selection_auth_failed = True
                self.school._set('manual_required', '学校人数查询会话已过期，请正常验证',
                                 selection_ready=False, selection_stage='auth_required')
                raise CountQueryError('学校登录已过期，请在正常窗口重新验证', 'waiting_login')
            wait = retry_seconds(response.headers.get('retry-after'))
            self.next_allowed = max(self.next_allowed, time.monotonic() + wait)
            if response.status_code == 429:
                raise CountQueryError('学校限制查询频率，已按提示延长等待', 'rate_limited', max(wait, 5))
            if response.status_code != 200:
                raise CountQueryError('学校人数查询暂时不可用，保留上次正确数据', retry_after=wait)
            try:
                counts, _ = count_mapping(response.json(), ids, template['mapping_path'])
            except (ValueError, TypeError):
                raise CountQueryError('人数响应无法解析，保留上次正确数据', 'invalid_response') from None
            return {'counts': counts, 'response_ms': round((time.perf_counter() - started) * 1000),
                    'source': 'school_httpx_std_count', 'server_delay': wait,
                    'request_started_monotonic':request_started, 'next_allowed_monotonic':self.next_allowed}

    async def close(self):
        self.attach(None)
        if self.tasks:
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None
