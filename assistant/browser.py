from __future__ import annotations
import asyncio
import hashlib
import os
import re
import shutil
import sys
import time
import json
import httpx
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout, Error as BrowserError
from .models import Course
from .schedule import normalize_print_data, merge_all_course_rows, merge_schedule_sources
from .calendar import merge_calendar
from .activity import Activity, now_iso
from .catalog import CATALOG_VIEW, normalize_catalog, normalize_school_data


CAS = "https://cas.cupk.edu.cn/cas/login?service=https://portal.cupk.edu.cn/portal/index_sso.jsp"
BASE = "https://eams.cupk.edu.cn"
HOSTS = {"cas.cupk.edu.cn", "portal.cupk.edu.cn", "eams.cupk.edu.cn"}

class BrowserIssue(Exception):
    pass


def catalog_identity(batch, lesson_id, code, class_code, name, teacher, time_text):
    if lesson_id:
        return f"school:catalog:{batch}:{lesson_id}"
    if not class_code and not time_text:
        raise BrowserIssue("课程行缺少教学班或时间标识，无法可靠区分；请导入完整课程文件")
    stable = (batch, code, class_code, name, teacher, time_text)
    return f"school:catalog:{batch}:" + hashlib.sha256(repr(stable).encode()).hexdigest()[:20]


class SemesterParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.option = None
        self.semesters = []
        self.selected = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select":
            self.active = attrs.get("id") in {"semesters", "allSemesters"}
        if self.active and tag == "option":
            ident = attrs.get("value", "")
            self.option = {"id": ident, "name": ""} if ident.isdecimal() else None
            if self.option is not None and "selected" in attrs:
                self.selected = ident

    def handle_data(self, data):
        if self.option is not None:
            self.option["name"] += data

    def handle_endtag(self, tag):
        if tag == "option" and self.option is not None:
            self.option["name"] = self.option["name"].strip()
            self.semesters.append(self.option)
            self.option = None
        if tag == "select":
            self.active = False


