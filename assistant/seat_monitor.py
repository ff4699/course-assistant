"""Independent, bounded batch seat monitoring and recommendations; never submits."""
import asyncio
from datetime import datetime, timezone
import time
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .counts import CountQueryError
from .models import Course, Plan, analyze_plan


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class SeatTargetInput(BaseModel):
    class_code: str = Field(min_length=1, max_length=80)
    priority: int = Field(default=1, ge=1, le=5)


class SeatSettings(BaseModel):
    targets: list[SeatTargetInput] = Field(default_factory=list, max_length=5)
    interval: int = Field(default=5, ge=5, le=300)
    stale_after: int = Field(default=15, ge=10, le=1800)
    strategy: Literal['priority_first', 'scarcity_first'] = 'priority_first'
    refresh_base: bool = False

    @model_validator(mode='after')
    def validate_codes(self):
        for target in self.targets:
            target.class_code = target.class_code.strip()
        codes = [target.class_code for target in self.targets]
        if any(not code for code in codes) or len(codes) != len(set(codes)):
            raise ValueError('教学班代码不能为空或重复')
        return self


class SeatMonitor:
    def __init__(self, store, mode, catalog, existing, query, scope):
        self.store, self.mode = store, mode
        self.catalog, self.existing, self.query, self.scope = catalog, existing, query, scope
        self.key = mode + ':seat_monitor'
        saved = store.get(self.key, {})
        self.settings = saved.get('settings', {'targets': [], 'interval': 5, 'stale_after': 15, 'strategy': 'priority_first'})
        self.results = saved.get('results', {})
        self.meta = {'phase': 'paused' if self.settings['targets'] else 'idle', 'querying': False,
                     'last_success': saved.get('meta', {}).get('last_success'),
                     'last_attempt': None, 'response_ms': None, 'failures': 0,
                     'last_error': None, 'next_allowed_at': None,
                     'message': '重启后监测已暂停，点击开始恢复' if self.settings['targets'] else '先配置最多5个教学班'}
        remaining_wait = 0
        try:
            saved_next = saved.get('meta', {}).get('next_allowed_at')
            if saved_next:
                remaining_wait = max(0, (datetime.fromisoformat(saved_next) - datetime.now(timezone.utc)).total_seconds())
                if remaining_wait:
                    self.meta['next_allowed_at'] = saved_next
        except (ValueError, TypeError):
            pass
        self.next_allowed = time.monotonic() + remaining_wait
        self.lock = asyncio.Lock()
        self.condition = asyncio.Condition()
        self.revision = 0
        self.task = None
        self.wake = asyncio.Event()

    async def publish(self):
        self.store.put(self.key, {'settings': self.settings, 'results': self.results, 'meta': self.meta})
        async with self.condition:
            self.revision += 1
            self.condition.notify_all()

    def resolve(self, inputs):
        scope = tuple(self.scope())
        if self.mode == 'school' and (len(scope) != 2 or any(v in ('', 'unverified', 'None') for v in scope)):
            raise CountQueryError('请先从当前学校批次读取课程目录，核实学期和批次', 'base_information')
        rows = []
        catalog = self.catalog()
        for target in inputs:
            matches = [c for c in catalog if c.class_code == target.class_code
                       and (c.batch, c.semester) == scope]
            if not matches:
                raise CountQueryError('当前范围目录找不到教学班代码：' + target.class_code, 'base_information')
            if len(matches) != 1 or not matches[0].lesson_id:
                raise CountQueryError('教学班内部ID缺失或匹配不唯一，请重新读取基础信息', 'base_information')
            course = matches[0]
            if self.mode == 'school' and not str(course.lesson_id).isdecimal():
                raise CountQueryError('内部lessonId尚未核实，不能用教学班代码代替', 'base_information')
            rows.append({'course': course.model_dump(), 'priority': target.priority})
        if len({row['course']['lesson_id'] for row in rows}) != len(rows):
            raise CountQueryError('不同教学班对应重复内部ID，请核实基础信息', 'base_information')
        return rows

    async def configure(self, settings):
        if self.lock.locked():
            raise CountQueryError('一轮人数查询尚未完成，请稍后保存设置', 'busy')
        targets = self.resolve(settings.targets) if settings.targets else []
        async with self.lock:
            self.meta['phase'] = 'paused' if targets else 'idle'
            self.wake.set()
            self.settings = {'targets': targets, 'interval': settings.interval,
                             'stale_after': settings.stale_after, 'strategy': settings.strategy}
            # Scope/ID changes must not inherit an old count under a similar class code.
            valid = {row['course']['id']: row['course'] for row in targets}
            self.results = {key: value for key, value in self.results.items() if key in valid
                            and (value['lesson_id'], value['batch'], value['semester']) ==
                            (valid[key]['lesson_id'], valid[key]['batch'], valid[key]['semester'])}
            shared = self.store.get(self.mode + ':catalog_seats', {})
            for row in targets:
                c = row['course']
                if (tuple(shared.get('scope', ())) == (c['batch'], c['semester'])
                    and c['lesson_id'] in shared.get('counts', {}) and shared.get('updated_at')):
                    self.results.setdefault(c['id'], {'lesson_id':c['lesson_id'], 'batch':c['batch'], 'semester':c['semester'],
                        'enrolled_count':shared['counts'][c['lesson_id']], 'updated_at':shared['updated_at'], 'source':shared['source']})
            stamps = [self.results[cid]['updated_at'] for cid in valid if cid in self.results]
            self.meta['last_success'] = min(stamps) if len(stamps) == len(valid) and stamps else None
            self.meta.update(message='已保存教学班映射与基础优先级；监测尚未开始', last_error=None, failures=0)
            await self.publish()
        return self.public()

    def update_base(self):
        # Read cached metadata only; refreshing seats never refetches the course catalog.
        if self.settings['targets']:
            old = self.settings['targets']
            fresh = self.resolve([SeatTargetInput(class_code=row['course']['class_code'], priority=row['priority']) for row in old])
            if any((a['course']['lesson_id'], a['course']['batch'], a['course']['semester']) !=
                   (b['course']['lesson_id'], b['course']['batch'], b['course']['semester']) for a, b in zip(old, fresh)):
                raise CountQueryError('教学班内部ID或学期批次已变化，请重新确认监测目标', 'base_information')
            self.settings['targets'] = fresh

    async def refresh(self):
        if self.lock.locked():
            raise CountQueryError('当前批量查询尚未完成，不会启动重叠请求', 'busy')
        if time.monotonic() < self.next_allowed:
            raise CountQueryError('尚在查询间隔或学校限流等待期，请等待显示的下一次时间', 'cooldown')
        if not self.settings['targets']:
            raise CountQueryError('请先保存1–5个教学班', 'invalid_targets')
        async with self.lock:
            round_started = time.monotonic()
            self.meta.update(querying=True, last_attempt=now_iso(), message='正在进行一次批量人数查询')
            await self.publish()
            delay = self.settings['interval']
            success_due = None
            try:
                self.update_base()
                targets = self.settings['targets']
                scope = tuple(self.scope())
                response = await self.query([r['course']['lesson_id'] for r in targets], scope)
                counts = response['counts']
                if set(counts) != {row['course']['lesson_id'] for row in targets} or any(type(n) is not int or n < 0 for n in counts.values()):
                    raise CountQueryError('批量响应不完整，未覆盖上次正确数据', 'incomplete_response')
                moment = now_iso()
                records = {row['course']['id']: {'lesson_id': row['course']['lesson_id'],
                           'batch': row['course']['batch'], 'semester': row['course']['semester'],
                           'enrolled_count': counts[row['course']['lesson_id']],
                           'updated_at': moment, 'source': response['source']} for row in targets}
                self.results.update(records)
                delay = max(delay, response.get('server_delay', 0))
                success_due = max(response.get('request_started_monotonic', round_started) + self.settings['interval'],
                                  response.get('next_allowed_monotonic', 0),
                                  time.monotonic() + response.get('server_delay', 0))
                self.meta.update(last_success=moment, response_ms=response.get('response_ms'),
                                 failures=0, last_error=None, message='批量人数已更新，推荐顺序独立于原志愿顺序')
            except CountQueryError as error:
                if error.kind not in {'busy', 'cooldown'}:
                    self.meta['failures'] += 1
                    self.meta.update(last_error=str(error))
                delay = max(delay * 2 ** min(self.meta['failures'], 5), error.retry_after)
                self.meta.update(message=str(error))
                if error.kind in {'waiting_login', 'waiting_api', 'base_information'}:
                    self.meta['phase'] = error.kind
                elif self.meta['failures'] >= 5:
                    self.meta.update(phase='error', message='连续5轮未成功，已暂停监测；' + str(error))
            except Exception:
                self.meta['failures'] += 1
                delay = delay * 2 ** min(self.meta['failures'], 5)
                self.meta.update(last_error='人数查询未完成，保留上次正确数据', message='人数查询未完成，保留上次正确数据')
                if self.meta['failures'] >= 5:
                    self.meta['phase'] = 'error'
            finally:
                self.next_allowed = success_due if success_due is not None else time.monotonic() + delay
                wait = max(0, self.next_allowed-time.monotonic())
                self.meta.update(querying=False, next_allowed_at=datetime.fromtimestamp(time.time() + wait, timezone.utc).isoformat())
                await self.publish()
        return self.public()

    async def control(self, action):
        if action == 'start':
            if not self.settings['targets']:
                raise CountQueryError('请先保存1–5个教学班', 'invalid_targets')
            self.meta.update(phase='running', failures=0, message='按一个计时任务批量监测全部目标')
            if self.task is None or self.task.done():
                self.task = asyncio.create_task(self.run())
        elif action in {'pause', 'stop'}:
            self.meta.update(phase='paused' if action == 'pause' else 'stopped',
                             message='已暂停，保留基础信息和上次正确余量' if action == 'pause' else '已停止，保留目标和历史余量')
        self.wake.set()
        await self.publish()
        return self.public()

    async def run(self):
        while self.meta['phase'] == 'running':
            self.wake.clear()
            delay = max(0, self.next_allowed - time.monotonic())
            if delay:
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=delay)
                    continue
                except asyncio.TimeoutError:
                    pass
            if self.meta['phase'] != 'running':
                break
            try:
                await self.refresh()
            except CountQueryError:
                # A manual round may have acquired the lock first. Do not spin.
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=self.settings['interval'])
                except asyncio.TimeoutError:
                    pass

    def public(self):
        targets = self.settings['targets']
        courses = [Course.model_validate(row['course']) for row in targets]
        existing = self.existing()
        analysis = analyze_plan(Plan(course_ids=[c.id for c in courses]), courses, existing)
        blocked = set()
        for issue in analysis['issues']:
            if issue['kind'] == 'full':
                continue  # Fullness is decided from this module's latest count, not old catalog counts.
            if issue['kind'] == 'unknown' and issue.get('course_id') is None:
                blocked.update(c.id for c in courses)
            if issue.get('course_id'):
                blocked.add(issue['course_id'])
            if issue.get('other_id'):
                blocked.add(issue['other_id'])
        rows = []
        for index, (target, course) in enumerate(zip(targets, courses)):
            result = self.results.get(course.id, {})
            stamp = result.get('updated_at')
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds() if stamp else None
            except (ValueError, TypeError):
                age = None
            stale = age is None or age > max(self.settings['stale_after'], self.settings['interval'] * 2)
            selected = any(c.semester == course.semester and (c.lesson_id and c.lesson_id == course.lesson_id or c.id == course.id) for c in existing)
            count = result.get('enrolled_count')
            remaining = max(0, course.capacity-count) if course.capacity is not None and count is not None else None
            if selected:
                status, reason = 'selected', '学校课表已包含该教学班'
            elif course.id in blocked:
                status, reason = 'conflict', '上课时间冲突或尚未核实'
            elif stale:
                status, reason = 'stale', '暂无有效实时数据' if stamp is None else '数据已过期，仅供参考'
            elif remaining is None:
                status, reason = 'unknown', '人数上限或已选人数尚未核实'
            elif remaining == 0:
                status, reason = 'full', '已满员，暂不推荐'
            else:
                status, reason = 'available', '可作为独立选课模块的参考，仍需其核验'
            rows.append({'course': course.model_copy(update={'enrolled_count':count}).public(),
                         'count_source':result.get('source'), 'base_priority': target['priority'], 'original_index': index,
                         'enrolled_count': count, 'capacity': course.capacity, 'remaining': remaining,
                         'updated_at': stamp, 'stale': stale, 'status': status, 'reason': reason,
                         'eligible': status == 'available', 'recommended_rank': None})
        available = [row for row in rows if row['eligible']]
        strategy = self.settings['strategy']
        available.sort(key=lambda r: (r['base_priority'], r['remaining'], r['original_index']) if strategy == 'priority_first'
                       else (r['remaining'], r['base_priority'], r['original_index']))
        for rank, row in enumerate(available, 1):
            row['recommended_rank'] = rank
        return {'mode': self.mode, 'settings': {k:v for k,v in self.settings.items() if k != 'targets'},
                'targets': rows, 'recommendations': [r['course']['id'] for r in available],
                'meta': dict(self.meta), 'revision': self.revision,
                'submission_enabled': False}

    async def events(self):
        seen = -1
        while True:
            async with self.condition:
                if seen == self.revision:
                    try:
                        await asyncio.wait_for(self.condition.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        pass  # Publish staleness changes even while monitoring is paused.
                seen = self.revision
            yield self.public()

    async def ingest_catalog_counts(self, response, scope):
        async with self.lock:
            targets = self.settings['targets']
            if not targets or any((r['course']['batch'], r['course']['semester']) != tuple(scope)
                                  or r['course']['lesson_id'] not in response['counts'] for r in targets):
                return
            stamp = now_iso()
            for row in targets:
                c = row['course']
                self.results[c['id']] = {'lesson_id':c['lesson_id'], 'batch':c['batch'], 'semester':c['semester'],
                    'enrolled_count':response['counts'][c['lesson_id']], 'updated_at':stamp, 'source':response['source']}
            self.meta.update(last_success=stamp, response_ms=response.get('response_ms'), failures=0,
                             last_error=None, message='已复用本次目录批量查询结果，原志愿顺序未更改')
            delay = max(self.settings['interval'], response.get('server_delay', 0))
            due=max(response.get('request_started_monotonic',time.monotonic())+self.settings['interval'],
                    response.get('next_allowed_monotonic',0),time.monotonic()+response.get('server_delay',0))
            self.next_allowed = max(self.next_allowed, due)
            self.meta['next_allowed_at'] = datetime.fromtimestamp(time.time()+max(0,self.next_allowed-time.monotonic()),timezone.utc).isoformat()
            await self.publish()

    async def close(self):
        self.meta['phase'] = 'paused' if self.settings['targets'] else 'idle'
        self.wake.set()
        if self.task:
            await self.task  # Let a bounded in-flight round complete; never overlap or discard it.
        await self.publish()
