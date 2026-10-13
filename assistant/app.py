from __future__ import annotations
import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, SecretStr
from playwright.async_api import Error as PlaywrightError
from .browser import BrowserIssue, SchoolSession
from .demo import DemoAdapter, required_courses
from .engine import Engine
from .models import Course, Mode, Plan, analyze_plan
from .store import Store
from .calendar import default_calendar, merge_calendar
from .sync import TimetableSync
from .catalog_sync import CatalogSync
from .workflow import guide, run_summary
from .seat_monitor import SeatMonitor, SeatSettings, now_iso
from .counts import CountQueryError


ROOT = Path(__file__).resolve().parent
DEFAULT_STATE = ROOT.parent / ".state"


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: SecretStr = Field(min_length=1, max_length=128)
    captcha: str = Field(min_length=1, max_length=12)


class ImportInput(BaseModel):
    catalog: list[Course] = Field(default_factory=list, max_length=5000)
    existing: list[Course] = Field(default_factory=list, max_length=1000)
    coverage: str = "unknown"
    calendar: dict | None = None


class SyncSettings(BaseModel):
    enabled: bool
    interval: int = Field(default=60, ge=30, le=300)


def create_app(state_dir: Path | None = None):
    state_dir = state_dir or Path(os.environ.get("CUPK_STATE_DIR", DEFAULT_STATE))
    if getattr(sys, 'frozen', False):
        from .consent import has_consent
        if not has_consent(state_dir):
            raise RuntimeError('请先阅读并同意用户协议')
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_dir.chmod(0o700)

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(state_dir / "assistant.sqlite3")
        app.state.demo = DemoAdapter(app.state.store)
        app.state.engine = Engine(app.state.store, app.state.demo)
        app.state.school = SchoolSession(state_dir)
        app.state.sync = TimetableSync(app.state.store, app.state.school)
        app.state.sync.start()
        app.state.catalog_sync = CatalogSync(app.state.store, app.state.school)
        app.state.catalog_sync.start()
        app.state.seat_monitors = make_seat_monitors(app)
        await app.state.engine.reconcile()
        yield
        await app.state.engine.stop()
        if app.state.engine.active:
            await app.state.engine.task
        await app.state.sync.close()
        await app.state.catalog_sync.close()
        for monitor in app.state.seat_monitors.values():
            await monitor.close()
        await app.state.school.close()
        app.state.store.close()

    app = FastAPI(title="CUPK 选课助手", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_only(request, call_next):
        if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD"}:
            origin = request.headers.get("origin")
            if request.headers.get("x-cupk-client") != "local" or (origin and urlparse(origin).netloc != request.headers.get("host")):
                return JSONResponse({"detail": "仅允许本地应用页面发起操作"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Do not echo invalid password or other request inputs in FastAPI's 422.
        return JSONResponse({"detail": "输入格式不正确，请检查课程字段或表单",
                             "fields": [".".join(str(v) for v in item["loc"]) for item in error.errors()]}, status_code=422)

    @app.exception_handler(BrowserIssue)
    async def browser_error(request, error):
        if request.url.path in {"/api/browser/login", "/api/browser/catalog", "/api/browser/semesters"}:
            if app.state.school.activity.data["state"] == "running":
                app.state.school.activity.finish("failed", str(error))
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(PlaywrightError)
    async def playwright_error(request, error):
        # Browser error call logs can contain request headers or form arguments.
        # Handle them without echoing the upstream error or its authentication data.
        app.state.school._set("uncertain", "页面或网络操作未完成，已保留登录 Profile")
        app.state.school.activity.finish("failed", "页面或网络操作未完成，请重试；会话保留")
        return JSONResponse({"detail": "页面或网络操作未完成，请检查会话后重试"}, status_code=503)

    @app.exception_handler(ValueError)
    async def value_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    def catalog(mode):
        if mode == "demo":
            return app.state.demo.catalog
        return [Course.model_validate(item) for item in app.state.store.get("school:catalog", [])]

    def existing(mode):
        if mode == "demo":
            return required_courses() + [c for c in app.state.demo.catalog if c.id in app.state.demo.selected]
        return [Course.model_validate(item) for item in app.state.store.get("school:existing", [])]

    def plan(mode):
        return Plan.model_validate(app.state.store.get(f"{mode}:plan", {}))

    def make_seat_monitors(application):
        async def demo_counts(ids, scope):
            snapshot = await application.state.demo.snapshot()
            values = {c.lesson_id:c.enrolled_count for c in snapshot.courses if c.lesson_id in ids}
            return {'counts': values, 'response_ms': 0, 'source': 'demo_simulation'}

        def school_scope():
            meta = application.state.store.get('school:catalog_sync', {})
            return str(meta.get('batch', 'unverified')), str(meta.get('semester', 'unverified'))

        return {
            'demo': SeatMonitor(application.state.store, 'demo', lambda: catalog('demo'),
                                lambda: existing('demo'), demo_counts,
                                lambda: ('demo-autumn', '2026-2027-1')),
            'school': SeatMonitor(application.state.store, 'school', lambda: catalog('school'),
                                  lambda: existing('school'), application.state.school.count_query.query, school_scope)}

    app.state.seat_monitor_factory = make_seat_monitors

    @app.exception_handler(CountQueryError)
    async def count_error(request, error):
        return JSONResponse({'detail': str(error), 'kind': error.kind}, status_code=409)

    @app.get('/api/seats')
    async def seat_status(mode: Mode = 'school'):
        return app.state.seat_monitors[mode].public()

    @app.put('/api/seats/settings')
    async def seat_settings(data: SeatSettings, mode: Mode = 'school'):
        monitor = app.state.seat_monitors[mode]
        if data.refresh_base and mode == 'school':
            await app.state.catalog_sync.read_all()
        else:
            try:
                monitor.resolve(data.targets)
            except CountQueryError as error:
                if mode != 'school' or error.kind != 'base_information':
                    raise
                await app.state.catalog_sync.read_all()
        return await monitor.configure(data)

    @app.post('/api/seats/refresh')
    async def seat_refresh(mode: Mode = 'school'):
        return await app.state.seat_monitors[mode].refresh()

    @app.post('/api/seats/{action}')
    async def seat_control(action: str, mode: Mode = 'school'):
        if action not in {'start', 'pause', 'stop'}:
            raise HTTPException(404, '不存在的监测操作')
        return await app.state.seat_monitors[mode].control(action)

    @app.get('/api/seats/recommendations')
    async def seat_recommendations(mode: Mode = 'school'):
        return app.state.seat_monitors[mode].public()

    @app.get('/api/seats/catalog')
    async def catalog_seats(mode: Mode = 'school'):
        saved = app.state.store.get(mode + ':catalog_seats', {})
        scope = tuple(app.state.seat_monitors[mode].scope())
        rows = []
        counts = saved.get('counts', {}) if tuple(saved.get('scope', ())) == scope else {}
        stamp = saved.get('updated_at') if counts else None
        for course in catalog(mode):
            if (course.batch, course.semester) != scope:
                continue
            count = counts.get(course.lesson_id)
            rows.append({'course_id':course.id, 'name':course.name, 'class_code':course.class_code,
                         'capacity':course.capacity, 'enrolled_count':count,
                         'remaining':max(0,course.capacity-count) if course.capacity is not None and count is not None else None})
        limit = app.state.school.count_query.template.get('observed_size',0) if app.state.school.count_query.template else 0
        return {'rows':rows, 'updated_at':stamp, 'response_ms':saved.get('response_ms'),
                'observed_batch_limit':limit if mode=='school' else len(rows), 'scope':'当前允许的课程目录'}

    @app.post('/api/seats/catalog/refresh')
    async def refresh_catalog_seats(mode: Mode = 'school'):
        monitor = app.state.seat_monitors[mode]
        scope = tuple(monitor.scope())
        courses = [c for c in catalog(mode) if (c.batch,c.semester) == scope]
        ids = [c.lesson_id for c in courses]
        if not ids or any(not ident for ident in ids) or len(set(ids)) != len(ids):
            raise CountQueryError('当前课程目录内部ID尚未核实，未发送查询', 'base_information')
        result = await app.state.school.count_query.query_catalog(ids, scope) if mode == 'school' else await monitor.query(ids, scope)
        if set(result['counts']) != set(ids):
            raise CountQueryError('目录人数响应不完整，保留上次快照', 'incomplete_response')
        app.state.store.put(mode + ':catalog_seats', {k:result[k] for k in ('counts','source','response_ms')} |
                            {'scope':list(scope), 'updated_at':now_iso()})
        await monitor.ingest_catalog_counts(result, scope)
        return await catalog_seats(mode)

    @app.get('/api/seats/events')
    async def seat_events(request: Request, mode: Mode = 'school'):
        async def stream():
            async for snapshot in app.state.seat_monitors[mode].events():
                if await request.is_disconnected():
                    break
                yield 'event: snapshot\ndata: ' + json.dumps(snapshot, ensure_ascii=False) + '\n\n'
        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.get("/api/state")
    async def get_state(mode: Mode = "demo"):
        await app.state.catalog_sync.observe()
        courses, current, selected_plan = catalog(mode), existing(mode), plan(mode)
        coverage = "complete" if mode == "demo" else app.state.store.get("school:coverage", "unknown")
        analysis = analyze_plan(selected_plan, courses, current)
        if mode == "school" and coverage != "complete":
            analysis["issues"].append({"course_id": None, "kind": "coverage", "message": "已有课程尚未完整，冲突检查仅供参考"})
            analysis["can_run"] = False
        sync = app.state.sync.public()
        if mode == "school" and sync["state"] in {"error", "waiting_login", "partial", "interrupted", "awaiting_semester"}:
            analysis["issues"].append({"course_id": None, "kind": "freshness", "message": "学校课表未能更新，请先检查登录或重新同步"})
            analysis["can_run"] = False
        run=app.state.engine.run_state if mode=='demo' else {'phase':'idle','targets':[],'events':[]}
        return {"mode": mode, "catalog": [c.public() for c in courses],
                "existing": [c.public() for c in current], "plan": selected_plan.model_dump(),
                "analysis": analysis,
                "run":{**run,'summary':run_summary(run)},
                "guide":guide(mode,app.state.school.public_status(),courses,current,selected_plan,analysis,coverage,sync,app.state.catalog_sync.public(),run),
                "session": app.state.school.public_status(),
                "coverage": coverage,
                "calendar": default_calendar() if mode == "demo" else app.state.store.get("school:calendar", default_calendar()),
                "sync": sync,
                "catalog_sync": app.state.catalog_sync.public(),
                "capabilities": {"execute": mode == "demo", "live_submit": False}}

    @app.put("/api/plan")
    async def save_plan(data: Plan, mode: Mode = "demo"):
        if mode == "demo" and app.state.engine.active:
            raise HTTPException(409, "演练正在执行，请停止后再修改计划")
        available = {c.id for c in catalog(mode)}
        if not set(data.course_ids) <= available:
            raise HTTPException(422, "计划包含当前目录之外的课程")
        app.state.store.put(f"{mode}:plan", data.model_dump())
        return {"saved": True}

    @app.post("/api/run/start")
    async def start_run(mode: Mode = "demo"):
        if mode != "demo":
            raise HTTPException(409, "本校真实选课提交尚待适配验证；当前可以登录、预选和检查冲突")
        await app.state.engine.start(plan(mode), existing(mode))
        return {"started": True, "mode": "demo"}

    @app.post("/api/run/stop")
    async def stop_run():
        await app.state.engine.stop()
        return {"stopping": True}

    @app.post('/api/run/resume')
    async def resume_run(mode:Mode='demo'):
        if mode!='demo':
            raise HTTPException(409,'本校真实提交与结果核验仍待验证；学校计划已保存')
        await app.state.engine.resume(plan(mode),existing(mode))
        return {'resumed':True}

    @app.post("/api/run/verify")
    async def verify_run(mode: Mode = "demo"):
        if mode != "demo":
            raise HTTPException(409, "本校完整已选列表核验接口尚待验证，可先导入课表")
        await app.state.engine.reconcile()
        return {"verified": True}

    @app.post("/api/demo/reset")
    async def reset_demo():
        if app.state.engine.active:
            raise HTTPException(409, "请先停止当前演练")
        app.state.demo.reset()
        app.state.engine.run_state = {"phase": "idle", "targets": [], "events": []}
        app.state.engine.save()
        return {"reset": True}

    @app.post("/api/school/import")
    async def import_courses(data: ImportInput):
        if app.state.sync.lock.locked():
            raise HTTPException(409, "学校课表正在同步，完成后再导入文件")
        if len({c.id for c in data.catalog}) != len(data.catalog) or len({c.id for c in data.existing}) != len(data.existing):
            raise HTTPException(422, "课程文件含有重复记录 ID")
        def normalize(course):
            course = course.model_copy(deep=True)
            course.source = "school"
            for field in ("semester", "batch", "entry"):
                if field not in course.model_fields_set:
                    setattr(course, field, "unverified")
            if "meetings" not in course.model_fields_set and "schedule_known" not in course.model_fields_set:
                course.schedule_known = False
            return course.model_dump()
        app.state.store.put("school:catalog", [normalize(c) for c in data.catalog])
        from .activity import now_iso
        app.state.catalog_sync.meta = {'state': 'imported', 'last_success': now_iso(),
            'message': f'课程文件已导入 {len(data.catalog)} 个教学班，学校目录完整性待核实',
            'coverage': 'unknown', 'source': 'import_file', 'saved_count': len(data.catalog)}
        app.state.store.put('school:catalog_sync', app.state.catalog_sync.meta)
        app.state.store.put("school:existing", [normalize(c) for c in data.existing])
        app.state.store.put("school:coverage", data.coverage if data.coverage in {"complete", "scheduled_only"} else "unknown")
        if data.calendar:
            metadata = merge_calendar(data.calendar, data.calendar.get("time_slots", []), data.calendar.get("semester_name", "2026-2027-1"))
            metadata["calendar_source"] = "课程文件中的学期信息"
            metadata["time_source"] = "课程文件中的时刻，缺失节次采用用户校历"
            app.state.store.put("school:calendar", metadata)
        app.state.sync.record_import(len(data.existing), app.state.store.get("school:coverage"))
        previous = plan("school")
        previous.course_ids = [cid for cid in previous.course_ids if cid in {c.id for c in data.catalog}]
        app.state.store.put("school:plan", previous.model_dump())
        return {"imported": len(data.catalog), "existing": len(data.existing)}

    @app.post("/api/browser/connect")
    async def connect_browser():
        result = await app.state.school.connect()
        await app.state.catalog_sync.observe(force=True)
        await import_current_if_known()
        return {**app.state.school.public_status(), "timetable": app.state.sync.public(),
                "catalog_sync": app.state.catalog_sync.public()}

    async def import_current_if_known():
        if app.state.school.status["state"] != "connected":
            return
        try:
            await app.state.school.semesters()
            current = app.state.school.current_semester
            if current and (app.state.sync.meta.get("semester") != current or not app.state.sync.meta.get("last_success")):
                await app.state.sync.sync(current)
            elif not current:
                app.state.sync.meta.update(state="awaiting_semester", message="学校未明确指定当前学期，请选择本学期并导入已有课表")
                app.state.sync.activity.begin("自动导入已有课表")
                app.state.sync.activity.step("semester", "需要选择学校当前学期")
                app.state.sync.activity.finish("waiting_user", app.state.sync.meta["message"])
                app.state.sync.save()
        except (BrowserIssue, PlaywrightError) as error:
            app.state.sync.meta.update(state="error", message=str(error) if isinstance(error, BrowserIssue) else "登录已完成，但课表读取失败；请在课表页面重试")
            app.state.sync.activity.begin("自动导入已有课表")
            app.state.sync.activity.finish("failed", app.state.sync.meta["message"])
            app.state.sync.save()

    @app.get("/api/browser/status")
    async def browser_status():
        await app.state.catalog_sync.observe()
        return app.state.school.public_status()

    @app.get('/api/browser/diagnostics')
    async def browser_diagnostics():
        return await app.state.school.diagnostics()

    @app.get("/api/browser/captcha")
    async def captcha_image():
        return Response(await app.state.school.captcha(), media_type="image/png")

    @app.post("/api/browser/captcha/refresh")
    async def refresh_captcha():
        await app.state.school.captcha(refresh=True)
        return {"refreshed": True}

    @app.post("/api/browser/login")
    async def login(data: LoginInput):
        result = await app.state.school.login(data.username, data.password.get_secret_value(), data.captcha)
        await app.state.catalog_sync.observe(force=True)
        await import_current_if_known()
        return {**app.state.school.public_status(), "timetable": app.state.sync.public(),
                "catalog_sync": app.state.catalog_sync.public()}

    @app.get("/api/browser/view")
    async def school_view(kind: str = "selection"):
        if kind not in {"selection", "timetable"}:
            raise HTTPException(422, "窗口类型无效")
        return Response(await app.state.school.view(kind), media_type="image/png")

    @app.get("/api/browser/semesters")
    async def get_semesters():
        return await app.state.school.semesters()

    @app.post("/api/browser/schedule/{semester}")
    async def import_schedule(semester: str):
        result = await app.state.sync.sync(semester)
        return {"imported": result.get("course_count", 0), "coverage": app.state.store.get("school:coverage"), "sync": result}

    @app.post("/api/timetable/sync")
    async def refresh_timetable():
        semester = app.state.sync.config.get("semester")
        if not semester:
            raise HTTPException(409, "请先选择学校当前学期并导入课表")
        return await app.state.sync.sync(semester)

    @app.put("/api/timetable/settings")
    async def configure_sync(data: SyncSettings):
        app.state.sync.configure(data.enabled, data.interval)
        return app.state.sync.public()

    @app.post("/api/browser/catalog")
    async def import_catalog():
        count = await app.state.catalog_sync.read()
        return {"imported": count, "fields": app.state.school.fields,
                "catalog_sync": app.state.catalog_sync.public()}

    @app.post('/api/browser/catalog/all')
    async def import_all_catalog():
        return {'catalog_sync': await app.state.catalog_sync.read_all()}

    @app.get("/api/export")
    async def export(mode: Mode = "demo"):
        state = await get_state(mode)
        # Explicitly omit browser Profile and all authentication material.
        payload = {key: state[key] for key in ("catalog", "existing", "plan", "coverage", "run", "calendar")}
        return Response(json.dumps(payload, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="cupk-{mode}-plan.json"'})

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/api/health")
    async def health():
        return {"application": "cupk-course-assistant", "version": "0.4.0",
                "desktop_instance": os.environ.get('CUPK_DESKTOP_INSTANCE')}

    @app.get("/agreement")
    async def agreement():
        return FileResponse(ROOT.parent / 'docs' / 'USER_AGREEMENT.md', media_type='text/plain; charset=utf-8')

    @app.get("/open-source-notices")
    async def open_source_notices():
        return FileResponse(ROOT.parent / 'THIRD_PARTY_NOTICES.md', media_type='text/plain; charset=utf-8')

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "static" / "index.html")

    return app


app = create_app()