class SchoolSession:
    """One persistent context per local app; normal auth only, read-only school mode."""
    def __init__(self, state_dir: Path):
        self.profile = state_dir / "browser-profile"
        self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.profile.chmod(0o700)
        self.context = self.page = self.playwright = None
        self.context_closed = False
        self.table_page = None
        self.activity = Activity()
        self.view_lock = asyncio.Lock()
        self.frame = None
        self.frame_at = None
        self.frames = {}
        self.current_semester = None
        self.lock = asyncio.Lock()
        self.file_lock = None
        self.status = {"state": "not_started", "message": "尚未连接教务系统",
                       "last_checked": None, "selection_ready": False,
                       "selection_stage": "not_started",
                       "live_submission_enabled": False}
        self.fields = []
        self.catalog_snapshot = None
        self.last_probe = 0
        self.last_auth_check = 0
        self.authenticated = False
        self.catalog_frame = None
        self.catalog_request_template = None
        self.catalog_origin_page = None
        self.response_tasks = set()
        self.selection_auth_failed = False
        from .counts import ObservedCountClient
        self.count_query = ObservedCountClient(self)

    def public_status(self):
        return {**self.status, "browser_running": self.context is not None and not self.context_closed,
                "activity": self.activity.public(), "frame_at": self.frame_at,
                "current_semester": self.current_semester,
                "profile_saved": any(self.profile.iterdir())}

    def _set(self, state, message, **extra):
        if state != 'connected':
            extra.setdefault('selection_ready', False)
        if extra.get('selection_ready') is False:
            extra.setdefault('selection_evidence', None)
        self.status.update(state=state, message=message,
                           last_checked=datetime.now(timezone.utc).isoformat(), **extra)

    def _acquire_profile(self):
        lock_path = self.profile.parent / "browser.lock"
        handle = open(lock_path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise BrowserIssue("此登录 Profile 已被另一进程使用，请使用已有服务")
        self.file_lock = handle

    def _release_profile(self):
        if self.file_lock:
            self.file_lock.close()
            self.file_lock = None

    async def _guard(self, route):
        request = route.request
        parsed = urlparse(request.url)
        blocked = re.search(r"(?:^|/)(?:add|drop|delete|save|submit|enroll|withdraw|add-request|drop-request|add-drop-request)(?:/|$)|addCourse|dropCourse|select-course|cancel-course", parsed.path, re.I)
        if parsed.hostname not in HOSTS or request.method in {"PUT", "PATCH", "DELETE"} or blocked:
            await route.abort()
            return
        # Until this school's mutation API is verified, only read-like POSTs are
        # permitted in its separate selection application. CAS login is unaffected.
        if request.method == "POST" and parsed.path.startswith("/course-selection/"):
            if not re.search(r"(?:get|list|query|search|count|info|detail|turn|semester|selected|addable|login|auth|token)", parsed.path, re.I):
                await route.abort()
                return
        await route.continue_()

    async def _ensure(self):
        if self.context is not None and self.context_closed:
            if self.playwright:
                await self.playwright.stop()
            self.context = self.playwright = self.page = None
            self._release_profile()
        if self.context is not None:
            return
        self._acquire_profile()
        try:
            self.playwright = await async_playwright().start()
            desktop = os.name == "nt" or sys.platform == "darwin" or bool(os.environ.get("DISPLAY"))
            headless = os.environ.get("CUPK_HEADLESS", "0" if desktop else "1") != "0"
            options = {"headless": headless, "viewport": {"width": 1440, "height": 1000}}
            from tools.browser_runtime import bundled_browser
            binary = os.environ.get("CUPK_CHROMIUM") or bundled_browser(Path(__file__).resolve().parents[1]) or shutil.which("chromium") or shutil.which("google-chrome")
            if binary:
                options["executable_path"] = binary
            if os.name != "nt":
                options["args"] = ["--no-sandbox"]
            proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
            if proxy:
                options["proxy"] = {"server": proxy, "bypass": "127.0.0.1,localhost"}
            from .consent import disable_password_storage
            disable_password_storage(self.profile)
            self.context = await self.playwright.chromium.launch_persistent_context(str(self.profile), **options)
            self.context_closed = False
            self.context.on("close", lambda *_: setattr(self, "context_closed", True))
            await self.context.route("**/*", self._guard)
            self.context.on('response', self._observe_catalog_response)
            self.count_query.attach(self.context)
            pages = self.context.pages
            self.page = next((p for p in pages if urlparse(p.url).hostname == "eams.cupk.edu.cn"),
                             pages[0] if pages else await self.context.new_page())
        except Exception:
            if self.playwright:
                await self.playwright.stop()
            self.context = self.playwright = None
            self._release_profile()
            raise BrowserIssue("浏览器启动失败，请安装 Chromium 或运行 playwright install chromium")

    async def _student_authenticated(self):
        response = await self.context.request.get(BASE + "/student/home", timeout=20000)
        parsed = urlparse(response.url)
        if response.status in (401, 403) or parsed.path.endswith("/login") or parsed.hostname == "cas.cupk.edu.cn":
            self.authenticated = False
            self.last_auth_check = time.monotonic()
            return False
        if not response.ok:
            raise BrowserIssue("教务系统暂时不可用，保留当前会话，请稍后重试")
        text = await response.text()
        # A real logout anchor, not a translation literal in a script or HTTP 200.
        self.authenticated = parsed.path == "/student/home" and bool(re.search(r'href=["\x27][^"\x27]*?/student/logout-emitter', text))
        self.last_auth_check = time.monotonic()
        return self.authenticated

    async def _manual_page(self):
        if urlparse(self.page.url).hostname != "cas.cupk.edu.cn" or not await self.page.locator("#captcha_img").count():
            await self.page.goto(CAS, wait_until="domcontentloaded", timeout=30000)
        if await self.page.locator("#captcha_img").count():
            self._set("manual_required", "需要人工填写验证码；验证后保留登录会话", selection_ready=False)
        else:
            self._set("uncertain", "登录流程仍在跳转，请稍后检查", selection_ready=False)

    async def _selection_view(self):
        if self.selection_auth_failed:
            return {'catalog': False, 'empty': False, 'no_batches': False, 'auth_required': True}
        candidates = [p for p in self.context.pages if not p.is_closed() and p != self.table_page
                      and self._selection_url(p.url)]
        preferred = self.catalog_origin_page if self.catalog_origin_page in candidates else self.page
        if preferred in candidates:
            candidates.remove(preferred)
            candidates.insert(0, preferred)
        found, no_batches, auth_required = [], False, False
        for page in candidates:
            for frame in page.frames:
                if frame != page.main_frame:
                    if not self._selection_url(frame.url):
                        continue
                    if not await (await frame.frame_element()).is_visible():
                        continue
                raw = await frame.evaluate(CATALOG_VIEW)
                no_batches = no_batches or raw['no_batches']
                auth_required = auth_required or raw['auth_required']
                if raw['auth_required']:
                    continue
                try:
                    snapshot = normalize_school_data(raw['schoolData'], raw, catalog_identity) if raw.get('schoolData') else normalize_catalog(raw, frame.url, catalog_identity)
                except (ValueError, BrowserIssue):
                    raise BrowserIssue("课程教学班或时间标识尚未匹配，保留上次目录；请检查学校页面")
                if snapshot['courses'] or snapshot['empty']:
                    snapshot['evidence'] = 'iframe_dom' if frame != page.main_frame else 'page_dom'
                    snapshot['source'] = 'school_page_state' if raw.get('schoolData') else 'school_dom'
                    snapshot['_frame'] = frame
                    found.append((page, snapshot))
        # Multiple live batch views need an explicit user choice. Never combine them.
        scopes = {(s['batch'], s['semester']) for _, s in found}
        if len(scopes) > 1 or len({id(p) for p, _ in found}) > 1:
            self.catalog_snapshot = None
            return {'catalog': False, 'empty': False, 'no_batches': False, 'ambiguous': True}
        if found:
            self.page, self.catalog_snapshot = found[0]
            self.catalog_frame = self.catalog_snapshot['_frame']
            self.fields = self.catalog_snapshot['fields']
            return {'catalog': True, 'empty': self.catalog_snapshot['empty'], 'no_batches': False}
        self.catalog_snapshot = None
        return {'catalog': False, 'empty': False, 'no_batches': no_batches, 'auth_required': auth_required}

    @staticmethod
    def _selection_url(url):
        parsed = urlparse(url)
        return parsed.hostname in HOSTS and (parsed.path.startswith('/course-selection/') or
                                             parsed.path.startswith('/student/for-std/course-select'))

    async def refresh_selection(self, force=False):
        """Observe manual navigation without launching, clicking, or reloading a page."""
        if self.lock.locked() or not force and time.monotonic() - self.last_probe < 3:
            return None
        async with self.lock:
            self.last_probe = time.monotonic()
            if self.context is None or self.context_closed:
                if self.status['selection_ready']:
                    self._set('uncertain', '学校窗口已关闭，保留上次目录', selection_ready=False,
                              selection_stage='closed')
                return None
            try:
                if time.monotonic() - self.last_auth_check >= 30:
                    await self._student_authenticated()
                if not self.authenticated:
                    self._set('manual_required', '学校登录尚未确认或已失效，请在学校窗口正常登录',
                              selection_ready=False, selection_stage='auth_required')
                    return None
                view = await self._selection_view()
                if view['catalog']:
                    self._set('connected', '课程列表已打开，已读取当前页面课程', selection_ready=True,
                              selection_stage='empty' if view['empty'] else 'catalog',
                              selection_evidence=self.catalog_snapshot['evidence'])
                    return self.catalog_snapshot
                message = ('发现多个课程列表，请关闭无关批次窗口后继续读取' if view.get('ambiguous') else
                           '选课应用要求正常登录验证' if view.get('auth_required') else
                           '当前未检测到可读取的课程列表，保留上次目录')
                self._set('uncertain' if view.get('auth_required') else 'connected', message,
                          selection_ready=False, selection_stage='choose_batch' if view.get('ambiguous') else 'entrance')
            except (BrowserError, BrowserIssue):
                self.catalog_snapshot = None
                self._set('uncertain', '课程页面正在切换或教学班字段尚未匹配，保留上次目录',
                          selection_ready=False, selection_stage='error')
            return None

    async def _start_selection_buttons(self):
        buttons = self.page.get_by_role("button", name="开始选课", exact=True).or_(
            self.page.get_by_role("link", name="开始选课", exact=True))
        visible = [buttons.nth(i) for i in range(await buttons.count()) if await buttons.nth(i).is_visible()]
        if not visible:
            # Some school themes render the entry as a clickable div/span.
            buttons = self.page.get_by_text("开始选课", exact=True)
            visible = [buttons.nth(i) for i in range(await buttons.count()) if await buttons.nth(i).is_visible()]
        return visible

    async def _enter_course_list(self):
        self._set("connected", "已进入选课入口，正在查找「开始选课」", selection_ready=False,
                  selection_stage="entrance")
        deadline = time.monotonic() + 15
        buttons = []
        while time.monotonic() < deadline:
            try:
                view = await self._selection_view()
                if view["catalog"]:
                    self._set("connected", "已进入课程列表，复用当前页面", selection_ready=True,
                              selection_stage="empty" if view["empty"] else "catalog")
                    return
                if view.get('ambiguous'):
                    self._set('connected', '发现多个课程列表，请关闭无关批次窗口后继续读取',
                              selection_stage='choose_batch')
                    return
                if view["no_batches"]:
                    self._set("connected", "学校当前没有可用选课批次，尚未进入课程列表", selection_stage="not_open")
                    return
                buttons = await self._start_selection_buttons()
                if buttons:
                    break
            except BrowserError:
                pass  # Normal SSO or entry navigation may replace the document.
            await asyncio.sleep(0.2)
        if not buttons:
            self._set("connected", "未找到「开始选课」按钮或课程列表，请检查学校窗口中的批次入口",
                      selection_stage="entrance")
            return
        if len(buttons) != 1:
            self._set("connected", "发现多个「开始选课」入口，请先在学校窗口选择需要的批次，再读取课程",
                      selection_stage="choose_batch")
            return
        button = buttons[0]
        disabled = (not await button.is_enabled() or await button.get_attribute("aria-disabled") == "true" or
                    bool(set((await button.get_attribute("class") or "").split()) & {"disabled", "is-disabled"}))
        if disabled:
            self._set("connected", "「开始选课」按钮暂不可用，请查看学校的开放时间或批次提示",
                      selection_stage="not_open")
            return
        self.activity.step("enter_selection", "点击「开始选课」，进入该批次课程列表")
        self._set("connected", "正在点击「开始选课」并等待课程列表", selection_stage="entering")
        origin, popups = self.page, []
        def observe_popup(popup):
            popups.append(popup)
        origin.on("popup", observe_popup)
        try:
            await button.click(timeout=10000)
            self.activity.step("course_list", "等待课程列表加载完成，再读取课程信息")
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if popups:
                    self.page = popups[-1]
                try:
                    view = await self._selection_view()
                    if view["catalog"]:
                        self._set("connected", "已点击「开始选课」并进入课程列表", selection_ready=True,
                                  selection_stage="empty" if view["empty"] else "catalog")
                        return
                    login = self.page.get_by_text("账号登录", exact=True)
                    if await login.count() and await login.first.is_visible():
                        self._set("uncertain", "选课应用要求重新验证，请检查登录会话", selection_ready=False,
                                  selection_stage="auth_required")
                        return
                except BrowserError:
                    pass
                await asyncio.sleep(0.2)
        except BrowserError:
            pass
        finally:
            origin.remove_listener("popup", observe_popup)
        self._set("connected", "已尝试点击「开始选课」，课程列表未加载完成；请查看学校窗口后重试读取",
                  selection_ready=False, selection_stage="error")

    async def _open_selection(self):
        # Reuse a real list or an entry already open in this same context.
        await self._selection_view()
        path = urlparse(self.page.url).path
        selection_page = path.startswith("/course-selection/") or path.startswith("/student/for-std/course-select")
        login = self.page.get_by_text("账号登录", exact=True)
        if not selection_page or (await login.count() and await login.first.is_visible()):
            self._set("checking", "正在打开学校选课入口", selection_ready=False, selection_stage="entrance")
            await self.page.goto(BASE + "/student/for-std/course-select", wait_until="domcontentloaded", timeout=30000)
        await self._enter_course_list()

    def _finish_connection(self):
        status = self.status
        result = "succeeded" if status["state"] == "connected" and status["selection_ready"] else (
            "waiting_user" if status["state"] == "manual_required" or
            (status["state"] == "connected" and status["selection_stage"] != "error") else "failed")
        self.activity.finish(result, status["message"])
        return self.public_status()

    async def connect(self):
        async with self.lock:
            self.activity.begin("连接学校")
            try:
                self.activity.step("browser", "启动或复用固定登录窗口")
                await self._ensure()
                self._set("checking", "正在检查现有登录会话", selection_ready=False)
                self.activity.step("session", "检查学生教务登录状态")
                if await self._student_authenticated():
                    self.activity.step("selection", "会话有效，打开或复用选课页面")
                    await self._open_selection()
                else:
                    self.activity.step("sso", "通过学校正常单点登录确认会话")
                    # Normal SSO may silently renew from a still-valid CAS session.
                    await self.page.goto(BASE + "/student/sso/login", wait_until="domcontentloaded", timeout=30000)
                    try:
                        await self.page.wait_for_url("**/student/home", timeout=12000)
                    except BrowserTimeout:
                        pass
                    if await self._student_authenticated():
                        self.activity.step("selection", "打开学校选课页面")
                        await self._open_selection()
                    else:
                        self.activity.step("manual", "等待你完成学校登录验证")
                        await self._manual_page()
            except BrowserIssue as error:
                self._set("uncertain", str(error), selection_ready=False)
            except Exception:
                self._set("uncertain", "网络或页面响应超时；已保留 Profile，请稍后重试", selection_ready=False)
            return self._finish_connection()

    async def captcha(self, refresh=False):
        async with self.lock:
            if self.context is None or self.status["state"] != "manual_required":
                raise BrowserIssue("当前没有等待填写的验证码")
            image = self.page.locator("#captcha_img")
            if refresh:
                async with self.page.expect_response(lambda r: "/cas/captcha.jpg" in r.url, timeout=15000):
                    await image.click()
            return await image.screenshot()

    async def login(self, username, password, captcha):
        async with self.lock:
            if self.context is None or self.status["state"] != "manual_required":
                raise BrowserIssue("请先检查会话；有效会话不需要重新提交密码")
            self.activity.begin("人工验证后登录")
            self.activity.step("form", "填写正常登录表单")
            await self.page.locator("#username").fill(username)
            await self.page.locator("#password").fill(password)
            await self.page.locator("#captcha").fill(captcha)
            self.activity.step("auth", "提交你填写的验证码，等待学校认证")
            await self.page.locator("#fm1 input.userButton:visible").first.click()
            try:
                await self.page.wait_for_url("https://portal.cupk.edu.cn/**", timeout=20000)
                self.activity.step("sso", "认证通过，进入学生教务")
                await self.page.goto(BASE + "/student/sso/login", wait_until="domcontentloaded", timeout=30000)
                await self.page.wait_for_url("**/student/home", timeout=20000)
                if not await self._student_authenticated():
                    raise BrowserIssue("学生教务登录尚未确认，请检查登录状态")
                self.activity.step("selection", "进入学校选课应用")
                await self._open_selection()
            except BrowserTimeout:
                if urlparse(self.page.url).hostname == "cas.cupk.edu.cn":
                    await self.page.locator("#password").fill("")
                    self._set("manual_required", "登录未通过或验证码已过期，请核对并重新填写")
                else:
                    self._set("uncertain", "跳转超时，保留当前会话；请先检查再决定是否重登")
            return self._finish_connection()

    async def view(self, kind="selection"):
        """Read-only live image; no navigation, no new context, no screenshot files."""
        target = self.table_page if kind == "timetable" else self.page
        if self.context is None or self.context_closed or target is None or target.is_closed():
            raise BrowserIssue("当前没有可显示的学校窗口，请先连接教务")
        if self.view_lock.locked():
            if kind in self.frames:
                return self.frames[kind]
            raise BrowserIssue("学校窗口正在刷新，稍后会继续显示")
        async with self.view_lock:
            try:
                frame = await target.screenshot(type="png", timeout=2500,
                                                mask=[target.locator('input[type="password"]')], animations="disabled")
                self.frame, self.frame_at = frame, now_iso()
                self.frames[kind] = frame
                return frame
            except Exception:
                raise BrowserIssue("学校页面正在切换，稍后会继续显示")

    async def semesters(self):
        async with self.lock:
            if self.context is None or not await self._student_authenticated():
                self._set("uncertain", "学生教务会话失效，请先连接，再完成人工验证")
                raise BrowserIssue("请先完成登录")
            response = await self.context.request.get(BASE + "/student/for-std/course-table", timeout=20000)
            parser = SemesterParser()
            parser.feed(await response.text())
            if not parser.semesters:
                raise BrowserIssue("课表页面结构尚未匹配，请先记录页面字段")
            self.current_semester = parser.selected
            return parser.semesters

    async def schedule(self, semester):
        available = await self.semesters()
        if semester not in {item["id"] for item in available}:
            raise BrowserIssue("请使用当前账号课表页面中的有效学期")
        async with self.lock:
            self.activity.begin("读取学校我的课表")
            self.activity.step("timetable", "打开本学期学校课表，保留当前选课标签页")
            if self.table_page is None or self.table_page.is_closed():
                self.table_page = await self.context.new_page()
            collected, tasks = [], []
            async def collect(response):
                parsed = urlparse(response.url)
                params = parse_qs(parsed.query)
                matches = params.get("semesterId") == [semester] or f"/semester/{semester}/" in parsed.path
                if "/for-std/course-table/" not in parsed.path or not matches or "json" not in response.headers.get("content-type", ""):
                    return
                try:
                    body = await response.json()
                    if isinstance(body, dict) and body.get("result") is not False and body.get("success") is not False:
                        full_semester = (parsed.path.endswith("/course-table/get-data") and isinstance(body.get("lessons"), list)
                                         and not set(params) & {"page", "pageNo", "offset", "limit", "pageSize", "size"})
                        collected.append((body, full_semester))
                except Exception:
                    pass
            def observe(response):
                tasks.append(asyncio.create_task(collect(response)))
            self.table_page.on("response", observe)
            confirmed = False
            try:
                await self.table_page.goto(BASE + "/student/for-std/course-table", wait_until="domcontentloaded", timeout=30000)
                select = self.table_page.locator("#semesters, #allSemesters").first
                if await select.count():
                    if await select.input_value() != semester:
                        await select.select_option(semester)
                    confirmed = await select.input_value() == semester
                try:
                    await self.table_page.wait_for_load_state("networkidle", timeout=6000)
                except BrowserTimeout:
                    pass
                if tasks:
                    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=6)
            finally:
                self.table_page.remove_listener("response", observe)
                for task in tasks:
                    if not task.done(): task.cancel()
            self.activity.step("records", "读取课程时间，并补充全部课程列表")
            response = await self.context.request.get(BASE + f"/student/for-std/course-table/semester/{semester}/print-data", timeout=20000)
            if not response.ok:
                raise BrowserIssue("课表读取失败，未更新本地课表")
            try:
                result = normalize_print_data(await response.json(), semester)
                for body, full_semester in collected:
                    candidate = body if "studentTableVms" in body else {"studentTableVms": [body]}
                    try:
                        candidate_result = normalize_print_data(candidate, semester, full_semester=full_semester)
                    except (ValueError, TypeError, AttributeError):
                        continue
                    if candidate_result["coverage"] == "complete":
                        result = merge_schedule_sources(result, candidate_result)
                if confirmed:
                    # This is a view control on the course-table page, never an enrollment action.
                    toggle = self.table_page.get_by_text("全部课程", exact=True)
                    if await toggle.count() == 1 and await toggle.is_visible():
                        await toggle.click()
                    full = await self.table_page.evaluate("""() => {
                        const text=document.body.innerText;
                        const match=text.match(/全部课程\\s*[（(]\\s*(\\d+)\\s*(?:门|个|条)?\\s*[）)]/);
                        const rows=[];
                        for(const table of document.querySelectorAll('table')) {
                            const hs=[...table.querySelectorAll('thead th')].map(x=>x.innerText.trim());
                            const idx=(labels)=>{for(const label of labels){const i=hs.findIndex(h=>h===label);if(i>=0)return i;}return -1;};
                            const ni=idx(['课程名称']);if(ni<0)continue;
                            for(const tr of table.querySelectorAll('tbody tr')){
                                const cs=[...tr.querySelectorAll('td')].map(x=>x.innerText.trim());
                                const get=(labels)=>cs[idx(labels)]||'';
                                if(!cs[ni])continue;
                                rows.push({name:cs[ni],lesson_id:tr.getAttribute('data-lesson-id'),
                                    code:get(['课程代码']),class_code:get(['教学班代码','教学班','教学班名称']),
                                    teachers:[get(['授课教师','教师'])].filter(Boolean),
                                    no_fixed_time:/无固定上课时间|无固定排课/.test(get(['上课时间']))});
                            }
                        }
                        return {rows,total:match?Number(match[1]):null};
                    }""")
                    result = merge_all_course_rows(result, full["rows"], semester, full["total"])
                self.activity.step("clock", "合并学校节次时间与校历时刻")
                metadata = {}
                try:
                    info = await self.context.request.get(BASE + f"/student/ws/semester/get/{semester}", timeout=10000)
                    if info.ok:
                        metadata = await info.json()
                except Exception:
                    pass
                name = next(item["name"] for item in available if item["id"] == semester)
                result["calendar"] = merge_calendar(metadata, result["time_slots"], name)
                self.activity.finish("succeeded", f"读取 {len(result['courses'])} 门已有课程；{'完整列表' if result['coverage']=='complete' else '部分列表待核实'}")
                return result
            except (ValueError, TypeError, AttributeError):
                raise BrowserIssue("课表数据格式需要适配，未更新本地课表")

    async def read_catalog(self):
        await self.connect()
        async with self.lock:
            if self.status["state"] != "connected":
                raise BrowserIssue("请先完成学校登录")
            if not self.status["selection_ready"]:
                raise BrowserIssue(self.status["message"])
            if self.status["selection_stage"] == "empty":
                raise BrowserIssue("已进入课程列表，但学校当前显示没有可选课程；保留上次目录")
            self.activity.begin("读取选课课程目录")
            self.activity.step("catalog", "检查当前选课页面的课程名称、教师和教学班标识")
            # Detection and import consume exactly the same normalized evidence.
            view = await self._selection_view()
            if not view['catalog'] or not self.catalog_snapshot['courses']:
                self._set('connected', '课程列表已变化，保留上次目录', selection_ready=False)
                raise BrowserIssue('当前没有可可靠读取的课程行，保留上次目录')
            result = self.catalog_snapshot['courses']
            self.activity.finish('succeeded', f'读取 {len(result)} 门当前页面课程，目录完整性待核实')
            return result

    async def read_all_catalog(self, on_page):
        """Walk only explicitly recognized read-only pagination in the same context."""
        await self.connect()
        async with self.lock:
            if not self.status['selection_ready'] or not self.catalog_snapshot:
                raise BrowserIssue(self.status['message'])
            initial = self.catalog_snapshot
            if self.catalog_request_template:
                return await self._read_catalog_httpx(on_page)
            pager = initial['pagination']
            if initial['batch'] == 'unverified' or pager.get('filtered'):
                raise BrowserIssue('请先确认学校批次并清空学校搜索条件，再读取全部页')
            if pager.get('page') != 1 or type(pager.get('total')) is not int:
                raise BrowserIssue('请在学校窗口进入第1页；当前页码或总数控件尚未匹配，不能认定全部目录')
            scope = (initial['batch'], initial['semester'])
            total = pager['total']
            if not 0 < total <= 5000:
                raise BrowserIssue('学校目录总数尚未匹配或超过本次5000条读取上限')
            self.activity.begin('批量读取选课目录')
            seen, pages = {}, 0
            deadline = time.monotonic() + 180
            try:
                while pages < 100 and time.monotonic() < deadline:
                    if time.monotonic() - self.last_auth_check >= 30:
                        await self._student_authenticated()
                    if not self.authenticated:
                        self._set('manual_required', '学校登录已失效，请在正常学校窗口验证',
                                  selection_ready=False, selection_stage='auth_required')
                        raise BrowserIssue('学校登录已失效，已暂停批量读取并保留已读取目录')
                    snapshot = self.catalog_snapshot
                    current = snapshot['pagination']
                    if (snapshot['batch'], snapshot['semester']) != scope or current.get('total') != total or current.get('filtered'):
                        raise BrowserIssue('读取期间批次、学期、筛选条件或总数发生变化，目录完整性待核实')
                    if current.get('page') != pages + 1 or not snapshot['courses']:
                        raise BrowserIssue('翻页结果未匹配或出现空页，保留已读取目录')
                    for course in snapshot['courses']:
                        previous = seen.get(course.id)
                        if previous and any(getattr(previous, field) != getattr(course, field)
                                            for field in ('name', 'class_code', 'code', 'teachers', 'semester', 'meetings')):
                            raise BrowserIssue('同一教学班标识对应不同课程信息，完整性待核实')
                        seen[course.id] = course
                    pages += 1
                    self.activity.step('catalog_pages', f'已读取第 {pages} 页，共 {len(seen)} / {total} 个教学班')
                    on_page(snapshot)
                    if current.get('next_disabled'):
                        if len(seen) != total:
                            raise BrowserIssue('已到末页，但唯一教学班数量与学校总数不一致，完整性待核实')
                        final = {**snapshot, 'courses': list(seen.values()), 'coverage': 'complete', 'pages_read': pages}
                        self.activity.finish('succeeded', f'已核对 {pages} 页、{len(seen)} 个教学班')
                        return final
                    next_button = self.catalog_frame.locator('.el-pagination button.btn-next,.ant-pagination-next button,[data-catalog-pagination] [aria-label="下一页"]').filter(visible=True)
                    if await next_button.count() != 1:
                        raise BrowserIssue('下一页控件尚未匹配，保留已读取目录')
                    old_ids = {c.id for c in snapshot['courses']}
                    await asyncio.sleep(0.8)
                    await next_button.click(timeout=5000)
                    page_deadline = time.monotonic() + 12
                    changed = False
                    while time.monotonic() < page_deadline:
                        await asyncio.sleep(0.25)
                        view = await self._selection_view()
                        new = self.catalog_snapshot
                        if view['catalog'] and new and new['pagination'].get('page') == pages + 1 and {c.id for c in new['courses']} != old_ids:
                            changed = True
                            break
                    if not changed:
                        raise BrowserIssue('下一页未加载完成，保留已读取目录，请检查学校窗口')
                raise BrowserIssue('本次批量读取达到时间或页数上限，保留已读取目录')
            except Exception:
                self.activity.finish('failed', '批量读取未完成，已保留已读取目录；完整性待核实')
                raise

    def _observe_catalog_response(self, response):
        if not re.fullmatch(r'/course-selection-api/api/v1/student/course-select/query-lesson/\d+/\d+', urlparse(response.url).path):
            return
        task=asyncio.create_task(self._capture_catalog_request(response))
        self.response_tasks.add(task)
        task.add_done_callback(self.response_tasks.discard)

    async def _capture_catalog_request(self, response):
        try:
            request=response.request
            body=json.loads(request.post_data or '{}')
            payload=await response.json()
            if request.method!='POST' or not response.ok or payload.get('result') or not isinstance((payload.get('data') or {}).get('lessons'),list):
                return
            self.catalog_request_template={'method':request.method,'url':request.url,
                'headers':await request.all_headers(),'body':request.post_data,
                'batch':str(body['turnId']),'semester':str(body['semesterId']),
                'lesson_ids':tuple(str(l['id']) for l in payload['data']['lessons'] if l.get('id') is not None)}
            try:
                self.catalog_origin_page = request.frame.page
            except Exception:
                pass
            self.selection_auth_failed = False
        except Exception:
            return  # Authentication and request values are never echoed.

    async def _read_catalog_httpx(self, on_page):
        from .catalog import normalize_school_data
        template=self.catalog_request_template
        snapshot=self.catalog_snapshot
        if (template['batch'],template['semester'])!=(snapshot['batch'],snapshot['semester']):
            raise BrowserIssue('已观察的查询与当前批次或学期不一致，请在学校页面正常查询后重试')
        parsed=urlparse(template['url'])
        if parsed.hostname not in HOSTS or not re.fullmatch(r'/course-selection-api/api/v1/student/course-select/query-lesson/\d+/\d+',parsed.path):
            raise BrowserIssue('当前查询不是已核实的只读课程查询')
        body=json.loads(template['body'])
        filter_keys = ('courseNameOrCode','lessonNameOrCode','teacherNameOrCode','week','grade',
            'departmentId','majorId','adminclassId','campusId','openDepartmentId','courseTypeId',
            'coursePropertyId','courseModuleTypeId','canSelect','creditGte','creditLte','hasCount',
            'ids','substitutedCourseId','courseSubstitutePoolId')
        if snapshot['pagination'].get('filtered') or any(body.get(k) not in (None, '', False, 0, []) for k in filter_keys):
            raise BrowserIssue('请在学校页面正常清空筛选后，再读取完整课程目录')
        headers={k:v for k,v in template['headers'].items() if k.lower() not in {'host','content-length','connection','accept-encoding'}}
        seen={}; total=None; deadline=time.monotonic()+180
        self.activity.begin('批量读取选课目录')
        try:
            async with httpx.AsyncClient(headers=headers,follow_redirects=False,timeout=20) as client:
                for number in range(1,101):
                    if time.monotonic()>deadline:
                        raise BrowserIssue('目录读取达到时间上限，已保留读取结果')
                    # Only pagination changes; identity/auth come from the actual
                    # normal request observed in this same session, in memory.
                    query={**body,'pageNo':number}
                    response=await client.post(template['url'],json=query)
                    if response.status_code in {401,403}:
                        self.selection_auth_failed = True
                        self._set('manual_required','选课会话需要正常验证',selection_ready=False,selection_stage='auth_required')
                        raise BrowserIssue('选课会话失效，保留目录，请正常验证')
                    if response.status_code!=200:
                        raise BrowserIssue('学校只读查询暂未完成，保留目录')
                    result=response.json()
                    data=result.get('data') or {}
                    info=data.get('pageInfo') or {}
                    lessons=data.get('lessons')
                    if result.get('result') or not isinstance(lessons,list) or info.get('currentPage')!=number:
                        raise BrowserIssue('学校课程查询响应或页码尚未匹配，保留目录')
                    current_total=info.get('totalRows')
                    if type(current_total) is not int or not 0<current_total<=5000 or total is not None and total!=current_total:
                        raise BrowserIssue('学校目录总数无效或读取期间变化，保留目录')
                    total=current_total
                    counts={c.lesson_id:c.enrolled_count for c in snapshot['courses']}
                    projected=[{'id':l.get('id'),'code':l.get('code'),'course':{k:(l.get('course') or {}).get(k) for k in ('nameZh','code','credits')},
                        'teachers':[{'nameZh':t.get('nameZh')} for t in l.get('teachers',[])],
                        'limitCount':l.get('limitCount'),'enrolled':counts.get(str(l.get('id'))),
                        'dateTimePlace':{'textZh':(l.get('dateTimePlace') or {}).get('textZh','')},
                        'schoolDisplay':next((c.school_display.model_dump() for c in snapshot['courses'] if c.lesson_id==str(l.get('id')) and c.school_display),None)} for l in lessons]
                    raw={'tables':[{'headers':self.fields}],'pagination':{'filtered':False}}
                    page=normalize_school_data({'batch':snapshot['batch'],'semester':snapshot['semester'],'type':snapshot.get('entry'),
                        'lessons':projected,'pageInfo':info},raw,catalog_identity)
                    page.update(source='school_httpx',evidence='observed_normal_query')
                    for course in page['courses']:
                        old=seen.get(course.id)
                        if old and (old.name,old.class_code,old.meetings)!=(course.name,course.class_code,course.meetings):
                            raise BrowserIssue('教学班身份在分页期间发生冲突，保留目录')
                        seen[course.id]=course
                    self.activity.step('catalog_pages',f'已读取第{number}页，{len(seen)}/{total}个教学班')
                    on_page(page)
                    if number==info.get('totalPages'):
                        if len(seen)!=total:
                            raise BrowserIssue('唯一教学班数量与学校总数不一致，完整性待核实')
                        page.update(courses=list(seen.values()),coverage='complete',pages_read=number)
                        self.activity.finish('succeeded',f'已核对{number}页、{total}个教学班')
                        return page
                    await asyncio.sleep(.8)
            raise BrowserIssue('目录读取达到页数上限，已保留读取结果')
        except BrowserIssue:
            self.activity.finish('failed','目录读取未完成，保留已读取课程')
            raise
        except Exception:
            self.activity.finish('failed','学校查询未完成，保留已读取课程')
            raise BrowserIssue('学校查询未完成，保留已读取课程') from None

    async def diagnostics(self):
        """Only structural summaries; never return DOM text, URLs, or auth values."""
        if self.context is None or self.context_closed:
            return {'browser_running': False, 'frames': []}
        summaries = []
        async with self.lock:
            for page in self.context.pages:
                if page.is_closed() or not self._selection_url(page.url):
                    continue
                for frame in page.frames:
                    if not self._selection_url(frame.url):
                        continue
                    try:
                        raw = await frame.evaluate(CATALOG_VIEW)
                        summaries.append({'iframe': frame != page.main_frame,
                            'headers': [t['headers'] for t in raw['tables']],
                            'row_counts': [len(t['rows']) for t in raw['tables']],
                            'pagination': raw['pagination'], 'auth_required': raw['auth_required']})
                    except BrowserError:
                        continue
        return {'browser_running': True, 'frames': summaries}

    async def close(self):
        # App shutdown only: keep the persistent Profile; never logout or clear cookies.
        async with self.lock:
            await self.count_query.close()
            for task in list(self.response_tasks):
                task.cancel()
            if self.response_tasks:
                await asyncio.gather(*self.response_tasks, return_exceptions=True)
            if self.context:
                await self.context.close()
            if self.playwright:
                await self.playwright.stop()
            self.context = self.playwright = self.page = self.table_page = None
            self._release_profile()
