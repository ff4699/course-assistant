"""Shared catalog observer; separate from the existing-timetable synchronizer."""
import asyncio
from .activity import now_iso
from .browser import BrowserIssue


class CatalogSync:
    def __init__(self, store, school):
        self.store, self.school = store, school
        self.meta = store.get('school:catalog_sync', {'state': 'idle', 'last_success': None,
            'message': '正常进入课程列表后自动读取目录', 'coverage': 'unknown'})
        self.worker = None
        self.attempted_scopes = set()
        if self.meta['state'] == 'running':
            self.meta.update(state='interrupted', message='上次目录读取中断，保留已读取课程，完整性待核实')

    def public(self):
        return dict(self.meta)

    def save_snapshot(self, snapshot):
        courses = snapshot['courses']
        if not courses:
            self.failed('学校当前显示空列表，保留上次目录', state='empty')
            return 0
        rows = [c.model_dump() for c in courses]
        previous = self.store.get('school:catalog', [])
        same_scope = snapshot['batch'] != 'unverified' and self.meta.get('batch') == snapshot['batch'] and self.meta.get('semester') == snapshot['semester']
        complete = snapshot.get('coverage') == 'complete'
        merged = {r['id']: r for r in previous} if same_scope and not complete else {}
        merged.update({r['id']: r for r in rows})
        saved = list(merged.values())
        changed = saved != previous or self.meta.get('state') != 'success' or complete
        self.meta.update(state='success', last_success=now_iso(), batch=snapshot['batch'],
            semester=snapshot['semester'], coverage='complete' if complete else 'current_page', source=snapshot['source'],
            evidence=snapshot['evidence'], current_count=len(rows), saved_count=len(saved),
            context_verified=snapshot['batch'] != 'unverified' and snapshot['semester'] != 'unverified',
            message=f'当前页面读到 {len(rows)} 个教学班，已保存 {len(saved)} 个；完整性、批次和学期以学校页面核验为准')
        if complete:
            self.meta.pop('last_error', None)
            self.meta.pop('last_error_at', None)
            self.meta.update(last_complete=now_iso(), complete_count=len(rows), pages_read=snapshot['pages_read'],
                message=f"已按学校总数核对 {snapshot['pages_read']} 页、{len(rows)} 个教学班；学期与批次仍需核实" if not self.meta['context_verified'] else
                        f"已核对全部 {snapshot['pages_read']} 页、{len(rows)} 个教学班")
        elif same_scope and self.meta.get('last_complete'):
            self.meta['message'] += f"；上次全量读取 {self.meta['complete_count']} 个"
        elif not same_scope:
            for key in ('last_complete', 'complete_count', 'pages_read'):
                self.meta.pop(key, None)
        if self.meta.get('last_error'):
            self.meta['message'] += '；上次未完成：' + self.meta['last_error']
        if changed:
            self.store.put('school:catalog', saved)
            self.store.put('school:catalog_sync', self.meta)
        # Do not prune or reorder a user's saved plan when only one page is read.
        return len(rows)

    def failed(self, message, state='error'):
        if self.meta.get('state') == state and self.meta.get('message') == message:
            return
        self.meta.update(state=state, message=message)
        if state in {'error', 'interrupted'}:
            self.meta.update(last_error=message, last_error_at=now_iso())
        self.store.put('school:catalog_sync', self.meta)

    async def observe(self, force=False):
        snapshot = await self.school.refresh_selection(force=force)
        if snapshot is not None:
            self.save_snapshot(snapshot)
        elif self.school.status['selection_stage'] in {'auth_required', 'error', 'closed'}:
            self.failed(self.school.status['message'], state='waiting_login' if
                        self.school.status['selection_stage'] == 'auth_required' else 'error')
        elif self.school.context is not None and not self.school.status['selection_ready']:
            self.failed(self.school.status['message'], state='waiting_page')

    async def read(self):
        try:
            await self.school.read_catalog()
            return self.save_snapshot(self.school.catalog_snapshot)
        except BrowserIssue as error:
            self.failed(str(error))
            raise

    async def read_all(self):
        if self.school.lock.locked():
            raise BrowserIssue('学校页面正在读取，请等待当前操作完成')
        self.meta.update(state='running', message='正在按学校页码批量读取课程目录')
        self.store.put('school:catalog_sync', self.meta)
        def on_page(snapshot):
            self.save_snapshot(snapshot)
            self.meta.update(state='running', message=f"正在读取第 {snapshot['pagination']['page']} 页，已保存 {self.meta['saved_count']} 个教学班")
            self.store.put('school:catalog_sync', self.meta)
        try:
            final = await self.school.read_all_catalog(on_page)
            self.save_snapshot(final)
            return self.public()
        except asyncio.CancelledError:
            self.failed('批量读取已中断，保留已读取课程，完整性待核实', state='interrupted')
            raise
        except Exception as error:
            self.failed(str(error) if isinstance(error, BrowserIssue) else '批量读取未完成，保留已读取目录')
            if isinstance(error, BrowserIssue):
                raise
            raise BrowserIssue('批量读取未完成，保留已读取目录') from None

    async def _run(self):
        while True:
            await asyncio.sleep(3)
            try:
                await self.observe()
                snapshot = self.school.catalog_snapshot
                if snapshot and self.school.status['selection_ready']:
                    pager = snapshot.get('pagination', {})
                    key = (snapshot['batch'], snapshot['semester'], pager.get('total'))
                    if snapshot.get('coverage') != 'complete' and key not in self.attempted_scopes and snapshot['batch'] != 'unverified' and pager.get('page') == 1 and type(pager.get('total')) is int and not pager.get('filtered'):
                        self.attempted_scopes.add(key)
                        await self.read_all()
            except BrowserIssue:
                pass  # read_all already saved the specific failure and retained pages.
            except Exception:
                self.failed('课程目录观察暂未完成，保留上次目录')

    def start(self):
        self.worker = asyncio.create_task(self._run())

    async def close(self):
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
